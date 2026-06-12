"""LLM configuration resolution — clean architecture.

This module is the single source of truth for "given a workflow, what
LLM should the agent talk to?".  It collapses the previous 115-line
inline routine in :mod:`react.py` into a small pipeline of focused
functions:

    workflow ──▶ find_llm_node  ──▶ read_llm_node  ──▶ LLMNodeConfig
                                                            │
                                                            ▼
                                         _resolve_from_sources  (inline → named DB → first DB)
                                                            │
                                                            ▼
                                                ResolvedLLMConfig
                                                            │
                                                            ▼
                                         _enrich_credentials  (Model Keys: API key or AWS creds)
                                                            │
                                                            ▼
                                                       dict (legacy contract)

The public entry point is :func:`resolve_llm_config`.  Each stage is
independently testable and never reaches into the workflow shape
again — node-schema knowledge lives only in :func:`read_llm_node`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional, Sequence, Tuple

from app.config import settings
from app.infrastructure.persistence import (
    llm_config_repository,
    model_key_repository,
    model_role_repository,
)

logger = logging.getLogger(__name__)


# ─── Constants ────────────────────────────────────────────────────────────────

#: Node ``type`` values that represent an LLM configuration node.
#: ``llm`` is the legacy ReactFlow type; ``language_model`` is the new
#: LangflowEditor type.  Both are accepted everywhere.
LLM_NODE_TYPES = ("llm", "language_model")

#: Provider strings that authenticate via AWS credentials rather than a
#: bearer API key.  Compared case-insensitively.
_BEDROCK_PROVIDERS = {"bedrock", "aws", "aws_bedrock", "aws bedrock"}

#: Provider strings that do not require any credential lookup.
_NO_CREDENTIALS_PROVIDERS = {"ollama"}

#: Names to try when looking up the Bedrock entry in the Model Keys table.
_BEDROCK_KEY_ALIASES = ("AWS Bedrock", "bedrock", "aws bedrock", "aws")

_DEFAULT_TEMPERATURE = 0.1
# Sourced from settings so it can be tuned via AGENT_MAX_OUTPUT_TOKENS without
# a code change. Defaults to 8192 — see Settings.agent_max_output_tokens for why
# 4096 was too small (truncated mid-reasoning before a tool_use could be emitted).
_DEFAULT_MAX_TOKENS  = settings.agent_max_output_tokens
_DEFAULT_REGION      = "us-east-1"


# ─── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class LLMNodeConfig:
    """Normalised view of an LLM/language_model node's user-supplied config.

    Reflects exactly what the user set on the node — *no* DB lookups,
    *no* credentials.  All fields optional because users may configure
    inline (provider+model), by reference (config_name), or not at all
    (fall back to first DB config).
    """
    config_name: Optional[str]      = None
    inline_provider: Optional[str]  = None
    inline_model: Optional[str]     = None
    temperature: Optional[float]    = None
    max_tokens: Optional[int]       = None
    region: Optional[str]           = None
    base_url: Optional[str]         = None
    system: Optional[str]           = None


@dataclass
class ResolvedLLMConfig:
    """Final, ready-to-use LLM config.  Mirrors the legacy dict contract.

    Always carries ``provider`` and ``model``.  Defaults applied for
    temperature/max_tokens/region.  Credential fields populated by
    :func:`_enrich_credentials`.
    """
    provider: str
    model: str
    temperature: float          = _DEFAULT_TEMPERATURE
    max_tokens: int             = _DEFAULT_MAX_TOKENS
    region: str                 = _DEFAULT_REGION
    base_url: Optional[str]     = None
    system: Optional[str]       = None
    api_key: Optional[str]      = None
    access_key_id: Optional[str]     = None
    secret_access_key: Optional[str] = None
    session_token: Optional[str]     = None
    aws_profile: Optional[str]       = None

    def to_dict(self) -> Dict[str, Any]:
        """Return the legacy-shaped dict consumed by react.py / batch_react.py.

        Drops ``None`` values so consumers using ``.get(key) or default``
        still see truthy defaults rather than explicit nulls.
        """
        return {k: v for k, v in asdict(self).items() if v is not None}


# ─── Stage 1: node discovery + schema normalisation ───────────────────────────


def find_llm_node(workflow: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the first LLM node in *workflow* (or ``None``)."""
    for node in workflow.get("nodes", []) or []:
        if node.get("type") in LLM_NODE_TYPES:
            return node
    return None


