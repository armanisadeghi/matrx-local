use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Debug, Serialize, Deserialize)]
pub struct TranscriptionConfig {
    pub selected_model: Option<String>,
    pub setup_complete: bool,
    #[serde(default = "default_wake_keyword")]
    pub wake_keyword: String,
    /// Bound for native microphone enumeration/configuration/open. This is a
    /// user-owned device setting rather than a compiled behavioral constant.
    #[serde(default = "default_capture_startup_timeout_ms")]
    pub capture_startup_timeout_ms: u64,
}

fn default_wake_keyword() -> String {
    "hey matrix".to_string()
}

fn default_capture_startup_timeout_ms() -> u64 {
    15_000
}

impl Default for TranscriptionConfig {
    fn default() -> Self {
        Self {
            selected_model: None,
            setup_complete: false,
            wake_keyword: default_wake_keyword(),
            capture_startup_timeout_ms: default_capture_startup_timeout_ms(),
        }
    }
}

impl TranscriptionConfig {
    pub fn capture_startup_timeout(&self) -> std::time::Duration {
        std::time::Duration::from_millis(self.capture_startup_timeout_ms.max(1))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn older_config_gets_the_startup_timeout_default() {
        let config: TranscriptionConfig = serde_json::from_str(
            r#"{"selected_model":null,"setup_complete":false,"wake_keyword":"hey matrix"}"#,
        )
        .unwrap();
        assert_eq!(config.capture_startup_timeout_ms, 15_000);
    }

    #[test]
    fn capture_startup_timeout_is_a_persisted_device_knob() {
        let config: TranscriptionConfig = serde_json::from_str(
            r#"{"selected_model":null,"setup_complete":false,"wake_keyword":"hey matrix","capture_startup_timeout_ms":2750}"#,
        )
        .unwrap();
        assert_eq!(
            config.capture_startup_timeout(),
            std::time::Duration::from_millis(2_750)
        );
    }
}

impl TranscriptionConfig {
    pub fn load(config_dir: &Path) -> Self {
        let path = config_dir.join("transcription.json");
        std::fs::read_to_string(&path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default()
    }

    pub fn save(&self, config_dir: &Path) -> Result<(), String> {
        std::fs::create_dir_all(config_dir)
            .map_err(|e| format!("Failed to create config dir: {}", e))?;
        let path = config_dir.join("transcription.json");
        let json = serde_json::to_string_pretty(self).map_err(|e| e.to_string())?;
        std::fs::write(path, json).map_err(|e| e.to_string())
    }
}
