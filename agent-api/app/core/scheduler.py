"""APScheduler integration for workflow scheduling."""
import asyncio
import uuid
import logging
from datetime import datetime
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
        self.reload_workflows()
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

    def reload_workflows(self):
        """Load all workflows from storage and schedule them."""
        logger.info("Loading workflows from storage...")
        
        # Remove all existing jobs
        self.scheduler.remove_all_jobs()
        
        # Load and schedule workflows
        workflows = workflow_storage.list_workflows()
        
        # Schedule workflows with cron expressions
        enabled_count = 0
        for workflow in workflows:
            # Handle dict-based workflows
            if isinstance(workflow, dict):
                if workflow.get('enabled') and workflow.get('schedule'):
                    # Convert to Workflow object for scheduling
                    try:
                        from app.models.workflow import Workflow
                        wf_obj = Workflow(**workflow)
                        self.schedule_workflow(wf_obj)
                        enabled_count += 1
                    except Exception as e:
                        logger.error(f"Failed to schedule workflow {workflow.get('name')}: {e}")
            else:
                # Legacy Workflow object
                if workflow.enabled and workflow.schedule:
                    self.schedule_workflow(workflow)
                    enabled_count += 1
        
        logger.info(f"Loaded {len(workflows)} workflows ({enabled_count} enabled for scheduling)")

    def schedule_workflow(self, workflow: Workflow):
        """Schedule a workflow using its cron expression."""
        if not workflow.enabled:
            logger.info(f"Skipping disabled workflow: {workflow.name}")
            return
        
        try:
            # Parse cron expression
            trigger = CronTrigger.from_crontab(workflow.schedule, timezone=pytz.timezone(settings.scheduler_timezone))
            
            # Schedule the job
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
        except Exception as e:
            logger.warning(f"Failed to unschedule workflow '{workflow_name}': {e}")

    async def _execute_workflow_wrapper(self, workflow_name: str):
        """Wrapper for workflow execution to handle async."""
        try:
            await self.execute_workflow(workflow_name)
        except Exception as e:
            logger.error(f"Error executing workflow '{workflow_name}': {e}", exc_info=True)

    async def execute_workflow(self, workflow_name: str, manual: bool = False) -> Optional[WorkflowExecution]:
        """Execute a workflow and return the execution record."""
        # Load workflow
        workflow = workflow_storage.load_workflow(workflow_name)
        if not workflow:
            logger.error(f"Workflow '{workflow_name}' not found")
            return None
        
        # Create execution record
        execution_id = str(uuid.uuid4())
        execution = WorkflowExecution(
            workflow_name=workflow_name,
            execution_id=execution_id,
            status=WorkflowStatus.RUNNING,
            start_time=datetime.utcnow(),
            task_results=[]
        )
        
        # Store in active executions
        self.active_executions[execution_id] = execution
        
        # Emit workflow start event
        await self._emit_event(workflow_name, WorkflowExecutionEvent(
            event_type="workflow_start",
            workflow_name=workflow_name,
            execution_id=execution_id,
            timestamp=datetime.utcnow(),
            data={"manual": manual}
        ))
        
        logger.info(f"Starting execution {execution_id} for workflow '{workflow_name}'")
        
        try:
            # Execute tasks sequentially
            for task in workflow.tasks:
                # Emit task start event
                await self._emit_event(workflow_name, WorkflowExecutionEvent(
                    event_type="task_start",
                    workflow_name=workflow_name,
                    execution_id=execution_id,
                    timestamp=datetime.utcnow(),
                    data={"task_name": task.name}
                ))
                
                # Execute task with retry
                result = await task_executor.execute_task_with_retry(task)
                execution.task_results.append(result)
                
                # Emit task complete event
                await self._emit_event(workflow_name, WorkflowExecutionEvent(
                    event_type="task_complete",
                    workflow_name=workflow_name,
                    execution_id=execution_id,
                    timestamp=datetime.utcnow(),
                    data={
                        "task_name": task.name,
                        "status": result.status,
                        "duration_seconds": result.duration_seconds
                    }
                ))
                
                # Stop on failure if no retries
                if result.status == TaskStatus.FAILED:
                    logger.warning(f"Task '{task.name}' failed in execution {execution_id}")
                    break
            
            # Determine final status
            all_success = all(r.status == TaskStatus.SUCCESS for r in execution.task_results)
            any_failed = any(r.status == TaskStatus.FAILED for r in execution.task_results)
            
            if all_success:
                execution.status = WorkflowStatus.SUCCESS
            elif any_failed:
                execution.status = WorkflowStatus.FAILED
            else:
                execution.status = WorkflowStatus.PARTIAL
        
        except Exception as e:
            logger.error(f"Workflow execution failed: {e}", exc_info=True)
            execution.status = WorkflowStatus.FAILED
            execution.error = str(e)
        
        finally:
            # Update execution record
            execution.end_time = datetime.utcnow()
            execution.duration_seconds = (execution.end_time - execution.start_time).total_seconds()
            
            # Save to storage
            workflow_storage.save_execution_log(execution)
            
            # Remove from active executions
            self.active_executions.pop(execution_id, None)
            
            # Emit workflow complete event
            await self._emit_event(workflow_name, WorkflowExecutionEvent(
                event_type="workflow_complete",
                workflow_name=workflow_name,
                execution_id=execution_id,
                timestamp=datetime.utcnow(),
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
        
        # Send to all queues
        dead_queues = set()
        for queue in self.event_queues[workflow_name]:
            try:
                await queue.put(event)
            except Exception:
                dead_queues.add(queue)
        
        # Clean up dead queues
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
        """Clear in-memory data.
        
        Args:
            clear_jobs: If True, also remove all scheduled jobs from APScheduler
        
        Returns:
            Dictionary with counts of cleared items
        """
        # Count before clearing
        active_count = len(self.active_executions)
        jobs_count = len(self.scheduler.get_jobs())
        event_queues_count = sum(len(queues) for queues in self.event_queues.values())
        
        # Clear active executions
        self.active_executions.clear()
        logger.info(f"Cleared {active_count} active executions")
        
        # Clear event queues
        self.event_queues.clear()
        logger.info(f"Cleared {event_queues_count} event queue subscriptions")
        
        # Optionally clear scheduled jobs
        if clear_jobs:
            self.scheduler.remove_all_jobs()
            logger.info(f"Cleared {jobs_count} scheduled jobs")
        
        return {
            "active_executions_cleared": active_count,
            "event_queues_cleared": event_queues_count,
            "scheduled_jobs_cleared": jobs_count if clear_jobs else 0
        }


# Global scheduler instance
workflow_scheduler = WorkflowScheduler()
