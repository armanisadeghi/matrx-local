---
type: Feature
title: Native Vault provider enrollment
description: Native OAuth enrollment, shared protected session and value-free host lifecycle boundary.
---

# Native Vault provider enrollment

The macOS credential-provider extension owns its distinct public OAuth client, PKCE transaction, Data Protection Keychain session shared with the signed containing native exchange process, and App Group status publication. The containing process is trusted for protected exchange after local verification and current account/generation checks. Its webview and Python helper remain separate processes without the Keychain group; public commands expose only selections and value-free status. Host library validation remains enabled. The host may invalidate or reconcile its actor through the shared generation/status record. `ready` remains false until separate signed-provider, Keychain, LocalAuthentication, and real admin OAuth acceptance gates pass.

For password use, AuthenticationServices supplies the
request's actual domain/URL identifiers, the provider performs LocalAuthentication
before its protected Keychain read, chooses a current organization, lists value-free
matches, and materializes only the selected matching item. The provider uses one
session/refresh primitive for configuration, password use and passkey use. `NativeVaultTransport.swift` holds the provider-only OAuth endpoints, bounded no-redirect request transport, form encoding, and response classification; `NativeVaultSessionAccess.swift` holds the shared protected-session acquisition and reconciliation primitive without extension UI dependencies. It
publishes bounded metadata-only identity suggestions and validates selected records
against the current account, generation and scope revision. It requires interaction
before credential use; source and harness checks do not establish signed OS delivery.

Canonical contracts: `/Users/armanisadeghi/code/common-docs/projects/credential-sharing-browser-login/NATIVE-ENROLLMENT.md` and `/Users/armanisadeghi/code/common-docs/projects/credential-sharing-browser-login/NATIVE-IDENTITIES.md`.

## Strict envelope corpus

`desktop/scripts/test-native-vault-codec.sh` compiles and executes
`NativeVaultCodec.swift`, the same source included by
`build-native-vault-provider.sh` in the application extension. Its corpus
proves strict per-object duplicate-key refusal after Unicode decoding, eight
container nesting, UTF-8 and surrogate handling, and the closed token,
userinfo, public-state, and active/refresh-pending private-session envelopes.
It does not access Keychain or OAuth and is not evidence that enrollment is
ready.

## Shared state

`NativeVaultState.swift` and the host's `native_vault.rs` use the Foundation-resolved App Group and held directory descriptors. Only explicit Connect initializes missing provider state, after private-session invalidation; ordinary reads never create it. Host status is historical metadata, with explicit busy/corrupt outcomes, and never means credential filling is ready. `desktop/scripts/test-native-vault-state.sh` and the Rust native-vault tests exercise the actual state implementation, including process contention and symlink refusal. Signed Keychain, enrollment lifecycle and OS credential delivery require separate acceptance under the canonical contract above.

## Identity suggestions

The provider's v2 public status holds only selected organization/revision, freshness and aggregate counts. `NativeVaultIdentity.swift` keeps account labels and service metadata in request-local Apple identity entries; it does not expose them through the host bridge. A selected password record is bound to the current subject, generation, scope revision and actual Apple service before the existing materialization path begins. A selected suggestion whose organization membership disappeared refuses before any match lookup; it never falls back to another organization. Unbound requests use the device choice or the explicit picker, without the account-level default. An invalid record refuses selection; stale metadata still requires current online authority before credential use. Shared session acquisition completes durable terminal cleanup before delivering failure. Ambiguous session and protected-API failures preserve Apple entries while marking only the captured revision stale; late responses cannot alter a replacement enrollment or revision.

## Protected exchange

`NativeVaultExport.swift` holds the authenticated, explicitly confirmed Apple export lifecycle. `NativeVaultImportTransport.swift` sends only native-owned source bytes to the dedicated import endpoint, binds writes and receipt reads to the acquired subject/generation and admitted scope, and validates fixed receipt envelopes. Cancelling forbids further writes but permits exact receipt recovery. Its focused transport corpus proves envelope, scope, receipt and late-account-switch refusal; this unit alone does not establish chooser integration or OS readiness. Canonical transfer contract: `/Users/armanisadeghi/code/common-docs/projects/credential-sharing-browser-login/NATIVE-EXCHANGE.md`.

`NativeVaultImportParser.swift` adapts the one-shot provider-private Rust file parser into bounded Swift inventory records. Rust cancellation refuses before ABI materialization; the Swift corpus verifies original public-key identity across CXF-to-source conversion. These private generated bindings are not public host IPC. Complete file-import readiness still requires the native chooser, confirmed controller, server storage and RP acceptance.

## Fill device (access ladder T-30)

`NativeVaultFillDevice.swift` gives the provider its own fill credential: a Secure Enclave P-256 key (non-exportable; Keychain keeps only the enclave's wrapped handle and the server's device id, this-device-only). Every `/vault/native/passwords/{id}/materialize` is signed with the extension's exact v2 wire (`matrx-vault-fill/v2`, surface `native_password_materialize`, fresh nonce, timestamp, SHA-256 of the sent body bytes, `X-Matrx-Fill-*` headers).

