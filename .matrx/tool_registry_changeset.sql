-- ═══════════════════════════════════════════════════════════════════
-- matrx-local tool registry changeset
-- generated: 2026-07-10T14:40:49+00:00 by app/tools/tool_sync.py emit-changeset
-- cloud baseline: embedded fallback baseline (route 404)
-- catalog: 108 tools; NEW=59 CHANGED=0 COLLISIONS=0 REMOVED=0 OK=0 UNVERIFIED=49
--
-- APPLY VIA SUPABASE MCP (project txzxabzwovsujtloxrus) AFTER REVIEW.
-- The desktop never writes tool.definition/tool.binding directly.
-- Wrap in a transaction; every statement is idempotent-safe to re-run
-- only as a whole (NEW inserts are not — do not apply twice).
-- ═══════════════════════════════════════════════════════════════════

BEGIN;

-- NEW: local_bluetooth_devices (dispatcher: BluetoothDevices)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_bluetooth_devices', 'List paired and nearby Bluetooth devices.', '{"properties": {"scan_duration": {"default": 5, "type": "integer"}}, "required": [], "type": "object"}'::jsonb, 'local_network', ARRAY['bluetooth', 'devices', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_browser_click (dispatcher: BrowserClick)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_browser_click', 'Click an element on the current browser page by CSS selector.', '{"properties": {"selector": {"description": "CSS selector of the element to click.", "title": "Selector", "type": "string"}, "timeout": {"default": 10, "description": "Seconds to wait for the element to appear before clicking.", "maximum": 60, "minimum": 1, "title": "Timeout", "type": "integer"}}, "required": ["selector"], "type": "object"}'::jsonb, 'local_browser', ARRAY['browser', 'click', 'playwright', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_browser_close (dispatcher: BrowserClose)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_browser_close', 'Close the browser instance(s) and free resources.', '{"properties": {"browser": {"type": "string"}}, "required": [], "type": "object"}'::jsonb, 'local_browser', ARRAY['browser', 'close', 'playwright', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_browser_eval (dispatcher: BrowserEval)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_browser_eval', 'Execute JavaScript in the current browser page context.', '{"properties": {"javascript": {"description": "JavaScript expression to evaluate in the page context. The return value is serialized.", "title": "Javascript", "type": "string"}}, "required": ["javascript"], "type": "object"}'::jsonb, 'local_browser', ARRAY['browser', 'javascript', 'playwright', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_browser_extract (dispatcher: BrowserExtract)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_browser_extract', 'Extract text, HTML, attributes, or form values from the current browser page.', '{"properties": {"all_matches": {"default": false, "description": "If true, return a list of all matching elements; otherwise return the first.", "title": "All Matches", "type": "boolean"}, "attribute": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": null, "description": "HTML attribute name to extract when extract_type=''attribute''.", "title": "Attribute"}, "extract_type": {"default": "text", "description": "What to extract: ''text'' (visible text), ''html'' (innerHTML), ''attribute'' (requires attribute param), ''value'' (form field value).", "enum": ["text", "html", "attribute", "value"], "title": "Extract Type", "type": "string"}, "selector": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": null, "description": "CSS selector to scope extraction. If omitted, extracts from full page.", "title": "Selector"}}, "required": [], "type": "object"}'::jsonb, 'local_browser', ARRAY['browser', 'extract', 'scrape', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_browser_navigate (dispatcher: BrowserNavigate)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_browser_navigate', 'Navigate the local Playwright-controlled browser to a URL.', '{"properties": {"timeout": {"default": 30, "description": "Seconds to wait for the page to load.", "maximum": 120, "minimum": 1, "title": "Timeout", "type": "integer"}, "url": {"description": "URL to navigate to (must include scheme: http/https).", "title": "Url", "type": "string"}, "wait_for": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": null, "description": "CSS selector to wait for after navigation before returning. Useful for SPAs or pages with lazy-loaded content.", "title": "Wait For"}}, "required": ["url"], "type": "object"}'::jsonb, 'local_browser', ARRAY['browser', 'navigate', 'playwright', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_browser_screenshot (dispatcher: BrowserScreenshot)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_browser_screenshot', 'Take a screenshot of the current browser page or a specific element.', '{"properties": {"full_page": {"default": false, "description": "If true, capture the full scrollable page instead of just the viewport.", "title": "Full Page", "type": "boolean"}, "selector": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": null, "description": "CSS selector of a specific element to screenshot.", "title": "Selector"}}, "required": [], "type": "object"}'::jsonb, 'local_browser', ARRAY['browser', 'screenshot', 'playwright', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_browser_tabs (dispatcher: BrowserTabs)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_browser_tabs', 'Manage browser tabs: list, open new, close, or switch to a tab.', '{"properties": {"action": {"default": "list", "description": "Tab action: ''list'' (list all tabs), ''new'' (open a new tab), ''close'' (close tab at tab_index), ''switch'' (focus tab at tab_index).", "enum": ["list", "new", "close", "switch"], "title": "Action", "type": "string"}, "tab_index": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": null, "description": "Zero-based tab index, required for ''close'' and ''switch'' actions.", "title": "Tab Index"}, "url": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": null, "description": "URL to load when action=''new''.", "title": "Url"}}, "required": [], "type": "object"}'::jsonb, 'local_browser', ARRAY['browser', 'tabs', 'playwright', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_browser_type (dispatcher: BrowserType)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_browser_type', 'Type text into an input element on the current browser page.', '{"properties": {"clear_first": {"default": true, "description": "Clear the field before typing.", "title": "Clear First", "type": "boolean"}, "press_enter": {"default": false, "description": "Press Enter after typing.", "title": "Press Enter", "type": "boolean"}, "selector": {"description": "CSS selector of the input element to type into.", "title": "Selector", "type": "string"}, "text": {"description": "Text to type.", "title": "Text", "type": "string"}, "timeout": {"default": 10, "description": "Seconds to wait for the element.", "maximum": 60, "minimum": 1, "title": "Timeout", "type": "integer"}}, "required": ["selector", "text"], "type": "object"}'::jsonb, 'local_browser', ARRAY['browser', 'type', 'playwright', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_cancel_scheduled (dispatcher: CancelScheduled)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_cancel_scheduled', 'Cancel a scheduled task.', '{"properties": {"task_id": {"type": "string"}}, "required": ["task_id"], "type": "object"}'::jsonb, 'local_scheduler', ARRAY['schedule', 'cancel', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_clipboard_read (dispatcher: ClipboardRead)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_clipboard_read', 'Read the current contents of the system clipboard.', '{"properties": {}, "required": [], "type": "object"}'::jsonb, 'local_system', ARRAY['clipboard', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_clipboard_write (dispatcher: ClipboardWrite)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_clipboard_write', 'Write text to the system clipboard.', '{"properties": {"content": {"description": "Text to write to the clipboard.", "title": "Content", "type": "string"}}, "required": ["content"], "type": "object"}'::jsonb, 'local_system', ARRAY['clipboard', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_connected_devices (dispatcher: ConnectedDevices)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_connected_devices', 'List all connected peripheral devices (USB, Bluetooth, monitors, etc.).', '{"properties": {}, "required": [], "type": "object"}'::jsonb, 'local_network', ARRAY['devices', 'usb', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_create_event (dispatcher: CreateEvent) [platforms: darwin]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_create_event', 'Create a new calendar event.', '{"properties": {"all_day": {"default": false, "description": "Whether this is an all-day event.", "type": "boolean"}, "calendar": {"description": "Calendar name to add the event to. Defaults to the default calendar.", "type": "string"}, "end": {"description": "End datetime in ISO 8601 format.", "type": "string"}, "notes": {"description": "Optional notes/description.", "type": "string"}, "start": {"description": "Start datetime in ISO 8601 format (e.g. \"2026-03-15T10:00:00\").", "type": "string"}, "title": {"description": "Event title.", "type": "string"}}, "required": ["title", "start", "end"], "type": "object"}'::jsonb, 'local_calendar', ARRAY['calendar', 'events', 'create', 'macos', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_create_reminder (dispatcher: CreateReminder) [platforms: darwin]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_create_reminder', 'Create a new reminder in macOS Reminders.', '{"properties": {"due": {"description": "Due date/time in ISO 8601 format (e.g. \"2026-03-20T09:00:00\").", "type": "string"}, "list_name": {"description": "Reminders list to add it to. Defaults to the default list.", "type": "string"}, "notes": {"description": "Optional notes.", "type": "string"}, "title": {"description": "Reminder title.", "type": "string"}}, "required": ["title"], "type": "object"}'::jsonb, 'local_calendar', ARRAY['reminders', 'create', 'macos', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_download_file (dispatcher: DownloadFile)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_download_file', 'Execute the DownloadFile tool.', '{"properties": {"save_path": {"type": "string"}, "timeout": {"default": 120, "type": "integer"}, "url": {"type": "string"}}, "required": ["url"], "type": "object"}'::jsonb, 'local_transfer', ARRAY['file', 'download', 'transfer', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_event_log (dispatcher: EventLog) [platforms: win32]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_event_log', 'Read Windows Event Log entries (Windows only).', '{"properties": {"count": {"default": 20, "type": "integer"}, "level": {"default": "Error", "type": "string"}, "log_name": {"default": "System", "type": "string"}, "source": {"type": "string"}}, "required": [], "type": "object"}'::jsonb, 'local_os', ARRAY['eventlog', 'windows', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_fetch_with_browser (dispatcher: FetchWithBrowser)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_fetch_with_browser', 'Fetch a URL using a headless browser (Playwright). Use when the page requires JavaScript rendering.', '{"properties": {"extract_text": {"default": false, "description": "If true, return plain text instead of full HTML.", "title": "Extract Text", "type": "boolean"}, "url": {"description": "URL to fetch using a headless browser.", "title": "Url", "type": "string"}, "wait_for": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": null, "description": "CSS selector to wait for after page load.", "title": "Wait For"}, "wait_timeout": {"default": 30000, "description": "Milliseconds to wait for wait_for selector.", "maximum": 120000, "minimum": 1000, "title": "Wait Timeout", "type": "integer"}}, "required": ["url"], "type": "object"}'::jsonb, 'local_network', ARRAY['http', 'fetch', 'browser', 'playwright', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_get_contact (dispatcher: GetContact) [platforms: darwin]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_get_contact', 'Get a single contact by its unique CNContact identifier.', '{"properties": {"identifier": {"description": "The CNContact identifier string (from search_contacts results).", "type": "string"}}, "required": ["identifier"], "type": "object"}'::jsonb, 'local_contacts', ARRAY['contacts', 'macos', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_get_email_accounts (dispatcher: GetEmailAccounts) [platforms: darwin]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_get_email_accounts', 'List configured Mail.app accounts and their mailboxes.', '{"properties": {}, "required": [], "type": "object"}'::jsonb, 'local_mail', ARRAY['mail', 'accounts', 'macos', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_get_location (dispatcher: GetLocation) [platforms: darwin]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_get_location', 'Get the device''s current GPS/network location via CoreLocation.', '{"properties": {"timeout": {"default": 15.0, "description": "Seconds to wait for a location fix (default 15, max 60).", "type": "number"}}, "required": [], "type": "object"}'::jsonb, 'local_location', ARRAY['location', 'gps', 'macos', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_get_photo (dispatcher: GetPhoto) [platforms: darwin]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_get_photo', 'Get a single photo asset with a thumbnail by its identifier.', '{"properties": {"identifier": {"description": "The PHAsset localIdentifier (from search_photos results).", "type": "string"}, "thumbnail_size": {"default": 512, "description": "Max thumbnail dimension in pixels (default 512, max 2048).", "type": "integer"}}, "required": ["identifier"], "type": "object"}'::jsonb, 'local_photos', ARRAY['photos', 'macos', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_heartbeat_status (dispatcher: HeartbeatStatus)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_heartbeat_status', 'Get the status of the heartbeat/scheduler system including all scheduled tasks,', '{"properties": {}, "required": [], "type": "object"}'::jsonb, 'local_scheduler', ARRAY['heartbeat', 'status', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_list_audio_devices (dispatcher: ListAudioDevices)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_list_audio_devices', 'List available audio input (microphones) and output (speakers) devices.', '{"properties": {}, "required": [], "type": "object"}'::jsonb, 'local_audio', ARRAY['audio', 'devices', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_list_conversations (dispatcher: ListConversations) [platforms: darwin]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_list_conversations', 'List recent iMessage/SMS conversations with the last message preview.', '{"properties": {"limit": {"default": 25, "description": "Maximum conversations to return (default 25, max 200).", "type": "integer"}}, "required": [], "type": "object"}'::jsonb, 'local_messages', ARRAY['messages', 'conversations', 'macos', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_list_emails (dispatcher: ListEmails) [platforms: darwin]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_list_emails', 'List recent emails from Mail.app via AppleScript.', '{"properties": {"limit": {"default": 25, "description": "Maximum messages to return (default 25, max 200).", "type": "integer"}, "mailbox": {"default": "INBOX", "description": "Mailbox name to read from (default \"INBOX\").", "type": "string"}, "unread_only": {"default": false, "description": "If True, only return unread messages.", "type": "boolean"}}, "required": [], "type": "object"}'::jsonb, 'local_mail', ARRAY['mail', 'email', 'macos', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_list_events (dispatcher: ListEvents) [platforms: darwin]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_list_events', 'List upcoming calendar events.', '{"properties": {"calendar_names": {"description": "Filter to specific calendar names. Omit for all calendars.", "items": {"type": "string"}, "type": "array"}, "days_ahead": {"default": 7, "description": "How many days into the future to look (default 7, max 365).", "type": "integer"}, "limit": {"default": 50, "description": "Maximum events to return (default 50, max 500).", "type": "integer"}}, "required": [], "type": "object"}'::jsonb, 'local_calendar', ARRAY['calendar', 'events', 'macos', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_list_messages (dispatcher: ListMessages) [platforms: darwin]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_list_messages', 'List recent iMessage and SMS messages from the Messages app.', '{"properties": {"contact": {"description": "Filter by contact name or phone number/email (partial match).", "type": "string"}, "limit": {"default": 50, "description": "Maximum messages to return (default 50, max 500).", "type": "integer"}, "unread_only": {"default": false, "description": "If True, only return unread messages.", "type": "boolean"}}, "required": [], "type": "object"}'::jsonb, 'local_messages', ARRAY['messages', 'imessage', 'macos', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_list_reminders (dispatcher: ListReminders) [platforms: darwin]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_list_reminders', 'List reminders from macOS Reminders.', '{"properties": {"include_completed": {"default": false, "description": "Include completed reminders (default False).", "type": "boolean"}, "limit": {"default": 50, "description": "Maximum reminders to return (default 50, max 500).", "type": "integer"}, "list_names": {"description": "Filter to specific Reminders list names. Omit for all lists.", "items": {"type": "string"}, "type": "array"}}, "required": [], "type": "object"}'::jsonb, 'local_calendar', ARRAY['reminders', 'macos', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_list_scheduled (dispatcher: ListScheduled)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_list_scheduled', 'List all active and recent scheduled tasks.', '{"properties": {}, "required": [], "type": "object"}'::jsonb, 'local_scheduler', ARRAY['schedule', 'list', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_list_screens (dispatcher: ListScreens)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_list_screens', 'List all connected monitors with their geometry.', '{"properties": {}, "required": [], "type": "object"}'::jsonb, 'local_system', ARRAY['screen', 'display', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_list_speech_locales (dispatcher: ListSpeechLocales) [platforms: darwin]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_list_speech_locales', 'List all locales supported by SFSpeechRecognizer on this device.', '{"properties": {}, "required": [], "type": "object"}'::jsonb, 'local_speech', ARRAY['speech', 'locales', 'macos', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_list_terminals (dispatcher: ListTerminals)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_list_terminals', 'List all running terminal emulators and interactive shells.', '{"properties": {}, "required": [], "type": "object"}'::jsonb, 'local_process', ARRAY['terminal', 'process', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_play_audio (dispatcher: PlayAudio)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_play_audio', 'Play an audio file through speakers.', '{"properties": {"device_index": {"type": "integer"}, "file_path": {"type": "string"}}, "required": ["file_path"], "type": "object"}'::jsonb, 'local_audio', ARRAY['audio', 'play', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_prevent_sleep (dispatcher: PreventSleep)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_prevent_sleep', 'Prevent or allow the system to go to sleep.', '{"properties": {"duration_minutes": {"type": "integer"}, "enable": {"default": true, "type": "boolean"}, "reason": {"default": "Matrx Local background tasks", "type": "string"}}, "required": [], "type": "object"}'::jsonb, 'local_scheduler', ARRAY['power', 'sleep', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_ps_get_env (dispatcher: PSGetEnv)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_ps_get_env', 'Read environment variables via PowerShell.', '{"properties": {"name": {"type": "string"}}, "required": [], "type": "object"}'::jsonb, 'local_os', ARRAY['env', 'powershell', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_ps_set_env (dispatcher: PSSetEnv) [platforms: win32]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_ps_set_env', 'Set an environment variable via PowerShell.', '{"properties": {"name": {"type": "string"}, "scope": {"default": "Process", "type": "string"}, "value": {"type": "string"}}, "required": ["name", "value"], "type": "object"}'::jsonb, 'local_os', ARRAY['env', 'powershell', 'windows', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_record_audio (dispatcher: RecordAudio)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_record_audio', 'Record audio from microphone for specified duration. Returns path to audio file.', '{"properties": {"channels": {"default": 1, "type": "integer"}, "device_index": {"type": "integer"}, "duration_seconds": {"default": 5, "type": "integer"}, "format": {"default": "wav", "type": "string"}, "sample_rate": {"type": "integer"}}, "required": [], "type": "object"}'::jsonb, 'local_audio', ARRAY['audio', 'record', 'microphone', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_registry_read (dispatcher: RegistryRead) [platforms: win32]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_registry_read', 'Read a Windows registry key or value (Windows only).', '{"properties": {"key_path": {"type": "string"}, "value_name": {"type": "string"}}, "required": ["key_path"], "type": "object"}'::jsonb, 'local_os', ARRAY['registry', 'windows', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_registry_write (dispatcher: RegistryWrite) [platforms: win32]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_registry_write', 'Write a value to the Windows registry (Windows only, use with caution).', '{"properties": {"key_path": {"type": "string"}, "value": {"type": "string"}, "value_name": {"type": "string"}, "value_type": {"default": "String", "type": "string"}}, "required": ["key_path", "value_name", "value"], "type": "object"}'::jsonb, 'local_os', ARRAY['registry', 'windows', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_research (dispatcher: Research)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_research', 'Deep web research: search + scrape all results + compile findings into a structured report.', '{"properties": {"country": {"default": "us", "description": "Two-letter country code for localized search results.", "title": "Country", "type": "string"}, "effort": {"default": "medium", "description": "''low'' (search only), ''medium'' (search + top results scraped), ''high'' (search + all results scraped + synthesis).", "enum": ["low", "medium", "high"], "title": "Effort", "type": "string"}, "freshness": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": null, "description": "Filter by age: ''pd'', ''pw'', ''pm'', ''py''.", "title": "Freshness"}, "query": {"description": "Research question or topic.", "title": "Query", "type": "string"}}, "required": ["query"], "type": "object"}'::jsonb, 'local_network', ARRAY['research', 'search', 'scrape', 'web', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_schedule_task (dispatcher: ScheduleTask)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_schedule_task', 'Schedule a tool to run repeatedly at a given interval.', '{"properties": {"_task_id": {"type": "string"}, "interval_seconds": {"default": 60, "type": "integer"}, "max_runs": {"type": "integer"}, "name": {"type": "string"}, "tool_input": {"type": "object"}, "tool_name": {"type": "string"}}, "required": ["name", "tool_name", "tool_input"], "type": "object"}'::jsonb, 'local_scheduler', ARRAY['schedule', 'task', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_scrape (dispatcher: Scrape)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_scrape', 'Scrape one or more URLs with the full scraper pipeline (JS rendering, content extraction, optional caching).', '{"properties": {"get_links": {"default": false, "description": "Also return a list of all links found on the page.", "title": "Get Links", "type": "boolean"}, "get_overview": {"default": false, "description": "Include a brief AI-generated overview of the page content.", "title": "Get Overview", "type": "boolean"}, "output_mode": {"default": "rich", "description": "''rich'' returns structured data with metadata, ''text'' plain text, ''html'' raw HTML, ''markdown'' Markdown.", "enum": ["rich", "text", "html", "markdown"], "title": "Output Mode", "type": "string"}, "urls": {"description": "List of URLs to scrape (max 10 per call).", "items": {"type": "string"}, "maxItems": 10, "minItems": 1, "title": "Urls", "type": "array"}, "use_cache": {"default": true, "description": "Return cached results if available.", "title": "Use Cache", "type": "boolean"}}, "required": ["urls"], "type": "object"}'::jsonb, 'local_network', ARRAY['scrape', 'web', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_search (dispatcher: Search)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_search', 'Search the web using Brave Search API and return results.', '{"properties": {"count": {"default": 10, "description": "Number of results to return.", "maximum": 50, "minimum": 1, "title": "Count", "type": "integer"}, "country": {"default": "us", "description": "Two-letter country code for localized results.", "title": "Country", "type": "string"}, "freshness": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": null, "description": "Filter by result age: ''pd'' (past day), ''pw'' (past week), ''pm'' (past month), ''py'' (past year).", "title": "Freshness"}, "keywords": {"description": "Search terms to look up.", "items": {"type": "string"}, "minItems": 1, "title": "Keywords", "type": "array"}}, "required": ["keywords"], "type": "object"}'::jsonb, 'local_network', ARRAY['search', 'web', 'brave', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_search_contacts (dispatcher: SearchContacts) [platforms: darwin]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_search_contacts', 'Search contacts by name. Returns up to `limit` matching contacts.', '{"properties": {"limit": {"default": 25, "description": "Maximum number of contacts to return (default 25, max 200).", "type": "integer"}, "query": {"description": "Name to search for. If omitted, returns up to `limit` contacts", "type": "string"}}, "required": [], "type": "object"}'::jsonb, 'local_contacts', ARRAY['contacts', 'search', 'macos', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_search_photos (dispatcher: SearchPhotos) [platforms: darwin]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_search_photos', 'Search and list photos/videos from the macOS Photos library.', '{"properties": {"favorites_only": {"default": false, "description": "If True, only return favorited assets.", "type": "boolean"}, "limit": {"default": 25, "description": "Maximum assets to return (default 25, max 200).", "type": "integer"}, "media_type": {"default": "image", "description": "\"image\", \"video\", or \"all\" (default \"image\").", "type": "string"}}, "required": [], "type": "object"}'::jsonb, 'local_photos', ARRAY['photos', 'search', 'macos', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_send_email (dispatcher: SendEmail) [platforms: darwin]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_send_email', 'Send an email via Mail.app using AppleScript.', '{"properties": {"bcc": {"description": "BCC recipient email address (optional).", "type": "string"}, "body": {"description": "Email body (plain text).", "type": "string"}, "cc": {"description": "CC recipient email address (optional).", "type": "string"}, "subject": {"description": "Email subject line.", "type": "string"}, "to": {"description": "Recipient email address.", "type": "string"}}, "required": ["to", "subject", "body"], "type": "object"}'::jsonb, 'local_mail', ARRAY['mail', 'email', 'send', 'macos', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_send_message (dispatcher: SendMessage) [platforms: darwin]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_send_message', 'Send an iMessage or SMS via the Messages app using AppleScript.', '{"properties": {"body": {"description": "Message text to send.", "type": "string"}, "recipient": {"description": "Phone number, email address, or contact name to send to.", "type": "string"}, "service": {"default": "iMessage", "description": "\"iMessage\" or \"SMS\" (default \"iMessage\").", "type": "string"}}, "required": ["recipient", "body"], "type": "object"}'::jsonb, 'local_messages', ARRAY['messages', 'imessage', 'send', 'macos', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_service_control (dispatcher: ServiceControl)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_service_control', 'Start, stop, or restart a system service.', '{"properties": {"action": {"type": "string"}, "name": {"type": "string"}}, "required": ["name", "action"], "type": "object"}'::jsonb, 'local_os', ARRAY['services', 'control', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_service_list (dispatcher: ServiceList)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_service_list', 'List Windows/system services with their status (Windows/macOS/Linux).', '{"properties": {"filter": {"type": "string"}, "status": {"type": "string"}}, "required": [], "type": "object"}'::jsonb, 'local_os', ARRAY['services', 'system', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_stop_watch (dispatcher: StopWatch)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_stop_watch', 'Stop watching a directory.', '{"properties": {"watch_id": {"type": "string"}}, "required": ["watch_id"], "type": "object"}'::jsonb, 'local_file_watch', ARRAY['file', 'watch', 'stop', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_tail_terminal (dispatcher: TailTerminal)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_tail_terminal', 'Fetch recent output from a terminal process.', '{"properties": {"lines": {"default": 50, "type": "integer"}, "pid": {"type": "integer"}, "tty": {"type": "string"}}, "required": [], "type": "object"}'::jsonb, 'local_process', ARRAY['terminal', 'output', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_transcribe_audio (dispatcher: TranscribeAudio)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_transcribe_audio', 'Transcribe audio file to text using OpenAI Whisper (local model).', '{"properties": {"file_path": {"type": "string"}, "language": {"type": "string"}, "model": {"default": "base", "type": "string"}}, "required": ["file_path"], "type": "object"}'::jsonb, 'local_audio', ARRAY['audio', 'transcribe', 'whisper', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_transcribe_with_speech (dispatcher: TranscribeWithSpeech) [platforms: darwin]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_transcribe_with_speech', 'Transcribe an audio file using Apple''s on-device SFSpeechRecognizer.', '{"properties": {"audio_path": {"description": "Absolute path to the audio file (WAV, M4A, MP3, AIFF, etc.).", "type": "string"}, "locale": {"default": "en-US", "description": "BCP-47 locale code for the language (e.g. \"en-US\", \"es-ES\", \"fr-FR\").", "type": "string"}, "timeout": {"default": 60.0, "description": "Maximum seconds to wait for transcription (default 60, max 300).", "type": "number"}}, "required": ["audio_path"], "type": "object"}'::jsonb, 'local_speech', ARRAY['speech', 'transcribe', 'macos', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_upload_file (dispatcher: UploadFile)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_upload_file', 'Execute the UploadFile tool.', '{"properties": {"field_name": {"default": "file", "type": "string"}, "file_path": {"type": "string"}, "timeout": {"default": 120, "type": "integer"}, "upload_url": {"type": "string"}}, "required": ["file_path", "upload_url"], "type": "object"}'::jsonb, 'local_transfer', ARRAY['file', 'upload', 'transfer', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_watch_directory (dispatcher: WatchDirectory)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_watch_directory', 'Start watching a directory for file changes (create, modify, delete, move).', '{"properties": {"path": {"type": "string"}, "patterns": {"items": {"type": "string"}, "type": "array"}, "recursive": {"default": true, "type": "boolean"}}, "required": ["path"], "type": "object"}'::jsonb, 'local_file_watch', ARRAY['file', 'watch', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_watch_events (dispatcher: WatchEvents)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_watch_events', 'Get accumulated file change events from a directory watch.', '{"properties": {"limit": {"default": 100, "type": "integer"}, "since_seconds": {"type": "number"}, "watch_id": {"type": "string"}}, "required": ["watch_id"], "type": "object"}'::jsonb, 'local_file_watch', ARRAY['file', 'watch', 'events', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_wifi_networks (dispatcher: WifiNetworks)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_wifi_networks', 'List available WiFi networks with signal strength, security, and channel.', '{"properties": {"rescan": {"default": false, "type": "boolean"}}, "required": [], "type": "object"}'::jsonb, 'local_network', ARRAY['wifi', 'network', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_windows_features (dispatcher: WindowsFeatures) [platforms: win32]
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_windows_features', 'List Windows optional features and capabilities (Windows only).', '{"properties": {"filter": {"type": "string"}, "installed_only": {"default": true, "type": "boolean"}}, "required": [], "type": "object"}'::jsonb, 'local_os', ARRAY['features', 'windows', 'local']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

COMMIT;
