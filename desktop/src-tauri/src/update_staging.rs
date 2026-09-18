//! Private, verified updater artifact staging.
//!
//! The updater plugin owns signature verification and platform installation.
//! This module only makes its verified bytes durable outside the application
//! bundle, so renderer state can never claim an update is ready by itself.

use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

const ARTIFACT_PREFIX: &str = "prepared-update-";
const FAILED_INSTALL_NOTICE: &str = "last-install-failure";

pub fn stage_verified_artifact(root: &Path, bytes: &[u8]) -> io::Result<PathBuf> {
    fs::create_dir_all(root)?;
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let final_path = root.join(format!("{ARTIFACT_PREFIX}{}-{nonce}.bin", std::process::id()));
    let temporary = root.join(format!(".{}", final_path.file_name().expect("artifact filename").to_string_lossy()));

    let result = (|| -> io::Result<()> {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        drop(file);
        fs::rename(&temporary, &final_path)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result.map(|()| final_path)
}

/// Remove abandoned stage files from an earlier app process. The current
/// prepared path is always preserved by the caller, so this never invalidates
/// the backend-owned update that can still be applied.
pub fn cleanup_abandoned_artifacts(root: &Path, preserve: Option<&Path>) -> io::Result<()> {
    let entries = match fs::read_dir(root) {
        Ok(entries) => entries,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    };
    for entry in entries {
        let path = entry?.path();
        let is_artifact = path.file_name().and_then(|name| name.to_str()).is_some_and(|name| {
            name.starts_with(ARTIFACT_PREFIX) && name.ends_with(".bin")
        });
        if is_artifact && preserve != Some(path.as_path()) {
            fs::remove_file(path)?;
        }
    }
    Ok(())
}

pub fn read_staged_artifact(path: &Path) -> io::Result<Vec<u8>> {
    let mut bytes = Vec::new();
    File::open(path)?.read_to_end(&mut bytes)?;
    if bytes.is_empty() {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "empty staged update"));
    }
    Ok(bytes)
}

pub fn record_install_failure(root: &Path, message: &str) -> io::Result<()> {
    fs::create_dir_all(root)?;
    fs::write(root.join(FAILED_INSTALL_NOTICE), message)
}

pub fn take_install_failure(root: &Path) -> io::Result<Option<String>> {
    let path = root.join(FAILED_INSTALL_NOTICE);
    match fs::read_to_string(&path) {
        Ok(message) => {
            fs::remove_file(path)?;
            Ok(Some(message))
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

#[cfg(target_os = "macos")]
pub fn macos_bundle_root(executable: &Path) -> Option<PathBuf> {
    let macos = executable.parent()?;
    if macos.file_name()?.to_str()? != "MacOS" {
        return None;
    }
    let contents = macos.parent()?;
    if contents.file_name()?.to_str()? != "Contents" {
        return None;
    }
    let bundle = contents.parent()?;
    (bundle.extension()?.to_str()? == "app").then(|| bundle.to_path_buf())
}

#[cfg(target_os = "macos")]
pub fn copy_macos_bundle(source: &Path, destination: &Path) -> io::Result<()> {
    let status = std::process::Command::new("ditto")
        .arg(source)
        .arg(destination)
        .status()?;
    if status.success() {
        Ok(())
    } else {
        Err(io::Error::other("ditto failed to copy application bundle"))
    }
}

#[cfg(test)]
mod tests {
    use super::{read_staged_artifact, stage_verified_artifact};
    use std::fs;

    #[test]
    fn verified_artifact_is_durable_and_replaces_only_the_staged_slot() {
        let root = std::env::temp_dir().join(format!("matrx-update-stage-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let path = stage_verified_artifact(&root, b"verified-one").expect("stage artifact");
        assert_eq!(read_staged_artifact(&path).expect("read artifact"), b"verified-one");
        let path = stage_verified_artifact(&root, b"verified-two").expect("replace staged artifact");
        assert_eq!(read_staged_artifact(&path).expect("read replacement"), b"verified-two");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn empty_artifact_is_not_a_prepared_update() {
        let path = std::env::temp_dir().join(format!("matrx-empty-update-{}", std::process::id()));
        fs::write(&path, []).expect("empty fixture");
        assert!(read_staged_artifact(&path).is_err());
        let _ = fs::remove_file(path);
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn bundle_root_requires_the_real_macos_bundle_layout() {
        use super::macos_bundle_root;
        use std::path::Path;
        assert_eq!(
            macos_bundle_root(Path::new("/Applications/AI Matrx.app/Contents/MacOS/aimatrx")),
            Some("/Applications/AI Matrx.app".into())
        );
        assert_eq!(macos_bundle_root(Path::new("/tmp/aimatrx")), None);
    }
}
