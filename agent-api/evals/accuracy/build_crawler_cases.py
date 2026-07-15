"""Derive Code Crawler ground truth from the fixture repo via Python ``ast``.

The expected (symbol -> file, line, kind) and intra-repo call edges are
computed directly from the fixture source, so the ground truth is correct by
construction and "100%" means the engine matches the AST exactly. Backend-
agnostic: consumed by the codegraph accuracy suite (run_codegraph).
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import tempfile
from typing import Any, Dict, List

HERE = os.path.dirname(__file__)
SAMPLE_REPO_DIR = os.path.join(HERE, "fixtures", "sample_repo")
DATASET_PATH = os.path.join(HERE, "datasets", "code_cases.jsonl")
DEFAULT_REPO_NAME = "eval-fixture-repo"


def _sync_fixture_repo() -> str:
    """Copy the fixture into a writable repos dir; return that dir (repos_base_path)."""
    base = os.getenv("EVAL_REPOS_DIR") or os.path.join(tempfile.gettempdir(), "eval_repos")
    target = os.path.join(base, DEFAULT_REPO_NAME)
    if os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(SAMPLE_REPO_DIR, target)
    return base


def _defs_in_file(path: str, rel: str) -> List[Dict[str, Any]]:
    """Return every top-level function/class and method definition in a file."""
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    out: List[Dict[str, Any]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append({"symbol": node.name, "file": rel, "line": node.lineno, "kind": "function"})
        elif isinstance(node, ast.ClassDef):
            out.append({"symbol": node.name, "file": rel, "line": node.lineno, "kind": "class"})
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append({"symbol": sub.name, "file": rel, "line": sub.lineno, "kind": "method"})
    return out


def _callees(func_node: ast.AST, known: set) -> set:
    """Names called inside *func_node* that are defined somewhere in the repo."""
    found: set = set()
    for n in ast.walk(func_node):
        if isinstance(n, ast.Call):
            fn = n.func
            name = None
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                name = fn.attr
            if name and name in known:
                found.add(name)
    return found


def _edges_in_file(path: str, known: set) -> List[Dict[str, str]]:
    """Intra-repo (caller -> callee) edges for functions/methods in a file."""
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    edges: List[Dict[str, str]] = []

    def handle(fn_node: ast.AST, caller: str) -> None:
        for callee in sorted(_callees(fn_node, known)):
            if callee != caller:
                edges.append({"from": caller, "to": callee})

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            handle(node, node.name)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    handle(sub, sub.name)
    return edges


def derive_crawler_cases(repo_name: str = DEFAULT_REPO_NAME,
                         sample_dir: str = SAMPLE_REPO_DIR) -> List[Dict[str, Any]]:
    """Build the full crawler case list (find + trace + body) from the fixture."""
    files = sorted(f for f in os.listdir(sample_dir) if f.endswith(".py"))
    all_defs: List[Dict[str, Any]] = []
    known: set = set()
    for f in files:
        defs = _defs_in_file(os.path.join(sample_dir, f), f)
        all_defs.extend(defs)
        known.update(d["symbol"] for d in defs)

    # caller -> {callees}
    edges: List[Dict[str, str]] = []
    for f in files:
        edges.extend(_edges_in_file(os.path.join(sample_dir, f), known))
    callees_by: Dict[str, List[Dict[str, str]]] = {}
    for e in edges:
        callees_by.setdefault(e["from"], []).append(e)

    cases: List[Dict[str, Any]] = []

    # find cases — one per definition
    for d in all_defs:
        cases.append({
            "id": f"find-{d['symbol']}",
            "op": "find",
            "args": {"symbol": d["symbol"], "repo": repo_name, "kind": d["kind"]},
            "expected": {"file": d["file"], "line": d["line"], "kind": d["kind"]},
        })

    # body cases — runner finds the symbol first to obtain a handle, then grades
    for d in all_defs:
        if d["kind"] in ("function", "method"):
            cases.append({
                "id": f"body-{d['symbol']}",
                "op": "body",
                "args": {"symbol": d["symbol"], "repo": repo_name},
                "expected": {"file": d["file"], "line": d["line"]},
            })

    # trace cases — callees direction for every symbol with >=1 intra-repo callee
    for caller, ce in sorted(callees_by.items()):
        cases.append({
            "id": f"trace-callees-{caller}",
            "op": "trace",
            "args": {"symbol": caller, "repo": repo_name, "direction": "callees", "depth": 1},
            "expected_edges": ce,
        })

    return cases


def main() -> None:
    cases = derive_crawler_cases()
    os.makedirs(os.path.dirname(DATASET_PATH), exist_ok=True)
    with open(DATASET_PATH, "w", encoding="utf-8") as fh:
        for c in cases:
            fh.write(json.dumps(c) + "\n")
    n_find = sum(1 for c in cases if c["op"] == "find")
    n_body = sum(1 for c in cases if c["op"] == "body")
    n_trace = sum(1 for c in cases if c["op"] == "trace")
    print(f"wrote {len(cases)} cases -> {DATASET_PATH}  (find={n_find} body={n_body} trace={n_trace})")


if __name__ == "__main__":
    main()
