"""Scheduler node handler — workflow trigger entry point."""
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict

from . import register


async def _execute(executor, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Execute scheduler/schedule node (trigger point)."""
    # Trigger nodes just mark the entry point — no work to do.
    await asyncio.sleep(0)  # Make function genuinely async
    return {
        "status": "success",
        "output": "Workflow triggered",
        "trigger_time": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    }


# Register under both legacy ('scheduler') and new LangflowEditor ('schedule') type names.
register("scheduler")(_execute)
register("schedule")(_execute)
