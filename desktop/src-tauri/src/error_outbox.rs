//! Disk-backed diagnostics outbox for renderer/native failures.
//!
//! This is transport persistence only. Issue identity and repair state remain
//! in the platform's existing `ops.system_error` / issue tracker. Keeping the
//! queue in Rust means a renderer failure can survive restart even while the
//! Python engine is unavailable.

use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;

const SCHEMA_VERSION: u8 = 1;
const MAX_RECORDS: usize = 1_000;
const MAX_ID_CHARS: usize = 128;
const MAX_SOURCE_CHARS: usize = 128;
const MAX_MESSAGE_CHARS: usize = 2_000;
const MAX_ROUTE_CHARS: usize = 512;
const MAX_WINDOW_CHARS: usize = 128;
const MIN_CAUSAL_SIGNATURE_CHARS: usize = 16;
const MAX_CAUSAL_SIGNATURE_CHARS: usize = 128;
const OUTBOX_FILE: &str = "error-outbox-v1.json";

static OUTBOX_LOCK: Mutex<()> = Mutex::new(());
static NATIVE_EVENT_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ErrorOutboxEvent {
    pub id: String,
    pub schema_version: u8,
    pub occurred_at: String,
    pub level: String,
    pub source: String,
    pub message: String,
    pub route: String,
    pub window_label: String,
    pub user_id: Option<String>,
    pub organization_id: Option<String>,
    #[serde(default)]
    pub causal_signature: Option<String>,
}

fn is_safe_causal_signature(value: &str) -> bool {
    (MIN_CAUSAL_SIGNATURE_CHARS..=MAX_CAUSAL_SIGNATURE_CHARS).contains(&value.len())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn clamp(value: String, max_chars: usize) -> String {
    value
        .chars()
        .filter(|ch| !ch.is_control() || *ch == '\n' || *ch == '\t')
        .take(max_chars)
        .collect()
}

fn redact_marker(mut value: String, marker: &str, whole_line: bool) -> String {
    let mut cursor = 0;
    loop {
        let lower = value[cursor..].to_ascii_lowercase();
        let Some(relative_marker_at) = lower.find(marker) else {
            return value;
        };
        let marker_at = cursor + relative_marker_at;
        let mut start = marker_at + marker.len();
        if marker != "bearer " && marker != "basic " {
            let tail = &value[start..];
            let Some(separator_at) = tail.find([':', '=', '%']) else {
                cursor = start;
                continue;
            };
            if separator_at > 4 {
                cursor = start;
                continue;
            }
            start += separator_at + 1;
            if value[start..].starts_with("3D") || value[start..].starts_with("3d") {
                start += 2;
            }
        }
        while value[start..].starts_with([' ', '\t', '\"', '\'', ':', '=']) {
            start += value[start..].chars().next().unwrap().len_utf8();
        }
        if value[start..].starts_with("[REDACTED]") {
            cursor = start + "[REDACTED]".len();
            continue;
        }
        let end = if whole_line {
            value[start..]
                .find(['\r', '\n'])
                .map(|offset| start + offset)
                .unwrap_or(value.len())
        } else {
            value[start..]
                .find([' ', '\t', '\r', '\n', '&', ',', ';', '}', ']', '\"', '\''])
                .map(|offset| start + offset)
                .unwrap_or(value.len())
        };
        if end <= start {
            cursor = start;
            continue;
        }
        value.replace_range(start..end, "[REDACTED]");
        cursor = start + "[REDACTED]".len();
    }
}

fn is_sensitive_key(key: &str) -> bool {
    let bytes = key.as_bytes();
    let mut decoded = String::with_capacity(key.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%' && index + 2 < bytes.len() {
            let hex = &key[index + 1..index + 3];
            if let Ok(byte) = u8::from_str_radix(hex, 16) {
                decoded.push(byte as char);
                index += 3;
                continue;
            }
        }
        decoded.push(bytes[index] as char);
        index += 1;
    }
    let normalized: String = decoded
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect();
    let has_key_segment = decoded
        .to_ascii_lowercase()
        .split(|ch: char| !ch.is_ascii_alphanumeric())
        .any(|segment| segment == "key");
    normalized == "code"
        || normalized == "state"
        || has_key_segment
        || [
            "token",
            "secret",
            "password",
            "passwd",
            "credential",
            "apikey",
            "authorization",
            "cookie",
        ]
        .iter()
        .any(|marker| normalized.contains(marker))
}

