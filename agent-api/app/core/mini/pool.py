"""Bounded worker pool for local model inference.

Local models are CPU-bound, and agent-api is a single asyncio process, so
inference must never run on the event loop or in the shared default executor.
:class:`MiniPool` runs it in a small ``spawn`` process pool (``fork`` is unsafe
once grpcio/boto3 threads exist) and guarantees that a caller always gets an
answer quickly:

* **Bounded queue.** When ``queue_limit`` calls are already in flight, a new
  call returns its fallback immediately instead of queueing.
* **Timeout.** A call that exceeds ``timeout`` returns its fallback. The work
  cannot be interrupted inside the worker, so it still counts as in flight
  until it really finishes; that keeps the queue bound honest.
* **Errors.** Any exception, including a crashed worker, returns the fallback
  and is counted; a broken pool is rebuilt on the next call.

The fallback is a zero-argument callable run on the event loop, so it must be
cheap: today's regex / lexical / no-op behaviour for that role.
"""
from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import statistics
import time
from collections import defaultdict, deque
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any, Callable, Deque, Dict, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Env vars that cap native thread pools inside a worker (ONNX/BLAS/tokenizers).
_THREAD_ENV = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
               "ONNX_INTRA_THREADS")

# Worker-side state (one copy per worker process).
_WORKER_THREADS = 1
_WORKER_MODELS: Dict[str, Any] = {}


def _init_worker(threads: int) -> None:
    """Pool initializer: cap native threads before any model library loads."""
    global _WORKER_THREADS
    _WORKER_THREADS = max(1, int(threads))
    for var in _THREAD_ENV:
        os.environ[var] = str(_WORKER_THREADS)
    os.environ["TOKENIZERS_PARALLELISM"] = "false"


def worker_threads() -> int:
    """Thread budget for this worker (for ONNX ``intra_op_num_threads``)."""
    return _WORKER_THREADS


def load_once(key: str, loader: Callable[[], T]) -> T:
    """Load a model at most once per worker process and cache it by *key*."""
    model = _WORKER_MODELS.get(key)
    if model is None:
        model = loader()
        _WORKER_MODELS[key] = model
    return model


def _ping(value: Any) -> Any:
    """Round-trip helper used by health checks and tests."""
    return value


def _sleep(seconds: float) -> float:
    """Blocking helper used by tests to exercise timeouts and the queue bound."""
    time.sleep(seconds)
    return seconds


class MiniPool:
    """Spawn-based process pool with per-role stats, timeouts and fallbacks."""

    def __init__(self, workers: int, threads_per_worker: int, queue_limit: int,
                 default_timeout: float) -> None:
        self.workers = max(1, workers)
        self.threads_per_worker = max(1, threads_per_worker)
        self.queue_limit = max(1, queue_limit)
        self.default_timeout = default_timeout
        self._executor: Optional[ProcessPoolExecutor] = None
        self._in_flight = 0
        self._counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._latency_ms: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=500))

    # ── lifecycle ────────────────────────────────────────────────────────
    def _ensure_executor(self) -> ProcessPoolExecutor:
        if self._executor is None:
            self._executor = ProcessPoolExecutor(
                max_workers=self.workers,
                mp_context=multiprocessing.get_context("spawn"),
                initializer=_init_worker,
                initargs=(self.threads_per_worker,),
            )
        return self._executor

    def shutdown(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    @property
    def in_flight(self) -> int:
        return self._in_flight

    # ── calls ────────────────────────────────────────────────────────────
    def _done(self, fut: "asyncio.Future[Any]") -> None:
        self._in_flight -= 1
        if not fut.cancelled():
            fut.exception()  # mark retrieved: abandoned (timed-out) calls stay quiet

    def _fallback(self, role: str, reason: str, fallback: Callable[[], T]) -> T:
        self._counts[role][f"fallback_{reason}"] += 1
        return fallback()

    async def run(self, role: str, fn: Callable[..., T], *args: Any,
                  fallback: Callable[[], T], timeout: Optional[float] = None) -> T:
        """Run ``fn(*args)`` in a worker; return ``fallback()`` on overload/timeout/error.

        ``fn`` must be a picklable module-level function.
        """
        self._counts[role]["calls"] += 1
        if self._in_flight >= self.queue_limit:
            return self._fallback(role, "queue_full", fallback)

        loop = asyncio.get_running_loop()
        try:
            fut = loop.run_in_executor(self._ensure_executor(), fn, *args)
        except (BrokenProcessPool, RuntimeError) as exc:
            logger.warning("mini pool unavailable for %s (%s); rebuilding", role, exc)
            self.shutdown()
            return self._fallback(role, "error", fallback)

        self._in_flight += 1
        fut.add_done_callback(self._done)
        t0 = time.perf_counter()
        try:
            result = await asyncio.wait_for(asyncio.shield(fut),
                                            timeout or self.default_timeout)
        except asyncio.TimeoutError:
            return self._fallback(role, "timeout", fallback)
        except BrokenProcessPool as exc:
            logger.warning("mini pool worker crashed during %s (%s); rebuilding", role, exc)
            self.shutdown()
            return self._fallback(role, "error", fallback)
        except Exception as exc:  # noqa: BLE001 — a model failure must never fail a run
            logger.warning("mini model call for %s failed: %s", role, exc)
            return self._fallback(role, "error", fallback)
        self._counts[role]["ok"] += 1
        self._latency_ms[role].append((time.perf_counter() - t0) * 1000.0)
        return result

    # ── observability ────────────────────────────────────────────────────
    def stats(self) -> Dict[str, Dict[str, Any]]:
        """Per-role counters plus p50/p95 latency of successful calls (ms)."""
        out: Dict[str, Dict[str, Any]] = {}
        for role, counts in self._counts.items():
            row: Dict[str, Any] = dict(counts)
            lat = sorted(self._latency_ms.get(role) or [])
            if lat:
                row["p50_ms"] = round(statistics.median(lat), 2)
                row["p95_ms"] = round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 2)
            out[role] = row
        return out


_POOL: Optional[MiniPool] = None


def get_pool() -> MiniPool:
    """Process-wide pool built from settings (created lazily, no workers until used)."""
    global _POOL
    if _POOL is None:
        from app.config import settings
        from app.core.mini.budget import inference_threads

        workers = max(1, int(getattr(settings, "mini_pool_workers", 2)))
        budget = inference_threads(reserved=int(getattr(settings, "mini_reserved_cpus", 1)))
        _POOL = MiniPool(
            workers=workers,
            threads_per_worker=max(1, budget // workers),
            queue_limit=int(getattr(settings, "mini_pool_queue_limit", 32)),
            default_timeout=float(getattr(settings, "mini_call_timeout_seconds", 2.0)),
        )
    return _POOL


def shutdown_pool() -> None:
    """Stop the worker processes (called from the API lifespan on shutdown)."""
    global _POOL
    if _POOL is not None:
        _POOL.shutdown()
        _POOL = None
