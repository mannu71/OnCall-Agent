"""APScheduler integration for workflow scheduling."""
import asyncio
import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Set
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from app.models.workflow import (
    Workflow,
    WorkflowExecution,
    WorkflowStatus,
    TaskResult,
    TaskStatus,
    WorkflowExecutionEvent
)
from app.repositories import WorkflowRepository
from app.core.executor import task_executor
from app.services.visual_workflow_executor import visual_executor
from app.config import settings

logger = logging.getLogger(__name__)


class WorkflowScheduler:
    """Manages workflow scheduling and execution."""

    def __init__(self, workflow_repo: Optional[WorkflowRepository] = None):
        """Initialize the scheduler.
        
        Args:
            workflow_repo: Workflow repository instance
        """
        self.scheduler = AsyncIOScheduler(timezone=settings.scheduler_timezone)
        self.workflow_repo = workflow_repo or WorkflowRepository()
        self.active_executions: Dict[str, WorkflowExecution] = {}
        self.event_queues: Dict[str, Set[asyncio.Queue]] = {}
        self._running = False

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
            
        logger.info("Workflow scheduler started")

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
        
        # Remove all existing jobs
        self.scheduler.remove_all_jobs()
        
        # Load and schedule workflows
        workflows = await self.workflow_repo.list_all()
        enabled_count = 0
        
        for workflow_data in workflows:
            try:
                # Ensure we have a Workflow object
                if isinstance(workflow_data, dict):
                    workflow = Workflow(**workflow_data)
                else:
                    workflow = workflow_data
                
                if workflow.enabled and workflow.schedule:
                    self.schedule_workflow(workflow)
                    enabled_count += 1
            except Exception as e:
                name = workflow_data.get('name') if isinstance(workflow_data, dict) else getattr(workflow_data, 'name', 'unknown')
                logger.error(f"Failed to process workflow '{name}': {e}")
        
        logger.info(f"Loaded {len(workflows)} workflows ({enabled_count} enabled for scheduling)")

    def schedule_workflow(self, workflow: Workflow):
        """Schedule a workflow using its cron expression."""
        if not workflow.enabled or not workflow.schedule:
            return
        
        try:
            trigger = CronTrigger.from_crontab(
                workflow.schedule, 
                timezone=pytz.timezone(settings.scheduler_timezone)
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
        """Wrapper for workflow execution to handle async."""
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
        
        # Visual Workflows (Node-based)
        if workflow_data.get('nodes'):
            logger.info(f"Delegating visual workflow '{workflow_name}' to visual_executor")
            result = await visual_executor.execute_workflow(workflow_data)
            # visual_executor handles its own persistence and events
            return None # Or convert result to WorkflowExecution if needed
            
        # Legacy Workflows (Task-based)
        workflow = Workflow(**workflow_data)
        execution_id = str(uuid.uuid4())
        execution = WorkflowExecution(
            workflow_name=workflow_name,
            execution_id=execution_id,
            status=WorkflowStatus.RUNNING,
            start_time=datetime.now(timezone.utc),
            task_results=[]
        )
        
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
            
            # Save legacy execution (visual executor has its own storage logic)
            # Re-using workflow_repo.save_execution if it expects WorkflowExecution
            if hasattr(self.workflow_repo, 'save_execution'):
                 await self.workflow_repo.save_execution(execution)
            
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
                await queue.put(event)
            except Exception:
                dead_queues.add(queue)
        
        if dead_queues:
            self.event_queues[workflow_name] -= dead_queues

    def subscribe_to_events(self, workflow_name: str) -> asyncio.Queue:
        """Subscribe to workflow execution events."""
        queue = asyncio.Queue()
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
