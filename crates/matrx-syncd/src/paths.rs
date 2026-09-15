//! Per-user, per-world paths (D18, SPEC-ENGINE §1.9, matrx-local Hard Rule 9).
//!
//! **The library composes no `~/.matrx` path — this module is where the daemon does it**, once,
//! and every other part of the daemon takes the result as a value.
//!
//! | Facility | macOS / Linux | Windows |
//! |---|---|---|
//! | Home | `$MATRX_HOME_DIR`, default `~/.matrx` (dev `~/.matrx-dev`) | `%LOCALAPPDATA%\Matrx\<world>\` — the ONE Windows home |
//! | Control endpoint | `<home>/run/syncd.sock`, mode 0600, parent dir 0700 | `\\.\pipe\matrx-syncd-<world>-<sha1(user SID)[:12]>` |
//! | Tokens | `<home>/syncd.token`, mode 0600, two lines | same path, ACL that SID only |
//! | Discovery · journal | `<home>/syncd.json` · `<home>/syncd.db` | same |

use matrx_sync::custody::World;
use std::io;
use std::path::{Path, PathBuf};

/// Every path this daemon owns.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Paths {
    /// The world these paths belong to.
    pub world: World,
    /// `~/.matrx`, `~/.matrx-dev`, or `%LOCALAPPDATA%\Matrx\<world>`.
    pub home: PathBuf,
    /// `<home>/syncd.db` — never `matrx.db`, which the Python engine owns.
    pub journal: PathBuf,
    /// `<home>/syncd.json` (C5) — never the engine's `local.json`, which has a single-publisher
    /// clobber guard and six readers (D18, SPEC-ENGINE rule 13).
    pub discovery: PathBuf,
    /// `<home>/syncd.token` — ONE file, two lines: `control`, then `read` (C5, S17).
    pub tokens: PathBuf,
    /// `<home>/run` — the socket's parent, mode 0700 on Unix.
    pub run_dir: PathBuf,
    /// `<home>/run/syncd.sock` on Unix. On Windows the control endpoint is [`Paths::pipe_name`]
    /// instead and this path is never created.
    pub socket: PathBuf,
    /// `<home>/logs` — where the supervisor redirects stdout/stderr (SPEC-ENGINE §1.4).
    pub logs: PathBuf,
}

impl Paths {
    /// Resolve this world's paths.
    ///
    /// `MATRX_HOME_DIR` is honoured on every OS because the Python engine already sets it for a
    /// dev run (`run.py`) and a daemon that ignored it would sit in a different home than the
    /// engine it is a sibling of.
    pub fn resolve(world: World) -> io::Result<Self> {
        let home = match std::env::var_os("MATRX_HOME_DIR") {
            Some(dir) if !dir.is_empty() => PathBuf::from(dir),
            _ => Self::default_home(world)?,
        };
        Ok(Paths {
            world,
            journal: home.join("syncd.db"),
            discovery: home.join("syncd.json"),
            tokens: home.join("syncd.token"),
            run_dir: home.join("run"),
            socket: home.join("run").join("syncd.sock"),
            logs: home.join("logs"),
            home,
        })
    }

    #[cfg(windows)]
    fn default_home(world: World) -> io::Result<PathBuf> {
        // `%LOCALAPPDATA%\Matrx\<world>\` is the ONE Windows home: nothing of the daemon's lives
        // under `%USERPROFILE%\.matrx` (SPEC-ENGINE §1.9). LOCALAPPDATA is per-user and never
        // roams, which is what keeps D18's isolation true on a domain-joined machine.
        let local = std::env::var_os("LOCALAPPDATA").ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::NotFound,
                "LOCALAPPDATA is not set, so this Windows user has no per-user application data \
                 directory for AI Matrx Sync to use",
            )
        })?;
        Ok(PathBuf::from(local).join("Matrx").join(world.as_str()))
    }

    #[cfg(not(windows))]
    fn default_home(world: World) -> io::Result<PathBuf> {
        let home = std::env::var_os("HOME").ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::NotFound,
                "HOME is not set, so AI Matrx Sync cannot find this user's home directory",
            )
        })?;
        Ok(PathBuf::from(home).join(match world {
            World::Live => ".matrx",
            World::Dev => ".matrx-dev",
        }))
    }

    /// Create every directory this daemon writes into, with the modes D18 requires.
    pub fn create_dirs(&self) -> io::Result<()> {
        std::fs::create_dir_all(&self.home)?;
        std::fs::create_dir_all(&self.run_dir)?;
        std::fs::create_dir_all(&self.logs)?;
        // The socket's parent is 0700: a second OS user cannot even list it (§12).
        set_dir_mode_0700(&self.run_dir)?;
        Ok(())
    }

    /// The Windows control endpoint: `\\.\pipe\matrx-syncd-<world>-<sha1(user SID)[:12]>` (C6).
    ///
    /// The SID — not the username — because two accounts can share a display name across a domain
    /// trust while their SIDs never collide, and because the pipe's DACL is written from the same
    /// SID.
    #[cfg(windows)]
    pub fn pipe_name(&self) -> io::Result<String> {
        let sid = crate::windows_user::current_user_sid()?;
        let digest = sha1_hex(sid.as_bytes());
        Ok(format!(
            r"\\.\pipe\matrx-syncd-{}-{}",
            self.world.as_str(),
            &digest[..12]
        ))
    }
}

