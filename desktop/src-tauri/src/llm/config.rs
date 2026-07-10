use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Debug, Serialize, Deserialize, Default)]
pub struct LlmConfig {
    pub selected_model: Option<String>,
    pub setup_complete: bool,
    pub last_port: Option<u16>,
    /// Context length (`-c`) from the last successful start. Used by auto-start
    /// so custom/large-context models are not silently clamped to 8192.
    #[serde(default)]
    pub last_context_length: Option<u32>,
    /// Legacy HuggingFace token fallback only. Prefer engine Settings → API Keys.
    pub hf_token: Option<String>,
}

impl LlmConfig {
    pub fn load(config_dir: &Path) -> Self {
        let path = config_dir.join("llm.json");
        std::fs::read_to_string(&path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default()
    }

    pub fn save(&self, config_dir: &Path) -> Result<(), String> {
        std::fs::create_dir_all(config_dir)
            .map_err(|e| format!("Failed to create config dir: {}", e))?;
        let path = config_dir.join("llm.json");
        let json = serde_json::to_string_pretty(self).map_err(|e| e.to_string())?;
        std::fs::write(path, json).map_err(|e| e.to_string())
    }
}
