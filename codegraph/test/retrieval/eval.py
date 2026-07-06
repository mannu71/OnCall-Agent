#!/usr/bin/env python3
"""Retrieval accuracy eval for codegraph's hybrid (BM25 + vector) search_graph.

Runs the curated query set in queries.json against a fresh index of
./fixture twice — once with CG_SEARCH_FUSION=off (legacy BM25-only ranking)
and once with fusion on (RRF hybrid + label/test-path re-rank + per-file
diversity cap) — and reports Precision@5, Precision@10, Recall@10 and MRR
for each, plus the delta.

Informational, like test/accuracy/eval.py: always exits 0. Prints a FAIL
line (without failing the run) if hybrid regresses baseline on any metric,
so a regression is visible in CI logs without gating the build on a small,
hand-built fixture.

Run inside the codegraph builder image (has the binary + python + grammars):
  docker run --rm -v <repo>/codegraph/test:/test:ro codegraph:builder \
      python3 /test/retrieval/eval.py
"""
import json
import os
import subprocess

BIN = "/build/build/codegraph"
HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixture")
QUERIES_PATH = os.path.join(HERE, "queries.json")
TOP_K = 10


def run_session(fusion_on, db_path):
    """Spawn a fresh `codegraph serve`, index the fixture, run every query,
    and return {query: [qualified_name, ...]} (results in rank order)."""
    env = dict(os.environ, CODEGRAPH_DB=db_path, CODEGRAPH_TS_SO="/languages.so",
               CG_TS_SO="/languages.so")
    if not fusion_on:
        env["CG_SEARCH_FUSION"] = "off"
    else:
        env.pop("CG_SEARCH_FUSION", None)

    p = subprocess.Popen([BIN, "serve"], env=env, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True)

    def call(req):
        p.stdin.write(json.dumps(req) + "\n")
        p.stdin.flush()
        return json.loads(p.stdout.readline())

    call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    idx = call({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "index_repository", "arguments": {"repo_path": FIXTURE}}})
    project = json.loads(idx["result"]["content"][0]["text"]).get("project", "fixture")

    with open(QUERIES_PATH) as f:
        queries = json.load(f)

    results = {}
    for i, q in enumerate(queries):
        r = call({"jsonrpc": "2.0", "id": 3 + i, "method": "tools/call", "params": {
            "name": "search_graph",
            "arguments": {"project": project, "query": q["query"], "limit": TOP_K}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        hits = [row.get("qualified_name", "") for row in payload.get("results", [])]
        results[q["query"]] = hits

    p.stdin.close()
    p.wait(timeout=10)
    return project, queries, results


def score(project, queries, results):
    """Compute per-query and averaged Precision@5, Precision@10, Recall@10, MRR."""
    per_query = []
    for q in queries:
        relevant = {"%s.%s" % (project, rel) for rel in q["relevant"]}
        hits = results.get(q["query"], [])
        hits5 = hits[:5]
        hits10 = hits[:TOP_K]
        p5 = sum(1 for h in hits5 if h in relevant) / 5.0
        p10 = sum(1 for h in hits10 if h in relevant) / float(TOP_K)
        recall10 = (sum(1 for h in hits10 if h in relevant) / len(relevant)) if relevant else 0.0
        rr = 0.0
        for rank, h in enumerate(hits10, start=1):
            if h in relevant:
                rr = 1.0 / rank
                break
        per_query.append({
            "query": q["query"], "p@5": p5, "p@10": p10, "recall@10": recall10, "rr": rr,
        })

    n = len(per_query) or 1
    avg = {
        "p@5": sum(pq["p@5"] for pq in per_query) / n,
        "p@10": sum(pq["p@10"] for pq in per_query) / n,
        "recall@10": sum(pq["recall@10"] for pq in per_query) / n,
        "mrr": sum(pq["rr"] for pq in per_query) / n,
    }
    return per_query, avg


def main():
    baseline_project, queries, baseline_results = run_session(
        fusion_on=False, db_path="/tmp/retrieval_baseline.db")
    hybrid_project, _, hybrid_results = run_session(
        fusion_on=True, db_path="/tmp/retrieval_hybrid.db")

    _, baseline_avg = score(baseline_project, queries, baseline_results)
    _, hybrid_avg = score(hybrid_project, queries, hybrid_results)

    print(json.dumps({
        "queries": len(queries),
        "baseline": {k: round(v, 4) for k, v in baseline_avg.items()},
        "hybrid": {k: round(v, 4) for k, v in hybrid_avg.items()},
        "delta": {k: round(hybrid_avg[k] - baseline_avg[k], 4) for k in baseline_avg},
    }, indent=2))

    print("\n%-12s %10s %10s %10s" % ("metric", "baseline", "hybrid", "delta"))
    for k in ("p@5", "p@10", "recall@10", "mrr"):
        print("%-12s %10.4f %10.4f %+10.4f" % (k, baseline_avg[k], hybrid_avg[k],
                                               hybrid_avg[k] - baseline_avg[k]))

    regressions = [k for k in baseline_avg if hybrid_avg[k] < baseline_avg[k] - 1e-9]
    if regressions:
        print("\nFAIL (informational): hybrid regressed baseline on: %s" %
              ", ".join(regressions))
    else:
        print("\nPASS (informational): hybrid did not regress baseline on any metric")


if __name__ == "__main__":
    main()