#[cfg(unix)]
fn set_dir_mode_0700(dir: &Path) -> io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700))
}

#[cfg(not(unix))]
fn set_dir_mode_0700(_dir: &Path) -> io::Result<()> {
    // On Windows the isolation comes from `%LOCALAPPDATA%`, which is already ACL'd to this user
    // alone, and from the named pipe's own DACL. There is no mode bit to set, and pretending to
    // set one would be the false sentence law 4 forbids.
    Ok(())
}

/// Write a file only this user can read (0600 on Unix).
///
/// Used for `syncd.token` and `syncd.json`. The write is atomic: a temporary file in the same
/// directory, chmod'ed before it holds anything, then renamed — so no reader ever sees a partial
/// token file, and no moment exists where the file is world-readable.
pub fn write_private(path: &Path, contents: &str) -> io::Result<()> {
    let parent = path.parent().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("{} has no parent directory", path.display()),
        )
    })?;
    let temp = parent.join(format!(
        ".{}.tmp",
        path.file_name().and_then(|n| n.to_str()).unwrap_or("syncd")
    ));
    {
        let file = create_private(&temp)?;
        use std::io::Write as _;
        let mut file = file;
        file.write_all(contents.as_bytes())?;
        file.sync_all()?;
    }
    std::fs::rename(&temp, path)
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
    // `%LOCALAPPDATA%` inherits an ACL granting this user and SYSTEM only, and a file created
    // there inherits it. There is no chmod; the containing directory is the control.
    std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(path)
}

/// SHA-1, hex-encoded. Used for one thing only: the per-user suffix of the Windows pipe name,
/// which C6 specifies as `sha1(user SID)[:12]`. It is a naming hash, never a security primitive.
#[cfg(windows)]
fn sha1_hex(bytes: &[u8]) -> String {
    use sha1::{Digest, Sha1};
    let digest = Sha1::digest(bytes);
    digest.iter().map(|b| format!("{b:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_two_worlds_never_share_a_path() {
        // Resolved without MATRX_HOME_DIR so the defaults are what is compared. The environment is
        // read per call, so this asserts the mapping, not a cached value.
        let previous = std::env::var_os("MATRX_HOME_DIR");
        std::env::remove_var("MATRX_HOME_DIR");
        let live = Paths::resolve(World::Live).expect("live");
        let dev = Paths::resolve(World::Dev).expect("dev");
        if let Some(p) = previous {
            std::env::set_var("MATRX_HOME_DIR", p);
        }

        assert_ne!(live.home, dev.home);
        assert_ne!(live.journal, dev.journal);
        assert_ne!(live.tokens, dev.tokens);
        assert_ne!(live.socket, dev.socket);
        assert!(live.journal.ends_with("syncd.db"));
        // The engine's file, never ours (SPEC-ENGINE rule 13).
        assert!(!live.discovery.ends_with("local.json"));
        assert!(!live.journal.ends_with("matrx.db"));
    }

    #[test]
    fn a_private_file_is_written_atomically_and_owner_only() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("syncd.token");
        write_private(&path, "control-token\nread-token\n").expect("write");
        assert_eq!(
            std::fs::read_to_string(&path).expect("read"),
            "control-token\nread-token\n"
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&path).expect("stat").permissions().mode();
            assert_eq!(mode & 0o777, 0o600, "mode is {:o}", mode & 0o777);
        }
        // No temporary is left behind.
        let leftovers: Vec<_> = std::fs::read_dir(dir.path())
            .expect("read_dir")
            .filter_map(|e| e.ok())
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .filter(|n| n.ends_with(".tmp"))
            .collect();
        assert!(leftovers.is_empty(), "left {leftovers:?}");
    }
}
