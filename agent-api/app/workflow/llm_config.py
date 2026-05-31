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

from app.infrastructure.persistence import llm_config_repository, model_key_repository

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
_DEFAULT_MAX_TOKENS  = 4096
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


def find_llm_node_for_consumer(
    workflow: Dict[str, Any],
    consumer_node_id: str,
    target_handles: Sequence[str] = ("model", "lm"),
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Return the LLM node wired to *consumer_node_id*'s model port.

    Walks workflow edges where ``target == consumer_node_id`` and
    ``targetHandle`` is one of *target_handles* (legacy ReactFlow uses
    ``model``; LangflowEditor uses ``model`` on the router slot).

    Returns:
        ``(node, connected)`` — *connected* is True when an edge-linked LM
        was found; False when falling back to :func:`find_llm_node`.
    """
    handles = frozenset(target_handles) | _LM_TARGET_HANDLES
    node_by_id = {
        n.get("id"): n
        for n in (workflow.get("nodes") or [])
        if n.get("id")
    }

    for edge in workflow.get("edges") or []:
        if edge.get("target") != consumer_node_id:
            continue
        th = edge.get("targetHandle") or ""
        if th and th not in handles:
            continue
        src_id = edge.get("source")
        src = node_by_id.get(src_id) if src_id else None
        if src and src.get("type") in LLM_NODE_TYPES:
            return src, True

    fallback = find_llm_node(workflow)
    if fallback:
        logger.warning(
            "No LLM edge to consumer '%s' — falling back to first workflow LLM '%s'",
            consumer_node_id,
            fallback.get("id"),
        )
    return fallback, False


def read_llm_node(node: Optional[Dict[str, Any]]) -> LLMNodeConfig:
    """Extract a normalised :class:`LLMNodeConfig` from one node.

    Handles all three schema dialects we ship:

    * **LangflowEditor** ``language_model`` nodes — config under
      ``node.params`` with snake_case keys (``llm``, ``temp``, ``system``).
    * **Legacy ReactFlow** ``llm`` nodes — config under ``node.data``
      with camelCase keys (``configName``, ``maxTokens``, ``baseUrl``).
    * **Inline-config** nodes — ``data.model`` + ``data.provider`` set
      directly (rare, used by tests).

    ``params`` always wins over ``data`` when both are present.
    """
    if not node:
        return LLMNodeConfig()

    p = node.get("params") or {}
    d = node.get("data")   or {}

    # ``params.llm`` is the chosen DB-config name in the new editor.
    config_name = (
        p.get("llm")
        or d.get("configName")
        or d.get("llmConfigId")
        or None
    )

    temperature = _coerce_float(p.get("temp") or d.get("temperature"))
    max_tokens  = _coerce_int  (d.get("maxTokens") or d.get("max_tokens"))

    return LLMNodeConfig(
        config_name      = config_name,
        inline_provider  = d.get("provider"),
        inline_model     = d.get("model"),
        temperature      = temperature,
        max_tokens       = max_tokens,
        region           = d.get("region"),
        base_url         = d.get("baseUrl") or d.get("base_url"),
        system           = p.get("system") or d.get("system"),
    )


# ─── Stage 2: resolve config from sources, in priority order ──────────────────


async def _resolve_from_sources(cfg: LLMNodeConfig) -> Optional[ResolvedLLMConfig]:
    """Try each source in priority order, returning the first hit.

    1. **Inline** — node has explicit provider+model.
    2. **Named** — node references a config saved in Settings by name.
    3. **Default** — first config in the DB (deterministic dict order).
    """
    for source in (_resolve_inline, _resolve_named, _resolve_default):
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
    node, _ = find_llm_node_for_consumer(workflow, consumer_node_id, target_handles)
    return await _resolve_llm_config_from_node(node)


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