# Handles accepted on edges targeting a consumer's Language Model port.
_LM_TARGET_HANDLES = frozenset({"model", "lm"})

# Per-consumer-port → accepted target-handle names. The agent node exposes three
# distinct model input ports; each resolves its own wired LLM/model.
_PORT_HANDLES: Dict[str, Tuple[str, ...]] = {
    "lm":       ("model", "lm"),
    "crawler":  ("crawler",),
    "subagent": ("subagent",),
}

# Source-slot prefix used by a multi-model Language Model node to identify which
# of its models a given output port (and therefore edge) refers to: ``lm::<name>``.
_MODEL_SLOT_PREFIX = "lm::"


def _edge_target_slot(edge: Dict[str, Any]) -> str:
    """Coalesce the target-port id across editor (``targetSlot``) and legacy
    ReactFlow (``targetHandle``) edge dialects."""
    return str(edge.get("targetSlot") or edge.get("targetHandle") or "")


def _edge_source_slot(edge: Dict[str, Any]) -> str:
    """Coalesce the source-port id across ``sourceSlot`` / ``sourceHandle``."""
    return str(edge.get("sourceSlot") or edge.get("sourceHandle") or "")


def _model_key_from_slot(source_slot: str) -> Optional[str]:
    """Extract the model name from a multi-model output slot id (``lm::<name>``)."""
    if source_slot.startswith(_MODEL_SLOT_PREFIX):
        return source_slot[len(_MODEL_SLOT_PREFIX):] or None
    return None


def find_llm_node_for_consumer(
    workflow: Dict[str, Any],
    consumer_node_id: str,
    target_handles: Sequence[str] = ("model", "lm"),
    *,
    accept_untagged: bool = True,
    fallback: bool = True,
) -> Tuple[Optional[Dict[str, Any]], str, bool]:
    """Return the LLM node wired to *consumer_node_id*'s port.

    Walks workflow edges where ``target == consumer_node_id`` and the (coalesced)
    target slot is one of *target_handles*. Edge dialects differ: the LangflowEditor
    writes ``sourceSlot``/``targetSlot``; legacy ReactFlow writes
    ``sourceHandle``/``targetHandle`` — both are honoured.

    Args:
        accept_untagged: when True, an edge with no target slot still matches
            (legacy single ``model→agent`` edge). The crawler/subagent ports pass
            False so an untagged main-model edge can't bleed into them.
        fallback: when True, fall back to the first workflow LLM node if no edge
            matches (preserves the original single-model behaviour).

    Returns:
        ``(node, source_slot, connected)`` — *source_slot* is the matched edge's
        source port id (used to pick which model of a multi-model node); *connected*
        is True when an edge-linked LM was found.
    """
    handles = frozenset(target_handles)
    node_by_id = {
        n.get("id"): n
        for n in (workflow.get("nodes") or [])
        if n.get("id")
    }

    for edge in workflow.get("edges") or []:
        if edge.get("target") != consumer_node_id:
            continue
        th = _edge_target_slot(edge)
        if th:
            if th not in handles:
                continue
        elif not accept_untagged:
            continue
        src_id = edge.get("source")
        src = node_by_id.get(src_id) if src_id else None
        if src and src.get("type") in LLM_NODE_TYPES:
            return src, _edge_source_slot(edge), True

    if fallback:
        fb = find_llm_node(workflow)
        if fb:
            logger.warning(
                "No LLM edge to consumer '%s' — falling back to first workflow LLM '%s'",
                consumer_node_id,
                fb.get("id"),
            )
        return fb, "", False
    return None, "", False


