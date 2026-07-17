"""Self-validation: prove the harness itself is trustworthy before trusting scores.

Two parts:
  1. Negative controls (deterministic, no Bedrock) — feed each grader a KNOWN-BAD
     input and assert it does NOT return 1.0, and a known-good input and assert it
     does. A grader that passes garbage is worse than no grader.
  2. Judge calibration (Bedrock) — a small hand-labeled set of faithful/unfaithful
     narratives; report judge-vs-label agreement so the judge's own reliability is
     measured, not assumed.

Run:  python -m evals.accuracy.selftest
"""
from __future__ import annotations

import asyncio
from typing import List, Tuple

from evals.accuracy import _bootstrap  # noqa: F401
from evals.accuracy import graders

_EVIDENCE = (
    '{"drilldown":[{"events":["correlationId=7f3a9c2e-1b4d-4e8a-9c6f-2d5e8a1b3c4d '
    'NullPointerException in AuthService.validate"]}]}'
)


def negative_controls() -> Tuple[int, int, List[str]]:
    """Return (passed, total, failures). Each check asserts the grader bites."""
    checks = []

    # find: good = 1.0, wrong line = 0.5, wrong file = 0.0
    good = {"found": True, "results": [{"file": "auth.py", "line": 8, "kind": "function"}]}
    checks.append(("find good==1.0", graders.grade_find(good, {"file": "auth.py", "line": 8, "kind": "function"})[0] == 1.0))
    wrong_line = {"found": True, "results": [{"file": "auth.py", "line": 99, "kind": "function"}]}
    checks.append(("find wrong-line<1.0", graders.grade_find(wrong_line, {"file": "auth.py", "line": 8, "kind": "function"})[0] < 1.0))
    wrong_file = {"found": True, "results": [{"file": "other.py", "line": 8, "kind": "function"}]}
    checks.append(("find wrong-file==0.0", graders.grade_find(wrong_file, {"file": "auth.py", "line": 8, "kind": "function"})[0] == 0.0))

    # id-grounding: planted fake trace id NOT in evidence must drop below 1.0
    halluc = "Root cause traced via traceId=1-12345678-9999999999999999aaaaaaaa and correlationId=7f3a9c2e-1b4d-4e8a-9c6f-2d5e8a1b3c4d"
    checks.append(("grounding hallucination<1.0", graders.grade_id_grounding(halluc, _EVIDENCE)[0] < 1.0))
    clean = "Investigate correlationId=7f3a9c2e-1b4d-4e8a-9c6f-2d5e8a1b3c4d"
    checks.append(("grounding clean==1.0", graders.grade_id_grounding(clean, _EVIDENCE)[0] == 1.0))

    # schema: missing required field must be 0.0
    missing = {"headline": "x", "severity": "high", "key_findings": ["a"], "recommended_actions": ["b"], "confidence": "high"}
    checks.append(("schema missing-field==0.0", graders.grade_schema(missing)[0] == 0.0))
    bad_enum = {"headline": "x", "severity": "spicy", "key_findings": ["a"], "primary_hypothesis": "y", "recommended_actions": ["b"], "confidence": "high"}
    checks.append(("schema bad-enum==0.0", graders.grade_schema(bad_enum)[0] == 0.0))
    full = {"headline": "x", "severity": "high", "key_findings": ["a"], "primary_hypothesis": "y", "recommended_actions": ["b"], "confidence": "high"}
    checks.append(("schema valid==1.0", graders.grade_schema(full)[0] == 1.0))

    # trace: a missing expected edge must drop F1 below 1.0
    miss = {"edges": [{"from": "a", "to": "b", "confidence": "resolved"}]}
    checks.append(("trace missing-edge<1.0", graders.grade_trace(miss, [{"from": "a", "to": "b"}, {"from": "a", "to": "c"}])[0] < 1.0))

    # severity: adjacent = 0.5, far = 0.0
    checks.append(("severity adjacent==0.5", graders.grade_severity({"severity": "high"}, "critical")[0] == 0.5))
    checks.append(("severity far==0.0", graders.grade_severity({"severity": "none"}, "critical")[0] == 0.0))

    # ── trajectory graders (agent-behaviour predicates) ──────────────────────
    # tool_selection: right first move == 1.0; wrong first move / forbidden call
    # drop below 1.0. Input is a list of turns (each turn = parallel tool names).
    corr_spec = {"first_any": ["cloudwatch_correlate_logs"],
                 "must_not_call": ["cloudwatch_watch_logs"]}
    good_corr = [["cloudwatch_correlate_logs"], ["cloudwatch_search_logs"]]
    checks.append(("selection good==1.0", graders.grade_tool_selection(good_corr, corr_spec)[0] == 1.0))
    bad_first = [["cloudwatch_search_logs"], ["cloudwatch_correlate_logs"]]
    checks.append(("selection wrong-first<1.0", graders.grade_tool_selection(bad_first, corr_spec)[0] < 1.0))
    forbidden_called = [["cloudwatch_correlate_logs"], ["cloudwatch_watch_logs"]]
    checks.append(("selection must-not-call<1.0", graders.grade_tool_selection(forbidden_called, corr_spec)[0] < 1.0))
    # alternation: a '|' pattern matches either alternative
    alt_spec = {"first_any": ["codegraph__find_symbol|codegraph__search_graph"]}
    checks.append(("selection alternation==1.0",
                   graders.grade_tool_selection([["codegraph__search_graph"]], alt_spec)[0] == 1.0))

    # protocol: get_body must precede a citation; forbidden re-scan; vacuous pass.
    proto_spec = {"requires_before": [["codegraph__get_code_snippet", "edit_file"]],
                  "forbidden": ["cloudwatch_watch_logs"]}
    read_then_edit = [["codegraph__find_symbol"], ["codegraph__get_code_snippet"], ["edit_file"]]
    checks.append(("protocol read-before-edit==1.0", graders.grade_protocol(read_then_edit, proto_spec)[0] == 1.0))
    edit_no_read = [["codegraph__find_symbol"], ["edit_file"]]
    checks.append(("protocol edit-without-read<1.0", graders.grade_protocol(edit_no_read, proto_spec)[0] < 1.0))
    vacuous = [["codegraph__find_symbol"]]  # never edits → requires_before vacuously ok
    checks.append(("protocol vacuous==1.0", graders.grade_protocol(vacuous, proto_spec)[0] == 1.0))
    rescan = [["codegraph__find_symbol"], ["cloudwatch_watch_logs"]]
    checks.append(("protocol forbidden<1.0", graders.grade_protocol(rescan, proto_spec)[0] < 1.0))
    checks.append(("protocol max_calls<1.0",
                   graders.grade_protocol([["a"], ["b"], ["c"]], {"max_calls": 2})[0] < 1.0))

    # final_answer: required mention present == 1.0; missing < 1.0; forbidden word.
    ans_spec = {"must_contain": ["auth.py", "145"], "must_not_contain": ["critical"]}
    good_ans = "The NPE is thrown in auth.py at line 145 during token validation."
    checks.append(("answer good==1.0", graders.grade_final_answer(good_ans, ans_spec)[0] == 1.0))
    missing_file = "The error happens at line 145 somewhere."
    checks.append(("answer missing-mention<1.0", graders.grade_final_answer(missing_file, ans_spec)[0] < 1.0))
    forbidden_word = "auth.py line 145 — this is a CRITICAL outage."
    checks.append(("answer forbidden-word<1.0", graders.grade_final_answer(forbidden_word, ans_spec)[0] < 1.0))
    clean_spec = {"must_not_contain": ["critical", "outage"]}
    checks.append(("answer clean==1.0",
                   graders.grade_final_answer("No issues found; coverage was full.", clean_spec)[0] == 1.0))

    failures = [name for name, ok in checks if not ok]
    return len(checks) - len(failures), len(checks), failures


