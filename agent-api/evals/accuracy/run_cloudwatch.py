"""Run the CloudWatch synthesis suite against injected evidence bundles.

Each case feeds a recorded evidence bundle straight into the real synthesis
function (analyze_cloudwatch_with_llm) via a minimal fake active_executions
workflow, so the LLM runs but no live AWS fetch is needed (reproducible).
Deterministic graders: schema validity, ID-grounding, severity match.
Judge: narrative faithfulness (reported separately).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

from evals.accuracy import _bootstrap  # noqa: F401
from evals.accuracy import graders
from evals.accuracy.judge import judge_faithfulness

HERE = os.path.dirname(__file__)
CASES_PATH = os.path.join(HERE, "datasets", "cloudwatch_cases.jsonl")
EVIDENCE_DIR = os.path.join(HERE, "fixtures", "cloudwatch_evidence")

CW_SYNTH_MODEL = os.getenv("CW_SYNTH_MODEL", "anthropic.claude-haiku-4-5-20251001-v1:0")
_EXEC_ID = "eval-cw"


def _load_cases() -> List[Dict[str, Any]]:
    cases = []
    with open(CASES_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def _fake_active_executions() -> Dict[str, Dict[str, Any]]:
    """Minimal workflow with one inline-config language_model node (bedrock)."""
    return {
        _EXEC_ID: {
            "workflow": {
                "nodes": [
                    {"id": "lm1", "type": "language_model",
                     "data": {"provider": "bedrock", "model": CW_SYNTH_MODEL}},
                ],
                "edges": [],
            }
        }
    }


async def run_cloudwatch_suite() -> List[Dict[str, Any]]:
    from app.workflow.executor.cloudwatch_analysis import analyze_cloudwatch_with_llm

    cases = _load_cases()
    out: List[Dict[str, Any]] = []

    for c in cases:
        with open(os.path.join(EVIDENCE_DIR, c["evidence_ref"]), "r", encoding="utf-8") as fh:
            evidence = json.load(fh)
        evidence_blob = json.dumps(evidence, default=str)
        t0 = time.time()
        text, model, structured, in_tok, out_tok = await analyze_cloudwatch_with_llm(
            active_executions=_fake_active_executions(),
            execution_id=_EXEC_ID,
            raw_result=evidence,
            analysis_type="investigation",
            log_groups=c.get("log_groups", ["/aws/lambda/kyc-auth", "/aws/lambda/kyc-core"]),
            time_range="2h",
            alerts=[],
            focus=c.get("focus"),
        )
        latency = round(time.time() - t0, 3)
        text = text or ""

        s_schema, d_schema = graders.grade_schema(structured)
        s_ground, d_ground = graders.grade_id_grounding(text, evidence_blob)
        s_sev, d_sev = graders.grade_severity(structured, c["expected"]["severity"])

        try:
            verdict = await judge_faithfulness(
                model_output=text, evidence=evidence_blob,
                requirements=c.get("judge_context", ""))
        except Exception as exc:  # noqa: BLE001
            verdict = {"score": 0.0, "verdict": "error", "reasoning": str(exc)[:120],
                       "input_tokens": 0, "output_tokens": 0}

        common = {"feature": "cloudwatch", "id": c["id"], "latency_s": latency,
                  "synth_tokens": in_tok + out_tok, "model": model,
                  "synthesized": bool(structured)}
        out.append({**common, "metric": "schema", "objective": True,
                    "score": s_schema, "diagnostic": d_schema})
        out.append({**common, "metric": "id_grounding", "objective": True,
                    "score": s_ground, "diagnostic": d_ground})
        out.append({**common, "metric": "severity", "objective": False,
                    "score": s_sev, "diagnostic": d_sev})
        out.append({**common, "metric": "judge_faithfulness", "objective": False,
                    "score": verdict["score"], "diagnostic": verdict["reasoning"],
                    "judge_tokens": verdict.get("input_tokens", 0) + verdict.get("output_tokens", 0)})
    return out


if __name__ == "__main__":
    import asyncio
    rows = asyncio.run(run_cloudwatch_suite())
    for r in rows:
        flag = "OK " if r["score"] >= 0.999 else ("~~ " if r["score"] >= 0.5 else "XX ")
        print(f"{flag}{r['id']:<22} {r['metric']:<18} {r['score']:.2f}  {r['diagnostic']}")
    obj = [r for r in rows if r.get("objective")]
    mean = sum(r["score"] for r in obj) / len(obj) if obj else 0.0
    print(f"\ncloudwatch mean OBJECTIVE score: {mean:.4f}  ({len(obj)} checks)")