def _offered_models(p: Dict[str, Any], d: Dict[str, Any]) -> Tuple[list, Dict[str, Dict[str, Any]]]:
    """Return ``(names, per_model)`` the node offers, across dialects.

    * New multi-model node: ``params.models = [{name, temp, system}, ...]``.
    * Comma-joined multi-select: ``params.llm = "A,B"``.
    * Legacy single: ``params.llm`` / ``data.configName`` / ``data.llmConfigId``.
    """
    names: list = []
    per_model: Dict[str, Dict[str, Any]] = {}

    models = p.get("models")
    if isinstance(models, list) and models:
        for m in models:
            if isinstance(m, dict) and m.get("name"):
                names.append(str(m["name"]))
                per_model[str(m["name"])] = m
            elif isinstance(m, str) and m.strip():
                names.append(m.strip())
        return names, per_model

    raw = p.get("llm") or d.get("configName") or d.get("llmConfigId")
    if raw:
        names = [s.strip() for s in str(raw).split(",") if s.strip()]
    return names, per_model


def read_llm_node(
    node: Optional[Dict[str, Any]],
    model_key: Optional[str] = None,
) -> LLMNodeConfig:
    """Extract a normalised :class:`LLMNodeConfig` from one node.

    Handles all schema dialects we ship:

    * **Multi-model** ``language_model`` nodes — ``params.models`` list, each an
      output port. *model_key* (parsed from the wired edge's ``lm::<name>`` source
      slot) selects which one; its per-model ``temp``/``system`` win.
    * **LangflowEditor** single — ``params`` with ``llm``/``temp``/``system``.
    * **Legacy ReactFlow** — ``data`` with ``configName``/``maxTokens``/``baseUrl``.
    * **Inline-config** — ``data.model`` + ``data.provider`` set directly.

    ``params`` always wins over ``data`` when both are present.
    """
    if not node:
        return LLMNodeConfig()

    p = node.get("params") or {}
    d = node.get("data")   or {}

    names, per_model = _offered_models(p, d)

    # Pick the model: the edge-selected key if it's on offer, else the first.
    chosen: Optional[str] = None
    if model_key and (not names or model_key in names):
        chosen = model_key
    elif names:
        if model_key:
            # The wired edge named a model this node no longer offers (e.g. a
            # hand-edited/imported workflow that skipped reconcileModelEdges).
            # Fall back to the first model, but warn — the resolved model won't
            # match the wire's intent.
            logger.warning(
                "read_llm_node: edge model_key %r not among node models %s — "
                "falling back to %r", model_key, names, names[0],
            )
        chosen = names[0]
    else:
        chosen = p.get("llm") or d.get("configName") or d.get("llmConfigId") or None

    m = per_model.get(chosen or "", {})

    # Per-model temp/system override the node-level values when present.
    temperature = _coerce_float(
        m.get("temp") if m else None
    )
    if temperature is None:
        temperature = _coerce_float(p.get("temp") or d.get("temperature"))
    max_tokens = _coerce_int(d.get("maxTokens") or d.get("max_tokens"))
    system = (m.get("system") if m else None) or p.get("system") or d.get("system")

    return LLMNodeConfig(
        config_name      = chosen,
        inline_provider  = d.get("provider"),
        inline_model     = d.get("model"),
        temperature      = temperature,
        max_tokens       = max_tokens,
        region           = d.get("region"),
        base_url         = d.get("baseUrl") or d.get("base_url"),
        system           = system,
    )


# ─── Stage 2: resolve config from sources, in priority order ──────────────────


async def _resolve_from_sources(cfg: LLMNodeConfig) -> Optional[ResolvedLLMConfig]:
    """Try each source in priority order, returning the first hit.

    1. **Inline** — node has explicit provider+model.
    2. **Named** — node references a config saved in Settings by name.
    3. **Role** — the gateway "agent" role assignment.
    4. **Default** — first config in the DB (deterministic dict order).
    """
    sources = (
        _resolve_inline,
        _resolve_named,
        lambda c: _resolve_role(c, "agent"),
        _resolve_default,
    )
    for source in sources:
        resolved = await source(cfg)
        if resolved:
            return resolved
    return None


async def _resolve_inline(cfg: LLMNodeConfig) -> Optional[ResolvedLLMConfig]:
    if not (cfg.inline_model and cfg.inline_provider):
        return None
    return _build_resolved(
        provider=cfg.inline_provider,
        model=cfg.inline_model,
        cfg=cfg,
        base={},
    )