# Hand-labeled calibration set: (narrative, evidence, expected_faithful)
_CALIBRATION = [
    ("NullPointerException in AuthService.validate, correlationId=7f3a9c2e-1b4d-4e8a-9c6f-2d5e8a1b3c4d, critical.",
     _EVIDENCE, True),
    ("Database deadlock in OrderService caused the outage; traceId=1-aaaa-bbbb.",
     _EVIDENCE, False),  # fabricates a different error + unknown id
    ("Nothing of concern: the logs were checked and no errors or anomalies were found.",
     '{"data_quality":{"coverage":"full"},"patterns":{"unique_patterns":[]},"anomalies":{"anomalies":[]}}', True),
    ("Critical outage with 5000 errors and SEV1 paging required.",
     '{"data_quality":{"coverage":"full"},"patterns":{"unique_patterns":[]},"anomalies":{"anomalies":[]}}', False),  # invents errors
]


def fixture_fidelity() -> Tuple[int, int, List[str]]:
    """Run the REAL summarisers over each cw_recorded fixture; planted IDs/findings
    must survive. Fails fast (and loudly) if a summariser's expected input shape
    drifts, instead of silently producing empty tool results mid-trajectory.
    Imports app code, so this runs in-container alongside the trajectory suite.
    """
    import json as _json
    from app.workflow.tools.cloudwatch_summarizers import (
        summarise_patterns, summarise_anomalies, summarise_correlation,
    )
    from evals.accuracy.fakes_cloudwatch import FakeCloudWatchRecording

    checks: List[Tuple[str, bool]] = []

    npe = FakeCloudWatchRecording.load("cw_npe_recorded.json")
    pat = _json.dumps(summarise_patterns(npe.response_for("analyze_log_patterns", {})))
    checks.append(("npe patterns keep correlationId", "7f3a9c2e-1b4d-4e8a-9c6f-2d5e8a1b3c4d" in pat))
    checks.append(("npe patterns name auth.py", "auth.py" in pat))
    anom = summarise_anomalies(npe.response_for("detect_anomalies", {}))
    checks.append(("npe anomaly critical kept", any(a.get("severity") == "critical" for a in anom.get("anomalies", []))))
    corr = _json.dumps(summarise_correlation(npe.response_for("correlate_logs", {})))
    checks.append(("npe correlation timeline keeps id", "7f3a9c2e-1b4d-4e8a-9c6f-2d5e8a1b3c4d" in corr))

    clean = FakeCloudWatchRecording.load("cw_clean_recorded.json")
    clean_pat = summarise_patterns(clean.response_for("analyze_log_patterns", {}))
    checks.append(("clean patterns empty", not clean_pat.get("unique_patterns")))
    checks.append(("clean evidence_grade none", clean_pat.get("evidence_grade") == "none"))

    thr = FakeCloudWatchRecording.load("cw_throttle_recorded.json")
    thr_pat = _json.dumps(summarise_patterns(thr.response_for("analyze_log_patterns", {})))
    checks.append(("throttle keeps sessionId", "sess-4d8f1a2b" in thr_pat))

    failures = [name for name, ok in checks if not ok]
    return len(checks) - len(failures), len(checks), failures


