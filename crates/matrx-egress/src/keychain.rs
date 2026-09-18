//! The OS credential store — where the device token rests, and the only place it rests.
//!
//! `keyring` 4.x with default features, exactly as `matrx-sync`'s custody does: the macOS
//! Keychain, the Windows Credential Manager, the Secret Service on Linux. One item per world,
//! service `ai.matrx.home-connection` (dev `ai.matrx.home-connection.dev`), account = this
//! computer's device identity.
//!
//! **`matrx-sync::custody::store::KeyringStore` is deliberately not reused.** It names syncd's
//! service through `World::keychain_service()` and stores a Supabase refresh token plus a user id;
//! this helper stores a device bearer token, a device id and a server address. Making it serve
//! both would mean editing that crate, which this work may not do.
//!
//! **A keychain that will not open is a named state, never a fallback to disk** (S7): the token
//! is never written to a file, not even a private one. Every call is bounded by the caller, for
//! the reason the sync daemon learned on 2026-09-15 — macOS can put up an approval dialog that a
//! background process has no window to answer, and an unbounded call waits for it forever.

use crate::world::World;
use serde::{Deserialize, Serialize};
use std::fmt;

/// What the helper keeps between runs.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StoredDevice {
    /// The stored-shape version. Only `1` exists.
    pub v: u8,
    /// `platform.egress_device.id`.
    pub device_id: String,
    /// The full bearer `mxe_<device_id>_<secret>`. Never logged, never written to a file.
    pub device_token: String,
    /// The name the account shows for this computer.
    pub display_name: String,
    /// The server this token belongs to — a token is meaningless against a different one.
    pub server: String,
    /// RFC3339, when this computer was connected.
    pub paired_at: String,
}

impl StoredDevice {
    /// The current stored-shape version.
    pub const VERSION: u8 = 1;

    /// Windows Credential Manager caps an item at 2560 bytes. Refuse to write a value that would
    /// fail on one OS only, rather than discovering it on a user's PC.
    pub const MAX_BLOB_BYTES: usize = 2560;
}

impl fmt::Display for StoredDevice {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // Display is what a log line reaches for. The token is not in it.
        write!(
            f,
            "{} ({}) on {}",
            self.display_name, self.device_id, self.server
        )
    }
}

/// Why a credential-store call failed. Every variant carries a remedy, because every one of them
/// reaches a user.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct KeychainError {
    /// What was being attempted, as a phrase: "read", "save", "remove".
    pub operation: &'static str,
    /// The underlying cause, for the log.
    pub cause: String,
}

impl KeychainError {
    /// What the user should do.
    pub fn remedy(&self) -> String {
        if cfg!(target_os = "macos") {
            "Your Mac is not letting AI Matrx use its keychain. Open the Home Connection from your \
             Applications folder and allow the prompt that appears — allowing it once is enough."
                .to_string()
        } else if cfg!(target_os = "windows") {
            "Windows is not letting AI Matrx use Credential Manager. Sign in to Windows normally \
             and start the Home Connection again."
                .to_string()
        } else {
            "This computer has no unlocked password store (Secret Service) for AI Matrx to use. \
             Start your desktop session's keyring and run the Home Connection again."
                .to_string()
        }
    }
}

impl fmt::Display for KeychainError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "could not {} this computer's saved connection: {}",
            self.operation, self.cause
        )
    }
}

impl std::error::Error for KeychainError {}

/// This world's credential item.
#[derive(Debug, Clone)]
pub struct Keychain {
    service: &'static str,
    account: String,
}

impl Keychain {
    /// The item for this world and this computer.
    pub fn new(world: World, account: impl Into<String>) -> Self {
        Keychain {
            service: world.keychain_service(),
            account: account.into(),
        }
    }

    fn entry(&self) -> Result<keyring::Entry, KeychainError> {
        keyring::Entry::new(self.service, &self.account).map_err(|e| KeychainError {
            operation: "open",
            cause: e.to_string(),
        })
    }

