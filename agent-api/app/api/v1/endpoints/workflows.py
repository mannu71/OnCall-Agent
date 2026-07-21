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
from app.infrastructure.persistence import WorkflowRepository, ExecutionRepository
from app.api.deps import get_workflow_repo, get_execution_repo, verify_workflow_exists
from app.core.runtime.scheduler import workflow_scheduler
from app.services.visual_workflow_executor import visual_executor
from app.workflow.routing import execute_workflow as run_workflow, is_workflow_running, is_visual_workflow
from app.services.workflow_output_extractor import extract_workflow_output
from app.core.exceptions import NotFoundException
from app.config import settings
from app.core.streaming.sse import SSE_HEADERS
from app.workflow.event_adapter import workflow_name_event_stream

router = APIRouter(prefix="/workflows", tags=["workflows"])
logger = logging.getLogger(__name__)


def _get_now_timestamp() -> str:
    """Get current UTC timestamp in ISO format with Z suffix."""
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _node_containers(data: Any) -> List[Dict[str, Any]]:
    """Candidate dicts that may hold node outputs (mirrors the FE extractor)."""
    if not isinstance(data, dict):
        return []
    return [c for c in (data, data.get("result"), data.get("results"), data.get("output"))
            if isinstance(c, dict)]


def _extract_final_answer(data: Any) -> str:
    """Dig the agent's final answer out of a node-keyed execute result.

    Server-side mirror of ``extractFinalAnswer`` in agentApiClient.js so a
    persisted assistant message matches what the chat renders.
    """
    if not data:
        return ""
    if isinstance(data, str):
        return data
    if isinstance(data.get("final_answer"), str) and data["final_answer"]:
        return data["final_answer"]
    for c in _node_containers(data):
        for v in c.values():
            if isinstance(v, dict) and isinstance(v.get("final_answer"), str) and v["final_answer"]:
                return v["final_answer"]
    if isinstance(data.get("output"), str) and data["output"].strip():
        return data["output"]
    best = ""
    for c in _node_containers(data):
        for v in c.values():
            if isinstance(v, dict) and isinstance(v.get("output"), str) and len(v["output"]) > len(best):
                best = v["output"]
    return best


def _extract_node_field(data: Any, field: str) -> Any:
    """Return the first node value for *field* (e.g. privacy_redactions)."""
    if not isinstance(data, dict):
        return None
    if data.get(field) is not None:
        return data[field]
    for c in _node_containers(data):
        for v in c.values():
            if isinstance(v, dict) and v.get(field) is not None:
                return v[field]
    return None


async def _persist_chat_message(session_id: str, *, role: str, content: str,
                                metadata: Optional[Dict[str, Any]] = None) -> None:
    """Append one chat message, swallowing all errors (chat persistence is best-effort)."""
    try:
        from app.infrastructure.persistence import session_repository
        await session_repository.append_message(
            session_id, role=role, content=content, metadata=metadata
        )
    except Exception as exc:  # noqa: BLE001 — never fail a run over chat persistence
        logger.warning("chat persistence: append %s failed (%s)", role, exc)


async def _persist_assistant_turn(session_id: str, result_dict: Dict[str, Any]) -> None:
    """Persist the assistant's final answer + UI metadata for a finished run."""
    answer = _extract_final_answer(result_dict)
    if not answer:
        return
    metadata = {
        "input_tokens": result_dict.get("input_tokens", 0) or 0,
        "output_tokens": result_dict.get("output_tokens", 0) or 0,
        "total_tokens": result_dict.get("total_tokens", 0) or 0,
        "cache_read_tokens": result_dict.get("cache_read_tokens", 0) or 0,
        "cache_creation_tokens": result_dict.get("cache_creation_tokens", 0) or 0,
        "context_window_size": result_dict.get("context_window_size", 0) or 0,
        "context_used_tokens": result_dict.get("context_used_tokens", 0) or 0,
        "context_used_pct": result_dict.get("context_used_pct", 0) or 0,
    }
    # Links this chat row to its executions row (build_result always sets
    # this at the top level — app/workflow/executor/result.py) so tool-
    # inclusive history replay can pair an assistant turn with the exact
    # trajectory that produced it, instead of falling back to timestamp
    # bracketing (app.harness.chat_history.rebuild_chat_history).
    execution_id = result_dict.get("execution_id")
    if execution_id is not None:
        metadata["execution_id"] = execution_id
    redactions = _extract_node_field(result_dict, "privacy_redactions")
    if redactions:
        metadata["privacy_redactions"] = redactions
    skills = _extract_node_field(result_dict, "selected_skills")
    if skills:
        metadata["selected_skills"] = skills
    await _persist_chat_message(session_id, role="assistant", content=answer, metadata=metadata)


