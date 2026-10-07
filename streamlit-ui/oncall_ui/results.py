"""Digging answers, tokens and metadata out of execute / execution payloads.

A visual-workflow execute response is keyed by node id, e.g.
``{workflow_name, "agent_…": {final_answer, …}, "cloudwatch_tool_…": {output}}``
plus top-level token counters. Ported from the React client's extractors.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterator, List, Optional


def _containers(data: Any) -> List[dict]:
    if not isinstance(data, dict):
        return []
    out = [data]
    for key in ("result", "results", "output"):
        if isinstance(data.get(key), dict):
            out.append(data[key])
    return out


def _node_values(data: Any) -> Iterator[dict]:
    for c in _containers(data):
        for v in c.values():
            if isinstance(v, dict):
                yield v


def extract_final_answer(data: Any) -> str:
    if not data:
        return ""
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return ""
    if isinstance(data.get("final_answer"), str) and data["final_answer"]:
        return data["final_answer"]
    # Prefer the agent / ReAct node's final answer.
    for v in _node_values(data):
        if isinstance(v.get("final_answer"), str) and v["final_answer"]:
            return v["final_answer"]
    if isinstance(data.get("output"), str) and data["output"].strip():
        return data["output"]
    # Fallback: the most substantial node `output` (e.g. a CloudWatch report).
    best = ""
    for v in _node_values(data):
        out = v.get("output")
        if isinstance(out, str) and len(out) > len(best):
            best = out
    return best


def _extract_list(data: Any, key: str) -> list:
    if not isinstance(data, dict):
        return []
    if isinstance(data.get(key), list):
        return data[key]
    for v in _node_values(data):
        if isinstance(v.get(key), list):
            return v[key]
    return []


def extract_privacy_redactions(data: Any) -> list:
    """UI-safe ``[{type, placeholder, preview}]`` rows (never raw values)."""
    return _extract_list(data, "privacy_redactions")


def extract_selected_skills(data: Any) -> list:
    return _extract_list(data, "selected_skills")


def extract_tokens(data: Any) -> Optional[Dict[str, int]]:
    if not isinstance(data, dict):
        return None
    if any(data.get(k) is not None for k in ("input_tokens", "output_tokens", "total_tokens")):
        inp, out = data.get("input_tokens") or 0, data.get("output_tokens") or 0
        return {"input": inp, "output": out, "total": data.get("total_tokens") or inp + out}
    for v in _node_values(data):
        if v.get("input_tokens") is not None or v.get("output_tokens") is not None:
            inp, out = v.get("input_tokens") or 0, v.get("output_tokens") or 0
            return {"input": inp, "output": out, "total": v.get("total_tokens") or inp + out}
    return None


def extract_node_field(data: Any, field: str) -> Any:
    """First node result (under ``results``) carrying a truthy ``field``."""
    nodes = data.get("results") if isinstance(data, dict) else None
    if not isinstance(nodes, dict):
        return None
    for v in nodes.values():
        if isinstance(v, dict) and v.get(field):
            return v[field]
    return None


_DQ_TEXT = re.compile(r"'text':\s*\"([\s\S]*?)\"\s*[,}]")
_SQ_TEXT = re.compile(r"'text':\s*'([\s\S]*?)'\s*[,}]")


def clean_llm_text(text: Any) -> str:
    """Strip the Python-repr content-block artefact from older LLM output."""
    if not text or not isinstance(text, str):
        return text or ""
    if not text.lstrip().startswith("[{"):
        return text
    dq = _DQ_TEXT.findall(text)
    if dq:
        return "".join(dq).replace("\\n", "\n").replace("\\'", "'").replace('\\"', '"')
    sq = _SQ_TEXT.findall(text)
    if sq:
        return "".join(sq).replace("\\n", "\n")
    try:
        as_json = (text.replace("'", '"').replace("None", "null")
                   .replace("True", "true").replace("False", "false"))
        blocks = json.loads(as_json)
        if isinstance(blocks, list):
            parts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
            if parts:
                return "".join(parts)
    except ValueError:
        pass
    return text
