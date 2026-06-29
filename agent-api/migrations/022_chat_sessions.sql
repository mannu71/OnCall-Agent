-- 022_chat_sessions.sql
-- Persistent chat sessions.
--
-- Previously the chat UI kept messages only in React state: they evaporated on
-- refresh, there was no resume, and only the last 12 turns were ever replayed to
-- the agent. This adds durable, resumable conversations.
--
-- Two-table split:
--   chat_sessions  — one row per conversation (metadata; cheap to list at boot)
--   chat_messages  — one row per message     (hydrated on demand when a session
--                    is opened)
--
-- IDs are app-generated uuid4 strings (no pgcrypto dependency, matching the
-- server-side-UUID pattern). Messages cascade-delete with their parent session.

CREATE TABLE IF NOT EXISTS chat_sessions (
    id              VARCHAR(36)  PRIMARY KEY,
    title           VARCHAR(255) NOT NULL DEFAULT 'New chat',
    workflow_name   VARCHAR(255),                            -- agent/workflow this chat targets
    model           VARCHAR(255),
    archived        BOOLEAN      NOT NULL DEFAULT FALSE,
    is_important    BOOLEAN      NOT NULL DEFAULT FALSE,      -- pinned: protected from cleanup
    message_count   INTEGER      NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_message_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id          BIGSERIAL    PRIMARY KEY,
    session_id  VARCHAR(36)  NOT NULL
                REFERENCES chat_sessions (id) ON DELETE CASCADE,
    role        VARCHAR(16)  NOT NULL,                        -- user | assistant | system
    content     TEXT         NOT NULL DEFAULT '',
    metadata    JSONB,                                        -- tool steps, tokens, privacy, trace
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Hydration + ordering: messages for one session, oldest first.
CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages (session_id, created_at);

-- Session list (non-archived, most-recently-active first).
CREATE INDEX IF NOT EXISTS idx_chat_sessions_active
    ON chat_sessions (archived, last_message_at DESC);
