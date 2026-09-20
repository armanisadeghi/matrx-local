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

## Change log

- 2026-09-20: Added private one-shot file parser ABI and Swift adapter with original-key invariance and cancellation/reuse refusal checks.

- 2026-09-20: Added dedicated native import transport with account/scope fencing, source-bound write receipts and cancellation-safe receipt lookup.

- 2026-09-20: Integrated suggestions with the current organization decoder and refused missing bound membership before lookup. The standalone core declares its own Cargo workspace so nested managed checkouts cannot inherit an unrelated parent workspace.

- 2026-09-19: Exposed the already-validated source-v1-to-PKCS#8 converter through the provider-private UniFFI bridge. The caller supplies owned canonical source bytes; the bridge has no vault fetch or authority function, zeroizes Rust-owned incoming bytes/intermediates, and returns DER plus typed public metadata to generated Swift. Generated Swift necessarily owns ABI copies and has no cryptographic wipe guarantee. The application-extension Swift harness verifies exact metadata and CryptoKit DER interoperability from synthetic in-memory source; this remains a conversion handoff only, at that checkpoint; later export/controller work is described above.

- 2026-09-19: Added a provider-private source-v1-to-PKCS#8 conversion primitive for the future native exchange controller. It accepts only the existing canonical ES256 source-v1 shape (zero counter, empty extensions, BE/BS true), reuses its exact COSE key-pair validation, returns a non-debuggable and non-serializable value with zeroizing DER plus typed public metadata, and does not expose FFI, a Tauri command, controller, import, or storage path. `test-native-vault-source-export.sh` writes only a deterministic synthetic test DER to a private temporary directory and uses OpenSSL to verify that its derived public key matches the source fixture; neither DER nor key material is printed.

- 2026-09-19: Added the provider-private UniFFI 0.32.1 native passkey bridge. The gated Rust static library accepts only typed Apple-adapter inputs, verifies the trusted native callback before maintained core work, commits canonical registration source through the callback before releasing a response, and fences cancellation with a one-shot atomic operation. The bridge zeroizes owned incoming source buffers where it owns them and registers a cancellation waiter before its atomic-state check. `test-native-vault-bridge.sh` compiles the generated Swift consumer with the application-extension restriction, proves nil versus empty persisted display names in harness memory, declared denied/failed outcomes and truly undeclared Swift callback errors, persistence outcomes, one-shot use, deterministic cancel/completion race winners, source limits, RP and allow-list refusal, and a real registration/assertion accepted by independent `python-fido2` with RP-ID, challenge and client-data-hash tamper rejection. `build-native-vault-provider.sh` regenerates and statically links the optimized release archive for arm64 and x86_64 using pinned Rust 1.93.1; package verification also mutates away a bridge symbol to prove the linkage guard rejects it. Malformed request inputs refuse before user-verification callbacks. Unexpected callback errors use UniFFI’s maintained conversion to discard foreign diagnostic text and return fixed failure without aborting. The bridge does not advertise passkeys, invoke an AuthenticationServices passkey controller, access provider storage, or establish OS readiness.

- 2026-09-13: Verified shared-state implementation and focused process/filesystem checks at `0eb823933`; documented its separation from unfinished native credential delivery.
