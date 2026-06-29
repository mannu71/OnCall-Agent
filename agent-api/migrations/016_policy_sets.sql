-- 016_policy_sets.sql
-- Named, reusable governance policy sets for the declarative policy engine.
--
-- A policy set is a named list of policy entries (see app/core/policy):
--   [{"type": "ask_on_os_tools"},
--    {"type": "cost_budget", "params": {"max_cost_usd": 5.0}},
--    {"type": "max_tool_calls_per_session", "params": {"limit": 50}}]
-- A workflow's agent node references one by name (agent_config.policySet, or a
-- {"type": "policy_set", "params": {"name": "..."}} entry) and the engine expands
-- it to these entries at agent-build time. Inline policy lists need no row here.

CREATE TABLE IF NOT EXISTS policy_sets (
    name        VARCHAR(128) PRIMARY KEY,
    description TEXT,
    policies    JSONB        NOT NULL DEFAULT '[]'::jsonb,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
