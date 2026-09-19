---
type: Feature
title: Native Vault provider enrollment
description: Provider-owned native OAuth enrollment and non-secret host lifecycle boundary.
---

# Native Vault provider enrollment

The macOS credential-provider extension owns its distinct public OAuth client, PKCE transaction, provider-only Data Protection Keychain session, and App Group status publication. The host reads only the non-secret generation/status record and may invalidate or reconcile its actor; it cannot receive private session data or request token material. `ready` remains false until separate signed-provider, Keychain, LocalAuthentication, and real admin OAuth acceptance gates pass.

For password use, AuthenticationServices supplies the
request's actual domain/URL identifiers, the provider performs LocalAuthentication
before its protected Keychain read, chooses a current organization, lists value-free
matches, and materializes only the selected matching item. The provider uses one
session/refresh primitive for configuration, password use and passkey use. It
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

The provider's v2 public status holds only selected organization/revision, freshness and aggregate counts. `NativeVaultIdentity.swift` keeps account labels and service metadata in request-local Apple identity entries; it does not expose them through the host bridge. A selected password record is bound to the current subject, generation, scope revision and actual Apple service before the existing materialization path begins. An invalid record refuses selection; stale metadata still requires current online authority before credential use. Shared session acquisition completes durable terminal cleanup before delivering failure. Ambiguous session and protected-API failures preserve Apple entries while marking only the captured revision stale; late responses cannot alter a replacement enrollment or revision.

## Change log

- 2026-09-19: Added a provider-private source-v1-to-PKCS#8 conversion primitive for the future native exchange controller. It accepts only the existing canonical ES256 source-v1 shape (zero counter, empty extensions, BE/BS true), reuses its exact COSE key-pair validation, returns a non-debuggable and non-serializable value with zeroizing DER plus typed public metadata, and does not expose FFI, a Tauri command, controller, import, or storage path. `test-native-vault-source-export.sh` writes only a deterministic synthetic test DER to a private temporary directory and uses OpenSSL to verify that its derived public key matches the source fixture; neither DER nor key material is printed.

- 2026-09-19: Added the provider-private UniFFI 0.32.1 native passkey bridge. The gated Rust static library accepts only typed Apple-adapter inputs, verifies the trusted native callback before maintained core work, commits canonical registration source through the callback before releasing a response, and fences cancellation with a one-shot atomic operation. The bridge zeroizes owned incoming source buffers where it owns them and registers a cancellation waiter before its atomic-state check. `test-native-vault-bridge.sh` compiles the generated Swift consumer with the application-extension restriction, proves nil versus empty persisted display names in harness memory, declared denied/failed outcomes and truly undeclared Swift callback errors, persistence outcomes, one-shot use, deterministic cancel/completion race winners, source limits, RP and allow-list refusal, and a real registration/assertion accepted by independent `python-fido2` with RP-ID, challenge and client-data-hash tamper rejection. `build-native-vault-provider.sh` regenerates and statically links the optimized release archive for arm64 and x86_64 using pinned Rust 1.93.1; package verification also mutates away a bridge symbol to prove the linkage guard rejects it. Malformed request inputs refuse before user-verification callbacks. Unexpected callback errors use UniFFI’s maintained conversion to discard foreign diagnostic text and return fixed failure without aborting. The bridge does not advertise passkeys, invoke an AuthenticationServices passkey controller, access provider storage, or establish OS readiness.

- 2026-09-13: Verified shared-state implementation and focused process/filesystem checks at `0eb823933`; documented its separation from unfinished native credential delivery.
