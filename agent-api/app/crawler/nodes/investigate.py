"""investigateAlertFlow nodes.

Pipeline: ParseAlert → LookupOverview → IdentifyLikelyAbstractions
          → FindSymbolsInScope → SynthesizeRootCause → BuildReport

Investigates an on-call alert by mapping it to code abstractions, locating
relevant symbol definitions, and synthesising a root-cause analysis.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from app.engine.crawler_engine import AsyncNode
from app.crawler.nodes.fetch import _append_trace

logger = logging.getLogger(__name__)

_MAX_SYMBOLS = 5
_MAX_FILE_CHARS = 60_000
# Total LLM-prompt context cap comes from settings.crawler_search_context_max_chars.


# ─────────────────────────────────────────────────────────────────────────────
# Node 1: ParseAlert
# ─────────────────────────────────────────────────────────────────────────────

class ParseAlert(AsyncNode):
    """Extract structured information from the raw alert text."""

    def __init__(self, max_retries: int = 3, wait: float = 10.0):
        super().__init__(max_retries=max_retries, wait=wait)

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        from app.config import settings

        # Defensive cap: a pathological alert payload shouldn't blow the prompt
        # budget. The structured fields we extract live near the top of the text.
        alert = str(shared["alert"])
        _cap = settings.crawler_alert_max_chars
        if len(alert) > _cap:
            alert = alert[:_cap] + "\n... [alert truncated]"
        return {
            "alert": alert,
            "repo": shared["repo"],
            "model": shared.get("model_id"),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.call_llm import call_llm
        import yaml

        t0 = time.monotonic()
        prompt = f"""You are an on-call engineer analysing an alert.

Alert text:
\"\"\"{prep_res['alert']}\"\"\"

Repository: "{prep_res['repo']}"

Extract structured information from this alert.

Output YAML only:

```yaml
summary: "One-sentence description of what went wrong"
severity: "critical|high|medium|low"
error_type: "exception class or error category"
suspected_symbols:
  - "FunctionOrClassName"
error_message: "Exact error string if present, else empty"
stack_trace_snippet: "Key stack frame(s) if present, else empty"
```"""

        # Graceful fallback on a parse miss, so retries are transport-only —
        # reading the cache on retry is always safe here.
        response, tokens_in, tokens_out, was_cached = await call_llm(
            prompt, model_id=prep_res["model"], use_cache=True
        )
        ms = int((time.monotonic() - t0) * 1000)

        try:
            yaml_str = response.split("```yaml")[1].split("```")[0].strip()
            parsed = yaml.safe_load(yaml_str) or {}
        except Exception:
            parsed = {"summary": prep_res["alert"][:200], "severity": "unknown"}

        return {
            "parsed": parsed,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cached": was_cached,
            "ms": ms,
        }

    async def post(
        self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]
    ) -> Optional[str]:
        shared["_alert_parsed"] = exec_res["parsed"]
        _append_trace(
            shared, "ParseAlert", 1,
            exec_res["tokens_in"], exec_res["tokens_out"], exec_res["cached"],
            llm_calls=1, ms=exec_res["ms"],
        )
        logger.info(
            "ParseAlert: severity=%s summary=%s",
            exec_res["parsed"].get("severity"),
            exec_res["parsed"].get("summary", "")[:80],
        )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 2: LookupOverview
# ─────────────────────────────────────────────────────────────────────────────

class LookupOverview(AsyncNode):
    """Load the indexed overview for *repo* from ``repo_abstractions``."""

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {"repo": shared["repo"]}

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            row = await session.execute(
                text("SELECT overview FROM repo_abstractions WHERE repo_name = :repo"),
                {"repo": prep_res["repo"]},
            )
            row = row.fetchone()

        if row is None:
            raise ValueError(
                f"LookupOverview: repo '{prep_res['repo']}' has not been indexed. "
                "Run indexFlow first."
            )
        overview = row[0]
        return {
            "abstractions": overview.get("abstractions", []),
            "file_map": overview.get("file_map", {}),
        }

    async def post(
        self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]
    ) -> Optional[str]:
        shared["_overview"] = exec_res
        _append_trace(shared, "LookupOverview", len(exec_res["abstractions"]), 0, 0, False)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 3: IdentifyLikelyAbstractions
# ─────────────────────────────────────────────────────────────────────────────

class IdentifyLikelyAbstractions(AsyncNode):
    """Map the alert to the most relevant abstractions and key symbols."""

    def __init__(self, max_retries: int = 3, wait: float = 10.0):
        super().__init__(max_retries=max_retries, wait=wait)

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        overview = shared["_overview"]
        parsed = shared["_alert_parsed"]
        abstraction_text = "\n".join(
            f"- {a['name']}: {str(a.get('summary', ''))[:120]}"
            for a in overview["abstractions"]
        )
        return {
            "repo": shared["repo"],
            "alert_summary": parsed.get("summary", ""),
            "error_type": parsed.get("error_type", ""),
            "suspected_symbols": parsed.get("suspected_symbols", []),
            "abstraction_text": abstraction_text,
            "file_map": overview["file_map"],
            "model": shared.get("model_id"),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.call_llm import call_llm
        import yaml

        t0 = time.monotonic()
        symbols_hint = (
            f"Suspected symbols: {prep_res['suspected_symbols']}"
            if prep_res["suspected_symbols"]
            else ""
        )

        prompt = f"""Project: "{prep_res['repo']}"

