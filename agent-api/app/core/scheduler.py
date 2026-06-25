"""APScheduler integration for workflow scheduling."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Set, List
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import pytz

from app.models.workflow import (
    Workflow,
    WorkflowExecution,
    WorkflowStatus,
    TaskResult,
    TaskStatus,
    WorkflowExecutionEvent
)
from app.infrastructure.persistence import WorkflowRepository
from app.core.executor import task_executor
from app.workflow.routing import execute_visual_workflow, is_visual_workflow
from app.infrastructure.persistence import ExecutionRepository
from app.config import settings
from app.core.app_timezone import get_global_timezone_name

logger = logging.getLogger(__name__)


class WorkflowScheduler:
    """Manages workflow scheduling and execution."""

    def __init__(self, workflow_repo: Optional[WorkflowRepository] = None):
        """Initialize the scheduler.
        
        Args:
            workflow_repo: Workflow repository instance
        """
        self.scheduler = AsyncIOScheduler(timezone=get_global_timezone_name())
        self.workflow_repo = workflow_repo or WorkflowRepository()
        self.active_executions: Dict[str, WorkflowExecution] = {}
        self.event_queues: Dict[str, Set[asyncio.Queue]] = {}
        self._running = False
        # Multi-replica leader election: only the replica holding the advisory
        # lock fires *scheduled* runs (manual API runs are unaffected). Single
        # node always wins the lock, so behaviour is unchanged there.
        self._leader_lock = None
        self._is_leader = False

    async def _ensure_leader(self) -> bool:
        """Return True if this replica is the scheduling leader.

        Lazily (re)acquires a Postgres advisory lock; a follower can become
        leader on a later fire after the previous leader's connection drops.
        """
        if self._is_leader:
            return True
        try:
            from app.core.distributed_lock import LeaderLock
            if self._leader_lock is None:
                self._leader_lock = LeaderLock("workflow_scheduler")
            self._is_leader = await self._leader_lock.acquire()
        except Exception as exc:  # noqa: BLE001 — fail-open for single node
            logger.warning("scheduler: leader check failed (%s) — assuming leader", exc)
            self._is_leader = True
        return self._is_leader

    def start(self):
        """Start the scheduler and load workflows."""
        if self._running:
            logger.warning("Scheduler is already running")
            return
        
        logger.info("Starting workflow scheduler...")
        self.scheduler.start()
        self._running = True
        
        # Load all workflows
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.reload_workflows())
        except RuntimeError:
            asyncio.run(self.reload_workflows())

        # Periodic self-improvement curator: promotes verified skills, pins
        # frequently-recalled memories, and (when enabled) consolidates memory.
        # Leader-locked like cron fires so a multi-replica deployment runs it
        # once. Interval is operator-tunable; cheap promotions run every cycle.
        try:
            _hours = max(1, int(getattr(settings, "curator_interval_hours", 6)))
            self.scheduler.add_job(
                self._run_curator_wrapper,
                IntervalTrigger(hours=_hours),
                id="self_improvement_curator",
                replace_existing=True,
            )
            logger.info("Scheduled self-improvement curator every %dh", _hours)
        except Exception as exc:  # noqa: BLE001 — curator is best-effort
            logger.warning("scheduler: curator scheduling skipped (%s)", exc)

        logger.info("Workflow scheduler started")

    async def _run_curator_wrapper(self):
        """Leader-gated periodic run of the self-improvement curator."""
        if not await self._ensure_leader():
            logger.debug("scheduler: not leader — skipping curator run")
            return
        try:
            from app.core.memory.curator import run_curator
            result = await run_curator()
            logger.info("scheduler: curator run complete — %s", result)
        except Exception as exc:  # noqa: BLE001 — never let the curator sink the loop
            logger.warning("scheduler: curator run failed (%s)", exc)

    def stop(self):
        """Stop the scheduler."""
        if not self._running:
            return
        
        logger.info("Stopping workflow scheduler...")
        self.scheduler.shutdown(wait=True)
        self._running = False
        logger.info("Workflow scheduler stopped")

    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._running

    async def reload_workflows(self):
        """Load all workflows from storage and schedule them."""
        logger.info("Loading workflows from storage...")

        workflows = await self.workflow_repo.list_all()
        desired_ids: set[str] = set()
        enabled_count = 0

        for workflow_data in workflows:
            try:
                if isinstance(workflow_data, dict):
                    workflow = Workflow(**workflow_data)
                else:
                    workflow = workflow_data

                if workflow.enabled and workflow.schedule:
                    self.schedule_workflow(workflow)
                    desired_ids.add(workflow.name)
                    enabled_count += 1
            except Exception as e:
                name = workflow_data.get('name') if isinstance(workflow_data, dict) else getattr(workflow_data, 'name', 'unknown')
                logger.error(f"Failed to process workflow '{name}': {e}")

        for job in self.scheduler.get_jobs():
            if job.id not in desired_ids:
                try:
                    self.scheduler.remove_job(job.id)
                except Exception as exc:
                    logger.debug("Could not remove stale job %s: %s", job.id, exc)

        logger.info(f"Loaded {len(workflows)} workflows ({enabled_count} enabled for scheduling)")

    def schedule_workflow(self, workflow: Workflow):
        """Schedule a workflow using its cron expression."""
        if not workflow.enabled or not workflow.schedule:
            return
        
        try:
            # workflow.schedule is already a UTC cron (the local HH:MM is
            # converted to UTC at save time in _params_to_cron using the global
            # timezone), so the trigger must be interpreted in UTC. Applying a
            # non-UTC tz here would double-apply the offset.
            trigger = CronTrigger.from_crontab(
                workflow.schedule,
                timezone=pytz.utc,
            )
            
            self.scheduler.add_job(
                self._execute_workflow_wrapper,
                trigger=trigger,
                args=[workflow.name],
                id=workflow.name,
                replace_existing=True,
                misfire_grace_time=60
            )
            
            logger.info(f"Scheduled workflow '{workflow.name}' with cron: {workflow.schedule}")
        except Exception as e:
            logger.error(f"Failed to schedule workflow '{workflow.name}': {e}")

    def unschedule_workflow(self, workflow_name: str):
        """Remove a workflow from the schedule."""
        try:
            self.scheduler.remove_job(workflow_name)
            logger.info(f"Unscheduled workflow: {workflow_name}")
        except Exception:
            logger.debug(f"Workflow '{workflow_name}' was not scheduled")

    async def _execute_workflow_wrapper(self, workflow_name: str):
        """Wrapper for a *scheduled* workflow fire (cron-triggered)."""
        # Only the leader replica fires scheduled runs, so a multi-replica
        # deployment doesn't double-execute the same cron. Manual API runs go
        # through execute_workflow() directly and are never gated.
        if not await self._ensure_leader():
            logger.debug(
                "scheduler: not leader — skipping scheduled run of '%s'", workflow_name
            )
            return
        try:
            await self.execute_workflow(workflow_name)
        except Exception as e:
            logger.error(f"Error in background execution of '{workflow_name}': {e}", exc_info=True)

    async def execute_workflow(self, workflow_name: str, manual: bool = False) -> Optional[WorkflowExecution]:
        """Execute a workflow and return the execution record."""
        workflow_data = await self.workflow_repo.get_by_name(workflow_name)
        if not workflow_data:
            logger.error(f"Workflow '{workflow_name}' not found")
            return None
        
        # Visual workflows (node-based) — canonical path via routing layer
        if is_visual_workflow(workflow_data):
            await execute_visual_workflow(workflow_data)
            return None

        # Legacy workflows (task-based)
        workflow = Workflow(**workflow_data)
        execution_repo = ExecutionRepository()
        workflow_id: Optional[int] = None
        if workflow.id is not None:
            try:
                workflow_id = int(workflow.id)
            except (TypeError, ValueError):
                workflow_id = None
        running_record = await execution_repo.create_running(
            workflow_name,
            workflow_id=workflow_id,
        )
        execution_id = str(running_record["execution_id"])
        execution = WorkflowExecution(
            workflow_name=workflow_name,
            execution_id=execution_id,
            status=WorkflowStatus.RUNNING,
            start_time=datetime.now(timezone.utc),
            task_results=[]
        )

        # In-process tracking for scheduler SSE subscribers only.
        self.active_executions[execution_id] = execution
        
        await self._emit_event(workflow_name, WorkflowExecutionEvent(
            event_type="workflow_start",
            workflow_name=workflow_name,
            execution_id=execution_id,
            timestamp=datetime.now(timezone.utc),
            data={"manual": manual}
        ))
        
        logger.info(f"Starting execution {execution_id} for legacy workflow '{workflow_name}'")
        
        try:
            for task_data in (workflow.tasks or []):
                # Ensure task is a model if needed, but executor usually handles it
                # For now assuming legacy scripts work as before
                from app.models.workflow import Task
                task = Task(**task_data) if isinstance(task_data, dict) else task_data
                
                await self._emit_event(workflow_name, WorkflowExecutionEvent(
                    event_type="task_start",
                    workflow_name=workflow_name,
                    execution_id=execution_id,
                    timestamp=datetime.now(timezone.utc),
                    data={"task_name": task.name}
                ))
                
                result = await task_executor.execute_task_with_retry(task)
                execution.task_results.append(result)
                
                await self._emit_event(workflow_name, WorkflowExecutionEvent(
                    event_type="task_complete",
                    workflow_name=workflow_name,
                    execution_id=execution_id,
                    timestamp=datetime.now(timezone.utc),
                    data={
                        "task_name": task.name,
                        "status": result.status,
                        "duration_seconds": result.duration_seconds
                    }
                ))
                
                if result.status == TaskStatus.FAILED:
                    break
            
            # Determine final status
            results = execution.task_results
            if not results:
                execution.status = WorkflowStatus.SUCCESS
            elif all(r.status == TaskStatus.SUCCESS for r in results):
                execution.status = WorkflowStatus.SUCCESS
            elif any(r.status == TaskStatus.FAILED for r in results):
                execution.status = WorkflowStatus.FAILED
            else:
                execution.status = WorkflowStatus.PARTIAL
        
        except Exception as e:
            logger.error(f"Workflow execution failed: {e}", exc_info=True)
            execution.status = WorkflowStatus.FAILED
            execution.error = str(e)
        
        finally:
            execution.end_time = datetime.now(timezone.utc)
            execution.duration_seconds = (execution.end_time - execution.start_time).total_seconds()
            
            # Persist legacy execution to the executions table.
            status_map = {
                WorkflowStatus.SUCCESS: "success",
                WorkflowStatus.FAILED: "failed",
                WorkflowStatus.PARTIAL: "partial",
                WorkflowStatus.RUNNING: "running",
                WorkflowStatus.PENDING: "pending",
            }
            await execution_repo.save({
                "execution_id": execution_id,
                "workflow_name": workflow_name,
                "workflow_id": workflow.id,
                "status": status_map.get(execution.status, str(execution.status)),
                "start_time": execution.start_time,
                "end_time": execution.end_time,
                "duration": execution.duration_seconds,
                "output": [r.model_dump(mode="json") for r in execution.task_results],
                "error": execution.error,
            })

            self.active_executions.pop(execution_id, None)
            
            await self._emit_event(workflow_name, WorkflowExecutionEvent(
                event_type="workflow_complete",
                workflow_name=workflow_name,
                execution_id=execution_id,
                timestamp=datetime.now(timezone.utc),
                data={
                    "status": execution.status,
                    "duration_seconds": execution.duration_seconds,
                    "total_tasks": len(execution.task_results),
                    "successful_tasks": sum(1 for r in execution.task_results if r.status == TaskStatus.SUCCESS)
                }
            ))
            
            logger.info(f"Completed execution {execution_id} for workflow '{workflow_name}' with status {execution.status}")
        
        return execution

    async def _emit_event(self, workflow_name: str, event: WorkflowExecutionEvent):
        """Emit an event to all subscribed queues."""
        if workflow_name not in self.event_queues:
            return

        dead_queues = set()
        for queue in self.event_queues[workflow_name]:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
            except Exception:
                dead_queues.add(queue)

        if dead_queues:
            self.event_queues[workflow_name] -= dead_queues

    def subscribe_to_events(self, workflow_name: str) -> asyncio.Queue:
        """Subscribe to workflow execution events."""
        queue = asyncio.Queue(maxsize=settings.sse_queue_maxsize)
        if workflow_name not in self.event_queues:
            self.event_queues[workflow_name] = set()
        self.event_queues[workflow_name].add(queue)
        return queue

    def unsubscribe_from_events(self, workflow_name: str, queue: asyncio.Queue):
        """Unsubscribe from workflow execution events."""
        if workflow_name in self.event_queues:
            self.event_queues[workflow_name].discard(queue)

    def get_active_executions(self) -> Dict[str, WorkflowExecution]:
        """Get all active workflow executions."""
        return self.active_executions.copy()

    def get_scheduled_jobs(self) -> list:
        """Get all scheduled jobs."""
        return self.scheduler.get_jobs()

    def clear_data(self, clear_jobs: bool = False) -> dict:
        """Clear in-memory data."""
        active_count = len(self.active_executions)
        jobs_count = len(self.scheduler.get_jobs())
        event_queues_count = sum(len(queues) for queues in self.event_queues.values())
        
        self.active_executions.clear()
        self.event_queues.clear()
        
        if clear_jobs:
            self.scheduler.remove_all_jobs()
        
        return {
            "active_executions_cleared": active_count,
            "event_queues_cleared": event_queues_count,
            "scheduled_jobs_cleared": jobs_count if clear_jobs else 0
        }


# Global scheduler instance
workflow_scheduler = WorkflowScheduler()
