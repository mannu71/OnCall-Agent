-- ============================================================================
-- 001_schema.sql  --  Consolidated baseline schema (squash of migrations 001-034)
-- ============================================================================
-- Generated from the live, fully-migrated database via:
--   pg_dump --schema-only --no-owner --no-privileges (LangGraph runtime tables
--   checkpoints/checkpoint_*/store/store_migrations excluded -- LangGraph creates
--   and version-tracks those itself on startup).
--
-- The migration runner (setup.ps1 / setup.sh) applies this file once on a fresh
-- volume and tracks it in schema_migrations. On a pre-existing install whose DB
-- already matches this schema, the runner stamps this file as applied WITHOUT
-- executing it. New migrations resume at 002_*.sql.
--
-- Do NOT edit historical schema below by hand -- add a new 002_*.sql instead.
-- ============================================================================

--
--



SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: agent_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_profiles (
    name character varying(128) NOT NULL,
    description text,
    role_prompt text,
    capabilities jsonb DEFAULT '[]'::jsonb NOT NULL,
    default_tools jsonb DEFAULT '[]'::jsonb NOT NULL,
    output_schema character varying(64),
    default_policies jsonb DEFAULT '[]'::jsonb NOT NULL,
    deep_features jsonb DEFAULT '{}'::jsonb NOT NULL,
    builtin boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: alerts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alerts (
    id integer NOT NULL,
    log_group character varying(255) NOT NULL,
    alert_type character varying(100) NOT NULL,
    severity character varying(20) NOT NULL,
    message text NOT NULL,
    details jsonb,
    status character varying(20) DEFAULT 'new'::character varying,
    created_at timestamp with time zone DEFAULT now(),
    resolved_at timestamp with time zone,
    resolved_by character varying(255)
);


--
-- Name: alerts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.alerts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: alerts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.alerts_id_seq OWNED BY public.alerts.id;


--
-- Name: analysis_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.analysis_history (
    id integer NOT NULL,
    log_group character varying(255) NOT NULL,
    analysis_type character varying(100) NOT NULL,
    start_time timestamp with time zone NOT NULL,
    end_time timestamp with time zone NOT NULL,
    summary text,
    anomalies_found integer DEFAULT 0,
    patterns_matched integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: analysis_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.analysis_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: analysis_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.analysis_history_id_seq OWNED BY public.analysis_history.id;


--
-- Name: app_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_settings (
    key character varying(128) NOT NULL,
    value text,
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: background_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.background_jobs (
    id integer NOT NULL,
    job_type character varying(50) NOT NULL,
    target character varying(255) NOT NULL,
    status character varying(20) DEFAULT 'queued'::character varying NOT NULL,
    progress integer DEFAULT 0 NOT NULL,
    total integer DEFAULT 0 NOT NULL,
    detail text,
    error text,
    payload jsonb,
    claimed_by character varying(64),
    heartbeat_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    ended_at timestamp with time zone
);


--
-- Name: background_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.background_jobs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: background_jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.background_jobs_id_seq OWNED BY public.background_jobs.id;


--
-- Name: baseline_metrics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.baseline_metrics (
    id integer NOT NULL,
    metric_name character varying(255) NOT NULL,
    log_group character varying(255) NOT NULL,
    normal_range_min double precision,
    normal_range_max double precision,
    threshold_warning double precision,
    threshold_critical double precision,
    time_window character varying(50),
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: baseline_metrics_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.baseline_metrics_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: baseline_metrics_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.baseline_metrics_id_seq OWNED BY public.baseline_metrics.id;


--
-- Name: chat_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chat_messages (
    id bigint NOT NULL,
    session_id character varying(36) NOT NULL,
    role character varying(16) NOT NULL,
    content text DEFAULT ''::text NOT NULL,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: chat_messages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.chat_messages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: chat_messages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.chat_messages_id_seq OWNED BY public.chat_messages.id;


--
-- Name: chat_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chat_sessions (
    id character varying(36) NOT NULL,
    title character varying(255) DEFAULT 'New chat'::character varying NOT NULL,
    workflow_name character varying(255),
    model character varying(255),
    archived boolean DEFAULT false NOT NULL,
    is_important boolean DEFAULT false NOT NULL,
    message_count integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    last_message_at timestamp with time zone,
    total_input_tokens integer DEFAULT 0 NOT NULL,
    total_output_tokens integer DEFAULT 0 NOT NULL,
    total_cache_read_tokens integer DEFAULT 0 NOT NULL,
    total_cache_creation_tokens integer DEFAULT 0 NOT NULL
);


--
-- Name: code_repos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.code_repos (
    id integer NOT NULL,
    repo_name character varying(255) NOT NULL,
    clone_url character varying(1000),
    local_path character varying(1000),
    language character varying(50),
    last_indexed timestamp with time zone,
    commit_hash character varying(64),
    status character varying(20) DEFAULT 'pending'::character varying,
    error_msg text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: code_repos_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.code_repos_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: code_repos_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.code_repos_id_seq OWNED BY public.code_repos.id;


--
-- Name: context_references; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.context_references (
    id integer NOT NULL,
    execution_id character varying(255),
    reference_type character varying(50) NOT NULL,
    target character varying(1000) NOT NULL,
    tokens_injected integer,
    created_at timestamp with time zone
);


--
-- Name: context_references_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.context_references_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: context_references_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.context_references_id_seq OWNED BY public.context_references.id;


--
-- Name: execution_scratch_store; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.execution_scratch_store (
    execution_id character varying(128) NOT NULL,
    store character varying(32) NOT NULL,
    value jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: executions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.executions (
    id integer NOT NULL,
    workflow_id integer,
    workflow_name character varying(255) NOT NULL,
    status character varying(50) DEFAULT 'pending'::character varying NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    duration_ms integer,
    input jsonb,
    output jsonb,
    error text,
    logs jsonb,
    trajectory jsonb,
    input_tokens integer DEFAULT 0,
    output_tokens integer DEFAULT 0,
    total_tokens integer DEFAULT 0,
    chat_session_id character varying(36)
);


--
-- Name: executions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.executions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: executions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.executions_id_seq OWNED BY public.executions.id;


--
-- Name: failure_ledger; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.failure_ledger (
    fingerprint character varying(80) NOT NULL,
    first_seen timestamp with time zone DEFAULT now() NOT NULL,
    last_seen timestamp with time zone DEFAULT now() NOT NULL,
    count integer DEFAULT 1 NOT NULL,
    sample_execution_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    status character varying(16) DEFAULT 'open'::character varying NOT NULL,
    control_ref text
);


--
-- Name: investigation_memory; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.investigation_memory (
    id integer NOT NULL,
    repo_name character varying(255) NOT NULL,
    function_name character varying(500) NOT NULL,
    file_path character varying(1000),
    entity_id character varying(16),
    summary text,
    tags jsonb DEFAULT '[]'::jsonb,
    access_count integer DEFAULT 1,
    last_accessed timestamp with time zone DEFAULT now(),
    created_at timestamp with time zone DEFAULT now(),
    status character varying(20) DEFAULT 'active'::character varying
);


--
-- Name: investigation_memory_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.investigation_memory_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: investigation_memory_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.investigation_memory_id_seq OWNED BY public.investigation_memory.id;


--
-- Name: knowledge_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.knowledge_entries (
    id integer NOT NULL,
    title character varying(255) NOT NULL,
    description text NOT NULL,
    symptoms jsonb,
    solution text,
    category character varying(100),
    source character varying(50) DEFAULT 'manual'::character varying,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    embedding public.vector(1024)
);


--
-- Name: knowledge_entries_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.knowledge_entries_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: knowledge_entries_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.knowledge_entries_id_seq OWNED BY public.knowledge_entries.id;


--
-- Name: known_issues_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.known_issues_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: known_issues_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.known_issues_id_seq OWNED BY public.knowledge_entries.id;


--
-- Name: llm_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_cache (
    prompt_sha256 character(64) NOT NULL,
    model_id character varying(255) NOT NULL,
    response text NOT NULL,
    tokens_in integer,
    tokens_out integer,
    cached_at timestamp with time zone DEFAULT now() NOT NULL,
    last_hit_at timestamp with time zone DEFAULT now() NOT NULL,
    hits integer DEFAULT 0 NOT NULL
);


--
-- Name: llm_configs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_configs (
    id integer NOT NULL,
    name character varying(255) NOT NULL,
    provider character varying(100) NOT NULL,
    model character varying(255) NOT NULL,
    endpoint character varying(500),
    base_url character varying(500),
    temperature double precision DEFAULT 0.7,
    max_tokens integer DEFAULT 4096,
    region character varying(50) DEFAULT 'us-east-1'::character varying,
    icon character varying(10),
    description text,
    aws_profile character varying(100),
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    use_for_embeddings boolean DEFAULT false
);


--
-- Name: COLUMN llm_configs.use_for_embeddings; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.llm_configs.use_for_embeddings IS 'Whether this LLM configuration should be used for generating embeddings';


--
-- Name: llm_configs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.llm_configs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: llm_configs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.llm_configs_id_seq OWNED BY public.llm_configs.id;


--
-- Name: log_patterns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.log_patterns (
    id integer NOT NULL,
    name character varying(255) NOT NULL,
    pattern text NOT NULL,
    pattern_type character varying(50) NOT NULL,
    severity integer DEFAULT 1,
    description text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    embedding public.vector(1024)
);


--
-- Name: log_patterns_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.log_patterns_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: log_patterns_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.log_patterns_id_seq OWNED BY public.log_patterns.id;


--
-- Name: mcp_role_assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mcp_role_assignments (
    id integer NOT NULL,
    role character varying(50) NOT NULL,
    server_name character varying(255) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: mcp_role_assignments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.mcp_role_assignments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: mcp_role_assignments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.mcp_role_assignments_id_seq OWNED BY public.mcp_role_assignments.id;


--
-- Name: mcp_servers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mcp_servers (
    id integer NOT NULL,
    name character varying(255) NOT NULL,
    command character varying(500) NOT NULL,
    args jsonb,
    env jsonb,
    enabled boolean DEFAULT true,
    description text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: mcp_servers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.mcp_servers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: mcp_servers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.mcp_servers_id_seq OWNED BY public.mcp_servers.id;


--
-- Name: memory_summaries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.memory_summaries (
    session_id character varying(64) NOT NULL,
    summary jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: model_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.model_keys (
    id integer NOT NULL,
    provider character varying(100) NOT NULL,
    api_key character varying(500),
    secret_key character varying(500),
    endpoint character varying(500),
    region character varying(50),
    access_key_id character varying(500),
    secret_access_key character varying(500),
    session_token character varying(500),
    description text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    key_label character varying(100) DEFAULT 'default'::character varying NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    enabled boolean DEFAULT true NOT NULL
);


--
-- Name: model_keys_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.model_keys_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: model_keys_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.model_keys_id_seq OWNED BY public.model_keys.id;


--
-- Name: model_role_assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.model_role_assignments (
    id integer NOT NULL,
    role character varying(50) NOT NULL,
    llm_config_name character varying(255) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: model_role_assignments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.model_role_assignments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: model_role_assignments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.model_role_assignments_id_seq OWNED BY public.model_role_assignments.id;


--
-- Name: pattern_memory; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pattern_memory (
    id integer NOT NULL,
    error_signature character varying(500) NOT NULL,
    resolution text NOT NULL,
    confidence_score double precision DEFAULT 0.6 NOT NULL,
    occurrence_count integer DEFAULT 1 NOT NULL,
    last_seen timestamp with time zone DEFAULT now(),
    curator_status character varying(20) DEFAULT 'active'::character varying,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: pattern_memory_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.pattern_memory_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: pattern_memory_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.pattern_memory_id_seq OWNED BY public.pattern_memory.id;


--
-- Name: policy_sets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.policy_sets (
    name character varying(128) NOT NULL,
    description text,
    policies jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: rca_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rca_history (
    id integer NOT NULL,
    repo_name character varying(255),
    error_signature character varying(500),
    root_cause_functions jsonb,
    root_cause_entity_ids jsonb,
    contributing_files jsonb,
    resolution_summary text,
    sub_tool_trace jsonb,
    evidence_grade character varying(30) DEFAULT 'inferred'::character varying,
    confirmation_count integer DEFAULT 1,
    integrity_flag character varying(50),
    is_novel_pattern boolean DEFAULT false,
    remediation_steps jsonb,
    remediation_grade character varying(30) DEFAULT 'suggestive'::character varying,
    incident_id character varying(255),
    execution_id character varying(255),
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: rca_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.rca_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: rca_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.rca_history_id_seq OWNED BY public.rca_history.id;


--
-- Name: repo_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.repo_groups (
    group_name character varying(255) NOT NULL,
    repos jsonb DEFAULT '[]'::jsonb NOT NULL,
    description text,
    overview jsonb,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE repo_groups; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.repo_groups IS 'Named groups of related repositories for cross-repo crawler search/trace and UI???API integration mapping.';


--
-- Name: semantic_memory; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.semantic_memory (
    id bigint NOT NULL,
    bank character varying(16) DEFAULT 'repo'::character varying NOT NULL,
    repo_name character varying(255),
    content text NOT NULL,
    content_sha256 character(64) NOT NULL,
    search_vector tsvector,
    source character varying(32) DEFAULT 'agent'::character varying NOT NULL,
    importance real DEFAULT 0.5 NOT NULL,
    veracity real DEFAULT 0.5 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_recalled_at timestamp with time zone,
    recall_count integer DEFAULT 0 NOT NULL,
    embedding public.vector(1024)
);


--
-- Name: semantic_memory_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.semantic_memory_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: semantic_memory_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.semantic_memory_id_seq OWNED BY public.semantic_memory.id;


--
-- Name: tool_approvals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tool_approvals (
    id integer NOT NULL,
    execution_id character varying(64) NOT NULL,
    request_id character varying(64) NOT NULL,
    tool_name character varying(255) NOT NULL,
    args_summary text,
    decision character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    decided_by character varying(255),
    reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    decided_at timestamp with time zone,
    risk_tier character varying(10),
    supervisor_verdict character varying(20),
    supervisor_reasoning text
);


--
-- Name: tool_approvals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tool_approvals_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tool_approvals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tool_approvals_id_seq OWNED BY public.tool_approvals.id;


--
-- Name: trajectories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trajectories (
    id integer NOT NULL,
    trajectory_id character varying(255) NOT NULL,
    execution_id character varying(255),
    model character varying(255),
    messages json NOT NULL,
    tool_calls json,
    completed boolean,
    trajectory_metadata json,
    created_at timestamp with time zone
);


--
-- Name: trajectories_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.trajectories_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: trajectories_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.trajectories_id_seq OWNED BY public.trajectories.id;


--
-- Name: trajectory_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trajectory_events (
    event_id character varying(32) NOT NULL,
    trace_id character varying(128) NOT NULL,
    span_id character varying(64),
    step_index integer NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    type character varying(24) NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: workflows; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workflows (
    id integer NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    nodes jsonb NOT NULL,
    edges jsonb NOT NULL,
    viewport jsonb,
    enabled boolean DEFAULT true,
    schedule character varying(100),
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    indexing_status character varying(50) DEFAULT NULL::character varying,
    type character varying(20) DEFAULT 'workflow'::character varying NOT NULL
);


--
-- Name: workflows_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.workflows_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: workflows_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.workflows_id_seq OWNED BY public.workflows.id;


--
-- Name: alerts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts ALTER COLUMN id SET DEFAULT nextval('public.alerts_id_seq'::regclass);


--
-- Name: analysis_history id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_history ALTER COLUMN id SET DEFAULT nextval('public.analysis_history_id_seq'::regclass);


--
-- Name: background_jobs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.background_jobs ALTER COLUMN id SET DEFAULT nextval('public.background_jobs_id_seq'::regclass);


--
-- Name: baseline_metrics id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.baseline_metrics ALTER COLUMN id SET DEFAULT nextval('public.baseline_metrics_id_seq'::regclass);


--
-- Name: chat_messages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_messages ALTER COLUMN id SET DEFAULT nextval('public.chat_messages_id_seq'::regclass);


--
-- Name: code_repos id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.code_repos ALTER COLUMN id SET DEFAULT nextval('public.code_repos_id_seq'::regclass);


--
-- Name: context_references id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.context_references ALTER COLUMN id SET DEFAULT nextval('public.context_references_id_seq'::regclass);


--
-- Name: executions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.executions ALTER COLUMN id SET DEFAULT nextval('public.executions_id_seq'::regclass);


--
-- Name: investigation_memory id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.investigation_memory ALTER COLUMN id SET DEFAULT nextval('public.investigation_memory_id_seq'::regclass);


--
-- Name: knowledge_entries id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_entries ALTER COLUMN id SET DEFAULT nextval('public.knowledge_entries_id_seq'::regclass);


--
-- Name: llm_configs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_configs ALTER COLUMN id SET DEFAULT nextval('public.llm_configs_id_seq'::regclass);


--
-- Name: log_patterns id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.log_patterns ALTER COLUMN id SET DEFAULT nextval('public.log_patterns_id_seq'::regclass);


--
-- Name: mcp_role_assignments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mcp_role_assignments ALTER COLUMN id SET DEFAULT nextval('public.mcp_role_assignments_id_seq'::regclass);


--
-- Name: mcp_servers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mcp_servers ALTER COLUMN id SET DEFAULT nextval('public.mcp_servers_id_seq'::regclass);


--
-- Name: model_keys id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_keys ALTER COLUMN id SET DEFAULT nextval('public.model_keys_id_seq'::regclass);


--
-- Name: model_role_assignments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_role_assignments ALTER COLUMN id SET DEFAULT nextval('public.model_role_assignments_id_seq'::regclass);


--
-- Name: pattern_memory id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pattern_memory ALTER COLUMN id SET DEFAULT nextval('public.pattern_memory_id_seq'::regclass);


--
-- Name: rca_history id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rca_history ALTER COLUMN id SET DEFAULT nextval('public.rca_history_id_seq'::regclass);


--
-- Name: semantic_memory id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.semantic_memory ALTER COLUMN id SET DEFAULT nextval('public.semantic_memory_id_seq'::regclass);


--
-- Name: tool_approvals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tool_approvals ALTER COLUMN id SET DEFAULT nextval('public.tool_approvals_id_seq'::regclass);


--
-- Name: trajectories id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trajectories ALTER COLUMN id SET DEFAULT nextval('public.trajectories_id_seq'::regclass);


--
-- Name: workflows id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflows ALTER COLUMN id SET DEFAULT nextval('public.workflows_id_seq'::regclass);


--
-- Name: agent_profiles agent_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_profiles
    ADD CONSTRAINT agent_profiles_pkey PRIMARY KEY (name);


--
-- Name: alerts alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_pkey PRIMARY KEY (id);


--
-- Name: analysis_history analysis_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.analysis_history
    ADD CONSTRAINT analysis_history_pkey PRIMARY KEY (id);


--
-- Name: app_settings app_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_settings
    ADD CONSTRAINT app_settings_pkey PRIMARY KEY (key);


--
-- Name: background_jobs background_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.background_jobs
    ADD CONSTRAINT background_jobs_pkey PRIMARY KEY (id);


--
-- Name: baseline_metrics baseline_metrics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.baseline_metrics
    ADD CONSTRAINT baseline_metrics_pkey PRIMARY KEY (id);


--
-- Name: chat_messages chat_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_messages
    ADD CONSTRAINT chat_messages_pkey PRIMARY KEY (id);


--
-- Name: chat_sessions chat_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_sessions
    ADD CONSTRAINT chat_sessions_pkey PRIMARY KEY (id);


--
-- Name: code_repos code_repos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.code_repos
    ADD CONSTRAINT code_repos_pkey PRIMARY KEY (id);


--
-- Name: code_repos code_repos_repo_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.code_repos
    ADD CONSTRAINT code_repos_repo_name_key UNIQUE (repo_name);


--
-- Name: execution_scratch_store execution_scratch_store_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.execution_scratch_store
    ADD CONSTRAINT execution_scratch_store_pkey PRIMARY KEY (execution_id, store);


--
-- Name: executions executions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.executions
    ADD CONSTRAINT executions_pkey PRIMARY KEY (id);


--
-- Name: failure_ledger failure_ledger_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.failure_ledger
    ADD CONSTRAINT failure_ledger_pkey PRIMARY KEY (fingerprint);


--
-- Name: knowledge_entries knowledge_entries_title_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_entries
    ADD CONSTRAINT knowledge_entries_title_key UNIQUE (title);


--
-- Name: knowledge_entries known_issues_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_entries
    ADD CONSTRAINT known_issues_pkey PRIMARY KEY (id);


--
-- Name: llm_cache llm_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_cache
    ADD CONSTRAINT llm_cache_pkey PRIMARY KEY (prompt_sha256);


--
-- Name: llm_configs llm_configs_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_configs
    ADD CONSTRAINT llm_configs_name_key UNIQUE (name);


--
-- Name: llm_configs llm_configs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_configs
    ADD CONSTRAINT llm_configs_pkey PRIMARY KEY (id);


--
-- Name: log_patterns log_patterns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.log_patterns
    ADD CONSTRAINT log_patterns_pkey PRIMARY KEY (id);


--
-- Name: mcp_role_assignments mcp_role_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mcp_role_assignments
    ADD CONSTRAINT mcp_role_assignments_pkey PRIMARY KEY (id);


--
-- Name: mcp_servers mcp_servers_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mcp_servers
    ADD CONSTRAINT mcp_servers_name_key UNIQUE (name);


--
-- Name: mcp_servers mcp_servers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mcp_servers
    ADD CONSTRAINT mcp_servers_pkey PRIMARY KEY (id);


--
-- Name: memory_summaries memory_summaries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_summaries
    ADD CONSTRAINT memory_summaries_pkey PRIMARY KEY (session_id);


--
-- Name: model_keys model_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_keys
    ADD CONSTRAINT model_keys_pkey PRIMARY KEY (id);


--
-- Name: model_keys model_keys_provider_label_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_keys
    ADD CONSTRAINT model_keys_provider_label_key UNIQUE (provider, key_label);


--
-- Name: model_role_assignments model_role_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_role_assignments
    ADD CONSTRAINT model_role_assignments_pkey PRIMARY KEY (id);


--
-- Name: model_role_assignments model_role_assignments_role_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_role_assignments
    ADD CONSTRAINT model_role_assignments_role_key UNIQUE (role);


--
-- Name: pattern_memory pattern_memory_error_signature_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pattern_memory
    ADD CONSTRAINT pattern_memory_error_signature_key UNIQUE (error_signature);


--
-- Name: pattern_memory pattern_memory_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pattern_memory
    ADD CONSTRAINT pattern_memory_pkey PRIMARY KEY (id);


--
-- Name: policy_sets policy_sets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.policy_sets
    ADD CONSTRAINT policy_sets_pkey PRIMARY KEY (name);


--
-- Name: semantic_memory semantic_memory_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.semantic_memory
    ADD CONSTRAINT semantic_memory_pkey PRIMARY KEY (id);


--
-- Name: tool_approvals tool_approvals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tool_approvals
    ADD CONSTRAINT tool_approvals_pkey PRIMARY KEY (id);


--
-- Name: trajectory_events trajectory_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trajectory_events
    ADD CONSTRAINT trajectory_events_pkey PRIMARY KEY (event_id);


--
-- Name: mcp_role_assignments uq_mcp_role_assignments_role_server; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mcp_role_assignments
    ADD CONSTRAINT uq_mcp_role_assignments_role_server UNIQUE (role, server_name);


--
-- Name: tool_approvals uq_tool_approvals_request; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tool_approvals
    ADD CONSTRAINT uq_tool_approvals_request UNIQUE (execution_id, request_id);


--
-- Name: workflows workflows_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflows
    ADD CONSTRAINT workflows_name_key UNIQUE (name);


--
-- Name: workflows workflows_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflows
    ADD CONSTRAINT workflows_pkey PRIMARY KEY (id);


--
-- Name: idx_alerts_log_group; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alerts_log_group ON public.alerts USING btree (log_group, created_at DESC);


--
-- Name: idx_alerts_status_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alerts_status_created ON public.alerts USING btree (status, created_at DESC);


--
-- Name: idx_chat_messages_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_messages_session ON public.chat_messages USING btree (session_id, created_at);


--
-- Name: idx_chat_sessions_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_sessions_active ON public.chat_sessions USING btree (archived, last_message_at DESC);


--
-- Name: idx_executions_chat_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_executions_chat_session ON public.executions USING btree (chat_session_id) WHERE (chat_session_id IS NOT NULL);


--
-- Name: idx_executions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_executions_status ON public.executions USING btree (status, started_at DESC);


--
-- Name: idx_executions_workflow_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_executions_workflow_name ON public.executions USING btree (workflow_name);


--
-- Name: idx_knowledge_entries_embedding; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_knowledge_entries_embedding ON public.knowledge_entries USING ivfflat (embedding public.vector_cosine_ops) WITH (lists='100');


--
-- Name: idx_known_issues_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_known_issues_category ON public.knowledge_entries USING btree (category, created_at DESC);


--
-- Name: idx_known_issues_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_known_issues_source ON public.knowledge_entries USING btree (source);


--
-- Name: idx_log_patterns_embedding; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_log_patterns_embedding ON public.log_patterns USING ivfflat (embedding public.vector_cosine_ops) WITH (lists='100');


--
-- Name: idx_log_patterns_type_sev; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_log_patterns_type_sev ON public.log_patterns USING btree (pattern_type, severity DESC);


--
-- Name: idx_model_keys_provider_priority; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_model_keys_provider_priority ON public.model_keys USING btree (provider, enabled, priority);


--
-- Name: idx_pattern_memory_confidence; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pattern_memory_confidence ON public.pattern_memory USING btree (confidence_score DESC);


--
-- Name: idx_pattern_memory_curator; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pattern_memory_curator ON public.pattern_memory USING btree (curator_status, last_seen);


--
-- Name: idx_pattern_memory_signature; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pattern_memory_signature ON public.pattern_memory USING btree (error_signature);


--
-- Name: idx_semantic_memory_bank_repo; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_semantic_memory_bank_repo ON public.semantic_memory USING btree (bank, repo_name);


--
-- Name: idx_semantic_memory_embedding; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_semantic_memory_embedding ON public.semantic_memory USING ivfflat (embedding public.vector_cosine_ops) WITH (lists='100');


--
-- Name: idx_semantic_memory_pinned; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_semantic_memory_pinned ON public.semantic_memory USING btree (repo_name, ((importance * veracity)) DESC) WHERE ((bank)::text = 'pinned'::text);


--
-- Name: idx_semantic_memory_search; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_semantic_memory_search ON public.semantic_memory USING gin (search_vector);


--
-- Name: idx_trajectory_events_trace; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_trajectory_events_trace ON public.trajectory_events USING btree (trace_id, step_index);


--
-- Name: ix_background_jobs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_background_jobs_status ON public.background_jobs USING btree (status);


--
-- Name: ix_background_jobs_type_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_background_jobs_type_target ON public.background_jobs USING btree (job_type, target);


--
-- Name: ix_knowledge_entries_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_knowledge_entries_category ON public.knowledge_entries USING btree (category, created_at DESC);


--
-- Name: ix_knowledge_entries_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_knowledge_entries_source ON public.knowledge_entries USING btree (source);


--
-- Name: ix_llm_cache_model_cached; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_cache_model_cached ON public.llm_cache USING btree (model_id, cached_at DESC);


--
-- Name: ix_mcp_role_assignments_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_mcp_role_assignments_role ON public.mcp_role_assignments USING btree (role);


--
-- Name: ix_tool_approvals_decision; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tool_approvals_decision ON public.tool_approvals USING btree (decision);


--
-- Name: ix_tool_approvals_execution; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tool_approvals_execution ON public.tool_approvals USING btree (execution_id);


--
-- Name: ix_tool_approvals_risk_tier; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tool_approvals_risk_tier ON public.tool_approvals USING btree (risk_tier);


--
-- Name: uq_semantic_memory_dedup; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_semantic_memory_dedup ON public.semantic_memory USING btree (bank, COALESCE(repo_name, ''::character varying), content_sha256);


--
-- Name: chat_messages chat_messages_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_messages
    ADD CONSTRAINT chat_messages_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.chat_sessions(id) ON DELETE CASCADE;


--
--




-- ============================================================================
-- Seed data (builtin/default rows a fresh install needs; idempotent)
-- ----------------------------------------------------------------------------
-- Squashed from migrations 011 (default timezone), 025 (builtin agent profiles)
-- and 027 (orchestrator profile). These are DEFAULTS, not captured live data --
-- pg_dump --schema-only carries no rows. Transitional data migrations (019 dim
-- rebuild, 024 known_issues copy, 032 title de-dupe) are intentionally omitted:
-- they only mattered while upgrading older databases.
-- ============================================================================

-- The pg_dump preamble set search_path='' (every object above is public-qualified).
-- Restore it so the unqualified seed inserts below resolve.
SET search_path TO public;

-- Global timezone default (migration 011). Operators change it from the
-- Settings page; UTC is the deterministic default before first change.
INSERT INTO app_settings (key, value, updated_at)
VALUES ('global_timezone', 'UTC', now())
ON CONFLICT (key) DO NOTHING;

-- Builtin agent profiles (migration 025). incident-rca is the platform default
-- (no-op role/schema so it reproduces baseline behaviour); the rest differ only
-- in role sentence + output schema + tool hints.
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

-- Orchestrator profile for multi-agent (supervisor-worker) workflows (migration 027).
-- Provides the orchestrator role prompt only; named subagents are defined per-workflow
-- on the agent node's "Subagent Definitions" field, not hardcoded here.
INSERT INTO agent_profiles (
    name, description, role_prompt, default_tools, output_schema, deep_features, builtin
)
VALUES (
    'orchestrator',
    'Generic orchestrator profile: delegates evidence-gathering to named subagents defined on the agent node, then synthesises their findings. Configure subagents via the "Subagent Definitions" field on the agent node.',
    'You are an orchestrator agent. Your job is synthesis, not evidence-gathering.

DELEGATION PROTOCOL
1. Decompose the request into separable tracks and delegate each to the matching subagent using the delegate_to_* tools available to you.
2. Issue all delegations before synthesising -- wait for all subagents to report back.
3. Only call raw tools directly for a quick cross-check that a subagent missed or for a simple follow-up that does not warrant a full delegation.
4. Synthesise all subagent findings into one cohesive, evidence-backed response.

WHEN NO SUBAGENTS ARE CONFIGURED
If no delegate_to_* tools are present, handle the request yourself using the tools available.',
    '[]'::jsonb,
    'investigation',
    '{}'::jsonb,
    TRUE
)
ON CONFLICT (name) DO NOTHING;
