"""CloudWatch Tool Sanitizer - Token-aware output filtering and deduplication.

This module provides a sanitization pipeline for CloudWatch tool outputs to prevent
context overflow by filtering noise, deduplicating events, and enforcing token budgets.
"""
import re
import logging
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token counting (Phase 5)
# ---------------------------------------------------------------------------
# These budgets drive HARD truncation, so they use a real BPE count rather than
# the chars/4 heuristic, which drifts 15-40% on JSON-heavy log payloads and
# causes silent truncation or context overflow. Both helpers are thin aliases
# for the shared implementation in :mod:`app.core.llm.token_estimate`, which
# owns the single lazily-built cl100k_base encoder and the graceful fallback
# for environments without tiktoken.
from app.core.llm.token_estimate import count_tokens_exact as _count_tokens
from app.core.llm.token_estimate import get_encoder as _get_encoder


# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------
def severity_rank(message: str) -> int:
    """Lower rank = higher priority when capping events under token budget.

    Module-level so the pattern ranker in ``cloudwatch_summarizers`` derives
    severity the same way the event sanitizer prioritises it — one keyword
    table, not two that drift apart.
    """
    low = (message or "").lower()
    # Structured JSON level field (common in Lambda / app logs)
    if '"level"' in low or '"severity"' in low:
        if any(x in low for x in ('"error"', '"critical"', '"fatal"', '"severe"')):
            return 0
        if '"warn' in low:
            return 2
    if any(x in low for x in ("fatal", "critical", "panic", "exception", "traceback")):
        return 0
    if "error" in low or "failed" in low:
        return 1
    if "warn" in low:
        return 2
    return 3


