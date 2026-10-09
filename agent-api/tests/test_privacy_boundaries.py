"""Raw PII must not cross the Bedrock boundary on the auxiliary LLM paths.

Each test drives one caller with a fake model that records what it was sent
and echoes the placeholders back, then checks two things: the model never saw
the raw value, and the user-facing result has the real value restored.
"""
import types

import pytest

from app.core import privacy

EMAIL = "jane.doe@example.com"
IP = "10.20.30.40"


class _EchoLLM:
    """Records prompts; replies with the prompt's text so placeholders round-trip."""

    def __init__(self, reply=None):
        self.prompts = []
        self._reply = reply

    async def ainvoke(self, messages):
        text = "\n".join(getattr(m, "content", str(m)) for m in messages)
        self.prompts.append(text)
        body = self._reply(text) if self._reply else text
        return types.SimpleNamespace(content=body, usage_metadata={})


def _placeholder_for(text: str, prefix: str) -> str:
    start = text.index(f"[{prefix}_")
    return text[start:text.index("]", start) + 1]


def test_rehydrate_obj_walks_nested_values():
    key = "test-rehydrate-obj"
    try:
        masked = privacy.pseudonymize(f"mail {EMAIL}", key)
        placeholder = _placeholder_for(masked, "EMAIL")
        obj = {"a": [placeholder, {"b": f"x {placeholder}"}], "n": 3}
        out = privacy.rehydrate_obj(obj, key)
        assert out == {"a": [EMAIL, {"b": f"x {EMAIL}"}], "n": 3}
    finally:
        privacy.drop_vault(key)


async def test_cloudwatch_synthesis_pseudonymizes_and_rehydrates(monkeypatch):
    from app.config import settings
    from app.workflow.executor import cloudwatch_analysis as cw
    import app.workflow.strategies.react.llm_factory as llm_factory
    import app.workflow.strategies.react.workflow_config as workflow_config

    llm = _EchoLLM(
        reply=lambda prompt: (
            "Root cause: login failures for "
            + _placeholder_for(prompt, "EMAIL")
            + " from host "
            + _placeholder_for(prompt, "IP")
            + ". " + "Detail. " * 10
        )
    )

    async def _cfg(_workflow):
        return {"provider": "fake", "model": "fake-model"}

    monkeypatch.setattr(workflow_config, "resolve_llm_config_for_workflow", _cfg)
    monkeypatch.setattr(llm_factory, "build_llm", lambda _cfg: llm)
    monkeypatch.setattr(settings, "cloudwatch_pipeline_llm_synthesis", True)
    monkeypatch.setattr(cw, "_synthesis_circuit_open", lambda: False)

    raw_result = {
        "patterns": {"unique_patterns": [
            {"normalized_pattern": f"login failed for {EMAIL} from {IP}",
             "occurrence_count": 12,
             "example_message": f"ERROR auth user={EMAIL} ip={IP}"},
        ]},
    }
    execution_id = "exec-cw-pii"
    text, model, structured, _, _ = await cw.analyze_cloudwatch_with_llm(
        active_executions={execution_id: {"workflow": {"nodes": [{"type": "llm"}]}}},
        execution_id=execution_id,
        raw_result=raw_result,
        analysis_type="errors",
        log_groups=["/aws/lambda/auth"],
        time_range="1h",
        alerts=[{"message": f"alert for {EMAIL}"}],
    )

    assert llm.prompts, "the model was never called"
    sent = "\n".join(llm.prompts)
    assert EMAIL not in sent and IP not in sent
    assert EMAIL in text and IP in text
    assert model == "fake-model"
    assert f"cw-synthesis:{execution_id}" not in privacy._VAULTS


async def test_batch_reduce_and_decompose_keep_pii_out_of_prompts():
    from app.workflow.strategies.batch_react import (
        BatchReactStrategy, InvestigationTarget, SubInvestigationResult,
    )
    from app.core.concurrency.map_reduce import MapResult

    strategy = BatchReactStrategy()
    log = types.SimpleNamespace(info=lambda *a, **k: None, debug=lambda *a, **k: None,
                                warning=lambda *a, **k: None)

    reduce_llm = _EchoLLM(reply=lambda p: f"Summary for {_placeholder_for(p, 'EMAIL')}")
    target = InvestigationTarget(name="auth", query="q")
    sub = SubInvestigationResult(target=target,
                                 final_answer=f"user {EMAIL} locked out",
                                 tool_calls=[], duration_ms=1)
    synthesis = await strategy._reduce(
        f"why is {EMAIL} locked out?", [MapResult(item=target, result=sub)],
        reduce_llm, log, "exec-batch",
    )
    assert EMAIL not in "\n".join(reduce_llm.prompts)
    assert synthesis == f"Summary for {EMAIL}"

    def _decompose_reply(prompt):
        ph = _placeholder_for(prompt, "EMAIL")
        return ('[{"name": "a", "query": "logins for %s"}, '
                '{"name": "b", "query": "db errors for %s"}]' % (ph, ph))

    decompose_llm = _EchoLLM(reply=_decompose_reply)
    targets = await strategy._llm_decompose(
        f"check logins and db for {EMAIL}", decompose_llm, log, "exec-batch",
    )
    assert EMAIL not in "\n".join(decompose_llm.prompts)
    assert [t.query for t in targets] == [f"logins for {EMAIL}", f"db errors for {EMAIL}"]
    assert not any(k.startswith("batch-") for k in privacy._VAULTS)


async def test_fact_extraction_sends_pseudonymized_excerpt(monkeypatch):
    from app.config import settings
    from app.core.memory import fact_extractor

    monkeypatch.setattr(settings, "memory_fact_extraction_enabled", True)
    seen = []

    async def _llm(prompt):
        seen.append(prompt)
        return "[]"

    privacy.bind_session("exec-facts")
    try:
        await fact_extractor.extract_facts(
            f"my email is {EMAIL}", "noted", llm_fn=_llm,
        )
    finally:
        privacy.drop_vault("exec-facts")
        privacy.bind_session(None)
    assert seen and EMAIL not in seen[0]


async def test_session_compaction_pseudonymizes_transcript(monkeypatch):
    import importlib
    from app.api.v1.endpoints import sessions

    call_llm_mod = importlib.import_module("app.core.llm.call_llm")

    sent = []

    async def _fake_call_llm(prompt, **_kw):
        sent.append(prompt)
        return f"- owner is {_placeholder_for(prompt, 'EMAIL')}", None, None, None

    monkeypatch.setattr(call_llm_mod, "call_llm", _fake_call_llm)

    stored = {}

    class _Repo:
        async def get_session(self, _sid):
            return {"messages": [
                {"role": "user", "content": f"page {EMAIL}"},
                {"role": "assistant", "content": "done"},
                {"role": "user", "content": "latest"},
            ]}

        async def replace_with_summary(self, sid, summary, keep_recent):
            stored["summary"] = summary
            return {"compacted": 2, "message_count": 2}

    await sessions.compact_session("s1", sessions.CompactRequest(keep_recent=1), repo=_Repo())
    assert sent and EMAIL not in sent[0]
    assert EMAIL in stored["summary"]
    assert "compact:s1" not in privacy._VAULTS
