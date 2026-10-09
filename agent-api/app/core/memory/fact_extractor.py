"""Per-turn durable-fact extraction.

After a completed turn we extract a SMALL number of durable, operational facts
worth recalling in later conversations (confirmed identifiers, environment facts,
stable preferences, ownership) and store them in the existing ``semantic_memory``
store. Deliberately conservative — a "MAX 2 facts, single short sentence"
discipline keeps this net-positive rather than a per-turn token leak.

Two extraction paths:
  1. LLM extraction (primary) — returns a small JSON list of {text, category}.
  2. Regex fallback (secondary) — captures a few obvious statements when the LLM
     is unavailable, so critical facts aren't lost.

Gated by ``settings.memory_fact_extraction_enabled`` — a no-op when disabled.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

# Allowed categories — anything else is coerced to "fact".
_CATEGORIES = {"fact", "preference", "context", "contact", "identity", "project"}

_EXTRACT_SYSTEM = """\
You extract DURABLE facts from a support/operations conversation that will be
useful in FUTURE conversations. Return ONLY a JSON array (possibly empty).

Each item: {"text": "<single short sentence, under 15 words>", "category": "<fact|preference|context|contact|identity|project>"}

Rules:
- MAX {max_facts} items — only the most important, durable facts.
- Capture: stable identifiers, environment/ownership facts, confirmed root
  causes, long-lived preferences, who-owns-what.
- DO NOT capture: transient state, one-off task details, the assistant's own
  phrasing, pleasantries, or anything that won't matter next week.
- If nothing durable was learned, return [].
"""

_EXTRACT_USER = "Conversation excerpt:\n{excerpt}"

# Regex fallback — a few obvious durable-statement shapes.
_FALLBACK_PATTERNS = [
    (re.compile(r"\bthe owner (?:of|for) ([\w/.\- ]{3,60}) is ([\w@.\- ]{2,60})", re.I), "context"),
    (re.compile(r"\b(?:root cause|the cause) (?:was|is) ([^.\n]{5,90})", re.I), "fact"),
    (re.compile(r"\bwe (?:always|never|prefer to) ([^.\n]{5,80})", re.I), "preference"),
]


async def extract_facts(
    user_content: str,
    assistant_content: str,
    *,
    llm_fn: Optional[Any] = None,
) -> List[Dict[str, str]]:
    """Return up to ``memory_fact_max_per_turn`` durable facts for this turn.

    ``llm_fn`` is an injectable async ``(prompt) -> str`` for tests; production
    uses :func:`app.core.llm.call_llm.call_llm`.
    """
    if not settings.memory_fact_extraction_enabled:
        return []
    excerpt = f"user: {user_content}\nassistant: {assistant_content}".strip()
    if not excerpt:
        return []
    # Facts are stored pseudonymized: the raw user turn must not reach the
    # model or the memory store. The run's active vault keeps placeholders
    # consistent with the (already pseudonymized) assistant answer.
    from app.core import privacy
    excerpt = privacy.pseudonymize_active(excerpt)
    max_facts = max(1, settings.memory_fact_max_per_turn)

    facts = await _llm_extract(excerpt, max_facts, llm_fn)
    if not facts:
        facts = _regex_extract(excerpt)
    return _normalize(facts, max_facts)


async def _llm_extract(
    excerpt: str, max_facts: int, llm_fn: Optional[Any]
) -> List[Dict[str, str]]:
    prompt = (
        # .replace, not .format: the template contains literal JSON braces.
        _EXTRACT_SYSTEM.replace("{max_facts}", str(max_facts))
        + "\n\n"
        + _EXTRACT_USER.replace("{excerpt}", excerpt[:6000])
    )
    try:
        if llm_fn is not None:
            raw = await llm_fn(prompt)
        else:
            from app.core.llm.call_llm import call_llm
            raw, _, _, _ = await call_llm(prompt, tier="search", use_cache=False)
        return _parse_facts(raw)
    except Exception as exc:  # noqa: BLE001 — fall back to regex, never break a run
        logger.debug("fact_extractor: LLM extract failed (%s)", exc)
        return []


def _parse_facts(raw: str) -> List[Dict[str, str]]:
    """Parse the LLM's JSON array, tolerating markdown fences and stray prose."""
    if not raw:
        return []
    text = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    # Grab the first [...] block if the model added commentary.
    match = re.search(r"\[.*\]", text, flags=re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _regex_extract(excerpt: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for pattern, category in _FALLBACK_PATTERNS:
        m = pattern.search(excerpt)
        if m:
            out.append({"text": m.group(0).strip(), "category": category})
    return out


def _normalize(facts: List[Any], max_facts: int) -> List[Dict[str, str]]:
    """Clamp to the cap, drop empties/over-long text, coerce categories."""
    cleaned: List[Dict[str, str]] = []
    seen = set()
    for item in facts:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text or len(text.split()) > 20:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        category = str(item.get("category") or "fact").lower()
        if category not in _CATEGORIES:
            category = "fact"
        cleaned.append({"text": text, "category": category})
        if len(cleaned) >= max_facts:
            break
    return cleaned


async def extract_and_store(
    user_content: str,
    assistant_content: str,
    *,
    repo: Optional[str] = None,
    llm_fn: Optional[Any] = None,
) -> int:
    """Extract durable facts and persist them to ``semantic_memory``.

    Returns the number of facts stored. Best-effort: storage failures are
    swallowed by the underlying ``remember`` call.
    """
    facts = await extract_facts(user_content, assistant_content, llm_fn=llm_fn)
    if not facts:
        return 0
    from app.services.semantic_memory import semantic_memory

    stored = 0
    for f in facts:
        row_id = await semantic_memory.remember(
            f["text"],
            repo=repo,
            source="agent",
            importance=0.5,
            veracity=0.5,
        )
        if row_id is not None:
            stored += 1
    logger.debug("fact_extractor: stored %d/%d fact(s)", stored, len(facts))
    return stored