class CloudWatchToolSanitizer:
    """Sanitizes CloudWatch tool outputs to prevent token explosion.
    
    Pipeline stages:
    1. Filter noise (health checks, 200s, routine polls)
    2. Deduplicate similar events
    3. Cap event count (max 20 events)
    4. Truncate long messages (max 300 chars)
    5. Format as compact text (not JSON)
    6. Enforce hard character limit (3000 chars)
    7. Enforce token budget (1000 tokens max)
    """
    
    MAX_EVENTS = 20
    MAX_MESSAGE_LEN = 300
    MAX_OUTPUT_CHARS = 3000
    TOKEN_BUDGET = 1000  # Max tokens any single tool call can contribute

    # Drill-down mode (cloudwatch_search_logs / watch_logs with drill_down=True)
    DRILL_DOWN_MAX_EVENTS = 25
    DRILL_DOWN_MESSAGE_LEN = 480
    DRILL_DOWN_TOKEN_BUDGET = 2500
    
    # Noise patterns to filter out
    NOISE_PATTERNS = (
        "HEALTH CHECK",
        "OPTIONS /",
        "GET /ping",
        "GET /health",
        "200 OK",
        "204 NO CONTENT",
        "ELB-HealthChecker",
        "kube-probe",
    )
    
    def __init__(self):
        """Initialize the sanitizer with token tracking."""
        self.token_log: List[Dict[str, Any]] = []
        self.session_stats = defaultdict(lambda: {
            "calls": 0,
            "total_tokens": 0,
            "truncations": 0,
            "events_filtered": 0,
            "events_deduplicated": 0,
        })
    
    def sanitize(
        self,
        raw_result: Dict[str, Any],
        tool_name: str,
        context: Optional[Dict[str, Any]] = None,
        *,
        drill_down: bool = False,
    ) -> str:
        """Sanitize CloudWatch tool output through the full pipeline.
        
        Args:
            raw_result: Raw result from CloudWatch API
            tool_name: Name of the tool that produced this result
            context: Additional context (log group, pattern, etc.)
            
        Returns:
            Sanitized, compact text output ready for agent consumption
        """
        context = context or {}
        max_events = self.DRILL_DOWN_MAX_EVENTS if drill_down else self.MAX_EVENTS
        max_msg = self.DRILL_DOWN_MESSAGE_LEN if drill_down else self.MAX_MESSAGE_LEN
        token_budget = self.DRILL_DOWN_TOKEN_BUDGET if drill_down else self.TOKEN_BUDGET
        
        # Extract events first so the raw-size estimate below can be derived
        # from message lengths instead of stringifying the (possibly multi-MB)
        # raw payload — that str() was pure CPU/memory cost on every call.
        events = self._extract_events(raw_result)
        total_events = len(events)

        # Telemetry-only raw-token estimate (char/4 heuristic). Log messages
        # dominate payload size; add a small per-event overhead for the
        # timestamp/keys. Fall back to a *bounded* string slice only when no
        # events were extracted, so we never materialize a huge dict to a string.
        if events:
            raw_chars = (
                sum(len(str(e.get("message", ""))) for e in events) + total_events * 40
            )
        else:
            raw_chars = len(str(raw_result)[:4000])
        raw_tokens = raw_chars // 4
        
        # Pipeline stages
        original_count = len(events)
        events = self._filter_noise(events)
        filtered_count = original_count - len(events)

        events.sort(key=lambda e: self._severity_rank(e.get("message", "")))

        original_count = len(events)
        events = self._deduplicate(events)
        deduplicated_count = original_count - len(events)
        
        events = events[:max_events]
        
        for event in events:
            msg = event.get("message", "")
            if len(msg) > max_msg:
                event["message"] = self._truncate_message(msg, max_msg)
        
        # Format as compact text
        output = self._format_compact(events, total_events)

        # Enforce character limit — emit a structured marker so downstream
        # agents know exactly what was dropped (raw events count + chars
        # discarded) and can decide whether to drill in via another tool call.
        chars_dropped = 0
        if len(output) > self.MAX_OUTPUT_CHARS:
            chars_dropped = len(output) - self.MAX_OUTPUT_CHARS
            output = (
                output[:self.MAX_OUTPUT_CHARS]
                + f"\n...[truncated by sanitizer: dropped {chars_dropped} chars, "
                f"{total_events - len(events)} of {total_events} raw events not shown]"
            )

        # Enforce token budget — same idea: leave a breadcrumb the agent can
        # reason about instead of a silent cut.
        truncated = False
        final_tokens = self._estimate_tokens(output)
        if final_tokens > token_budget:
            output = self._truncate_to_token_budget(output, token_budget)
            output += (
                f"\n[token-budget cut at {token_budget} tokens; "
                f"original was ~{final_tokens} tokens, raw events={total_events}]"
            )
            final_tokens = token_budget
            truncated = True
        
        # Record metrics
        self._record(
            tool_name=tool_name,
            context=context,
            raw_tokens=raw_tokens,
            final_tokens=final_tokens,
            total_events=total_events,
            returned_events=len(events),
            truncated=truncated,
            filtered_count=filtered_count,
            deduplicated_count=deduplicated_count,
        )
        
        return output
    
    def _extract_events(self, raw_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract events from various CloudWatch result formats."""
        # Handle different result structures
        if isinstance(raw_result, dict):
            # Standard format with 'events' key
            if "events" in raw_result:
                return raw_result["events"]
            
            # Insights query results
            if "results" in raw_result:
                results = raw_result["results"]
                events = []
                for row in results:
                    # Convert Insights row format to event format
                    fields = {f.get("field"): f.get("value") for f in row}
                    events.append({
                        "timestamp": fields.get("@timestamp", ""),
                        "message": fields.get("@message", ""),
                    })
                return events
            
            # Multi-log-group format
            events = []
            for key, value in raw_result.items():
                if isinstance(value, dict) and "events" in value:
                    events.extend(value["events"])
            return events
        
        return []
    
    @staticmethod
    def _truncate_message(message: str, max_len: int) -> str:
        """Keep message tail for errors (stack traces) else head."""
        if len(message) <= max_len:
            return message
        low = message.lower()
        if any(x in low for x in ("error", "exception", "fatal", "traceback", "failed")):
            return "…" + message[-(max_len - 12):] + " [tail]"
        return message[: max_len - 12] + "...[truncated]"

    @staticmethod
    def _severity_rank(message: str) -> int:
        """Lower rank = higher priority when capping events under token budget."""
        return severity_rank(message)

    def _filter_noise(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter out routine/noise events (health checks, 200s, etc.)."""
        filtered = []
        for event in events:
            msg = event.get("message", "").upper()
            if not any(pattern in msg for pattern in self.NOISE_PATTERNS):
                filtered.append(event)
        return filtered
    
    def _deduplicate(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Deduplicate similar events and add occurrence counts.
        
        Groups events by normalized message (numbers replaced with 'N')
        and adds [xN] prefix for duplicates.
        """
        seen = {}
        
        for event in events:
            msg = event.get("message", "")
            # Normalize: strip UUIDs, hex hashes, transaction IDs, and plain
            # numbers before grouping so semantically identical log lines with
            # different runtime IDs collapse to the same bucket.
            # Key width is 250 chars (was 100) to avoid false-positive grouping
            # of messages that share a long common prefix but differ later.
            key = re.sub(
                r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b'
                r'|\b[0-9a-fA-F]{8,}\b'
                r'|\d+',
                'N',
                msg,
            )[:250]
            
            if key not in seen:
                seen[key] = {"event": event.copy(), "count": 1}
            else:
                seen[key]["count"] += 1
        
        # Build result with occurrence counts
        result = []
        for val in seen.values():
            event = val["event"]
            if val["count"] > 1:
                event["message"] = f"[x{val['count']}] {event['message']}"
            result.append(event)
        
        return result
    
    def _format_compact(self, events: List[Dict[str, Any]], total_raw: int) -> str:
        """Format events as compact text (not JSON) to save tokens."""
        lines = [f"[{total_raw} total events, showing {len(events)} after filtering]\n"]
        
        for event in events:
            timestamp = event.get("timestamp", "")
            message = event.get("message", "").strip()
            
            # Compact timestamp format
            if timestamp:
                try:
                    # Try to parse and format compactly
                    if "T" in timestamp:
                        # ISO format
                        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        timestamp = dt.strftime("%H:%M:%S")
                except Exception:
                    # Keep original if parsing fails
                    pass
            
            lines.append(f"{timestamp} | {message}")
        
        return "\n".join(lines)
    
    def _estimate_tokens(self, text: str) -> int:
        """Accurate token count via tiktoken cl100k_base, with safe fallback."""
        return _count_tokens(text)

    def _truncate_to_token_budget(self, text: str, budget: Optional[int] = None) -> str:
        """Truncate text to fit within token budget using accurate counting."""
        budget = budget if budget is not None else self.TOKEN_BUDGET
        if _count_tokens(text) <= budget:
            return text
        lo, hi = 0, len(text)
        best = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if _count_tokens(text[:mid]) <= budget:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return text[:best] + "\n...[truncated to token budget]"
    
    def _record(
        self,
        tool_name: str,
        context: Dict[str, Any],
        raw_tokens: int,
        final_tokens: int,
        total_events: int,
        returned_events: int,
        truncated: bool,
        filtered_count: int,
        deduplicated_count: int,
    ):
        """Record metrics for this sanitization operation."""
        saved = raw_tokens - final_tokens
        reduction_pct = round((saved / raw_tokens * 100) if raw_tokens else 0, 1)
        
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "tool": tool_name,
            "context": context,
            "raw_tokens": raw_tokens,
            "final_tokens": final_tokens,
            "tokens_saved": saved,
            "reduction_pct": reduction_pct,
            "total_events_from_cw": total_events,
            "events_returned_to_agent": returned_events,
            "events_filtered": filtered_count,
            "events_deduplicated": deduplicated_count,
            "truncated_to_budget": truncated,
        }
        
        self.token_log.append(entry)
        
        # Update session stats
        stats = self.session_stats[tool_name]
        stats["calls"] += 1
        stats["total_tokens"] += final_tokens
        stats["truncations"] += int(truncated)
        stats["events_filtered"] += filtered_count
        stats["events_deduplicated"] += deduplicated_count
        
        # Log results
        if final_tokens > self.TOKEN_BUDGET * 0.8:
            logger.warning(
                f"⚠️  CloudWatch tool [{tool_name}]: {final_tokens} tokens after sanitization "
                f"({reduction_pct}% reduction from {raw_tokens} raw, "
                f"filtered {filtered_count}, deduped {deduplicated_count})"
            )
        else:
            logger.info(
                f"✅ CloudWatch tool [{tool_name}]: {raw_tokens} raw → {final_tokens} tokens "
                f"({reduction_pct}% reduction, {saved} tokens saved, "
                f"filtered {filtered_count}, deduped {deduplicated_count})"
            )
    
    def get_session_report(self) -> Dict[str, Any]:
        """Get comprehensive session report with all metrics."""
        total_tokens = sum(e["final_tokens"] for e in self.token_log)
        total_raw = sum(e["raw_tokens"] for e in self.token_log)
        total_saved = total_raw - total_tokens
        
        return {
            "session_summary": {
                "total_tool_calls": len(self.token_log),
                "total_raw_tokens": total_raw,
                "total_tokens_to_agent": total_tokens,
                "total_tokens_saved": total_saved,
                "overall_reduction_pct": round((total_saved / total_raw * 100) if total_raw else 0, 1),
                "budget_truncations": sum(e["truncated_to_budget"] for e in self.token_log),
            },
            "per_tool": dict(self.session_stats),
            "call_log": self.token_log,
        }
    
    def print_session_report(self):
        """Print a formatted session report to console."""
        report = self.get_session_report()
        s = report["session_summary"]
        
        logger.info("\n" + "=" * 55)
        logger.info("       CLOUDWATCH TOOL TOKEN USAGE REPORT")
        logger.info("=" * 55)
        logger.info(f"  Total tool calls      : {s['total_tool_calls']}")
        logger.info(f"  Raw tokens (unsanitized): {s['total_raw_tokens']:,}")
        logger.info(f"  Tokens sent to agent  : {s['total_tokens_to_agent']:,}")
        logger.info(f"  Tokens saved          : {s['total_tokens_saved']:,}  ({s['overall_reduction_pct']}% reduction)")
        logger.info(f"  Budget truncations    : {s['budget_truncations']}")
        logger.info("-" * 55)
        logger.info("  Per-tool breakdown:")
        
        for tool, stats in report["per_tool"].items():
            logger.info(f"    {tool}")
            logger.info(f"      Calls      : {stats['calls']}")
            logger.info(f"      Tokens     : {stats['total_tokens']:,}")
            logger.info(f"      Truncations: {stats['truncations']}")
            logger.info(f"      Filtered   : {stats['events_filtered']}")
            logger.info(f"      Deduplicated: {stats['events_deduplicated']}")
        
        logger.info("=" * 55 + "\n")
