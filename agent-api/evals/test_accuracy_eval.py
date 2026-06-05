"""Pytest gate for the accuracy harness.

Excluded from default runs (marker ``accuracy_eval``) because it calls real
Bedrock. Run inside the agent-api container, which has DB + model-key creds:

    pytest -m accuracy_eval -v
"""
import pytest

from evals.accuracy.runner import run_all
from evals.accuracy.selftest import negative_controls

OBJECTIVE_THRESHOLD = 0.99


def test_graders_bite():
    """The deterministic graders must reject known-bad input (harness self-trust)."""
    passed, total, failures = negative_controls()
    assert not failures, f"graders failed negative controls: {failures} ({passed}/{total})"


@pytest.mark.accuracy_eval
@pytest.mark.asyncio
async def test_objective_accuracy():
    agg = await run_all()
    crawler = agg["crawler"]["objective_accuracy"]
    cloud = agg["cloudwatch"]["objective_accuracy"]
    assert crawler >= OBJECTIVE_THRESHOLD, f"CodeCrawler objective accuracy {crawler:.4f} < {OBJECTIVE_THRESHOLD}"
    assert cloud >= OBJECTIVE_THRESHOLD, f"CloudWatch objective accuracy {cloud:.4f} < {OBJECTIVE_THRESHOLD}"