fn redact_sensitive_pairs(mut value: String) -> String {
    let mut cursor = 0;
    while cursor < value.len() {
        let Some(relative_separator) = value[cursor..].find([':', '=']) else {
            break;
        };
        let separator = cursor + relative_separator;
        let bytes = value.as_bytes();
        let mut key_end = separator;
        while key_end > 0 && matches!(bytes[key_end - 1], b' ' | b'\t' | b'\'' | b'"') {
            key_end -= 1;
        }
        let mut key_start = key_end;
        while key_start > 0
            && (bytes[key_start - 1].is_ascii_alphanumeric()
                || matches!(bytes[key_start - 1], b'_' | b'-' | b'.' | b'%'))
        {
            key_start -= 1;
        }
        if key_start == key_end || !is_sensitive_key(&value[key_start..key_end]) {
            cursor = separator + 1;
            continue;
        }

        let normalized_key: String = value[key_start..key_end]
            .chars()
            .filter(|ch| ch.is_ascii_alphanumeric())
            .flat_map(char::to_lowercase)
            .collect();
        let mut start = separator + 1;
        while start < value.len() && matches!(value.as_bytes()[start], b' ' | b'\t') {
            start += 1;
        }
        let quote = if start < value.len() && matches!(value.as_bytes()[start], b'\'' | b'"') {
            let quote = value.as_bytes()[start];
            start += 1;
            Some(quote)
        } else {
            None
        };
        let whole_line =
            normalized_key.contains("authorization") || normalized_key.contains("cookie");
        let end = if whole_line {
            value[start..]
                .find(['\r', '\n'])
                .map(|offset| start + offset)
                .unwrap_or(value.len())
        } else if let Some(quote) = quote {
            value.as_bytes()[start..]
                .iter()
                .position(|byte| *byte == quote)
                .map(|offset| start + offset)
                .unwrap_or(value.len())
        } else {
            value[start..]
                .find([' ', '\t', '\r', '\n', '&', ',', ';', '}', ']', '"', '\''])
                .map(|offset| start + offset)
                .unwrap_or(value.len())
        };
        if end > start {
            value.replace_range(start..end, "[REDACTED]");
            cursor = start + "[REDACTED]".len();
        } else {
            cursor = separator + 1;
        }
    }
    value
}

