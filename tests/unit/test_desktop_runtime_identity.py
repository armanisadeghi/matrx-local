"""The executable supplies shared-package identity without user environment setup."""
import os
import sys

import pytest


@pytest.mark.parametrize("frozen,stage", [(False, "local"), (True, "production")])
def test_desktop_declares_its_actual_runtime(monkeypatch, tmp_path, frozen, stage):
    from app import package_integration
    from matrx_utils import conf

    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setenv("MATRX_STAGE", "development")
    monkeypatch.setenv("MATRX_ROLE", "app_server")
    monkeypatch.setattr(conf, "configure_settings", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.config.MATRX_HOME_DIR", tmp_path)
    package_integration.configure_matrx_packages()
    assert os.environ["MATRX_STAGE"] == stage
    assert os.environ["MATRX_ROLE"] == "desktop"
