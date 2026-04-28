"""MCP Tools package for CloudWatch Log Analyzer."""
from app.mcp.tools.watch_tools import (
    watch_log_groups,
    analyze_log_patterns,
    detect_anomalies,
    correlate_logs
)
from app.mcp.tools.alert_tools import (
    create_alert,
    get_alerts,
    acknowledge_alert,
    get_alert_summary
)

__all__ = [
    # Watch tools
    'watch_log_groups',
    'analyze_log_patterns',
    'detect_anomalies',
    'correlate_logs',
    # Alert tools
    'create_alert',
    'get_alerts',
    'acknowledge_alert',
    'get_alert_summary'
]