fn redact_prefixed_token(mut value: String, prefix: &str, minimum_chars: usize) -> String {
    let mut cursor = 0;
    loop {
        let Some(relative_start) = value[cursor..].find(prefix) else {
            return value;
        };
        let start = cursor + relative_start;
        let end = value[start..]
            .char_indices()
            .take_while(|(_, ch)| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.'))
            .last()
            .map(|(offset, ch)| start + offset + ch.len_utf8())
            .unwrap_or(start);
        if end - start >= minimum_chars {
            value.replace_range(start..end, "[REDACTED]");
            cursor = start + "[REDACTED]".len();
        } else {
            cursor = end.max(start + prefix.len());
        }
    }
}

fn redact_jwt_like(mut value: String) -> String {
    let mut cursor = 0;
    loop {
        let Some(relative_start) = value[cursor..].find("eyJ") else {
            return value;
        };
        let start = cursor + relative_start;
        let end = value[start..]
            .char_indices()
            .take_while(|(_, ch)| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.'))
            .last()
            .map(|(offset, ch)| start + offset + ch.len_utf8())
            .unwrap_or(start);
        let candidate = &value[start..end];
        let segments: Vec<&str> = candidate.split('.').collect();
        if segments.len() == 3 && segments.iter().all(|segment| !segment.is_empty()) {
            value.replace_range(start..end, "[REDACTED]");
            cursor = start + "[REDACTED]".len();
        } else {
            cursor = end.max(start + 3);
        }
    }
}

fn redact_sensitive(value: String) -> String {
    let mut redacted = redact_sensitive_pairs(value);
    redacted = redact_marker(redacted, "bearer ", false);
    redacted = redact_marker(redacted, "basic ", false);
    redacted = redact_prefixed_token(redacted, "sk-", 11);
    redact_jwt_like(redacted)
}

fn validate(mut event: ErrorOutboxEvent) -> Result<ErrorOutboxEvent, String> {
    if event.schema_version != SCHEMA_VERSION {
        return Err(format!(
            "unsupported diagnostics outbox schema version {}",
            event.schema_version
        ));
    }
    if event.level != "warn" && event.level != "error" {
        return Err("only warning and error diagnostics are durable".into());
    }
    event.id = clamp(event.id, MAX_ID_CHARS);
    event.source = clamp(redact_sensitive(event.source), MAX_SOURCE_CHARS);
    event.message = clamp(redact_sensitive(event.message), MAX_MESSAGE_CHARS);
    event.route = clamp(event.route, MAX_ROUTE_CHARS);
    event.window_label = clamp(event.window_label, MAX_WINDOW_CHARS);
    event.user_id = event.user_id.map(|value| clamp(value, MAX_ID_CHARS));
    event.organization_id = event
        .organization_id
        .map(|value| clamp(value, MAX_ID_CHARS));
    if let Some(signature) = event.causal_signature.as_deref() {
        if !is_safe_causal_signature(signature) {
            return Err(
                "diagnostics causal signature must be a 16-128 character lowercase hex fingerprint"
                    .into(),
            );
        }
    }
    event.occurred_at = clamp(event.occurred_at, 64);
    if event.id.is_empty() || event.message.is_empty() || event.occurred_at.is_empty() {
        return Err("diagnostics event requires id, occurredAt, and message".into());
    }
    Ok(event)
}

fn outbox_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let _ = app;
    crate::matrx_home_dir()
        .map(|dir| dir.join("diagnostics").join(OUTBOX_FILE))
        .ok_or_else(|| "diagnostics outbox home unavailable for this runtime".to_string())
}

fn read_events(path: &Path) -> Result<Vec<ErrorOutboxEvent>, String> {
    match std::fs::read(path) {
        Ok(bytes) => serde_json::from_slice(&bytes)
            .map_err(|error| format!("diagnostics outbox is unreadable: {error}")),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            let backup_path = path.with_extension("json.bak");
            match std::fs::read(backup_path) {
                Ok(bytes) => serde_json::from_slice(&bytes)
                    .map_err(|error| format!("diagnostics outbox backup is unreadable: {error}")),
                Err(backup_error) if backup_error.kind() == std::io::ErrorKind::NotFound => {
                    Ok(Vec::new())
                }
                Err(backup_error) => Err(format!(
                    "could not read diagnostics outbox backup: {backup_error}"
                )),
            }
        }
        Err(error) => Err(format!("could not read diagnostics outbox: {error}")),
    }
}

