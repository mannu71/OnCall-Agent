"""Two-tier model router (plan §2.5).

Maps semantic node *roles* to concrete model identifiers so cheap
subtasks (decompose, classify, format tool args, summarise truncations)
land on Haiku-class models while synthesis / final-answer keeps the
strong tier. ~40% cost reduction on decomposition-heavy workflows.

Strategies call ``model_for(role=...)`` instead of hard-coding a model
string in ``_build_llm``. The mapping is configurable via env so
operators can pin specific snapshot versions without a code change.

Default mapping (override via env)::

    MODEL_TIER_CHEAP   = "claude-haiku-4-5-20251001"
    MODEL_TIER_STRONG  = "claude-sonnet-4-6"
    MODEL_TIER_FALLBACK= "claude-sonnet-4-6"   # used when role is unknown
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Union

logger = logging.getLogger(__name__)


class NodeRole(str, Enum):
    """Semantic role of an LLM call within a workflow.

    Strategies tag each LLM invocation with a role; the router chooses
    which physical model to dispatch to.
    """
    DECOMPOSE = "decompose"      # break a task into subtasks
    CLASSIFY = "classify"         # cheap routing / triage decision
    FORMAT = "format"             # tool-arg shaping, summary writing
    SYNTHESIZE = "synthesize"    # root-cause / cross-source synthesis
    ANSWER = "answer"             # final user-facing answer
    GENERIC = "generic"           # unspecified; uses fallback tier


_CHEAP_ROLES = {NodeRole.DECOMPOSE, NodeRole.CLASSIFY, NodeRole.FORMAT}
_STRONG_ROLES = {NodeRole.SYNTHESIZE, NodeRole.ANSWER}


@dataclass(frozen=True)
class ModelTiers:
    """Concrete model strings for each tier."""
    cheap: str
    strong: str
    fallback: str

    @classmethod
    def from_env(cls) -> "ModelTiers":
        return cls(
            cheap=os.environ.get("MODEL_TIER_CHEAP", "claude-haiku-4-5-20251001"),
            strong=os.environ.get("MODEL_TIER_STRONG", "claude-sonnet-4-6"),
            fallback=os.environ.get("MODEL_TIER_FALLBACK", "claude-sonnet-4-6"),
        )


# Module-level singleton; re-read env on first import. Tests can override
# by constructing their own ModelTiers and calling ``set_tiers``.
_tiers: ModelTiers = ModelTiers.from_env()


def set_tiers(tiers: ModelTiers) -> None:
    """Replace the module-level tier mapping (intended for tests)."""
    global _tiers
    _tiers = tiers


def get_tiers() -> ModelTiers:
    return _tiers


def model_for(role: Union[NodeRole, str], *, override: Optional[str] = None) -> str:
    """Resolve the physical model identifier for *role*.

    - ``override`` (e.g. a workflow-level model pin) always wins.
    - Cheap roles (decompose/classify/format) -> ``MODEL_TIER_CHEAP``.
    - Strong roles (synthesize/answer) -> ``MODEL_TIER_STRONG``.
    - Unknown / GENERIC -> ``MODEL_TIER_FALLBACK``.
    """
    if override:
        return override
    if isinstance(role, str):
        try:
            role = NodeRole(role.lower())
        except ValueError:
            role = NodeRole.GENERIC

    tiers = get_tiers()
    if role in _CHEAP_ROLES:
        return tiers.cheap
    if role in _STRONG_ROLES:
        return tiers.strong
    return tiers.fallback


def explain(role: Union[NodeRole, str]) -> Dict[str, str]:
    """Return a debug dict explaining the routing decision (used in logs)."""
    chosen = model_for(role)
    return {
        "role": role.value if isinstance(role, NodeRole) else str(role),
        "model": chosen,
        "tiers": str(get_tiers()),
    }
