-- ═══════════════════════════════════════════════════════════════════
-- matrx-local tool registry changeset
-- generated: 2026-07-14T00:07:25+00:00 by app/tools/tool_sync.py emit-changeset
-- cloud baseline: live https://server.app.matrxserver.com/ai-tools/app/matrx-local/all (executor=matrx-local, count=108)
-- catalog: 113 tools; NEW=5 CHANGED=1 COLLISIONS=0 REMOVED=0 OK=107 UNVERIFIED=0
--
-- APPLY VIA SUPABASE MCP (project txzxabzwovsujtloxrus) AFTER REVIEW.
-- The desktop never writes tool.definition/tool.binding directly.
-- Wrap in a transaction; every statement is idempotent-safe to re-run
-- only as a whole (NEW inserts are not — do not apply twice).
-- ═══════════════════════════════════════════════════════════════════

BEGIN;

-- NEW: local_copy_path (dispatcher: Copy)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_copy_path', 'Copy a file or directory on the local filesystem (recursive for directories, metadata preserved). Refuses to overwrite unless overwrite=true.', '{"properties": {"destination": {"description": "Target path. Copying a file into an existing directory keeps the file''s name.", "title": "Destination", "type": "string"}, "overwrite": {"default": false, "description": "Replace the destination if it already exists.", "title": "Overwrite", "type": "boolean"}, "source": {"description": "Path of the file or directory to copy.", "title": "Source", "type": "string"}}, "required": ["source", "destination"], "type": "object"}'::jsonb, 'local_file_ops', ARRAY['file', 'copy', 'local', 'filesystem']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_delete_path (dispatcher: Delete)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_delete_path', 'Delete a file or directory. Moves to the OS trash by default so the action is recoverable; pass permanent=true for an unrecoverable delete.', '{"properties": {"path": {"description": "Path of the file or directory to delete.", "title": "Path", "type": "string"}, "permanent": {"default": false, "description": "If false (default), move to the OS trash (recoverable). If true, delete permanently \u2014 NOT recoverable.", "title": "Permanent", "type": "boolean"}}, "required": ["path"], "type": "object"}'::jsonb, 'local_file_ops', ARRAY['file', 'delete', 'trash', 'local', 'filesystem']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_make_directory (dispatcher: Mkdir)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_make_directory', 'Create a directory on the local filesystem, including missing parent directories by default.', '{"properties": {"parents": {"default": true, "description": "Create missing parent directories as needed.", "title": "Parents", "type": "boolean"}, "path": {"description": "Path of the directory to create.", "title": "Path", "type": "string"}}, "required": ["path"], "type": "object"}'::jsonb, 'local_file_ops', ARRAY['file', 'directory', 'create', 'local', 'filesystem']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_move_path (dispatcher: Move)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_move_path', 'Move a file or directory to a new location on the local filesystem. Moving a file into an existing directory keeps its name. Refuses to overwrite unless overwrite=true.', '{"properties": {"destination": {"description": "Target path. Moving a file into an existing directory keeps the file''s name.", "title": "Destination", "type": "string"}, "overwrite": {"default": false, "description": "Replace the destination if it already exists.", "title": "Overwrite", "type": "boolean"}, "source": {"description": "Path of the file or directory to move.", "title": "Source", "type": "string"}}, "required": ["source", "destination"], "type": "object"}'::jsonb, 'local_file_ops', ARRAY['file', 'move', 'local', 'filesystem']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- NEW: local_rename_path (dispatcher: Rename)
WITH def AS (
  INSERT INTO tool.definition (name, description, parameters, category, tags, source_kind, tool_group, organization_id, visibility, is_active)
  VALUES ('local_rename_path', 'Rename a file or directory in place (same parent directory). Use Move to relocate.', '{"properties": {"new_name": {"description": "New bare name (no path separators). Use Move to relocate.", "title": "New Name", "type": "string"}, "path": {"description": "Path of the file or directory to rename.", "title": "Path", "type": "string"}}, "required": ["path", "new_name"], "type": "object"}'::jsonb, 'local_file_ops', ARRAY['file', 'rename', 'local', 'filesystem']::text[], 'native', 'core', '39c38960-d30c-4840-b0c1-c9960de95582', 'public', true)
  RETURNING id
)
INSERT INTO tool.binding (tool_id, executor_name, is_active)
SELECT id, 'matrx-local', true FROM def;

-- CHANGED: local_edit_file — parameters updated to match the code catalog
UPDATE tool.definition
   SET parameters = '{"properties": {"file_path": {"description": "Path to the file to edit.", "title": "File Path", "type": "string"}, "new_string": {"description": "Replacement string.", "title": "New String", "type": "string"}, "old_string": {"description": "Exact string to find and replace. Must match exactly including whitespace. Must be unique in the file unless replace_all is true.", "title": "Old String", "type": "string"}, "replace_all": {"default": false, "description": "Replace every occurrence instead of requiring a unique match.", "title": "Replace All", "type": "boolean"}}, "required": ["file_path", "old_string", "new_string"], "type": "object"}'::jsonb,
       updated_at = now()
 WHERE name = 'local_edit_file';

COMMIT;
