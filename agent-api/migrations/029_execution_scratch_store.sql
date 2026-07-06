-- 029_execution_scratch_store.sql
-- Optional Postgres-backed store for deep-agent session scratch (todos + VFS).
--
-- planning_tools._store and app.core.vfs's in-memory VFSBackend are process-local
-- dicts — fine on a single node, but not cross-replica safe: if a run's tool calls
-- get load-balanced across replicas mid-run, a later replica can't see an earlier
-- replica's todos/VFS writes. This table backs an opt-in alternate backend
-- (SCRATCH_STORE_BACKEND=postgres, default remains "memory" — zero behavior
-- change unless explicitly configured).
--
-- One row per (execution_id, store): the whole store's content as one JSON blob,
-- matching how both existing in-memory stores already work (planning_tools keeps
-- one list per session; VFSBackend keeps one {path: content} dict per session) —
-- no need for a more granular per-key schema.

CREATE TABLE IF NOT EXISTS execution_scratch_store (
    execution_id VARCHAR(128) NOT NULL,
    store        VARCHAR(32)  NOT NULL,   -- 'todos' | 'vfs'
    value        JSONB        NOT NULL,
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (execution_id, store)
);
