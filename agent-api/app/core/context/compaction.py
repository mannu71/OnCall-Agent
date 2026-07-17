"""
Compaction algorithm for the agent memory loop.

Walk backwards from the newest message, accumulate estimated tokens, then
summarize everything older into a StructuredSummary that survives across
multiple compactions.

Usage
-----
    from app.core.context.compaction import compact, StructuredSummary

    compacted_msgs, summary = await compact(
        messages,
        transport=my_transport,
        window_size=200_000,
    )
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    """Estimate token count using the project-wide chars/4 heuristic, optionally
    scaled by a learned per-model calibration factor. The factor is 1.0 (exact
    no-op) unless token-estimate calibration is enabled AND a real observation
    has been recorded, so default behavior is byte-identical to raw chars/4."""
    base = max(1, len(text) // 4)
    try:
        from app.core.llm.token_calibration import current_factor
        factor = current_factor()
        if factor != 1.0:
            return max(1, int(base * factor))
    except Exception:  # noqa: BLE001 — calibration must never break estimation
        pass
    return base


def _msg_token_estimate(msg: BaseMessage) -> int:
    """Estimate the token cost of a single message."""
    content = msg.content
    if isinstance(content, str):
        base = _estimate_tokens(content)
    elif isinstance(content, list):
        # Multipart content (e.g. tool use blocks)
        base = sum(_estimate_tokens(str(part)) for part in content)
    else:
        base = _estimate_tokens(str(content))

    # Add overhead for role name and structure
    overhead = 4
    # AIMessages with tool_calls add extra tokens for each call
    if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
        overhead += sum(_estimate_tokens(json.dumps(tc)) for tc in msg.tool_calls)

    return base + overhead


# ---------------------------------------------------------------------------
# Symbol & file extraction helpers
# ---------------------------------------------------------------------------

_CODE_ANALYZER_TOOLS = frozenset({
    "get_function",
    "find_symbol",
    "get_definition",
    "get_callers",
    "get_references",
})

_PATH_KEYS = frozenset({"file_path", "path", "file"})
_SYMBOL_KEYS = frozenset({"name", "symbol_id", "symbol"})


def _extract_symbols_and_files(
    messages: List[BaseMessage],
) -> Tuple[set[str], set[str]]:
    """Scan messages for file paths and code symbol names used in tool calls.

    Returns
    -------
    (symbols, files)
        Two sets: resolved symbol names and touched file paths.
    """
    symbols: set[str] = set()
    files: set[str] = set()

    def _scan_dict(d: Dict[str, Any]) -> None:
        for k, v in d.items():
            if k in _PATH_KEYS and isinstance(v, str) and v:
                files.add(v)
            if k in _SYMBOL_KEYS and isinstance(v, str) and v:
                symbols.add(v)

    def _scan_value(v: Any) -> None:
        if isinstance(v, dict):
            _scan_dict(v)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    _scan_dict(item)

    for msg in messages:
        if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                args: Dict[str, Any] = tc.get("args") or {}
                tool_name: str = tc.get("name") or ""
                _scan_dict(args)
                if tool_name in _CODE_ANALYZER_TOOLS:
                    for k in _SYMBOL_KEYS:
                        val = args.get(k)
                        if val and isinstance(val, str):
                            symbols.add(val)

        if isinstance(msg, ToolMessage):
            content = msg.content
            if isinstance(content, str):
                # Try to parse JSON from the tool result
                try:
                    parsed = json.loads(content)
                    _scan_value(parsed)
                except (json.JSONDecodeError, ValueError):
                    pass
            elif isinstance(content, (dict, list)):
                _scan_value(content)

    return symbols, files


# ---------------------------------------------------------------------------
# StructuredSummary dataclass
# ---------------------------------------------------------------------------

@dataclass
class StructuredSummary:
    """A structured summary of a conversation segment.

    ``symbols_resolved`` and ``files_touched`` are cumulative across
    compactions — they are NEVER shrunk when merging.
    """

    goals: str
    progress: str
    decisions: List[str]
    symbols_resolved: List[str]
    files_touched: List[str]
    open_questions: List[str]
    generated_at: datetime
    summary_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    previous_summary_id: Optional[str] = None
    token_count_at_summarization: int = 0

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def to_system_message_text(self) -> str:
        """Render a stable, scannable text block for injection as SystemMessage."""

        def _bullets(items: List[str]) -> str:
            if not items:
                return "  (none)"
            return "\n".join(f"  - {item}" for item in items)

        ts = self.generated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        header = (
            f"=== COMPACTED CONTEXT SUMMARY (id={self.summary_id}, at={ts}) ==="
        )
        if self.previous_summary_id:
            header += f"\n    (merges prior summary id={self.previous_summary_id})"

        sections = [
            header,
            "",
            "## Goals",
            self.goals or "(not captured)",
            "",
            "## Progress",
            self.progress or "(not captured)",
            "",
            "## Decisions",
            _bullets(self.decisions),
            "",
            "## Symbols Resolved (cumulative)",
            _bullets(self.symbols_resolved),
            "",
            "## Files Touched (cumulative)",
            _bullets(self.files_touched),
            "",
            "## Open Questions",
            _bullets(self.open_questions),
            "",
            "=== END COMPACTED CONTEXT ===",
        ]
        return "\n".join(sections)

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary_id": self.summary_id,
            "previous_summary_id": self.previous_summary_id,
            "generated_at": self.generated_at.isoformat(),
            "token_count_at_summarization": self.token_count_at_summarization,
            "goals": self.goals,
            "progress": self.progress,
            "decisions": self.decisions,
            "symbols_resolved": self.symbols_resolved,
            "files_touched": self.files_touched,
            "open_questions": self.open_questions,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StructuredSummary":
        """Inverse of :meth:`to_dict` — restore a summary from a JSON dict.

        Used by :class:`ContextCompactionManager` to load a persisted
        summary from the ``memory_summaries`` Postgres table on first
        access for a given ``session_id``.
        """
        generated_raw = data.get("generated_at")
        if isinstance(generated_raw, str):
            generated_at = datetime.fromisoformat(generated_raw)
        elif isinstance(generated_raw, datetime):
            generated_at = generated_raw
        else:
            generated_at = datetime.utcnow()

        return cls(
            goals=data.get("goals", ""),
            progress=data.get("progress", ""),
            decisions=list(data.get("decisions") or []),
            symbols_resolved=list(data.get("symbols_resolved") or []),
            files_touched=list(data.get("files_touched") or []),
            open_questions=list(data.get("open_questions") or []),
            generated_at=generated_at,
            summary_id=data.get("summary_id") or str(uuid.uuid4())[:8],
            previous_summary_id=data.get("previous_summary_id"),
            token_count_at_summarization=int(
                data.get("token_count_at_summarization") or 0
            ),
        )


# ---------------------------------------------------------------------------
# Summarisation prompt + LLM call
# ---------------------------------------------------------------------------

_SUMMARY_PROMPT = """\
You are summarizing an in-progress engineering conversation so that it
can be compacted without losing critical context. Read the messages
below and produce a structured summary covering: goals, progress,
decisions, symbols_resolved, files_touched, open_questions. Be specific
— include file paths and symbol names. Do not omit anything that would
be needed to resume the work.

