//! The one dev/live word, and everything derived from it.
//!
//! Identical in shape and in intent to `matrx-sync`'s `custody::World` (matrx-local Hard Rule 9):
//! a source build lives in the DEV world and can never touch the packaged app's home or its
//! keychain items. It is a separate type rather than a re-use of that one because the two carry
//! different derivations — that one names syncd's keychain service and port bands, this one names
//! the Home Connection's — and this task may not edit `matrx-sync`.

use serde::{Deserialize, Serialize};
use std::fmt;
use std::io;
use std::path::PathBuf;

/// Which world this process belongs to. Never a `dev` boolean.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum World {
    /// The packaged app and the installed helper: `~/.matrx`.
    Live,
    /// Every source run: `~/.matrx-dev`.
    Dev,
}

impl World {
    /// The wire spelling.
    pub const fn as_str(self) -> &'static str {
        match self {
            World::Live => "live",
            World::Dev => "dev",
        }
    }

    /// Parse the wire spelling. `None` for anything else — a world is never guessed.
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "live" => Some(World::Live),
            "dev" => Some(World::Dev),
            _ => None,
        }
    }

    /// The keychain item's service name (contract § "The helper binary").
    ///
    /// One item per world. `ai.matrx.home-connection` is deliberately NOT one of syncd's
    /// `com.aimatrx.syncd*` services: the two hold different credentials with different lifetimes,
    /// and signing out of one must never touch the other.
    pub const fn keychain_service(self) -> &'static str {
        match self {
            World::Live => "ai.matrx.home-connection",
            World::Dev => "ai.matrx.home-connection.dev",
        }
    }

    /// This world's home directory, following `matrx-syncd`'s rule exactly: `$MATRX_HOME_DIR`
    /// when set, else `~/.matrx` / `~/.matrx-dev`, else `%LOCALAPPDATA%\Matrx\<world>` on Windows.
    pub fn home(self) -> io::Result<PathBuf> {
        match std::env::var_os("MATRX_HOME_DIR") {
            Some(dir) if !dir.is_empty() => Ok(PathBuf::from(dir)),
            _ => self.default_home(),
        }
    }

    #[cfg(windows)]
    fn default_home(self) -> io::Result<PathBuf> {
        let local = std::env::var_os("LOCALAPPDATA").ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::NotFound,
                "LOCALAPPDATA is not set, so this Windows user has no per-user application data \
                 folder for the AI Matrx Home Connection to use. Sign in to Windows normally and \
                 run it again.",
            )
        })?;
        Ok(PathBuf::from(local).join("Matrx").join(self.as_str()))
    }

    #[cfg(not(windows))]
    fn default_home(self) -> io::Result<PathBuf> {
        let home = std::env::var_os("HOME").ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::NotFound,
                "HOME is not set, so the AI Matrx Home Connection cannot find this user's home \
                 folder. Run it from a normal login session.",
            )
        })?;
        Ok(PathBuf::from(home).join(match self {
            World::Live => ".matrx",
            World::Dev => ".matrx-dev",
        }))
    }

    /// The world a build defaults to: dev for a source build, live for a release build
    /// (Hard Rule 9).
    pub const fn default_for_build() -> Self {
        if cfg!(debug_assertions) {
            World::Dev
        } else {
            World::Live
        }
    }
}

impl fmt::Display for World {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_two_worlds_never_share_a_keychain_service() {
        assert_ne!(World::Live.keychain_service(), World::Dev.keychain_service());
        assert_eq!(World::Live.keychain_service(), "ai.matrx.home-connection");
        assert_eq!(World::Dev.keychain_service(), "ai.matrx.home-connection.dev");
    }

    #[test]
    fn the_helper_never_names_syncds_keychain_service() {
        for world in [World::Live, World::Dev] {
            assert!(
                !world.keychain_service().contains("syncd"),
                "{world} points at the sync daemon's item"
            );
        }
    }

    #[test]
    fn a_world_is_never_guessed() {
        assert_eq!(World::parse("live"), Some(World::Live));
        assert_eq!(World::parse("dev"), Some(World::Dev));
        assert_eq!(World::parse("development"), None);
        assert_eq!(World::parse(""), None);
    }
}
