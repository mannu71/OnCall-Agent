"""Auxiliary model role resolution and off-event-loop semantic search."""
import json
import threading


async def test_auxiliary_role_is_valid_and_falls_back_to_crawler(monkeypatch):
    from app.infrastructure.persistence.model_role_repository import (
        VALID_ROLES, ModelRoleRepository,
    )
    from app.workflow import llm_config

    ModelRoleRepository._validate_role("auxiliary")  # must not raise
    assert "auxiliary" in VALID_ROLES

    looked_up = []

    async def _no_default(cfg):
        raise AssertionError("default config must not be used when crawler resolves")

    class _Resolved:
        def to_dict(self):
            return {"model": "crawler-model"}

    async def _enrich(_resolved):
        return None

    monkeypatch.setattr(llm_config, "_resolve_default", _no_default)
    monkeypatch.setattr(llm_config, "_enrich_credentials", _enrich)

    async def _resolve_role_obj(cfg, role):
        looked_up.append(role)
        return _Resolved() if role == "crawler" else None

    monkeypatch.setattr(llm_config, "_resolve_role", _resolve_role_obj)
    out = await llm_config.resolve_llm_config_for_role("auxiliary")
    assert out == {"model": "crawler-model"}
    assert looked_up == ["auxiliary", "crawler"]


async def test_other_roles_do_not_fall_back_to_crawler(monkeypatch):
    from app.workflow import llm_config

    looked_up = []

    class _Resolved:
        def to_dict(self):
            return {"model": "default-model"}

    async def _resolve_role(cfg, role):
        looked_up.append(role)
        return None

    async def _default(cfg):
        return _Resolved()

    async def _enrich(_resolved):
        return None

    monkeypatch.setattr(llm_config, "_resolve_role", _resolve_role)
    monkeypatch.setattr(llm_config, "_resolve_default", _default)
    monkeypatch.setattr(llm_config, "_enrich_credentials", _enrich)
    out = await llm_config.resolve_llm_config_for_role("subagent")
    assert out == {"model": "default-model"}
    assert looked_up == ["subagent"]


async def test_semantic_search_runs_off_the_event_loop(monkeypatch):
    from app.config import settings
    from app.core.code_semantic import search as search_mod
    from app.workflow.tools import codegraph_tools

    monkeypatch.setattr(settings, "code_semantic_enabled", True)
    loop_thread = threading.get_ident()
    seen = {}

    class _FakeSvc:
        def search(self, project, query, top_k=10, include_tests=None):
            seen["thread"] = threading.get_ident()
            return [{"name": "validate_credentials", "score": 0.9}]

        def is_indexing(self, _project):
            return False

    monkeypatch.setattr(search_mod, "get_code_semantic_search", lambda: _FakeSvc())
    tool = codegraph_tools._build_onnx_semantic_tool(["repo-a"])
    assert tool is not None
    out = json.loads(await tool.coroutine(query="where are credentials validated"))
    assert out["results"][0]["name"] == "validate_credentials"
    assert seen["thread"] != loop_thread
