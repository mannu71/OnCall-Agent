"""Concurrency primitives: fan-out/fan-in, thread pools, locks.

Modules: map_reduce and parallel_flow (deliberately separate semantics -- reduce
phase + min_success_rate vs order-preserving fan-out), thread_pools,
workflow_concurrency, distributed_lock. Import modules directly; no re-exports.
"""
