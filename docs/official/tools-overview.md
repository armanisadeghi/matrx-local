# Tools Overview

**Authoritative list:** `app/tools/catalog.py`

```bash
uv run python -m app.tools.tool_sync list
```

Cloud canon: Supabase `tool.definition` ⨝ `tool.binding` (executor `matrx-local`). Rules: `app/tools/FEATURE.md`. Count enforced by `tests/parity/test_tool_count.py`.

---

## Categories (human overview — may lag catalog)

| Category | Examples |
|----------|----------|
| File ops | Read, Write, Edit, Glob, Grep |
| Shell | Bash, BashOutput, TaskStop |
| System | SystemInfo, Screenshot, ListDirectory, OpenUrl, OpenPath |
| Clipboard / notify | ClipboardRead/Write, Notify |
| Network | FetchUrl, FetchWithBrowser, Scrape, Search, Research |
| Browser automation | BrowserNavigate, Click, Type, Extract, Screenshot, Eval, Tabs |
| Process / window / input | ListProcesses, LaunchApp, ListWindows, TypeText, Hotkey, … |
| Audio | ListAudioDevices, RecordAudio, PlayAudio, TranscribeAudio |
| Discovery / monitoring | NetworkInfo, PortScan, SystemResources, WatchDirectory, … |
| OS integration | AppleScript, PowerShellScript, ScheduleTask, ImageOCR, WifiNetworks, … |

Platform-gated tools (mail, messages, calendar, etc.) are in the catalog with platform flags — do not duplicate rows here.

Invoke via `POST /tools/invoke` or WebSocket — [communication-protocols.md](./communication-protocols.md).
