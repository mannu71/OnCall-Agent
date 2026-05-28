"""Sync/async graph framework for crawler flows.

Provides small, composable Node/Flow base classes for building sequential
DAG pipelines. Each node implements three phases:

    prep(shared)            — read inputs from shared dict
    exec(prep_res)          — do the work (LLM call, file read, DB query)
    post(shared, prep_res, exec_res) — write results back; return routing action

Nodes are chained with the ``>>`` operator:

    fetch >> extract >> analyze >> persist
    flow = AsyncFlow(start=fetch)
    await flow.run(shared)

MIT-licensed design pattern. Original concept from the-pocket/PocketFlow.
Attribution preserved per MIT license terms. Implementation rewritten for
this project — no production dependency on the upstream package.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_ACTION = "default"


# ─────────────────────────────────────────────────────────────────────────────
# SyncBaseNode — shared wiring logic
# ─────────────────────────────────────────────────────────────────────────────

class SyncBaseNode:
    """Base wiring for all crawler nodes. Subclass Node or AsyncNode."""

    def __init__(self, max_retries: int = 1, wait: float = 0.0):
        self.max_retries = max_retries
        self.wait = wait
        self.cur_retry = 0
        self.successors: Dict[str, "SyncBaseNode"] = {}

    def __rshift__(self, other: "SyncBaseNode") -> "SyncBaseNode":
        """Wire ``self >> other`` as the default successor."""
        return self.next(other)

    def next(self, node: "SyncBaseNode", action: str = _DEFAULT_ACTION) -> "SyncBaseNode":
        """Set *node* as successor for *action*. Returns *node* for chaining."""
        self.successors[action] = node
        return node

    def prep(self, shared: Dict[str, Any]) -> Any:
        return None

    def exec(self, prep_res: Any) -> Any:
        return None

    def post(self, shared: Dict[str, Any], prep_res: Any, exec_res: Any) -> Optional[str]:
        return None

    def _exec_with_retry(self, prep_res: Any) -> Any:
        for attempt in range(self.max_retries):
            self.cur_retry = attempt
            try:
                return self.exec(prep_res)
            except Exception:
                if attempt >= self.max_retries - 1:
                    raise
                time.sleep(self.wait)

    def run(self, shared: Dict[str, Any]) -> Optional["SyncBaseNode"]:
        prep_res = self.prep(shared)
        exec_res = self._exec_with_retry(prep_res)
        action = self.post(shared, prep_res, exec_res) or _DEFAULT_ACTION
        return self.successors.get(action)


# ─────────────────────────────────────────────────────────────────────────────
# Sync variants
# ─────────────────────────────────────────────────────────────────────────────

class Node(SyncBaseNode):
    """Synchronous crawler node. Override prep / exec / post."""


class BatchNode(SyncBaseNode):
    """Sync node that processes a list returned by prep() item-by-item."""

    def exec(self, prep_res: Any) -> List[Any]:  # type: ignore[override]
        return [self._exec_item(item) for item in (prep_res or [])]

    def _exec_item(self, item: Any) -> Any:
        raise NotImplementedError("BatchNode subclasses must implement _exec_item()")


class Flow(SyncBaseNode):
    """Runs a linear sequence of sync nodes starting from *start*."""

    def __init__(self, start: SyncBaseNode):
        super().__init__()
        self.start = start

    def run(self, shared: Dict[str, Any]) -> Dict[str, Any]:  # type: ignore[override]
        current: Optional[SyncBaseNode] = self.start
        visited: List[str] = []
        while current is not None:
            name = type(current).__name__
            if name in visited:
                logger.warning("Flow: cycle at '%s' — halting", name)
                break
            visited.append(name)
            current = current.run(shared)
        return shared


class BatchFlow(Flow):
    """Runs the inner flow once per item in ``shared["batch_items"]``."""

    def run(self, shared: Dict[str, Any]) -> Dict[str, Any]:  # type: ignore[override]
        items = shared.get("batch_items", [])
        results = []
        for item in items:
            item_shared = {**shared, "batch_item": item}
            Flow.run(self, item_shared)
            results.append(item_shared.get("batch_result"))
        shared["batch_results"] = results
        return shared


# ─────────────────────────────────────────────────────────────────────────────
# AsyncNode — used by all crawler flows in this project
# ─────────────────────────────────────────────────────────────────────────────

class AsyncNode(SyncBaseNode):
    """Async crawler node. Override async prep / exec / post.

    All crawler flows use AsyncNode + AsyncFlow so they integrate cleanly
    with the FastAPI event loop without blocking.
    """

    async def prep(self, shared: Dict[str, Any]) -> Any:  # type: ignore[override]
        return None

    async def exec(self, prep_res: Any) -> Any:  # type: ignore[override]
        return None

    async def post(  # type: ignore[override]
        self,
        shared: Dict[str, Any],
        prep_res: Any,
        exec_res: Any,
    ) -> Optional[str]:
        return None

    async def _async_exec_with_retry(self, prep_res: Any) -> Any:
        for attempt in range(self.max_retries):
            self.cur_retry = attempt
            try:
                return await self.exec(prep_res)
            except Exception:
                if attempt >= self.max_retries - 1:
                    raise
                await asyncio.sleep(self.wait)

    async def run(self, shared: Dict[str, Any]) -> Optional["AsyncNode"]:  # type: ignore[override]
        prep_res = await self.prep(shared)
        exec_res = await self._async_exec_with_retry(prep_res)
        action = await self.post(shared, prep_res, exec_res) or _DEFAULT_ACTION
        return self.successors.get(action)  # type: ignore[return-value]


class AsyncBatchNode(AsyncNode):
    """Async node — processes each item returned by prep() sequentially."""

    async def exec(self, prep_res: Any) -> List[Any]:  # type: ignore[override]
        return [await self._exec_item(item) for item in (prep_res or [])]

    async def _exec_item(self, item: Any) -> Any:
        raise NotImplementedError("AsyncBatchNode subclasses must implement _exec_item()")


class AsyncParallelBatchNode(AsyncNode):
    """Async node — processes each item returned by prep() in parallel."""

    async def exec(self, prep_res: Any) -> List[Any]:  # type: ignore[override]
        return list(await asyncio.gather(
            *(self._exec_item(item) for item in (prep_res or []))
        ))

    async def _exec_item(self, item: Any) -> Any:
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# AsyncFlow
# ─────────────────────────────────────────────────────────────────────────────

class AsyncFlow(AsyncNode):
    """Runs a sequential DAG of AsyncNode instances.

    Usage::

        flow = AsyncFlow(start=fetch_node)
        await flow.run(shared)
    """

    def __init__(self, start: AsyncNode):
        super().__init__()
        self.start = start

    async def run(self, shared: Dict[str, Any]) -> Dict[str, Any]:  # type: ignore[override]
        current: Optional[AsyncNode] = self.start
        visited: List[str] = []
        while current is not None:
            name = type(current).__name__
            if name in visited:
                logger.warning("AsyncFlow: cycle at '%s' — halting", name)
                break
            visited.append(name)
            logger.debug("AsyncFlow: running '%s'", name)
            current = await current.run(shared)
        logger.debug("AsyncFlow: complete — nodes: %s", visited)
        return shared


class AsyncBatchFlow(AsyncFlow):
    """Runs the inner flow once per item in ``shared["batch_items"]``."""

    async def run(self, shared: Dict[str, Any]) -> Dict[str, Any]:  # type: ignore[override]
        items = shared.get("batch_items", [])
        results = []
        for item in items:
            item_shared = {**shared, "batch_item": item}
            await AsyncFlow.run(self, item_shared)
            results.append(item_shared.get("batch_result"))
        shared["batch_results"] = results
        return shared


class AsyncParallelBatchFlow(AsyncFlow):
    """Runs the inner flow in parallel for each item in ``shared["batch_items"]``."""

    async def _run_one(self, shared: Dict[str, Any], item: Any) -> Any:
        item_shared = {**shared, "batch_item": item}
        await AsyncFlow.run(self, item_shared)
        return item_shared.get("batch_result")

    async def run(self, shared: Dict[str, Any]) -> Dict[str, Any]:  # type: ignore[override]
        items = shared.get("batch_items", [])
        shared["batch_results"] = list(
            await asyncio.gather(*(self._run_one(shared, item) for item in items))
        )
        return shared
