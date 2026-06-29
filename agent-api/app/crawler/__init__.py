"""Code crawler package.

Provides a library of async flows for indexing and querying local repositories
without embeddings, inverted indexes, or external CLI tools. All retrieval is
LLM-driven with on-disk verification and a Postgres prompt cache.

Public API
----------
run_flow(name, flow, shared)
    Execute an AsyncFlow and persist a ``flow_runs`` row with the full trace.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict

from app.engine.crawler_engine import AsyncFlow

logger = logging.getLogger(__name__)


async def run_flow(
    flow_name: str,
    flow: AsyncFlow,
    shared: Dict[str, Any],
) -> Dict[str, Any]:
    """Run *flow* and write one ``flow_runs`` row.

    Args:
        flow_name: Identifier stored in ``flow_runs.flow_name``.
        flow:      Configured AsyncFlow instance ready to run.
        shared:    Pre-populated input dict. Mutated in-place by nodes.

    Returns:
        The *shared* dict after all nodes have executed.
    """
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text

    started = time.monotonic()
    error: str | None = None
    success = False

    try:
        await flow.run(shared)
        success = True
    except Exception as exc:
        error = str(exc)
        logger.error("run_flow[%s] failed: %s", flow_name, exc, exc_info=True)
        raise
    finally:
        duration_ms = int((time.monotonic() - started) * 1000)
        trace = shared.get("_trace", [])
        response = shared.get("response")

        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    text("""
                        INSERT INTO flow_runs
                            (flow_name, session_id, repo_name,
                             inputs, response, trace,
                             success, error, duration_ms)
                        VALUES
                            (:flow_name, :session_id, :repo_name,
                             CAST(:inputs AS jsonb), CAST(:response AS jsonb),
                             CAST(:trace AS jsonb),
                             :success, :error, :duration_ms)
                    """),
                    {
                        "flow_name": flow_name,
                        "session_id": shared.get("session_id"),
                        "repo_name": shared.get("repo"),
                        "inputs": _safe_json(shared.get("_inputs", {})),
                        "response": _safe_json(response),
                        "trace": _safe_json(trace),
                        "success": success,
                        "error": error,
                        "duration_ms": duration_ms,
                    },
                )
                await session.commit()
        except Exception as db_exc:
            logger.warning("run_flow: could not persist flow_runs row: %s", db_exc)

    return shared


def _safe_json(value: Any) -> str:
    """Serialise *value* to a JSON string, falling back to an error marker."""
    try:
        return json.dumps(value, default=str)
    except Exception:
        return '{"error": "serialisation_failed"}'
