# Matrx Local — Comprehensive Configuration Reference

> Last updated **2026-03-30**. Canonical definitions: `desktop/src/lib/settings.ts`
> (`AppSettings`, `DEFAULTS`, `settingsToCloud`, `mergeCloudSettings`) and
> `app/services/cloud_sync/settings_sync.py` (`DEFAULT_SETTINGS`). Cloud payload:
> Supabase `app_settings.settings_json` (per user + instance), synced via
> `SettingsSync` when the engine is configured and the desktop pushes settings.

---

## How storage works

| Layer | Role |
|-------|------|
| **localStorage `matrx-settings`** | Primary JSON blob for all `AppSettings` fields on the desktop. |
| **Engine + cloud** | `syncAllSettings()` calls `engine.updateCloudSettings(settingsToCloud(...))`; engine merges into `~/.matrx/settings.json` and may push to Supabase. |
| **Per-key native/engine sync** | `saveSetting()` only special-cases: `launchOnStartup`, `minimizeToTray`, `headlessScraping`, `scrapeDelay`, `proxyEnabled`, `proxyPort`, `tunnelEnabled`. Full blob still goes to engine on `syncAllSettings()`. |
| **Theme nuance** | `AppSettings.theme` is cloud-synced. `use-theme.ts` also persists **`matrx-theme`** for immediate DOM application. See **Gaps** — Configurations saves theme to `matrx-settings` only unless `setTheme` is invoked elsewhere. |

---

## 1. `AppSettings` — full catalog (localStorage + cloud)

All rows below use storage: **localStorage** key `matrx-settings` unless noted.
**Cloud** = included in `settingsToCloud` / `mergeCloudSettings` → `settings_json`.

### Application, updates, instance

| Setting | Key (local) | Key (cloud) | Type | Default | Cloud |
|---------|-------------|-------------|------|---------|-------|
| Launch on startup | `launchOnStartup` | `launch_on_startup` | boolean | `false` | Yes |
| Minimize to tray | `minimizeToTray` | `minimize_to_tray` | boolean | `true` | Yes |
| Theme | `theme` | `theme` | `dark` \| `light` \| `system` | `"dark"` | Yes |
| Auto-check updates | `autoCheckUpdates` | `auto_check_updates` | boolean | `true` | Yes |
| Update interval (minutes) | `updateCheckInterval` | `update_check_interval` | number (≥ 60 when merged) | `240` | Yes |
| Instance name | `instanceName` | `instance_name` | string | `"My Computer"` | Yes |

### Scraping, proxy, remote access, notifications

| Setting | Key (local) | Key (cloud) | Type | Default | Cloud |
|---------|-------------|-------------|------|---------|-------|
| Headless scraping | `headlessScraping` | `headless_scraping` | boolean | `true` | Yes |
| Scrape delay | `scrapeDelay` | `scrape_delay` | string / coerced to number in cloud | `"1.0"` | Yes |
| Proxy enabled | `proxyEnabled` | `proxy_enabled` | boolean | `true` | Yes |
| Proxy port | `proxyPort` | `proxy_port` | number | `22180` | Yes |
| Tunnel enabled | `tunnelEnabled` | `tunnel_enabled` | boolean | `false` | Yes |
| Notification sound | `notificationSound` | `notification_sound` | boolean | `true` | Yes |
| Notification sound style | `notificationSoundStyle` | `notification_sound_style` | `chime` \| `alert` \| `success` \| `error` | `"chime"` | Yes |

### Wake word

| Setting | Key (local) | Key (cloud) | Type | Default | Cloud |
|---------|-------------|-------------|------|---------|-------|
| Wake word enabled | `wakeWordEnabled` | `wake_word_enabled` | boolean | `true` | Yes |
| Listen on startup | `wakeWordListenOnStartup` | `wake_word_listen_on_startup` | boolean | `true` | Yes |
| Engine | `wakeWordEngine` | `wake_word_engine` | `whisper` \| `oww` | `"whisper"` | Yes |
| OWW model filename | `wakeWordOwwModel` | `wake_word_oww_model` | string | `"hey_jarvis"` | Yes |
| OWW threshold | `wakeWordOwwThreshold` | `wake_word_oww_threshold` | number | `0.5` | Yes |
| Custom keyword (whisper) | `wakeWordCustomKeyword` | `wake_word_custom_keyword` | string | `"hey matrix"` | Yes |

**Note:** Rust still persists the whisper keyword to **`transcription.json`** when `configure_wake_word` runs. That is separate from the React/Supabase blob — see **Gaps**.

### Chat and AI defaults

