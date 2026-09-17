//! Parsing for the desktop payload version embedded in `matrx-syncd` at build time.

/// Read the desktop app version from its canonical Tauri configuration.
///
/// The daemon crate has an independent workspace version, so it cannot identify the app payload
/// that bundled it.  The updater's daemon-upgrade handshake instead compares this value with the
/// running daemon's reported version.
pub fn app_version_from_tauri_config(source: &str) -> Result<String, String> {
    let config: serde_json::Value = serde_json::from_str(source)
        .map_err(|error| format!("desktop/src-tauri/tauri.conf.json is not valid JSON: {error}"))?;
    let version = config
        .get("version")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| {
            "desktop/src-tauri/tauri.conf.json must contain a string top-level `version`"
                .to_string()
        })?;

    if version.trim().is_empty() || version.trim() != version {
        return Err(
            "desktop/src-tauri/tauri.conf.json has an empty or whitespace-padded `version`"
                .to_string(),
        );
    }

    Ok(version.to_string())
}

#[cfg(test)]
mod tests {
    use super::app_version_from_tauri_config;

    #[test]
    fn reads_the_canonical_tauri_app_version() {
        assert_eq!(
            app_version_from_tauri_config(r#"{"productName":"AI Matrx","version":"1.4.141"}"#),
            Ok("1.4.141".to_string())
        );
    }

    #[test]
    fn rejects_missing_non_string_empty_and_malformed_versions() {
        for source in [
            r#"{"productName":"AI Matrx"}"#,
            r#"{"version":141}"#,
            r#"{"version":""}"#,
            r#"{"version":" 1.4.141"}"#,
            r#"{"version":"1.4.141""#,
        ] {
            assert!(app_version_from_tauri_config(source).is_err(), "{source}");
        }
    }
}
