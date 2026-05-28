"""Visual workflow executor for node-based workflows."""
import asyncio
import os
import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from collections import defaultdict
from pathlib import Path

from app.repositories import ExecutionRepository
from app.services.mcp_client_manager import MCPClientManager
from app.mcp.tools.watch_tools import (
    watch_log_groups,
    analyze_log_patterns,
    detect_anomalies,
    correlate_logs,
)
from app.core.redact import redact
# Streaming subsystem (extracted) — re-exported for backwards compatibility.
from app.workflow.executor.streaming import (  # noqa: F401
    _AgentStreamCallback,
    _DROPPED_EVENTS,
    _SSE_QUEUE_MAXSIZE,
    _safe_put,
    get_dropped_event_count,
)

logger = logging.getLogger(__name__)

# Node types that contain only connection metadata (excluded from sanitized output)
_TOOL_NODE_KEYS = {'server_id', 'tool_data'}


class _ExecutionEvent:
    """Internal execution event for SSE streaming."""
    def __init__(self, event_type: str, data: Dict[str, Any]):
        self.event_type = event_type
        self.data = data
        self.timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    
    def dict(self):
        return {
            "event_type": self.event_type,
            "data": self.data,
            "timestamp": self.timestamp
        }


class VisualWorkflowExecutor:
    """Executes visual node-based workflows."""
    
    def __init__(self):
        self.active_executions: Dict[str, Dict[str, Any]] = {}
        self.event_queues: Dict[str, List[asyncio.Queue]] = defaultdict(list)
        self.mcp_managers: Dict[str, MCPClientManager] = {}  # Per-execution MCP managers
        self.background_tasks: set = set()  # Keep references to background tasks
    
    @staticmethod
    def _build_node_result(value: Dict[str, Any]) -> Dict[str, Any]:
        """Extract relevant fields from a node execution result."""
        node_result = {}
        for field in ('status', 'output', 'trigger_time', 'model',
                      'input_tokens', 'output_tokens', 'total_tokens',
                      # Agent/ReAct specific fields — preserved for Dashboard detail view
                      'final_answer', 'tool_calls', 'provider', 'message_count', 'user_query',
                      # Full LangChain message trace — required for trajectory persistence
                      'messages'):
            if field in value:
                node_result[field] = value[field]
        
        if 'queries_executed' in value:
            node_result['queries_executed'] = value.get('queries_executed')
            node_result['failures'] = value.get('failures', 0)
            node_result['results'] = value.get('results', [])
            err = value.get('error')
            if err:
                node_result['error'] = err

        if 'analysis_type' in value:
            node_result['analysis_type'] = value.get('analysis_type')
            # cloudwatch_tool (tool-provider stub) stores configured groups as 'log_groups';
            # legacy cloudwatchAnalyzer stores analyzed groups as 'log_groups_analyzed'.
            # Prefer log_groups_analyzed; fall back to log_groups so the Dashboard always has data.
            node_result['log_groups_analyzed'] = (
                value.get('log_groups_analyzed') or value.get('log_groups', [])
            )
            node_result['time_range'] = value.get('time_range')
            node_result['data'] = value.get('data', {})
            alerts = value.get('alerts')
            if alerts:
                node_result['alerts'] = alerts
        
        return node_result
    
    def _sanitize_results(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """Remove internal implementation details and sensitive data from results."""
        excluded_keys = {'execution_id', 'inputs', 'connected_tool_server', 'db_server_map'}
        sanitized = {}
        
        for key, value in results.items():
            if key in excluded_keys:
                continue
            if not isinstance(value, dict):
                sanitized[key] = value
                continue
            
            # Skip tool nodes (connection metadata only)
            if value.keys() & _TOOL_NODE_KEYS:
                continue
            
            node_result = self._build_node_result(value)
            if node_result:
                sanitized[key] = node_result
        
        return sanitized
    
    def subscribe_to_events(self, execution_id: str) -> asyncio.Queue:
        """Subscribe to execution events."""
        queue = asyncio.Queue(maxsize=_SSE_QUEUE_MAXSIZE)
        self.event_queues[execution_id].append(queue)
        return queue
    
    def unsubscribe_from_events(self, execution_id: str, queue: asyncio.Queue):
        """Unsubscribe from execution events."""
        if execution_id in self.event_queues:
            try:
                self.event_queues[execution_id].remove(queue)
            except ValueError:
                pass
    
    async def _publish_event(self, execution_id: str, event_type: str, data: Dict[str, Any]):
        """Publish an event to all subscribers."""
        event = _ExecutionEvent(event_type, data)
        
        # Store in execution state
        if execution_id in self.active_executions:
            if 'events' not in self.active_executions[execution_id]:
                self.active_executions[execution_id]['events'] = []
            self.active_executions[execution_id]['events'].append(event.dict())
        
        # Publish to queues using non-blocking put with drop-on-full semantics.
        # Prevents a slow SSE subscriber from stalling the executor's event loop.
        for queue in self.event_queues.get(execution_id, []):
            try:
                _safe_put(queue, event)
            except Exception as e:
                logger.warning(f"Error publishing event: {e}")
    
    async def publish_token_usage_delta(self, execution_id: str, ledger) -> None:
        """Push a ``token_usage_delta`` SSE event with current ledger totals.

        Strategies that wire up a per-execution ``TokenLedger`` via the
        ``on_usage`` callback (see ``app/core/transport/anthropic_transport.py``)
        call this helper after each LLM round so the UI shows live spend
        — closing the loop on plan §2.6.

        Argument ``ledger`` is duck-typed: anything exposing ``totals()``
        works. We accept Any so tests can pass a stub without importing
        the production type.
        """
        try:
            totals = ledger.totals() if hasattr(ledger, "totals") else dict(ledger)
        except Exception as e:  # noqa: BLE001
            logger.warning("publish_token_usage_delta: bad ledger (%s)", e)
            return
        await self._publish_event(execution_id, "token_usage_delta", totals)

    def _is_workflow_running(self, workflow_name: str) -> Optional[str]:
        """Return execution ID if workflow is already running, None otherwise."""
        for exec_id, exec_data in self.active_executions.items():
            if exec_data.get('workflow_name') == workflow_name and exec_data.get('status') == 'running':
                return exec_id
        return None

    async def execute_workflow(self, workflow: Dict[str, Any], inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute a visual workflow and return results."""
        workflow_name = workflow.get('name')
        
        # Prevent duplicate concurrent executions of the same workflow
        existing_id = self._is_workflow_running(workflow_name)
        if existing_id:
            logger.warning(f"Workflow '{workflow_name}' is already running (ID: {existing_id}), skipping")
            return {
                "status": "skipped",
                "message": f"Workflow '{workflow_name}' is already running",
                "existing_execution_id": existing_id
            }
        
        execution_id = str(uuid.uuid4())
        start_time = datetime.now(timezone.utc)
        
        logger.info(f"Starting workflow execution: {workflow_name} (ID: {execution_id})")
        
        # Initialize execution state
        self.active_executions[execution_id] = {
            "id": execution_id,
            "workflow_name": workflow_name,
            "workflow_id": workflow.get('id'),
            "workflow": workflow,  # Full workflow stored for agent node access
            "status": "running",
            "start_time": start_time.isoformat().replace('+00:00', 'Z'),
            "nodes_completed": [],
            "events": [],
            "inputs": inputs or {}
        }
        
        result = None
        try:
            await self._publish_event(execution_id, "workflow_started", {
                "execution_id": execution_id,
                "workflow_name": workflow_name,
                "workflow_id": workflow.get('id')
            })
            
            nodes = workflow.get('nodes', [])
            edges = workflow.get('edges', [])
            
            logger.info(f"Workflow has {len(nodes)} nodes and {len(edges)} edges")
            
            if not nodes:
                raise ValueError("Workflow has no nodes to execute")
            
            adjacency, start_nodes, parents_of, node_map = self._build_execution_graph(nodes, edges)
            executed, execution_results = await self._execute_nodes_bfs(
                execution_id, adjacency, start_nodes, parents_of, node_map
            )
            
            # Check if any node failed
            failed_nodes = [
                node_id for node_id, node_result in execution_results.items()
                if isinstance(node_result, dict) and node_result.get('status') == 'failed'
            ]
            
            if failed_nodes:
                # Mark workflow as failed if any node failed
                error_messages = []
                for node_id in failed_nodes:
                    node_result = execution_results[node_id]
                    error_msg = node_result.get('error', 'Unknown error')
                    error_messages.append(f"{node_id}: {error_msg}")
                
                result = self._build_result(
                    execution_id, "failed", start_time, len(executed),
                    sanitized_results=self._sanitize_results(execution_results),
                    error=f"Node(s) failed: {'; '.join(error_messages)}"
                )
            else:
                result = self._build_result(
                    execution_id, "success", start_time, len(executed),
                    sanitized_results=self._sanitize_results(execution_results)
                )
            
            self.active_executions[execution_id]['status'] = 'completed'
            self.active_executions[execution_id]['end_time'] = result['end_time']
            self.active_executions[execution_id]['duration'] = result['duration']
            
            _itok = sum(v.get('input_tokens', 0) or 0 for v in execution_results.values() if isinstance(v, dict))
            _otok = sum(v.get('output_tokens', 0) or 0 for v in execution_results.values() if isinstance(v, dict))
            await self._publish_event(execution_id, "workflow_completed", {
                "execution_id": execution_id,
                "status": "success",
                "duration": result['duration'],
                "nodes_executed": len(executed),
                "input_tokens":  _itok,
                "output_tokens": _otok,
                "total_tokens":  _itok + _otok,
            })
        
        except Exception as e:
            result = self._build_result(execution_id, "failed", start_time, error=str(e))
            logger.error(f"Workflow execution failed: {execution_id} - {e}")
            
            if execution_id in self.active_executions:
                self.active_executions[execution_id]['status'] = 'failed'
                self.active_executions[execution_id]['error'] = str(e)
                self.active_executions[execution_id]['end_time'] = result['end_time']
            
            await self._publish_event(execution_id, "workflow_failed", {
                "execution_id": execution_id,
                "error": str(e),
                "duration": result['duration']
            })
        
        finally:
            # Persist before cleanup (cleanup removes events from active_executions)
            if result:
                await self._persist_execution(result, workflow, execution_id)
            await self.cleanup_execution(execution_id)
        
        return result
    
    @staticmethod
    def _build_execution_graph(nodes, edges):
        """Backwards-compat delegator — see app.workflow.executor.graph.build_execution_graph."""
        from app.workflow.executor.graph import build_execution_graph
        return build_execution_graph(nodes, edges)

    async def _execute_nodes_bfs(self, execution_id, adjacency, start_nodes, parents_of, node_map):
        """Backwards-compat delegator — see app.workflow.executor.graph.execute_nodes_bfs."""
        from app.workflow.executor.graph import execute_nodes_bfs
        return await execute_nodes_bfs(
            self._execute_node, adjacency, start_nodes, parents_of, node_map, execution_id,
        )
    
    @staticmethod
    def _build_result(execution_id, status, start_time, nodes_executed=0, sanitized_results=None, error=None):
        """Backwards-compat delegator — see app.workflow.executor.result.build_result."""
        from app.workflow.executor.result import build_result
        return build_result(execution_id, status, start_time, nodes_executed, sanitized_results, error)

    async def _persist_execution(self, result: Dict[str, Any], workflow: Dict[str, Any], execution_id: str):
        """Save execution result to storage (delegates to app.workflow.executor.result)."""
        from app.workflow.executor.result import persist_execution
        execution_repo = ExecutionRepository()
        events = self.active_executions.get(execution_id, {}).get('events', [])
        await persist_execution(execution_repo, result, workflow, events)
    
    async def _execute_node(self, execution_id: str, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single node."""
        node_id = node.get('id')
        node_type = node.get('type')
        node_data = node.get('data', {})
        
        await self._publish_event(execution_id, "node_started", {
            "node_id": node_id,
            "node_type": node_type,
            "label": node_data.get('label', node_type)
        })
        
        start_time = datetime.now(timezone.utc)
        context['execution_id'] = execution_id
        context['inputs'] = self.active_executions[execution_id].get('inputs', {})
        context['workflow_name'] = self.active_executions[execution_id].get('workflow_name')
        
        # Node type → executor mapping (legacy hardcoded types)
        executors = {
            'scheduler':          self._execute_scheduler_node,
            'orchestrator':       self._execute_orchestrator_node,
            'agent':              self._execute_agent_node,
            'batchAgent':         self._execute_batch_agent_node,
            'tool':               self._execute_tool_node,
            'cloudwatchAnalyzer': self._execute_cloudwatch_node,
            'codeAnalyzer':       self._execute_code_analyzer_node,
        }

        try:
            from app.workflow.executor.handlers import HANDLERS
            executor = executors.get(node_type)
            if executor:
                result = await executor(node, context)
            elif node_type in HANDLERS:
                # New handler-registry types (e.g. 'schedule', 'database')
                result = await HANDLERS[node_type](self, node, context)
            else:
                result = {
                    "status": "skipped",
                    "message": f"Unknown node type: {node_type}"
                }
            
            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds()
            
            await self._publish_event(execution_id, "node_completed", {
                "node_id": node_id,
                "node_type": node_type,
                "status": result.get('status', 'success'),
                "duration": duration,
                "output": result.get('output', '')[:500],  # Truncate for event
                "input_tokens":  result.get('input_tokens',  0) or 0,
                "output_tokens": result.get('output_tokens', 0) or 0,
                "total_tokens":  result.get('total_tokens',  0) or 0,
            })
            
            self.active_executions[execution_id]['nodes_completed'].append(node_id)
            
            return result
        
        except Exception as e:
            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds()
            
            await self._publish_event(execution_id, "node_failed", {
                "node_id": node_id,
                "node_type": node_type,
                "error": str(e),
                "duration": duration
            })
            
            return {
                "status": "failed",
                "error": str(e)
            }
    
    async def _execute_scheduler_node(self, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Backwards-compat delegator — see app.workflow.executor.handlers.scheduler."""
        from app.workflow.executor.handlers import HANDLERS
        return await HANDLERS["scheduler"](self, node, context)
    
    @staticmethod
    def _load_sql_content(node_data: Dict[str, Any], workflow_name: Optional[str] = None) -> Optional[str]:
        """Load SQL content from node data, either inline or from a file.
        
        Args:
            node_data: The orchestrator node data containing sqlFile/fileName
            workflow_name: Optional workflow name to look for SQL in workflow's own directory
            
        Returns:
            SQL content string or None if not found
        """
        # Check for SQL content in order of preference
        sql_content = node_data.get('sqlContent') or node_data.get('fileContent')
        if sql_content:
            return sql_content
        
        sql_file = node_data.get('sqlFile') or node_data.get('fileName')
        if not sql_file:
            return None
        
        sql_filename = sql_file.replace('sql/', '').replace('sql\\', '')
        
        # Search paths in priority order:
        search_paths = []
        
        # 1. Workflow's own directory (if workflow_name provided)
        if workflow_name:
            safe_name = workflow_name.replace(' ', '_').replace('/', '_').replace('\\', '_')
            search_paths.extend([
                Path('data/storage/workflows') / safe_name / sql_filename,
                Path('/app/data/storage/workflows') / safe_name / sql_filename,
            ])
        
        # 2. Global SQL directories (fallback for backwards compatibility)
        search_paths.extend([
            Path('data/config/sql') / sql_filename,
            Path('/app/data/config/sql') / sql_filename,
        ])
        
        for candidate in search_paths:
            if candidate.exists():
                logger.info(f"Loaded SQL from: {candidate}")
                return candidate.read_text(encoding='utf-8')
        
        return None
    
    async def _execute_orchestrator_node(self, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Backwards-compat delegator — see app.workflow.executor.handlers.orchestrator."""
        from app.workflow.executor.handlers import HANDLERS
        return await HANDLERS["orchestrator"](self, node, context)
    
    async def _execute_agent_node(self, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Backwards-compat delegator — see app.workflow.executor.handlers.agent."""
        from app.workflow.executor.handlers import HANDLERS
        return await HANDLERS["agent"](self, node, context)

    async def _execute_batch_agent_node(self, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Backwards-compat delegator — see app.workflow.executor.handlers.batch_agent."""
        from app.workflow.executor.handlers import HANDLERS
        return await HANDLERS["batchAgent"](self, node, context)

    async def _execute_tool_node(self, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Backwards-compat delegator — see app.workflow.executor.handlers.tool."""
        from app.workflow.executor.handlers import HANDLERS
        return await HANDLERS["tool"](self, node, context)
    
    @staticmethod
    def _parse_time_range_minutes(time_range: str) -> int:
        """Convert time range string (e.g. '15m', '1h', '7d') to minutes."""
        units = {'m': 1, 'h': 60, 'd': 1440}
        if not time_range:
            return 60
        suffix = time_range[-1]
        if suffix not in units:
            return 60
        try:
            value = int(time_range[:-1])
            return value * units[suffix]
        except (ValueError, IndexError):
            return 60

    async def _execute_cloudwatch_node(self, node: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Backwards-compat delegator — see app.workflow.executor.handlers.cloudwatch."""
        from app.workflow.executor.handlers import HANDLERS
        return await HANDLERS["cloudwatchAnalyzer"](self, node, context)

    # ------------------------------------------------------------------
    # Code Analyzer node
    # ------------------------------------------------------------------

    async def _execute_code_analyzer_node(
        self,
        node: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute a Code Analyzer node.

        Asserts that all configured repos are already indexed in
        ``repo_abstractions`` (indexing now happens at workflow-save time via
        background_indexer.py, not at run time).  Returns early with a clear
        error if any repo is missing.

        Optionally runs a pre-execution semantic summary search when
        ``preSummary`` is enabled on the node.

        Returns a result dict with ``code_analysis_type`` discriminator so the
        downstream agent node can pick it up from ``context``.
        """
        import os
        import re as _re

        from app.core.security import check_path, PathJailError
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import text as _text

        node_data    = node.get('data', {})
        execution_id = context.get('execution_id')
        node_id      = node.get('id')  # noqa: F841 — retained for _publish_event calls
        repos        = node_data.get('repos', [])
        pre_summary  = node_data.get('preSummary', False)

        REPOS_BASE_PATH = os.getenv('REPOS_BASE_PATH', '/tmp/indexed_repos')

        if not repos:
            return {
                'status': 'failed',
                'error': 'No repos configured for Code Analyzer node.',
                'code_analysis_type': 'pre_summary',
            }

        # ── Cross-platform path normalisation ──────────────────────────────
        # When the backend runs on Linux/Docker but the workflow was configured
        # from a Windows client, repo paths may look like "C:/Users/.../repo".
        # Path("C:/...").resolve() on Linux prepends CWD and escapes the jail.
        # Remap such paths to REPOS_BASE_PATH/{repo_name} with a clear warning.
        _WIN_ABS_RE = _re.compile(r'^[A-Za-z]:[/\\]')

        def _normalise_repo_path(repo_cfg: dict) -> dict:
            raw = repo_cfg.get('path', '')
            if raw and _WIN_ABS_RE.match(raw) and os.sep == '/':
                safe_name = (
                    repo_cfg.get('name')
                    or os.path.basename(raw.replace('\\', '/').rstrip('/'))
                )
                remapped = os.path.join(REPOS_BASE_PATH, safe_name)
                logger.warning(
                    "codeAnalyzer: Windows path %r is not accessible from a "
                    "Linux/Docker host; remapped to %r. "
                    "To index local source, mount the directory into the container "
                    "at that path or set REPOS_BASE_PATH accordingly.",
                    raw, remapped,
                )
                return {**repo_cfg, 'path': remapped}
            return repo_cfg

        repos = [_normalise_repo_path(r) for r in repos]

        # ── Security guards ────────────────────────────────────────────────
        try:
            # Lazy import to keep the import chain light until needed
            from app.core.security import scan_injection, InjectionError  # type: ignore
        except ImportError:
            # scan_injection may not exist yet on older deployments — define no-op
            def scan_injection(text: str) -> None:  # type: ignore
                pass
            class InjectionError(Exception):  # type: ignore
                pass

        for repo in repos:
            try:
                scan_injection(repo.get('name', ''))
                scan_injection(repo.get('path', ''))
            except InjectionError as exc:
                return {
                    'status': 'failed',
                    'error': f'Injection detected in repo config: {exc}',
                    'code_analysis_type': 'pre_summary',
                }
            try:
                check_path(repo.get('path', ''), jail=REPOS_BASE_PATH)
            except PathJailError as exc:
                return {
                    'status': 'failed',
                    'error': f'Repo path outside allowed root ({REPOS_BASE_PATH}): {exc}',
                    'code_analysis_type': 'pre_summary',
                }

        # ── Assert repos are indexed ───────────────────────────────────────
        # Indexing now happens at workflow-save time (background_indexer.py),
        # not at execution time.  We simply verify every repo has a row in
        # repo_abstractions and bail out early with a clear message if not.
        unindexed: List[str] = []
        all_indexed: List[str] = []

        for repo in repos:
            repo_name = repo.get('name', '')
            if not repo_name:
                continue
            try:
                async with AsyncSessionLocal() as chk_session:
                    chk_row = await chk_session.execute(
                        _text(
                            "SELECT 1 FROM repo_abstractions WHERE repo_name = :rn"
                        ),
                        {'rn': repo_name},
                    )
                    if chk_row.fetchone() is not None:
                        all_indexed.append(repo_name)
                    else:
                        unindexed.append(repo_name)
            except Exception as chk_exc:
                logger.debug('codeAnalyzer: freshness check failed for %s: %s', repo_name, chk_exc)
                unindexed.append(repo_name)

        if unindexed:
            return {
                'status': 'not_indexed',
                'error': (
                    f"Repos not yet indexed: {unindexed}. "
                    "Re-save the workflow to trigger background indexing."
                ),
                'code_analysis_type': 'pre_summary',
            }

        repos_config_with_lg = repos  # pass full config to agent so logGroupToRepo mapping is available

        # ── Optional pre-execution summary ────────────────────────────────
        pre_summary_results: Dict[str, Any] = {}
        if pre_summary and all_indexed:
            inputs = context.get('inputs', {})
            search_query = (
                inputs.get('service_name')
                or inputs.get('error_signature')
                or inputs.get('component')
                or inputs.get('user_query', '')[:200]
            )
            if search_query:
                from app.mcp.tools.crawler_tools import crawler_search_semantic as _search_semantic
                for repo in repos:
                    rn = repo.get('name', '')
                    if rn not in all_indexed:
                        continue
                    try:
                        hits = await _search_semantic(
                            query=search_query, repo=rn, limit=5,
                        )
                        pre_summary_results[rn] = hits
                    except Exception as se:
                        logger.debug('codeAnalyzer pre-summary search failed for %s: %s', rn, se)

        return {
            'status':             'success',
            'output':             f'Repos ready: {", ".join(all_indexed)}',
            'code_analysis_type': 'pre_summary',
            'repos_indexed':      all_indexed,
            'repos_config':       repos_config_with_lg,
            'pre_summary':        pre_summary_results or None,
        }

    async def _correlate_anomalies_to_code(
        self,
        cw_results: Dict[str, Any],
        code_results: Dict[str, Any],
        log_group_to_repo: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        """Lightweight anomaly→code correlation.  No LLM — regex + exact match.

        Extracts log groups and error/function names from CloudWatch anomaly
        text, maps to repo names via ``log_group_to_repo``, then matches
        against ``code_chunks.name``.

        Returns a list of correlation dicts suitable for injection into the
        agent's initial context block.
        """
        import re as _re
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import text as _text

        _ENTITY_RE = _re.compile(
            r'(?:ERROR|Exception|error|exception)\s+in\s+([\w\.]+)',
            _re.IGNORECASE,
        )

        correlations: List[Dict[str, Any]] = []

        for _key, cw_val in cw_results.items():
            output_text = cw_val.get('output', '') or ''
            log_groups  = cw_val.get('log_groups_analyzed') or []

            for lg in log_groups:
                repo_name = log_group_to_repo.get(lg)
                if not repo_name:
                    continue

                # Extract function/class names from anomaly text
                matches = _ENTITY_RE.findall(output_text)
                for match in matches:
                    func_name = match.split('.')[-1]  # "auth.service.authenticate_user" → "authenticate_user"
                    if not func_name:
                        continue
                    # Locate the symbol via the crawler
                    try:
                        from app.mcp.tools.crawler_tools import crawler_find_symbol as _find_sym
                        hit = await _find_sym(symbol=func_name, repo=repo_name, limit=1)
                        results_list = hit.get('results', [])
                        if results_list:
                            r = results_list[0]
                            correlations.append({
                                'log_group':  lg,
                                'repo':       repo_name,
                                'anomaly_snippet': output_text[:200],
                                'matched_function': func_name,
                                'matched_file':     r.get('file', ''),
                                'matched_line':     r.get('line'),
                                'confidence':       'exact_name_match',
                                'suggestion': (
                                    f'crawler_trace_path(symbol="{func_name}", '
                                    f'repo="{repo_name}")'
                                ),
                            })
                    except Exception as corr_exc:
                        logger.warning(
                            "Anomaly→code correlation failed for repo %s: %s",
                            repo_name, corr_exc, exc_info=True,
                        )

        return correlations

    async def _analyze_cloudwatch_with_llm(
        self,
        raw_result: Dict[str, Any],
        analysis_type: str,
        log_groups: List[str],
        time_range: str,
        alerts: list,
        execution_id: str,
    ) -> tuple:
        """Use the workflow's LLM node to produce a structured analysis.

        Attempts structured output (Pydantic schema) for providers that
        support it (OpenAI, Anthropic, Bedrock with Claude).  Falls back to
        free-form text for others (Ollama, Groq).

        Returns:
            Tuple of (analysis_text, model_name).  Both are ``None`` when
            no LLM is available or the call fails (graceful fallback to
            the static summary built by ``_build_cloudwatch_summary``).
        """
        try:
            from app.workflow.strategies.react import ReactStrategy
            from langchain_core.messages import SystemMessage, HumanMessage
            from pydantic import BaseModel as _PydanticBase, Field as _Field
            from typing import List as _List
            import json

            # ----------------------------------------------------------
            # Structured output schema
            # ----------------------------------------------------------
            class CloudWatchAnalysisSummary(_PydanticBase):
                """Structured CloudWatch incident analysis for on-call engineers."""
                headline: str = _Field(
                    description="One-sentence incident summary suitable for Slack or PagerDuty."
                )
                severity: str = _Field(
                    description="Overall severity: critical | high | medium | low | none."
                )
                key_findings: _List[str] = _Field(
                    description="2-5 bullet points describing what was found."
                )
                root_cause_hypothesis: str = _Field(
                    description=(
                        "Most likely root cause based on the data. "
                        "Use 'Unknown' if there is insufficient evidence."
                    )
                )
                recommended_actions: _List[str] = _Field(
                    description="Ordered list of actions for the on-call engineer."
                )
                related_services: _List[str] = _Field(
                    default_factory=list,
                    description="Other services likely involved, inferred from patterns.",
                )
                confidence: str = _Field(
                    description="Confidence in this analysis: high | medium | low."
                )

            active = self.active_executions.get(execution_id, {})
            workflow = active.get('workflow')
            if not workflow:
                return None, None, None

            nodes = workflow.get('nodes', [])
            if not any(n.get('type') == 'llm' for n in nodes):
                logger.debug("CloudWatch LLM analysis skipped: no LLM node in workflow")
                return None, None, None

            strategy = ReactStrategy()
            llm_config = await strategy._resolve_llm_config(workflow)
            llm = strategy._build_llm(llm_config)
            model_name = llm_config.get('model')

            # Serialize raw data (truncate to stay within context limits).
            data_str = json.dumps(raw_result, indent=2, default=str)
            if len(data_str) > 15_000:
                data_str = data_str[:15_000] + "\n... [truncated]"

            alert_section = ""
            if alerts:
                alert_section = (
                    "\n\nAlerts triggered:\n"
                    + json.dumps(alerts, indent=2, default=str)
                )

            system_prompt = (
                "You are an expert CloudWatch log analysis engineer for KYC Protect. "
                "Analyze the provided log analysis results and give a clear, actionable summary. "
                "Be concise but thorough. Focus on what matters for an on-call engineer."
            )

            human_prompt = (
                f"Analyze these **{analysis_type}** results from CloudWatch log groups "
                f"{log_groups} over the last **{time_range}**.\n\n"
                f"Raw analysis data:\n```json\n{data_str}\n```"
                f"{alert_section}"
            )

            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=human_prompt),
            ]

            # ----------------------------------------------------------
            # Try structured output first (OpenAI, Anthropic, Bedrock).
            # Fall back gracefully to free-form text for other providers.
            # ----------------------------------------------------------
            provider = llm_config.get('provider', '').lower()
            _structured_providers = {'openai', 'anthropic', 'bedrock', 'azure'}

            structured_analysis: Optional[Dict[str, Any]] = None
            analysis_text: Optional[str] = None
            input_tokens = 0
            output_tokens = 0

            if provider in _structured_providers:
                try:
                    structured_llm = llm.with_structured_output(
                        CloudWatchAnalysisSummary, include_raw=True
                    )
                    raw_result = await asyncio.wait_for(
                        structured_llm.ainvoke(messages),
                        timeout=60.0,
                    )
                    parsed: CloudWatchAnalysisSummary = (
                        raw_result.get('parsed') if isinstance(raw_result, dict) else raw_result
                    )
                    raw_msg = raw_result.get('raw') if isinstance(raw_result, dict) else None
                    usage = getattr(raw_msg, 'usage_metadata', None) or {}
                    input_tokens  += usage.get('input_tokens', 0)
                    output_tokens += usage.get('output_tokens', 0)
                    structured_analysis = parsed.model_dump()
                    # Build human-readable text from structured fields.
                    analysis_text = (
                        f"**{parsed.headline}**\n\n"
                        f"Severity: {parsed.severity.upper()} (confidence: {parsed.confidence})\n\n"
                        f"**Key Findings:**\n"
                        + "\n".join(f"- {f}" for f in parsed.key_findings)
                        + f"\n\n**Root Cause:** {parsed.root_cause_hypothesis}\n\n"
                        f"**Recommended Actions:**\n"
                        + "\n".join(f"{i+1}. {a}" for i, a in enumerate(parsed.recommended_actions))
                        + (
                            f"\n\n**Related Services:** {', '.join(parsed.related_services)}"
                            if parsed.related_services else ""
                        )
                    )
                    logger.info(
                        "CloudWatch structured LLM analysis complete (severity=%s, model=%s, tokens=%d)",
                        parsed.severity, model_name, input_tokens + output_tokens,
                    )
                except Exception as struct_err:
                    logger.warning(
                        "CloudWatch structured output failed, falling back to free-form: %s",
                        struct_err,
                    )

            # Free-form fallback (or primary path for unsupported providers).
            if analysis_text is None:
                free_form_prompt = human_prompt + (
                    "\n\nProvide:\n"
                    "1. A concise summary of findings\n"
                    "2. Key patterns or anomalies identified\n"
                    "3. Severity assessment\n"
                    "4. Recommended next steps\n"
                )
                response = await asyncio.wait_for(
                    llm.ainvoke([
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=free_form_prompt),
                    ]),
                    timeout=60.0,
                )
                usage = getattr(response, 'usage_metadata', None) or {}
                input_tokens  += usage.get('input_tokens', 0)
                output_tokens += usage.get('output_tokens', 0)
                analysis_text = str(response.content) if response.content else None
                if analysis_text:
                    logger.info(
                        "CloudWatch free-form LLM analysis complete (%d chars, model=%s, tokens=%d)",
                        len(analysis_text), model_name, input_tokens + output_tokens,
                    )

            return analysis_text, model_name, structured_analysis, input_tokens, output_tokens

        except Exception as e:
            logger.warning("CloudWatch LLM analysis failed (falling back to static): %s", e)
            return None, None, None, 0, 0

    @staticmethod
    def _build_cloudwatch_summary(result: Dict[str, Any], analysis_type: str, log_groups: list) -> str:
        """Build a human-readable summary from CloudWatch analysis results."""
        if not isinstance(result, dict):
            return f"Analysis complete ({analysis_type})"

        if analysis_type == 'error-patterns':
            patterns = result.get('patterns', {})
            pattern_summaries = []
            for ptype, pdata in patterns.items():
                status = pdata.get('status', 'unknown')
                if status == 'Complete':
                    data_count = len(pdata.get('data', []))
                    pattern_summaries.append(f"{ptype}: {data_count} results")
                else:
                    pattern_summaries.append(f"{ptype}: {status}")
            return f"Pattern analysis: {', '.join(pattern_summaries)} across {len(log_groups)} log group(s)"

        elif analysis_type == 'activity-summary':
            summary = result.get('summary', {})
            total_events = summary.get('total_events', 0)
            successful = summary.get('successful_log_groups', 0)
            return f"Activity summary: {total_events} events across {successful}/{len(log_groups)} log group(s)"

        elif analysis_type == 'anomaly-detection':
            anomaly_summary = result.get('summary', {})
            total = anomaly_summary.get('total_anomalies', 0)
            critical = anomaly_summary.get('critical_severity', 0)
            high = anomaly_summary.get('high_severity', 0)
            medium = anomaly_summary.get('medium_severity', 0)
            low = anomaly_summary.get('low_severity', 0)
            return f"Anomaly detection: {total} anomalies (critical: {critical}, high: {high}, medium: {medium}, low: {low})"

        elif analysis_type == 'correlation':
            summary = result.get('summary', {})
            total_events = summary.get('total_events', 0)
            services = summary.get('services_involved', [])
            return f"Log correlation: {total_events} events across {len(services)} service(s)"

        return f"Analysis complete ({analysis_type})"

    @staticmethod
    def _check_cloudwatch_alerts(result: Dict[str, Any], analysis_type: str, error_threshold: int) -> list:
        """Check analysis results against thresholds and generate alerts."""
        alerts = []

        if analysis_type == 'error-patterns':
            patterns = result.get('patterns', {})
            if not isinstance(patterns, dict):
                patterns = {}
            error_data = patterns.get('error', {})
            if not isinstance(error_data, dict):
                error_data = {}
            for entry in error_data.get('data', []):
                if not isinstance(entry, (list, tuple)):
                    continue
                for field in entry:
                    if not isinstance(field, dict):
                        continue
                    if field.get('field') == 'count()':
                        try:
                            count = float(field.get('value', 0))
                            if count > error_threshold:
                                alerts.append({
                                    "type": "error_threshold_exceeded",
                                    "message": f"Error count {count} exceeds threshold {error_threshold}",
                                    "severity": "high" if count > error_threshold * 2 else "medium"
                                })
                        except (ValueError, TypeError):
                            pass

            # Check unique error patterns — alert when a single pattern
            # exceeds the threshold (indicates a repeated systematic issue).
            for pattern_entry in result.get('unique_patterns', []):
                if not isinstance(pattern_entry, (list, tuple)):
                    continue
                for field in pattern_entry:
                    if not isinstance(field, dict):
                        continue
                    if field.get('field') == 'occurrence_count':
                        try:
                            occ_count = float(field.get('value', 0))
                            if occ_count > error_threshold:
                                # Find the pattern text
                                pattern_text = ""
                                for f2 in pattern_entry:
                                    if isinstance(f2, dict) and f2.get('field') == 'error_pattern':
                                        pattern_text = (f2.get('value') or '')[:120]
                                        break
                                alerts.append({
                                    "type": "repeated_error_pattern",
                                    "message": (
                                        f"Error pattern occurred {int(occ_count)} times "
                                        f"(threshold {error_threshold}): {pattern_text}"
                                    ),
                                    "severity": "high" if occ_count > error_threshold * 3 else "medium",
                                })
                        except (ValueError, TypeError):
                            pass

        elif analysis_type == 'anomaly-detection':
            for anomaly in result.get('anomalies', []):
                if not isinstance(anomaly, dict):
                    continue
                z_info = ""
                if anomaly.get('z_score'):
                    z_info = f", z-score={anomaly['z_score']}"
                deviation_info = ""
                if anomaly.get('deviation_factor'):
                    deviation_info = f" ({anomaly['deviation_factor']}x baseline)"
                alerts.append({
                    "type": "anomaly_detected",
                    "message": (
                        f"Anomaly at {anomaly.get('timestamp')}: "
                        f"{anomaly.get('current_count')} events{deviation_info}{z_info}"
                    ),
                    "severity": anomaly.get('severity', 'medium')
                })

        return alerts

    def get_execution_status(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Get status of an execution."""
        return self.active_executions.get(execution_id)
    
    # ── Dynamic graph execution ───────────────────────────────────────────────

    async def run_workflow_graph(
        self,
        workflow: Dict[str, Any],
        inputs: Optional[Dict[str, Any]] = None,
        registry: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Execute a workflow using the dynamic graph engine.

        Unlike the BFS executor (which routes by node type at runtime),
        WorkflowGraph instantiates nodes from the ``node_registry`` using the
        node ``type`` field, wires successors from the edge list, then runs
        the graph.  This enables arbitrary custom node logic without changing
        the executor.

        Args:
            workflow: ReactFlow workflow dict (same schema as ``execute_workflow``).
            inputs:   Optional initial shared state injected before the first node.
            registry: NodeRegistry to use.  Defaults to ``default_registry``
                      from ``app.engine``.

        Returns:
            The shared state dict after the graph terminates.
        """
        from app.engine.crawler_engine import WorkflowGraph, default_registry

        reg = registry or default_registry
        shared: Dict[str, Any] = {"inputs": inputs or {}, "workflow": workflow}

        try:
            flow = WorkflowGraph.from_workflow_json(workflow, reg)
        except (ValueError, KeyError) as exc:
            logger.warning(
                "run_workflow_graph: could not build WorkflowGraph: %s — "
                "falling back to BFS executor",
                exc,
            )
            return await self.execute_workflow(workflow, inputs)

        workflow_name = workflow.get("name", "workflow")
        logger.info("WorkflowGraph: running workflow '%s'", workflow_name)
        result = await flow.run(shared)
        logger.info("WorkflowGraph: workflow '%s' complete", workflow_name)
        return result

    async def cleanup_execution(self, execution_id: str):
        """Clean up execution data and disconnect MCP clients."""
        if execution_id in self.mcp_managers:
            mcp_manager = self.mcp_managers.pop(execution_id)
            try:
                await mcp_manager.disconnect_all()
            except asyncio.CancelledError:
                logger.warning(f"MCP cleanup cancelled for {execution_id}")
            except Exception as e:
                logger.warning(f"Error during MCP cleanup of {execution_id}: {e}")
        
        self.active_executions.pop(execution_id, None)
        self.event_queues.pop(execution_id, None)


# Global executor instance
visual_executor = VisualWorkflowExecutor()
