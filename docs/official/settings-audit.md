# Settings System Audit

> Companion to [settings-catalog.md](./settings-catalog.md).  
> **Last updated:** 2026-03-25  
> Every `AppSettings` key tracked across all five concerns:  
> (1) Single source of truth, (2) Vite ↔ Python sync, (3) Local DB (localStorage + `~/.matrx/settings.json`), (4) Cloud sync, (5) UI error visibility

---

## Architecture

```
User changes setting
       │
       ▼
React draft state (use-configurations / useState in Settings.tsx)
       │  optimistic update — instant
       ▼
localStorage["matrx-settings"]    ← canonical frontend store
       │  saveSetting() / saveSettings()
       ▼
syncAllSettings() → PUT /cloud/settings
       │             writes ~/.matrx/settings.json
       │             if Supabase configured → upsert app_settings
       │
       ▼
Supabase (app_settings table)  ← cloud store; fire-and-forget
```

**Rules:**
- Local DB = localStorage (React-side) + `~/.matrx/settings.json` (Python-side). Both must stay in sync.
- Cloud sync is **non-blocking**. Work is done once localStorage + Python file are updated.
- Errors in engine sync surface as visible warnings. Errors in cloud sync are logged but never block the user.
- `instance_name` additionally writes to `app_instances` table (not just `app_settings`).

---

## Per-Setting Status Table