fn write_events(path: &Path, events: &[ErrorOutboxEvent]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "diagnostics outbox has no parent directory".to_string())?;
    std::fs::create_dir_all(parent)
        .map_err(|error| format!("could not create diagnostics directory: {error}"))?;
    let temp_path = path.with_extension("json.tmp");
    let encoded = serde_json::to_vec(events)
        .map_err(|error| format!("could not encode diagnostics outbox: {error}"))?;

    let mut options = std::fs::OpenOptions::new();
    options.create(true).truncate(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options
        .open(&temp_path)
        .map_err(|error| format!("could not open diagnostics outbox temp file: {error}"))?;
    file.write_all(&encoded)
        .and_then(|_| file.sync_all())
        .map_err(|error| format!("could not persist diagnostics outbox: {error}"))?;
    // Windows cannot rename over an existing file. Rotate the previous queue
    // to a recoverable backup first; a crash between these renames is handled
    // by `read_events`, which falls back to that backup.
    let backup_path = path.with_extension("json.bak");
    if !path.exists() && backup_path.exists() {
        std::fs::rename(&backup_path, path)
            .map_err(|error| format!("could not restore diagnostics outbox backup: {error}"))?;
    }
    if path.exists() {
        if backup_path.exists() {
            std::fs::remove_file(&backup_path).map_err(|error| {
                format!("could not replace stale diagnostics outbox backup: {error}")
            })?;
        }
        std::fs::rename(path, &backup_path)
            .map_err(|error| format!("could not rotate diagnostics outbox: {error}"))?;
    }
    if let Err(error) = std::fs::rename(&temp_path, path) {
        let _ = std::fs::rename(&backup_path, path);
        return Err(format!("could not commit diagnostics outbox: {error}"));
    }
    let _ = std::fs::remove_file(backup_path);
    Ok(())
}

fn enqueue_at(path: &Path, event: ErrorOutboxEvent) -> Result<(), String> {
    let event = validate(event)?;
    let mut events = read_events(path)?;
    if events.iter().any(|existing| existing.id == event.id) {
        return Ok(());
    }
    if let Some(signature) = event.causal_signature.as_deref() {
        if events.iter().any(|existing| {
            existing.causal_signature.as_deref() == Some(signature)
                && existing.source == event.source
                && existing.user_id == event.user_id
                && existing.organization_id == event.organization_id
        }) {
            return Ok(());
        }
    }
    events.push(event);
    if events.len() > MAX_RECORDS {
        events.drain(0..events.len() - MAX_RECORDS);
    }
    write_events(path, &events)
}

fn native_event_at(
    level: &str,
    source: &str,
    message: String,
    unix_millis: u128,
    sequence: u64,
) -> ErrorOutboxEvent {
    let occurred_at =
        crate::lifecycle_log::format_utc((unix_millis / 1_000) as u64).replacen(' ', "T", 1);
    ErrorOutboxEvent {
        id: format!("native-{}-{}-{}", std::process::id(), unix_millis, sequence),
        schema_version: SCHEMA_VERSION,
        occurred_at,
        level: level.to_string(),
        source: source.to_string(),
        message,
        route: "native://desktop".into(),
        window_label: "native".into(),
        // Native startup and device failures can happen before authentication.
        // They remain identity-free and installation-local rather than being
        // reassigned to whichever user signs in next.
        user_id: None,
        organization_id: None,
        causal_signature: None,
    }
}

/// Persist a native warning/error through the same bounded, redacting queue
/// used by renderer diagnostics. This never writes Python-owned storage and
/// is safe before the engine or authenticated renderer is available.
pub async fn enqueue_native_event(
    app: tauri::AppHandle,
    level: &str,
    source: &str,
    message: impl Into<String>,
) -> Result<(), String> {
    let unix_millis = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or(0);
    let sequence = NATIVE_EVENT_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    enqueue_error_outbox_event(
        app,
        native_event_at(level, source, message.into(), unix_millis, sequence),
    )
    .await
}

fn acknowledge_at(path: &Path, ids: &[String]) -> Result<(), String> {
    if ids.is_empty() {
        return Ok(());
    }
    let acknowledged: HashSet<&str> = ids.iter().map(String::as_str).collect();
    let mut events = read_events(path)?;
    let original_len = events.len();
    events.retain(|event| !acknowledged.contains(event.id.as_str()));
    if events.len() != original_len {
        write_events(path, &events)?;
    }
    Ok(())
}

