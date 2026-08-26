"""SQLite schema definitions and migration scripts.

Each migration is a (version, sql) tuple.  Migrations are applied in order
and tracked in the ``_migrations`` table so they only run once.
"""

from __future__ import annotations

# ------------------------------------------------------------------
# Migration 1: Core tables
# ------------------------------------------------------------------

_V1_CORE = """
-- AI models: cached from AIDream server /api/ai-models
CREATE TABLE IF NOT EXISTS ai_models (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    common_name  TEXT NOT NULL DEFAULT '',
    provider     TEXT NOT NULL DEFAULT '',
    endpoints    TEXT NOT NULL DEFAULT '[]',
    capabilities TEXT NOT NULL DEFAULT '[]',
    context_window INTEGER,
    max_tokens   INTEGER,
    is_primary   INTEGER NOT NULL DEFAULT 0,
    is_premium   INTEGER NOT NULL DEFAULT 0,
    is_deprecated INTEGER NOT NULL DEFAULT 0,
    raw_json     TEXT NOT NULL DEFAULT '{}',
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ai_models_provider ON ai_models(provider);
CREATE INDEX IF NOT EXISTS idx_ai_models_name ON ai_models(name);

-- Agents / prompts: merged view of builtins + user prompts (backward-compat read layer)
CREATE TABLE IF NOT EXISTS agents (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    source          TEXT NOT NULL DEFAULT 'builtin',
    variable_defaults TEXT NOT NULL DEFAULT '[]',
    settings        TEXT NOT NULL DEFAULT '{}',
    is_active       INTEGER NOT NULL DEFAULT 1,
    raw_json        TEXT NOT NULL DEFAULT '{}',
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_agents_source ON agents(source);

-- Conversations: migrated from localStorage
CREATE TABLE IF NOT EXISTS conversations (
    id                      TEXT PRIMARY KEY,
    title                   TEXT NOT NULL DEFAULT 'New conversation',
    mode                    TEXT NOT NULL DEFAULT 'chat',
    model                   TEXT NOT NULL DEFAULT '',
    server_conversation_id  TEXT,
    route_mode              TEXT NOT NULL DEFAULT 'chat',
    agent_id                TEXT,
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at DESC);

-- Messages: one-to-many from conversations
CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'user',
    content         TEXT NOT NULL DEFAULT '',
    model           TEXT,
    tool_calls      TEXT,
    tool_results    TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at);

-- Tools: cached from matrx-ai tool registry / Supabase tools table
CREATE TABLE IF NOT EXISTS tools (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '[]',
    parameters  TEXT NOT NULL DEFAULT '{}',
    source      TEXT NOT NULL DEFAULT 'local',
    version     TEXT NOT NULL DEFAULT '1',
    raw_json    TEXT NOT NULL DEFAULT '{}',
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tools_name ON tools(name);
CREATE INDEX IF NOT EXISTS idx_tools_category ON tools(category);

-- Sync metadata: tracks last sync timestamps per entity type
CREATE TABLE IF NOT EXISTS sync_meta (
    entity_type     TEXT PRIMARY KEY,
    last_synced_at  TEXT,
    last_hash       TEXT,
    status          TEXT NOT NULL DEFAULT 'idle',
    error_message   TEXT,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Pending sync queue: local changes waiting to be pushed to cloud
CREATE TABLE IF NOT EXISTS sync_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    action      TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    attempts    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sync_queue_entity ON sync_queue(entity_type, created_at)
"""

# ------------------------------------------------------------------
# Migration 2: Extended tables — prompts, notes, auth, instance
# ------------------------------------------------------------------

