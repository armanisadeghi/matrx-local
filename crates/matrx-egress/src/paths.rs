//! `<home>/egress/` — the helper's own corner of the world's home directory.
//!
//! | File | What it is |
//! |---|---|
//! | `<home>/egress/status.json` | the status JSON, rewritten on every change and every 5 s |
//! | `<home>/egress/control.json` | the running standalone instance's loopback port and token |
//!
//! The token never appears in either file: `control.json` holds the CONTROL token for the local
//! control listener, which is minted fresh at every start and deleted at a clean stop — the same
//! rule `matrx-syncd` applies to `syncd.token`, for the same reason (a file that outlives the
//! process that minted it authorises nothing, so leaving it behind only puts a live-looking
//! credential on disk).

use crate::world::World;
use std::io;
use std::path::{Path, PathBuf};

/// Every path the helper owns.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Paths {
    /// Which world these belong to.
    pub world: World,
    /// `~/.matrx` or `~/.matrx-dev` (or `$MATRX_HOME_DIR`).
    pub home: PathBuf,
    /// `<home>/egress`.
    pub dir: PathBuf,
    /// `<home>/egress/status.json`.
    pub status: PathBuf,
    /// `<home>/egress/control.json`.
    pub control: PathBuf,
}

impl Paths {
    /// Resolve this world's paths. Creates nothing.
    pub fn resolve(world: World) -> io::Result<Self> {
        let home = world.home()?;
        let dir = home.join("egress");
        Ok(Paths {
            world,
            status: dir.join("status.json"),
            control: dir.join("control.json"),
            dir,
            home,
        })
    }

    /// Create `<home>/egress`, owner-only on Unix.
    pub fn create_dirs(&self) -> io::Result<()> {
        std::fs::create_dir_all(&self.dir)?;
        set_dir_mode_0700(&self.dir)
    }
}

#[cfg(unix)]
fn set_dir_mode_0700(dir: &Path) -> io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700))
}

#[cfg(not(unix))]
fn set_dir_mode_0700(_dir: &Path) -> io::Result<()> {
    // `%LOCALAPPDATA%` is already ACL'd to this user and SYSTEM and a directory created there
    // inherits it. There is no mode bit; pretending to set one would be a false sentence.
    Ok(())
}

/// Write a file atomically: a temporary beside it, then a rename.
///
/// Atomic because the status file has a reader — the desktop engine's supervisor tails it — and a
/// reader must never see half a JSON document. `private` also makes it owner-only on Unix before
/// it holds anything, which is what `control.json` needs.
pub fn write_atomic(path: &Path, contents: &str, private: bool) -> io::Result<()> {
    let parent = path.parent().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{} has no parent directory", path.display()),
        )
    })?;
    std::fs::create_dir_all(parent)?;
    let temp = parent.join(format!(
        ".{}.{}.tmp",
        path.file_name().and_then(|n| n.to_str()).unwrap_or("egress"),
        std::process::id()
    ));
    {
        use std::io::Write as _;
        let mut file = if private {
            create_private(&temp)?
        } else {
            std::fs::File::create(&temp)?
        };
        file.write_all(contents.as_bytes())?;
        file.sync_all()?;
    }
    match std::fs::rename(&temp, path) {
        Ok(()) => Ok(()),
        Err(e) => {
            let _ = std::fs::remove_file(&temp);
            Err(e)
        }
    }
}

#[cfg(unix)]
fn create_private(path: &Path) -> io::Result<std::fs::File> {
    use std::os::unix::fs::OpenOptionsExt;
    std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(path)
}

#[cfg(not(unix))]
fn create_private(path: &Path) -> io::Result<std::fs::File> {
    std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(path)
}

/// Remove a file, treating "already gone" as success.
pub fn remove_quietly(path: &Path) -> io::Result<()> {
    match std::fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(e),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_two_worlds_never_share_a_file() {
        let previous = std::env::var_os("MATRX_HOME_DIR");
        std::env::remove_var("MATRX_HOME_DIR");
        let live = Paths::resolve(World::Live).expect("live");
        let dev = Paths::resolve(World::Dev).expect("dev");
        if let Some(p) = previous {
            std::env::set_var("MATRX_HOME_DIR", p);
        }
        assert_ne!(live.status, dev.status);
        assert_ne!(live.control, dev.control);
        assert!(live.status.ends_with("egress/status.json"));
        // Never the sync daemon's files, and never the engine's.
        for path in [&live.status, &live.control, &dev.status, &dev.control] {
            let text = path.display().to_string();
            assert!(!text.contains("syncd"), "{text}");
            assert!(!text.ends_with("local.json"), "{text}");
        }
    }

    #[test]
    fn a_private_write_is_atomic_and_owner_only_and_leaves_no_temporary() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("control.json");
        write_atomic(&path, "{\"token\":\"x\"}", true).expect("write");
        assert_eq!(
            std::fs::read_to_string(&path).expect("read"),
            "{\"token\":\"x\"}"
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&path).expect("stat").permissions().mode();
            assert_eq!(mode & 0o777, 0o600, "mode is {:o}", mode & 0o777);
        }
        // A second write replaces it in place, still atomically.
        write_atomic(&path, "{\"token\":\"y\"}", true).expect("rewrite");
        assert_eq!(
            std::fs::read_to_string(&path).expect("read"),
            "{\"token\":\"y\"}"
        );
        let leftovers: Vec<_> = std::fs::read_dir(dir.path())
            .expect("read_dir")
            .filter_map(|e| e.ok())
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .filter(|n| n.ends_with(".tmp"))
            .collect();
        assert!(leftovers.is_empty(), "left {leftovers:?}");
    }

    #[test]
    fn write_atomic_creates_the_directory_it_needs() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("egress").join("status.json");
        write_atomic(&path, "{}", false).expect("write");
        assert!(path.exists());
    }

    #[test]
    fn removing_a_file_that_is_already_gone_is_success() {
        let dir = tempfile::tempdir().expect("tempdir");
        remove_quietly(&dir.path().join("never-existed.json")).expect("remove");
    }
}
