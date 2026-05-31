"""Async Map-Reduce engine for parallel workload dispatch.

Pattern
-------
  Map phase   — run ``mapper(item)`` for every item concurrently,
                bounded by a semaphore so resource usage stays predictable.
                Every item produces a ``MapResult`` regardless of success/failure
                (errors are captured, not raised), so the reduce phase always
                sees the full picture.

  Reduce phase — run ``reducer(map_results)`` exactly once after all items
                 complete.  Only executes when at least ``min_success_rate``
                 fraction of items succeeded; otherwise ``MapReduceResult``
                 carries a ``reduce_error`` explaining the skip.

No external dependencies — pure asyncio + stdlib.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, Generic, List, Optional, TypeVar

from app.config import settings

logger = logging.getLogger(__name__)

# Generic type vars so the engine stays fully typed for any item/result shape.
_I  = TypeVar("_I")   # map input item
_MR = TypeVar("_MR")  # map result per item
_RR = TypeVar("_RR")  # final reduce result

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class MapReduceConfig:
    """Runtime tunables for the map-reduce engine.

    All values can be overridden via environment variables.
    """

    # Maximum number of map tasks running at the same time.
    concurrency_limit: int = settings.parallel_flow_concurrency

    # Per-item wall-clock timeout (seconds).  0 = no timeout.
    item_timeout_seconds: float = settings.parallel_flow_item_timeout

    # If True, cancel all pending tasks when the first failure is recorded.
    fail_fast: bool = settings.parallel_flow_fail_fast

    # Reduce phase is skipped when fewer than this fraction of items succeeded.
    min_success_rate: float = settings.parallel_flow_min_success_rate


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class MapResult(Generic[_I, _MR]):
    """Outcome of processing one map item."""

    item: _I
    result: Optional[_MR] = None
    error:  Optional[str] = None
    duration_ms: float    = 0.0

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass
class MapReduceResult(Generic[_I, _MR, _RR]):
    """Aggregated outcome of a full map-reduce run."""

    map_results:    List[MapResult[_I, _MR]]
    reduce_result:  Optional[_RR]  = None
    reduce_error:   Optional[str]  = None
    total_duration_ms: float       = 0.0

    # ---- convenience views -----------------------------------------------

    @property
    def successful_maps(self) -> List[MapResult[_I, _MR]]:
        return [r for r in self.map_results if r.success]

    @property
    def failed_maps(self) -> List[MapResult[_I, _MR]]:
        return [r for r in self.map_results if not r.success]

    @property
    def map_success_rate(self) -> float:
        if not self.map_results:
            return 0.0
        return len(self.successful_maps) / len(self.map_results)

    @property
    def succeeded(self) -> bool:
        """True when the reduce phase ran and produced a result."""
        return self.reduce_result is not None

    def summary(self) -> Dict[str, Any]:
        """Return a JSON-serialisable summary dict for logging / SSE events."""
        return {
            "total_items":      len(self.map_results),
            "succeeded":        len(self.successful_maps),
            "failed":           len(self.failed_maps),
            "success_rate":     round(self.map_success_rate, 3),
            "total_duration_ms": round(self.total_duration_ms, 1),
            "reduce_ok":        self.reduce_result is not None,
            "reduce_error":     self.reduce_error,
        }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class MapReduceEngine:
    """Async map-reduce executor.

    Usage::

        engine = MapReduceEngine(MapReduceConfig(concurrency_limit=4))
        result = await engine.run(
            items   = [target_a, target_b, target_c],
            mapper  = my_async_mapper,
            reducer = my_async_reducer,
        )
        if result.succeeded:
            print(result.reduce_result)

    The mapper and reducer are plain async callables — no subclassing required.

    ``mapper(item)  -> Any``
    ``reducer(map_results: List[MapResult]) -> Any``
    """

    def __init__(self, config: Optional[MapReduceConfig] = None) -> None:
        self._cfg = config or MapReduceConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        items:   List[_I],
        mapper:  Callable[[_I], Coroutine[Any, Any, _MR]],
        reducer: Callable[[List[MapResult[_I, _MR]]], Coroutine[Any, Any, _RR]],
    ) -> MapReduceResult[_I, _MR, _RR]:
        """Execute the full map-reduce cycle.

        Args:
            items:   Items to process in the map phase.
            mapper:  Async callable ``(item) -> result``.
            reducer: Async callable ``(map_results) -> reduce_result``.

        Returns:
            :class:`MapReduceResult` containing per-item outcomes and the
            final reduced value (or error).
        """
        if not items:
            logger.warning("MapReduceEngine.run called with an empty item list — nothing to do")
            return MapReduceResult(map_results=[], reduce_error="No items to process")

        wall_start = datetime.now(timezone.utc)

        # ── Map phase ──────────────────────────────────────────────────────
        map_results = await self._run_map(items, mapper)

        success_rate = (
            sum(1 for r in map_results if r.success) / len(map_results)
            if map_results else 0.0
        )

        logger.info(
            "MapReduceEngine: map phase complete — %d/%d succeeded (%.0f%%)",
            sum(1 for r in map_results if r.success),
            len(map_results),
            success_rate * 100,
        )

        # ── Reduce phase ───────────────────────────────────────────────────
        reduce_result: Optional[_RR] = None
        reduce_error:  Optional[str] = None

        if success_rate < self._cfg.min_success_rate:
            reduce_error = (
                f"Reduce phase skipped: only {success_rate:.0%} of items succeeded "
                f"(threshold {self._cfg.min_success_rate:.0%})"
            )
            logger.warning("MapReduceEngine: %s", reduce_error)
        else:
            try:
                reduce_result = await reducer(map_results)
                logger.info("MapReduceEngine: reduce phase complete")
            except Exception as exc:
                reduce_error = str(exc)
                logger.error("MapReduceEngine: reduce phase failed — %s", exc, exc_info=True)

        total_ms = (
            (datetime.now(timezone.utc) - wall_start).total_seconds() * 1000
        )

        return MapReduceResult(
            map_results      = map_results,
            reduce_result    = reduce_result,
            reduce_error     = reduce_error,
            total_duration_ms= total_ms,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _run_map(
        self,
        items:  List[_I],
        mapper: Callable[[_I], Coroutine[Any, Any, _MR]],
    ) -> List[MapResult[_I, _MR]]:
        """Dispatch all map tasks with bounded concurrency.

        Returns results in the same order as *items*.
        """
        sem = asyncio.Semaphore(self._cfg.concurrency_limit)
        stop_event = asyncio.Event()  # used for fail-fast cancellation

        tasks = [
            asyncio.create_task(
                self._map_one(item, mapper, sem, stop_event),
                name=f"map_{idx}",
            )
            for idx, item in enumerate(items)
        ]

        results: List[MapResult[_I, _MR]] = await asyncio.gather(*tasks, return_exceptions=False)
        return list(results)

    async def _map_one(
        self,
        item:        _I,
        mapper:      Callable[[_I], Coroutine[Any, Any, _MR]],
        sem:         asyncio.Semaphore,
        stop_event:  asyncio.Event,
    ) -> MapResult[_I, _MR]:
        """Execute a single map item inside the semaphore, capturing any error."""
        if stop_event.is_set():
            return MapResult(item=item, error="Cancelled (fail-fast)")

        async with sem:
            if stop_event.is_set():
                return MapResult(item=item, error="Cancelled (fail-fast)")

            start = datetime.now(timezone.utc)
            try:
                if self._cfg.item_timeout_seconds > 0:
                    result = await asyncio.wait_for(
                        mapper(item),
                        timeout=self._cfg.item_timeout_seconds,
                    )
                else:
                    result = await mapper(item)

                duration_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
                return MapResult(item=item, result=result, duration_ms=duration_ms)

            except asyncio.TimeoutError:
                duration_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
                err = f"Timed out after {self._cfg.item_timeout_seconds:.0f}s"
                logger.warning("MapReduceEngine: item timed out — %r", item)
                if self._cfg.fail_fast:
                    stop_event.set()
                return MapResult(item=item, error=err, duration_ms=duration_ms)

            except Exception as exc:
                duration_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
                logger.warning("MapReduceEngine: item failed — %r: %s", item, exc)
                if self._cfg.fail_fast:
                    stop_event.set()
                return MapResult(item=item, error=str(exc), duration_ms=duration_ms)