Prior cumulative summary (merge into your output):
{prior_summary}

Messages to summarize (oldest to newest):
{messages_rendered}

Respond with ONLY a JSON object with these exact keys:
{{
  "goals": "<string: what the user originally asked for>",
  "progress": "<string: what has been done so far, with specific file paths>",
  "decisions": ["<string>", ...],
  "symbols_resolved": ["<string>", ...],
  "files_touched": ["<string>", ...],
  "open_questions": ["<string>", ...]
}}
"""


def _render_messages_for_summary(messages: List[BaseMessage]) -> str:
    """Render messages to plain text for the summarization prompt."""
    lines: List[str] = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            role = "SYSTEM"
        elif isinstance(msg, HumanMessage):
            role = "USER"
        elif isinstance(msg, AIMessage):
            role = "ASSISTANT"
        elif isinstance(msg, ToolMessage):
            role = f"TOOL({getattr(msg, 'tool_call_id', '?')})"
        else:
            role = msg.__class__.__name__.upper()

        content = msg.content
        if isinstance(content, list):
            text = " ".join(str(part) for part in content)
        else:
            text = str(content)

        # Cap individual message at 2000 chars to avoid blowing the summary prompt
        if len(text) > 2000:
            text = text[:2000] + "...[truncated]"

        lines.append(f"[{role}]: {text}")

        if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                lines.append(
                    f"  → TOOL_CALL {tc.get('name')} args={json.dumps(tc.get('args', {}))}"
                )

    return "\n".join(lines)


async def _call_llm_for_summary(
    messages_to_summarize: List[BaseMessage],
    prior_summary: Optional[StructuredSummary],
    transport: Any,
    summarization_model: str,
) -> Dict[str, Any]:
    """Call the LLM transport to generate a structured summary dict."""
    prior_text = "(none)"
    if prior_summary is not None:
        prior_text = prior_summary.to_system_message_text()

    rendered = _render_messages_for_summary(messages_to_summarize)
    prompt = _SUMMARY_PROMPT.format(
        prior_summary=prior_text,
        messages_rendered=rendered,
    )

    # The transport's complete() method signature may vary; we handle both
    # positional and keyword invocation styles.
    try:
        response_text = await transport.complete(
            messages=[{"role": "user", "content": prompt}],
            model=summarization_model,
            max_tokens=2048,
        )
    except TypeError:
        # Fallback: some transports may not accept model/max_tokens kwargs
        response_text = await transport.complete(
            messages=[{"role": "user", "content": prompt}],
        )

    # Extract the string payload regardless of what the transport returns
    if isinstance(response_text, str):
        raw = response_text
    elif hasattr(response_text, "content"):
        raw = str(response_text.content)
    else:
        raw = str(response_text)

    # Parse JSON from the response (strip markdown fences if present)
    raw = raw.strip()
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        raw = json_match.group(0)

    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("LLM summary response was not valid JSON; using fallback")
        result = {
            "goals": raw[:500],
            "progress": "(parse error — raw response stored in goals)",
            "decisions": [],
            "symbols_resolved": [],
            "files_touched": [],
            "open_questions": [],
        }

    return result


# ---------------------------------------------------------------------------
# Main compact() function
# ---------------------------------------------------------------------------

async def compact(
    messages: List[BaseMessage],
    *,
    transport: Any,
    window_size: int = 200_000,
    reserve_tokens: int = 16_384,
    keep_recent_tokens: int = 20_000,
    prior_summary: Optional[StructuredSummary] = None,
    summarization_model: Optional[str] = None,
) -> Tuple[List[BaseMessage], StructuredSummary]:
    """Compact a message list down to a structured summary + recent tail.

    ``summarization_model`` must be supplied by the caller (resolved from the
    user's configured model); no model is hardcoded here.

    Parameters
    ----------
    messages:
        Full conversation history (LangChain BaseMessage instances).
    transport:
        A ProviderTransport-compatible object with an async ``complete()``
        method.  Injected to enable testing with FakeTransport.
    window_size:
        Provider context window size in tokens (default 200 000).
    reserve_tokens:
        Number of tokens to reserve for the model response (default 16 384).
    keep_recent_tokens:
        Walk backwards from the newest message; stop accumulating once
        this many tokens have been counted.  Everything newer = "recent
        tail".  Everything older = "to summarize" (default 20 000).
    prior_summary:
        Previous StructuredSummary from an earlier compaction, if any.
        Cumulative lists (symbols_resolved, files_touched, decisions) are
        merged from it.
    summarization_model:
        Model identifier passed to ``transport.complete()`` for the
        summarization LLM call.

    Returns
    -------
    (compacted_messages, new_summary)
        If no compaction was needed, messages is returned unchanged and
        new_summary reflects cumulative tracking with an empty summary body.
    """
    if not summarization_model:
        raise ValueError(
            "compact(): summarization_model is required — resolve it from the "
            "user's configured model before calling (no model is hardcoded)."
        )
    if not messages:
        # Edge case: nothing to compact
        empty_summary = StructuredSummary(
            goals="",
            progress="",
            decisions=[],
            symbols_resolved=[],
            files_touched=[],
            open_questions=[],
            generated_at=datetime.now(timezone.utc),
            token_count_at_summarization=0,
        )
        return messages, empty_summary

    # ------------------------------------------------------------------
    # Step 1: estimate total tokens
    # ------------------------------------------------------------------
    total_tokens = sum(_msg_token_estimate(m) for m in messages)
    budget = window_size - reserve_tokens

    logger.debug(
        "compact(): total_tokens=%d budget=%d keep_recent=%d",
        total_tokens,
        budget,
        keep_recent_tokens,
    )

    # ------------------------------------------------------------------
    # Step 2: idempotency — if already under budget, no-op
    # ------------------------------------------------------------------
    if total_tokens <= budget:
        # Still extract symbols/files so cumulative tracking stays current
        new_symbols, new_files = _extract_symbols_and_files(messages)
        prior_symbols = set(prior_summary.symbols_resolved) if prior_summary else set()
        prior_files = set(prior_summary.files_touched) if prior_summary else set()

        no_op_summary = StructuredSummary(
            goals=prior_summary.goals if prior_summary else "",
            progress=prior_summary.progress if prior_summary else "",
            decisions=list(prior_summary.decisions) if prior_summary else [],
            symbols_resolved=sorted(prior_symbols | new_symbols),
            files_touched=sorted(prior_files | new_files),
            open_questions=list(prior_summary.open_questions) if prior_summary else [],
            generated_at=datetime.now(timezone.utc),
            previous_summary_id=prior_summary.summary_id if prior_summary else None,
            token_count_at_summarization=total_tokens,
        )
        return messages, no_op_summary

    # ------------------------------------------------------------------
    # Step 3: walk backwards to find the "recent tail"
    # ------------------------------------------------------------------
    accumulated = 0
    tail_start_idx = len(messages)  # exclusive start of tail (from end)

    for i in range(len(messages) - 1, -1, -1):
        msg_tokens = _msg_token_estimate(messages[i])
        accumulated += msg_tokens
        if accumulated >= keep_recent_tokens:
            tail_start_idx = i
            break
    else:
        # Never reached keep_recent_tokens: entire list is "recent"
        tail_start_idx = 0

    recent_tail = messages[tail_start_idx:]
    to_summarize = messages[:tail_start_idx]

    logger.info(
        "compact(): summarizing %d messages, keeping %d recent messages "
        "(accumulated_recent_tokens=%d)",
        len(to_summarize),
        len(recent_tail),
        accumulated,
    )

    if not to_summarize:
        # Nothing old enough to summarize — return unchanged to avoid empty summary
        new_symbols, new_files = _extract_symbols_and_files(messages)
        prior_symbols = set(prior_summary.symbols_resolved) if prior_summary else set()
        prior_files = set(prior_summary.files_touched) if prior_summary else set()
        no_change_summary = StructuredSummary(
            goals=prior_summary.goals if prior_summary else "",
            progress=prior_summary.progress if prior_summary else "",
            decisions=list(prior_summary.decisions) if prior_summary else [],
            symbols_resolved=sorted(prior_symbols | new_symbols),
            files_touched=sorted(prior_files | new_files),
            open_questions=list(prior_summary.open_questions) if prior_summary else [],
            generated_at=datetime.now(timezone.utc),
            previous_summary_id=prior_summary.summary_id if prior_summary else None,
            token_count_at_summarization=total_tokens,
        )
        return messages, no_change_summary

    # ------------------------------------------------------------------
    # Step 4: extract cumulative symbols and files from the full history
    # ------------------------------------------------------------------
    all_symbols, all_files = _extract_symbols_and_files(messages)
    prior_symbols = set(prior_summary.symbols_resolved) if prior_summary else set()
    prior_files = set(prior_summary.files_touched) if prior_summary else set()
    prior_decisions = list(prior_summary.decisions) if prior_summary else []

    merged_symbols = sorted(prior_symbols | all_symbols)
    merged_files = sorted(prior_files | all_files)

    # ------------------------------------------------------------------
    # Step 5: call LLM to generate structured summary
    # ------------------------------------------------------------------
    summary_dict = await _call_llm_for_summary(
        messages_to_summarize=to_summarize,
        prior_summary=prior_summary,
        transport=transport,
        summarization_model=summarization_model,
    )

    # Merge decisions (deduplicate, keep prior ones too)
    llm_decisions: List[str] = summary_dict.get("decisions") or []
    decision_set = set(prior_decisions) | set(llm_decisions)
    # Preserve order: prior first, then new
    merged_decisions: List[str] = list(prior_decisions)
    for d in llm_decisions:
        if d not in set(merged_decisions):
            merged_decisions.append(d)

    # Merge cumulative symbols/files from LLM response too
    llm_symbols: List[str] = summary_dict.get("symbols_resolved") or []
    llm_files: List[str] = summary_dict.get("files_touched") or []
    merged_symbols = sorted(set(merged_symbols) | set(llm_symbols))
    merged_files = sorted(set(merged_files) | set(llm_files))

    # ------------------------------------------------------------------
    # Step 6: build StructuredSummary
    # ------------------------------------------------------------------
    new_summary = StructuredSummary(
        goals=summary_dict.get("goals") or (prior_summary.goals if prior_summary else ""),
        progress=summary_dict.get("progress") or "",
        decisions=merged_decisions,
        symbols_resolved=merged_symbols,
        files_touched=merged_files,
        open_questions=summary_dict.get("open_questions") or [],
        generated_at=datetime.now(timezone.utc),
        previous_summary_id=prior_summary.summary_id if prior_summary else None,
        token_count_at_summarization=total_tokens,
    )

    # ------------------------------------------------------------------
    # Step 7: build compacted message list
    # ------------------------------------------------------------------
    summary_text = new_summary.to_system_message_text()
    compacted: List[BaseMessage] = [SystemMessage(content=summary_text), *recent_tail]

    logger.info(
        "compact(): produced %d-message compacted list (was %d). "
        "summary_id=%s symbols=%d files=%d",
        len(compacted),
        len(messages),
        new_summary.summary_id,
        len(merged_symbols),
        len(merged_files),
    )

    return compacted, new_summary
