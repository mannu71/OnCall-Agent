# Deprecated / unused UI modules

These files were removed from active routes during the performance optimization pass (2026-06).
They remain in git history if needed for reference.

| File | Reason |
|------|--------|
| `Analytics.jsx` | No route in App.jsx |
| `WorkflowEditor.jsx` | Replaced by LangflowEditor |
| `NodeConfigPanel.jsx` | Only used by WorkflowEditor |
| `ExecutionMonitor.jsx` | Never mounted in routes |
| `TrajectoryReplay.jsx` | Unreferenced |

The canonical workflow canvas is `src/components/workflow/LangflowEditor.jsx`.
