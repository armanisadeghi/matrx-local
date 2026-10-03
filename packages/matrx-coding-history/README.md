# matrx-coding-history

Claude Code's local history (`~/.claude/projects`) read, hashed and turned into AI Matrx
coding-session bridge envelopes, plus the capture reconciler that backfills sessions the hook
path missed. Shared by Matrx Local (this repo's engine) and Matrx 2 (`matrx-desktop/python/coding`).

Nothing here reads a database, keychain or app setting: each app injects its own
`BridgeSink`, `AccessTokens`, `SyncMeta`, `BackfillLedger`, `CloudHttp` and logger
(`matrx_coding_history.ports`).

    uv run pytest packages/matrx-coding-history/tests
