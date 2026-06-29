-- 014_gateway_assignments.sql
-- LLM + MCP "gateway": map abstract roles to concrete configs/servers so the
-- agent, crawler and subagents can each run a different model, and agents get a
-- default MCP toolbox when a workflow wires none.
--
-- Resolution precedence is unchanged for explicitly-wired/named nodes — these
-- assignments only fill the previously-arbitrary "first row" / "no tools" gaps.
-- See app/infrastructure/persistence/model_role_repository.py and
-- mcp_role_repository.py for the consumers.

-- One row per role (agent | crawler | subagent) → a named llm_configs row.
CREATE TABLE IF NOT EXISTS model_role_assignments (
    id              SERIAL PRIMARY KEY,
    role            VARCHAR(50)  NOT NULL UNIQUE,   -- agent | crawler | subagent
    llm_config_name VARCHAR(255) NOT NULL,          -- llm_configs.name
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- One role → many MCP servers (currently only the 'agent' role is used).
CREATE TABLE IF NOT EXISTS mcp_role_assignments (
    id          SERIAL PRIMARY KEY,
    role        VARCHAR(50)  NOT NULL,              -- agent
    server_name VARCHAR(255) NOT NULL,              -- mcp_servers.name
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_mcp_role_assignments_role_server UNIQUE (role, server_name)
);

CREATE INDEX IF NOT EXISTS ix_mcp_role_assignments_role ON mcp_role_assignments (role);
