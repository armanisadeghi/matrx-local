---
type: Feature
title: Native Vault provider enrollment
description: Provider-owned native OAuth enrollment and non-secret host lifecycle boundary.
---

# Native Vault provider enrollment

The macOS credential-provider extension owns its distinct public OAuth client, PKCE transaction, provider-only Data Protection Keychain session, and App Group status publication. The host reads only the non-secret generation/status record and may invalidate or reconcile its actor; it cannot receive private session data or request token material. `ready` remains false until separate signed-provider, Keychain, LocalAuthentication, and real admin OAuth acceptance gates pass.

The first password unit is direct-list only: AuthenticationServices supplies the
request's actual domain/URL identifiers, the provider performs LocalAuthentication
before its protected Keychain read, chooses a current organization, lists value-free
matches, and materializes only the selected matching item. The provider uses one
session/refresh primitive for configuration and password use. It does not populate
the identity store, handle no-interaction filling, or claim signed OS delivery.

Canonical contract: `/Users/armanisadeghi/code/common-docs/projects/credential-sharing-browser-login/NATIVE-ENROLLMENT.md`.

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

## Change log

- 2026-09-19: Added the provider-private UniFFI 0.32.1 native passkey bridge. The gated Rust static library accepts only typed Apple-adapter inputs, verifies the trusted native callback before maintained core work, commits canonical registration source through the callback before releasing a response, and fences cancellation with a one-shot atomic operation. `build-native-vault-provider.sh` regenerates and statically links its extension-safe Swift bindings for arm64 and x86_64. `test-native-vault-bridge.sh` drives the generated Swift consumer through a real registration/assertion accepted by independent `python-fido2`. The bridge does not advertise passkeys, invoke an AuthenticationServices passkey controller, access provider storage, or establish OS readiness.

- 2026-09-13: Verified shared-state implementation and focused process/filesystem checks at `0eb823933`; documented its separation from unfinished native credential delivery.
