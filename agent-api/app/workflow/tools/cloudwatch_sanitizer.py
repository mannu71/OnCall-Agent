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
        context: Optional[Dict[str, Any]] = None
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
        
        # Estimate raw token count (rough approximation: 1 token ≈ 4 chars)
        raw_str = str(raw_result)
        raw_tokens = self._estimate_tokens(raw_str)
        
        # Extract events from result
        events = self._extract_events(raw_result)
        total_events = len(events)
        
        # Pipeline stages
        original_count = len(events)
        events = self._filter_noise(events)
        filtered_count = original_count - len(events)
        
        original_count = len(events)
        events = self._deduplicate(events)
        deduplicated_count = original_count - len(events)
        
        events = events[:self.MAX_EVENTS]
        
        for event in events:
            if len(event.get("message", "")) > self.MAX_MESSAGE_LEN:
                event["message"] = event["message"][:self.MAX_MESSAGE_LEN] + "...[truncated]"
        
        # Format as compact text
        output = self._format_compact(events, total_events)
        
        # Enforce character limit
        if len(output) > self.MAX_OUTPUT_CHARS:
            output = output[:self.MAX_OUTPUT_CHARS] + "\n...[output truncated to char limit]"
        
        # Enforce token budget
        truncated = False
        final_tokens = self._estimate_tokens(output)
        if final_tokens > self.TOKEN_BUDGET:
            output = self._truncate_to_token_budget(output)
            final_tokens = self.TOKEN_BUDGET
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
            # Normalize: replace numbers with 'N', take first 100 chars as key
            key = re.sub(r'\d+', 'N', msg)[:100]
            
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
        """Estimate token count (rough approximation: 1 token ≈ 4 chars)."""
        return len(text) // 4
    
    def _truncate_to_token_budget(self, text: str) -> str:
        """Truncate text to fit within token budget."""
        # Rough approximation: 1 token ≈ 4 chars
        max_chars = self.TOKEN_BUDGET * 4
        if len(text) > max_chars:
            return text[:max_chars] + "\n...[truncated to token budget]"
        return text
    
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