async def _resolve_named(cfg: LLMNodeConfig) -> Optional[ResolvedLLMConfig]:
    if not cfg.config_name:
        return None
    try:
        db_cfg = await llm_config_repository.get_by_name(cfg.config_name)
    except Exception as e:
        logger.warning("Could not load LLM config '%s' from DB: %s", cfg.config_name, e)
        return None
    if not db_cfg:
        return None
    return _build_resolved(
        provider=db_cfg["provider"],
        model=db_cfg["model"],
        cfg=cfg,
        base=db_cfg,
    )


async def _resolve_role(cfg: LLMNodeConfig, role: str) -> Optional[ResolvedLLMConfig]:
    """Resolve the LLM config assigned to a gateway *role* (e.g. "agent").

    Returns None when no assignment exists, or when the assigned config was
    deleted (logs a warning and falls through to the next source).
    """
    try:
        config_name = await model_role_repository.get(role)
    except Exception as e:
        logger.warning("Could not look up model role '%s': %s", role, e)
        return None
    if not config_name:
        return None
    try:
        db_cfg = await llm_config_repository.get_by_name(config_name)
    except Exception as e:
        logger.warning("Could not load role-assigned LLM config '%s': %s", config_name, e)
        return None
    if not db_cfg:
        logger.warning(
            "Model role '%s' references deleted LLM config '%s' — falling back",
            role, config_name,
        )
        return None
    logger.info("Using role-assigned LLM config '%s' for role '%s'", config_name, role)
    return _build_resolved(
        provider=db_cfg["provider"],
        model=db_cfg["model"],
        cfg=cfg,
        base=db_cfg,
    )


async def _resolve_default(cfg: LLMNodeConfig) -> Optional[ResolvedLLMConfig]:
    try:
        db_configs = await llm_config_repository.list_all()
    except Exception as e:
        logger.warning("Could not load LLM configs from DB: %s", e)
        return None
    if not db_configs:
        return None
    first_name, db_cfg = next(iter(db_configs.items()))
    logger.info("ReactStrategy: using first available LLM config '%s'", first_name)
    return _build_resolved(
        provider=db_cfg["provider"],
        model=db_cfg["model"],
        cfg=cfg,
        base=db_cfg,
    )


def _build_resolved(
    *,
    provider: str,
    model: str,
    cfg: LLMNodeConfig,
    base: Dict[str, Any],
) -> ResolvedLLMConfig:
    """Apply override precedence: node-config wins, then DB row, then defaults."""
    return ResolvedLLMConfig(
        provider    = provider,
        model       = model,
        temperature = cfg.temperature if cfg.temperature is not None
                      else float(base.get("temperature", _DEFAULT_TEMPERATURE)),
        max_tokens  = cfg.max_tokens if cfg.max_tokens is not None
                      else int(base.get("max_tokens", _DEFAULT_MAX_TOKENS)),
        region      = cfg.region or base.get("region") or _DEFAULT_REGION,
        base_url    = cfg.base_url or base.get("base_url"),
        system      = cfg.system,
    )


# ─── Stage 3: enrich with credentials from Model Keys ─────────────────────────


async def _enrich_credentials(resolved: ResolvedLLMConfig) -> None:
    """Populate API key (non-Bedrock) or AWS credentials (Bedrock) in place."""
    provider = (resolved.provider or "").lower()

    if provider in _NO_CREDENTIALS_PROVIDERS:
        return

    if provider in _BEDROCK_PROVIDERS:
        await _enrich_bedrock_credentials(resolved)
    else:
        await _enrich_api_key_credentials(resolved)


async def _enrich_api_key_credentials(resolved: ResolvedLLMConfig) -> None:
    if resolved.api_key:
        return
    try:
        mk = await model_key_repository.get_by_provider(resolved.provider, include_secrets=True)
    except Exception as e:
        logger.warning("Could not look up Model Key for provider '%s': %s",
                       resolved.provider, e)
        return
    if not mk:
        return
    resolved.api_key = mk.get("api_key") or resolved.api_key
    if not resolved.base_url and mk.get("endpoint"):
        resolved.base_url = mk["endpoint"]
    if mk.get("region") and (not resolved.region or resolved.region == _DEFAULT_REGION):
        resolved.region = mk["region"]