| Setting | Key (local) | Key (cloud) | Type | Default | Cloud |
|---------|-------------|-------------|------|---------|-------|
| Default cloud/local model id | `chatDefaultModel` | `chat_default_model` | string (`""` = first from engine) | `""` | Yes |
| Default mode | `chatDefaultMode` | `chat_default_mode` | `chat` \| `co-work` \| `code` | `"chat"` | Yes |
| Max conversations (cap target) | `chatMaxConversations` | `chat_max_conversations` | number | `100` | Yes |
| Default system prompt id | `chatDefaultSystemPromptId` | `chat_default_system_prompt_id` | string (`""` = builtin) | `""` | Yes |

### Local LLM (model + sampling)

| Setting | Key (local) | Key (cloud) | Type | Default | Cloud |
|---------|-------------|-------------|------|---------|-------|
| Default model filename | `llmDefaultModel` | `llm_default_model` | string (`""` = auto) | `""` | Yes |
| GPU layers | `llmDefaultGpuLayers` | `llm_default_gpu_layers` | number (`-1` = auto) | `-1` | Yes |
| Context length | `llmDefaultContextLength` | `llm_default_context_length` | number | `8192` | Yes |
| Auto-start llama-server | `llmAutoStartServer` | `llm_auto_start_server` | boolean | `false` | Yes |
| Chat temperature | `llmChatTemperature` | `llm_chat_temperature` | number | `0.7` | Yes |
| Chat top_p | `llmChatTopP` | `llm_chat_top_p` | number | `0.8` | Yes |
| Chat top_k | `llmChatTopK` | `llm_chat_top_k` | number | `20` | Yes |
| Chat max tokens | `llmChatMaxTokens` | `llm_chat_max_tokens` | number | `1024` | Yes |
| Reasoning temperature | `llmReasoningTemperature` | `llm_reasoning_temperature` | number | `0.6` | Yes |
| Reasoning top_p | `llmReasoningTopP` | `llm_reasoning_top_p` | number | `0.95` | Yes |
| Reasoning top_k | `llmReasoningTopK` | `llm_reasoning_top_k` | number | `20` | Yes |
| Reasoning max tokens | `llmReasoningMaxTokens` | `llm_reasoning_max_tokens` | number | `4096` | Yes |
| Enable thinking | `llmEnableThinking` | `llm_enable_thinking` | boolean | `false` | Yes |
| Tool-call temperature | `llmToolCallTemperature` | `llm_tool_call_temperature` | number | `0.7` | Yes |
| Tool-call top_p | `llmToolCallTopP` | `llm_tool_call_top_p` | number | `0.8` | Yes |
| Tool-call top_k | `llmToolCallTopK` | `llm_tool_call_top_k` | number | `20` | Yes |
| Structured output temperature | `llmStructuredOutputTemperature` | `llm_structured_output_temperature` | number | `0.1` | Yes |
| Stream max tokens | `llmStreamMaxTokens` | `llm_stream_max_tokens` | number | `1024` | Yes |

Sampling values are read by `desktop/src/lib/llm/api.ts` from `AppSettings` (not hardcoded constants).

### Transcription / voice

| Setting | Key (local) | Key (cloud) | Type | Default | Cloud |
|---------|-------------|-------------|------|---------|-------|
| Default Whisper model | `transcriptionDefaultModel` | `transcription_default_model` | string (`""` = auto) | `""` | Yes |
| Auto-init on startup | `transcriptionAutoInit` | `transcription_auto_init` | boolean | `true` | Yes |
| Audio input device | `transcriptionAudioDevice` | `transcription_audio_device` | string (`""` = default) | `""` | Yes |
| Processing timeout (ms) | `transcriptionProcessingTimeout` | `transcription_processing_timeout` | number | `15000` | Yes |

Rust **`transcription.json`** still holds `selected_model`, `setup_complete`, `wake_keyword` for native code paths.

### Text to speech

| Setting | Key (local) | Key (cloud) | Type | Default | Cloud |
|---------|-------------|-------------|------|---------|-------|
| Default voice | `ttsDefaultVoice` | `tts_default_voice` | string | `"af_heart"` | Yes |
| Default speed | `ttsDefaultSpeed` | `tts_default_speed` | number | `1.0` | Yes |
| Auto-download model | `ttsAutoDownloadModel` | `tts_auto_download_model` | boolean | `false` | Yes |
| Favorite voices | `ttsFavoriteVoices` | `tts_favorite_voices` | string[] | `[]` | Yes |
| Chat read-aloud voice | `ttsChatVoice` | `tts_chat_voice` | string (`""` = use default) | `""` | Yes |
| Chat read-aloud speed | `ttsChatSpeed` | `tts_chat_speed` | number (`0` = use default speed) | `0` | Yes |
| Notification voice | `ttsNotificationVoice` | `tts_notification_voice` | string | `""` | Yes |
| Read-aloud enabled | `ttsReadAloudEnabled` | `tts_read_aloud_enabled` | boolean | `true` | Yes |
| Read-aloud autoplay | `ttsReadAloudAutoPlay` | `tts_read_aloud_auto_play` | boolean | `false` | Yes |
| Streaming threshold (chars) | `ttsStreamingThreshold` | `tts_streaming_threshold` | number | `200` | Yes |
| Auto-clean markdown (TTS page) | `ttsAutoCleanMarkdown` | `tts_auto_clean_markdown` | boolean | `false` | Yes |

