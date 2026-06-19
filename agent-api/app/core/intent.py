"""Lightweight conversational-intent detection for agent turns.

Used to decide whether a user message is small talk (a greeting, thanks, a
"what can you do?") versus a real investigation request. Conversational turns can
skip expensive, credentialed pre-work (e.g. the deterministic CloudWatch scan) and
let the agent simply reply.

Design: a conservative, dependency-free heuristic — **no LLM, no model** (so it
adds zero per-turn cost and pins no model in code). It is biased toward
*investigate*: a message is only treated as conversational when it clearly looks
like small talk AND carries no investigation signal. On any doubt it returns
False, so a genuine query is never mis-skipped (and the agent's tools can recover
regardless).
"""
from __future__ import annotations

import re
from typing import Optional

from app.core.trace_ids import extract_trace_ids

# Whole-message small-talk shapes (the ENTIRE message must match one of these —
# a prefix like "hi, why is auth 500ing?" must NOT count as conversational).
_SMALL_TALK_RE = re.compile(
    r"^\s*(?:"
    r"hi|hii+|hey|hello|hiya|yo|sup|"
    r"good\s*(?:morning|afternoon|evening)|greetings|"
    r"thanks(?:\s*you)?|thank\s*you|thx|ty|cheers|"
    r"ok(?:ay)?|cool|nice|great|got\s*it|"
    r"bye|goodbye|see\s*ya|"
    r"who\s*are\s*you|what\s*are\s*you|"
    r"what\s*can\s*you\s*do|what\s*do\s*you\s*do|help|"
    r"how\s*are\s*you|how(?:'s|\s+is)\s+it\s+going"
    r")"
    r"[\s!.,?]*$",
    re.IGNORECASE,
)

# Any of these tokens signals a real investigation — short-circuits to False even
# if the message also contains a greeting word.
_INVESTIGATION_TOKENS = (
    "error", "errors", "fail", "failed", "failing", "failure", "exception",
    "log", "logs", "trace", "stack", "crash", "timeout", "latency", "slow",
    "alarm", "alert", "5xx", "4xx", "500", "503", "throttl", "outage", "down",
    "spike", "spiking", "anomaly", "why", "debug", "investigate", "root cause",
    "rca", "repo", "deploy", "rollback", "metric", "cpu", "memory", "leak",
    "query", "database", "db ", "sql", "endpoint", "api ", "status code",
)

# Below this length a greeting-shaped message is safe to treat as small talk;
# longer messages are assumed to carry real intent even if they start politely.
_MAX_SMALL_TALK_CHARS = 64


def is_conversational(query: Optional[str]) -> bool:
    """True only when *query* is clearly small talk with no investigation signal.

    Biased toward returning False (investigate) — see module docstring.
    """
    text = (query or "").strip()
    if not text:
        # An empty/instruction-less turn is not "small talk"; let normal handling
        # decide (the agent node already skips when there is no query at all).
        return False
    if len(text) > _MAX_SMALL_TALK_CHARS:
        return False
    lowered = text.lower()
    if any(tok in lowered for tok in _INVESTIGATION_TOKENS):
        return False
    if extract_trace_ids(text)["found"]:
        return False
    return bool(_SMALL_TALK_RE.match(text))
