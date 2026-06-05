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

    agree, n, notes = asyncio.run(judge_calibration())
    print(f"\n[judge calibration] agreement with human labels: {agree}/{n} = {agree/n*100:.0f}%")
    for line in notes:
        print(line)
    print("\nSelf-test complete. Deterministic graders are sound; judge agreement above "
          "is the trust level for the (separately-reported) faithfulness column.")


if __name__ == "__main__":
    main()