### UI

| Setting | Key (local) | Key (cloud) | Type | Default | Cloud |
|---------|-------------|-------------|------|---------|-------|
| Sidebar collapsed | `sidebarCollapsed` | `sidebar_collapsed` | boolean | `false` | Yes |

`AppSidebar` also reads/writes legacy **`sidebar-collapsed`** for backward compatibility.

**Field count:** 58 keys in `AppSettings`, all mapped to cloud snake_case.

---

## 2. Configurations page (`Configurations.tsx` + `use-configurations.ts`)

Dirty-tracking sections (each has section save/cancel):

| Section id | `AppSettings` keys |
|------------|-------------------|
| `application` | `instanceName`, `launchOnStartup`, `minimizeToTray`, `autoCheckUpdates`, `updateCheckInterval` |
| `appearance` | `theme`, `sidebarCollapsed` |
| `chatAi` | `chatDefaultModel`, `chatDefaultMode`, `chatMaxConversations`, `chatDefaultSystemPromptId` |
| `localLlm` | `llmDefaultModel`, `llmDefaultGpuLayers`, `llmDefaultContextLength`, `llmAutoStartServer` |
| `localLlmSampling` | all `llm*` sampling / thinking keys (14 keys) |
| `voice` | `transcriptionDefaultModel`, `transcriptionAutoInit`, `transcriptionAudioDevice`, `transcriptionProcessingTimeout` |
| `tts` | all `tts*` keys (12 keys) |
| `wakeWord` | all `wakeWord*` keys (6 keys) |
| `scraping` | `headlessScraping`, `scrapeDelay` |
| `proxy` | `proxyEnabled`, `proxyPort`, `tunnelEnabled` |
| `notifications` | `notificationSound`, `notificationSoundStyle` |

Legacy **Settings** page still exposes overlapping controls (e.g. theme with `setTheme` + `updateSetting`).

---

## 3. Engine-only and environment (not in `AppSettings`)

### Runtime discovery and secrets (`.env`, `~/.matrx/local.json`)

| Concern | Location | Cloud |
|---------|----------|-------|
| Engine listen port | `MATRX_PORT`, discovery, `~/.matrx/local.json` | No |
| `API_KEY` | `.env` | No |
| `SCRAPER_API_KEY`, `SCRAPER_SERVER_URL` | `.env` | No |
| `DATABASE_URL` (local PG cache) | `.env` | No |
| Brave, proxies, `DEBUG`, `LOG_LEVEL`, CORS extras, tunnel token | `app/config.py` / `.env` | No |

### Engine settings file (`~/.matrx/settings.json` via sync)

| Concern | API / code | Cloud |
|---------|------------|-------|
| Documents / downloads / scrape paths | `GET/PUT /settings/paths` | No |
| Forbidden URL patterns | `/settings/forbidden-urls` | No |
| Provider API keys (Anthropic, OpenAI, …) | `/settings/api-keys` | No |
| Hugging Face token (GGUF / gated downloads) | `/settings/api-keys/huggingface` (+ Tauri `llm.json` fallback) | No |

Detail for env vars: [configuration.md](./configuration.md).

---

## 4. Other desktop persistence (not `AppSettings`)

These are **not** in `settings_json` unless separately migrated.

| Key / store | Purpose |
|-------------|---------|
| `matrx-theme` | Theme for `useTheme()` / DOM class (`dark`). |
| `matrx-system-prompts` | Full prompt library (`system-prompts.ts`); `chatDefaultSystemPromptId` references an id here. |
| `matrx-selected-audio-device` | `AudioDevicesContext` selected mic (may diverge from `transcriptionAudioDevice`). |
| `matrx-chat-conversations` | Chat thread persistence. |
| `llm-playground-conversations` | Local Models playground chats. |
| `custom-model-names` | User renames for local GGUF models. |
| `matrx:scrape-history` | Recent scrape URLs. |
| Transcription sessions store | `lib/transcription/sessions.ts` localStorage key. |
| `matrx-setup-dismissed` | First-run / setup wizard. |
| Updater keys in `use-auto-update.ts` | Prepared update version, dismissed version banners. |
| OAuth PKCE (`oauth.ts`, `use-auth.ts`) | Transient auth flow. |
| Installed apps panel cache | One-off cache in `InstalledAppsPanel`. |
| `~/.matrx/downloads.db` (Rust) | Download manager queue state. |

