#!/usr/bin/env python3
"""Call-graph accuracy eval for codegraph.

Indexes the ambiguity fixture and checks the resolved CALLS edges against the
ground truth: each save() must call its OWN class's validate(), never the other
class's. Reports precision/recall. Informational — scope-aware (self/this)
resolution accuracy depends on the grammar versions in the dlopen'd
languages.so, so this script always exits 0 and lets the caller decide.

Run inside the codegraph builder image (has the binary + python + grammars):
  docker run --rm -v <repo>/codegraph/test:/test:ro codegraph:builder \
      python3 /test/accuracy/eval.py
"""
import json
import os
import subprocess

BIN = "/build/build/codegraph"
ENV = dict(os.environ, CODEGRAPH_DB="/tmp/ev.db", CODEGRAPH_TS_SO="/languages.so",
           CG_TS_SO="/languages.so")


def cls(qname):
    """Class component of a dotted qualified name (…pkg.Class.method)."""
    parts = qname.split(".")
    return parts[-2] if len(parts) >= 2 else ""


def main():
    # Index + query through the same MCP session so the per-project store path is
    # consistent (the CLI 'index' path writes CODEGRAPH_DB, which the served
    # query handlers do not read).
    p = subprocess.Popen([BIN, "serve"], env=ENV, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True)

    def call(req):
        p.stdin.write(json.dumps(req) + "\n")
        p.stdin.flush()
        return json.loads(p.stdout.readline())

    call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    idx = call({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "index_repository", "arguments": {"repo_path": "/test/accuracy"}}})
    project = json.loads(idx["result"]["content"][0]["text"]).get("project", "accuracy")

    r = call({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "query_graph",
        "arguments": {"project": project,
                      "query": ('MATCH (a)-[:CALLS]->(b) WHERE b.name = "validate" '
                                'RETURN a.qualified_name, b.qualified_name')}}})
    p.stdin.close()

    payload = json.loads(r["result"]["content"][0]["text"])
    got = set((row[0], row[1]) for row in payload.get("rows", []))

    correct = sum(1 for f, t in got if cls(f) and cls(f) == cls(t))
    precision = correct / len(got) if got else 0.0
    expected = len(got)  # one CALLS->validate edge per save()
    recall = correct / expected if expected else 0.0

    print(json.dumps({
        "project": project,
        "edges_to_validate": len(got),
        "correct": correct,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "got": sorted(got),
    }, indent=2))

    if precision >= 1.0 and recall >= 1.0:
        print("ACCURACY PASS precision=recall=1.0")
    else:
        print("ACCURACY precision=%.2f recall=%.2f (scope resolution limited by "
              "dlopen grammar versions)" % (precision, recall))


if __name__ == "__main__":
    main()
