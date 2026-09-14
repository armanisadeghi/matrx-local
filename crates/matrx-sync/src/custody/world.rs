//! The one dev/live word (matrx-local Hard Rule 9, SPEC-ENGINE §1.9, SPEC-CUSTODY §2).
//!
//! Two separate custodies that never see each other's keychain items. This type carries the word
//! and everything custody derives from it; the *paths* are the daemon's, never this crate's.

use serde::{Deserialize, Serialize};
use std::fmt;

/// Which world this process belongs to. Never a `dev` boolean (SPEC-ENGINE §1.9).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum World {
    /// The packaged app: `~/.matrx`, engine band 22140–22159, daemon band 22160–22179.
    Live,
    /// Every source run: `~/.matrx-dev`, engine band 22240–22259, daemon band 22260–22279.
    Dev,
}

impl World {
    /// The wire spelling, identical to the serde representation and to `syncd.json`'s `world`.
    pub const fn as_str(self) -> &'static str {
        match self {
            World::Live => "live",
            World::Dev => "dev",
        }
    }

    /// Parse the wire spelling. Returns `None` for anything else — a world is never guessed.
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "live" => Some(World::Live),
            "dev" => Some(World::Dev),
            _ => None,
        }
    }

    /// The base of this world's daemon port band (C7, S21): 22160 live, 22260 dev.
    pub const fn daemon_band_base(self) -> u16 {
        match self {
            World::Live => 22160,
            World::Dev => 22260,
        }
    }

    /// The last port of this world's daemon band, inclusive.
    pub const fn daemon_band_last(self) -> u16 {
        self.daemon_band_base() + 19
    }

    /// The ONE fixed port in the daemon (S3, S21): the OAuth loopback callback. 22161 / 22261.
    ///
    /// It is fixed only because Supabase requires exact redirect matching (SPEC-CUSTODY §3.1);
    /// nothing else in the daemon may claim a fixed port.
    pub const fn oauth_callback_port(self) -> u16 {
        self.daemon_band_base() + 1
    }

    /// The fixed loopback redirect URI registered on the desktop OAuth client (S3).
    ///
    /// The literal is `localhost`, matching the already-registered
    /// `http://localhost:1420/auth/callback`, so no new matching behaviour is assumed. The daemon
    /// binds `127.0.0.1` **and** `[::1]` on the port so either resolution lands.
    pub fn loopback_redirect_uri(self) -> String {
        format!(
            "http://localhost:{}/oauth/callback",
            self.oauth_callback_port()
        )
    }

    /// The keychain service name for this world (S6). One item per (world, user); no item name
    /// ever contains an email address.
    pub const fn keychain_service(self) -> &'static str {
        match self {
            World::Live => "com.aimatrx.syncd",
            World::Dev => "com.aimatrx.syncd.dev",
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
    fn bands_never_overlap_the_engine_bands() {
        // VERIFIED constants: app/preflight.py DEFAULT_ENGINE_PORT = 22140, ENGINE_PORT_SCAN = 20;
        // desktop/src/lib/engine-ports.ts mirrors them. C7 forbids any overlap.
        for (world, engine_base) in [(World::Live, 22140u16), (World::Dev, 22240u16)] {
            let engine = engine_base..=(engine_base + 19);
            assert!(!engine.contains(&world.daemon_band_base()));
            assert!(!engine.contains(&world.daemon_band_last()));
            assert!(!engine.contains(&world.oauth_callback_port()));
        }
    }

    #[test]
    fn the_fixed_oauth_port_is_s3s_literal() {
        assert_eq!(World::Live.oauth_callback_port(), 22161);
        assert_eq!(World::Dev.oauth_callback_port(), 22261);
        assert_eq!(
            World::Dev.loopback_redirect_uri(),
            "http://localhost:22261/oauth/callback"
        );
    }

    #[test]
    fn the_two_worlds_never_share_a_keychain_service() {
        assert_ne!(World::Live.keychain_service(), World::Dev.keychain_service());
    }

    #[test]
    fn a_world_is_never_guessed() {
        assert_eq!(World::parse("live"), Some(World::Live));
        assert_eq!(World::parse("dev"), Some(World::Dev));
        assert_eq!(World::parse("development"), None);
        assert_eq!(World::parse(""), None);
    }
}
