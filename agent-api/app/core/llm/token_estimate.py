"""Single token-estimation entry point for the whole app.

Bedrock exposes no tokenizer, so sizing decisions run on a chars/4 heuristic.
That heuristic used to be re-implemented at nine separate call sites, each with
slightly different rounding and only ONE of them (history compaction) applying
the learned per-model correction from :mod:`app.core.llm.token_calibration`.
Everything routes through here now, so a single change moves every budget.

Two bases:

* ``estimate_tokens`` — chars/4, scaled by the calibration factor. The factor is
  1.0 (exact no-op) unless calibration is enabled AND a real observation has
  been recorded, so the default config is byte-identical to raw chars/4.
* ``count_tokens_exact`` — a real BPE count via tiktoken's ``cl100k_base`` when
  the package is importable. NOT calibration-scaled: the factor is learned as
  ``actual / chars4_estimate`` and means nothing applied to a real tokenization.
  cl100k is an OpenAI tokenizer, so against a Bedrock model this is still an
  approximation — just a much closer one than chars/4. Use it where the count
  drives a hard truncation (CloudWatch output budgets), not where it drives a
  soft threshold.

The calibration loop closes over ``estimate_tokens``: the pre-model hook feeds
back ``(estimate, actual)`` pairs built from this same function, so the EWMA
converges regardless of what the base does.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4

# Lazily-built tiktoken encoder. ``False`` records a failed import so the
# attempt is made once per process rather than once per call.
_ENCODER: Any = None


def _apply_factor(base: int, model: Optional[str]) -> int:
    """Scale a heuristic estimate by the learned per-model factor."""
    try:
        from app.core.llm import token_calibration
        factor = (
            token_calibration.factor_for(model)
            if model is not None
            else token_calibration.current_factor()
        )
        if factor != 1.0:
            return max(1, int(base * factor))
    except Exception:  # noqa: BLE001 — calibration must never break estimation
        pass
    return base


def estimate_tokens(text: str, *, model: Optional[str] = None) -> int:
    """Calibrated chars/4 estimate for a string. 0 for empty input.

    This is the default for every soft budget: compaction thresholds, injected
    context sizing, chat-replay trimming, pinned-fact budgets.
    """
    if not text:
        return 0
    base = max(1, len(text) // _CHARS_PER_TOKEN)
    return _apply_factor(base, model)


def estimate_messages_tokens(
    messages: Optional[List[Dict[str, Any]]],
    *,
    model: Optional[str] = None,
) -> int:
    """Calibrated estimate for a list of message-shaped dicts."""
    if not messages:
        return 0
    return estimate_tokens("".join(str(m) for m in messages), model=model)


def estimate_request_tokens(
    messages: Optional[List[Dict[str, Any]]],
    *,
    system_prompt: str = "",
    tools: Optional[List[Dict[str, Any]]] = None,
    model: Optional[str] = None,
) -> int:
    """Calibrated estimate for a full request: system + messages + tool schemas.

    With many tools bound, schemas alone can add 20-30K tokens, so they are
    counted rather than assumed negligible.
    """
    parts: List[str] = []
    if system_prompt:
        parts.append(system_prompt)
    if messages:
        parts.extend(str(m) for m in messages)
    if tools:
        parts.append(str(tools))
    return estimate_tokens("".join(parts), model=model)


def get_encoder() -> Any:
    """The cl100k_base encoder, or ``None`` when tiktoken is unavailable.

    Exposed for callers that need the encoder itself rather than a count — e.g.
    positional truncation, which slices token ids and decodes back.
    """
    global _ENCODER
    if _ENCODER is None:
        try:
            import tiktoken  # type: ignore
            _ENCODER = tiktoken.get_encoding("cl100k_base")
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "tiktoken unavailable (%s); token counts fall back to the "
                "chars/%d heuristic.", exc, _CHARS_PER_TOKEN,
            )
            _ENCODER = False
    return _ENCODER or None


def count_tokens_exact(text: str) -> int:
    """Real BPE token count, falling back to the chars/4 heuristic.

    Uncalibrated by design — see the module docstring.
    """
    if not text:
        return 0
    enc = get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:  # noqa: BLE001 — counting must never break a caller
            pass
    return max(1, len(text) // _CHARS_PER_TOKEN)


__all__ = [
    "estimate_tokens",
    "estimate_messages_tokens",
    "estimate_request_tokens",
    "count_tokens_exact",
    "get_encoder",
]
