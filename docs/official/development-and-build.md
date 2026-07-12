# Development & Build

Dev commands: [CLAUDE.md](../../CLAUDE.md). CI gotchas: [build-lessons.md](./build-lessons.md).

---

## Prerequisites

- uv, Node 20+, pnpm 10+, Rust (Tauri builds)
- `uv run playwright install chromium` for scraping

---

## Development modes

| Mode | Engine | UI |
|------|--------|-----|
| Split dev | `uv run python run.py` | `cd desktop && pnpm dev` → :1420 |
| Full desktop | `./scripts/build-sidecar.sh` once | `cd desktop && pnpm tauri dev` |

Optional: `./scripts/launch.sh` for combined dev launch.

---

### Linux / WSL2

Tauri from WSL2 builds a native Linux binary. GUI needs WSLg (Win11) or an X server. Install deps: `libwebkit2gtk-4.1-dev`, `libgtk-3-dev`, `libayatana-appindicator3-dev`, `librsvg2-dev`, `libssl-dev`, `build-essential`. Install Rust via rustup inside WSL (Windows `cargo` does not work there).

---

## Distribution build

```bash
uv sync && bash scripts/build-sidecar.sh
cd desktop && pnpm install && pnpm tauri build
```

Outputs: `.dmg` (macOS), `.msi`/NSIS (Windows), `.deb` (Linux).

---

## CI/CD

GitHub Actions: `.github/workflows/release.yml` — push `v*` tag → cross-platform release + Tauri updater `latest.json`.

**macOS signing:** `APPLE_CERTIFICATE`, `APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID`. Sign sidecar dylibs, llama-server, llama.cpp dylibs, app bundle (hardened runtime + notarization).

PyInstaller specs: `specs/*.spec`, `scripts/build-sidecar.sh`. Hidden-import sync rule: [CLAUDE.md](../../CLAUDE.md).

Release workflow notes: [release-script-guide.md](../release-script-guide.md).