#[tauri::command]
pub async fn enqueue_error_outbox_event(
    app: tauri::AppHandle,
    event: ErrorOutboxEvent,
) -> Result<(), String> {
    let path = outbox_path(&app)?;
    tokio::task::spawn_blocking(move || {
        let _guard = OUTBOX_LOCK
            .lock()
            .map_err(|_| "diagnostics outbox lock was poisoned".to_string())?;
        enqueue_at(&path, event)
    })
    .await
    .map_err(|error| format!("diagnostics outbox task failed: {error}"))?
}

#[tauri::command]
pub async fn read_error_outbox_events(
    app: tauri::AppHandle,
    limit: Option<usize>,
) -> Result<Vec<ErrorOutboxEvent>, String> {
    // Dev and isolated smoke worlds retain their own local evidence but are
    // never telemetry producers for the installed fleet.
    if cfg!(debug_assertions) || crate::isolated_test_run() {
        return Ok(Vec::new());
    }
    let path = outbox_path(&app)?;
    tokio::task::spawn_blocking(move || {
        let _guard = OUTBOX_LOCK
            .lock()
            .map_err(|_| "diagnostics outbox lock was poisoned".to_string())?;
        let events = read_events(&path)?;
        Ok(events
            .into_iter()
            .take(limit.unwrap_or(20).clamp(1, MAX_RECORDS))
            .collect())
    })
    .await
    .map_err(|error| format!("diagnostics outbox task failed: {error}"))?
}

#[tauri::command]
pub async fn acknowledge_error_outbox_events(
    app: tauri::AppHandle,
    ids: Vec<String>,
) -> Result<(), String> {
    let path = outbox_path(&app)?;
    tokio::task::spawn_blocking(move || {
        let _guard = OUTBOX_LOCK
            .lock()
            .map_err(|_| "diagnostics outbox lock was poisoned".to_string())?;
        acknowledge_at(&path, &ids)
    })
    .await
    .map_err(|error| format!("diagnostics outbox task failed: {error}"))?
}

#[cfg(test)]
mod tests {
    use super::*;

    fn event(id: &str, message: &str) -> ErrorOutboxEvent {
        ErrorOutboxEvent {
            id: id.into(),
            schema_version: 1,
            occurred_at: "2026-09-15T00:00:00.000Z".into(),
            level: "error".into(),
            source: "test".into(),
            message: message.into(),
            route: "/test".into(),
            window_label: "main".into(),
            user_id: Some("user-one".into()),
            organization_id: Some("org-one".into()),
            causal_signature: None,
        }
    }

