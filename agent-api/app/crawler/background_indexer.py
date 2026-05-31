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

    workflow_repo = WorkflowRepository()
    errors: List[str] = []
    sem = asyncio.Semaphore(_INDEX_CONCURRENCY)

    async def _index_one(repo_name: str) -> None:
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

    await asyncio.gather(*[_index_one(repo) for repo in repos])

    final_status: str | None = f"indexing_failed: {errors}" if errors else None

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
