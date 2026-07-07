-- 031_drop_skills.sql
-- Collapse the skill system to file-based markdown (SKILL.md) only.
--
-- The DB-backed "executable"/distilled skill subsystem (SkillService + the
-- `skills` table) has been removed. Skills are now exclusively markdown
-- SKILL.md files on disk (SkillManager), auto-selected per query or invoked by
-- slash-command. This drops the now-unused `skills` table (created in
-- migration 005, altered in 023). Idempotent and safe to re-run.

DROP TABLE IF EXISTS skills CASCADE;
