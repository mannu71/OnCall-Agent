-- 028_session_tokens_and_chat_source.sql
-- Session-level cumulative token rollup + chat-vs-workflow execution discriminator.
--
-- Two independent additions:
--
-- 1. chat_sessions gains cumulative token counters. Previously only per-message
--    (chat_messages.metadata JSON) and per-execution (executions table) token
--    counts existed — there was no "how many tokens has this whole conversation
--    used" figure. These are incremented transactionally in append_message()
--    alongside the existing message_count/last_message_at bump.
--
-- 2. executions gains chat_session_id — every chat message currently reuses
--    the same /workflows/{name}/execute endpoint as a real scheduled/manual
--    workflow run, so the Dashboard's "Recent runs" conflated ad-hoc chat
--    turns with real workflow executions. Nullable; set only when the execute
--    call carried a session_id (i.e. it came from the chat UI), so
--    `chat_session_id IS NULL` cleanly identifies real workflow runs.

ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS total_input_tokens         INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS total_output_tokens        INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS total_cache_read_tokens     INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS total_cache_creation_tokens INTEGER NOT NULL DEFAULT 0;

ALTER TABLE executions
    ADD COLUMN IF NOT EXISTS chat_session_id VARCHAR(36);

CREATE INDEX IF NOT EXISTS idx_executions_chat_session
    ON executions (chat_session_id)
    WHERE chat_session_id IS NOT NULL;