Turning filling on (first fill, or after `403 fill_device_required`) reads `GET /api/vault/fill-devices/step-up-methods` and offers the account's step-ups as equals (T-30c; champion: 1Password/Bitwarden unlock with master password or passkey): the AI Matrx password (sent once on `POST /api/vault/fill-devices`, never stored), or passkey approval on the web app — the passkey's relying party — at `https://aimatrx.com/vault/approve-browser?key=<RFC 7638 thumbprint>&label=…` (the same page the extension uses; it shows the key's short code and lets an account with no passkey add one), after which registration is sent WITHOUT a password and the server claims that single-use approval. An account with neither is told to add a passkey on aimatrx.com. A revoked key is replaced with a new enclave key, never re-admitted. Macs without a Secure Enclave are told to use the browser extension.

Turned off in the web Vault: the server ends this provider's sign-in and answers `401 fill_device_revoked` (never the plain `native_session_required`). The provider says "Password filling was turned off for this Mac. Approve it again to keep filling." with one action, **Reconnect and approve**: it runs the provider's own connect flow in the same sheet and, on success, continues the same fill (a new key, one approval). Test seams: `nativeFillStepUpPrompt`, `nativeFillReapprove`.

Corpus: `test-native-vault-password.sh` (software key stand-in; passkey, neither-method and turned-off cases). Live proof against a local aidream as admin@admin.com with the real enclave: `test-native-vault-fill-device-live.sh` — register, signed fill, attacks (replay, unsigned, wrong key with a real-key control, web-session registration even with the password, forged passkey approval, revoked key, old key after re-register, reused passkey approval), turned-off message, reconnect-and-approve, passkey-approval claim. The one stand-in: a live WebAuthn ceremony needs a person's authenticator, so `native-vault-passkey-approval-fixture.py` writes the approval row the verified approval writes; the claim path it feeds is the real one.

## Change log

- 2026-09-28 (Claude Opus 5.5, T-30d verification): passkey approval offered as the equal alternative to the password; a Mac turned off in the web Vault now says so (server `401 fill_device_revoked`) with one Reconnect-and-approve action that continues the fill; live harness extended with the full attack set. Server: native routes log the cause of every 503 and retry a transaction Postgres cancelled on lock_timeout (the intermittent local 503s were a parallel identity sweep holding the same vault rows).

- 2026-09-28 (Claude Opus 5.5, T-30 follow-up): the provider now registers a Secure Enclave device key with the password step-up and signs every materialize (v2 wire); live-proven on a local aidream. The organization decoder accepts the server's `archived_at` and never offers an archived organization.

- 2026-09-28 (Claude Opus 5.5, access ladder T-30): aidream now refuses `/vault/native/passwords/{id}/materialize` with 403 `fill_device_required` unless the request is signed by a registered native device key (Secure Enclave P-256 fits) with a one-time nonce over the provider's own live OAuth session; registering (`POST /api/vault/fill-devices`) needs the person's password. This provider does not sign yet — adopting the aidream `fill_devices.canonical_fill_message` v2 wire is required before password use works.

- 2026-09-20: Added private one-shot file parser ABI and Swift adapter with original-key invariance and cancellation/reuse refusal checks.

- 2026-09-20: Added dedicated native import transport with account/scope fencing, source-bound write receipts and cancellation-safe receipt lookup.

- 2026-09-20: Integrated suggestions with the current organization decoder and refused missing bound membership before lookup. The standalone core declares its own Cargo workspace so nested managed checkouts cannot inherit an unrelated parent workspace.

- 2026-09-19: Exposed the already-validated source-v1-to-PKCS#8 converter through the provider-private UniFFI bridge. The caller supplies owned canonical source bytes; the bridge has no vault fetch or authority function, zeroizes Rust-owned incoming bytes/intermediates, and returns DER plus typed public metadata to generated Swift. Generated Swift necessarily owns ABI copies and has no cryptographic wipe guarantee. The application-extension Swift harness verifies exact metadata and CryptoKit DER interoperability from synthetic in-memory source; this remains a conversion handoff only, at that checkpoint; later export/controller work is described above.

- 2026-09-19: Added a provider-private source-v1-to-PKCS#8 conversion primitive for the future native exchange controller. It accepts only the existing canonical ES256 source-v1 shape (zero counter, empty extensions, BE/BS true), reuses its exact COSE key-pair validation, returns a non-debuggable and non-serializable value with zeroizing DER plus typed public metadata, and does not expose FFI, a Tauri command, controller, import, or storage path. `test-native-vault-source-export.sh` writes only a deterministic synthetic test DER to a private temporary directory and uses OpenSSL to verify that its derived public key matches the source fixture; neither DER nor key material is printed.

- 2026-09-19: Added the provider-private UniFFI 0.32.1 native passkey bridge. The gated Rust static library accepts only typed Apple-adapter inputs, verifies the trusted native callback before maintained core work, commits canonical registration source through the callback before releasing a response, and fences cancellation with a one-shot atomic operation. The bridge zeroizes owned incoming source buffers where it owns them and registers a cancellation waiter before its atomic-state check. `test-native-vault-bridge.sh` compiles the generated Swift consumer with the application-extension restriction, proves nil versus empty persisted display names in harness memory, declared denied/failed outcomes and truly undeclared Swift callback errors, persistence outcomes, one-shot use, deterministic cancel/completion race winners, source limits, RP and allow-list refusal, and a real registration/assertion accepted by independent `python-fido2` with RP-ID, challenge and client-data-hash tamper rejection. `build-native-vault-provider.sh` regenerates and statically links the optimized release archive for arm64 and x86_64 using pinned Rust 1.93.1; package verification also mutates away a bridge symbol to prove the linkage guard rejects it. Malformed request inputs refuse before user-verification callbacks. Unexpected callback errors use UniFFI’s maintained conversion to discard foreign diagnostic text and return fixed failure without aborting. The bridge does not advertise passkeys, invoke an AuthenticationServices passkey controller, access provider storage, or establish OS readiness.

- 2026-09-13: Verified shared-state implementation and focused process/filesystem checks at `0eb823933`; documented its separation from unfinished native credential delivery.
