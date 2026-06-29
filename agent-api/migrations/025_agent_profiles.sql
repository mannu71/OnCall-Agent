-- 025_agent_profiles.sql
-- First-class, reusable "agent profiles" so users can configure ANY type of agent
-- (customer-support, data-analysis, code-explorer, …) instead of the platform
-- hard-coding the on-call/investigation behaviour. A profile is a named bundle of:
--   * role_prompt      — full role-sentence override (NULL = keep derived default)
--   * capabilities     — extra composable capability ids (see app/harness/capabilities)
--   * default_tools    — advisory tool/node hints the UI uses when stamping a template
--   * output_schema    — structured-output schema name (see output_registry)
--   * default_policies — governance policy entries (see app/core/policy)
--   * deep_features    — {planning, filesystem, subagents} deep-agent toggles/defs
--
-- An agent node references a profile by name (agent_config.profile); the executor
-- merges these fields UNDER any explicit node overrides (node always wins), so an
-- existing workflow with no `profile` is completely unaffected.

CREATE TABLE IF NOT EXISTS agent_profiles (
    name             VARCHAR(128) PRIMARY KEY,
    description      TEXT,
    role_prompt      TEXT,
    capabilities     JSONB        NOT NULL DEFAULT '[]'::jsonb,
    default_tools    JSONB        NOT NULL DEFAULT '[]'::jsonb,
    output_schema    VARCHAR(64),
    default_policies JSONB        NOT NULL DEFAULT '[]'::jsonb,
    deep_features    JSONB        NOT NULL DEFAULT '{}'::jsonb,
    builtin          BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- ── Seed builtin profiles (idempotent) ───────────────────────────────────────
-- incident-rca is intentionally a no-op (role_prompt NULL, no capability override,
-- output_schema = investigation) so selecting it reproduces today's behaviour
-- exactly; the others differ only in role sentence + output schema + tool hints.
INSERT INTO agent_profiles (name, description, role_prompt, default_tools, output_schema, builtin)
VALUES
  ('incident-rca',
   'On-call incident root-cause analysis over CloudWatch logs, code and data (the platform default).',
   NULL,
   '["cloudwatch_tool","code_search_tool","database"]'::jsonb,
   'investigation',
   TRUE),

  ('code-explorer',
   'Answers questions about a codebase: locate, read, trace and explain source.',
   'You are a code exploration assistant for the connected repositories. Help the user locate, read, trace and explain source code. Cite repo, file path, symbol and line numbers as evidence; never guess when the code is reachable.',
   '["code_search_tool"]'::jsonb,
   'generic',
   TRUE),

  ('data-analyst',
   'Answers analytical questions over connected databases and produces structured findings.',
   'You are a data-analysis assistant with access to the connected databases and tools. Answer analytical questions with evidence: cite tables, columns and values; note data-quality caveats; prefer targeted queries over full scans.',
   '["database"]'::jsonb,
   'data_analysis',
   TRUE),

  ('customer-support',
   'Resolves customer requests using connected knowledge-base / ticketing tools.',
   'You are a customer-support agent. Understand the customer''s intent, use the connected tools (knowledge base, tickets, account lookups) to resolve their request, and be concise and helpful. If you cannot fully resolve it, say what is needed or that it should be escalated.',
   '["tool"]'::jsonb,
   'support_resolution',
   TRUE),

  ('general-assistant',
   'A general-purpose assistant that adapts to whatever tools are wired in.',
   'You are a helpful, precise general-purpose assistant. Use whatever tools are available to get facts, answer exactly what was asked, and back claims with evidence rather than guessing.',
   '[]'::jsonb,
   'generic',
   TRUE)
ON CONFLICT (name) DO NOTHING;
