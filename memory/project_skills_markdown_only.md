---
name: project_skills_markdown_only
description: 2026-07-06 collapsed the skill system to file-based markdown SKILL.md only; deleted the entire DB/JSON SkillService distilled subsystem.
metadata:
  type: project
---

2026-07-06 — Per user directive ("all skills file-based, delete all DB related"),
collapsed the TWO skill systems into ONE. **Kept:** `SkillManager`
(`agent-api/app/core/skills/manager.py`) — markdown `SKILL.md` files
(claude-code model), RAG auto-selected per query via `select_for_query`, or
slash-invoked. **Deleted:** the entire `SkillService` subsystem — distilled
"executable" skills, the `execute_skill` tool, JSON `data/skills_store`,
`recall_two_stage`, curator skill-audit, and all DB bits.

Removed: `app/core/skills/service.py` (whole file); `SkillModel` (db_models.py) +
migration `031_drop_skills.sql` drops the `skills` table; config settings
`skill_min_tool_calls/skill_confidence_min/skill_promote_success_count/
skill_two_stage_recall/skill_shortlist_k/skills_store_dir`. Rewired: `skills.py`
API is now fs-only (`GET /skills` returns `{filesystem}`, no `db`); auto_learn
dropped `_distill_skill`; curator dropped skill audit; `improvement/apply.py`
`_apply_skill_proposal` now writes a `draft_` `SKILL.md` via SkillManager;
harness dropped `execute_skill` (tool_setup/permissions/disclosure/agent_builder
prompt); `build_recall_context`/context_builder dropped the KB skill-recall leg.
UI: `Skills.jsx` fs-only, `SkillsPicker`/`skillCatalog` fs-only (no `.db`
provenance filter), removed db methods from `agentApiClient.js`.

KEPT (still valid): `skill_rag_selection_enabled`+`skill_rag_k` flags, `skills_dir`,
per-agent `skills` scoping (spec_factory `_pick("skills")` → picker), seed skills
under `app/core/skills/seed`, `selected_skills` chat metadata. Supersedes the
self-evolving-skills half of [[project_odysseus_borrow]] and the two-stage recall
in [[project_osaurus_borrow]].

VERIFIED DONE (2026-07-06): rebuilt agent-api via ROOT compose
(`docker compose -f docker-compose.yml build agent-api` from repo root — project
`oncall-agent`, container `kyc-agent-api`; the agent-api/docker-compose.yml is a
DIFFERENT project and wrong context — `COPY agent-api/app` fails there); recreated
container; full harness_selftest ALL PASS in-container (had to `docker cp
agent-api/evals` in — evals not in image — and run with `PYTHONPATH=/app`); UI
Skills page renders 3 markdown skills, no errors; `/api/v1/skills` returns
`{filesystem}` only. Migration 031 applied → `skills` table dropped. New DB-free
+ real-DB auto-learn tests pass; that testing found+fixed a separate KB-upsert bug
— see [[project_autolearn_kb_upsert_fix]]. See [[project_agent_api_no_reload]].
