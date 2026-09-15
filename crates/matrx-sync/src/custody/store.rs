//! The OS credential store (§4, S6, S7, S15).
//!
//! One item per (world, user): service `com.aimatrx.syncd` / `com.aimatrx.syncd.dev`, account =
//! the Supabase user id. **The access token and the code verifier are never on disk, anywhere.**
//!
//! S7 is the rule that shapes this module: a keychain that will not open is a named state, never a
//! fallback to disk. There is no second store to try — keyring's `v1` feature set carries no
//! keyutils store — so the daemon runs in memory for the session and publishes
//! `credential_store_unavailable` with its remedy.

use super::error::{CustodyError, Result};
use super::world::World;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Mutex;

/// The versioned JSON object stored under the keychain item (§4).
///
/// **Not stored:** the organization — membership is a cloud fact resolved per request, and caching
/// it here would be a second source of truth (SCOPE §2, D5).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StoredCredential {
    /// The stored-shape version. Only `1` exists.
    pub v: u8,
    /// The one refresh token on the device.
    pub refresh_token: String,
    /// Supabase user id (uuid) — also the keychain item's account name.
    pub user_id: String,
    /// For the sign-in-needed prompt ("Sign back in as …"). Never part of an item name.
    pub email: String,
    /// The public OAuth client this token belongs to.
    pub client_id: String,
    /// RFC3339, when the session was first established.
    pub issued_at: String,
    /// RFC3339, when the refresh token was last replaced.
    pub rotated_at: String,
    /// `live` or `dev`.
    pub world: World,
}

impl StoredCredential {
    /// The current stored-shape version.
    pub const VERSION: u8 = 1;

    /// Windows Credential Manager caps a blob at 2560 bytes. The daemon refuses to write and says
    /// why rather than letting a future value fail opaquely on one OS (§4, S7).
    pub const MAX_BLOB_BYTES: usize = 2560;

    /// Serialise, refusing a blob that will not fit the narrowest of the three stores.
    pub fn to_blob(&self) -> Result<String> {
        let blob = serde_json::to_string(self).map_err(|e| CustodyError::CredentialStore {
            operation: "encode the credential",
            cause: e.to_string(),
        })?;
        if blob.len() > Self::MAX_BLOB_BYTES {
            return Err(CustodyError::CredentialStore {
                operation: "store the credential",
                cause: format!(
                    "the credential is {} bytes and the Windows Credential Manager caps an item at \
                     {}; refusing to write a value that would fail on one OS only",
                    blob.len(),
                    Self::MAX_BLOB_BYTES
                ),
            });
        }
        Ok(blob)
    }

    /// Parse a stored blob, refusing a version this build does not know.
    pub fn from_blob(blob: &str) -> Result<Self> {
        let value: Self =
            serde_json::from_str(blob).map_err(|e| CustodyError::CredentialStore {
                operation: "decode the stored credential",
                cause: e.to_string(),
            })?;
        if value.v != Self::VERSION {
            return Err(CustodyError::CredentialStore {
                operation: "decode the stored credential",
                cause: format!(
                    "the stored credential is version {} and this build reads version {}",
                    value.v,
                    Self::VERSION
                ),
            });
        }
        Ok(value)
    }
}

/// The one seam between custody and the OS keychain.
///
/// Synchronous on purpose: every implementation is a blocking OS call, and the [`Custodian`]
/// wraps it in `spawn_blocking` rather than pretending it is async.
///
/// [`Custodian`]: super::Custodian
pub trait CredentialStore: Send + Sync + std::fmt::Debug {
    /// Read the item for `user_id`, or `None` when no item exists.
    fn load(&self, user_id: &str) -> Result<Option<StoredCredential>>;
    /// Create or replace the item.
    fn save(&self, credential: &StoredCredential) -> Result<()>;
    /// Delete the item. Deleting an item that is already gone is success, not an error.
    fn delete(&self, user_id: &str) -> Result<()>;
}

/// The real store: `keyring` 4.2.0 with default features (S6).
#[derive(Debug)]
pub struct KeyringStore {
    service: &'static str,
}

impl KeyringStore {
    /// A store for this world's service name.
    pub fn new(world: World) -> Self {
        KeyringStore {
            service: world.keychain_service(),
        }
    }

    fn entry(&self, user_id: &str) -> Result<keyring::Entry> {
        keyring::Entry::new(self.service, user_id).map_err(|e| CustodyError::CredentialStore {
            operation: "open the credential item",
            cause: e.to_string(),
        })
    }
}

impl CredentialStore for KeyringStore {
    fn load(&self, user_id: &str) -> Result<Option<StoredCredential>> {
        match self.entry(user_id)?.get_password() {
            Ok(blob) => StoredCredential::from_blob(&blob).map(Some),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(e) => Err(CustodyError::CredentialStore {
                operation: "read the credential item",
                cause: e.to_string(),
            }),
        }
    }