Alert summary: "{prep_res['alert_summary']}"
Error type: "{prep_res['error_type']}"
{symbols_hint}

Available abstractions:
{prep_res['abstraction_text']}

Which abstractions are most likely involved in this alert?
Also list the key symbols (functions/classes) that should be investigated.

Output YAML only:

```yaml
relevant_abstractions:
  - Auth Layer
  - Data Access
key_symbols:
  - authenticate
  - get_user_by_token
```"""

        # Graceful fallback on a parse miss (data={}), so retries are
        # transport-only — reading the cache on retry is always safe here.
        response, tokens_in, tokens_out, was_cached = await call_llm(
            prompt, model_id=prep_res["model"], use_cache=True
        )
        ms = int((time.monotonic() - t0) * 1000)

        try:
            yaml_str = response.split("```yaml")[1].split("```")[0].strip()
            data = yaml.safe_load(yaml_str) or {}
        except Exception:
            data = {}

        relevant_abstractions = data.get("relevant_abstractions", [])
        if not isinstance(relevant_abstractions, list):
            relevant_abstractions = []

        key_symbols = data.get("key_symbols", []) or prep_res["suspected_symbols"]
        if not isinstance(key_symbols, list):
            key_symbols = list(prep_res["suspected_symbols"])

        scope_files: List[str] = []
        seen: set = set()
        for name in relevant_abstractions:
            for path in prep_res["file_map"].get(name, []):
                if path not in seen:
                    seen.add(path)
                    scope_files.append(path)

        # Ground the scope in the knowledge graph. The abstraction file_map only
        # samples a fraction of a large repo, so resolve the alert's symbols to
        # their real, indexed files via kg_search — disk-verified definitions the
        # LLM/file_map can miss. This is what makes RCA work on large repos.
        from app.crawler.kg_search import kg_search
        for sym in list(key_symbols)[:_MAX_SYMBOLS]:
            if not sym:
                continue
            try:
                for hit in await kg_search(prep_res["repo"], str(sym), limit=3):
                    p = hit.get("file")
                    if p and p not in seen:
                        seen.add(p)
                        scope_files.append(p)
            except Exception as exc:  # noqa: BLE001 — grounding is best-effort
                logger.debug("RCA kg_search failed for %r: %s", sym, exc)

        return {
            "relevant_abstractions": relevant_abstractions,
            "key_symbols": key_symbols[:_MAX_SYMBOLS],
            "scope_files": scope_files,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cached": was_cached,
            "ms": ms,
        }

    async def post(
        self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]
    ) -> Optional[str]:
        shared["_relevant_abstractions"] = exec_res["relevant_abstractions"]
        shared["_key_symbols"] = exec_res["key_symbols"]
        shared["_scope_files"] = exec_res["scope_files"]
        _append_trace(
            shared, "IdentifyLikelyAbstractions",
            len(exec_res["key_symbols"]),
            exec_res["tokens_in"], exec_res["tokens_out"], exec_res["cached"],
            llm_calls=1, ms=exec_res["ms"],
        )
        logger.info(
            "IdentifyLikelyAbstractions: %d symbols, %d scope files",
            len(exec_res["key_symbols"]), len(exec_res["scope_files"]),
        )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 4: FindSymbolsInScope
# ─────────────────────────────────────────────────────────────────────────────

class FindSymbolsInScope(AsyncNode):
    """Read scope files from disk to build the investigation context."""

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "repo": shared["repo"],
            "scope_files": shared["_scope_files"],
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        import asyncio
        from app.core.security import check_path, PathJailError

        from app.config import settings
        repos_root = settings.repos_base_path
        repo_dir = os.path.join(repos_root, prep_res["repo"])
        _ctx_cap = settings.crawler_search_context_max_chars

        def _read_scope() -> str:
            parts = []
            total = 0
            for relpath in prep_res["scope_files"]:
                if not relpath:
                    continue
                abs_path = os.path.join(repo_dir, relpath)
                try:
                    check_path(abs_path, repos_root)
                    with open(abs_path, encoding="utf-8-sig", errors="replace") as f:
                        content = f.read()
                    truncated = content[:_MAX_FILE_CHARS]
                    entry = f"--- File: {relpath} ---\n{truncated}\n\n"
                    if total + len(entry) > _ctx_cap:
                        break
                    parts.append(entry)
                    total += len(entry)
                except Exception as exc:
                    logger.debug("FindSymbolsInScope: skip %s — %s", relpath, exc)
            return "".join(parts)

        t0 = time.monotonic()
        file_context = await asyncio.to_thread(_read_scope)
        ms = int((time.monotonic() - t0) * 1000)

        return {"file_context": file_context, "ms": ms}

    async def post(
        self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]
    ) -> Optional[str]:
        shared["_investigation_context"] = exec_res["file_context"]
        _append_trace(shared, "FindSymbolsInScope", len(prep_res["scope_files"]),
                      0, 0, False, ms=exec_res["ms"])
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 5: SynthesizeRootCause
# ─────────────────────────────────────────────────────────────────────────────

class SynthesizeRootCause(AsyncNode):
    """Synthesise a root-cause analysis from all collected evidence."""

    def __init__(self, max_retries: int = 3, wait: float = 10.0):
        super().__init__(max_retries=max_retries, wait=wait)

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        parsed = shared["_alert_parsed"]
        return {
            "repo": shared["repo"],
            "alert_summary": parsed.get("summary", ""),
            "severity": parsed.get("severity", "unknown"),
            "error_type": parsed.get("error_type", ""),
            "error_message": parsed.get("error_message", ""),
            "stack_trace": parsed.get("stack_trace_snippet", ""),
            "key_symbols": shared["_key_symbols"],
            "relevant_abstractions": shared["_relevant_abstractions"],
            "file_context": shared["_investigation_context"],
            "model": shared.get("model_id"),
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        from app.crawler.call_llm import call_llm
        import yaml

        t0 = time.monotonic()
        stack_section = (
            f"\nStack trace:\n{prep_res['stack_trace']}" if prep_res["stack_trace"] else ""
        )
        error_section = (
            f"\nError message: {prep_res['error_message']}" if prep_res["error_message"] else ""
        )

        prompt = f"""You are an on-call engineer performing root-cause analysis.