def _find_scheduler_node(nodes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Find the scheduler node in the nodes list.

    Supports both legacy 'scheduler' type and new 'schedule' type.
    """
    return next((n for n in nodes if n.get('type') in ('scheduler', 'schedule')), None)


def _clear_schedule_fields(workflow_dict: Dict[str, Any]) -> None:
    """Clear schedule-related fields from workflow.

    For agentic processes (type='agent') we only clear the schedule expression —
    the enabled state is user-owned (Active toggle) and must not be forced off.
    Standard workflows without a scheduler node are disabled since they have no
    trigger mechanism.
    """
    if workflow_dict.get("type") == "agent":
        workflow_dict["schedule"] = None
        logger.info("[SYNC] Agentic process — clearing schedule only (preserving enabled)")
    else:
        workflow_dict.update({'schedule': None, 'enabled': False})
        logger.info("[SYNC] No scheduler node found, clearing schedule")


#: Schedule-node weekday label → cron day-of-week number (Sun=0 … Sat=6).
_DOW_NUM = {'Mon': '1', 'Tue': '2', 'Wed': '3', 'Thu': '4', 'Fri': '5', 'Sat': '6', 'Sun': '0'}


def _days_to_cron_dow(days: Any) -> str:
    """Map the Schedule node's ``days`` ("Mon,Tue,…") to a cron day-of-week field.

    Empty or all-seven selections → ``*`` (every day). Order-independent.
    """
    parts = [d.strip() for d in str(days or '').split(',') if d.strip()]
    nums = [_DOW_NUM[d] for d in parts if d in _DOW_NUM]
    if not nums or len(set(nums)) == 7:
        return '*'
    # Preserve Mon→Sun order for readability.
    ordered = [_DOW_NUM[d] for d in ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
               if _DOW_NUM[d] in nums]
    return ','.join(ordered)


def _params_to_cron(params: Dict[str, Any]) -> Optional[str]:
    """Convert new-schema Schedule node params to a UTC cron expression.

    New Schedule node stores: ``params.frequency``, ``params.time`` (HH:MM local),
    ``params.days`` ("Mon,Tue,…"). The timezone is the operator-configured GLOBAL
    timezone (Settings page) — the node no longer carries its own tz.
    """
    from app.core.runtime.app_timezone import get_global_timezone_name

    frequency = (params.get('frequency') or 'Daily').strip()
    time_str = params.get('time') or '09:00'
    # Timezone is global (Settings → global_timezone), not per-node. Any legacy
    # ``params.tz`` is intentionally ignored so all schedules share one tz.
    tz_str = get_global_timezone_name()

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

    # Time-based frequencies — convert local time in the global tz to UTC
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

    if freq_lower == 'monthly':
        return f'{utc_minute} {utc_hour} 1 * *'

    # Daily / Weekly both honour the selected days-of-week. Weekly with no day
    # selected defaults to Monday; Daily with none → every day.
    dow = _days_to_cron_dow(params.get('days'))
    if freq_lower == 'weekly' and dow == '*':
        dow = '1'
    return f'{utc_minute} {utc_hour} * * {dow}'


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
        workflow_dict['schedule'] = None
        return

    if not enabled:
        workflow_dict['schedule'] = None
        workflow_dict['enabled'] = False
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


def _normalize_and_validate(workflow_dict: Dict[str, Any]) -> None:
    """Normalize node dialect (data.* → params.*) and validate required config.

    Normalization is non-destructive; validation raises HTTP 400 with the list of
    field-level problems when a node is missing required config (e.g. a CloudWatch
    node with no log groups, a code analyzer node with no repos).
    """
    from app.workflow.schema import normalize_workflow_dialect, validate_workflow

    normalize_workflow_dialect(workflow_dict)
    errors = validate_workflow(workflow_dict)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Workflow validation failed", "errors": errors},
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

    # Normalize legacy data.* → params.* (non-destructive) then validate per-node
    # required config so a structurally-invalid workflow fails fast at save time
    # rather than mid-execution.
    _normalize_and_validate(workflow_dict)

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


@router.post("/import-spec", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def import_agent_spec(
    spec: Dict[str, Any] = Body(..., description="Declarative agent spec (see app.spec)"),
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo),
):
    """Import a declarative agent spec as a runnable workflow.

    Validates the spec (policy allowlist, MCP resolvability, builtin/sub-agent
    references) and converts it into the platform workflow schema, then creates
    it through the same save path as a UI-built workflow.
    """
    from app.spec import SpecValidationError, spec_to_workflow_dict

    try:
        workflow_dict = spec_to_workflow_dict(spec)
    except SpecValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "spec validation failed", "errors": exc.errors},
        )
    except Exception as exc:  # malformed spec / pydantic error
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid spec: {exc}",
        )

    name = workflow_dict["name"]
    if await workflow_repo.exists(name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Workflow '{name}' already exists",
        )

    now = _get_now_timestamp()
    _normalize_and_validate(workflow_dict)
    _validate_orchestrator_nodes(workflow_dict)
    _sync_scheduler_node(workflow_dict)
    workflow_dict["created_at"] = now
    workflow_dict["updated_at"] = now

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

    # Normalize legacy data.* → params.* (non-destructive) then validate per-node
    # required config (only when this update carries nodes).
    if 'nodes' in workflow_dict:
        _normalize_and_validate(workflow_dict)

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
    # codegraph repos index into the native engine (incremental, self-deduping).
    codegraph_repos = _extract_codegraph_repos(workflow_dict)
    if codegraph_repos:
        # Disable the workflow while background indexing runs so it cannot
        # be executed with un-indexed repos.
        workflow_dict["enabled"] = False
        workflow_dict["indexing_status"] = "indexing"
        logger.info(
            "[UPDATE] workflow='%s' indexing: codegraph=%s — disabling and firing indexer",
            workflow_name, codegraph_repos,
        )

    # Pass original workflow_name for rename detection
    saved_workflow = await workflow_repo.save(workflow_dict, original_name=workflow_name)
    await workflow_scheduler.reload_workflows()

    if codegraph_repos:
        # codegraph indexes via its own in-process engine.
        from app.services.codegraph_indexer import index_workflow_repos_codegraph
        cg_task = asyncio.create_task(
            index_workflow_repos_codegraph(saved_workflow["name"], codegraph_repos)
        )
        visual_executor.background_tasks.add(cg_task)
        cg_task.add_done_callback(visual_executor.background_tasks.discard)

    return WorkflowResponse(**saved_workflow)


def _extract_codegraph_repos(workflow_dict: dict) -> list:
    """Return deduped repo names from code-analyzer nodes.

    codegraph tracks its own index, so we don't filter against a Postgres table;
    ``index_repository`` is incremental and dedupes unchanged files on its side.
    """
    from app.workflow.code_analyzer_config import (
        CODE_ANALYZER_NODE_TYPES,
        read_code_analyzer_repos,
    )

    repo_names: list = []
    for node in workflow_dict.get("nodes") or []:
        if node.get("type") in CODE_ANALYZER_NODE_TYPES:
            for r in read_code_analyzer_repos(node):
                if r["name"]:
                    repo_names.append(r["name"])

    return list(dict.fromkeys(repo_names))


async def _cleanup_active_executions(workflow_name: str) -> None:
    """Cancel and cleanup any active executions for a workflow."""
    from app.services.execution_state import execution_state

    await execution_state.cancel_by_workflow_name(workflow_name)
    for execution_id in list(visual_executor.event_queues.keys()):
        if execution_id not in execution_state.runtime_cache:
            visual_executor.event_queues.pop(execution_id, None)


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
    await _cleanup_active_executions(workflow_name)
    
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
    query: Optional[str] = None,
    input: Optional[str] = None,
    output_mode: Optional[str] = None,
    permission_mode: Optional[str] = None,
    session_id: Optional[str] = None,
    inputs: Optional[Dict[str, Any]] = Body(None),
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo)
):
    """Manually execute a workflow with optional input parameters.

    The interactive chat query can arrive as the ``query`` or ``input`` query
    param OR inside the ``inputs`` body. Normalize all of them to
    ``inputs['user_query']`` so the agent's ReAct loop actually receives it
    (previously the chat's ``?input=`` was silently dropped because only the body
    was read).

    When ``session_id`` is supplied (chat with a persisted session), the user
    turn is recorded before the run and the assistant turn after it, so the
    conversation survives a page refresh. With no ``session_id`` the behaviour is
    unchanged — persistence is purely additive.
    """
    _typed_query = query or input
    if _typed_query:
        inputs = {**(inputs or {}), "user_query": _typed_query}
    if output_mode:
        # "Data Query Mode" — agent returns a validated InvestigationReport too.
        inputs = {**(inputs or {}), "output_mode": output_mode}
    if permission_mode:
        # Tool gatekeeping: default | auto_allow | plan.
        inputs = {**(inputs or {}), "permission_mode": permission_mode}
    if session_id:
        # Marks this execution as chat-triggered (Dashboard "Recent runs"
        # excludes these by default — see build_result/persist_execution).
        # Prefixed to avoid colliding with a real workflow input also named
        # "session_id".
        inputs = {**(inputs or {}), "_chat_session_id": session_id}

    workflow = await workflow_repo.get_by_name(workflow_name)
    if not workflow:
        raise NotFoundException(
            message=f"Workflow '{workflow_name}' not found",
            details={"workflow_name": workflow_name}
        )

    # Block execution of inactive workflows (standard and agentic alike)
    if not workflow.get("enabled", True):
        return {
            "status": "inactive",
            "workflow_name": workflow_name,
            "message": f"Workflow '{workflow_name}' is not active. Enable it before running."
        }

    # Check if already running (prevents duplicate executions)
    if await is_workflow_running(workflow_name):
        return {
            "status": "already_running",
            "workflow_name": workflow_name,
            "message": f"Workflow '{workflow_name}' is already running"
        }

    # Persist the user turn up-front (best-effort; never blocks the run).
    if session_id and _typed_query:
        await _persist_chat_message(session_id, role="user", content=_typed_query)

    if background:
        task = asyncio.create_task(
            run_workflow(workflow, inputs=inputs, manual=True)
        )
        if is_visual_workflow(workflow):
            visual_executor.background_tasks.add(task)
            task.add_done_callback(visual_executor.background_tasks.discard)
        return {
            "status": "started",
            "workflow_name": workflow_name,
            "message": f"Workflow '{workflow_name}' execution started in background"
        }

    # Run as a registered, genuinely-cancellable task (not a bare await): the
    # foreground path used to just `await run_workflow(...)` directly, which
    # gave "Stop" nothing to cancel — cancel_execution could only mark the DB
    # row cancelled while this coroutine kept running orphaned, so the
    # workflow stayed "running" and the next message was rejected with
    # already_running. visual_workflow_executor.execute_workflow registers
    # this exact task (via asyncio.current_task()) against the execution_id
    # the instant it's minted, so cancel_execution's task.cancel() now reaches
    # here for real.
    fg_task = asyncio.create_task(run_workflow(workflow, inputs=inputs, manual=True))
    try:
        result = await fg_task
    except asyncio.CancelledError:
        logger.info(
            "Workflow '%s' execution cancelled (Stop / operator cancel)", workflow_name,
        )
        return {
            "status": "cancelled",
            "workflow_name": workflow_name,
            "message": f"Workflow '{workflow_name}' was cancelled",
        }
    result_dict = result if isinstance(result, dict) else result.model_dump(mode="json")

    # Persist the assistant turn (final answer + token/privacy/skill metadata).
    if session_id:
        await _persist_assistant_turn(session_id, result_dict)
        # Best-effort: collapse older turns into a summary once the session's
        # replay grows past budget, so a long conversation doesn't keep
        # resending every prior full report every turn.
        try:
            from app.core.context.compaction_manager import compact_chat_session_if_needed
            await compact_chat_session_if_needed(session_id)
        except Exception as _cc_exc:  # noqa: BLE001 — never fail a run over compaction
            logger.warning("chat session compaction skipped (%s)", _cc_exc)

    return result_dict


@router.get("/{workflow_name}/stream")
async def stream_workflow_execution(
    workflow_name: str = Depends(verify_workflow_exists),
):
    """Stream events for an already-running workflow execution via SSE."""
    return StreamingResponse(
        workflow_name_event_stream(workflow_name),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


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
