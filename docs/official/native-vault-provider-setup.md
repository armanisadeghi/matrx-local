# Native Vault provider setup

The desktop host reports the current macOS AutoFill setting only for its own
packaged bundle: `com.aimatrx.desktop` with the nested provider
`com.aimatrx.desktop.vault-provider`. It requires the host AutoFill
credential-provider entitlement before it queries the identity-store state or
offers a system action. A source Tauri run therefore reports unavailable rather
than inspecting another installed app.

On macOS 15 or later, **Enable AutoFill** asks macOS to enable the contained
provider. macOS requires ten seconds between requests. **Open macOS settings**
uses the supported Settings helper and only means Settings navigation was
accepted. **Refresh** reads the identity-store enabled bit; it does not read
provider identities, Keychain entries, tokens, or a private session.

Enabled AutoFill is not a live Vault connection and does not prove that a
credential can be filled. The Settings screen keeps those facts separate and
does not advertise passkey filling.

Testing the two system actions changes a macOS-user-level setting. Run that
acceptance test only in a disposable macOS account or VM with the exact signed
bundle and provider. Source Tauri and browser tests can verify the unavailable
path and bridge contract, but cannot prove bundled-provider setup.
