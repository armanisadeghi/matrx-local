# Source isolation bootstrap

Source runs default to `~/.matrx-dev` unless `MATRX_LIVE_ENGINE=1` or an
explicit `MATRX_HOME_DIR` selects another home. `app.config` applies this
default independently, so an import outside `run.py` receives private defaults
for runtime data, temporary files, logs, configuration, and Notes, Files, and
Code directories.

The launcher no longer creates links from a source home to live caches. It
preserves existing cache directories. Before source startup, it refuses any of
the former cache paths that is already a symlink resolving outside the selected
source home. The remedy is to choose a new private `MATRX_HOME_DIR`. It never
unlinks, migrates, or deletes existing files.

This is a default-root boundary only. It is not an isolation certificate.
Explicit environment overrides, inherited `.env` values, keychain namespaces,
and cloud destinations remain outside this change and must be constrained by
the outer certified wrapper before active testing. Frozen builds and explicit
`MATRX_LIVE_ENGINE=1` source runs retain their existing default paths.