---

## 5. Data flow (unchanged)

```
Configurations or Settings saves
  → saveSettings() → localStorage `matrx-settings`
  → syncAllSettings() → engine.updateCloudSettings(settingsToCloud(...))
  → engine persists + optional Supabase `app_settings.settings_json`
  → hydrateFromEngine() / merge when cloud is newer
```

---

## 6. Gaps, fixes, and tracking recommendations

Items to fix or unify — **not** exhaustive of all product work, but specific to configuration consistency.

### Must-fix (incorrect or confusing behavior)

1. **Theme dual storage** — `AppSettings.theme` syncs to cloud; **`matrx-theme`** is what `useTheme()` reads/writes. Saving theme from **Configurations** updates only `matrx-settings`, so `<html>` class can stay stale until reload or a Settings-page path that calls `setTheme`. **Fix:** On Configurations save (appearance section or `saveAll`), call the same `setTheme` used on the Settings page, or subscribe to `matrx-settings-changed` in `useTheme` and reconcile.

2. **`chatMaxConversations` not applied** — `use-chat.ts` still uses a hardcoded `MAX_CONVERSATIONS = 100` when persisting `matrx-chat-conversations`, ignoring `AppSettings.chatMaxConversations`. **Fix:** Read the cap from settings (or ref) when slicing saved conversations.

3. **Duplicate microphone preference** — `transcriptionAudioDevice` (AppSettings, cloud) vs **`matrx-selected-audio-device`** (`AudioDevicesContext`). Users can end up with two sources of truth. **Fix:** Single key (prefer AppSettings) and migrate context to read/write it, or explicit bidirectional sync on mount/save.

4. **Wake keyword dual persistence** — `wakeWordCustomKeyword` in Supabase vs **`transcription.json`** `wake_keyword` updated by Rust `configure_wake_word`. **Fix:** On save from Configurations, invoke Tauri to align native config, or document that native path is authoritative until next sync.

### Should track in Configurations or engine UI (today elsewhere only)

5. **Forbidden URLs** — Engine-only (`~/.matrx/settings.json`). Consider a “Scraping safety” card on Configurations or deep-link to Settings scraper tab so users discover it.

6. **Storage paths** — Same: engine `/settings/paths`; optional surfaced row on Configurations.

7. **Image generation defaults** — Presets/steps/guidance on Local Models / image-gen UI appear ephemeral; if users expect cross-device parity, add `AppSettings` keys + sections.

8. **Chat sidebar width/collapsed** — Still component state in `Chat.tsx` (not `AppSettings`). Add keys if layout prefs should sync.

9. **Local Models: `custom-model-names`** — localStorage only. Optional: fold into `AppSettings` for cloud backup of display labels.

10. **Scrape history / playground / sessions** — Ephemeral local data; usually OK not in cloud; document as “device-local only.”

### Operational / documentation

11. **Python `DEFAULT_SETTINGS` vs TypeScript** — Already maintained in parallel; any new `AppSettings` key **must** land in both files (project rule). CI or a codegen check would reduce drift.

12. **HF / API keys** — Correctly remain out of `settings_json` for security; keep in engine secret store + `.env` only.

13. **`llmAutoStartServer`** — Field exists in `AppSettings`; verify end-to-end that app launch actually starts llama-server when true (if not wired, track as implementation gap in `AGENT_TASKS.md`).

14. **Wake word engine switching** — Ensure choosing `oww` vs `whisper` in Configurations propagates to Rust/Python listeners the same as the Voice page (integration test on device).

### Resolved vs old docs (for clarity)

- Wake word and LLM sampling are **no longer** “local only” or “all hardcoded”: they live in **`AppSettings`** and **cloud `settings_json`**.
- Wake word settings are **not** stored in “Rust SQLite” for this product path; Rust uses **`transcription.json`** for parts of transcription + keyword side effects.
- TTS preferences are first-class in **`AppSettings`** and synced.

---

*When adding a new preference: extend `AppSettings` + `DEFAULTS` + `mergeCloudSettings` + `settingsToCloud` + `DEFAULT_SETTINGS` in Python, add to `SECTION_KEYS` if it belongs on Configurations, and update this file.*
