"""Background indexer — fire-and-forget coroutine for index-on-save.

Called by the PUT /workflows/{name} handler whenever a workflow with a
codeAnalyzer node is saved and contains repos that are not yet in
``repo_abstractions``.  The handler disables the workflow and sets
``indexing_status = 'indexing'`` before firing this task; on completion
this coroutine re-enables the workflow and clears the status.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

_INDEX_CONCURRENCY = settings.background_index_concurrency

# Holds references to recovery tasks so the event loop doesn't GC them mid-run.
_recovery_tasks: set = set()


async def index_workflow_repos(
    workflow_name: str,
    repos: List[str],
    model_id: Optional[str] = None,
) -> None:
    """Index repos for a workflow, then re-enable it.

    Repos are indexed in parallel with bounded concurrency
    (``BACKGROUND_INDEX_CONCURRENCY``, default 2).
    """
    from app.services.crawler_service import crawler_service
    from app.infrastructure.persistence.workflow_repository import WorkflowRepository
    from app.services.background_jobs import background_job_store

    workflow_repo = WorkflowRepository()
    errors: List[str] = []
    sem = asyncio.Semaphore(_INDEX_CONCURRENCY)

    # Durable job record for live status / restart recovery (best-effort).
    job_id = await background_job_store.create(
        "repo_index", workflow_name,
        total=len(repos), payload={"repos": repos, "model_id": model_id},
    )
    await background_job_store.mark_running(job_id)
    _done = 0

    async def _index_one(repo_name: str) -> None:
        nonlocal _done
        async with sem:
            logger.info(
                "background_indexer: indexing repo=%s for workflow=%s (model=%s)",
                repo_name,
                workflow_name,
                model_id or "default",
            )
            try:
                result = await crawler_service.index_repo(
                    repo=repo_name, force=False, model_id=model_id
                )
                if isinstance(result, dict) and "error" in result:
                    logger.warning(
                        "background_indexer: repo=%s error: %s",
                        repo_name,
                        result["error"],
                    )
                    errors.append(repo_name)
                else:
                    logger.info("background_indexer: repo=%s indexed OK", repo_name)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "background_indexer: repo=%s unhandled exception", repo_name
                )
                errors.append(repo_name)
            finally:
                _done += 1
                await background_job_store.update_progress(
                    job_id, progress=_done, detail=f"indexed {repo_name}",
                )

    await asyncio.gather(*[_index_one(repo) for repo in repos])

    final_status: str | None = f"indexing_failed: {errors}" if errors else None
    if errors:
        await background_job_store.mark_failed(job_id, error=f"repos failed: {errors}")
    else:
        await background_job_store.mark_completed(job_id, detail="all repos indexed")

    try:
        workflow = await workflow_repo.get_by_name(workflow_name)
        if workflow is not None:
            workflow["enabled"] = True
            workflow["indexing_status"] = final_status
            await workflow_repo.save(workflow)
            logger.info(
                "background_indexer: workflow=%s re-enabled (indexing_status=%s)",
                workflow_name,
                final_status,
            )
        else:
            logger.warning(
                "background_indexer: workflow=%s not found after indexing — cannot re-enable",
                workflow_name,
            )
    except Exception:  # noqa: BLE001
        logger.exception(
            "background_indexer: failed to re-enable workflow=%s after indexing",
            workflow_name,
        )


async def index_workflow_repos_codegraph(
    workflow_name: str,
    repos: List[str],
) -> None:
    """Index repos into the codegraph engine, then re-enable the workflow.

    Mirrors :func:`index_workflow_repos` but targets the native codegraph engine
    instead of the crawler. codegraph is driven in-process over stdio (no
    ``mcp_servers`` row): we spawn ``codegraph serve`` once via a short-lived
    ``MCPClientManager`` and call its ``index_repository`` tool per repo
    (incremental — codegraph dedupes unchanged files), then disconnect.

    Repo paths resolve under ``REPOS_BASE_PATH`` (the same base the crawler uses).
    """
    import json
    import os

    from app.config import settings
    from app.infrastructure.persistence.workflow_repository import WorkflowRepository
    from app.services.background_jobs import background_job_store
    from app.services.mcp_client_manager import MCPClientManager
    from app.workflow.tools.codegraph_tools import (
        CODEGRAPH_SERVER_ID,
        codegraph_inline_config,
    )

    workflow_repo = WorkflowRepository()
    errors: List[str] = []

    job_id = await background_job_store.create(
        "repo_index_codegraph", workflow_name,
        total=len(repos), payload={"repos": repos, "backend": "codegraph"},
    )
    await background_job_store.mark_running(job_id)

    manager = MCPClientManager()
    connected = False
    try:
        connected = await manager.connect_server(
            CODEGRAPH_SERVER_ID, codegraph_inline_config()
        )
        if not connected:
            err = getattr(manager, "last_errors", {}).get(CODEGRAPH_SERVER_ID, "")
            logger.warning(
                "background_indexer[codegraph]: failed to start engine (bin=%s): %s",
                settings.codegraph_bin, err,
            )
            errors = list(repos)
        else:
            _done = 0
            for repo_name in repos:
                repo_path = os.path.join(settings.repos_base_path, repo_name)
                logger.info(
                    "background_indexer[codegraph]: indexing repo=%s path=%s",
                    repo_name, repo_path,
                )
                try:
                    result = await manager.execute_tool(
                        server_id=CODEGRAPH_SERVER_ID,
                        tool_name="index_repository",
                        # mode=fast skips build artifacts / test fixtures
                        # (bin/, build/, tests fixtures, generated) and the
                        # expensive similarity/semantic-edge passes — the
                        # difference between sub-second and many-minutes on large
                        # repos (e.g. a 9 GB checkout whose tests/ alone is ~5 GB).
                        arguments={"repo_path": repo_path, "mode": "fast"},
                        tool_timeout=0,  # indexing can be long; no per-call cap
                    )
                    if result.get("isError"):
                        logger.warning(
                            "background_indexer[codegraph]: repo=%s error: %s",
                            repo_name, _content_text(result),
                        )
                        errors.append(repo_name)
                    else:
                        logger.info(
                            "background_indexer[codegraph]: repo=%s indexed OK", repo_name
                        )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "background_indexer[codegraph]: repo=%s unhandled exception",
                        repo_name,
                    )
                    errors.append(repo_name)
                finally:
                    _done += 1
                    await background_job_store.update_progress(
                        job_id, progress=_done, detail=f"indexed {repo_name}",
                    )
    finally:
        if connected:
            try:
                await manager.disconnect_all()
            except Exception:  # noqa: BLE001
                logger.debug("background_indexer[codegraph]: disconnect failed", exc_info=True)

    final_status: str | None = f"indexing_failed: {errors}" if errors else None
    if errors:
        await background_job_store.mark_failed(job_id, error=f"repos failed: {errors}")
    else:
        await background_job_store.mark_completed(job_id, detail="all repos indexed")

    try:
        workflow = await workflow_repo.get_by_name(workflow_name)
        if workflow is not None:
            workflow["enabled"] = True
            workflow["indexing_status"] = final_status
            await workflow_repo.save(workflow)
            logger.info(
                "background_indexer[codegraph]: workflow=%s re-enabled (indexing_status=%s)",
                workflow_name, final_status,
            )
        else:
            logger.warning(
                "background_indexer[codegraph]: workflow=%s not found after indexing",
                workflow_name,
            )
    except Exception:  # noqa: BLE001
        logger.exception(
            "background_indexer[codegraph]: failed to re-enable workflow=%s",
            workflow_name,
        )


def _content_text(result: dict) -> str:
    """Best-effort flatten of an MCP tool result's content to a short string."""
    content = result.get("content")
    if isinstance(content, list):
        parts = []
        for item in content:
            if hasattr(item, "text"):
                parts.append(item.text)
            elif isinstance(item, dict):
                parts.append(item.get("text", ""))
            else:
                parts.append(str(item))
        return " ".join(p for p in parts if p)[:500]
    return str(content or result.get("error") or "")[:500]


