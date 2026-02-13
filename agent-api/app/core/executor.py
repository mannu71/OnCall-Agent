"""Task executor for running workflow tasks."""
import asyncio
import subprocess
import sys
import io
import traceback
from datetime import datetime
from typing import Optional

from app.models.workflow import Task, TaskResult, TaskStatus, TaskType
from app.workflow.engine import WorkflowEngine
from app.repositories import WorkflowRepository


class TaskExecutor:
    """Executes individual tasks."""

    async def execute_task(self, task: Task) -> TaskResult:
        """Execute a single task and return the result."""
        start_time = datetime.utcnow()
        
        try:
            if task.type == TaskType.SHELL:
                output, error = await self._execute_shell(task)
            elif task.type == TaskType.PYTHON:
                output, error = await self._execute_python(task)
            elif task.type == "workflow":  # New workflow task type
                output, error = await self._execute_workflow(task)
            else:
                raise ValueError(f"Unknown task type: {task.type}")
            
            end_time = datetime.utcnow()
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
            end_time = datetime.utcnow()
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
        """Execute a Python script."""
        try:
            # Capture stdout and stderr
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            
            stdout_buffer = io.StringIO()
            stderr_buffer = io.StringIO()
            
            sys.stdout = stdout_buffer
            sys.stderr = stderr_buffer
            
            try:
                # Execute the script with timeout
                async def run_script():
                    exec(task.script, {'__builtins__': __builtins__})
                
                await asyncio.wait_for(run_script(), timeout=task.timeout)
                
                # Get output
                stdout_str = stdout_buffer.getvalue()
                stderr_str = stderr_buffer.getvalue()
                
                # Check for errors
                if stderr_str:
                    return stdout_str or None, stderr_str
                
                return stdout_str or None, None
            
            except asyncio.TimeoutError:
                return None, f"Task timed out after {task.timeout} seconds"
            
            except Exception as e:
                error_trace = traceback.format_exc()
                return None, error_trace
            
            finally:
                # Restore stdout and stderr
                sys.stdout = old_stdout
                sys.stderr = old_stderr
        
        except Exception as e:
            return None, f"Python execution error: {str(e)}"
    
    async def _execute_workflow(self, task: Task) -> tuple[Optional[str], Optional[str]]:
        """Execute a workflow using WorkflowEngine."""
        try:
            # Get workflow name from task
            workflow_name = getattr(task, 'workflow_name', None)
            workflow_file = getattr(task, 'workflow_file', None)
            
            if not workflow_name and not workflow_file:
                return None, "Workflow task must specify workflow_name or workflow_file"
            
            # Extract workflow name from file if provided
            if workflow_file and not workflow_name:
                from pathlib import Path
                workflow_name = Path(workflow_file).stem
            
            # Load workflow definition
            workflow_def = workflow_storage.load_workflow(workflow_name)
            
            # Prepare execution context
            context = {
                "user_query": getattr(task, 'user_query', None),
                "variables": getattr(task, 'variables', {}),
                "timeout": task.timeout
            }
            
            # Execute workflow
            engine = WorkflowEngine()
            result = await engine.execute(workflow_def, context)
            
            # Cleanup
            await engine.cleanup()
            
            # Format output
            import json
            output_str = json.dumps(result, indent=2)
            
            if result.get("success"):
                return output_str, None
            else:
                return output_str, result.get("error", "Workflow execution failed")
        
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
