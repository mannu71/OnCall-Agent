# Accuracy Report — CloudWatch & CodeCrawler

_Generated: 2026-06-05_

## Headline (objective, deterministic metrics)

| Feature | Objective accuracy | Cases | Target | Status |
|---|---|---|---|---|
| CodeCrawler (find/body/trace vs AST oracle) | **100.00%** | 18/18 | >=99% | PASS |
| CloudWatch (schema + ID-grounding) | **100.00%** | 8/8 | >=99% | PASS |

> The headline counts only objectively-checkable metrics. Semantic metrics (CloudWatch severity, LLM-judge faithfulness) are reported separately below because they are subjective and an LLM judge agrees with a human only ~80-90% of the time - folding them into the headline would make "100%" a measurement artifact rather than a fact.

## CodeCrawler detail

Ground truth is derived from the fixture repo via Python `ast`, so the expected `(symbol, file, line, kind)` and intra-repo call edges are correct by construction.

| Operation | Accuracy | Passed |
|---|---|---|
| find | 100.00% | 8/8 |
| body | 100.00% | 6/6 |
| trace | 100.00% | 4/4 |

## CloudWatch detail

Synthesis runs the real `analyze_cloudwatch_with_llm` over recorded synthetic evidence bundles (reproducible; no live AWS fetch).

| Metric | Type | Accuracy | Passed |
|---|---|---|---|
| schema | objective | 100.00% | 4/4 |
| id_grounding | objective | 100.00% | 4/4 |
| severity | semantic | 100.00% | 4/4 |
| judge_faithfulness | semantic | 91.75% | 0/4 |

- **id_grounding** is the hallucination detector: fraction of ID-like tokens in the narrative that actually appear in the evidence. 100% = no fabricated IDs.

## Sub-perfect cases

| Feature | Case | Metric | Score | Diagnostic |
|---|---|---|---|---|
| cloudwatch | cw-critical-npe | judge_faithfulness | 0.92 | The output faithfully cites all required elements: NullPointerException in AuthService.validate() with line 145, all three identifiers (correlationId, traceId,  |
| cloudwatch | cw-clean-nofindings | judge_faithfulness | 0.95 | The output accurately reflects the evidence: full coverage, zero anomalies, zero critical/high severity issues, no error patterns, and 3 alarms with 0 in alarm  |
| cloudwatch | cw-partial-coverage | judge_faithfulness | 0.85 | The output accurately reflects partial coverage, low evidence grade, surfaces the AccessDenied error with requestId, maintains medium severity with appropriate  |
| cloudwatch | cw-anomaly-spike | judge_faithfulness | 0.95 | The output accurately surfaces all required elements: the ~9x baseline throttling spike (530 vs 60 baseline), high severity rating, kyc-gateway log group, sessi |

## Fixes applied to reach 100% (objective)

1. **Index crash (real bug)** - `app/crawler/nodes/abstractions.py`: the relationship-analysis node assumed every LLM-returned relationship is a dict and crashed (`'str' object has no attribute 'get'`) on a bare-string relationship, failing the whole index flow. Added a guard to skip non-dict entries.

2. **Severity over-rating on weak evidence (real bug)** - `app/workflow/executor/cloudwatch_analysis.py`: synthesis rated a partial-coverage / low-`evidence_grade` case `high` where `medium` is warranted, and the LLM would not self-cap even when instructed. Added a deterministic guardrail `_clamp_severity_for_weak_evidence` that caps severity at `medium` when coverage is partial/none, results are sampled, or `evidence_grade` is low - UNLESS an alarm is in ALARM state or a high/critical anomaly is present. Prompt guidance was added too (defence in depth). This took CloudWatch severity from 87.5% -> 100% exact.

3. **Trace metric scoping** - `grade_trace` measures the intra-repo (`resolved`) call graph and excludes `confidence='external'` edges (calls into stdlib/third-party such as `str.split`), which are legitimate crawler output but outside an AST-derived intra-repo ground truth.

4. **Per-case log groups (fixture correctness)** - the suite now passes each case's own `log_groups` to synthesis instead of a generic default, so the judge no longer penalises a harness/evidence mismatch.

5. **Stale container code** - the running image predated the host rename `root_cause_hypothesis` -> `primary_hypothesis` in the CloudWatch structured schema; syncing the current source restored schema conformance (the structured output had been failing the required-field check).

## Recommendations

- **Surface `patterns.evidence_grade` into `build_synthesis_payload`.** The payload keeps only specific per-pattern fields, so `evidence_grade` never reaches the model; the deterministic clamp makes the guardrail robust regardless, but feeding the grade in lets the model's own confidence wording match it.

- **Add regression unit tests** for `_clamp_severity_for_weak_evidence` (partial->medium; alarm / high-anomaly override) and for non-dict relationships in the index flow.

- **Rebuild / redeploy the container image** so the deployed code matches the host: the app-code fixes above were `docker cp`'d into the running container to measure and will revert on restart until a real redeploy.

- **Wire the suite into CI** behind the `accuracy_eval` marker so accuracy regressions are caught automatically as the datasets grow.


## How to reproduce

```
# inside the agent-api container (has DB + Bedrock model-key creds)
python -m evals.accuracy.runner --report
python -m evals.accuracy.selftest   # proves the graders bite on known-bad input
```
