"""Shared low-level helpers.

Keep this package initializer free of eager imports. ``app.config`` imports
``app.common.platform_ctx`` during bootstrap, so importing higher-level common
modules here would make their imports of configuration constants circular.
Callers should import the concrete submodule they use.
"""
