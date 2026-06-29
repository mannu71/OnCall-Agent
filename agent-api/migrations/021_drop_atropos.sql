-- 021_drop_atropos.sql
-- Remove the dead atropos_trajectories table (created in 004_atropos.sql).
--
-- No application code reads or writes this table: the trajectory feature stores
-- per-run message traces on executions.trajectory (see trajectory_service.py),
-- and the "atropos format" export (/trajectories/{id}/atropos) is derived from
-- that execution data — it never used this table. Dropping it (and its indexes)
-- removes dead schema with zero behavioural impact.

DROP TABLE IF EXISTS atropos_trajectories CASCADE;
