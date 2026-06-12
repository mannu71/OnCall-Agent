"""Hermetic CloudWatch tool layer for the trajectory eval — no live AWS.

``build_cloudwatch_agent_tools`` imports the underlying watch/metrics functions
*inside its body* and the summariser/KB helpers at module scope, so we can swap
the whole data layer by monkeypatching module attributes. The REAL tool code
(sanitizer, summarisers, token budgets, Insights linting) still runs over our
recorded fixtures, so the agent sees production-shaped tool results.

Recordings live in ``fixtures/cw_recorded/<name>.json`` and carry:

  * ``synthesis`` — the pre-computed-analysis text seeded into the query (what the
    deterministic CloudWatch node would have produced upstream), and
  * ``responses`` — a dict keyed by underlying function name, each value the RAW
    shape that function returns (the shape the summarisers consume), NOT the
    synthesis-evidence-bundle shape used by ``run_cloudwatch.py``.

Patch seams (all looked up at call time, so the context manager must stay open
across BOTH the tool build and the agent run):

  app.mcp.tools.watch_tools:   watch_log_groups, analyze_log_patterns,
                               detect_anomalies, correlate_logs, discover_log_groups
  app.mcp.tools.metrics_tools: get_metric_data, get_metric_statistics, list_metric_alarms
  app.mcp.tools.search_tools:  CloudWatchLogsSearchTools (search_logs_multi)
  app.workflow.tools.cloudwatch_agent_tools:
                               attach_metrics_context (→ identity),
                               recall_kb_for_pattern / recall_kb_for_log_groups (→ None),
                               should_auto_drill_down (→ False, so analyze/detect stay
                               hermetic and never fire an Insights drill-down)
"""
from __future__ import annotations

import contextlib
import json
import os
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(__file__)
RECORDED_DIR = os.path.join(HERE, "fixtures", "cw_recorded")


class FakeCloudWatchRecording:
    """A recorded set of raw CloudWatch function responses + a synthesis block."""

    def __init__(self, data: Dict[str, Any]):
        self._data = data or {}
        self.synthesis: str = str(self._data.get("synthesis") or "")
        self._responses: Dict[str, Any] = self._data.get("responses") or {}

    @classmethod
    def load(cls, name: str) -> "FakeCloudWatchRecording":
        path = name if os.path.isabs(name) else os.path.join(RECORDED_DIR, name)
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    def response_for(self, fn_name: str, default: Any) -> Any:
        return self._responses.get(fn_name, default)


@contextlib.contextmanager
def patched_cloudwatch(recording: FakeCloudWatchRecording):
    """Patch the CloudWatch data layer to serve *recording*; restore on exit."""
    import app.mcp.tools.watch_tools as watch_tools
    import app.mcp.tools.metrics_tools as metrics_tools
    import app.mcp.tools.search_tools as search_tools
    import app.workflow.tools.cloudwatch_agent_tools as cw_tools

    def _fake(fn_name: str, default: Any):
        async def _impl(*_args, **_kwargs):
            return recording.response_for(fn_name, default)
        return _impl

    # search_logs uses an instantiated tools class — fake just enough of it.
    class _FakeSearchTools:
        def __init__(self, *_, **__):
            pass

        async def search_logs_multi(self, *_, **__):
            # Default is an empty CloudWatch Insights result (rows of {field,value});
            # _has_events() is True but the sanitizer extracts 0 events without error.
            return recording.response_for("search_logs", {"results": []})

    async def _identity(summary, *_args, **_kwargs):
        return summary

    async def _none(*_args, **_kwargs):
        return None

    patches = [
        (watch_tools, "watch_log_groups", _fake("watch_log_groups", {"log_groups": {}, "summary": {}, "errors": []})),
        (watch_tools, "analyze_log_patterns", _fake("analyze_log_patterns", {"unique_patterns": [], "patterns": {}, "data_quality": {"coverage": "full"}})),
        (watch_tools, "detect_anomalies", _fake("detect_anomalies", {"anomalies": [], "summary": {}, "log_groups_analyzed": []})),
        (watch_tools, "correlate_logs", _fake("correlate_logs", {"timeline": [], "data_quality": {"coverage": "full"}})),
        (watch_tools, "discover_log_groups", _fake("discover_log_groups", {"groups": [], "region": "us-east-1"})),
        (metrics_tools, "get_metric_data", _fake("get_metric_data", {"metrics": []})),
        (metrics_tools, "get_metric_statistics", _fake("get_metric_statistics", {"datapoints": []})),
        (metrics_tools, "list_metric_alarms", _fake("list_metric_alarms", {"alarms": [], "summary": {}, "region": "us-east-1"})),
        (search_tools, "CloudWatchLogsSearchTools", _FakeSearchTools),
        (cw_tools, "attach_metrics_context", _identity),
        (cw_tools, "recall_kb_for_pattern", _none),
        (cw_tools, "recall_kb_for_log_groups", _none),
        (cw_tools, "should_auto_drill_down", lambda *_a, **_k: False),
    ]

    originals = [(mod, attr, getattr(mod, attr, None)) for mod, attr, _ in patches]
    try:
        for mod, attr, val in patches:
            setattr(mod, attr, val)
        yield recording
    finally:
        for mod, attr, val in originals:
            if val is not None:
                setattr(mod, attr, val)
