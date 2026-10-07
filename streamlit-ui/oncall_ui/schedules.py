"""Schedule view-model over workflows (port of ``SchedulerContext`` + ``workflowUtils``)."""
from __future__ import annotations

import copy
import time
from datetime import datetime, timezone
from typing import List, Optional

from .timeutils import cron_to_local_time
from .workflow_model import clean_orphaned_edges


def scheduler_node(workflow: dict) -> Optional[dict]:
    return next((n for n in workflow.get("nodes") or [] if n.get("type") == "scheduler"), None)


def from_api(wf: dict, tz: Optional[str]) -> dict:
    """Flatten a workflow into the fields the schedule/dashboard views use."""
    node = scheduler_node(wf)
    start_time, recurrence = None, None
    if node and node.get("data"):
        start_time = node["data"].get("startTime") or cron_to_local_time(node["data"].get("cronExpression"), tz)
        recurrence = node["data"].get("recurrence")
    elif wf.get("schedule"):
        start_time = cron_to_local_time(wf["schedule"], tz)
    return {
        "name": wf.get("name"),
        "title": wf.get("name"),
        "description": wf.get("description") or "",
        "schedule": wf.get("schedule"),
        "enabled": wf.get("enabled", True) if wf.get("enabled") is not None else True,
        "indexing_status": wf.get("indexing_status"),
        "start_time": start_time,
        "recurrence": recurrence or wf.get("recurrence") or "daily",
        "nodes": wf.get("nodes") or [],
        "edges": wf.get("edges") or [],
        "type": wf.get("type"),
        "updated_at": wf.get("updatedAt") or wf.get("updated_at"),
        "created_at": wf.get("createdAt") or wf.get("created_at"),
    }


def target_nodes(workflow: dict) -> List[dict]:
    """Nodes a schedule can be connected to (orchestrator / agent)."""
    return [{"id": n["id"], "label": f"{(n.get('data') or {}).get('label') or n.get('name') or n['type']} ({n['type']})"}
            for n in workflow.get("nodes") or [] if n.get("type") in ("orchestrator", "agent")]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def apply_schedule(workflow: dict, schedule: dict) -> dict:
    """Create/update the workflow's scheduler node and sync the top-level fields.

    ``schedule`` keys: title, start_time (local HH:MM), recurrence, schedule
    (UTC cron), enabled, description, target_node.
    """
    wf = copy.deepcopy(workflow)
    nodes, edges = list(wf.get("nodes") or []), list(wf.get("edges") or [])
    node = next((n for n in nodes if n.get("type") == "scheduler"), None)
    if node is None:
        node = {"id": f"scheduler-{int(time.time() * 1000)}", "type": "scheduler",
                "position": {"x": 100, "y": 100}, "data": {}}
        nodes.append(node)
        if schedule.get("target_node"):
            edges.append({"id": f"{node['id']}-{schedule['target_node']}", "source": node["id"],
                          "target": schedule["target_node"], "animated": True})
    data = node.get("data") or {}
    enabled = schedule.get("enabled")
    node["data"] = {
        **data,
        "label": schedule.get("title") or data.get("label") or "Schedule",
        "startTime": schedule.get("start_time") or data.get("startTime"),
        "recurrence": schedule.get("recurrence") or data.get("recurrence"),
        "cronExpression": schedule.get("schedule") or data.get("cronExpression"),
        "enabled": enabled if enabled is not None else data.get("enabled", True),
    }
    wf.update({
        "nodes": nodes,
        "edges": clean_orphaned_edges(nodes, edges),
        "schedule": schedule.get("schedule") or wf.get("schedule"),
        "enabled": enabled if enabled is not None else wf.get("enabled"),
        "description": schedule.get("description") or wf.get("description"),
        "startTime": schedule.get("start_time") or node["data"].get("startTime") or wf.get("startTime"),
        "recurrence": schedule.get("recurrence") or node["data"].get("recurrence") or wf.get("recurrence"),
        "updatedAt": _now_iso(),
    })
    wf.setdefault("createdAt", wf["updatedAt"])
    return wf
