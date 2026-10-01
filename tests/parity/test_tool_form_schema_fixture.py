"""The desktop tool-form test runs every tool's real input schema through the
form. Its fixture must be the live catalog, or that guard checks stale tools.

Regenerate after changing any tool's arguments:

    uv run python -c "import json; from app.tools.tool_schemas import generate_all_tool_schemas as g; json.dump(g(), open('desktop/src/lib/__fixtures__/engine-tool-schemas.json','w'), indent=1, sort_keys=True, default=str)"
"""

import json
from pathlib import Path

from app.tools.tool_schemas import generate_all_tool_schemas

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "desktop/src/lib/__fixtures__/engine-tool-schemas.json"
)


def test_tool_form_fixture_matches_live_catalog() -> None:
    live = json.loads(json.dumps(generate_all_tool_schemas(), default=str))
    on_disk = json.loads(FIXTURE.read_text())
    assert on_disk == live, (
        "desktop tool-form fixture is stale; regenerate it (see module docstring)"
    )
