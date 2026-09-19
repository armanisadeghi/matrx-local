//! Non-secret native Vault coordination. Provider Keychain material never enters this module.
use serde::Serialize;

#[derive(Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TransitionResult {
    Applied,
    Unchanged,
    StateUnavailable,
    StateCorrupt,
    Busy,
    UnsupportedPlatform,
}
#[derive(Debug, Serialize)]
pub struct HistoricalStatus {
    pub state: &'static str,
    pub last_configured_subject: Option<String>,
}

#[cfg(target_os = "macos")]
mod platform {
    use super::*;
    use objc2::rc::autoreleasepool;
    use objc2_foundation::{NSFileManager, NSString};
    use serde::{Deserialize, Deserializer, Serialize};
    use std::fs::File;
    use std::io::{Read, Write};
    use std::os::fd::{AsRawFd, FromRawFd};
    use std::time::{Duration, Instant};

    fn required_nullable<'de, D: Deserializer<'de>>(d: D) -> Result<Option<String>, D::Error> {
        Option::<String>::deserialize(d)
    }
    #[derive(Deserialize, Serialize, Clone)]
    #[serde(deny_unknown_fields)]
    struct Suggestions {
        #[serde(deserialize_with = "required_nullable")]
        organization_id: Option<String>,
        revision: String,
        status: String,
        refreshed_at_ms: Option<i64>,
        count: u16,
        unsupported_count: u16,
    }
    #[derive(Clone)]
    enum SuggestionField {
        Absent,
        Null,
        Value(Suggestions),
    }
    impl Default for SuggestionField {
        fn default() -> Self {
            Self::Absent
        }
    }
    impl SuggestionField {
        fn absent(&self) -> bool {
            matches!(self, Self::Absent)
        }
    }
    impl<'de> Deserialize<'de> for SuggestionField {
        fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
            Ok(match Option::<Suggestions>::deserialize(d)? {
                Some(value) => Self::Value(value),
                None => Self::Null,
            })
        }
    }
    impl Serialize for SuggestionField {
        fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
            match self {
                Self::Absent | Self::Null => serializer.serialize_none(),
                Self::Value(value) => value.serialize(serializer),
            }
        }
    }
    #[derive(Deserialize, Serialize, Clone)]
    #[serde(deny_unknown_fields)]
    struct State {
        version: u8,
        generation: String,
        #[serde(deserialize_with = "required_nullable")]
        host_subject: Option<String>,
        #[serde(deserialize_with = "required_nullable")]
        provider_subject: Option<String>,
        #[serde(default, skip_serializing_if = "SuggestionField::absent")]
        suggestions: SuggestionField,
    }
    fn empty_suggestions() -> Suggestions {
        Suggestions {
            organization_id: None,
            revision: generation(),
            status: "empty".into(),
            refreshed_at_ms: None,
            count: 0,
            unsupported_count: 0,
        }
    }
    fn empty_state() -> State {
        State {
            version: 2,
            generation: generation(),
            host_subject: None,
            provider_subject: None,
            suggestions: SuggestionField::Value(empty_suggestions()),
        }
    }
    fn subject(value: &str) -> bool {
        value.len() == 36
            && value == value.to_ascii_lowercase()
            && value.as_bytes().iter().enumerate().all(|(i, c)| match i {
                8 | 13 | 18 | 23 => *c == b'-',
                _ => c.is_ascii_hexdigit(),
            })
    }
    fn generation() -> String {
        format!(
            "{:08x}-{:04x}-{:04x}-{:04x}-{:012x}",
            rand_word() as u32,
            (rand_word() >> 16) as u16,
            ((rand_word() as u16) & 0x0fff) | 0x4000,
            ((rand_word() as u16) & 0x3fff) | 0x8000,
            rand_word() & 0x0000_ffff_ffff_ffff
        )
    }
    fn rand_word() -> u64 {
        unsafe { ((libc::arc4random() as u64) << 32) | libc::arc4random() as u64 }
    }
    fn c(value: &str) -> std::ffi::CString {
        std::ffi::CString::new(value).unwrap()
    }
    fn valid(fd: i32, kind: libc::mode_t, mode: libc::mode_t) -> bool {
        let mut s: libc::stat = unsafe { std::mem::zeroed() };
        unsafe {
            libc::fstat(fd, &mut s) == 0
                && s.st_uid == libc::geteuid()
                && s.st_mode & libc::S_IFMT == kind
                && s.st_mode & 0o7777 == mode
        }
    }
    /// The first native-Vault release created this directory through
    /// `create_dir_all`, so the process umask left existing installs at 0755.
    /// Tighten only that exact, owned predecessor through the already-opened
    /// no-follow descriptor. Every other mismatch remains fail-closed.
    fn ensure_private_dir(fd: i32) -> bool {
        if valid(fd, libc::S_IFDIR, 0o700) {
            return true;
        }
        if !valid(fd, libc::S_IFDIR, 0o755) {
            return false;
        }
        unsafe {
            libc::fchmod(fd, 0o700) == 0 && libc::fsync(fd) == 0 && valid(fd, libc::S_IFDIR, 0o700)
        }
    }
    fn owned_dir(fd: i32) -> bool {
        let mut s: libc::stat = unsafe { std::mem::zeroed() };
        unsafe {
            libc::fstat(fd, &mut s) == 0
                && s.st_uid == libc::geteuid()
                && s.st_mode & libc::S_IFMT == libc::S_IFDIR
        }
    }
    fn dir() -> Result<File, TransitionResult> {
        let root = autoreleasepool(|pool| {
            let identifier = NSString::from_str("group.com.aimatrx.desktop.vault-status");
            let url = NSFileManager::defaultManager()
                .containerURLForSecurityApplicationGroupIdentifier(&identifier)?;
            let path = url.path()?;
            Some(unsafe { path.to_str(pool) }.to_owned())
        })
        .ok_or(TransitionResult::StateUnavailable)?;
        dir_at(&root)
    }
    fn dir_at(root: &str) -> Result<File, TransitionResult> {
        let root = c(&root);
        let root_fd = unsafe {
            libc::open(
                root.as_ptr(),
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW,
            )
        };
        if root_fd < 0 || !owned_dir(root_fd) {
            if root_fd >= 0 {
                unsafe {
                    libc::close(root_fd);
                }
            }
            return Err(TransitionResult::StateUnavailable);
        }
        let name = c("NativeVault");
        if unsafe { libc::mkdirat(root_fd, name.as_ptr(), 0o700) } != 0
            && std::io::Error::last_os_error().raw_os_error() != Some(libc::EEXIST)
        {
            unsafe {
                libc::close(root_fd);
            }
            return Err(TransitionResult::StateUnavailable);
        }
        let fd = unsafe {
            libc::openat(
                root_fd,
                name.as_ptr(),
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW,
            )
        };
        unsafe {
            libc::close(root_fd);
        }
        if fd < 0 || !ensure_private_dir(fd) {
            if fd >= 0 {
                unsafe {
                    libc::close(fd);
                }
            }
            return Err(TransitionResult::StateUnavailable);
        }
        Ok(unsafe { File::from_raw_fd(fd) })
    }
    fn existing_dir_at(root_path: &str) -> Result<Option<File>, TransitionResult> {
        let root = c(root_path);
        let root_fd = unsafe {
            libc::open(
                root.as_ptr(),
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW,
            )
        };
        if root_fd < 0 || !owned_dir(root_fd) {
            if root_fd >= 0 {
                unsafe {
                    libc::close(root_fd);
                }
            }
            return Err(TransitionResult::StateUnavailable);
        }
        let name = c("NativeVault");
        let fd = unsafe {
            libc::openat(
                root_fd,
                name.as_ptr(),
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW,
            )
        };
        unsafe {
            libc::close(root_fd);
        }
        if fd < 0 {
            return match std::io::Error::last_os_error().raw_os_error() {
                Some(libc::ENOENT) => Ok(None),
                Some(libc::ELOOP) | Some(libc::ENOTDIR) => Err(TransitionResult::StateCorrupt),
                _ => Err(TransitionResult::StateUnavailable),
            };
        }
        if !valid(fd, libc::S_IFDIR, 0o700) {
            unsafe {
                libc::close(fd);
            }
            return Err(TransitionResult::StateCorrupt);
        }
        Ok(Some(unsafe { File::from_raw_fd(fd) }))
    }
    fn existing_dir() -> Result<Option<File>, TransitionResult> {
        let root = autoreleasepool(|pool| {
            let identifier = NSString::from_str("group.com.aimatrx.desktop.vault-status");
            let url = NSFileManager::defaultManager()
                .containerURLForSecurityApplicationGroupIdentifier(&identifier)?;
            let path = url.path()?;
            Some(unsafe { path.to_str(pool) }.to_owned())
        })
        .ok_or(TransitionResult::StateUnavailable)?;
        existing_dir_at(&root)
    }
    fn read(dir: &File) -> Result<State, TransitionResult> {
        let name = c("state.json");
        let fd = unsafe {
            libc::openat(
                dir.as_raw_fd(),
                name.as_ptr(),
                libc::O_RDONLY | libc::O_NOFOLLOW,
            )
        };
        if fd < 0 {
            return match std::io::Error::last_os_error().raw_os_error() {
                Some(libc::ENOENT) => Ok(empty_state()),
                Some(libc::ELOOP) => Err(TransitionResult::StateCorrupt),
                _ => Err(TransitionResult::StateUnavailable),
            };
        }
        if !valid(fd, libc::S_IFREG, 0o600) {
            unsafe {
                libc::close(fd);
            }
            return Err(TransitionResult::StateCorrupt);
        }
        let mut bytes = Vec::new();
        let file = unsafe { File::from_raw_fd(fd) };
        file.take(2049)
            .read_to_end(&mut bytes)
            .map_err(|_| TransitionResult::StateUnavailable)?;
        if bytes.len() > 2048 {
            return Err(TransitionResult::StateCorrupt);
        }
        decode(&bytes)
    }
    fn decode(bytes: &[u8]) -> Result<State, TransitionResult> {
        let state: State =
            serde_json::from_slice(bytes).map_err(|_| TransitionResult::StateCorrupt)?;
        if !(state.version == 1 || state.version == 2)
            || !subject(&state.generation)
            || state.host_subject.as_deref().is_some_and(|v| !subject(v))
            || state
                .provider_subject
                .as_deref()
                .is_some_and(|v| !subject(v))
        {
            return Err(TransitionResult::StateCorrupt);
        }
        match (&state.suggestions, state.version) {
            (SuggestionField::Absent, 1) => {}
            (SuggestionField::Value(value), 2)
                if subject(&value.revision)
                    && value
                        .organization_id
                        .as_deref()
                        .map(subject)
                        .unwrap_or(true)
                    && matches!(
                        value.status.as_str(),
                        "empty" | "ready" | "stale" | "failed"
                    )
                    && value.count <= 2000
                    && value.unsupported_count <= 2000
                    && value.refreshed_at_ms.map(|v| v > 0).unwrap_or(true)
                    && (value.status != "empty"
                        || (value.organization_id.is_none()
                            && value.refreshed_at_ms.is_none()
                            && value.count == 0
                            && value.unsupported_count == 0))
                    && (value.status != "ready"
                        || (value.organization_id.is_some()
                            && value.refreshed_at_ms.is_some())) => {}
            _ => return Err(TransitionResult::StateCorrupt),
        }
        Ok(state)
    }
    fn write(dir: &File, value: &State) -> Result<(), TransitionResult> {
        let temp = c(&format!(".state-{}-{}", std::process::id(), generation()));
        let bytes = serde_json::to_vec(value).map_err(|_| TransitionResult::StateCorrupt)?;
        if bytes.len() > 2048 {
            return Err(TransitionResult::StateCorrupt);
        }
        let fd = unsafe {
            libc::openat(
                dir.as_raw_fd(),
                temp.as_ptr(),
                libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL | libc::O_NOFOLLOW,
                0o600,
            )
        };
        if fd < 0 || !valid(fd, libc::S_IFREG, 0o600) {
            if fd >= 0 {
                unsafe {
                    libc::close(fd);
                }
            }
            return Err(TransitionResult::StateUnavailable);
        }
        let mut file = unsafe { File::from_raw_fd(fd) };
        let result = file
            .write_all(&bytes)
            .and_then(|_| file.sync_all())
            .map_err(|_| TransitionResult::StateUnavailable);
        if result.is_err() {
            unsafe {
                libc::unlinkat(dir.as_raw_fd(), temp.as_ptr(), 0);
            }
            return result;
        }
        let final_name = c("state.json");
        if unsafe {
            libc::renameat(
                dir.as_raw_fd(),
                temp.as_ptr(),
                dir.as_raw_fd(),
                final_name.as_ptr(),
            )
        } != 0
            || unsafe { libc::fsync(dir.as_raw_fd()) } != 0
        {
            unsafe {
                libc::unlinkat(dir.as_raw_fd(), temp.as_ptr(), 0);
            }
            return Err(TransitionResult::StateUnavailable);
        }
        Ok(())
    }
    fn locked_at<T>(
        dir: File,
        work: impl FnOnce(&File, State) -> Result<T, TransitionResult>,
    ) -> Result<T, TransitionResult> {
        let name = c("state.lock");
        let fd = unsafe {
            libc::openat(
                dir.as_raw_fd(),
                name.as_ptr(),
                libc::O_RDWR | libc::O_CREAT | libc::O_NOFOLLOW,
                0o600,
            )
        };
        if fd < 0 || !valid(fd, libc::S_IFREG, 0o600) {
            if fd >= 0 {
                unsafe {
                    libc::close(fd);
                }
            }
            return Err(TransitionResult::StateUnavailable);
        };
        let lock = unsafe { File::from_raw_fd(fd) };
        let deadline = Instant::now() + Duration::from_secs(2);
        while unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            if Instant::now() >= deadline {
                return Err(TransitionResult::Busy);
            };
            std::thread::sleep(Duration::from_millis(50));
        }
        let answer = work(&dir, read(&dir)?);
        unsafe {
            libc::flock(lock.as_raw_fd(), libc::LOCK_UN);
        };
        answer
    }
    fn locked<T>(
        work: impl FnOnce(&File, State) -> Result<T, TransitionResult>,
    ) -> Result<T, TransitionResult> {
        locked_at(dir()?, work)
    }
    /// Status is read-only: it never creates a lock inode.  If a transition is
    /// holding the existing lock, expose the closed `busy` status rather than
    /// reporting stale configured metadata.
    fn status_lock(dir: &File) -> Result<(), TransitionResult> {
        let name = c("state.lock");
        let fd = unsafe {
            libc::openat(
                dir.as_raw_fd(),
                name.as_ptr(),
                libc::O_RDWR | libc::O_NOFOLLOW,
            )
        };
        if fd < 0 {
            return match std::io::Error::last_os_error().raw_os_error() {
                Some(libc::ENOENT) => Ok(()),
                Some(libc::ELOOP) => Err(TransitionResult::StateCorrupt),
                _ => Err(TransitionResult::StateUnavailable),
            };
        }
        if !valid(fd, libc::S_IFREG, 0o600) {
            unsafe { libc::close(fd) };
            return Err(TransitionResult::StateCorrupt);
        }
        let lock = unsafe { File::from_raw_fd(fd) };
        if unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            return if std::io::Error::last_os_error().raw_os_error() == Some(libc::EWOULDBLOCK) {
                Err(TransitionResult::Busy)
            } else {
                Err(TransitionResult::StateUnavailable)
            };
        }
        unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_UN) };
        Ok(())
    }
    pub fn reconcile(next: Option<String>, force: bool) -> TransitionResult {
        if next.as_deref().is_some_and(|v| !subject(v)) {
            return TransitionResult::StateCorrupt;
        }
        match locked(|dir, mut state| {
            if !force && state.host_subject == next {
                return Ok(TransitionResult::Unchanged);
            };
            state.version = 2;
            state.generation = generation();
            state.host_subject = if force { None } else { next };
            state.provider_subject = None;
            state.suggestions = SuggestionField::Value(empty_suggestions());
            write(dir, &state)?;
            Ok(TransitionResult::Applied)
        }) {
            Ok(v) => v,
            Err(v) => v,
        }
    }
    pub fn status() -> HistoricalStatus {
        match existing_dir().and_then(|dir| match dir {
            None => Ok(empty_state()),
            Some(dir) => {
                status_lock(&dir)?;
                read(&dir)
            }
        }) {
            Ok(state) if state.provider_subject.is_some() => HistoricalStatus {
                state: "configured",
                last_configured_subject: state.provider_subject,
            },
            Ok(_) => HistoricalStatus {
                state: "uninitialized",
                last_configured_subject: None,
            },
            Err(TransitionResult::StateCorrupt) => HistoricalStatus {
                state: "state_corrupt",
                last_configured_subject: None,
            },
            Err(TransitionResult::Busy) => HistoricalStatus {
                state: "busy",
                last_configured_subject: None,
            },
            Err(_) => HistoricalStatus {
                state: "state_unavailable",
                last_configured_subject: None,
            },
        }
    }
    fn status_at(root: &str) -> HistoricalStatus {
        match existing_dir_at(root).and_then(|dir| match dir {
            None => Ok(empty_state()),
            Some(dir) => {
                status_lock(&dir)?;
                read(&dir)
            }
        }) {
            Ok(state) if state.provider_subject.is_some() => HistoricalStatus {
                state: "configured",
                last_configured_subject: state.provider_subject,
            },
            Ok(_) => HistoricalStatus {
                state: "uninitialized",
                last_configured_subject: None,
            },
            Err(TransitionResult::StateCorrupt) => HistoricalStatus {
                state: "state_corrupt",
                last_configured_subject: None,
            },
            Err(TransitionResult::Busy) => HistoricalStatus {
                state: "busy",
                last_configured_subject: None,
            },
            Err(_) => HistoricalStatus {
                state: "state_unavailable",
                last_configured_subject: None,
            },
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use std::process::Command;
        fn state() -> String {
            r#"{"version":1,"generation":"11111111-1111-4111-8111-111111111111","host_subject":null,"provider_subject":null}"#.into()
        }
        fn temp() -> (std::path::PathBuf, File) {
            let mut p = std::env::temp_dir();
            p.push(format!("native-vault-{}", generation()));
            std::fs::create_dir(&p).unwrap();
            std::fs::set_permissions(&p, std::os::unix::fs::PermissionsExt::from_mode(0o700))
                .unwrap();
            let f = File::open(&p).unwrap();
            (p, f)
        }
        #[test]
        fn closed_state_refuses_missing_unknown_duplicate_and_corrupt_fields() {
            assert!(decode(state().as_bytes()).is_ok());
            for invalid in [
                r#"{"version":1,"generation":"11111111-1111-4111-8111-111111111111","host_subject":null}"#,
                r#"{"version":1,"generation":"11111111-1111-4111-8111-111111111111","host_subject":null,"provider_subject":null,"x":1}"#,
                r#"{"version":1,"version":1,"generation":"11111111-1111-4111-8111-111111111111","host_subject":null,"provider_subject":null}"#,
                r#"{"version":1,"generation":"upper","host_subject":null,"provider_subject":null}"#,
                r#"{"version":1,"generation":"11111111-1111-4111-8111-111111111111","host_subject":null,"provider_subject":null,"suggestions":null}"#,
            ] {
                assert!(matches!(
                    decode(invalid.as_bytes()),
                    Err(TransitionResult::StateCorrupt)
                ));
            }
        }
        #[test]
        fn generated_generations_are_canonical_and_round_trip() {
            for _ in 0..4096 {
                let generation = generation();
                assert!(subject(&generation));
                let encoded = format!(
                    r#"{{"version":1,"generation":"{generation}","host_subject":null,"provider_subject":null}}"#
                );
                assert!(decode(encoded.as_bytes()).is_ok());
            }
        }
        #[test]
        fn state_reader_refuses_symlink_and_accepts_atomic_written_state() {
            let (path, dir) = temp();
            assert!(read(&dir).is_ok());
            let link = path.join("state.json");
            std::os::unix::fs::symlink("/tmp", &link).unwrap();
            assert!(matches!(read(&dir), Err(TransitionResult::StateCorrupt)));
            std::fs::remove_file(&link).unwrap();
            let value = decode(state().as_bytes()).unwrap();
            write(&dir, &value).unwrap();
            assert_eq!(read(&dir).unwrap().generation, value.generation);
            std::fs::remove_dir_all(path).unwrap();
        }
        #[test]
        fn readonly_status_never_creates_missing_entries_and_refuses_corrupt_state() {
            let (root, _) = temp();
            let before = std::fs::read_dir(&root).unwrap().count();
            let root_text = root.to_str().unwrap();
            assert_eq!(status_at(root_text).state, "uninitialized");
            assert_eq!(std::fs::read_dir(&root).unwrap().count(), before);
            let vault = root.join("NativeVault");
            std::os::unix::fs::symlink("/tmp", &vault).unwrap();
            assert_eq!(status_at(root_text).state, "state_corrupt");
            std::fs::remove_file(&vault).unwrap();
            std::fs::create_dir(&vault).unwrap();
            std::fs::set_permissions(&vault, std::os::unix::fs::PermissionsExt::from_mode(0o700))
                .unwrap();
            assert_eq!(status_at(root_text).state, "uninitialized");
            assert_eq!(std::fs::read_dir(&vault).unwrap().count(), 0);
            std::fs::write(vault.join("state.json"), b"{").unwrap();
            std::fs::set_permissions(
                vault.join("state.json"),
                std::os::unix::fs::PermissionsExt::from_mode(0o600),
            )
            .unwrap();
            assert_eq!(status_at(root_text).state, "state_corrupt");
            std::fs::remove_dir_all(root).unwrap();
        }
        #[test]
        fn mutating_path_repairs_only_the_owned_legacy_directory_mode() {
            use std::os::unix::fs::{MetadataExt, PermissionsExt};

            let (path, _) = temp();
            let vault = path.join("NativeVault");
            std::fs::create_dir(&vault).unwrap();
            std::fs::set_permissions(&vault, PermissionsExt::from_mode(0o755)).unwrap();
            let dir = dir_at(path.to_str().unwrap()).unwrap();
            assert_eq!(std::fs::metadata(&vault).unwrap().mode() & 0o7777, 0o700);
            assert!(
                ensure_private_dir(dir.as_raw_fd()),
                "the migrated mode stays valid"
            );

            std::fs::set_permissions(&vault, PermissionsExt::from_mode(0o750)).unwrap();
            assert!(!ensure_private_dir(dir.as_raw_fd()));
            assert_eq!(std::fs::metadata(&vault).unwrap().mode() & 0o7777, 0o750);

            std::fs::set_permissions(&vault, PermissionsExt::from_mode(0o4755)).unwrap();
            assert!(matches!(
                dir_at(path.to_str().unwrap()),
                Err(TransitionResult::StateUnavailable)
            ));
            assert_eq!(std::fs::metadata(&vault).unwrap().mode() & 0o7777, 0o4755);
            std::fs::remove_dir_all(path).unwrap();
        }
        #[test]
        fn mutating_path_refuses_a_legacy_directory_symlink() {
            let (path, _) = temp();
            let target = path.join("target");
            std::fs::create_dir(&target).unwrap();
            std::os::unix::fs::symlink(&target, path.join("NativeVault")).unwrap();

            assert!(matches!(
                dir_at(path.to_str().unwrap()),
                Err(TransitionResult::StateUnavailable)
            ));
            assert!(std::fs::symlink_metadata(path.join("NativeVault"))
                .unwrap()
                .file_type()
                .is_symlink());
            std::fs::remove_dir_all(path).unwrap();
        }
        #[test]
        fn flock_worker() {
            if let Some(path) = std::env::var_os("NATIVE_VAULT_STATUS_TEST_DIR") {
                println!("{}", status_at(&path.to_string_lossy()).state);
                return;
            }
            let Some(path) = std::env::var_os("NATIVE_VAULT_LOCK_TEST_DIR") else {
                return;
            };
            let dir = File::open(path).unwrap();
            let result = locked_at(dir, |dir, mut value| {
                value.generation = generation();
                write(dir, &value)?;
                Ok(TransitionResult::Applied)
            });
            println!("{result:?}");
        }
        #[test]
        fn two_process_lock_timeout_then_generation_write_are_serialized() {
            let (path, _) = temp();
            let vault = path.join("NativeVault");
            std::fs::create_dir(&vault).unwrap();
            std::fs::set_permissions(&vault, std::os::unix::fs::PermissionsExt::from_mode(0o700))
                .unwrap();
            let dir = File::open(&vault).unwrap();
            let value = decode(state().as_bytes()).unwrap();
            write(&dir, &value).unwrap();
            let child = || {
                Command::new(std::env::current_exe().unwrap())
                    .arg("--exact")
                    .arg("native_vault::platform::tests::flock_worker")
                    .arg("--nocapture")
                    .env("NATIVE_VAULT_LOCK_TEST_DIR", &vault)
                    .output()
                    .unwrap()
            };
            locked_at(dir, |_, _| {
                let status = Command::new(std::env::current_exe().unwrap())
                    .arg("--exact")
                    .arg("native_vault::platform::tests::flock_worker")
                    .arg("--nocapture")
                    .env("NATIVE_VAULT_STATUS_TEST_DIR", &path)
                    .output()
                    .unwrap();
                assert!(status.status.success());
                assert!(String::from_utf8_lossy(&status.stdout).contains("busy"));
                let out = child();
                assert!(out.status.success());
                assert!(String::from_utf8_lossy(&out.stdout).contains("Busy"));
                Ok(())
            })
            .unwrap();
            let out = child();
            assert!(out.status.success());
            assert!(String::from_utf8_lossy(&out.stdout).contains("Applied"));
            let after = read(&File::open(&vault).unwrap()).unwrap();
            assert_ne!(after.generation, value.generation);
            std::fs::remove_dir_all(path).unwrap();
        }
    }
}
#[cfg(target_os = "macos")]
pub fn reconcile(subject: Option<String>) -> TransitionResult {
    platform::reconcile(subject, false)
}
#[cfg(target_os = "macos")]
pub fn invalidate() -> TransitionResult {
    platform::reconcile(None, true)
}
#[cfg(target_os = "macos")]
pub fn status() -> HistoricalStatus {
    platform::status()
}
#[cfg(not(target_os = "macos"))]
pub fn reconcile(_: Option<String>) -> TransitionResult {
    TransitionResult::UnsupportedPlatform
}
#[cfg(not(target_os = "macos"))]
pub fn invalidate() -> TransitionResult {
    TransitionResult::UnsupportedPlatform
}
#[cfg(not(target_os = "macos"))]
pub fn status() -> HistoricalStatus {
    HistoricalStatus {
        state: "unsupported_platform",
        last_configured_subject: None,
    }
}
