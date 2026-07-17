"""Token-estimate calibration.

The codebase estimates tokens with a chars/4 heuristic (Bedrock exposes no
tokenizer). That heuristic drifts per model — some tokenizers pack more
characters per token than others — so compaction thresholds can fire early or
late. This module learns a per-model correction factor from ACTUAL Bedrock
usage: for each real model call it compares the chars/4 estimate of the prompt
against the provider-reported prompt-token count and keeps an EWMA of
actual/estimated. The factor is clamped to a sane band so one noisy sample
can't wildly distort estimates.

Opt-in (``settings.token_estimate_calibration_enabled``). Disabled → every
factor is 1.0, i.e. the estimator is byte-identical to the raw chars/4
heuristic. Models are always DB-resolved elsewhere; this only keys learned
factors by whatever model id the caller passes.
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_EWMA_ALPHA = 0.2   # weight of the newest sample
_FACTOR_MIN = 0.7
_FACTOR_MAX = 1.6

_lock = threading.Lock()
_factors: Dict[str, float] = {}   # model -> clamped EWMA factor
_last_model: Optional[str] = None


def is_enabled() -> bool:
    try:
        from app.config import settings
        return bool(getattr(settings, "token_estimate_calibration_enabled", False))
    except Exception:  # noqa: BLE001
        return False


def _clamp(x: float) -> float:
    return max(_FACTOR_MIN, min(_FACTOR_MAX, x))


def record(model: Optional[str], estimated_tokens: int, actual_tokens: int) -> None:
    """Fold one (estimated, actual) prompt-token observation into a model's EWMA.

    No-op when disabled or when either count is non-positive (no ratio to form).
    """
    global _last_model
    if not is_enabled() or estimated_tokens <= 0 or actual_tokens <= 0:
        return
    key = model or "_default"
    ratio = actual_tokens / estimated_tokens
    with _lock:
        prev = _factors.get(key)
        blended = ratio if prev is None else (1 - _EWMA_ALPHA) * prev + _EWMA_ALPHA * ratio
        _factors[key] = _clamp(blended)
        _last_model = key


def factor_for(model: Optional[str]) -> float:
    """Clamped calibration factor for a model. 1.0 when disabled or unseen."""
    if not is_enabled():
        return 1.0
    with _lock:
        return _factors.get(model or "_default", 1.0)


def current_factor() -> float:
    """Factor for the most-recently-recorded model — the bridge for call sites
    (the compaction estimator) that don't carry a model id. 1.0 when disabled or
    before any observation, so it is an exact no-op in the default config."""
    if not is_enabled():
        return 1.0
    with _lock:
        return _factors.get(_last_model, 1.0) if _last_model else 1.0


def reset() -> None:
    """Test hook — clear all learned factors and the last-model pointer."""
    global _last_model
    with _lock:
        _factors.clear()
        _last_model = None


__all__ = ["is_enabled", "record", "factor_for", "current_factor", "reset"]