    fn save(&self, credential: &StoredCredential) -> Result<()> {
        let blob = credential.to_blob()?;
        self.entry(&credential.user_id)?
            .set_password(&blob)
            .map_err(|e| CustodyError::CredentialStore {
                operation: "write the credential item",
                cause: e.to_string(),
            })
    }

    fn delete(&self, user_id: &str) -> Result<()> {
        match self.entry(user_id)?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(e) => Err(CustodyError::CredentialStore {
                operation: "delete the credential item",
                cause: e.to_string(),
            }),
        }
    }
}

/// `FakeKeychain` (§13): an in-memory store for the store / locked / failed-write paths.
///
/// **It is a mock and is never cited as product evidence** (SCOPE §6 attestation rule).
#[derive(Debug, Default)]
pub struct FakeKeychain {
    items: Mutex<HashMap<String, String>>,
    /// When set, every operation fails with this cause — the "keychain will not open" path (S7).
    locked: Mutex<Option<String>>,
    /// When set, only writes fail — S8's write-ahead failure path.
    refuse_writes: Mutex<Option<String>>,
    /// When set, every operation blocks for this long — the macOS "a dialog is up and nobody is
    /// there to click it" path (S15). Observed live on 2026-09-15.
    hang_for: Mutex<Option<std::time::Duration>>,
}

impl FakeKeychain {
    /// An empty, working fake.
    pub fn new() -> Self {
        Self::default()
    }

    /// Make every operation fail as if the store were locked or absent (S7).
    pub fn lock(&self, cause: &str) {
        *self.locked.lock().expect("fake keychain lock") = Some(cause.to_string());
    }

    /// Make every operation block, as a keychain showing an approval dialog does. A background
    /// service has no window to answer that dialog in, so this must become a named state rather
    /// than a hang (S7, S15).
    pub fn hang(&self, for_duration: std::time::Duration) {
        *self.hang_for.lock().expect("fake keychain lock") = Some(for_duration);
    }

    /// Make writes — and only writes — fail (S8).
    pub fn refuse_writes(&self, cause: &str) {
        *self.refuse_writes.lock().expect("fake keychain lock") = Some(cause.to_string());
    }

    /// Allow writes again.
    pub fn allow_writes(&self) {
        *self.refuse_writes.lock().expect("fake keychain lock") = None;
    }

    /// Whether an item exists for `user_id` — what the sign-out proof asserts.
    pub fn contains(&self, user_id: &str) -> bool {
        self.items
            .lock()
            .expect("fake keychain lock")
            .contains_key(user_id)
    }

    /// Remove an item without bringing [`CredentialStore`] into the caller's scope — what the
    /// battery uses to simulate a wiped keychain.
    pub fn delete_for_test(&self, user_id: &str) {
        self.items
            .lock()
            .expect("fake keychain lock")
            .remove(user_id);
    }

    /// The refresh token currently stored for `user_id`, if any — what the write-ahead assertion
    /// (S8) reads to prove a failed rotation left the old value untouched.
    pub fn stored_refresh_token(&self, user_id: &str) -> Option<String> {
        self.items
            .lock()
            .expect("fake keychain lock")
            .get(user_id)
            .and_then(|blob| StoredCredential::from_blob(blob).ok())
            .map(|c| c.refresh_token)
    }

    fn gate(&self, operation: &'static str) -> Result<()> {
        let hang = *self.hang_for.lock().expect("fake keychain lock");
        if let Some(duration) = hang {
            std::thread::sleep(duration);
        }
        if let Some(cause) = self.locked.lock().expect("fake keychain lock").clone() {
            return Err(CustodyError::CredentialStore { operation, cause });
        }
        Ok(())
    }
}

impl CredentialStore for FakeKeychain {
    fn load(&self, user_id: &str) -> Result<Option<StoredCredential>> {
        self.gate("read the credential item")?;
        match self.items.lock().expect("fake keychain lock").get(user_id) {
            Some(blob) => StoredCredential::from_blob(blob).map(Some),
            None => Ok(None),
        }
    }

    fn save(&self, credential: &StoredCredential) -> Result<()> {
        self.gate("write the credential item")?;
        if let Some(cause) = self.refuse_writes.lock().expect("fake keychain lock").clone() {
            return Err(CustodyError::CredentialStore {
                operation: "write the credential item",
                cause,
            });
        }
        let blob = credential.to_blob()?;
        self.items
            .lock()
            .expect("fake keychain lock")
            .insert(credential.user_id.clone(), blob);
        Ok(())
    }

    fn delete(&self, user_id: &str) -> Result<()> {
        self.gate("delete the credential item")?;
        self.items
            .lock()
            .expect("fake keychain lock")
            .remove(user_id);
        Ok(())
    }
}
