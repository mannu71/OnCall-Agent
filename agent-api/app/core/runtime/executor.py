"""Task executor for running workflow tasks.

Legacy shell/python tasks run here.  Python scripts are executed in an isolated
subprocess — never via in-process ``exec()`` — so scheduled YAML tasks cannot
mutate the API process.  Visual node-based workflows route through
:mod:`app.workflow.routing` instead.
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from typing import Optional

from app.models.workflow import Task, TaskResult, TaskStatus, TaskType, WorkflowStatus
from app.infrastructure.persistence import WorkflowRepository


class TaskExecutor:
    """Executes individual tasks."""

    async def execute_task(self, task: Task) -> TaskResult:
        """Execute a single task and return the result."""
        start_time = datetime.now(timezone.utc)
        
        try:
            if task.type == TaskType.SHELL:
                output, error = await self._execute_shell(task)
            elif task.type == TaskType.PYTHON:
                output, error = await self._execute_python(task)
            elif task.type == "workflow":  # New workflow task type
                output, error = await self._execute_workflow(task)
            else:
                raise ValueError(f"Unknown task type: {task.type}")
            
            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds()
            
            # Determine status based on error
            status = TaskStatus.SUCCESS if not error else TaskStatus.FAILED
            
            return TaskResult(
                task_name=task.name,
                status=status,
                output=output,
                error=error,
                start_time=start_time,
                end_time=end_time,
                duration_seconds=duration
            )
        
        except Exception as e:
            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds()
            
            return TaskResult(
                task_name=task.name,
                status=TaskStatus.FAILED,
                output=None,
                error=str(e),
                start_time=start_time,
                end_time=end_time,
                duration_seconds=duration
            )

    async def _execute_shell(self, task: Task) -> tuple[Optional[str], Optional[str]]:
        """Execute a shell command."""
        try:
            # Run the command with timeout
            process = await asyncio.create_subprocess_shell(
                task.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                shell=True
            )
            
            # Wait for completion with timeout
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=task.timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return None, f"Task timed out after {task.timeout} seconds"
            
            stdout_str = stdout.decode('utf-8') if stdout else None
            stderr_str = stderr.decode('utf-8') if stderr else None
            
            # Check return code
            if process.returncode != 0:
                return stdout_str, stderr_str or f"Command exited with code {process.returncode}"
            
            return stdout_str, None
        
        except Exception as e:
            return None, f"Shell execution error: {str(e)}"

    async def _execute_python(self, task: Task) -> tuple[Optional[str], Optional[str]]:
        """Execute a Python script in an isolated subprocess."""
        script_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                delete=False,
                encoding="utf-8",
            ) as handle:
                handle.write(task.script or "")
                script_path = handle.name

            process = await asyncio.create_subprocess_exec(
                sys.executable,
                script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=task.timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return None, f"Task timed out after {task.timeout} seconds"

            stdout_str = stdout.decode("utf-8", errors="replace") if stdout else None
            stderr_str = stderr.decode("utf-8", errors="replace") if stderr else None

            if process.returncode != 0:
                return stdout_str, stderr_str or f"Script exited with code {process.returncode}"

            if stderr_str:
                return stdout_str or None, stderr_str

            return stdout_str or None, None

        except Exception as e:
            return None, f"Python execution error: {str(e)}"

        finally:
            if script_path and os.path.exists(script_path):
                try:
                    os.unlink(script_path)
                except OSError:
                    pass
    
    async def _execute_workflow(self, task: Task) -> tuple[Optional[str], Optional[str]]:
        """Execute a nested workflow reference via the canonical routing layer."""
        try:
            workflow_name = getattr(task, 'workflow_name', None)
            workflow_file = getattr(task, 'workflow_file', None)

            if not workflow_name and not workflow_file:
                return None, "Workflow task must specify workflow_name or workflow_file"

            if workflow_file and not workflow_name:
                from pathlib import Path
                workflow_name = Path(workflow_file).stem

            workflow_repo = WorkflowRepository()
            workflow_def = await workflow_repo.get_by_name(workflow_name)

            if not workflow_def:
                return None, f"Workflow '{workflow_name}' not found"

            from app.workflow.routing import execute_workflow, is_visual_workflow

            context = {
                "user_query": getattr(task, 'user_query', None),
                "variables": getattr(task, 'variables', {}),
                "timeout": task.timeout,
            }

            if is_visual_workflow(workflow_def):
                result = await execute_workflow(
                    workflow_def,
                    inputs=context.get("variables"),
                )
            else:
                result = await execute_workflow(workflow_def, manual=True)

            import json
            if isinstance(result, dict):
                output_str = json.dumps(result, indent=2)
                failed = result.get("status") in ("failed", "error", "skipped")
                if not failed and result.get("success", True):
                    return output_str, None
                return output_str, result.get("error") or result.get("message", "Workflow execution failed")

            output_str = json.dumps(result.model_dump(mode="json"), indent=2, default=str)
            if result.status == WorkflowStatus.SUCCESS:
                return output_str, None
            return output_str, result.error or "Workflow execution failed"

        except Exception as e:
            return None, f"Workflow execution error: {str(e)}"

    async def execute_task_with_retry(self, task: Task) -> TaskResult:
        """Execute a task with retry logic."""
        result = None
        
        for attempt in range(task.retry_count + 1):
            result = await self.execute_task(task)
            
            if result.status == TaskStatus.SUCCESS:
                return result
            
            # If failed and retries remaining, wait and retry
            if attempt < task.retry_count:
                await asyncio.sleep(task.retry_delay)
        
        return result


# Global executor instance
task_executor = TaskExecutor()
