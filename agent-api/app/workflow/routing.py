"""Canonical workflow execution routing.

Visual (node-based) workflows MUST run through ``VisualWorkflowExecutor`` and
``workflow/executor/handlers``. Legacy task-based workflows (shell/python
tasks) run through ``TaskExecutor`` via the scheduler.

``WorkflowEngine`` is not a supported entry point for visual workflows; handlers
invoke individual strategies (e.g. ``ReactStrategy``) as needed.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Union

from app.models.workflow import WorkflowExecution

logger = logging.getLogger(__name__)


def is_visual_workflow(workflow_data: Dict[str, Any]) -> bool:
    """Return True when the workflow is a node-based visual workflow."""
    return bool(workflow_data.get("nodes"))


def is_legacy_task_workflow(workflow_data: Dict[str, Any]) -> bool:
    """Return True when the workflow uses legacy shell/python tasks (no nodes)."""
    return bool(workflow_data.get("tasks")) and not is_visual_workflow(workflow_data)


async def is_workflow_running(workflow_name: str) -> Optional[str]:
    """Return the active execution id for *workflow_name*, or None."""
    from app.services.execution_state import execution_state

    return await execution_state.find_running_id(workflow_name)


async def execute_visual_workflow(
    workflow_data: Dict[str, Any],
    inputs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute a node-based workflow via ``VisualWorkflowExecutor``."""
    from app.services.visual_workflow_executor import visual_executor

    name = workflow_data.get("name", "<unknown>")
    logger.info("Routing visual workflow '%s' to VisualWorkflowExecutor", name)
    return await visual_executor.execute_workflow(workflow_data, inputs=inputs)


async def execute_legacy_workflow(
    workflow_name: str,
    *,
    manual: bool = False,
) -> Optional[WorkflowExecution]:
    """Execute a legacy task-based workflow via the scheduler / TaskExecutor."""
    from app.core.scheduler import workflow_scheduler

    logger.info(
        "Routing legacy workflow '%s' to TaskExecutor via scheduler", workflow_name
    )
    return await workflow_scheduler.execute_workflow(workflow_name, manual=manual)


async def execute_workflow(
    workflow_data: Dict[str, Any],
    *,
    inputs: Optional[Dict[str, Any]] = None,
    manual: bool = False,
) -> Union[Dict[str, Any], WorkflowExecution, None]:
    """Route workflow execution to the correct runtime.

    Returns:
        Visual workflows: result dict from ``VisualWorkflowExecutor``.
        Legacy workflows: ``WorkflowExecution`` record from the scheduler.
    """
    name = workflow_data.get("name", "<unknown>")

    if is_visual_workflow(workflow_data):
        return await execute_visual_workflow(workflow_data, inputs=inputs)

    if is_legacy_task_workflow(workflow_data):
        return await execute_legacy_workflow(name, manual=manual)

    raise ValueError(
        f"Workflow '{name}' has neither nodes nor tasks; nothing to execute."
    )
