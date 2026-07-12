# Desktop (Tauri)

---

## Process identity (macOS Activity Monitor)

| Process | Name | Owner |
|---------|------|-------|
| Tauri UI | **AI Matrx** | Rust — `aimatrx-desktop` |
| Python sidecar | **Matrx Engine** | Rust spawns; engine self-manages children |

WebKit helpers (`AI Matrx Web Content`, …) are OS-spawned from the bundle.

---

## Engine packaging by platform

| OS | Format | Location |
|----|--------|----------|
| **macOS** | `Matrx Engine.app` helper bundle | `Contents/Frameworks/` via `tauri.macos.conf.json` `bundle.macOS.files` — **not** `externalBin`. `LSUIElement` + `LSBackgroundOnly`. |
| **Windows / Linux** | `matrx-engine-<triple>(.exe)` | `externalBin` in `tauri.conf.json` |

macOS overlay **replaces** array fields (e.g. `externalBin`) — do not assume merge. See [build-lessons.md](./build-lessons.md).

PyInstaller: [development-and-build.md](./development-and-build.md). llama-server signing: [CLAUDE.md](../../CLAUDE.md).

---

## System tray

- No `trayIcon` in `tauri.conf.json` (duplicate icon bug)
- `setup_tray()` in `lib.rs` uses `TrayIconBuilder`
- Sidecar: `TAURI_SIDECAR=1` → engine skips `pystray`; standalone dev keeps pystray in `run.py`

---

## Content Security Policy

Allows: `127.0.0.1:*`, `*.supabase.co`, profile CDNs (Google, GitHub, Supabase). Config: `tauri.conf.json`.

Lifecycle (sidecar spawn/kill): [lifecycle-ownership.md](./lifecycle-ownership.md).
