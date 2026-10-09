"""Small local models for narrow, high-volume jobs (routing, PII, injection,
embeddings, reranking).

Everything here is off by default (``MINI_MODELS_ENABLED=false``) and every role
falls back to today's regex / lexical / Bedrock path when its model is not
configured, overloaded, slow or failing. See ``registry`` for how models are
pinned and licensed, ``pool`` for how they run off the event loop, and
``budget`` for the container-aware CPU budget.
"""
from app.core.mini.budget import available_cpus, inference_threads
from app.core.mini.pool import MiniPool, get_pool, load_once, shutdown_pool, worker_threads
from app.core.mini.protocol import MiniModel
from app.core.mini.registry import (
    ROLES,
    TASKS,
    MiniModelSpec,
    all_specs,
    get_spec,
    model_for_role,
    register,
)

__all__ = [
    "MiniModel",
    "MiniModelSpec",
    "MiniPool",
    "ROLES",
    "TASKS",
    "all_specs",
    "available_cpus",
    "get_pool",
    "get_spec",
    "inference_threads",
    "load_once",
    "model_for_role",
    "register",
    "shutdown_pool",
    "worker_threads",
]
