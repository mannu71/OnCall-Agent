"""Workflow API routes."""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, status, Query, Depends, Body
from fastapi.responses import StreamingResponse

from app.models.workflow import (
    Workflow,
    WorkflowCreate,
    WorkflowUpdate,
    WorkflowResponse
)
from app.repositories import WorkflowRepository, ExecutionRepository
from app.api.deps import get_workflow_repo, get_execution_repo, verify_workflow_exists
from app.core.scheduler import workflow_scheduler
from app.services.visual_workflow_executor import visual_executor
from app.services.workflow_output_extractor import extract_workflow_output
from app.core.exceptions import NotFoundException
from app.config import settings

router = APIRouter(prefix="/workflows", tags=["workflows"])
logger = logging.getLogger(__name__)


def _get_now_timestamp() -> str:
    """Get current UTC timestamp in ISO format with Z suffix."""
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _find_scheduler_node(nodes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Find the scheduler node in the nodes list.

    Supports both legacy 'scheduler' type and new 'schedule' type.
    """
    return next((n for n in nodes if n.get('type') in ('scheduler', 'schedule')), None)


def _clear_schedule_fields(workflow_dict: Dict[str, Any]) -> None:
    """Clear schedule-related fields from workflow."""
    workflow_dict.update({'schedule': None, 'enabled': False})
    logger.info("[SYNC] No scheduler node found, clearing schedule")


def _params_to_cron(params: Dict[str, Any]) -> Optional[str]:
    """Convert new-schema Schedule node params to a UTC cron expression.

    New Schedule node stores: ``params.frequency``, ``params.time`` (HH:MM local),
    ``params.tz`` (IANA timezone), ``params.days`` (unused for now).
    """
    frequency = (params.get('frequency') or 'Daily').strip()
    time_str = params.get('time') or '09:00'
    tz_str = params.get('tz') or 'UTC'

    freq_lower = frequency.lower()

    # Fixed-interval frequencies — no time conversion needed
    if freq_lower in ('every 5 min', 'every 5 minutes'):
        return '*/5 * * * *'
    if freq_lower in ('every 15 min', 'every 15 minutes'):
        return '*/15 * * * *'
    if freq_lower in ('every 30 min', 'every 30 minutes'):
        return '*/30 * * * *'
    if freq_lower == 'hourly':
        return '0 * * * *'

    # Time-based frequencies — convert local time in tz to UTC
    try:
        hour, minute = (int(p) for p in time_str.split(':'))
    except (ValueError, AttributeError):
        hour, minute = 9, 0

    try:
        from zoneinfo import ZoneInfo
        local_dt = datetime(2024, 1, 15, hour, minute, tzinfo=ZoneInfo(tz_str))
        utc_dt = local_dt.astimezone(ZoneInfo('UTC'))
        utc_hour, utc_minute = utc_dt.hour, utc_dt.minute
    except Exception:
        utc_hour, utc_minute = hour, minute

    if freq_lower == 'weekly':
        return f'{utc_minute} {utc_hour} * * 1'
    if freq_lower == 'monthly':
        return f'{utc_minute} {utc_hour} 1 * *'
    # Daily (default)
    return f'{utc_minute} {utc_hour} * * *'


def _frequency_to_recurrence(frequency: str) -> str:
    """Map UI frequency label to legacy recurrence string."""
    f = (frequency or 'daily').lower()
    if 'weekly' in f:
        return 'weekly'
    if 'monthly' in f:
        return 'monthly'
    return 'daily'


def _sync_update_to_scheduler_node(scheduler_node: Dict[str, Any], update_data: Dict[str, Any]) -> None:
    """Sync top-level schedule fields from update_data to scheduler node.

    Handles both old-schema nodes (``data.cronExpression``) and new-schema
    nodes (``params.frequency`` / ``params.time``).
    """
    if not update_data:
        return

    params = scheduler_node.get('params')
    node_data = scheduler_node.get('data', {})

    if params is not None:
        # New schema node — update params directly
        if 'enabled' in update_data:
            params['enabled'] = update_data['enabled']
        # If a raw cron arrives from the Schedule Management page, store it
        if schedule := update_data.get('schedule'):
            params['_cronOverride'] = schedule
    else:
        # Old schema node — update data fields
        if schedule := update_data.get('schedule'):
            node_data['cronExpression'] = schedule
        if 'enabled' in update_data:
            node_data['enabled'] = update_data['enabled']
        if start_time := update_data.get('startTime'):
            node_data['startTime'] = start_time
        if recurrence := update_data.get('recurrence'):
            node_data['recurrence'] = recurrence


def _sync_scheduler_to_workflow(scheduler_node: Dict[str, Any], workflow_dict: Dict[str, Any]) -> None:
    """Sync scheduler node data to workflow-level fields.

    Supports both schemas:
    - Old (legacy): ``node.data.cronExpression`` / ``startTime`` / ``recurrence``
    - New (LangflowEditor): ``node.params.frequency`` / ``time`` / ``tz``
    """
    params = scheduler_node.get('params')
    node_data = scheduler_node.get('data') or {}

    if params is not None:
        # New schema — derive cron from params.frequency + time + tz
        cron_expression = params.get('_cronOverride') or _params_to_cron(params)
        enabled = params.get('enabled', True)
        start_time = params.get('time')
        recurrence = _frequency_to_recurrence(params.get('frequency', 'Daily'))
    else:
        # Old schema
        cron_expression = node_data.get('cronExpression')
        enabled = node_data.get('enabled', True)
        start_time = node_data.get('startTime')
        recurrence = node_data.get('recurrence')

    if not cron_expression:
        return

    workflow_dict['schedule'] = cron_expression
    workflow_dict['enabled'] = enabled

    if start_time:
        workflow_dict['startTime'] = start_time
    if recurrence:
        workflow_dict['recurrence'] = recurrence

    logger.info(f"[SYNC] Synced from scheduler node: {cron_expression=}, {enabled=}")


def _sync_scheduler_node(workflow_dict: Dict[str, Any], update_data: Optional[Dict[str, Any]] = None) -> None:
    """
    Ensure the workflow level schedule/enabled fields and the scheduler node stay in sync.
    
    Always syncs FROM the scheduler node TO workflow level fields.
    If update_data contains top-level schedule fields, those are synced TO the scheduler node first.
    """
    nodes = workflow_dict.get('nodes', [])
    if not nodes:
        if update_data and 'nodes' in update_data:
            _clear_schedule_fields(workflow_dict)
        return

    scheduler_node = _find_scheduler_node(nodes)
    if not scheduler_node:
        if update_data and 'nodes' in update_data:
            _clear_schedule_fields(workflow_dict)
        return

    # Ensure old-schema nodes have a data dict; new-schema nodes use params (already present)
    if scheduler_node.get('params') is None and 'data' not in scheduler_node:
        scheduler_node['data'] = {}

    # If update_data has top-level schedule fields, sync them TO the scheduler node first
    # (This handles updates from Schedule Management page)
    _sync_update_to_scheduler_node(scheduler_node, update_data)

    # Always sync FROM scheduler node TO workflow level fields
    # This ensures changes made in the Configure Scheduler Node dialog are preserved
    _sync_scheduler_to_workflow(scheduler_node, workflow_dict)


def _validate_orchestrator_nodes(workflow_dict: Dict[str, Any]) -> None:
    """Validate that all orchestrator nodes have SQL files attached.

    Supports both schema shapes:
    - Legacy (ReactFlow):  node['data']['fileName'] / node['data']['fileContent']
    - New (LangflowEditor): node['params']['sqlFile'] / node['params']['sqlContent']

    Args:
        workflow_dict: Workflow dictionary with nodes

    Raises:
        HTTPException: If any orchestrator node is missing SQL file
    """
    nodes = workflow_dict.get('nodes', [])
    orchestrator_nodes = [n for n in nodes if n.get('type') == 'orchestrator']

    nodes_without_sql = []
    for node in orchestrator_nodes:
        # New schema: params.sqlFile / params.sqlContent
        params = node.get('params', {})
        file_name = params.get('sqlFile') or params.get('fileName')
        file_content = params.get('sqlContent') or params.get('fileContent')

        # Legacy schema fallback: data.fileName / data.fileContent
        if not file_name and not file_content:
            node_data = node.get('data', {})
            file_name = node_data.get('fileName')
            file_content = node_data.get('fileContent')

        # Must have either content (new upload) or file name (existing file)
        if not file_name and not file_content:
            node_label = (
                params.get('label')
                or node.get('name')
                or node.get('data', {}).get('label')
                or f"Orchestrator {node.get('id', 'unknown')}"
            )
            nodes_without_sql.append(node_label)

    if nodes_without_sql:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SQL Orchestrator nodes must have an SQL file attached: {', '.join(nodes_without_sql)}"
        )


@router.get("", response_model=List[WorkflowResponse])
async def list_workflows(
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo)
):
    """List all workflows."""
    return await workflow_repo.list_all()


@router.get("/{workflow_name}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_name: str,
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo)
):
    """Get a specific workflow."""
    workflow = await workflow_repo.get_by_name(workflow_name)
    if not workflow:
        raise NotFoundException(
            message=f"Workflow '{workflow_name}' not found",
            details={"workflow_name": workflow_name}
        )
    return WorkflowResponse(**workflow)


@router.post("", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    workflow_data: WorkflowCreate,
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo)
):
    """Create a new workflow."""
    if await workflow_repo.exists(workflow_data.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Workflow '{workflow_data.name}' already exists"
        )
    
    now = _get_now_timestamp()
    workflow_dict = workflow_data.model_dump()
    
    # Validate orchestrator nodes have SQL files
    _validate_orchestrator_nodes(workflow_dict)
    
    # Sync scheduler node and fields
    _sync_scheduler_node(workflow_dict)
    
    # Set timestamps
    workflow_dict['created_at'] = workflow_dict.get('createdAt', now)
    workflow_dict['updated_at'] = workflow_dict.get('updatedAt', now)
    
    saved_workflow = await workflow_repo.save(workflow_dict)
    await workflow_scheduler.reload_workflows()
    
    return WorkflowResponse(**saved_workflow)


@router.put("/{workflow_name}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_name: str,
    workflow_update: WorkflowUpdate,
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo)
):
    """Update an existing workflow."""
    existing_workflow = await workflow_repo.get_by_name(workflow_name)
    if not existing_workflow:
        raise NotFoundException(
            message=f"Workflow '{workflow_name}' not found",
            details={"workflow_name": workflow_name}
        )
    
    update_data = workflow_update.model_dump(exclude_unset=True)
    workflow_dict = {**existing_workflow, **update_data}
    
    logger.info(f"[UPDATE] Updating workflow '{workflow_name}'")
    
    # Validate orchestrator nodes have SQL files
    _validate_orchestrator_nodes(workflow_dict)
    
    # Log scheduler node data if present
    if 'nodes' in workflow_dict:
        scheduler_node = _find_scheduler_node(workflow_dict['nodes'])
        if scheduler_node:
            logger.info(f"[UPDATE] Scheduler node BEFORE sync: data={scheduler_node.get('data')}, params={scheduler_node.get('params')}")
    
    # Sync scheduler node and fields
    _sync_scheduler_node(workflow_dict, update_data)
    
    # Log what was synced
    logger.info(f"[UPDATE] Workflow fields AFTER sync: schedule={workflow_dict.get('schedule')}, enabled={workflow_dict.get('enabled')}, startTime={workflow_dict.get('startTime')}, recurrence={workflow_dict.get('recurrence')}")
    
    # Update timestamps
    now = _get_now_timestamp()
    workflow_dict['updated_at'] = now
    if 'updatedAt' in workflow_dict:
        workflow_dict['updatedAt'] = now
    
    # Preserve created timestamps
    for field in ['created_at', 'createdAt']:
        if field in existing_workflow:
            workflow_dict[field] = existing_workflow[field]
    
    # ── Detect codeAnalyzer repos that need indexing ─────────────────────
    repos_to_index = await _extract_unindexed_repos(workflow_dict)
    if repos_to_index:
        # Disable the workflow while background indexing runs so it cannot
        # be executed with un-indexed repos.
        workflow_dict["enabled"] = False
        workflow_dict["indexing_status"] = "indexing"
        logger.info(
            "[UPDATE] workflow='%s' has %d unindexed repo(s) %s — disabling and firing background indexer",
            workflow_name, len(repos_to_index), repos_to_index,
        )

    # Pass original workflow_name for rename detection
    saved_workflow = await workflow_repo.save(workflow_dict, original_name=workflow_name)
    await workflow_scheduler.reload_workflows()

    if repos_to_index:
        # Resolve the model the workflow's LLM node targets so the indexer
        # uses the same model the user just configured (not the global default).
        indexer_model_id = await _resolve_workflow_model_id(workflow_dict)
        # Fire-and-forget: does not block the HTTP response
        from app.crawler.background_indexer import index_workflow_repos
        task = asyncio.create_task(
            index_workflow_repos(saved_workflow["name"], repos_to_index, model_id=indexer_model_id)
        )
        visual_executor.background_tasks.add(task)
        task.add_done_callback(visual_executor.background_tasks.discard)

    return WorkflowResponse(**saved_workflow)


async def _extract_unindexed_repos(workflow_dict: dict) -> list:
    """Return repo names from codeAnalyzer nodes not yet in repo_abstractions.

    Only repos missing from the ``repo_abstractions`` table are returned so
    that re-saving a workflow with already-indexed repos does not trigger a
    redundant background index run.
    """
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text

    repo_names: list = []
    for node in workflow_dict.get("nodes") or []:
        if node.get("type") == "codeAnalyzer":
            for r in node.get("data", {}).get("repos") or []:
                name = r.get("name") if isinstance(r, dict) else r
                if name:
                    repo_names.append(name)

    if not repo_names:
        return []

    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text("SELECT repo_name FROM repo_abstractions WHERE repo_name = ANY(:names)"),
            {"names": repo_names},
        )
        already_indexed = {r[0] for r in rows.fetchall()}

    return [n for n in repo_names if n not in already_indexed]


async def _resolve_workflow_model_id(workflow_dict: dict) -> "Optional[str]":
    """Return the Bedrock model ID (with cross-region prefix) for the workflow's LLM node.

    Looks at the workflow's ``llm`` node to find which LLM config it references,
    then looks that config up in the DB and applies the same inference-profile
    remapping that ReactStrategy._build_llm() uses (prepend eu./us./ap.).

    Falls back to None when no llm node is found or the config cannot be
    resolved — call_llm will then use the first entry from llm_configs as usual.
    """
    from app.repositories.db_repository import db_repository

    # Find the llm node
    llm_node = next(
        (n for n in (workflow_dict.get("nodes") or []) if n.get("type") == "llm"),
        None,
    )
    if not llm_node:
        return None

    data = llm_node.get("data") or {}
    config_name = data.get("configName") or data.get("llmConfigId") or data.get("model")
    if not config_name:
        return None

    try:
        db_configs = await db_repository.list_llm_configs()
        cfg = db_configs.get(config_name) or next(iter(db_configs.values()), None)
        if not cfg:
            return None

        model  = cfg.get("model", "")
        region = cfg.get("region", "us-east-1")

        # Apply cross-region inference profile prefix (same logic as ReactStrategy)
        _PROFILE_PREFIXES  = ("us.", "eu.", "ap.")
        _NEEDS_PROFILE_FOR = ("anthropic.", "amazon.", "meta.", "mistral.")
        if (model and
                not any(model.startswith(p) for p in _PROFILE_PREFIXES) and
                any(model.startswith(p) for p in _NEEDS_PROFILE_FOR)):
            if region.startswith("eu-"):
                model = f"eu.{model}"
            elif region.startswith("ap-"):
                model = f"ap.{model}"
            else:
                model = f"us.{model}"

        logger.info(
            "_resolve_workflow_model_id: workflow='%s' llm_node config='%s' → model=%s",
            workflow_dict.get("name"), config_name, model,
        )
        return model or None
    except Exception as exc:
        logger.warning("_resolve_workflow_model_id: failed (%s), indexer will use default", exc)
        return None


def _cleanup_active_executions(workflow_name: str) -> None:
    """Cancel and cleanup any active executions for a workflow.
    
    Args:
        workflow_name: Name of the workflow
    """
    execution_ids_to_clear = [
        eid for eid, data in visual_executor.active_executions.items()
        if data.get('workflow_name') == workflow_name
    ]
    
    for execution_id in execution_ids_to_clear:
        visual_executor.cleanup_execution(execution_id)


async def _cleanup_legacy_sql_files(
    sql_files: List[str], 
    workflow_name: str,
    workflow_repo: WorkflowRepository
) -> List[str]:
    """Cleanup legacy SQL files from global directory if not used by other workflows.
    
    Args:
        sql_files: List of SQL filenames
        workflow_name: Name of the workflow being deleted
        workflow_repo: Workflow repository instance
        
    Returns:
        List of deleted SQL filenames
    """
    from pathlib import Path
    
    deleted_scripts = []
    sql_base_path = Path("data") / "config" / "sql"
    
    for sql_file in sql_files:
        sql_path = sql_base_path / sql_file
        if not sql_path.exists():
            continue
            
        # Check if file is used by other workflows
        is_used = await workflow_repo.is_sql_file_used_by_other_workflows(sql_file, workflow_name)
        
        if not is_used:
            try:
                sql_path.unlink()
                deleted_scripts.append(sql_file)
                logger.info(f"Deleted global SQL script: {sql_file}")
            except Exception as e:
                logger.error(f"Error deleting SQL script {sql_file}: {e}")
        else:
            logger.info(f"Kept global SQL script '{sql_file}' (used by other workflows)")
    
    return deleted_scripts


@router.delete("/{workflow_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_name: str,
    delete_scripts: bool = Query(True, description="Also delete SQL scripts from legacy global directory if not used by other workflows"),
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo),
    execution_repo: ExecutionRepository = Depends(get_execution_repo)
):
    """Delete a workflow and all associated files.
    
    Automatically deletes the entire workflow directory (including workflow.yaml and all SQL files).
    
    Args:
        workflow_name: Name of workflow to delete
        delete_scripts: If True, also delete SQL scripts from legacy global sql/ directory (if not shared)
    """
    # Get SQL files from global directory (for legacy cleanup)
    sql_files = await workflow_repo.get_sql_files_for_workflow(workflow_name) if delete_scripts else []
    
    # Delete workflow directory (includes workflow.yaml and all SQL files)
    if not await workflow_repo.delete(workflow_name):
        raise NotFoundException(
            message=f"Workflow '{workflow_name}' not found",
            details={"workflow_name": workflow_name}
        )
    
    # Cancel and cleanup any active executions
    _cleanup_active_executions(workflow_name)
    
    # Delete all execution history for this workflow
    deleted_count = await execution_repo.delete_by_workflow(workflow_name)
    
    # Cleanup legacy SQL scripts from global directory if requested
    deleted_scripts = []
    if delete_scripts and sql_files:
        deleted_scripts = await _cleanup_legacy_sql_files(sql_files, workflow_name, workflow_repo)
    
    # Log deletion summary
    log_msg = f"Deleted workflow directory '{workflow_name}/' (includes workflow.yaml and all SQL files) and {deleted_count} execution records"
    if deleted_scripts:
        log_msg += f"; also cleaned up {len(deleted_scripts)} legacy SQL scripts from global directory: {', '.join(deleted_scripts)}"
    logger.info(log_msg)
    
    await workflow_scheduler.reload_workflows()
    return None


@router.post("/{workflow_name}/execute", response_model=dict)
async def execute_workflow(
    workflow_name: str,
    background: bool = False,
    inputs: Optional[Dict[str, Any]] = Body(None),
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo)
):
    """Manually execute a workflow with optional input parameters."""
    workflow = await workflow_repo.get_by_name(workflow_name)
    if not workflow:
        raise NotFoundException(
            message=f"Workflow '{workflow_name}' not found",
            details={"workflow_name": workflow_name}
        )
    
    # Check if already running (prevents duplicate executions)
    if visual_executor._is_workflow_running(workflow_name):
        return {
            "status": "already_running",
            "workflow_name": workflow_name,
            "message": f"Workflow '{workflow_name}' is already running"
        }
    
    if background:
        task = asyncio.create_task(visual_executor.execute_workflow(workflow, inputs=inputs))
        visual_executor.background_tasks.add(task)
        task.add_done_callback(visual_executor.background_tasks.discard)
        # Keep a local reference to prevent premature garbage collection
        _ = task  # noqa: F841
        return {
            "status": "started",
            "workflow_name": workflow_name,
            "message": f"Workflow '{workflow_name}' execution started in background"
        }
    
    return await visual_executor.execute_workflow(workflow, inputs=inputs)


@router.get("/{workflow_name}/stream")
async def stream_workflow_execution(
    workflow_name: str = Depends(verify_workflow_exists),
):
    """Stream events for an already-running workflow execution via SSE.
    
    Monitors an existing execution. Does NOT start a new one.
    
    Event types emitted:
    - workflow_started, workflow_completed, workflow_failed
    - node_started, node_completed, node_failed
    - llm_token:   real-time LLM output token
    - tool_call:   agent invoking a tool
    - tool_result: tool execution result
    - agent_error: error from agent execution
    - agent_complete: agent finished
    - keepalive:   connection keep-alive
    """
    
    async def event_generator():
        exec_id = visual_executor._is_workflow_running(workflow_name)
        
        if not exec_id:
            yield f"data: {json.dumps({'event': 'no_execution', 'message': 'No active execution found'})}\n\n"
            return
        
        queue = visual_executor.subscribe_to_events(exec_id)
        try:
            yield f"data: {json.dumps({'event': 'connected', 'execution_id': exec_id})}\n\n"
            
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=60.0)
                    
                    event_type = event.event_type
                    event_data = event.dict()
                    
                    if event_type in ("llm_token", "tool_call", "tool_result", "agent_error", "agent_complete"):
                        yield f"event: {event_type}\ndata: {json.dumps(event_data)}\n\n"
                    else:
                        yield f"data: {json.dumps(event_data)}\n\n"
                    
                    if event_type in ("workflow_completed", "workflow_failed"):
                        break
                except asyncio.TimeoutError:
                    if exec_id not in visual_executor.active_executions:
                        break
                    yield f"data: {json.dumps({'event': 'keepalive'})}\n\n"
        finally:
            visual_executor.unsubscribe_from_events(exec_id, queue)
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/{workflow_name}/executions", response_model=List[dict])
async def get_workflow_executions(
    limit: int = Query(50, ge=1, le=500, description="Maximum number of executions to return"),
    workflow_name: str = Depends(verify_workflow_exists),
    execution_repo: ExecutionRepository = Depends(get_execution_repo)
):
    """Get execution history for a workflow."""
    # Harmonized with /executions list endpoint: enrich each row with `output`
    # and top-level token fields via the shared extractor.
    executions = await execution_repo.list_by_workflow(workflow_name, limit=limit)
    return [extract_workflow_output(exec) for exec in executions]