async def judge_calibration() -> Tuple[int, int, List[str]]:
    from evals.accuracy.judge import judge_faithfulness
    agree = 0
    notes: List[str] = []
    for i, (narrative, evidence, expected_faithful) in enumerate(_CALIBRATION):
        v = await judge_faithfulness(model_output=narrative, evidence=evidence,
                                     requirements="Faithful = supported by evidence, nothing fabricated.")
        judged_faithful = v["score"] >= 0.75
        ok = judged_faithful == expected_faithful
        agree += int(ok)
        notes.append(f"  case {i}: expected={'faithful' if expected_faithful else 'unfaithful'} "
                     f"judge_score={v['score']:.2f} -> {'AGREE' if ok else 'DISAGREE'}")
    return agree, len(_CALIBRATION), notes


def main() -> None:
    passed, total, failures = negative_controls()
    print(f"\n[negative controls] {passed}/{total} graders bite correctly")
    for f in failures:
        print(f"   FAILED: {f}")
    assert not failures, f"grader self-test failed: {failures}"

    fpassed, ftotal, ffailures = fixture_fidelity()
    print(f"\n[fixture fidelity] {fpassed}/{ftotal} cw_recorded fixtures survive the real summarisers")
    for f in ffailures:
        print(f"   FAILED: {f}")
    assert not ffailures, f"fixture fidelity failed: {ffailures}"

    agree, n, notes = asyncio.run(judge_calibration())
    print(f"\n[judge calibration] agreement with human labels: {agree}/{n} = {agree/n*100:.0f}%")
    for line in notes:
        print(line)
    print("\nSelf-test complete. Deterministic graders are sound; judge agreement above "
          "is the trust level for the (separately-reported) faithfulness column.")


if __name__ == "__main__":
    main()
