-- migrations/009_knowledge_graph.sql
-- Knowledge graph layer for the code crawler.
-- Run after 008_workflow_indexing_status.sql.
--
-- Adds four tables that hold a static-analysis (tree-sitter) view of every
-- indexed repository alongside the existing LLM ``repo_abstractions`` table.
--
-- The crawler's existing flows query repo_abstractions for the high-level
-- map; the new tables answer EXACT questions:
--   • kg_nodes           — one row per code entity (class/function/method/...)
--   • kg_edges           — typed relationships between nodes (calls/imports/...)
--   • kg_files           — per-file SHA256 + parse-stats for incremental updates
--   • kg_unresolved_refs — deferred cross-file resolution queue
--
-- Design references:
--   tirth8205/code-review-graph  — qualified-name scheme, resolve_bare pass
--   colbymchenry/codegraph       — files table, unresolved_refs, FTS
--
-- All statements are idempotent (IF NOT EXISTS).

-- ─────────────────────────────────────────────────────────────────────────────
-- kg_nodes
-- One row per code entity. ``qualified_name`` uses the ``::`` separator
-- (e.g. ``src/Foo.cs::FooService::CheckEntity``) for stable cross-language IDs.
-- search_vector is auto-maintained via the trigger below so Postgres FTS can
-- replace codegraph's SQLite FTS5 virtual table.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kg_nodes (
    id              BIGSERIAL    PRIMARY KEY,
    repo_name       VARCHAR(255) NOT NULL,
    kind            VARCHAR(32)  NOT NULL,
    -- 'file' | 'class' | 'function' | 'method' | 'interface'
    -- | 'type_alias' | 'enum' | 'trait' | 'route' | 'variable'
    name            VARCHAR(255) NOT NULL,
    qualified_name  TEXT         NOT NULL,
    file_path       TEXT         NOT NULL,
    line_start      INT,
    line_end        INT,
    col_start       INT,
    col_end         INT,
    language        VARCHAR(32),
    parent_name     TEXT,
    signature       TEXT,
    docstring       TEXT,
    exported        BOOLEAN      DEFAULT FALSE,
    is_async        BOOLEAN      DEFAULT FALSE,
    is_static       BOOLEAN      DEFAULT FALSE,
    is_abstract     BOOLEAN      DEFAULT FALSE,
    is_test         BOOLEAN      DEFAULT FALSE,
    decorators      JSONB,
    type_parameters JSONB,
    summary         TEXT,
    search_vector   TSVECTOR,
    created_at      TIMESTAMPTZ  DEFAULT NOW(),
    CONSTRAINT kg_nodes_repo_qname_uk UNIQUE (repo_name, qualified_name)
);

CREATE INDEX IF NOT EXISTS kg_nodes_repo_name_idx  ON kg_nodes (repo_name, name);
CREATE INDEX IF NOT EXISTS kg_nodes_repo_file_idx  ON kg_nodes (repo_name, file_path);
CREATE INDEX IF NOT EXISTS kg_nodes_repo_kind_idx  ON kg_nodes (repo_name, kind);
CREATE INDEX IF NOT EXISTS kg_nodes_parent_idx     ON kg_nodes (repo_name, parent_name);
CREATE INDEX IF NOT EXISTS kg_nodes_fts_idx        ON kg_nodes USING GIN (search_vector);

-- Auto-maintain the FTS vector with weights:
--   A = name (highest), B = qualified_name, C = signature, D = docstring
CREATE OR REPLACE FUNCTION kg_nodes_fts_trigger() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('english', coalesce(NEW.name, '')),           'A') ||
        setweight(to_tsvector('english', coalesce(NEW.qualified_name, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(NEW.signature, '')),      'C') ||
        setweight(to_tsvector('english', coalesce(NEW.docstring, '')),      'D');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS kg_nodes_fts_upd ON kg_nodes;
CREATE TRIGGER kg_nodes_fts_upd
    BEFORE INSERT OR UPDATE ON kg_nodes
    FOR EACH ROW
    EXECUTE FUNCTION kg_nodes_fts_trigger();

-- ─────────────────────────────────────────────────────────────────────────────
-- kg_edges
-- Typed relationship between two nodes. ``target_qname`` may be bare (just a
-- name) when the call site couldn't be resolved during the first parse pass;
-- the resolver later promotes such edges to fully-qualified names and updates
-- ``confidence`` from 'extracted' → 'resolved' (or leaves 'extracted' for
-- external library targets we never see defined in this repo).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kg_edges (
    id              BIGSERIAL    PRIMARY KEY,
    repo_name       VARCHAR(255) NOT NULL,
    kind            VARCHAR(32)  NOT NULL,
    -- 'calls' | 'imports_from' | 'inherits' | 'implements'
    -- | 'contains' | 'tested_by' | 'references' | 'depends_on'
    source_qname    TEXT         NOT NULL,
    target_qname    TEXT         NOT NULL,
    file_path       TEXT,
    line            INT,
    col             INT,
    confidence      VARCHAR(16)  DEFAULT 'extracted',
    -- 'extracted' = raw from AST, may be bare name
    -- 'resolved'  = promoted to qualified_name via cross-file resolution
    -- 'external'  = known to point outside this repo (library / stdlib)
    metadata        JSONB,
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS kg_edges_source_idx ON kg_edges (repo_name, source_qname, kind);
CREATE INDEX IF NOT EXISTS kg_edges_target_idx ON kg_edges (repo_name, target_qname, kind);
CREATE INDEX IF NOT EXISTS kg_edges_kind_idx   ON kg_edges (repo_name, kind);
CREATE INDEX IF NOT EXISTS kg_edges_file_idx   ON kg_edges (repo_name, file_path);

-- ─────────────────────────────────────────────────────────────────────────────
-- kg_files
-- Per-file SHA256 plus parse statistics. ``FilterChangedFiles`` compares
-- current sha against this table to skip unchanged files. ``parse_error`` is
-- populated on tree-sitter failure so we can surface broken files to the
-- agent without re-parsing them every run.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kg_files (
    repo_name   VARCHAR(255) NOT NULL,
    file_path   TEXT         NOT NULL,
    sha256      CHAR(64)     NOT NULL,
    language    VARCHAR(32),
    size_bytes  INT,
    node_count  INT          DEFAULT 0,
    edge_count  INT          DEFAULT 0,
    parse_error TEXT,
    parsed_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (repo_name, file_path)
);

CREATE INDEX IF NOT EXISTS kg_files_lang_idx ON kg_files (repo_name, language);

-- ─────────────────────────────────────────────────────────────────────────────
-- kg_unresolved_refs
-- Deferred-resolution queue. When parser sees a bare call like ``Foo.bar()``
-- but the receiver type isn't yet known (file not yet parsed, or external),
-- it inserts a row here. The resolver pass empties this queue once all files
-- in the repo are parsed, promoting matches into kg_edges and dropping
-- truly external ones.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kg_unresolved_refs (
    id          BIGSERIAL    PRIMARY KEY,
    repo_name   VARCHAR(255) NOT NULL,
    from_qname  TEXT         NOT NULL,
    ref_name    TEXT         NOT NULL,
    ref_kind    VARCHAR(32)  NOT NULL,
    file_path   TEXT,
    line        INT,
    candidates  JSONB,
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS kg_unresolved_repo_idx ON kg_unresolved_refs (repo_name, ref_name);