    /// Read the stored device, or `None` when this computer has never been connected.
    pub fn load(&self) -> Result<Option<StoredDevice>, KeychainError> {
        match self.entry()?.get_password() {
            Ok(blob) => {
                let stored: StoredDevice =
                    serde_json::from_str(&blob).map_err(|e| KeychainError {
                        operation: "read",
                        cause: e.to_string(),
                    })?;
                if stored.v != StoredDevice::VERSION {
                    return Err(KeychainError {
                        operation: "read",
                        cause: format!(
                            "the saved connection is version {} and this version of the Home \
                             Connection reads version {}",
                            stored.v,
                            StoredDevice::VERSION
                        ),
                    });
                }
                Ok(Some(stored))
            }
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(e) => Err(KeychainError {
                operation: "read",
                cause: e.to_string(),
            }),
        }
    }

    /// Create or replace the item.
    pub fn save(&self, device: &StoredDevice) -> Result<(), KeychainError> {
        let blob = serde_json::to_string(device).map_err(|e| KeychainError {
            operation: "save",
            cause: e.to_string(),
        })?;
        if blob.len() > StoredDevice::MAX_BLOB_BYTES {
            return Err(KeychainError {
                operation: "save",
                cause: format!(
                    "the saved connection would be {} bytes and one of the three credential \
                     stores caps an item at {}",
                    blob.len(),
                    StoredDevice::MAX_BLOB_BYTES
                ),
            });
        }
        self.entry()?
            .set_password(&blob)
            .map_err(|e| KeychainError {
                operation: "save",
                cause: e.to_string(),
            })
    }

    /// Delete the item. Deleting one that is already gone is success.
    pub fn delete(&self) -> Result<(), KeychainError> {
        match self.entry()?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(e) => Err(KeychainError {
                operation: "remove",
                cause: e.to_string(),
            }),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> StoredDevice {
        StoredDevice {
            v: StoredDevice::VERSION,
            device_id: "6d0f0c2e-0000-4000-8000-000000000001".into(),
            device_token: "mxe_6d0f0c2e-0000-4000-8000-000000000001_supersecretvalue".into(),
            display_name: "Arman's MacBook Pro".into(),
            server: "https://server.app.matrxserver.com".into(),
            paired_at: "2026-09-18T00:00:00Z".into(),
        }
    }

    #[test]
    fn the_item_is_this_worlds_and_its_account_is_never_an_email_address() {
        let live = Keychain::new(World::Live, "inst_abc-helper");
        let dev = Keychain::new(World::Dev, "inst_abc-helper");
        assert_eq!(live.service, "ai.matrx.home-connection");
        assert_eq!(dev.service, "ai.matrx.home-connection.dev");
        assert_ne!(live.service, dev.service);
        assert!(!live.account.contains('@'));
    }

    #[test]
    fn the_stored_shape_round_trips() {
        let device = sample();
        let blob = serde_json::to_string(&device).expect("serialise");
        let back: StoredDevice = serde_json::from_str(&blob).expect("parse");
        assert_eq!(back, device);
        assert!(blob.len() < StoredDevice::MAX_BLOB_BYTES);
    }

    #[test]
    fn the_token_is_not_in_the_log_line() {
        let rendered = format!("{}", sample());
        assert!(!rendered.contains("supersecretvalue"), "{rendered}");
        assert!(rendered.contains("Arman's MacBook Pro"), "{rendered}");
    }

    #[test]
    fn every_failure_carries_a_remedy_that_names_this_operating_system() {
        let error = KeychainError {
            operation: "read",
            cause: "the keychain is locked".into(),
        };
        let remedy = error.remedy();
        assert!(remedy.len() > 40, "{remedy}");
        if cfg!(target_os = "macos") {
            assert!(remedy.contains("Mac"), "{remedy}");
            assert!(!remedy.contains("Secret Service"), "{remedy}");
        }
        assert!(error.to_string().contains("the keychain is locked"));
    }
}