async def _enrich_bedrock_credentials(resolved: ResolvedLLMConfig) -> None:
    for alias in _BEDROCK_KEY_ALIASES:
        try:
            mk = await model_key_repository.get_by_provider(alias, include_secrets=True)
        except Exception as e:
            logger.warning("Could not look up Model Key '%s' for Bedrock: %s", alias, e)
            continue
        if not mk:
            continue
        resolved.access_key_id     = mk.get("access_key_id")     or resolved.access_key_id
        resolved.secret_access_key = mk.get("secret_access_key") or resolved.secret_access_key
        resolved.session_token     = mk.get("session_token")     or resolved.session_token
        if mk.get("region") and (not resolved.region or resolved.region == _DEFAULT_REGION):
            resolved.region = mk["region"]
        break


# ─── Public entry point ───────────────────────────────────────────────────────


async def _resolve_llm_config_from_node(node: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Resolve credentials + model from a specific LLM/language_model node."""
    cfg = read_llm_node(node)
    resolved = await _resolve_from_sources(cfg)
    if not resolved:
        raise ValueError(
            "No LLM configuration available. Configure an LLM in Settings "
            "or add an LLM node to the workflow."
        )
    await _enrich_credentials(resolved)
    return resolved.to_dict()


async def resolve_llm_config(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve an LLM config from *workflow* and return the legacy dict.

    Raises :class:`ValueError` if no source can produce a config — i.e.
    no inline values, no named reference, and no DB configs at all.
    """
    return await _resolve_llm_config_from_node(find_llm_node(workflow))


async def resolve_llm_config_for_node(
    workflow: Dict[str, Any],
    consumer_node_id: str,
    target_handles: Sequence[str] = ("model", "lm"),
) -> Dict[str, Any]:
    """Resolve LLM config for the model port wired to *consumer_node_id*."""
    node, source_slot, _ = find_llm_node_for_consumer(
        workflow, consumer_node_id, target_handles
    )
    cfg = read_llm_node(node, model_key=_model_key_from_slot(source_slot))
    resolved = await _resolve_from_sources(cfg)
    if not resolved:
        raise ValueError(
            "No LLM configuration available. Configure an LLM in Settings "
            "or add an LLM node to the workflow."
        )
    await _enrich_credentials(resolved)
    return resolved.to_dict()


async def resolve_llm_config_for_consumer_port(
    workflow: Dict[str, Any],
    consumer_node_id: str,
    port: str,
) -> Optional[Dict[str, Any]]:
    """Resolve the model wired to a specific *port* of *consumer_node_id*.

    *port* is one of ``"lm"`` (main model, also accepts the legacy ``model``
    handle and untagged single edges), ``"crawler"``, or ``"subagent"``.

    Returns the resolved config dict when a Language Model node is wired to that
    port, else ``None`` so the caller can fall back (global role → first row).
    Only the node's own inline/named config is used — no role/default fallback
    happens here, because an unwired port must return None, not a default.
    """
    handles = _PORT_HANDLES.get(port, (port,))
    node, source_slot, connected = find_llm_node_for_consumer(
        workflow,
        consumer_node_id,
        handles,
        accept_untagged=(port == "lm"),
        fallback=False,
    )
    if not connected or not node:
        return None

    cfg = read_llm_node(node, model_key=_model_key_from_slot(source_slot))
    resolved = await _resolve_inline(cfg) or await _resolve_named(cfg)
    if not resolved:
        return None
    await _enrich_credentials(resolved)
    return resolved.to_dict()


async def resolve_llm_config_for_role(role: str) -> Dict[str, Any]:
    """Resolve the LLM config assigned to a gateway *role*, with fallback.

    Priority: role assignment → first DB config row. Used by non-workflow
    consumers (crawler, subagent) that have no LLM node to read.

    Raises:
        ValueError: if neither the role nor a default DB config resolves.
    """
    cfg = LLMNodeConfig()
    resolved = await _resolve_role(cfg, role)
    if not resolved:
        resolved = await _resolve_default(cfg)
    if not resolved:
        raise ValueError(
            f"No LLM configuration available for role '{role}'. "
            "Configure an LLM in Settings."
        )
    await _enrich_credentials(resolved)
    return resolved.to_dict()


# ─── Small coercion helpers ───────────────────────────────────────────────────


def _coerce_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _coerce_int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