| # | Key | Display Name | State Source of Truth | Vite↔Python Sync | Local DB | Cloud Sync | UI Error Visibility | Status |
|---|-----|-------------|----------------------|-----------------|----------|------------|---------------------|--------|
| 1 | `instanceName` | Computer Name | localStorage | `PUT /cloud/settings` + re-registers `app_instances` | ✅ localStorage + `settings.json` | ✅ `app_settings` + `app_instances` | ✅ SyncStatusRow; Settings.tsx reloads on broadcast | ✅ **FIXED** |
| 2 | `launchOnStartup` | Launch on startup | localStorage | `syncSetting` → Tauri autostart plugin | ✅ localStorage + `settings.json` | ✅ `app_settings` | ⚠️ console.warn only in Settings.tsx (Configs shows error) | ⚠️ **PARTIAL** |
| 3 | `minimizeToTray` | Minimize to tray | localStorage | `syncSetting` → `setCloseToTray()` | ✅ localStorage + `settings.json` | ✅ `app_settings` | ⚠️ console.warn only in Settings.tsx | ⚠️ **PARTIAL** |
| 4 | `theme` | Theme | localStorage + `use-theme.ts` reads on mount | None needed (frontend-only) | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A (localStorage write never fails) | ✅ OK |
| 5 | `autoCheckUpdates` | Auto-check for updates | localStorage | None needed (read by `use-auto-update.ts`) | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A | ✅ OK |
| 6 | `updateCheckInterval` | Update check frequency | localStorage | None needed | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A | ✅ OK |
| 7 | `headlessScraping` | Hide scraping browser | localStorage | `syncSetting` → `engine.updateSettings()` | ✅ localStorage + `settings.json` | ✅ `app_settings` | ⚠️ console.warn only in Settings.tsx | ⚠️ **PARTIAL** |
| 8 | `scrapeDelay` | Delay between requests | localStorage | `syncSetting` → `engine.updateSettings()` | ✅ localStorage + `settings.json` | ✅ `app_settings` | ⚠️ console.warn only in Settings.tsx | ⚠️ **PARTIAL** |
| 9 | `proxyEnabled` | Local proxy enabled | localStorage | `syncSetting` → `engine.proxyStart/Stop()` | ✅ localStorage + `settings.json` | ✅ `app_settings` | ⚠️ console.warn only in Settings.tsx | ⚠️ **PARTIAL** |
| 10 | `proxyPort` | Proxy port | localStorage | `syncSetting` → proxy restart | ✅ localStorage + `settings.json` | ✅ `app_settings` | ⚠️ console.warn only in Settings.tsx | ⚠️ **PARTIAL** |
| 11 | `tunnelEnabled` | Remote tunnel | localStorage | `syncSetting` → `/tunnel/start\|stop` | ✅ localStorage + `settings.json` | ✅ `app_settings` | ⚠️ double-call in Settings.tsx (harmless but wasteful) | ⚠️ **PARTIAL** |
| 12 | `notificationSound` | Sound enabled | localStorage + `use-notifications.ts` ref | None (frontend-only) | ✅ localStorage + `settings.json` | ✅ `app_settings` | ❌ hook not reactive to changes after mount | ❌ **BUG** |
| 13 | `notificationSoundStyle` | Sound style | localStorage | None (frontend-only) | ✅ localStorage + `settings.json` | ✅ `app_settings` | ❌ `use-notifications.ts` ignores this field entirely | ❌ **BUG** |
| 14 | `wakeWordEnabled` | Wake word enabled | localStorage | None (read by `Voice.tsx` on mount) | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A (read at mount) | ✅ OK |
| 15 | `wakeWordListenOnStartup` | Listen on startup | localStorage | None | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A | ✅ OK |
| 16 | `wakeWordEngine` | Detection engine | localStorage | None | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A | ✅ OK |
| 17 | `wakeWordOwwModel` | OWW model | localStorage | None | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A | ✅ OK |
| 18 | `wakeWordOwwThreshold` | Detection threshold | localStorage | None | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A | ✅ OK |
| 19 | `wakeWordCustomKeyword` | Custom keyword | localStorage | None | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A | ✅ OK |
| 20 | `chatDefaultModel` | Default AI model | localStorage | None (read by chat at call time) | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A | ✅ OK |
| 21 | `chatDefaultMode` | Default chat mode | localStorage | None | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A | ✅ OK |
| 22 | `chatMaxConversations` | Max conversations | localStorage | None | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A | ✅ OK |
| 23 | `chatDefaultSystemPromptId` | System prompt | localStorage | None | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A | ✅ OK |
| 24 | `llmDefaultModel` | Default model (LLM) | localStorage | None (read by `LocalModels.tsx`) | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A | ✅ OK |
| 25 | `llmDefaultGpuLayers` | GPU layers | localStorage | None | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A | ✅ OK |
| 26 | `llmDefaultContextLength` | Context length | localStorage | None | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A | ✅ OK |
| 27 | `llmAutoStartServer` | Auto-start server | localStorage | None | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A | ✅ OK |
| 28 | `llmChatTemperature` | Chat Temperature | localStorage | None (read by `llm/api.ts` cached) | ✅ localStorage + `settings.json` | ✅ `app_settings` | ❌ `llm/api.ts` cache not invalidated on change | ❌ **BUG** |
| 29 | `llmChatTopP` | Chat Top P | localStorage | None (cached) | ✅ localStorage + `settings.json` | ✅ `app_settings` | ❌ cache not invalidated | ❌ **BUG** |
| 30 | `llmChatTopK` | Chat Top K | localStorage | None (cached) | ✅ localStorage + `settings.json` | ✅ `app_settings` | ❌ cache not invalidated | ❌ **BUG** |
| 31 | `llmChatMaxTokens` | Chat Max tokens | localStorage | None (cached) | ✅ localStorage + `settings.json` | ✅ `app_settings` | ❌ cache not invalidated | ❌ **BUG** |
| 32 | `llmReasoningTemperature` | Reasoning Temperature | localStorage | None (cached) | ✅ localStorage + `settings.json` | ✅ `app_settings` | ❌ cache not invalidated | ❌ **BUG** |
| 33 | `llmReasoningTopP` | Reasoning Top P | localStorage | None (cached) | ✅ localStorage + `settings.json` | ✅ `app_settings` | ❌ cache not invalidated | ❌ **BUG** |
| 34 | `llmEnableThinking` | Enable thinking | localStorage | None (cached) | ✅ localStorage + `settings.json` | ✅ `app_settings` | ❌ cache not invalidated | ❌ **BUG** |
| 35 | `llmToolCallTemperature` | Tool Call Temperature | localStorage | None (cached) | ✅ localStorage + `settings.json` | ✅ `app_settings` | ❌ cache not invalidated | ❌ **BUG** |
| 36 | `llmToolCallTopP` | Tool Call Top P | localStorage | None (cached) | ✅ localStorage + `settings.json` | ✅ `app_settings` | ❌ cache not invalidated | ❌ **BUG** |
| 37 | `llmToolCallTopK` | Tool Call Top K | localStorage | None (cached) | ✅ localStorage + `settings.json` | ✅ `app_settings` | ❌ cache not invalidated | ❌ **BUG** |
| 38 | `llmStructuredOutputTemperature` | Structured output temp | localStorage | None (cached) | ✅ localStorage + `settings.json` | ✅ `app_settings` | ❌ cache not invalidated | ❌ **BUG** |
| 39 | `llmStreamMaxTokens` | Stream max tokens | localStorage | None (cached) | ✅ localStorage + `settings.json` | ✅ `app_settings` | ❌ cache not invalidated | ❌ **BUG** |
| 40 | `transcriptionDefaultModel` | Default Whisper model | localStorage | None | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A | ✅ OK |
| 41 | `transcriptionAutoInit` | Auto-initialize | localStorage | None | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A | ✅ OK |
| 42 | `transcriptionAudioDevice` | Audio input device | localStorage | None | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A | ✅ OK |
| 43 | `transcriptionProcessingTimeout` | Processing timeout | localStorage | None | ✅ localStorage + `settings.json` | ✅ `app_settings` | N/A | ✅ OK |
| 44 | `sidebarCollapsed` | Sidebar collapsed | localStorage **+ raw write in AppSidebar.tsx** | None | ✅ localStorage + `settings.json` | ✅ `app_settings` | ❌ AppSidebar bypasses saveSetting(), no broadcast, no engine sync | ❌ **BUG** |

