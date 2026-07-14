"""GENERATED FILE — do not edit by hand.

Structural mirror of the canonical cloud schemas for the local SQLite store.
Regenerate with: python scripts/generate_mirror_schema.py
Source snapshot: schema_mirror/snapshot.json (cloud DB is the spec).
"""

SNAPSHOT_HASH = "4006599661d4c31c1630718c16ccb0f4611bd1c824528c945054298672355ab2"
SNAPSHOT_GENERATED_AT = "2026-07-13"

# schema -> table -> {columns, pk, cursor_col, has_deleted_at, create_sql, index_sql}
MIRROR_TABLES = {
    "chat": {
        "agent_memory": {
            "columns": {
                "access_count": "INTEGER",
                "content": "TEXT",
                "created_at": "TEXT",
                "created_by": "TEXT",
                "deleted_at": "TEXT",
                "expires_at": "TEXT",
                "id": "TEXT",
                "importance": "REAL",
                "key": "TEXT",
                "last_accessed_at": "TEXT",
                "memory_type": "TEXT",
                "metadata": "TEXT",
                "organization_id": "TEXT",
                "scope": "TEXT",
                "scope_id": "TEXT",
                "updated_at": "TEXT",
                "updated_by": "TEXT",
                "user_id": "TEXT",
                "version": "INTEGER"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"agent_memory\" (\n    \"id\" TEXT NOT NULL,\n    \"user_id\" TEXT,\n    \"memory_type\" TEXT,\n    \"scope\" TEXT,\n    \"scope_id\" TEXT,\n    \"key\" TEXT,\n    \"content\" TEXT,\n    \"importance\" REAL,\n    \"access_count\" INTEGER,\n    \"last_accessed_at\" TEXT,\n    \"expires_at\" TEXT,\n    \"metadata\" TEXT,\n    \"created_at\" TEXT,\n    \"updated_at\" TEXT,\n    \"deleted_at\" TEXT,\n    \"organization_id\" TEXT,\n    \"created_by\" TEXT,\n    \"updated_by\" TEXT,\n    \"version\" INTEGER, PRIMARY KEY (\"id\")\n)",
            "cursor_col": "updated_at",
            "has_deleted_at": True,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_agent_memory_user_id\" ON \"chat\".\"agent_memory\" (\"user_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_agent_memory_created_by\" ON \"chat\".\"agent_memory\" (\"created_by\")",
                "CREATE INDEX IF NOT EXISTS \"idx_agent_memory_updated_at\" ON \"chat\".\"agent_memory\" (\"updated_at\")",
                "CREATE INDEX IF NOT EXISTS \"idx_agent_memory_created_at\" ON \"chat\".\"agent_memory\" (\"created_at\")"
            ],
            "pk": [
                "id"
            ]
        },
        "agent_plan": {
            "columns": {
                "conversation_id": "TEXT",
                "created_at": "TEXT",
                "created_by": "TEXT",
                "domains": "TEXT",
                "estimated_minutes": "INTEGER",
                "id": "TEXT",
                "organization_id": "TEXT",
                "project_id": "TEXT",
                "reasoning": "TEXT",
                "status": "TEXT",
                "steps": "TEXT",
                "title": "TEXT",
                "updated_at": "TEXT",
                "updated_by": "TEXT",
                "user_id": "TEXT",
                "version": "INTEGER"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"agent_plan\" (\n    \"id\" TEXT NOT NULL,\n    \"conversation_id\" TEXT,\n    \"user_id\" TEXT,\n    \"title\" TEXT,\n    \"steps\" TEXT,\n    \"reasoning\" TEXT,\n    \"domains\" TEXT,\n    \"estimated_minutes\" INTEGER,\n    \"status\" TEXT,\n    \"project_id\" TEXT,\n    \"created_at\" TEXT,\n    \"updated_at\" TEXT,\n    \"organization_id\" TEXT,\n    \"created_by\" TEXT,\n    \"updated_by\" TEXT,\n    \"version\" INTEGER, PRIMARY KEY (\"id\")\n)",
            "cursor_col": "updated_at",
            "has_deleted_at": False,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_agent_plan_conversation_id\" ON \"chat\".\"agent_plan\" (\"conversation_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_agent_plan_user_id\" ON \"chat\".\"agent_plan\" (\"user_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_agent_plan_created_by\" ON \"chat\".\"agent_plan\" (\"created_by\")",
                "CREATE INDEX IF NOT EXISTS \"idx_agent_plan_updated_at\" ON \"chat\".\"agent_plan\" (\"updated_at\")",
                "CREATE INDEX IF NOT EXISTS \"idx_agent_plan_created_at\" ON \"chat\".\"agent_plan\" (\"created_at\")"
            ],
            "pk": [
                "id"
            ]
        },
        "agent_run": {
            "columns": {
                "created_at": "TEXT",
                "created_by": "TEXT",
                "deleted_at": "TEXT",
                "episode_id": "TEXT",
                "error": "TEXT",
                "id": "TEXT",
                "input_fingerprint": "TEXT",
                "kind": "TEXT",
                "last_heartbeat_at": "TEXT",
                "metadata": "TEXT",
                "organization_id": "TEXT",
                "request": "TEXT",
                "result": "TEXT",
                "status": "TEXT",
                "total_cost": "REAL",
                "updated_at": "TEXT",
                "updated_by": "TEXT",
                "user_id": "TEXT",
                "version": "INTEGER",
                "visibility": "TEXT"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"agent_run\" (\n    \"id\" TEXT NOT NULL,\n    \"kind\" TEXT,\n    \"user_id\" TEXT,\n    \"status\" TEXT,\n    \"input_fingerprint\" TEXT,\n    \"request\" TEXT,\n    \"result\" TEXT,\n    \"error\" TEXT,\n    \"total_cost\" REAL,\n    \"created_at\" TEXT,\n    \"updated_at\" TEXT,\n    \"episode_id\" TEXT,\n    \"last_heartbeat_at\" TEXT,\n    \"organization_id\" TEXT,\n    \"created_by\" TEXT,\n    \"updated_by\" TEXT,\n    \"version\" INTEGER,\n    \"deleted_at\" TEXT,\n    \"metadata\" TEXT,\n    \"visibility\" TEXT, PRIMARY KEY (\"id\")\n)",
            "cursor_col": "updated_at",
            "has_deleted_at": True,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_agent_run_user_id\" ON \"chat\".\"agent_run\" (\"user_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_agent_run_created_by\" ON \"chat\".\"agent_run\" (\"created_by\")",
                "CREATE INDEX IF NOT EXISTS \"idx_agent_run_updated_at\" ON \"chat\".\"agent_run\" (\"updated_at\")",
                "CREATE INDEX IF NOT EXISTS \"idx_agent_run_created_at\" ON \"chat\".\"agent_run\" (\"created_at\")"
            ],
            "pk": [
                "id"
            ]
        },
        "agent_run_stage": {
            "columns": {
                "cost": "REAL",
                "error": "TEXT",
                "finished_at": "TEXT",
                "id": "TEXT",
                "output": "TEXT",
                "run_id": "TEXT",
                "stage_key": "TEXT",
                "started_at": "TEXT",
                "status": "TEXT"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"agent_run_stage\" (\n    \"id\" TEXT NOT NULL,\n    \"run_id\" TEXT,\n    \"stage_key\" TEXT,\n    \"status\" TEXT,\n    \"output\" TEXT,\n    \"error\" TEXT,\n    \"cost\" REAL,\n    \"started_at\" TEXT,\n    \"finished_at\" TEXT, PRIMARY KEY (\"id\")\n)",
            "cursor_col": None,
            "has_deleted_at": False,
            "index_sql": [],
            "pk": [
                "id"
            ]
        },
        "agent_task": {
            "columns": {
                "conversation_id": "TEXT",
                "created_at": "TEXT",
                "created_by": "TEXT",
                "id": "TEXT",
                "note": "TEXT",
                "plan_id": "TEXT",
                "position": "INTEGER",
                "status": "TEXT",
                "title": "TEXT",
                "updated_at": "TEXT",
                "user_id": "TEXT"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"agent_task\" (\n    \"id\" TEXT NOT NULL,\n    \"conversation_id\" TEXT,\n    \"user_id\" TEXT,\n    \"plan_id\" TEXT,\n    \"title\" TEXT,\n    \"status\" TEXT,\n    \"note\" TEXT,\n    \"position\" INTEGER,\n    \"created_by\" TEXT,\n    \"created_at\" TEXT,\n    \"updated_at\" TEXT, PRIMARY KEY (\"id\")\n)",
            "cursor_col": "updated_at",
            "has_deleted_at": False,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_agent_task_conversation_id\" ON \"chat\".\"agent_task\" (\"conversation_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_agent_task_user_id\" ON \"chat\".\"agent_task\" (\"user_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_agent_task_created_by\" ON \"chat\".\"agent_task\" (\"created_by\")",
                "CREATE INDEX IF NOT EXISTS \"idx_agent_task_updated_at\" ON \"chat\".\"agent_task\" (\"updated_at\")",
                "CREATE INDEX IF NOT EXISTS \"idx_agent_task_created_at\" ON \"chat\".\"agent_task\" (\"created_at\")"
            ],
            "pk": [
                "id"
            ]
        },
        "artifact": {
            "columns": {
                "artifact_index": "INTEGER",
                "artifact_type": "TEXT",
                "canvas_item_id": "TEXT",
                "conversation_id": "TEXT",
                "created_at": "TEXT",
                "created_by": "TEXT",
                "deleted_at": "TEXT",
                "description": "TEXT",
                "external_id": "TEXT",
                "external_system": "TEXT",
                "external_url": "TEXT",
                "id": "TEXT",
                "message_id": "TEXT",
                "metadata": "TEXT",
                "organization_id": "TEXT",
                "project_id": "TEXT",
                "source_id": "TEXT",
                "source_system": "TEXT",
                "status": "TEXT",
                "task_id": "TEXT",
                "thumbnail_url": "TEXT",
                "title": "TEXT",
                "updated_at": "TEXT",
                "updated_by": "TEXT",
                "user_id": "TEXT",
                "version": "INTEGER"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"artifact\" (\n    \"id\" TEXT NOT NULL,\n    \"message_id\" TEXT,\n    \"conversation_id\" TEXT,\n    \"user_id\" TEXT,\n    \"organization_id\" TEXT,\n    \"project_id\" TEXT,\n    \"task_id\" TEXT,\n    \"artifact_type\" TEXT,\n    \"status\" TEXT,\n    \"external_system\" TEXT,\n    \"external_id\" TEXT,\n    \"external_url\" TEXT,\n    \"title\" TEXT,\n    \"description\" TEXT,\n    \"thumbnail_url\" TEXT,\n    \"metadata\" TEXT,\n    \"created_at\" TEXT,\n    \"updated_at\" TEXT,\n    \"deleted_at\" TEXT,\n    \"canvas_item_id\" TEXT,\n    \"created_by\" TEXT,\n    \"updated_by\" TEXT,\n    \"version\" INTEGER,\n    \"source_system\" TEXT,\n    \"source_id\" TEXT,\n    \"artifact_index\" INTEGER, PRIMARY KEY (\"id\")\n)",
            "cursor_col": "updated_at",
            "has_deleted_at": True,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_artifact_conversation_id\" ON \"chat\".\"artifact\" (\"conversation_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_artifact_user_id\" ON \"chat\".\"artifact\" (\"user_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_artifact_created_by\" ON \"chat\".\"artifact\" (\"created_by\")",
                "CREATE INDEX IF NOT EXISTS \"idx_artifact_updated_at\" ON \"chat\".\"artifact\" (\"updated_at\")",
                "CREATE INDEX IF NOT EXISTS \"idx_artifact_created_at\" ON \"chat\".\"artifact\" (\"created_at\")"
            ],
            "pk": [
                "id"
            ]
        },
        "code_edit": {
            "columns": {
                "applied_at": "TEXT",
                "block_index": "INTEGER",
                "conversation_id": "TEXT",
                "created_at": "TEXT",
                "id": "TEXT",
                "message_file_id": "TEXT",
                "message_id": "TEXT",
                "reject_reason": "TEXT",
                "rejected_at": "TEXT",
                "replace_text": "TEXT",
                "reverted_at": "TEXT",
                "search_text": "TEXT",
                "status": "TEXT",
                "user_id": "TEXT"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"code_edit\" (\n    \"id\" TEXT NOT NULL,\n    \"message_file_id\" TEXT,\n    \"message_id\" TEXT,\n    \"conversation_id\" TEXT,\n    \"user_id\" TEXT,\n    \"block_index\" INTEGER,\n    \"search_text\" TEXT,\n    \"replace_text\" TEXT,\n    \"status\" TEXT,\n    \"applied_at\" TEXT,\n    \"rejected_at\" TEXT,\n    \"reverted_at\" TEXT,\n    \"reject_reason\" TEXT,\n    \"created_at\" TEXT, PRIMARY KEY (\"id\")\n)",
            "cursor_col": "created_at",
            "has_deleted_at": False,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_code_edit_conversation_id\" ON \"chat\".\"code_edit\" (\"conversation_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_code_edit_user_id\" ON \"chat\".\"code_edit\" (\"user_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_code_edit_created_at\" ON \"chat\".\"code_edit\" (\"created_at\")"
            ],
            "pk": [
                "id"
            ]
        },
        "code_message_file": {
            "columns": {
                "after_content": "TEXT",
                "before_content": "TEXT",
                "conversation_id": "TEXT",
                "created_at": "TEXT",
                "edits_applied_count": "INTEGER",
                "edits_pending_count": "INTEGER",
                "edits_rejected_count": "INTEGER",
                "file_adapter": "TEXT",
                "file_path": "TEXT",
                "git_branch": "TEXT",
                "git_commit_sha": "TEXT",
                "id": "TEXT",
                "library_file_id": "TEXT",
                "message_id": "TEXT",
                "organization_id": "TEXT",
                "reverted_at": "TEXT",
                "status": "TEXT",
                "updated_at": "TEXT",
                "user_id": "TEXT"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"code_message_file\" (\n    \"id\" TEXT NOT NULL,\n    \"message_id\" TEXT,\n    \"conversation_id\" TEXT,\n    \"user_id\" TEXT,\n    \"organization_id\" TEXT,\n    \"file_adapter\" TEXT,\n    \"file_path\" TEXT,\n    \"library_file_id\" TEXT,\n    \"before_content\" TEXT,\n    \"after_content\" TEXT,\n    \"edits_applied_count\" INTEGER,\n    \"edits_rejected_count\" INTEGER,\n    \"edits_pending_count\" INTEGER,\n    \"status\" TEXT,\n    \"reverted_at\" TEXT,\n    \"git_commit_sha\" TEXT,\n    \"git_branch\" TEXT,\n    \"created_at\" TEXT,\n    \"updated_at\" TEXT, PRIMARY KEY (\"id\")\n)",
            "cursor_col": "updated_at",
            "has_deleted_at": False,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_code_message_file_conversation_id\" ON \"chat\".\"code_message_file\" (\"conversation_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_code_message_file_user_id\" ON \"chat\".\"code_message_file\" (\"user_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_code_message_file_updated_at\" ON \"chat\".\"code_message_file\" (\"updated_at\")",
                "CREATE INDEX IF NOT EXISTS \"idx_code_message_file_created_at\" ON \"chat\".\"code_message_file\" (\"created_at\")"
            ],
            "pk": [
                "id"
            ]
        },
        "conversation": {
            "columns": {
                "app_instance_id": "TEXT",
                "cache_state": "TEXT",
                "config": "TEXT",
                "conversation_type": "TEXT",
                "created_at": "TEXT",
                "created_by": "TEXT",
                "deleted_at": "TEXT",
                "description": "TEXT",
                "exclude_from_kg": "INTEGER",
                "forked_at_position": "INTEGER",
                "forked_from_id": "TEXT",
                "id": "TEXT",
                "initial_agent_id": "TEXT",
                "initial_agent_version_id": "TEXT",
                "is_ephemeral": "INTEGER",
                "is_favorite": "INTEGER",
                "keywords": "TEXT",
                "last_context_breakdown": "TEXT",
                "last_model_id": "TEXT",
                "last_request_id": "TEXT",
                "last_request_status": "TEXT",
                "message_count": "INTEGER",
                "metadata": "TEXT",
                "organization_id": "TEXT",
                "overrides": "TEXT",
                "parent_conversation_id": "TEXT",
                "project_id": "TEXT",
                "sandbox_instance_id": "TEXT",
                "source_app": "TEXT",
                "source_feature": "TEXT",
                "status": "TEXT",
                "system_instruction": "TEXT",
                "task_id": "TEXT",
                "title": "TEXT",
                "updated_at": "TEXT",
                "updated_by": "TEXT",
                "variables": "TEXT",
                "version": "INTEGER",
                "visibility": "TEXT"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"conversation\" (\n    \"id\" TEXT NOT NULL,\n    \"title\" TEXT,\n    \"system_instruction\" TEXT,\n    \"config\" TEXT,\n    \"status\" TEXT,\n    \"message_count\" INTEGER,\n    \"forked_from_id\" TEXT,\n    \"forked_at_position\" INTEGER,\n    \"created_at\" TEXT,\n    \"updated_at\" TEXT,\n    \"deleted_at\" TEXT,\n    \"metadata\" TEXT,\n    \"last_model_id\" TEXT,\n    \"parent_conversation_id\" TEXT,\n    \"variables\" TEXT,\n    \"overrides\" TEXT,\n    \"description\" TEXT,\n    \"keywords\" TEXT,\n    \"organization_id\" TEXT,\n    \"project_id\" TEXT,\n    \"task_id\" TEXT,\n    \"source_app\" TEXT,\n    \"source_feature\" TEXT,\n    \"is_ephemeral\" INTEGER,\n    \"initial_agent_id\" TEXT,\n    \"initial_agent_version_id\" TEXT,\n    \"is_favorite\" INTEGER,\n    \"cache_state\" TEXT,\n    \"last_context_breakdown\" TEXT,\n    \"sandbox_instance_id\" TEXT,\n    \"last_request_status\" TEXT,\n    \"last_request_id\" TEXT,\n    \"app_instance_id\" TEXT,\n    \"exclude_from_kg\" INTEGER,\n    \"conversation_type\" TEXT,\n    \"created_by\" TEXT,\n    \"updated_by\" TEXT,\n    \"version\" INTEGER,\n    \"visibility\" TEXT, PRIMARY KEY (\"id\")\n)",
            "cursor_col": "updated_at",
            "has_deleted_at": True,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_conversation_created_by\" ON \"chat\".\"conversation\" (\"created_by\")",
                "CREATE INDEX IF NOT EXISTS \"idx_conversation_updated_at\" ON \"chat\".\"conversation\" (\"updated_at\")",
                "CREATE INDEX IF NOT EXISTS \"idx_conversation_created_at\" ON \"chat\".\"conversation\" (\"created_at\")"
            ],
            "pk": [
                "id"
            ]
        },
        "conversation_value": {
            "columns": {
                "chars": "INTEGER",
                "content": "TEXT",
                "content_json": "TEXT",
                "conversation_id": "TEXT",
                "created_at": "TEXT",
                "created_by": "TEXT",
                "deleted_at": "TEXT",
                "description": "TEXT",
                "id": "TEXT",
                "json_schema": "TEXT",
                "key": "TEXT",
                "kind": "TEXT",
                "metadata": "TEXT",
                "organization_id": "TEXT",
                "source_agent_id": "TEXT",
                "source_call_id": "TEXT",
                "source_execution_id": "TEXT",
                "status": "TEXT",
                "updated_at": "TEXT",
                "updated_by": "TEXT",
                "version": "INTEGER"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"conversation_value\" (\n    \"id\" TEXT NOT NULL,\n    \"conversation_id\" TEXT,\n    \"organization_id\" TEXT,\n    \"key\" TEXT,\n    \"description\" TEXT,\n    \"kind\" TEXT,\n    \"content\" TEXT,\n    \"content_json\" TEXT,\n    \"json_schema\" TEXT,\n    \"chars\" INTEGER,\n    \"source_agent_id\" TEXT,\n    \"source_call_id\" TEXT,\n    \"source_execution_id\" TEXT,\n    \"status\" TEXT,\n    \"metadata\" TEXT,\n    \"created_by\" TEXT,\n    \"updated_by\" TEXT,\n    \"created_at\" TEXT,\n    \"updated_at\" TEXT,\n    \"version\" INTEGER,\n    \"deleted_at\" TEXT, PRIMARY KEY (\"id\")\n)",
            "cursor_col": "updated_at",
            "has_deleted_at": True,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_conversation_value_conversation_id\" ON \"chat\".\"conversation_value\" (\"conversation_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_conversation_value_created_by\" ON \"chat\".\"conversation_value\" (\"created_by\")",
                "CREATE INDEX IF NOT EXISTS \"idx_conversation_value_updated_at\" ON \"chat\".\"conversation_value\" (\"updated_at\")",
                "CREATE INDEX IF NOT EXISTS \"idx_conversation_value_created_at\" ON \"chat\".\"conversation_value\" (\"created_at\")"
            ],
            "pk": [
                "id"
            ]
        },
        "media": {
            "columns": {
                "conversation_id": "TEXT",
                "created_at": "TEXT",
                "deleted_at": "TEXT",
                "file_size_bytes": "INTEGER",
                "file_uri": "TEXT",
                "id": "TEXT",
                "kind": "TEXT",
                "metadata": "TEXT",
                "mime_type": "TEXT",
                "url": "TEXT",
                "user_id": "TEXT"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"media\" (\n    \"id\" TEXT NOT NULL,\n    \"conversation_id\" TEXT,\n    \"user_id\" TEXT,\n    \"kind\" TEXT,\n    \"url\" TEXT,\n    \"file_uri\" TEXT,\n    \"mime_type\" TEXT,\n    \"file_size_bytes\" INTEGER,\n    \"created_at\" TEXT,\n    \"deleted_at\" TEXT,\n    \"metadata\" TEXT, PRIMARY KEY (\"id\")\n)",
            "cursor_col": "created_at",
            "has_deleted_at": True,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_media_conversation_id\" ON \"chat\".\"media\" (\"conversation_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_media_user_id\" ON \"chat\".\"media\" (\"user_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_media_created_at\" ON \"chat\".\"media\" (\"created_at\")"
            ],
            "pk": [
                "id"
            ]
        },
        "message": {
            "columns": {
                "agent_id": "TEXT",
                "content": "TEXT",
                "content_chars": "INTEGER",
                "content_history": "TEXT",
                "conversation_id": "TEXT",
                "created_at": "TEXT",
                "created_by": "TEXT",
                "deleted_at": "TEXT",
                "error": "TEXT",
                "id": "TEXT",
                "is_visible_to_model": "INTEGER",
                "is_visible_to_user": "INTEGER",
                "metadata": "TEXT",
                "model_context": "TEXT",
                "organization_id": "TEXT",
                "position": "INTEGER",
                "role": "TEXT",
                "source": "TEXT",
                "status": "TEXT",
                "tool_results_chars": "INTEGER",
                "tools_on_call": "TEXT",
                "updated_at": "TEXT",
                "updated_by": "TEXT",
                "user_content": "TEXT",
                "version": "INTEGER",
                "voice": "TEXT"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"message\" (\n    \"id\" TEXT NOT NULL,\n    \"conversation_id\" TEXT,\n    \"role\" TEXT,\n    \"position\" INTEGER,\n    \"status\" TEXT,\n    \"content\" TEXT,\n    \"created_at\" TEXT,\n    \"deleted_at\" TEXT,\n    \"metadata\" TEXT,\n    \"content_history\" TEXT,\n    \"source\" TEXT,\n    \"agent_id\" TEXT,\n    \"is_visible_to_user\" INTEGER,\n    \"is_visible_to_model\" INTEGER,\n    \"user_content\" TEXT,\n    \"content_chars\" INTEGER,\n    \"tool_results_chars\" INTEGER,\n    \"tools_on_call\" TEXT,\n    \"model_context\" TEXT,\n    \"error\" TEXT,\n    \"voice\" TEXT,\n    \"organization_id\" TEXT,\n    \"created_by\" TEXT,\n    \"updated_by\" TEXT,\n    \"updated_at\" TEXT,\n    \"version\" INTEGER, PRIMARY KEY (\"id\")\n)",
            "cursor_col": "updated_at",
            "has_deleted_at": True,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_message_conversation_id\" ON \"chat\".\"message\" (\"conversation_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_message_created_by\" ON \"chat\".\"message\" (\"created_by\")",
                "CREATE INDEX IF NOT EXISTS \"idx_message_updated_at\" ON \"chat\".\"message\" (\"updated_at\")",
                "CREATE INDEX IF NOT EXISTS \"idx_message_created_at\" ON \"chat\".\"message\" (\"created_at\")"
            ],
            "pk": [
                "id"
            ]
        },
        "observational_memory": {
            "columns": {
                "active_observations": "TEXT",
                "buffered_observations": "TEXT",
                "buffered_reflection": "TEXT",
                "buffered_reflection_input_tokens": "INTEGER",
                "buffered_reflection_tokens": "INTEGER",
                "config": "TEXT",
                "conversation_id": "TEXT",
                "created_at": "TEXT",
                "created_by": "TEXT",
                "current_task": "TEXT",
                "deleted_at": "TEXT",
                "generation_count": "INTEGER",
                "id": "TEXT",
                "is_buffering_observation": "INTEGER",
                "is_buffering_reflection": "INTEGER",
                "last_buffered_at_time": "TEXT",
                "last_buffered_at_tokens": "INTEGER",
                "last_observed_at": "TEXT",
                "metadata": "TEXT",
                "observation_token_count": "INTEGER",
                "observed_message_ids": "TEXT",
                "observed_timezone": "TEXT",
                "organization_id": "TEXT",
                "pending_message_tokens": "INTEGER",
                "reflected_observation_line_count": "INTEGER",
                "scope": "TEXT",
                "suggested_response": "TEXT",
                "updated_at": "TEXT",
                "updated_by": "TEXT",
                "user_id": "TEXT",
                "version": "INTEGER"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"observational_memory\" (\n    \"id\" TEXT NOT NULL,\n    \"user_id\" TEXT,\n    \"conversation_id\" TEXT,\n    \"scope\" TEXT,\n    \"active_observations\" TEXT,\n    \"observation_token_count\" INTEGER,\n    \"current_task\" TEXT,\n    \"suggested_response\" TEXT,\n    \"last_observed_at\" TEXT,\n    \"observed_message_ids\" TEXT,\n    \"pending_message_tokens\" INTEGER,\n    \"buffered_observations\" TEXT,\n    \"is_buffering_observation\" INTEGER,\n    \"last_buffered_at_tokens\" INTEGER,\n    \"last_buffered_at_time\" TEXT,\n    \"buffered_reflection\" TEXT,\n    \"buffered_reflection_input_tokens\" INTEGER,\n    \"buffered_reflection_tokens\" INTEGER,\n    \"is_buffering_reflection\" INTEGER,\n    \"reflected_observation_line_count\" INTEGER,\n    \"generation_count\" INTEGER,\n    \"observed_timezone\" TEXT,\n    \"config\" TEXT,\n    \"metadata\" TEXT,\n    \"created_at\" TEXT,\n    \"updated_at\" TEXT,\n    \"deleted_at\" TEXT,\n    \"organization_id\" TEXT,\n    \"created_by\" TEXT,\n    \"updated_by\" TEXT,\n    \"version\" INTEGER, PRIMARY KEY (\"id\")\n)",
            "cursor_col": "updated_at",
            "has_deleted_at": True,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_observational_memory_conversation_id\" ON \"chat\".\"observational_memory\" (\"conversation_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_observational_memory_user_id\" ON \"chat\".\"observational_memory\" (\"user_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_observational_memory_created_by\" ON \"chat\".\"observational_memory\" (\"created_by\")",
                "CREATE INDEX IF NOT EXISTS \"idx_observational_memory_updated_at\" ON \"chat\".\"observational_memory\" (\"updated_at\")",
                "CREATE INDEX IF NOT EXISTS \"idx_observational_memory_created_at\" ON \"chat\".\"observational_memory\" (\"created_at\")"
            ],
            "pk": [
                "id"
            ]
        },
        "observational_memory_event": {
            "columns": {
                "completed_at": "TEXT",
                "conversation_id": "TEXT",
                "cost": "REAL",
                "created_at": "TEXT",
                "duration_ms": "INTEGER",
                "error": "TEXT",
                "event_type": "TEXT",
                "id": "TEXT",
                "input_tokens": "INTEGER",
                "memory_record_id": "TEXT",
                "metadata": "TEXT",
                "model": "TEXT",
                "output_tokens": "INTEGER",
                "success": "INTEGER",
                "trigger_reason": "TEXT",
                "triggered_at": "TEXT",
                "user_id": "TEXT",
                "user_request_id": "TEXT"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"observational_memory_event\" (\n    \"id\" TEXT NOT NULL,\n    \"memory_record_id\" TEXT,\n    \"conversation_id\" TEXT,\n    \"user_id\" TEXT,\n    \"user_request_id\" TEXT,\n    \"event_type\" TEXT,\n    \"model\" TEXT,\n    \"input_tokens\" INTEGER,\n    \"output_tokens\" INTEGER,\n    \"cost\" REAL,\n    \"duration_ms\" INTEGER,\n    \"triggered_at\" TEXT,\n    \"completed_at\" TEXT,\n    \"success\" INTEGER,\n    \"error\" TEXT,\n    \"trigger_reason\" TEXT,\n    \"metadata\" TEXT,\n    \"created_at\" TEXT, PRIMARY KEY (\"id\")\n)",
            "cursor_col": "created_at",
            "has_deleted_at": False,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_observational_memory_event_conversation_id\" ON \"chat\".\"observational_memory_event\" (\"conversation_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_observational_memory_event_user_id\" ON \"chat\".\"observational_memory_event\" (\"user_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_observational_memory_event_created_at\" ON \"chat\".\"observational_memory_event\" (\"created_at\")"
            ],
            "pk": [
                "id"
            ]
        },
        "pending_injection": {
            "columns": {
                "consumed_at": "TEXT",
                "consumed_by_request_id": "TEXT",
                "consumed_message_id": "TEXT",
                "content": "TEXT",
                "conversation_id": "TEXT",
                "created_at": "TEXT",
                "enqueued_seq": "INTEGER",
                "id": "TEXT",
                "is_visible_to_model": "INTEGER",
                "is_visible_to_user": "INTEGER",
                "kind": "TEXT",
                "metadata": "TEXT",
                "source": "TEXT",
                "status": "TEXT",
                "user_id": "TEXT"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"pending_injection\" (\n    \"id\" TEXT NOT NULL,\n    \"conversation_id\" TEXT,\n    \"user_id\" TEXT,\n    \"kind\" TEXT,\n    \"content\" TEXT,\n    \"status\" TEXT,\n    \"source\" TEXT,\n    \"is_visible_to_user\" INTEGER,\n    \"is_visible_to_model\" INTEGER,\n    \"enqueued_seq\" INTEGER,\n    \"created_at\" TEXT,\n    \"consumed_at\" TEXT,\n    \"consumed_by_request_id\" TEXT,\n    \"consumed_message_id\" TEXT,\n    \"metadata\" TEXT, PRIMARY KEY (\"id\")\n)",
            "cursor_col": "created_at",
            "has_deleted_at": False,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_pending_injection_conversation_id\" ON \"chat\".\"pending_injection\" (\"conversation_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_pending_injection_user_id\" ON \"chat\".\"pending_injection\" (\"user_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_pending_injection_created_at\" ON \"chat\".\"pending_injection\" (\"created_at\")"
            ],
            "pk": [
                "id"
            ]
        },
        "request": {
            "columns": {
                "ai_model_id": "TEXT",
                "api_duration_ms": "INTEGER",
                "cached_tokens": "INTEGER",
                "conversation_id": "TEXT",
                "cost": "REAL",
                "created_at": "TEXT",
                "deleted_at": "TEXT",
                "error": "TEXT",
                "finish_reason": "TEXT",
                "id": "TEXT",
                "input_tokens": "INTEGER",
                "iteration": "INTEGER",
                "metadata": "TEXT",
                "output_tokens": "INTEGER",
                "provider": "TEXT",
                "raw_usage": "TEXT",
                "response_id": "TEXT",
                "status": "TEXT",
                "tool_calls_count": "INTEGER",
                "tool_calls_details": "TEXT",
                "tool_duration_ms": "INTEGER",
                "total_duration_ms": "INTEGER",
                "total_tokens": "INTEGER",
                "trim_summary": "TEXT",
                "user_request_id": "TEXT"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"request\" (\n    \"id\" TEXT NOT NULL,\n    \"user_request_id\" TEXT,\n    \"conversation_id\" TEXT,\n    \"provider\" TEXT,\n    \"iteration\" INTEGER,\n    \"input_tokens\" INTEGER,\n    \"output_tokens\" INTEGER,\n    \"cached_tokens\" INTEGER,\n    \"total_tokens\" INTEGER,\n    \"cost\" REAL,\n    \"api_duration_ms\" INTEGER,\n    \"tool_duration_ms\" INTEGER,\n    \"total_duration_ms\" INTEGER,\n    \"tool_calls_count\" INTEGER,\n    \"tool_calls_details\" TEXT,\n    \"finish_reason\" TEXT,\n    \"response_id\" TEXT,\n    \"created_at\" TEXT,\n    \"deleted_at\" TEXT,\n    \"metadata\" TEXT,\n    \"ai_model_id\" TEXT,\n    \"raw_usage\" TEXT,\n    \"trim_summary\" TEXT,\n    \"status\" TEXT,\n    \"error\" TEXT, PRIMARY KEY (\"id\")\n)",
            "cursor_col": "created_at",
            "has_deleted_at": True,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_request_conversation_id\" ON \"chat\".\"request\" (\"conversation_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_request_created_at\" ON \"chat\".\"request\" (\"created_at\")"
            ],
            "pk": [
                "id"
            ]
        },
        "request_snapshot": {
            "columns": {
                "conversation_id": "TEXT",
                "created_at": "TEXT",
                "cx_request_id": "TEXT",
                "id": "TEXT",
                "iteration": "INTEGER",
                "model": "TEXT",
                "provider": "TEXT",
                "request_payload": "TEXT",
                "response_message_id": "TEXT",
                "response_payload": "TEXT",
                "trigger_message_id": "TEXT",
                "unified_payload": "TEXT",
                "user_request_id": "TEXT"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"request_snapshot\" (\n    \"id\" TEXT NOT NULL,\n    \"conversation_id\" TEXT,\n    \"user_request_id\" TEXT,\n    \"cx_request_id\" TEXT,\n    \"iteration\" INTEGER,\n    \"trigger_message_id\" TEXT,\n    \"response_message_id\" TEXT,\n    \"provider\" TEXT,\n    \"model\" TEXT,\n    \"request_payload\" TEXT,\n    \"response_payload\" TEXT,\n    \"created_at\" TEXT,\n    \"unified_payload\" TEXT, PRIMARY KEY (\"id\")\n)",
            "cursor_col": "created_at",
            "has_deleted_at": False,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_request_snapshot_conversation_id\" ON \"chat\".\"request_snapshot\" (\"conversation_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_request_snapshot_created_at\" ON \"chat\".\"request_snapshot\" (\"created_at\")"
            ],
            "pk": [
                "id"
            ]
        },
        "tool_call": {
            "columns": {
                "arguments": "TEXT",
                "call_id": "TEXT",
                "completed_at": "TEXT",
                "conversation_id": "TEXT",
                "cost_usd": "REAL",
                "created_at": "TEXT",
                "created_by": "TEXT",
                "deleted_at": "TEXT",
                "duration_ms": "INTEGER",
                "error_message": "TEXT",
                "error_type": "TEXT",
                "execution_events": "TEXT",
                "expires_at": "TEXT",
                "fault_domain": "TEXT",
                "file_path": "TEXT",
                "id": "TEXT",
                "input_tokens": "INTEGER",
                "is_client_delegated": "INTEGER",
                "is_error": "INTEGER",
                "iteration": "INTEGER",
                "message_id": "TEXT",
                "metadata": "TEXT",
                "model_stub_at": "TEXT",
                "organization_id": "TEXT",
                "output": "TEXT",
                "output_chars": "INTEGER",
                "output_preview": "TEXT",
                "output_tokens": "INTEGER",
                "output_type": "TEXT",
                "parent_call_id": "TEXT",
                "persist_key": "TEXT",
                "resolution_source": "TEXT",
                "resolved_at": "TEXT",
                "retry_count": "INTEGER",
                "runtime_execution_id": "TEXT",
                "started_at": "TEXT",
                "status": "TEXT",
                "success": "INTEGER",
                "tool_name": "TEXT",
                "tool_name_as_called": "TEXT",
                "tool_type": "TEXT",
                "total_tokens": "INTEGER",
                "updated_at": "TEXT",
                "updated_by": "TEXT",
                "user_id": "TEXT",
                "user_request_id": "TEXT",
                "value_ref_key": "TEXT",
                "version": "INTEGER"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"tool_call\" (\n    \"id\" TEXT NOT NULL,\n    \"conversation_id\" TEXT,\n    \"message_id\" TEXT,\n    \"user_id\" TEXT,\n    \"user_request_id\" TEXT,\n    \"tool_name\" TEXT,\n    \"tool_type\" TEXT,\n    \"call_id\" TEXT,\n    \"status\" TEXT,\n    \"arguments\" TEXT,\n    \"success\" INTEGER,\n    \"output\" TEXT,\n    \"output_type\" TEXT,\n    \"is_error\" INTEGER,\n    \"error_type\" TEXT,\n    \"error_message\" TEXT,\n    \"duration_ms\" INTEGER,\n    \"started_at\" TEXT,\n    \"completed_at\" TEXT,\n    \"input_tokens\" INTEGER,\n    \"output_tokens\" INTEGER,\n    \"total_tokens\" INTEGER,\n    \"cost_usd\" REAL,\n    \"iteration\" INTEGER,\n    \"retry_count\" INTEGER,\n    \"parent_call_id\" TEXT,\n    \"execution_events\" TEXT,\n    \"persist_key\" TEXT,\n    \"file_path\" TEXT,\n    \"metadata\" TEXT,\n    \"created_at\" TEXT,\n    \"deleted_at\" TEXT,\n    \"output_chars\" INTEGER,\n    \"output_preview\" TEXT,\n    \"is_client_delegated\" INTEGER,\n    \"expires_at\" TEXT,\n    \"resolved_at\" TEXT,\n    \"resolution_source\" TEXT,\n    \"tool_name_as_called\" TEXT,\n    \"fault_domain\" TEXT,\n    \"organization_id\" TEXT,\n    \"created_by\" TEXT,\n    \"updated_by\" TEXT,\n    \"updated_at\" TEXT,\n    \"version\" INTEGER,\n    \"value_ref_key\" TEXT,\n    \"model_stub_at\" TEXT,\n    \"runtime_execution_id\" TEXT, PRIMARY KEY (\"id\")\n)",
            "cursor_col": "updated_at",
            "has_deleted_at": True,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_tool_call_conversation_id\" ON \"chat\".\"tool_call\" (\"conversation_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_tool_call_user_id\" ON \"chat\".\"tool_call\" (\"user_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_tool_call_created_by\" ON \"chat\".\"tool_call\" (\"created_by\")",
                "CREATE INDEX IF NOT EXISTS \"idx_tool_call_updated_at\" ON \"chat\".\"tool_call\" (\"updated_at\")",
                "CREATE INDEX IF NOT EXISTS \"idx_tool_call_created_at\" ON \"chat\".\"tool_call\" (\"created_at\")"
            ],
            "pk": [
                "id"
            ]
        },
        "tool_trace": {
            "columns": {
                "args": "TEXT",
                "call_id": "TEXT",
                "conversation_id": "TEXT",
                "created_at": "TEXT",
                "duration_ms": "INTEGER",
                "err_msg": "TEXT",
                "err_type": "TEXT",
                "event": "TEXT",
                "fault_domain": "TEXT",
                "id": "TEXT",
                "kind": "TEXT",
                "metadata": "TEXT",
                "process_pid": "INTEGER",
                "process_started_at": "TEXT",
                "result_preview": "TEXT",
                "tool_name": "TEXT",
                "ts": "TEXT",
                "user_id": "TEXT"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"tool_trace\" (\n    \"id\" TEXT NOT NULL,\n    \"process_pid\" INTEGER,\n    \"process_started_at\" TEXT,\n    \"ts\" TEXT,\n    \"event\" TEXT,\n    \"tool_name\" TEXT,\n    \"kind\" TEXT,\n    \"duration_ms\" INTEGER,\n    \"args\" TEXT,\n    \"result_preview\" TEXT,\n    \"err_type\" TEXT,\n    \"err_msg\" TEXT,\n    \"conversation_id\" TEXT,\n    \"call_id\" TEXT,\n    \"user_id\" TEXT,\n    \"metadata\" TEXT,\n    \"created_at\" TEXT,\n    \"fault_domain\" TEXT, PRIMARY KEY (\"id\")\n)",
            "cursor_col": "created_at",
            "has_deleted_at": False,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_tool_trace_conversation_id\" ON \"chat\".\"tool_trace\" (\"conversation_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_tool_trace_user_id\" ON \"chat\".\"tool_trace\" (\"user_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_tool_trace_created_at\" ON \"chat\".\"tool_trace\" (\"created_at\")"
            ],
            "pk": [
                "id"
            ]
        },
        "user_request": {
            "columns": {
                "agent_id": "TEXT",
                "agent_version_id": "TEXT",
                "api_duration_ms": "INTEGER",
                "completed_at": "TEXT",
                "created_at": "TEXT",
                "created_by": "TEXT",
                "deleted_at": "TEXT",
                "error": "TEXT",
                "finish_reason": "TEXT",
                "id": "TEXT",
                "iterations": "INTEGER",
                "last_activity_at": "TEXT",
                "metadata": "TEXT",
                "organization_id": "TEXT",
                "source_app": "TEXT",
                "source_feature": "TEXT",
                "status": "TEXT",
                "tool_duration_ms": "INTEGER",
                "total_cached_tokens": "INTEGER",
                "total_cost": "REAL",
                "total_duration_ms": "INTEGER",
                "total_input_tokens": "INTEGER",
                "total_output_tokens": "INTEGER",
                "total_tokens": "INTEGER",
                "total_tool_calls": "INTEGER",
                "updated_at": "TEXT",
                "updated_by": "TEXT",
                "user_id": "TEXT",
                "version": "INTEGER"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"user_request\" (\n    \"id\" TEXT NOT NULL,\n    \"user_id\" TEXT,\n    \"total_input_tokens\" INTEGER,\n    \"total_output_tokens\" INTEGER,\n    \"total_cached_tokens\" INTEGER,\n    \"total_tokens\" INTEGER,\n    \"total_cost\" REAL,\n    \"total_duration_ms\" INTEGER,\n    \"api_duration_ms\" INTEGER,\n    \"tool_duration_ms\" INTEGER,\n    \"iterations\" INTEGER,\n    \"total_tool_calls\" INTEGER,\n    \"status\" TEXT,\n    \"finish_reason\" TEXT,\n    \"error\" TEXT,\n    \"created_at\" TEXT,\n    \"completed_at\" TEXT,\n    \"deleted_at\" TEXT,\n    \"metadata\" TEXT,\n    \"source_app\" TEXT,\n    \"source_feature\" TEXT,\n    \"agent_id\" TEXT,\n    \"agent_version_id\" TEXT,\n    \"last_activity_at\" TEXT,\n    \"organization_id\" TEXT,\n    \"created_by\" TEXT,\n    \"updated_by\" TEXT,\n    \"updated_at\" TEXT,\n    \"version\" INTEGER, PRIMARY KEY (\"id\")\n)",
            "cursor_col": "updated_at",
            "has_deleted_at": True,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_user_request_user_id\" ON \"chat\".\"user_request\" (\"user_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_user_request_created_by\" ON \"chat\".\"user_request\" (\"created_by\")",
                "CREATE INDEX IF NOT EXISTS \"idx_user_request_updated_at\" ON \"chat\".\"user_request\" (\"updated_at\")",
                "CREATE INDEX IF NOT EXISTS \"idx_user_request_created_at\" ON \"chat\".\"user_request\" (\"created_at\")"
            ],
            "pk": [
                "id"
            ]
        },
        "user_todo": {
            "columns": {
                "context": "TEXT",
                "conversation_id": "TEXT",
                "created_at": "TEXT",
                "created_by": "TEXT",
                "ctx_task_id": "TEXT",
                "done": "INTEGER",
                "done_at": "TEXT",
                "due": "TEXT",
                "id": "TEXT",
                "organization_id": "TEXT",
                "title": "TEXT",
                "updated_at": "TEXT",
                "updated_by": "TEXT",
                "user_id": "TEXT",
                "version": "INTEGER"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"user_todo\" (\n    \"id\" TEXT NOT NULL,\n    \"conversation_id\" TEXT,\n    \"user_id\" TEXT,\n    \"title\" TEXT,\n    \"context\" TEXT,\n    \"due\" TEXT,\n    \"done\" INTEGER,\n    \"done_at\" TEXT,\n    \"ctx_task_id\" TEXT,\n    \"created_at\" TEXT,\n    \"updated_at\" TEXT,\n    \"organization_id\" TEXT,\n    \"created_by\" TEXT,\n    \"updated_by\" TEXT,\n    \"version\" INTEGER, PRIMARY KEY (\"id\")\n)",
            "cursor_col": "updated_at",
            "has_deleted_at": False,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_user_todo_conversation_id\" ON \"chat\".\"user_todo\" (\"conversation_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_user_todo_user_id\" ON \"chat\".\"user_todo\" (\"user_id\")",
                "CREATE INDEX IF NOT EXISTS \"idx_user_todo_created_by\" ON \"chat\".\"user_todo\" (\"created_by\")",
                "CREATE INDEX IF NOT EXISTS \"idx_user_todo_updated_at\" ON \"chat\".\"user_todo\" (\"updated_at\")",
                "CREATE INDEX IF NOT EXISTS \"idx_user_todo_created_at\" ON \"chat\".\"user_todo\" (\"created_at\")"
            ],
            "pk": [
                "id"
            ]
        },
        "user_usage_summary": {
            "columns": {
                "auth_type": "TEXT",
                "blocked_reason": "TEXT",
                "cost_24h_mcents": "INTEGER",
                "cost_6h_mcents": "INTEGER",
                "daily_blocked": "INTEGER",
                "last_request_at": "TEXT",
                "requests_24h": "INTEGER",
                "requests_6h": "INTEGER",
                "tokens_24h": "INTEGER",
                "tokens_6h": "INTEGER",
                "updated_at": "TEXT",
                "user_id": "TEXT",
                "window_24h_starts_at": "TEXT",
                "window_6h_starts_at": "TEXT",
                "window_blocked": "INTEGER"
            },
            "create_sql": "CREATE TABLE IF NOT EXISTS \"chat\".\"user_usage_summary\" (\n    \"user_id\" TEXT NOT NULL,\n    \"auth_type\" TEXT,\n    \"cost_6h_mcents\" INTEGER,\n    \"cost_24h_mcents\" INTEGER,\n    \"requests_6h\" INTEGER,\n    \"requests_24h\" INTEGER,\n    \"tokens_6h\" INTEGER,\n    \"tokens_24h\" INTEGER,\n    \"last_request_at\" TEXT,\n    \"window_6h_starts_at\" TEXT,\n    \"window_24h_starts_at\" TEXT,\n    \"daily_blocked\" INTEGER,\n    \"window_blocked\" INTEGER,\n    \"blocked_reason\" TEXT,\n    \"updated_at\" TEXT, PRIMARY KEY (\"user_id\")\n)",
            "cursor_col": "updated_at",
            "has_deleted_at": False,
            "index_sql": [
                "CREATE INDEX IF NOT EXISTS \"idx_user_usage_summary_updated_at\" ON \"chat\".\"user_usage_summary\" (\"updated_at\")"
            ],
            "pk": [
                "user_id"
            ]
        }
    }
}
