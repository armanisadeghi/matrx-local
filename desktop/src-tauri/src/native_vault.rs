//! Non-secret native Vault coordination. Provider Keychain material never enters this module.
use serde::Serialize;

#[derive(Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TransitionResult { Applied, Unchanged, StateUnavailable, StateCorrupt, Busy, UnsupportedPlatform }

#[derive(Debug, Serialize)]
pub struct HistoricalStatus { pub state: &'static str, pub last_configured_subject: Option<String> }

#[cfg(target_os = "macos")]
mod platform {
    use super::*;
    use serde::{Deserialize, Serialize};
    use std::fs::{self, File, OpenOptions};
    use std::io::{Read, Write};
    use std::os::unix::fs::OpenOptionsExt;
    use std::os::unix::io::AsRawFd;
    use std::path::{Path, PathBuf};
    use std::time::{Duration, Instant};

    #[derive(Deserialize, Serialize, Clone)]
    struct State { version: u8, generation: u64, host_subject: Option<String>, provider_subject: Option<String> }
    fn subject(value: &str) -> bool { value.len() == 36 && value == value.to_ascii_lowercase() && value.as_bytes().iter().enumerate().all(|(i, c)| match i { 8|13|18|23 => *c == b'-', _ => c.is_ascii_hexdigit() }) }
    fn dir() -> Result<PathBuf, TransitionResult> {
        let home = std::env::var_os("HOME").ok_or(TransitionResult::StateUnavailable)?;
        let path = PathBuf::from(home).join("Library/Group Containers/group.com.aimatrx.desktop.vault-status/NativeVault");
        fs::create_dir_all(&path).map_err(|_| TransitionResult::StateUnavailable)?; Ok(path)
    }
    fn read(dir: &Path) -> Result<State, TransitionResult> {
        let path = dir.join("state.json");
        if !path.exists() { return Ok(State { version: 1, generation: 0, host_subject: None, provider_subject: None }); }
        let meta = fs::symlink_metadata(&path).map_err(|_| TransitionResult::StateUnavailable)?;
        if meta.file_type().is_symlink() || meta.len() > 2048 { return Err(TransitionResult::StateCorrupt); }
        let mut bytes = Vec::new(); File::open(path).map_err(|_| TransitionResult::StateUnavailable)?.take(2049).read_to_end(&mut bytes).map_err(|_| TransitionResult::StateUnavailable)?;
        let state: State = serde_json::from_slice(&bytes).map_err(|_| TransitionResult::StateCorrupt)?;
        if state.version != 1 || state.host_subject.as_deref().is_some_and(|v| !subject(v)) || state.provider_subject.as_deref().is_some_and(|v| !subject(v)) { return Err(TransitionResult::StateCorrupt); }
        Ok(state)
    }
    fn write(dir: &Path, value: &State) -> Result<(), TransitionResult> {
        let temp = dir.join(format!(".state-{}", std::process::id()));
        let bytes = serde_json::to_vec(value).map_err(|_| TransitionResult::StateCorrupt)?;
        let mut file = OpenOptions::new().write(true).create_new(true).mode(0o600).open(&temp).map_err(|_| TransitionResult::StateUnavailable)?;
        file.write_all(&bytes).and_then(|_| file.sync_all()).map_err(|_| TransitionResult::StateUnavailable)?;
        fs::rename(temp, dir.join("state.json")).map_err(|_| TransitionResult::StateUnavailable)?;
        File::open(dir).and_then(|f| f.sync_all()).map_err(|_| TransitionResult::StateUnavailable)
    }
    fn locked<T>(work: impl FnOnce(&Path, State) -> Result<T, TransitionResult>) -> Result<T, TransitionResult> {
        let dir = dir()?; let lock = OpenOptions::new().read(true).write(true).create(true).mode(0o600).custom_flags(libc::O_NOFOLLOW).open(dir.join("state.lock")).map_err(|_| TransitionResult::StateUnavailable)?;
        let deadline = Instant::now() + Duration::from_secs(2);
        while unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 { if Instant::now() >= deadline { return Err(TransitionResult::Busy); }; std::thread::sleep(Duration::from_millis(50)); }
        let answer = work(&dir, read(&dir)?); unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_UN); }; answer
    }
    pub fn reconcile(next: Option<String>, force: bool) -> TransitionResult {
        if next.as_deref().is_some_and(|v| !subject(v)) { return TransitionResult::StateCorrupt; }
        match locked(|dir, mut state| { if !force && state.host_subject == next { return Ok(TransitionResult::Unchanged); }; state.generation = state.generation.checked_add(1).ok_or(TransitionResult::StateCorrupt)?; state.host_subject = if force { None } else { next }; state.provider_subject = None; write(dir, &state)?; Ok(TransitionResult::Applied) }) { Ok(v) => v, Err(v) => v }
    }
    pub fn status() -> HistoricalStatus { match locked(|_, state| Ok(state)) { Ok(state) if state.provider_subject.is_some() => HistoricalStatus { state: "configured", last_configured_subject: state.provider_subject }, Ok(_) => HistoricalStatus { state: "uninitialized", last_configured_subject: None }, Err(TransitionResult::StateCorrupt) => HistoricalStatus { state: "state_corrupt", last_configured_subject: None }, Err(TransitionResult::Busy) => HistoricalStatus { state: "busy", last_configured_subject: None }, Err(_) => HistoricalStatus { state: "state_unavailable", last_configured_subject: None } } }
}
#[cfg(target_os = "macos")] pub fn reconcile(subject: Option<String>) -> TransitionResult { platform::reconcile(subject, false) }
#[cfg(target_os = "macos")] pub fn invalidate() -> TransitionResult { platform::reconcile(None, true) }
#[cfg(target_os = "macos")] pub fn status() -> HistoricalStatus { platform::status() }
#[cfg(not(target_os = "macos"))] pub fn reconcile(_: Option<String>) -> TransitionResult { TransitionResult::UnsupportedPlatform }
#[cfg(not(target_os = "macos"))] pub fn invalidate() -> TransitionResult { TransitionResult::UnsupportedPlatform }
#[cfg(not(target_os = "macos"))] pub fn status() -> HistoricalStatus { HistoricalStatus { state: "unsupported_platform", last_configured_subject: None } }
