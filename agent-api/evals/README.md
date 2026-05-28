# Eval harness (P3 §2.8)

Golden-trace regression suite for the agent-api workflow strategies.
Each case in `golden_cases.jsonl` defines a workflow + inputs and the
expected outputs / tool calls / token ceiling. The harness re-runs the
workflow against the live `VisualWorkflowExecutor` and gates merges on:

- answer-match accuracy   (>= baseline - 2pp)
- mean total tokens       (<= baseline + 15%)
- p95 latency             (<= baseline + 25%)

## Usage

    cd agent-api
    pytest -m eval -v

## Adding a case

Append one JSON object per line to `golden_cases.jsonl`:

    {"id": "kyc-001",
     "workflow": "kyc-protect-investigation",
     "inputs": {"customer_id": "synthetic-001"},
     "expected_substrings": ["root cause", "auth-svc"],
     "expected_tools": ["search_logs", "correlate_logs"],
     "max_total_tokens": 50000}

Keep inputs synthetic — do **not** commit real customer IDs or PHI.

## CI

The `eval` marker is excluded from default `pytest` runs (it costs real
LLM tokens). A separate GitHub Actions job (`.github/workflows/eval.yml`,
not part of this scaffold) runs the suite on PRs touching prompts, tool
schemas, or strategies, and reports the metrics to the PR.