Project: "{prep_res['repo']}"
Alert: "{prep_res['alert_summary']}"
Severity: {prep_res['severity']}
Error type: {prep_res['error_type']}{error_section}{stack_section}

Relevant abstractions: {prep_res['relevant_abstractions']}
Key symbols investigated: {prep_res['key_symbols']}

Code context:
{prep_res['file_context'][:100_000]}

Provide a thorough root-cause analysis.

IMPORTANT:
- Remediation suggestions are always "suggestive" grade — never claim certainty.
- Base all findings on the code context provided, not assumptions.

Output YAML only:

```yaml
root_cause: "Precise description of what is causing the alert"
confidence: "high|medium|low"
contributing_factors:
  - "Factor one"
evidence_files:
  - path/to/relevant.py
recommendations:
  - action: "What to check or fix"
    grade: "suggestive"
    rationale: "Why this might help"
immediate_actions:
  - "Check X log"
```"""

        # Graceful fallback on a parse miss (rca defaults), so retries are
        # transport-only — reading the cache on retry is always safe here.
        response, tokens_in, tokens_out, was_cached = await call_llm(
            prompt, model_id=prep_res["model"], use_cache=True,
            max_tokens=8192,
        )
        ms = int((time.monotonic() - t0) * 1000)

        try:
            yaml_str = response.split("```yaml")[1].split("```")[0].strip()
            rca = yaml.safe_load(yaml_str) or {}
        except Exception:
            rca = {"root_cause": "Could not parse RCA.", "confidence": "low"}

        return {
            "rca": rca,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cached": was_cached,
            "ms": ms,
        }

    async def post(
        self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]
    ) -> Optional[str]:
        shared["_rca"] = exec_res["rca"]
        _append_trace(
            shared, "SynthesizeRootCause", 1,
            exec_res["tokens_in"], exec_res["tokens_out"], exec_res["cached"],
            llm_calls=1, ms=exec_res["ms"],
        )
        logger.info("SynthesizeRootCause: confidence=%s", exec_res["rca"].get("confidence", "?"))
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Node 6: BuildReport
# ─────────────────────────────────────────────────────────────────────────────

class BuildReport(AsyncNode):
    """Assemble the final investigation report."""

    async def prep(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        parsed = shared["_alert_parsed"]
        return {
            "repo": shared["repo"],
            "alert_summary": parsed.get("summary", shared["alert"][:200]),
            "severity": parsed.get("severity", "unknown"),
            "rca": shared["_rca"],
            "key_symbols": shared["_key_symbols"],
            "relevant_abstractions": shared["_relevant_abstractions"],
        }

    async def exec(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        rca = prep_res["rca"]
        raw_recs = rca.get("recommendations", [])
        recommendations = []
        if isinstance(raw_recs, list):
            for r in raw_recs:
                if isinstance(r, dict):
                    recommendations.append({
                        "action": str(r.get("action", "")),
                        "grade": str(r.get("grade", "suggestive")),
                        "rationale": str(r.get("rationale", "")),
                    })
                else:
                    recommendations.append({"action": str(r), "grade": "suggestive", "rationale": ""})

        return {
            "alert_summary": prep_res["alert_summary"],
            "repo": prep_res["repo"],
            "severity": prep_res["severity"],
            "root_cause": str(rca.get("root_cause", "")),
            "confidence": str(rca.get("confidence", "low")),
            "contributing_factors": rca.get("contributing_factors", []),
            "evidence_files": rca.get("evidence_files", []),
            "recommendations": recommendations,
            "immediate_actions": rca.get("immediate_actions", []),
            "contributing_symbols": prep_res["key_symbols"],
            "contributing_abstractions": prep_res["relevant_abstractions"],
        }

    async def post(
        self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]
    ) -> Optional[str]:
        shared["response"] = exec_res
        _append_trace(shared, "BuildReport", 1, 0, 0, False)
        logger.info(
            "BuildReport: severity=%s confidence=%s",
            exec_res["severity"], exec_res["confidence"],
        )
        return None
