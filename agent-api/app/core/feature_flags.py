"""Runtime-editable feature-flag overlay.

Feature flags are declared in ``app.config.Settings`` and default from environment
variables. This module lets an operator override them at runtime from the Settings
UI without a container restart, following the same persist-then-apply pattern used
for the global timezone (``app.core.app_timezone``):

  * Overrides are stored in the generic ``app_settings`` key/value table under the
    ``flag:`` prefix (migration 011 — no new table needed).
  * On startup (and on every write) the stored overrides are applied in-place to
    the process-wide ``settings`` singleton, so all existing ``settings.<flag>``
    reads transparently see the effective value.

Single-worker assumption: the shipped deployment runs ``UVICORN_WORKERS=1``, so the
singleton is the source of truth. If multiple workers are ever enabled, a write in
one worker won't propagate to the others until they reload — acceptable for these
opt-in toggles, and callable via ``load_overlay()`` on demand.

Infrastructure/security/startup-only flags (API auth, secrets manager, OTEL,
startup indexing recovery, supervisor HITL) are intentionally NOT exposed here —
they must stay environment-controlled.

Likewise the numeric tuning knobs and experimental/performance toggles (LangGraph
runtime, self-improvement & governance, code-semantic indexing, chat tool-replay
limits, skill budgets) are NOT surfaced: they are code-baked at their best
defaults in ``Settings`` and overridable only via their environment variables.
Only the handful of flags an operator genuinely flips live in ``FLAG_CATALOG``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.config import settings
from app.infrastructure.persistence import app_settings_repository

logger = logging.getLogger(__name__)

_DB_PREFIX = "flag:"

# Declarative catalog. ``key`` matches a ``Settings`` attribute exactly; ``type``
# drives coercion + the UI control. Grouped for display. Keep this list as the
# single source of truth — the API and UI both render from it.
#
# This is deliberately a SHORT list of the flags an operator actually flips. The
# many numeric tuning knobs (compression sizes/timeouts, skill budgets, chat
# tool-replay limits, code-semantic indexing knobs, Bedrock pool size) and the
# experimental / performance toggles (LangGraph runtime, self-improvement &
# governance) are NOT exposed here: they are code-baked at their best defaults in
# ``app.config.Settings`` and remain overridable only via their environment
# variables (same pattern as ``code_semantic_enabled``, which is forced on in
# config and never surfaced). To re-expose one, re-add its ``{key, type, group,
# label, help}`` entry below — nothing in the API or UI needs to change.
FLAG_CATALOG: List[Dict[str, Any]] = [
    # ── Tool-output compression ──
    {"key": "compression_enabled", "type": "bool", "group": "Compression",
     "label": "Compress MCP tool output",
     "help": "Send large MCP tool results through the compression sidecar before they enter context."},
    {"key": "compression_all_tools", "type": "bool", "group": "Compression",
     "label": "Compress all tool output",
     "help": "Extend compression to non-MCP tools (CloudWatch, DB, Code Crawler). Biggest win for log-heavy runs."},

    # ── Memory & learning ──
    {"key": "semantic_memory_enabled", "type": "bool", "group": "Memory & learning",
     "label": "Semantic memory recall",
     "help": "Retrieve bank-scoped semantic memories (hybrid FTS+vector) per turn."},
    {"key": "memory_fact_extraction_enabled", "type": "bool", "group": "Memory & learning",
     "label": "Durable-fact extraction",
     "help": "After each turn, extract a few durable facts into semantic memory."},
    {"key": "memory_audit_enabled", "type": "bool", "group": "Memory & learning",
     "label": "Memory audit / consolidation",
     "help": "Let the curator run LLM consolidation of stored memories."},
    {"key": "pinned_facts_enabled", "type": "bool", "group": "Memory & learning",
     "label": "Pinned facts",
     "help": "Inject the operator-pinned facts memory tier into context."},

    # ── Agent behaviour ──
    {"key": "agent_planning_enabled", "type": "bool", "group": "Agent behaviour",
     "label": "Plan → execute → verify",
     "help": "Add the planning discipline and planning tools to the system prompt."},
    {"key": "pii_pseudonymization_enabled", "type": "bool", "group": "Agent behaviour",
     "label": "PII pseudonymization",
     "help": "Pseudonymize PII in flagship/KYC flows before it reaches the model."},

    # ── Reliability ──
    {"key": "routing_fallback_enabled", "type": "bool", "group": "Reliability",
     "label": "Model routing fallback",
     "help": "On Bedrock errors, fall back across credential/region/model chains."},

    # ── Supervision (Action Supervisor write-gate) ──
    {"key": "action_supervisor_enabled", "type": "bool", "group": "Supervision",
     "label": "Action Supervisor",
     "help": "Review every intercepted write-class action (edits, run_command, "
             "playbook/KB writes, MCP mutations, wiki publish) before it runs."},
    {"key": "action_supervisor_shadow_mode", "type": "bool", "group": "Supervision",
     "label": "Shadow mode",
     "help": "Review and record the verdict as advice, but the human/timeout "
             "still decides. Turn off to let the Supervisor auto-decide low-risk "
             "actions. Calibrate in shadow first."},
    {"key": "wiki_publish_approval_enabled", "type": "bool", "group": "Supervision",
     "label": "Gate wiki publish",
     "help": "Route the wiki-publish output node through the approval gate."},
]

_CATALOG_BY_KEY = {spec["key"]: spec for spec in FLAG_CATALOG}

# Pristine environment/default values, snapshotted at import BEFORE any overlay is
# applied, so the UI can offer "reset to default".
_DEFAULTS: Dict[str, Any] = {spec["key"]: getattr(settings, spec["key"]) for spec in FLAG_CATALOG}


def _coerce(spec: Dict[str, Any], raw: Any) -> Any:
    t = spec["type"]
    if t == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if t == "int":
        return int(raw)
    if t == "float":
        return float(raw)
    return "" if raw is None else str(raw)


def _apply(key: str, value: Any) -> None:
    setattr(settings, key, value)


async def load_overlay() -> Dict[str, Any]:
    """Read stored overrides and apply them to the settings singleton.

    Best-effort: a failure here must never block startup. Returns the applied
    overrides (for logging/inspection).
    """
    applied: Dict[str, Any] = {}
    try:
        stored = await app_settings_repository.all()
    except Exception as exc:  # noqa: BLE001 — settings overlay must not block startup
        logger.warning("feature_flags: could not load overrides (%s)", exc)
        return applied

    for db_key, raw in stored.items():
        if not db_key.startswith(_DB_PREFIX):
            continue
        key = db_key[len(_DB_PREFIX):]
        spec = _CATALOG_BY_KEY.get(key)
        if spec is None:
            continue
        try:
            value = _coerce(spec, raw)
            _apply(key, value)
            applied[key] = value
        except Exception as exc:  # noqa: BLE001 — skip a bad row, keep the rest
            logger.warning("feature_flags: skipping bad override %s=%r (%s)", key, raw, exc)

    if applied:
        logger.info("feature_flags: applied %d runtime override(s): %s",
                    len(applied), ", ".join(f"{k}={v!r}" for k, v in applied.items()))
    return applied


async def set_flags(updates: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Persist and apply a batch of flag updates. Returns the new effective list.

    A value of ``None`` clears the override and restores the environment default.
    Unknown keys are rejected (ValueError) so the API returns 400.
    """
    for key in updates:
        if key not in _CATALOG_BY_KEY:
            raise ValueError(f"Unknown feature flag: {key!r}")

    for key, raw in updates.items():
        spec = _CATALOG_BY_KEY[key]
        db_key = f"{_DB_PREFIX}{key}"
        if raw is None:
            await app_settings_repository.set(db_key, "")  # tombstone → treat as default
            _apply(key, _DEFAULTS[key])
            continue
        value = _coerce(spec, raw)
        await app_settings_repository.set(db_key, str(value))
        _apply(key, value)

    return effective()


def effective() -> List[Dict[str, Any]]:
    """Return the catalog annotated with current value + environment default."""
    out: List[Dict[str, Any]] = []
    for spec in FLAG_CATALOG:
        key = spec["key"]
        out.append({
            "key": key,
            "label": spec["label"],
            "group": spec["group"],
            "type": spec["type"],
            "help": spec["help"],
            "value": getattr(settings, key),
            "default": _DEFAULTS[key],
        })
    return out
