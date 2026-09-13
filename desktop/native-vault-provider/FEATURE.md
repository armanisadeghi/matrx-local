---
type: Feature
title: Native Vault provider enrollment
description: Provider-owned native OAuth enrollment and non-secret host lifecycle boundary.
---

# Native Vault provider enrollment

The macOS credential-provider extension owns its distinct public OAuth client, PKCE transaction, provider-only Data Protection Keychain session, and App Group status publication. The host reads only the non-secret generation/status record and may invalidate or reconcile its actor; it cannot receive private session data or request token material. `ready` remains false until separate signed-provider, Keychain, LocalAuthentication, and real admin OAuth acceptance gates pass.

Canonical contract: `/Users/armanisadeghi/code/common-docs/projects/credential-sharing-browser-login/NATIVE-ENROLLMENT.md`.
