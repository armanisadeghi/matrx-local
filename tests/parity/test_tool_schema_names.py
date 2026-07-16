"""Parity: advertised chat tool schemas use cloud registry names."""

from __future__ import annotations

from app.tools.actions import ACTION_GROUPS
from app.tools.tool_schemas import generate_all_tool_schemas, generate_tool_schema


def test_action_group_schema_uses_cloud_name() -> None:
    schema = generate_tool_schema("Screen")
    assert schema is not None
    assert schema["name"] == "local_screen"
    assert "monitor" in schema["input_schema"]["properties"]
    assert "region" in schema["input_schema"]["properties"]


def test_all_action_group_schemas_use_cloud_names_not_dispatcher_names() -> None:
    schemas = {schema["name"]: schema for schema in generate_all_tool_schemas()}
    dispatcher_names = set(ACTION_GROUPS)
    cloud_names = {group.cloud_name for group in ACTION_GROUPS.values()}

    assert cloud_names <= set(schemas)
    assert not (dispatcher_names & set(schemas))
