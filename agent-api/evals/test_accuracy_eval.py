"""Pytest gate for the accuracy harness.

Excluded from default runs (marker ``accuracy_eval``) because it calls real
Bedrock. Run inside the agent-api container, which has DB + model-key creds:

    pytest -m accuracy_eval -v
"""
import pytest

from evals.accuracy.runner import run_all
from evals.accuracy.selftest import negative_controls

OBJECTIVE_THRESHOLD = 0.99
# Trajectory cases exercise real agent behaviour (not a pure function), so they
# start at a lower gate and ratchet up as the predicates are tuned.
TRAJECTORY_THRESHOLD = 0.90


def test_graders_bite():
    """The deterministic graders must reject known-bad input (harness self-trust)."""
    passed, total, failures = negative_controls()
    assert not failures, f"graders failed negative controls: {failures} ({passed}/{total})"


@pytest.mark.accuracy_eval
@pytest.mark.asyncio
async def test_objective_accuracy():
    agg = await run_all()
    # runner.aggregate() emits "codegraph" (the Code Crawler backend was removed);
    # the old "crawler" key was stale and raised KeyError before the gate ran.
    codegraph = agg["codegraph"]["objective_accuracy"]
    cloud = agg["cloudwatch"]["objective_accuracy"]
    assert codegraph >= OBJECTIVE_THRESHOLD, f"codegraph objective accuracy {codegraph:.4f} < {OBJECTIVE_THRESHOLD}"
    assert cloud >= OBJECTIVE_THRESHOLD, f"CloudWatch objective accuracy {cloud:.4f} < {OBJECTIVE_THRESHOLD}"

    traj = agg.get("trajectory") or {}
    if traj.get("objective_n"):
        acc = traj["objective_accuracy"]
        assert acc >= TRAJECTORY_THRESHOLD, f"Agent trajectory objective accuracy {acc:.4f} < {TRAJECTORY_THRESHOLD}"