async def recover_interrupted_indexing() -> None:
    """Re-fire indexing for workflows left mid-index after a process restart.

    ``index_workflow_repos`` is a fire-and-forget in-process task: it sets
    ``indexing_status='indexing'`` up front and only clears it on completion.
    If the process dies in between (restart / crash / OOM) the workflow stays
    disabled and stuck on 'indexing' forever, since the task that would clear
    it is gone. Called once on startup, this finds those workflows and re-runs
    the indexer so they self-heal (or clears the flag when there's nothing to
    index).
    """
    from app.infrastructure.persistence.workflow_repository import WorkflowRepository
    from app.workflow.code_analyzer_config import (
        CODE_ANALYZER_NODE_TYPES,
        read_code_analyzer_repos,
    )

    repo = WorkflowRepository()
    try:
        workflows = await repo.list_all()
    except Exception:  # noqa: BLE001
        logger.exception("recover_interrupted_indexing: failed to list workflows")
        return

    stuck = [w for w in workflows if w.get("indexing_status") == "indexing"]
    if not stuck:
        return

    logger.info(
        "recover_interrupted_indexing: %d workflow(s) stuck mid-index, recovering: %s",
        len(stuck), [w.get("name") for w in stuck],
    )

    for wf in stuck:
        repo_names: List[str] = []
        for node in wf.get("nodes") or []:
            if node.get("type") in CODE_ANALYZER_NODE_TYPES:
                repo_names.extend(
                    r["name"] for r in read_code_analyzer_repos(node) if r["name"]
                )
        repo_names = list(dict.fromkeys(repo_names))  # dedupe, keep order

        if not repo_names:
            # Nothing to index — clear the stuck flag and re-enable so the card
            # doesn't show "Indexing" forever.
            wf["enabled"] = True
            wf["indexing_status"] = None
            try:
                await repo.save(wf)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "recover_interrupted_indexing: failed to clear status for %s",
                    wf.get("name"),
                )
            continue

        task = asyncio.create_task(
            index_workflow_repos(wf["name"], repo_names, model_id=None)
        )
        _recovery_tasks.add(task)
        task.add_done_callback(_recovery_tasks.discard)
