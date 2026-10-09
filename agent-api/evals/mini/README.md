# Mini-model bake-off (milestone M0)

Scores small local models against today's behaviour for each role in the
mini-model plan, before any of them ships. Hermetic: no network, Bedrock or
database.

```bash
cd agent-api
python -m evals.mini.run --out evals/mini/reports
```

| Role | Data | Main metrics | Gate |
|---|---|---|---|
| `router` | `datasets/router.jsonl` (97 hand-written seed examples, **draft**: grow to 300-500 with Haiku-drafted labels and one SRE review pass) | accuracy per label, investigation misroute rate, p95 latency | misroute ≤ 2%, p95 ≤ 50 ms |
| `pii` | `corpora.pii_corpus()` — synthetic log lines with planted PII and operational tokens | masked recall per entity type, false-positive spans, keep-violation rate (UUIDs, trace ids, timestamps that got masked) | no recall regression on today's 6 types; keep violations ≤ 1% |
| `injection` | `corpora.injection_corpus()` — benign ops text (including look-alike phrases) and attacks in logs, tickets, wiki | detection rate, false-flag rate per source | false flags ≤ 1% |
| `templates` | `corpora.template_corpus()` — lines rendered from 15 known templates | Loghub-style grouping accuracy, group count | ≥ baseline |

Corpora are generated from a fixed seed with synthetic values (example.com,
documentation IP ranges, fake keys), so every run is reproducible and nothing
real is stored.

## Adding a candidate

Add a function to `CANDIDATES[role]` in `candidates.py`, with the same
signature as that role's `baseline`. Model candidates should load their files
from the pinned registry (`app/core/mini/registry.py`) and report their own
latency honestly: run the bake-off inside the agent-api container, since CPU
speed there is what production gets.

## Baseline today (2026-10-09)

- **router:** never misroutes an investigation, but has no lookup path (lookup recall 0).
- **pii:** misses PERSON, ADDRESS, AWS access keys and JWTs entirely; the phone
  regex also masks digit runs inside about 4% of request UUIDs.
- **injection:** flags about 31% of benign log lines and catches about 67% of attacks.
- **templates:** the normalizer splits 15 templates into about 256 groups
  (grouping accuracy about 0.07), partly by design (numeric magnitude buckets)
  and partly because names, emails, account ids and pod ids are not masked.
