# Tools Overview

Cross-repo system-of-record: /Users/armanisadeghi/code/common-docs/systems/agents/agent-tools/STATE.md — read it before touching this feature in ANY repo.

**Authoritative list:** `app/tools/catalog.py`. Never maintain a second list here or anywhere
else — a hand-written category table goes stale the day it is written.

```bash
uv run python -m app.tools.tool_sync list
```

Local rules and traps: `app/tools/FEATURE.md`. Count enforced by
`tests/parity/test_tool_count.py`. Platform-gated tools carry platform flags in the catalog.

Invoke via `POST /tools/invoke` or WebSocket — [communication-protocols.md](./communication-protocols.md).