---

## Bugs Fixed in This Session

### B1 — `instanceName` never updated `app_instances` in Postgres
**Fixed:** `PUT /cloud/settings` now updates `InstanceManager.instance_name` and re-registers the instance whenever `instance_name` is in the payload. `InstanceManager._load_persisted_name()` reads from `settings.json` on startup so name survives engine restarts.

### B2 — `syncAllSettings()` swallowed all errors, always showed "Saved"
**Fixed:** `syncAllSettings()` now returns `SyncResult { local, engine, cloud }`. Each step is independently "ok" | "skipped" | error string. `use-configurations` captures this and `Configurations.tsx` shows a 6-second status row per section.

### B3 — React state isolated between pages (Settings.tsx + Configurations.tsx out of sync)
**Fixed:** `broadcastSettingsChanged()` fires `"matrx-settings-changed"` on `window` after every save. Settings.tsx listens and reloads its state + instance info. `use-configurations` reloads from localStorage when the event fires while mounted.

### B4 — `sidebarCollapsed` bypassed the settings system in AppSidebar.tsx
**Fixed:** `AppSidebar.toggleCollapsed()` now calls `saveSetting("sidebarCollapsed", next)` + `broadcastSettingsChanged()`. The legacy `"sidebar-collapsed"` key is written for backward compatibility only during init. Raw `JSON.parse/stringify` writes removed.

### B5 — `use-notifications.ts` ignored `notificationSoundStyle` and didn't react to changes
**Fixed:** Hook listens to `"matrx-settings-changed"` and reloads both `notificationSound` and `notificationSoundStyle`. `soundForLevel()` now respects the user's chosen style.

### B6 — `llm/api.ts` cache never invalidated, sampling setting changes not picked up
**Fixed:** Module-level listener for `"matrx-settings-changed"` calls `invalidateLlmSettingsCache()` immediately. Next LLM call after any settings change picks up fresh values.

### B7 — `reasoningParams()` used `llmChatTopK` instead of reasoning top_k
**Fixed:** Added `llmReasoningTopK` and `llmReasoningMaxTokens` to `AppSettings`, `DEFAULTS`, `settingsToCloud`, `mergeCloudSettings`, Python `DEFAULT_SETTINGS`. `reasoningParams()` now uses `s.llmReasoningTopK`.

### B8 — `Settings.tsx updateSetting()` only wrote to localStorage for non-side-effected keys
**Fixed:** `updateSetting()` now calls `syncAllSettings()` in the background after `saveSetting()`, ensuring the engine and cloud are always eventually updated. Errors are logged. User flow is not blocked.

### B9 — Corrupted localStorage parse silently reset all settings
**Fixed:** `loadSettings()` now logs `console.error` with a clear message when the parse fails, so devtools and activity logs surface the problem.

---

## Remaining Architectural Notes

- **`syncSetting()` for side-effected keys (proxy, tunnel, scraper):** These still only call `console.warn` on error in Settings.tsx because `updateSetting()` doesn't await/inspect the result. This is acceptable UX — the proxy/tunnel status UI will show the actual state immediately after the action.
- **Cloud sync timestamp comparison:** Uses ISO string lexicographic comparison in Python. Valid for UTC ISO-8601 but fragile. Tracked in AGENT_TASKS.md for a future improvement.
- **Orphan instance state not surfaced in UI:** `is_orphan` from `/cloud/configure` response is logged but no React component shows a warning. Tracked in AGENT_TASKS.md.
- **`sidebarCollapsed` initial read:** Still reads `"sidebar-collapsed"` (old key) as fallback on first mount for users upgrading from older versions. This fallback is intentional.