_V2_EXTENDED = """
-- Auth tokens: persists the user JWT so Python survives restarts.
-- Single row keyed by 'current_user'. Both Python and React keep this in sync.
CREATE TABLE IF NOT EXISTS auth_tokens (
    key           TEXT PRIMARY KEY,
    access_token  TEXT NOT NULL,
    refresh_token TEXT,
    user_id       TEXT,
    expires_at    INTEGER,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Agent catalog: platform + user agents from AIDream /api/agents (JWT required).
-- Formerly "prompt builtins" from the now-removed /api/prompts/builtins endpoint;
-- table name kept for backward compatibility with existing local caches.
CREATE TABLE IF NOT EXISTS prompt_builtins (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL DEFAULT '',
    description       TEXT NOT NULL DEFAULT '',
    category          TEXT NOT NULL DEFAULT '',
    tags              TEXT NOT NULL DEFAULT '[]',
    variable_defaults TEXT NOT NULL DEFAULT '[]',
    settings          TEXT NOT NULL DEFAULT '{}',
    is_active         INTEGER NOT NULL DEFAULT 1,
    raw_json          TEXT NOT NULL DEFAULT '{}',
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_prompt_builtins_name ON prompt_builtins(name);
CREATE INDEX IF NOT EXISTS idx_prompt_builtins_category ON prompt_builtins(category);

-- Legacy user-prompts table. The unified /api/agents catalog now includes the
-- user's own agents, so this table is no longer written by the sync engine;
-- retained for schema/back-compat and diagnostic counts.
CREATE TABLE IF NOT EXISTS prompts (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL DEFAULT '',
    name              TEXT NOT NULL DEFAULT '',
    description       TEXT NOT NULL DEFAULT '',
    category          TEXT NOT NULL DEFAULT '',
    tags              TEXT NOT NULL DEFAULT '[]',
    variable_defaults TEXT NOT NULL DEFAULT '[]',
    settings          TEXT NOT NULL DEFAULT '{}',
    is_favorite       INTEGER NOT NULL DEFAULT 0,
    raw_json          TEXT NOT NULL DEFAULT '{}',
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_prompts_user ON prompts(user_id);
CREATE INDEX IF NOT EXISTS idx_prompts_name ON prompts(name);

-- Notes: local working copy of the user's notes (cloud is durable truth;
-- this table is the first-access replica — see docs/SYNC_CONTRACT.md)
CREATE TABLE IF NOT EXISTS notes (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT '',
    folder_id   TEXT,
    title       TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL DEFAULT '',
    content_hash TEXT,
    file_path   TEXT,
    is_deleted  INTEGER NOT NULL DEFAULT 0,
    is_pinned   INTEGER NOT NULL DEFAULT 0,
    tags        TEXT NOT NULL DEFAULT '[]',
    sync_version INTEGER NOT NULL DEFAULT 0,
    supabase_updated_at TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id, is_deleted);
CREATE INDEX IF NOT EXISTS idx_notes_folder ON notes(folder_id);
CREATE INDEX IF NOT EXISTS idx_notes_updated ON notes(updated_at DESC);

-- Note folders
CREATE TABLE IF NOT EXISTS note_folders (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT '',
    parent_id   TEXT,
    name        TEXT NOT NULL DEFAULT '',
    path        TEXT NOT NULL DEFAULT '',
    is_deleted  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_note_folders_user ON note_folders(user_id, is_deleted);

-- Note versions: snapshot history for a note
CREATE TABLE IF NOT EXISTS note_versions (
    id          TEXT PRIMARY KEY,
    note_id     TEXT NOT NULL,
    user_id     TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL DEFAULT '',
    content_hash TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_note_versions_note ON note_versions(note_id, created_at DESC);

-- Note shares
CREATE TABLE IF NOT EXISTS note_shares (
    id          TEXT PRIMARY KEY,
    note_id     TEXT NOT NULL,
    owner_id    TEXT NOT NULL DEFAULT '',
    shared_with TEXT NOT NULL DEFAULT '',
    permission  TEXT NOT NULL DEFAULT 'read',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_note_shares_note ON note_shares(note_id);
CREATE INDEX IF NOT EXISTS idx_note_shares_user ON note_shares(shared_with);

-- Note devices: registered devices for multi-device sync tracking
CREATE TABLE IF NOT EXISTS note_devices (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT '',
    device_id   TEXT NOT NULL DEFAULT '',
    device_name TEXT NOT NULL DEFAULT '',
    platform    TEXT NOT NULL DEFAULT '',
    last_seen   TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, device_id)
);

CREATE INDEX IF NOT EXISTS idx_note_devices_user ON note_devices(user_id);

-- App instance: single-row table describing this installation
-- Row key is always 'self'. Use INSERT OR REPLACE to update.
CREATE TABLE IF NOT EXISTS app_instance (
    key            TEXT PRIMARY KEY DEFAULT 'self',
    instance_id    TEXT NOT NULL DEFAULT '',
    instance_name  TEXT NOT NULL DEFAULT 'My Computer',
    user_id        TEXT NOT NULL DEFAULT '',
    platform       TEXT NOT NULL DEFAULT '',
    os_version     TEXT NOT NULL DEFAULT '',
    architecture   TEXT NOT NULL DEFAULT '',
    hostname       TEXT NOT NULL DEFAULT '',
    registered_at  TEXT,
    last_heartbeat TEXT,
    raw_json       TEXT NOT NULL DEFAULT '{}',
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- App settings: single-row JSON blob for all user/instance settings
-- Row key is always 'settings'. Use INSERT OR REPLACE to update.
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY DEFAULT 'settings',
    settings   TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# ------------------------------------------------------------------
# Migration 3: Conversation persistence tables for matrx-ai client mode
# ------------------------------------------------------------------

_V3_CONVERSATION_PERSISTENCE = """
-- User requests: one per AI interaction, status tracks lifecycle
CREATE TABLE IF NOT EXISTS user_requests (
    id                 TEXT PRIMARY KEY,
    conversation_id    TEXT NOT NULL,
    user_id            TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'pending',
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_user_requests_conv ON user_requests(conversation_id, created_at DESC);

-- Tool call logs: one row per tool invocation within a request
CREATE TABLE IF NOT EXISTS tool_call_logs (
    id             TEXT PRIMARY KEY,
    conversation_id TEXT,
    user_request_id TEXT,
    status         TEXT NOT NULL DEFAULT 'running',
    data           TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tool_call_logs_request ON tool_call_logs(user_request_id, created_at DESC);
"""

# ------------------------------------------------------------------
# Migration 4: Add user_id to agents table for per-user isolation
# ------------------------------------------------------------------

_V4_AGENTS_USER_ID = """
-- Add user_id column to agents so user-sourced agents can be filtered
-- per authenticated user.  Builtins always have user_id = '' (empty string).
ALTER TABLE agents ADD COLUMN user_id TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_agents_user_id ON agents(user_id);
"""

# ------------------------------------------------------------------
# Migration 5: Add category, tags, is_favorite to agents table
# ------------------------------------------------------------------

_V5_AGENTS_METADATA = """
-- category and tags are used for search/filtering in the AgentPicker UI.
-- is_favorite allows users to star their most-used agents.
ALTER TABLE agents ADD COLUMN category TEXT NOT NULL DEFAULT '';
ALTER TABLE agents ADD COLUMN tags     TEXT NOT NULL DEFAULT '[]';
ALTER TABLE agents ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_agents_category ON agents(category);
"""

# ------------------------------------------------------------------
# Migration 6: Notes sync metadata — offline-first sync support
# ------------------------------------------------------------------

_V6_NOTES_SYNC_METADATA = """
-- Per-note sync status for offline-first architecture.
-- sync_status: never_synced | synced | pending_push | failed | excluded
--   failed → the last cloud push raised; note is retried by list_pending_push
-- last_synced_at: timestamp of last successful sync for this note
ALTER TABLE notes ADD COLUMN sync_status TEXT NOT NULL DEFAULT 'never_synced';
ALTER TABLE notes ADD COLUMN last_synced_at TEXT;
ALTER TABLE notes ADD COLUMN sync_enabled INTEGER NOT NULL DEFAULT 1;
ALTER TABLE notes ADD COLUMN remote_content_hash TEXT;
ALTER TABLE notes ADD COLUMN folder_name TEXT NOT NULL DEFAULT 'General';
ALTER TABLE notes ADD COLUMN label TEXT NOT NULL DEFAULT '';
ALTER TABLE notes ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_notes_sync_status ON notes(sync_status);
CREATE INDEX IF NOT EXISTS idx_notes_file_path ON notes(file_path);
"""

# ------------------------------------------------------------------
# Migration 7: Local note versions — offline version history support
# ------------------------------------------------------------------

_V7_LOCAL_NOTE_VERSIONS = """
-- Add label and version_number to note_versions for local version history.
-- This makes version history work fully offline without Supabase.
ALTER TABLE note_versions ADD COLUMN label TEXT NOT NULL DEFAULT '';
ALTER TABLE note_versions ADD COLUMN version_number INTEGER NOT NULL DEFAULT 0;
ALTER TABLE note_versions ADD COLUMN change_source TEXT NOT NULL DEFAULT 'local';

CREATE INDEX IF NOT EXISTS idx_note_versions_number ON note_versions(note_id, version_number DESC);
"""

# ------------------------------------------------------------------
# Migration 8: Scrape pages — persistent local store for all scrapes
#
# Every scrape result is written here immediately (local-first).
# cloud_sync_status tracks whether it has been pushed to the remote
# scraper server:
#   pending   — not yet pushed (includes cloud failure backlog)
#   synced    — confirmed in server DB
#   failed    — cloud push failed N times; will retry on next startup
#
# Soft-delete: is_deleted=1 hides the row from normal queries but keeps
# it for history.  Hard delete is a separate explicit admin action.
# ------------------------------------------------------------------

_V8_SCRAPE_PAGES = """
CREATE TABLE IF NOT EXISTS scrape_pages (
    id                TEXT PRIMARY KEY,
    url               TEXT NOT NULL,
    page_name         TEXT NOT NULL,
    domain            TEXT NOT NULL DEFAULT '',
    content           TEXT NOT NULL DEFAULT '{}',
    char_count        INTEGER NOT NULL DEFAULT 0,
    content_type      TEXT NOT NULL DEFAULT 'html',
    scraped_at        TEXT NOT NULL DEFAULT (datetime('now')),
    cloud_sync_status TEXT NOT NULL DEFAULT 'pending',
    cloud_sync_at     TEXT,
    cloud_sync_error  TEXT,
    cloud_sync_attempts INTEGER NOT NULL DEFAULT 0,
    is_deleted        INTEGER NOT NULL DEFAULT 0,
    deleted_at        TEXT,
    user_id           TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_scrape_pages_url ON scrape_pages(url);
CREATE INDEX IF NOT EXISTS idx_scrape_pages_page_name ON scrape_pages(page_name);
CREATE INDEX IF NOT EXISTS idx_scrape_pages_domain ON scrape_pages(domain);
CREATE INDEX IF NOT EXISTS idx_scrape_pages_sync ON scrape_pages(cloud_sync_status, is_deleted);
CREATE INDEX IF NOT EXISTS idx_scrape_pages_scraped ON scrape_pages(scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_scrape_pages_user ON scrape_pages(user_id, is_deleted)
"""

# ------------------------------------------------------------------
# Migration 9: Universal download manager — persistent download queue
#
# Tracks all large file downloads (LLM models, Whisper models, image
# gen models, future file-sync items) across restarts.
#
# status lifecycle:
#   queued → active → completed
#                  → failed    (error_msg set)
#                  → cancelled (user-initiated)
#
# category values: 'llm' | 'whisper' | 'image_gen' | 'tts' | 'file_sync'
# ------------------------------------------------------------------

_V9_DOWNLOADS = """
CREATE TABLE IF NOT EXISTS downloads (
    id           TEXT PRIMARY KEY,
    category     TEXT NOT NULL,
    filename     TEXT NOT NULL,
    display_name TEXT NOT NULL,
    urls         TEXT NOT NULL DEFAULT '[]',
    total_bytes  INTEGER NOT NULL DEFAULT 0,
    bytes_done   INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'queued',
    error_msg    TEXT,
    priority     INTEGER NOT NULL DEFAULT 0,
    part_current INTEGER NOT NULL DEFAULT 1,
    part_total   INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    metadata     TEXT
);

CREATE INDEX IF NOT EXISTS idx_downloads_status   ON downloads(status);
CREATE INDEX IF NOT EXISTS idx_downloads_category ON downloads(category);
CREATE INDEX IF NOT EXISTS idx_downloads_created  ON downloads(created_at DESC)
"""

# ------------------------------------------------------------------
# Migration 10: Chat cutover to the canonical cloud mirror
#
# The bespoke conversations/messages/user_requests/tool_call_logs tables are
# replaced by the structural mirror of the cloud chat schema
# (chat.conversation / chat.message / chat.user_request / chat.tool_call —
# ATTACHed mirror files, see app/services/local_db/mirror.py; the mirror is
# attached BEFORE migrations run so this SQL can reference chat.*).
#
# Existing local data is copied into the canonical shape and every copied
# row is seeded into the sync_queue outbox so historical local-only chats
# get pushed to the cloud on the first sync. Then the bespoke tables are
# ANNIHILATED — no dual systems.
#
# Canonical-mapping notes (same rules as ConversationsRepo/MessagesRepo):
#   conversations.mode/route_mode/model -> conversation.config JSON
#   conversations.agent_id              -> conversation.initial_agent_id
#   messages.content (text)             -> message.content [{type,text}]
#   messages.model/tool_calls/results   -> message.metadata JSON
#   user_requests.conversation_id       -> user_request.metadata JSON
#     (the cloud user_request table has no conversation_id column)
#   user_requests.user_id               -> user_request.created_by
#     (the legacy owner column was cut cloud-side; created_by is canonical)
#   tool_call_logs.data (full dict)     -> canonical columns extracted,
#                                          whole blob kept in metadata
# ------------------------------------------------------------------

_V10_CHAT_MIRROR_CUTOVER = """
INSERT OR IGNORE INTO chat.conversation
    (id, title, config, status, initial_agent_id, source_app, created_at,
     updated_at, message_count, is_favorite, is_ephemeral, conversation_type,
     visibility, version, metadata, variables, overrides, cache_state,
     source_feature, exclude_from_kg)
SELECT id, title,
       json_object('mode', mode, 'route_mode', route_mode, 'model', COALESCE(model, '')),
       'active', agent_id, 'matrx_local', created_at, updated_at,
       0, 0, 0, 'standard', 'private', 1,
       CASE WHEN server_conversation_id IS NOT NULL
            THEN json_object('legacy_server_conversation_id', server_conversation_id)
            ELSE '{}' END,
       '{}', '{}', '{}', '', 0
FROM conversations;
INSERT OR IGNORE INTO chat.message
    (id, conversation_id, role, position, status, content, metadata, error,
     source, is_visible_to_user, is_visible_to_model, content_chars,
     tool_results_chars, created_at, updated_at, version)
SELECT m.id, m.conversation_id, m.role,
       (ROW_NUMBER() OVER (PARTITION BY m.conversation_id ORDER BY m.created_at, m.id)) - 1,
       'active',
       CASE WHEN m.content IS NULL OR m.content = '' THEN '[]'
            ELSE json_array(json_object('type', 'text', 'text', m.content)) END,
       json_patch(
           json_patch(
               json_object('model', m.model),
               CASE WHEN m.tool_calls IS NOT NULL AND json_valid(m.tool_calls)
                    THEN json_object('tool_calls', json(m.tool_calls)) ELSE '{}' END),
           CASE WHEN m.tool_results IS NOT NULL AND json_valid(m.tool_results)
                THEN json_object('tool_results', json(m.tool_results)) ELSE '{}' END),
       CASE WHEN m.error IS NOT NULL THEN json_object('message', m.error) END,
       'user',
       1, 1, length(COALESCE(m.content, '')), 0, m.created_at, m.created_at, 1
FROM messages m;
UPDATE chat.conversation
SET message_count = (SELECT COUNT(*) FROM chat.message m
                     WHERE m.conversation_id = chat.conversation.id);
INSERT OR IGNORE INTO chat.user_request
    (id, created_by, status, source_app, metadata, created_at, updated_at,
     last_activity_at, total_input_tokens, total_output_tokens,
     total_cached_tokens, total_tokens, iterations, total_tool_calls, version)
SELECT id, user_id, status, 'matrx_local',
       json_object('conversation_id', conversation_id),
       created_at, updated_at, updated_at, 0, 0, 0, 0, 1, 0, 1
FROM user_requests;
INSERT OR IGNORE INTO chat.tool_call
    (id, conversation_id, user_request_id, status, tool_name, tool_type,
     call_id, arguments, message_id, iteration, started_at, completed_at,
     metadata, created_at, updated_at, version)
SELECT id, conversation_id, user_request_id, status,
       CASE WHEN json_valid(data) THEN json_extract(data, '$.tool_name') END,
       CASE WHEN json_valid(data) THEN json_extract(data, '$.tool_type') END,
       CASE WHEN json_valid(data) THEN json_extract(data, '$.call_id') END,
       CASE WHEN json_valid(data) THEN json_extract(data, '$.arguments') END,
       CASE WHEN json_valid(data) THEN json_extract(data, '$.message_id') END,
       CASE WHEN json_valid(data) THEN json_extract(data, '$.iteration') END,
       CASE WHEN json_valid(data) THEN json_extract(data, '$.started_at') END,
       CASE WHEN json_valid(data) THEN json_extract(data, '$.completed_at') END,
       CASE WHEN json_valid(data) THEN json(data) ELSE json_object('raw', data) END,
       created_at, updated_at, 1
FROM tool_call_logs;
INSERT INTO sync_queue (entity_type, entity_id, action, payload)
SELECT 'chat.conversation', c.id, 'upsert', '{}' FROM conversations c
WHERE c.server_conversation_id IS NULL
  AND length(c.id) = 36 AND substr(c.id,9,1)='-' AND substr(c.id,14,1)='-'
  AND substr(c.id,19,1)='-' AND substr(c.id,24,1)='-'
  AND NOT EXISTS (SELECT 1 FROM sync_queue q
                  WHERE q.entity_type='chat.conversation' AND q.entity_id=c.id);
INSERT INTO sync_queue (entity_type, entity_id, action, payload)
SELECT 'chat.message', m.id, 'upsert', '{}' FROM messages m
WHERE length(m.id) = 36 AND substr(m.id,9,1)='-' AND substr(m.id,14,1)='-'
  AND substr(m.id,19,1)='-' AND substr(m.id,24,1)='-'
  AND EXISTS (SELECT 1 FROM sync_queue q
              WHERE q.entity_type='chat.conversation' AND q.entity_id=m.conversation_id)
  AND NOT EXISTS (SELECT 1 FROM sync_queue q
                  WHERE q.entity_type='chat.message' AND q.entity_id=m.id);
INSERT INTO sync_queue (entity_type, entity_id, action, payload)
SELECT 'chat.user_request', r.id, 'upsert', '{}' FROM user_requests r
WHERE length(r.id) = 36 AND substr(r.id,9,1)='-' AND substr(r.id,14,1)='-'
  AND substr(r.id,19,1)='-' AND substr(r.id,24,1)='-'
  AND EXISTS (SELECT 1 FROM sync_queue q
              WHERE q.entity_type='chat.conversation' AND q.entity_id=r.conversation_id)
  AND NOT EXISTS (SELECT 1 FROM sync_queue q
                  WHERE q.entity_type='chat.user_request' AND q.entity_id=r.id);
INSERT INTO sync_queue (entity_type, entity_id, action, payload)
SELECT 'chat.tool_call', t.id, 'upsert', '{}' FROM tool_call_logs t
WHERE length(t.id) = 36 AND substr(t.id,9,1)='-' AND substr(t.id,14,1)='-'
  AND substr(t.id,19,1)='-' AND substr(t.id,24,1)='-'
  AND EXISTS (SELECT 1 FROM sync_queue q
              WHERE q.entity_type='chat.conversation' AND q.entity_id=t.conversation_id)
  AND NOT EXISTS (SELECT 1 FROM sync_queue q
                  WHERE q.entity_type='chat.tool_call' AND q.entity_id=t.id)
"""

# ------------------------------------------------------------------
# Migration 12: annihilate the bespoke chat tables.
#
# Deliberately a SEPARATE migration from the V10 copy: SQLite transactions
# spanning the main DB and an ATTACHed WAL database are not atomic as a
# set, so copy-and-drop in one commit risks (crash-window) persisting the
# DROPs while losing the copies — the user's whole chat history. Split,
# the failure modes are safe: V10 is INSERT OR IGNORE + NOT EXISTS guards
# (idempotent re-run), and V11 only runs after V10 committed and recorded.
#
# Seeding rules (V10): only UUID-shaped ids reach the outbox (the cloud pk
# is uuid — legacy localStorage ids can never push), and conversations that
# already exist server-side under a DIFFERENT id (server_conversation_id
# set, preserved in metadata.legacy_server_conversation_id) are not pushed
# — pushing them under their local id would duplicate the conversation.
# Children only seed when their conversation seeded (RLS authorizes
# children through the conversation row).
# ------------------------------------------------------------------

_V12_CHAT_BESPOKE_DROP = """
DROP TABLE IF EXISTS tool_call_logs;
DROP TABLE IF EXISTS user_requests;
DROP TABLE IF EXISTS messages;
DROP TABLE IF EXISTS conversations
"""

# ------------------------------------------------------------------
# Migration 13: repair chat.message.source vocabulary (MXL-D-052).
#
# V10 (and MessagesRepo.create until this fix) wrote source='model' for
# every non-user role. The canonical vocabulary — aidream's Message model
# (db/models/chat.py, default='user') and the live cloud CHECK constraint
# cx_message_source_check — is 'user' | 'agent_template' | 'system':
# `source` records the ORIGIN of the row (user session / agent template /
# system injection), while `role` carries authorship. 'model' is illegal in
# the cloud, so every AI/assistant message 400'd on push and eventually
# dead-lettered — synced conversations held only the user's turns.
#
# Repair, in order (order matters — the enqueue predicate is source='model'):
#   1. Drop the poisoned queue entries (pending AND dead-lettered) for the
#      affected messages so step 2 can seed fresh pending pushes.
#   2. Re-enqueue every affected pushable message: UUID-shaped id (cloud pk
#      is uuid) and not a child of a legacy conversation that lives
#      server-side under a different id (V10 seeding rule — pushing those
#      under the local conversation id would fail or duplicate).
#   3. Remap source to the canonical 'user'.
# ------------------------------------------------------------------

_V13_MESSAGE_SOURCE_REPAIR = """
DELETE FROM sync_queue
WHERE entity_type = 'chat.message'
  AND entity_id IN (SELECT id FROM chat.message WHERE source = 'model');
INSERT INTO sync_queue (entity_type, entity_id, action, payload)
SELECT 'chat.message', m.id, 'upsert', '{}'
FROM chat.message m
WHERE m.source = 'model'
  AND length(m.id) = 36 AND substr(m.id,9,1)='-' AND substr(m.id,14,1)='-'
  AND substr(m.id,19,1)='-' AND substr(m.id,24,1)='-'
  AND NOT EXISTS (SELECT 1 FROM chat.conversation c
                  WHERE c.id = m.conversation_id
                    AND json_extract(c.metadata,
                        '$.legacy_server_conversation_id') IS NOT NULL);
UPDATE chat.message SET source = 'user' WHERE source = 'model'
"""

# ------------------------------------------------------------------
# Migration 14: Complete executable-agent definitions
#
# The `agents` table is intentionally a picker/listing projection. It must
# never again be interpreted as an executable prompt. This separate cache
# stores the opaque canonical definition returned by AIDream and consumed by
# matrx-ai's ExecutionAgentSource seam.
# ------------------------------------------------------------------

_V14_AGENT_EXECUTION_DEFINITIONS = """
CREATE TABLE IF NOT EXISTS agent_execution_definitions (
    definition_id  TEXT NOT NULL,
    is_version     INTEGER NOT NULL DEFAULT 0,
    definition_json TEXT NOT NULL,
    definition_hash TEXT NOT NULL,
    revision       TEXT,
    fetched_at     TEXT NOT NULL,
    PRIMARY KEY (definition_id, is_version)
);

CREATE INDEX IF NOT EXISTS idx_agent_execution_definitions_fetched
ON agent_execution_definitions(fetched_at)
"""

# ------------------------------------------------------------------
# All migrations in order
# ------------------------------------------------------------------

# ------------------------------------------------------------------
# Migration 11: File sync — local state sidecar for the files.* mirror
#
# The cloud rows live in the ATTACHed structural mirror (files.files /
# files.folders — schema_mirror/snapshot.json is the spec). This table is
# the LOCAL half the mirror deliberately does not carry: per-file on-disk
# state for the ~/Documents/Matrx/Files replica.
#
# file_id is the cloud files.files id, or 'local:<uuid>' for a file created
# on this machine that has not been uploaded yet (re-keyed to the cloud id
# when the upload lands).
#
# local_state lifecycle:
#   pointer      → placeholder on disk, bytes on demand (virtual mapping)
#   synced       → bytes on disk match last_synced_hash
#   pending_push → local bytes changed / created / deleted; pending_op says how
#   conflict     → both sides changed; both copies preserved (.sync/conflicts)
#
# last_synced_hash follows the notes doctrine: the cloud checksum at the
# last SUCCESSFUL sync, written only after a push landed or a pull wrote
# the file (docs/SYNC_CONTRACT.md).
# ------------------------------------------------------------------

_V11_FILE_SYNC_STATE = """
CREATE TABLE IF NOT EXISTS file_sync_state (
    file_id          TEXT PRIMARY KEY,
    rel_path         TEXT NOT NULL,
    local_state      TEXT NOT NULL DEFAULT 'pointer',
    pending_op       TEXT,
    local_hash       TEXT,
    local_size       INTEGER,
    local_mtime      REAL,
    last_synced_hash TEXT,
    error            TEXT,
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_file_sync_state_path ON file_sync_state(rel_path);
CREATE INDEX IF NOT EXISTS idx_file_sync_state_state ON file_sync_state(local_state);
CREATE INDEX IF NOT EXISTS idx_file_sync_state_pending ON file_sync_state(pending_op)
    WHERE pending_op IS NOT NULL
"""

_V15_DELEGATION_OUTBOX = """
CREATE TABLE IF NOT EXISTS delegation_outbox (
    call_id          TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL,
    user_request_id  TEXT NOT NULL DEFAULT '',
    tool_name        TEXT NOT NULL,
    state            TEXT NOT NULL DEFAULT 'queued',
    owner_id         TEXT,
    lease_expires_at TEXT,
    result_payload   TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_delegation_outbox_state
    ON delegation_outbox(state, created_at);
"""

# ------------------------------------------------------------------
# Migration 16: local-first media artifacts
#
# Screenshot bytes are committed here before a local tool call succeeds.
# Cloud publication is an independent, always-on outbox and deliberately does
# not inherit the optional Files replica mode: these records are required to
# make a locally-produced tool result portable when its conversation later
# resumes in the cloud.
# ------------------------------------------------------------------

_V16_LOCAL_ARTIFACTS = """
CREATE TABLE IF NOT EXISTS local_artifacts (
    artifact_id      TEXT PRIMARY KEY,
    kind             TEXT NOT NULL DEFAULT 'image_ref',
    local_path       TEXT,
    media_type       TEXT NOT NULL,
    file_name        TEXT NOT NULL,
    size_bytes       INTEGER NOT NULL,
    checksum         TEXT NOT NULL,
    source_width     INTEGER NOT NULL,
    source_height    INTEGER NOT NULL,
    capture_source   TEXT NOT NULL,
    capture_json     TEXT NOT NULL DEFAULT '{}',
    sync_state       TEXT NOT NULL DEFAULT 'sync_pending',
    cloud_file_id    TEXT,
    url              TEXT,
    cdn_url          TEXT,
    -- signed_url column retired 2026-08-26 (signed URLs eradicated platform-
    -- wide; durable url/cdn_url/download_url only). Existing local DBs keep a
    -- vestigial NULL column; nothing reads or writes it.
    download_url     TEXT,
    visibility       TEXT NOT NULL DEFAULT 'private',
    sync_attempts    INTEGER NOT NULL DEFAULT 0,
    sync_error       TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    published_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_local_artifacts_sync
    ON local_artifacts(sync_state, created_at);
CREATE INDEX IF NOT EXISTS idx_local_artifacts_cloud_file
    ON local_artifacts(cloud_file_id)
    WHERE cloud_file_id IS NOT NULL;
"""

# Provider validation used to be persisted and rendered on later launches as
# though it were current truth. Validation is now explicit and session-only;
# remove the misleading historical blob once when upgrading.
_V17_DROP_STALE_API_KEY_VALIDATION = """
UPDATE app_settings
SET settings = json_remove(settings, '$.api_key_validation'),
    updated_at = datetime('now')
WHERE key = 'settings'
  AND json_type(settings, '$.api_key_validation') IS NOT NULL
"""

# Cloud scrape sync: separate "cannot push YET" from "push REJECTED".
#
# Before this migration every unsuccessful push — no signed-in user, a dead
# gateway, an expired token — burned one of five retries and then parked the
# row in terminal 'failed'. Two whole classes of blocker resolve themselves
# (sign in / the server comes back), so a retry budget was the wrong tool for
# them; `cloud_sync_blocked_reason` names the blocker instead and leaves the
# row queued.
#
# The UPDATE is one-time recovery for rows stranded by the client-side bug this
# migration ships with: `save_content` never sent the `page_name` the server
# marks required, so EVERY push since the 2026-04-29 /api/v1 → /api/scraper
# migration answered 422 and every affected row ran itself out to terminal.
# Those rows are only "permanently rejected" in the sense that a fixed client
# never got to ask again — so clear the counter and let them re-push.
_V18_SCRAPE_SYNC_BLOCK_REASON = """
ALTER TABLE scrape_pages ADD COLUMN cloud_sync_blocked_reason TEXT;

UPDATE scrape_pages
SET cloud_sync_status = 'pending',
    cloud_sync_attempts = 0,
    cloud_sync_error = NULL,
    cloud_sync_blocked_reason = NULL
WHERE cloud_sync_status = 'failed'
  AND is_deleted = 0
"""

# Coding-agent command hooks must not wait on network availability. The exact
# validated adapter envelope lands here before the loopback ingress returns
# 202; an engine-owned publisher later replays the oldest row unchanged to
# aidream. This is deliberately a local outbox, not a chat.* structural mirror:
# raw provider ledgers remain owner-only in cloud Postgres and are never pulled.
_V19_CODING_SESSION_BRIDGE_OUTBOX = """
CREATE TABLE IF NOT EXISTS coding_session_bridge_outbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key      TEXT,
    envelope_json   TEXT NOT NULL,
    envelope_sha256 TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL DEFAULT 0,
    last_error      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_coding_session_bridge_dedupe
ON coding_session_bridge_outbox(dedupe_key)
WHERE dedupe_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_coding_session_bridge_order
ON coding_session_bridge_outbox(id)
"""

# Claude Code owns the label it shows in its own sidebar; AI Matrx must show
# the SAME one and keep showing it after a rename. The pull-sync reconciler
# records the exact label payload the cloud last acknowledged per bound session
# so an unchanged title costs no network work and a rename is detected on the
# next pass. V25 clears legacy enqueue-time rows once when this becomes an
# acknowledgement ledger; it is not a cache of provider state.
_V20_CLAUDE_SESSION_METADATA_SENT = """
CREATE TABLE IF NOT EXISTS claude_session_metadata_sent (
    provider_session_id TEXT PRIMARY KEY,
    payload_sha256      TEXT NOT NULL,
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

# The RETURN direction of the same rule: a rename made in AI Matrx must reach
# Claude Code's own session index. This send-ledger records the exact title
# last written down per bound session, so an unchanged label costs zero file
# work and only a genuine rename reopens another application's data. It is a
# record of what WE wrote, never a cache of what Claude currently shows —
# Claude's index is always re-read for that.
_V21_CLAUDE_SESSION_TITLE_PUSHED = """
CREATE TABLE IF NOT EXISTS claude_session_title_pushed (
    provider_session_id TEXT PRIMARY KEY,
    title_sha256        TEXT NOT NULL,
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

# A Claude Code hook that fails is NON-BLOCKING: the coding session keeps
# running and mirrors nothing, forever, with no error anywhere (a 23.5-hour
# outage on 2026-08-16 was noticed only because timestamps looked wrong). The
# capture reconciler closes that hole by backfilling sessions the hook path
# never delivered, through the SAME durable outbox and the SAME import path the
# explicit history sync already uses. This is its attempt ledger: one row per
# local session it has enqueued, so a permanently unimportable session is
# retried a bounded number of times instead of spinning on every pass forever.
_V22_CLAUDE_CAPTURE_BACKFILL = """
CREATE TABLE IF NOT EXISTS claude_capture_backfill (
    session_key     TEXT PRIMARY KEY,
    source_revision TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    enqueued_at     TEXT,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

# THE POISON-ROW QUARANTINE. The bridge outbox publishes in strict insertion
# order and never reorders a provider event stream — correct, and the reason a
# single permanently-rejected row stops EVERYTHING behind it. Found live
# 2026-08-17: one row had failed 2,520 times since 2026-08-13 with HTTP 409
# `entry_mutated` (the server already holds that stable event id with different
# bytes, so the local copy can never be accepted) and had blocked 3,709 rows
# for four days, silently. Retrying a permanent rejection forever is not
# durability — it is a stall.
#
# A terminal row moves HERE and leaves the queue, so ordering is preserved for
# everything still deliverable. It is never deleted: the exact envelope stays
# for inspection, because zero data loss is the outbox's whole contract.
_V23_CODING_SESSION_BRIDGE_QUARANTINE = """
CREATE TABLE IF NOT EXISTS coding_session_bridge_quarantine (
    id               INTEGER PRIMARY KEY,
    envelope_json    TEXT NOT NULL,
    envelope_sha256  TEXT NOT NULL,
    attempts         INTEGER NOT NULL DEFAULT 0,
    http_status      INTEGER,
    last_error       TEXT,
    original_created_at TEXT,
    quarantined_at   TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

# Bounded, payload-free delivery truth for the Coding Sessions UI. Successful
# publication deletes the durable outbox row, so queue inspection alone can
# only say what is waiting — it cannot prove what aidream acknowledged. One
# aggregate row per provider/action/source retains the latest enqueue and
# acknowledgement receipt plus small response counts. It deliberately stores
# no provider session id, project path, envelope, transcript, or server body.
_V24_CODING_SESSION_BRIDGE_DELIVERY_ACTIVITY = """
CREATE TABLE IF NOT EXISTS coding_session_bridge_delivery_activity (
    provider                       TEXT NOT NULL,
    action                         TEXT NOT NULL,
    source                         TEXT NOT NULL,
    last_enqueued_at               TEXT,
    last_enqueued_receipt_id       INTEGER,
    last_acknowledged_at           TEXT,
    last_acknowledged_receipt_id   INTEGER,
    last_acknowledged_accepted     INTEGER,
    last_acknowledged_duplicates   INTEGER,
    last_acknowledged_fidelity     TEXT,
    acknowledged_envelopes         INTEGER NOT NULL DEFAULT 0,
    updated_at                     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (provider, action, source)
)
"""

# Ordered publication is scoped to one logical provider session stream, not to
# every coding surface on the machine. A deferred Claude session must not stop
# a Codex session (or even another Claude session) from reaching the cloud.
# The key is a canonical JSON array of provider, opaque project key, opaque
# session identity, and a discriminator. Every stream/action in a real session
# uses the fixed `$session` discriminator; sessionless requests use their
# action plus source. It is local scheduling metadata only and is never exposed
# through status.
_V25_CODING_SESSION_BRIDGE_DELIVERY_LANES = """
ALTER TABLE coding_session_bridge_outbox ADD COLUMN lane_key TEXT;

UPDATE coding_session_bridge_outbox
SET lane_key = CASE
    WHEN json_valid(envelope_json) THEN json_array(
        COALESCE(json_extract(envelope_json, '$.provider'), 'unknown'),
        COALESCE(json_extract(envelope_json, '$.provider_project_key'), ''),
        CASE
            WHEN json_extract(envelope_json, '$.provider_session_id') IS NOT NULL
                THEN json_extract(envelope_json, '$.provider_session_id')
            ELSE '$action:' || COALESCE(
                json_extract(envelope_json, '$.action'), 'unknown'
            )
        END,
        CASE
            WHEN json_extract(envelope_json, '$.provider_session_id') IS NOT NULL
                THEN '$session'
            ELSE COALESCE(
                json_extract(envelope_json, '$.source_metadata.source_kind'),
                json_extract(envelope_json, '$.origin'),
                'unspecified'
            )
        END
    )
    ELSE json_array('unknown', '', '$row:' || id, 'main')
END
WHERE lane_key IS NULL;

CREATE INDEX IF NOT EXISTS idx_coding_session_bridge_lane_order
ON coding_session_bridge_outbox(lane_key, id);

-- Before V25 this ledger was written at local enqueue time. It now means a
-- validated cloud acknowledgement, so legacy rows cannot be trusted. Clearing
-- it causes one idempotent metadata reconciliation and repairs any label that
-- was queued but never delivered.
DELETE FROM claude_session_metadata_sent
"""

# Queue status must stay cheap regardless of transcript volume. Parsing the
# raw envelope column on every status request turned a 1.2 GB durable queue
# into a multi-second full-table scan and let overlapping UI polls monopolize
# the single shared SQLite connection. This payload-free side index is written
# in the SAME durable transaction as each outbox mutation. It also carries
# local-only enqueue provenance so an explicit-history discard can never erase
# automatic recovery or local-runtime mirrors.
_V26_CODING_SESSION_BRIDGE_QUEUE_METADATA = """
CREATE TABLE IF NOT EXISTS coding_session_bridge_queue_metadata (
    receipt_id      INTEGER PRIMARY KEY,
    queue_state     TEXT NOT NULL CHECK (queue_state IN ('pending', 'quarantine')),
    provider        TEXT NOT NULL,
    action          TEXT NOT NULL,
    source          TEXT NOT NULL,
    enqueue_origin  TEXT NOT NULL,
    session_key     TEXT,
    payload_bytes   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_coding_session_bridge_queue_dimensions
ON coding_session_bridge_queue_metadata(
    queue_state, provider, action, source, enqueue_origin
);

CREATE INDEX IF NOT EXISTS idx_coding_session_bridge_queue_sessions
ON coding_session_bridge_queue_metadata(queue_state, provider, session_key);

INSERT OR REPLACE INTO coding_session_bridge_queue_metadata (
    receipt_id, queue_state, provider, action, source, enqueue_origin,
    session_key, payload_bytes, created_at
)
SELECT
    id,
    'pending',
    CASE WHEN json_valid(envelope_json)
         THEN COALESCE(json_extract(envelope_json, '$.provider'), 'unknown')
         ELSE 'unknown' END,
    CASE WHEN json_valid(envelope_json)
         THEN COALESCE(json_extract(envelope_json, '$.action'), 'unknown')
         ELSE 'unknown' END,
    CASE WHEN json_valid(envelope_json)
         THEN COALESCE(
             json_extract(envelope_json, '$.source_metadata.source_kind'),
             json_extract(envelope_json, '$.origin'),
             'unspecified'
         ) ELSE 'invalid' END,
    CASE
        WHEN json_valid(envelope_json)
         AND json_extract(envelope_json, '$.action') = 'append_native'
         AND json_extract(envelope_json, '$.source_metadata.source_kind') =
             'claude_local_jsonl'
            THEN 'explicit_history'
        WHEN json_valid(envelope_json)
         AND json_extract(envelope_json, '$.action') = 'observe_hook'
            THEN 'live_hook'
        ELSE 'unspecified'
    END,
    CASE WHEN json_valid(envelope_json)
         THEN COALESCE(
             json_extract(envelope_json, '$.provider_session_id'),
             json_extract(envelope_json, '$.stream_key')
         ) ELSE NULL END,
    length(CAST(envelope_json AS BLOB)),
    created_at
FROM coding_session_bridge_outbox;

INSERT OR REPLACE INTO coding_session_bridge_queue_metadata (
    receipt_id, queue_state, provider, action, source, enqueue_origin,
    session_key, payload_bytes, created_at
)
SELECT
    id,
    'quarantine',
    CASE WHEN json_valid(envelope_json)
         THEN COALESCE(json_extract(envelope_json, '$.provider'), 'unknown')
         ELSE 'unknown' END,
    CASE WHEN json_valid(envelope_json)
         THEN COALESCE(json_extract(envelope_json, '$.action'), 'unknown')
         ELSE 'unknown' END,
    CASE WHEN json_valid(envelope_json)
         THEN COALESCE(
             json_extract(envelope_json, '$.source_metadata.source_kind'),
             json_extract(envelope_json, '$.origin'),
             'unspecified'
         ) ELSE 'invalid' END,
    CASE
        WHEN json_valid(envelope_json)
         AND json_extract(envelope_json, '$.action') = 'append_native'
         AND json_extract(envelope_json, '$.source_metadata.source_kind') =
             'claude_local_jsonl'
            THEN 'explicit_history'
        WHEN json_valid(envelope_json)
         AND json_extract(envelope_json, '$.action') = 'observe_hook'
            THEN 'live_hook'
        ELSE 'unspecified'
    END,
    CASE WHEN json_valid(envelope_json)
         THEN COALESCE(
             json_extract(envelope_json, '$.provider_session_id'),
             json_extract(envelope_json, '$.stream_key')
         ) ELSE NULL END,
    length(CAST(envelope_json AS BLOB)),
    COALESCE(original_created_at, quarantined_at)
FROM coding_session_bridge_quarantine
"""

# A review is a durable, immutable inventory snapshot rather than a transient
# list.  Keeping the row-level comparison locally lets the desktop explain
# exactly what was new, changed, missing, or unchanged without re-reading every
# transcript or sending filesystem inventory to the cloud.
_V27_CODING_SESSION_HISTORY_SCANS = """
CREATE TABLE IF NOT EXISTS coding_session_history_scans (
    scan_id               TEXT PRIMARY KEY,
    provider              TEXT NOT NULL,
    provider_account_key  TEXT,
    previous_scan_id      TEXT,
    status                TEXT NOT NULL CHECK (status IN ('scanning', 'completed', 'failed')),
    started_at            TEXT NOT NULL,
    completed_at          TEXT,
    session_count         INTEGER NOT NULL DEFAULT 0,
    present_count         INTEGER NOT NULL DEFAULT 0,
    new_count             INTEGER NOT NULL DEFAULT 0,
    content_changed_count INTEGER NOT NULL DEFAULT 0,
    metadata_changed_count INTEGER NOT NULL DEFAULT 0,
    missing_count         INTEGER NOT NULL DEFAULT 0,
    unchanged_count       INTEGER NOT NULL DEFAULT 0,
    blocked_count         INTEGER NOT NULL DEFAULT 0,
    file_count            INTEGER NOT NULL DEFAULT 0,
    project_count         INTEGER NOT NULL DEFAULT 0,
    total_bytes           INTEGER NOT NULL DEFAULT 0,
    error_message         TEXT
);

CREATE INDEX IF NOT EXISTS idx_coding_session_history_scans_latest
ON coding_session_history_scans(provider, provider_account_key, started_at DESC);

CREATE TABLE IF NOT EXISTS coding_session_history_scan_rows (
    scan_id               TEXT NOT NULL,
    session_id            TEXT NOT NULL,
    project_key           TEXT NOT NULL,
    present               INTEGER NOT NULL DEFAULT 1,
    change_type           TEXT NOT NULL CHECK (change_type IN (
        'new', 'content_changed', 'metadata_changed', 'missing', 'unchanged'
    )),
    source_state          TEXT,
    source_revision       TEXT,
    title                 TEXT NOT NULL,
    title_source          TEXT,
    project_name          TEXT NOT NULL,
    git_branch            TEXT,
    worktree_name         TEXT,
    is_archived           INTEGER,
    is_pinned             INTEGER,
    pinned_rank           INTEGER,
    category              TEXT,
    payload_bytes         INTEGER NOT NULL DEFAULT 0,
    file_count            INTEGER NOT NULL DEFAULT 0,
    subagent_count        INTEGER NOT NULL DEFAULT 0,
    last_modified_ns      INTEGER NOT NULL DEFAULT 0,
    import_available      INTEGER NOT NULL DEFAULT 0,
    import_blocked_reason TEXT,
    PRIMARY KEY (scan_id, session_id, project_key),
    FOREIGN KEY (scan_id) REFERENCES coding_session_history_scans(scan_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_coding_session_history_rows_page
ON coding_session_history_scan_rows(scan_id, present, last_modified_ns DESC);

CREATE INDEX IF NOT EXISTS idx_coding_session_history_rows_change
ON coding_session_history_scan_rows(scan_id, change_type, last_modified_ns DESC);
"""

# One durable envelope can carry many native transcript entries. Counts in the
# delivery inspector must distinguish envelopes from logical items without
# JSON-parsing the payload column during every status request.
_V28_CODING_SESSION_BRIDGE_QUEUE_ITEM_COUNT = """
ALTER TABLE coding_session_bridge_queue_metadata
ADD COLUMN item_count INTEGER NOT NULL DEFAULT 1;

UPDATE coding_session_bridge_queue_metadata
SET item_count = CASE
    WHEN queue_state = 'pending' THEN COALESCE((
        SELECT CASE
            WHEN json_valid(outbox.envelope_json)
             AND json_type(outbox.envelope_json, '$.entries') = 'array'
                THEN json_array_length(outbox.envelope_json, '$.entries')
            WHEN json_valid(outbox.envelope_json)
             AND json_type(outbox.envelope_json, '$.hook_event') = 'object'
                THEN 1
            ELSE 0
        END
        FROM coding_session_bridge_outbox AS outbox
        WHERE outbox.id = coding_session_bridge_queue_metadata.receipt_id
    ), 0)
    ELSE COALESCE((
        SELECT CASE
            WHEN json_valid(preserved.envelope_json)
             AND json_type(preserved.envelope_json, '$.entries') = 'array'
                THEN json_array_length(preserved.envelope_json, '$.entries')
            WHEN json_valid(preserved.envelope_json)
             AND json_type(preserved.envelope_json, '$.hook_event') = 'object'
                THEN 1
            ELSE 0
        END
        FROM coding_session_bridge_quarantine AS preserved
        WHERE preserved.id = coding_session_bridge_queue_metadata.receipt_id
    ), 0)
END;
"""

# Every session-details pass is an inspectable operation. Row comparisons are
# local-only metadata (never transcript content) and push intents are committed
# before another application's files are opened for mutation.
_V29_CODING_SESSION_METADATA_SYNC_OPERATIONS = """
CREATE TABLE IF NOT EXISTS coding_session_metadata_sync_operations (
    operation_id       TEXT PRIMARY KEY,
    mode               TEXT NOT NULL CHECK (mode IN ('preview', 'apply', 'verify', 'retry')),
    status             TEXT NOT NULL CHECK (status IN ('running', 'completed', 'partial', 'failed')),
    started_at         TEXT NOT NULL,
    completed_at       TEXT,
    parent_operation_id TEXT,
    bound_sessions     INTEGER NOT NULL DEFAULT 0,
    compared_sessions  INTEGER NOT NULL DEFAULT 0,
    detected_sessions  INTEGER NOT NULL DEFAULT 0,
    enqueued_sessions  INTEGER NOT NULL DEFAULT 0,
    acknowledged_sessions INTEGER NOT NULL DEFAULT 0,
    verified_sessions  INTEGER NOT NULL DEFAULT 0,
    failed_sessions    INTEGER NOT NULL DEFAULT 0,
    index_files        INTEGER NOT NULL DEFAULT 0,
    index_records      INTEGER NOT NULL DEFAULT 0,
    index_unreadable   INTEGER NOT NULL DEFAULT 0,
    index_truncated    INTEGER NOT NULL DEFAULT 0,
    index_writable     INTEGER NOT NULL DEFAULT 0,
    error_message      TEXT
);

CREATE INDEX IF NOT EXISTS idx_coding_session_metadata_sync_latest
ON coding_session_metadata_sync_operations(started_at DESC);

CREATE TABLE IF NOT EXISTS coding_session_metadata_sync_rows (
    operation_id       TEXT NOT NULL,
    provider_session_id TEXT NOT NULL,
    session_ref        TEXT NOT NULL,
    local_values_json  TEXT NOT NULL,
    cloud_values_json  TEXT NOT NULL,
    chosen_values_json TEXT NOT NULL,
    direction          TEXT NOT NULL,
    action             TEXT NOT NULL,
    reason             TEXT NOT NULL,
    state              TEXT NOT NULL,
    receipt_id         INTEGER,
    write_intent_id    TEXT,
    outcome_json       TEXT,
    PRIMARY KEY (operation_id, provider_session_id),
    FOREIGN KEY (operation_id) REFERENCES coding_session_metadata_sync_operations(operation_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_coding_session_metadata_sync_rows_state
ON coding_session_metadata_sync_rows(operation_id, state, session_ref);

CREATE TABLE IF NOT EXISTS coding_session_title_push_intents (
    intent_id           TEXT PRIMARY KEY,
    operation_id        TEXT NOT NULL,
    provider_session_id TEXT NOT NULL,
    cli_session_id      TEXT NOT NULL,
    desired_title       TEXT NOT NULL,
    desired_payload_json TEXT NOT NULL,
    status              TEXT NOT NULL,
    receipt_id          INTEGER,
    copy_outcomes_json  TEXT,
    error_message       TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    FOREIGN KEY (operation_id) REFERENCES coding_session_metadata_sync_operations(operation_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_coding_session_title_push_pending
ON coding_session_title_push_intents(status, updated_at);
"""

# Local provider executions must remain inspectable after an engine restart.
# The journal stores product-visible metadata plus redacted event evidence; it
# never stores the raw start prompt or durable SDK message content.
_V30_CODING_SESSION_RUNTIME_JOURNAL = """
CREATE TABLE IF NOT EXISTS coding_session_runtime_runs (
    runtime_id            TEXT PRIMARY KEY,
    session_id            TEXT NOT NULL,
    workspace             TEXT NOT NULL,
    action                TEXT NOT NULL CHECK (action IN ('start', 'resume')),
    status                TEXT NOT NULL CHECK (status IN (
        'starting', 'running', 'completed', 'failed', 'cancelled', 'interrupted'
    )),
    prompt_preview        TEXT NOT NULL DEFAULT '',
    provider_session_id   TEXT,
    provider_project_key  TEXT,
    conversation_id       TEXT,
    transcript_path       TEXT,
    execution_error       TEXT,
    mirror_passes         INTEGER NOT NULL DEFAULT 0,
    mirror_error          TEXT,
    cancel_requested      INTEGER NOT NULL DEFAULT 0,
    started_at            REAL NOT NULL,
    ended_at              REAL,
    turns_completed       INTEGER NOT NULL DEFAULT 0,
    next_sequence         INTEGER NOT NULL DEFAULT 1,
    restart_reason        TEXT,
    runtime_config_json   TEXT NOT NULL,
    updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_coding_session_runtime_runs_recent
ON coding_session_runtime_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS coding_session_runtime_events (
    runtime_id            TEXT NOT NULL,
    sequence              INTEGER NOT NULL,
    emitted_at            TEXT NOT NULL,
    event_json            TEXT NOT NULL,
    PRIMARY KEY (runtime_id, sequence),
    FOREIGN KEY (runtime_id) REFERENCES coding_session_runtime_runs(runtime_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_coding_session_runtime_events_replay
ON coding_session_runtime_events(runtime_id, sequence);
"""

MIGRATIONS: list[tuple[int, str]] = [
    (1, _V1_CORE),
    (2, _V2_EXTENDED),
    (3, _V3_CONVERSATION_PERSISTENCE),
    (4, _V4_AGENTS_USER_ID),
    (5, _V5_AGENTS_METADATA),
    (6, _V6_NOTES_SYNC_METADATA),
    (7, _V7_LOCAL_NOTE_VERSIONS),
    (8, _V8_SCRAPE_PAGES),
    (9, _V9_DOWNLOADS),
    (10, _V10_CHAT_MIRROR_CUTOVER),
    (11, _V11_FILE_SYNC_STATE),
    (12, _V12_CHAT_BESPOKE_DROP),
    (13, _V13_MESSAGE_SOURCE_REPAIR),
    (14, _V14_AGENT_EXECUTION_DEFINITIONS),
    (15, _V15_DELEGATION_OUTBOX),
    (16, _V16_LOCAL_ARTIFACTS),
    (17, _V17_DROP_STALE_API_KEY_VALIDATION),
    (18, _V18_SCRAPE_SYNC_BLOCK_REASON),
    (19, _V19_CODING_SESSION_BRIDGE_OUTBOX),
    (20, _V20_CLAUDE_SESSION_METADATA_SENT),
    (21, _V21_CLAUDE_SESSION_TITLE_PUSHED),
    (22, _V22_CLAUDE_CAPTURE_BACKFILL),
    (23, _V23_CODING_SESSION_BRIDGE_QUARANTINE),
    (24, _V24_CODING_SESSION_BRIDGE_DELIVERY_ACTIVITY),
    (25, _V25_CODING_SESSION_BRIDGE_DELIVERY_LANES),
    (26, _V26_CODING_SESSION_BRIDGE_QUEUE_METADATA),
    (27, _V27_CODING_SESSION_HISTORY_SCANS),
    (28, _V28_CODING_SESSION_BRIDGE_QUEUE_ITEM_COUNT),
    (29, _V29_CODING_SESSION_METADATA_SYNC_OPERATIONS),
    (30, _V30_CODING_SESSION_RUNTIME_JOURNAL),
]
