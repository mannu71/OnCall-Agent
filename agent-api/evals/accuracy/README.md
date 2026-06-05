# Accuracy harness — CloudWatch & CodeCrawler

Measures the accuracy of the two AI features against ground truth, splitting
**objective** (deterministic, the headline number) from **semantic** (LLM-judged,
reported separately).

## What is measured

| Feature | Objective (headline) | Semantic (reported) |
|---|---|---|
| CodeCrawler | `find`/`body`/`trace` vs an **AST-derived oracle** over the fixture repo — exact `(symbol, file, line, kind)` + intra-repo call edges | — |
| CloudWatch | structured **schema validity** + **ID-grounding** (no fabricated correlation/trace/request IDs) over injected evidence bundles | severity match, judge faithfulness |

Ground truth for CodeCrawler is generated from `fixtures/sample_repo/` by `build_crawler_cases.py` via Python `ast`, so it is correct by construction.
CloudWatch cases inject recorded synthetic evidence (`fixtures/cloudwatch_evidence/*.json`) straight into the real `analyze_cloudwatch_with_llm`, so the LLM runs but no live AWS fetch is needed (reproducible).

## Running (inside the agent-api container)

The container has the DB and the Bedrock **model-key credentials** (the host's
ambient `~/.aws` creds are not valid for Bedrock). The judge and CloudWatch
synthesis build their LLM through the app's enriched `resolve_llm_config` ->
`build_llm` path, which injects those creds and the eu cross-region inference
profile.

```bash
# copy the harness in (only app/ + data/ are baked into the image)
docker cp evals agent-api-agent-api-1:/app/evals

docker exec agent-api-agent-api-1 sh -c "cd /app && python -m evals.accuracy.runner --report"
docker exec agent-api-agent-api-1 sh -c "cd /app && python -m evals.accuracy.selftest"
# or the pytest gate:
docker exec agent-api-agent-api-1 sh -c "cd /app && pytest -m accuracy_eval -v"
```

Reports are written to `reports/accuracy_report_<date>.md`.

## Self-validation

`selftest.py` proves the harness is trustworthy before any score is believed:
- **negative controls** — every grader is fed known-bad input and must NOT return 1.0;
- **judge calibration** — a hand-labeled faithful/unfaithful set reports judge-vs-human agreement.

## Files

- `graders.py` — deterministic scorers (pure, no LLM)
- `judge.py` — Bedrock LLM-as-judge (faithfulness only)
- `build_crawler_cases.py` — AST oracle -> crawler ground truth
- `run_crawler.py` / `run_cloudwatch.py` — suite runners
- `runner.py` — orchestrate + aggregate + `--report`
- `report.py` — markdown report
- `selftest.py` — harness self-validation
