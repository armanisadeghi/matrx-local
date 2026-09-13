# passkey-authenticator provenance

- Source: official crates.io `passkey-authenticator` 0.5.0 archive.
- Archive SHA-256: `20bf5800e3f6287580da985fb88e678f37c078d62242cb536941c44d852ab37c`.
- Released source tag: `53ca3f9ab146848dfe3ff1e2e93b03b8542de4c3` (`passkey-authenticator-v0.5.0`).
- License: MIT OR Apache-2.0; this vendor copy retains matching notices because the archive omits them.
- Exact normalized delta: [`provenance/passkey-authenticator-0.5.0.patch`](provenance/passkey-authenticator-0.5.0.patch), SHA-256 `af3acb38c587a39728627e234120df48c836c545eeb607c5986e7080a68f259c`.
- The guard downloads the official archive, verifies its SHA, applies that patch, and compares every non-`target` vendor file byte-for-byte.
- The patch adds validated immutable BackupFlags; make/get apply them after final authenticator-data construction, plus the stored-wrapper accessor seam and public operation fixtures.
- `provenance/semantic_adapter.rs` is one source copied unchanged into pristine and patched root-lock graphs. Its cfg sections only select the released ordinary make API versus the patched backup-aware API/accessor.
- Pristine must semantically fail false/false, true/false, and cross-user exclusion while true/true is a control; patched must pass all four.
- The pinned core compiler is Rust 1.93.1; `cargo metadata --locked` reports no selected package with a higher declared `rust-version`.
- `provenance/fido2_verifier.py` is the isolated `python-fido2==2.2.1` RP completion proof used by `desktop/scripts/check-native-vault-core.sh`.
