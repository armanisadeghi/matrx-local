"""Fail-closed checks for cloud-to-local mirror snapshot refresh."""

from copy import deepcopy

import pytest

from scripts.refresh_mirror_snapshot import validate_non_destructive


def _snapshot() -> dict:
    return {
        "schemas": {
            "chat": {
                "thing": {
                    "kind": "table",
                    "pk": ["id"],
                    "columns": [{"name": "id"}, {"name": "value"}],
                }
            }
        }
    }


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda new: new["schemas"]["chat"].pop("thing"), "relation removed"),
        (lambda new: new["schemas"]["chat"]["thing"].update(kind="view"), "relation kind changed"),
        (lambda new: new["schemas"]["chat"]["thing"].update(pk=["value"]), "primary key changed"),
        (lambda new: new["schemas"]["chat"]["thing"]["columns"].pop(), "chat.thing.value"),
    ],
)
def test_refresh_refuses_destructive_changes(change, reason):
    old = _snapshot()
    updated = deepcopy(old)
    change(updated)
    with pytest.raises(SystemExit, match=reason):
        validate_non_destructive(old, updated)


def test_refresh_accepts_additive_column():
    old = _snapshot()
    updated = deepcopy(old)
    updated["schemas"]["chat"]["thing"]["columns"].append({"name": "custom_fields"})
    validate_non_destructive(old, updated)
