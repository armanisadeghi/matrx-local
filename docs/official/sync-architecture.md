# Sync Architecture

**Ratified doctrine:** [docs/SYNC_CONTRACT.md](../SYNC_CONTRACT.md) — read before touching sync code.

One-liner: cloud is durable source of truth; local SQLite (`~/.matrx/matrx.db`) and local files are a first-access replica (offline-proof, never a competing server).

---

## Three subsystems

| Subsystem | Module | Role |
|----------|--------|------|
| **Replica pull** | `app/services/local_db/sync_engine.py` | AIDream models/agents + tool catalog → SQLite; startup + every 10 min. Only writer of cloud catalog into SQLite. |
| **Notes sync** | `app/services/documents/` | Bidirectional; content-hash conflicts, tombstones, file watcher. See `app/services/documents/FEATURE.md`. |
| **Cloud sync** | `app/services/cloud_sync/` | Whole-blob settings + instance registration/heartbeat (`app_instances`, tunnel URLs). |

---

## Tests

- `tests/characterization/` — pinned sync behavior
- `tests/parity/test_sync_contract.py` — contract parity
