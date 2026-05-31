"""Async parallel flow runner — concurrent dispatch of the same workflow
against multiple parameter sets.

Pattern
-------
``ParallelFlowRunner.run_all(param_sets, flow_fn)`` fans out ``flow_fn`` across
every param set concurrently (bounded by a semaphore), collects every result —
successes and failures alike — and returns them in the same order as the inputs.

Key differences from :class:`~app.core.map_reduce.MapReduceEngine`
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* No reduce phase — callers aggregate results themselves.
* Designed for *flow-level* parallelism (e.g. investigate 5 services at once)
  rather than data-level map-reduce.
* ``batch_timeout`` applies to the *entire batch*, not per item, so you can
  cap wall-clock time regardless of how many items there are.
* Provides a typed ``FlowResult`` with the original params attached so callers
  don't need to zip inputs/outputs manually.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, Generic, List, Optional, TypeVar

from app.config import settings

logger = logging.getLogger(__name__)

_P  = TypeVar("_P")  # param type
_R  = TypeVar("_R")  # result type

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class ParallelFlowConfig:
    """Tunables for parallel flow execution."""

    concurrency: int = 5
    item_timeout_seconds: float = 180.0
    batch_timeout_seconds: float = 0.0
    fail_fast: bool = False

    @classmethod
    def from_settings(cls) -> "ParallelFlowConfig":
        return cls(
            concurrency=settings.parallel_flow_concurrency,
            item_timeout_seconds=settings.parallel_flow_item_timeout,
            batch_timeout_seconds=settings.parallel_flow_batch_timeout,
            fail_fast=settings.parallel_flow_fail_fast,
        )


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class FlowResult(Generic[_P, _R]):
    """Outcome of one parallel flow invocation."""

    params:     _P
    result:     Optional[_R] = None
    error:      Optional[str] = None
    duration_ms: float        = 0.0

    @property
    def success(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class ParallelFlowRunner:
    """Fan out a single async callable across N parameter sets concurrently.

    Usage::

        runner = ParallelFlowRunner(ParallelFlowConfig(concurrency=3))
        results: List[FlowResult] = await runner.run_all(
            param_sets = [service_a, service_b, service_c],
            flow_fn    = investigate,   # async def investigate(params) -> str
        )
        for r in results:
            if r.success:
                print(r.params, "→", r.result)

    Results are always returned in the **same order** as *param_sets* even
    though execution is concurrent.
    """

    def __init__(self, config: Optional[ParallelFlowConfig] = None) -> None:
        self._cfg = config or ParallelFlowConfig.from_settings()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_all(
        self,
        param_sets: List[_P],
        flow_fn:    Callable[[_P], Coroutine[Any, Any, _R]],
    ) -> List[FlowResult[_P, _R]]:
        """Run ``flow_fn(p)`` for every ``p`` in *param_sets* concurrently.

        Args:
            param_sets: Inputs to fan out across.
            flow_fn:    ``async def flow_fn(params) -> result``

        Returns:
            One :class:`FlowResult` per input, in the original order.
        """
        if not param_sets:
            logger.warning("ParallelFlowRunner.run_all: empty param_sets — nothing to run")
            return []

        sem        = asyncio.Semaphore(self._cfg.concurrency)
        stop_flag  = asyncio.Event()

        tasks = [
            asyncio.create_task(
                self._run_one(p, flow_fn, sem, stop_flag),
                name=f"parallel_flow_{i}",
            )
            for i, p in enumerate(param_sets)
        ]

        logger.info(
            "ParallelFlowRunner: dispatching %d tasks (concurrency=%d)",
            len(tasks), self._cfg.concurrency,
        )

        if self._cfg.batch_timeout_seconds > 0:
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=False),
                    timeout=self._cfg.batch_timeout_seconds,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "ParallelFlowRunner: batch timed out after %.0fs — "
                    "cancelling remaining tasks",
                    self._cfg.batch_timeout_seconds,
                )
                for t in tasks:
                    if not t.done():
                        t.cancel()
                # Collect whatever finished before the timeout.
                results = []
                for i, (t, p) in enumerate(zip(tasks, param_sets)):
                    if t.done() and not t.cancelled():
                        try:
                            results.append(t.result())
                        except Exception as exc:
                            results.append(FlowResult(params=p, error=str(exc)))
                    else:
                        results.append(
                            FlowResult(
                                params=p,
                                error=f"Cancelled: batch timeout after "
                                      f"{self._cfg.batch_timeout_seconds:.0f}s",
                            )
                        )
        else:
            results = await asyncio.gather(*tasks, return_exceptions=False)

        success = sum(1 for r in results if r.success)
        logger.info(
            "ParallelFlowRunner: completed %d/%d successfully",
            success, len(results),
        )
        return list(results)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _run_one(
        self,
        params:    _P,
        flow_fn:   Callable[[_P], Coroutine[Any, Any, _R]],
        sem:       asyncio.Semaphore,
        stop_flag: asyncio.Event,
    ) -> FlowResult[_P, _R]:
        """Execute one flow item inside the semaphore, capturing any error."""
        if stop_flag.is_set():
            return FlowResult(params=params, error="Cancelled (fail-fast)")

        async with sem:
            if stop_flag.is_set():
                return FlowResult(params=params, error="Cancelled (fail-fast)")

            start = datetime.now(timezone.utc)
            try:
                if self._cfg.item_timeout_seconds > 0:
                    result = await asyncio.wait_for(
                        flow_fn(params),
                        timeout=self._cfg.item_timeout_seconds,
                    )
                else:
                    result = await flow_fn(params)

                duration_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
                return FlowResult(params=params, result=result, duration_ms=duration_ms)

            except asyncio.TimeoutError:
                duration_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
                logger.warning("ParallelFlowRunner: item timed out — %r", params)
                if self._cfg.fail_fast:
                    stop_flag.set()
                return FlowResult(
                    params=params,
                    error=f"Timed out after {self._cfg.item_timeout_seconds:.0f}s",
                    duration_ms=duration_ms,
                )

            except Exception as exc:
                duration_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
                logger.warning("ParallelFlowRunner: item failed — %r: %s", params, exc)
                if self._cfg.fail_fast:
                    stop_flag.set()
                return FlowResult(params=params, error=str(exc), duration_ms=duration_ms)

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    async def run_with_index(
        self,
        param_sets: List[_P],
        flow_fn:    Callable[[int, _P], Coroutine[Any, Any, _R]],
    ) -> List[FlowResult[_P, _R]]:
        """Like :meth:`run_all` but ``flow_fn`` also receives the item index.

        Useful when items don't carry their own identity and the index is
        needed to correlate results (e.g. log group #2 of 5).
        """
        return await self.run_all(
            param_sets=list(enumerate(param_sets)),
            flow_fn=lambda idx_p: flow_fn(idx_p[0], idx_p[1]),
        )

    async def first_success(
        self,
        param_sets: List[_P],
        flow_fn:    Callable[[_P], Coroutine[Any, Any, _R]],
    ) -> Optional[FlowResult[_P, _R]]:
        """Return the first successful result, or None if all failed.

        Useful for fallback chains: try provider A, B, C in parallel — take
        whichever responds first without error.
        """
        results = await self.run_all(param_sets, flow_fn)
        return next((r for r in results if r.success), None)
