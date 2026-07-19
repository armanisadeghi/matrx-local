from __future__ import annotations

import builtins

from app.services.image_gen import service as image_service
from app.services.video_gen import service as video_service


def test_dependency_probes_stop_at_missing_root_torch(monkeypatch) -> None:
    imported: list[str] = []
    original_import = builtins.__import__

    def isolated_import(name, *args, **kwargs):
        imported.append(name)
        if name == "torch":
            error = ModuleNotFoundError("No module named 'torch'")
            error.name = "torch"
            raise error
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", isolated_import)

    image_available, image_reason = image_service._check_deps()
    video_available, video_reason = video_service._check_deps()

    assert image_available is False
    assert video_available is False
    assert "torch" in image_reason
    assert "torch" in video_reason
    assert imported == ["torch", "torch"]