    fn temp_path(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "matrx-local-error-outbox-{}-{}-{}.json",
            std::process::id(),
            name,
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ))
    }

    #[test]
    fn persists_and_acknowledges_only_named_events() {
        let path = temp_path("ack");
        enqueue_at(&path, event("one", "first")).unwrap();
        enqueue_at(&path, event("two", "second")).unwrap();

        assert_eq!(read_events(&path).unwrap().len(), 2);
        acknowledge_at(&path, &["one".into()]).unwrap();
        assert_eq!(read_events(&path).unwrap(), vec![event("two", "second")]);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn duplicate_ids_are_idempotent_and_non_errors_are_rejected() {
        let path = temp_path("dedupe");
        enqueue_at(&path, event("same", "first")).unwrap();
        enqueue_at(&path, event("same", "second")).unwrap();
        assert_eq!(read_events(&path).unwrap(), vec![event("same", "first")]);

        let mut info = event("info", "not durable");
        info.level = "info".into();
        assert!(enqueue_at(&path, info).is_err());
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn coalesces_only_same_causal_signature_source_and_identity() {
        let path = temp_path("causal-dedupe");
        let signature = "a".repeat(64);
        let mut first = event("one", "first");
        first.causal_signature = Some(signature.clone());
        enqueue_at(&path, first).unwrap();

        let mut same_cause = event("two", "retry");
        same_cause.causal_signature = Some(signature.clone());
        enqueue_at(&path, same_cause).unwrap();
        assert_eq!(read_events(&path).unwrap().len(), 1);

        let mut different_user = event("three", "other user");
        different_user.causal_signature = Some(signature.clone());
        different_user.user_id = Some("user-two".into());
        enqueue_at(&path, different_user).unwrap();

        let mut different_org = event("four", "other org");
        different_org.causal_signature = Some(signature.clone());
        different_org.organization_id = Some("org-two".into());
        enqueue_at(&path, different_org).unwrap();

        let mut different_source = event("five", "other source");
        different_source.causal_signature = Some(signature);
        different_source.source = "other".into();
        enqueue_at(&path, different_source).unwrap();

        assert_eq!(read_events(&path).unwrap().len(), 4);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn reads_old_records_without_a_causal_signature() {
        let path = temp_path("old-record");
        std::fs::write(
            &path,
            r#"[{"id":"old","schemaVersion":1,"occurredAt":"2026-09-15T00:00:00.000Z","level":"error","source":"test","message":"old","route":"/test","windowLabel":"main","userId":"user-one","organizationId":"org-one"}]"#,
        )
        .unwrap();

        let records = read_events(&path).unwrap();
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].causal_signature, None);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn rejects_private_or_malformed_causal_signatures() {
        let path = temp_path("invalid-causal-signature");
        let mut invalid = event("private", "must not persist");
        invalid.causal_signature = Some("token=private-value".into());
        assert!(enqueue_at(&path, invalid).is_err());
        assert!(!path.exists());
    }

    #[test]
    fn recovers_a_queue_left_in_the_windows_backup_position() {
        let path = temp_path("backup");
        enqueue_at(&path, event("one", "first")).unwrap();
        let backup = path.with_extension("json.bak");
        std::fs::rename(&path, &backup).unwrap();

        assert_eq!(read_events(&path).unwrap(), vec![event("one", "first")]);
        enqueue_at(&path, event("two", "second")).unwrap();
        assert_eq!(read_events(&path).unwrap().len(), 2);
        assert!(!backup.exists());
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn redacts_repeated_and_token_shaped_secrets_before_disk() {
        let path = temp_path("redaction");
        let secret_message = "TOKEN=first AWS_SECRET_ACCESS_KEY=second SUPABASE_SERVICE_ROLE_KEY=third passwd=passwd-value credential=credential-value code=oauth-value state=csrf-value sk-abcdefghijk eyJheader.payload.signature";
        enqueue_at(&path, event("secret", secret_message)).unwrap();

        let persisted = &read_events(&path).unwrap()[0].message;
        assert!(!persisted.contains("first"));
        assert!(!persisted.contains("second"));
        assert!(!persisted.contains("third"));
        assert!(!persisted.contains("passwd-value"));
        assert!(!persisted.contains("credential-value"));
        assert!(!persisted.contains("oauth-value"));
        assert!(!persisted.contains("csrf-value"));
        assert!(!persisted.contains("sk-abcdefghijk"));
        assert!(!persisted.contains("eyJheader.payload.signature"));
        assert_eq!(persisted.matches("[REDACTED]").count(), 9);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn native_events_are_identity_free_timestamped_and_redacted() {
        let path = temp_path("native");
        let event = native_event_at(
            "error",
            "native.startup",
            "installer failed token=top-secret".into(),
            1_789_430_400_123,
            7,
        );
        enqueue_at(&path, event).unwrap();

        let persisted = read_events(&path).unwrap().remove(0);
        assert_eq!(persisted.source, "native.startup");
        assert_eq!(persisted.occurred_at, "2026-09-15T00:00:00Z");
        assert_eq!(persisted.route, "native://desktop");
        assert_eq!(persisted.window_label, "native");
        assert_eq!(persisted.user_id, None);
        assert_eq!(persisted.organization_id, None);
        assert_eq!(persisted.message, "installer failed token=[REDACTED]");
        let _ = std::fs::remove_file(path);
    }
}
