# Code Analyzer Eval Harness — Phase 0

Dual-metric accuracy + token-efficiency gate for the code analyzer tools.
Runs only with `pytest -m eval` (the marker is reserved in `pytest.ini`).

## Why

You can't optimize what you don't measure. This harness publishes two
numbers per question and one headline number per run:

- **Accuracy** — did the tool return a result that satisfies the expected
  validator? Aggregated as per-category and per-language F1.
- **Tokens per correct answer** — total tokens (args + result, estimated
  at 4 chars/token to match `app/core/prompt_caching.py`) divided by
  number of correct answers.
- **Headline:** `answers_per_kilo_token = correct / (total_tokens / 1000)`.

Every PR that touches the analyzer must show this number is non-decreasing.

## Running

```bash
# Default project test run skips eval (see pytest.ini addopts)
pytest                                # eval excluded
pytest -m eval                        # run only the eval

# Enforce CI thresholds (set after baseline is published)
ECC_EVAL_MIN_ACCURACY=0.70 \
ECC_EVAL_MIN_ANSWERS_PER_KT=2.0 \
  pytest -m eval
```

The eval needs a real, indexed repo:

1. Postgres with pgvector reachable via the project's `app.core.database`
   configuration (env vars or `.env`).
2. The repos referenced in JSONL files (`agent-api`, etc.) must already
   be indexed via `index_repo()`.

If Postgres is unreachable or the repo is not indexed, individual
questions are recorded as `infra_error` and the per-test status is
`SKIPPED`. The aggregate report still publishes, marking those rows.

## Output

After the run:

- **Terminal summary** — printed by `tests/eval/conftest.py` via the
  `pytest_terminal_summary` hook. Shows overall + by-category +
  by-language breakdown.
- **JSON report** — written to `tests/eval/last_run_report.json`. Diff
  this file across PRs to track trends.

## Adding questions

Append one JSON object per line to a file under `ground_truth/`. One
file per language is the convention (`python.jsonl`, `typescript.jsonl`,
etc.).

### Schema

```jsonc
{
  "id":       "py-def-001",                 // stable, unique
  "category": "definition",                 // see below
  "language": "python",
  "repo":     "agent-api",                  // must match an indexed repo
  "question": "Where is foo defined?",      // human-readable
  "tool":     "get_function",               // see below
  "args":     { "name": "foo", "repo": "agent-api" },
  "expected": { /* validator — see below */ }
}
```

### Categories (eight, mirroring the plan)

| Category            | Meaning                                                  |
|---------------------|----------------------------------------------------------|
| `definition`        | Where is symbol X defined?                               |
| `references`        | Where is X referenced?                                   |
| `callers`           | Who calls X?                                             |
| `callees`           | What does X call? (no current tool — future)             |
| `semantic`          | Find code about a concept (fuzzy intent)                 |
| `cross_file`        | Caller/definition span different files                   |
| `ambiguous`         | Multiple symbols share a name — which wins?              |
| `refactor_survival` | Symbol moved/renamed — does the index still resolve it?  |

### Tools (the current 5-tool surface)

`search_code`, `get_function`, `get_callers`, `get_recent_changes`, `get_file_context`.

### Validator kinds

**`single_dict`** — tool returned a single object, every named field passes its predicate:
```json
"expected": {
  "kind": "single_dict",
  "must_match": {
    "name":      {"equals":   "foo"},
    "file_path": {"contains": "core/foo.py"}
  }
}
```

**`list_contains`** — tool returned a list, *at least one* row passes:
```json
"expected": {
  "kind": "list_contains",
  "must_have_one": {"file_path": {"contains": "transport/anthropic_transport.py"}}
}
```

**`list_recall`** — tool returned a list, the fraction of `expected_items`
that are present is at least `min_recall`:
```json
"expected": {
  "kind": "list_recall",
  "expected_items": [
    {"file_path": {"contains": "a.py"}},
    {"file_path": {"contains": "b.py"}}
  ],
  "min_recall": 0.8
}
```

### Field predicates

`equals`, `contains`, `regex`. Combine freely:
```json
{"file_path": {"regex": "core/.*\\.py", "contains": "transport"}}
```

## Curation rules

1. **Verify ground truth manually first.** A question with a wrong
   expected answer is worse than no question — it pulls the aggregate
   in the wrong direction. Run the equivalent `grep`/`rg` first.
2. **Prefer unique symbols** for `definition`/`callers` unless the
   category is explicitly `ambiguous`.
3. **Cover all 8 categories** for each supported language. Plan target
   is 100 questions across categories per language.
4. **Avoid testing symbols you intend to rename** — every rename
   invalidates a test. Use long-lived public-API names where possible.
5. **Cross-file questions are the highest-leverage** — they expose the
   resolver's accuracy ceiling. Add many of these.

## What this eval does NOT measure

- End-to-end agent loops (LLM round-trips). That's a separate
  integration eval — out of scope for Phase 0.
- Indexer cold-start time / cost. Tracked separately in Phase 5
  benchmarks.
- LLM token counts at the provider level. We use the project's existing
  chars/4 heuristic for consistency; swap in real tokenizer counts when
  the integration eval lands.
