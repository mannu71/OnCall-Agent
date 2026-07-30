#!/usr/bin/env python3
"""Call-graph accuracy eval for codegraph.

Indexes the ambiguity fixture and checks the resolved CALLS edges against a
FIXED ground truth: each save() must call its OWN class's validate(), and both
must be present. Reports precision and recall and exits non-zero when either
falls below threshold.

Ground truth is hardcoded on purpose. The previous version computed
`expected = len(got)` — the number of edges the engine happened to return —
which made recall identical to precision by construction and meant a resolver
that emitted only ONE of the two correct edges scored a perfect 1.0/1.0. A
recall metric that cannot observe a missing edge measures nothing.

Run inside the codegraph builder image (has the binary + python + grammars):
  docker run --rm -v <repo>/codegraph/test:/test:ro codegraph:builder \
      python3 /test/accuracy/eval.py
"""
import json
import os
import subprocess
import sys

BIN = "/build/build/codegraph"
ENV = dict(os.environ, CODEGRAPH_DB="/tmp/ev.db", CODEGRAPH_TS_SO="/languages.so",
           CG_TS_SO="/languages.so")

# Both thresholds must hold. Anything less means the resolver is either wiring
# save() to the wrong class's validate() (precision) or dropping an edge
# outright (recall).
MIN_PRECISION = 1.0
MIN_RECALL = 1.0


def ground_truth(project):
    """The only two CALLS->validate edges that svc.py can legitimately produce."""
    return {
        (f"{project}.svc.UserService.save", f"{project}.svc.UserService.validate"),
        (f"{project}.svc.OrderService.save", f"{project}.svc.OrderService.validate"),
    }


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

    expected = ground_truth(project)
    hits = got & expected

    precision = len(hits) / len(got) if got else 0.0
    recall = len(hits) / len(expected)

    ok = precision >= MIN_PRECISION and recall >= MIN_RECALL
    print(json.dumps({
        "project": project,
        "edges_to_validate": len(got),
        "correct": len(hits),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "missing": sorted(expected - got),
        "spurious": sorted(got - expected),
    }, indent=2))

    if ok:
        print("ACCURACY PASS precision=%.2f recall=%.2f" % (precision, recall))
        return 0
    print("ACCURACY FAIL precision=%.2f (min %.2f) recall=%.2f (min %.2f)"
          % (precision, MIN_PRECISION, recall, MIN_RECALL), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
