# Personal messaging service

Device-owned WhatsApp uses Beeper Desktop's documented API at fixed loopback
`127.0.0.1:23373`. This service supplies native HTTP, encrypted grant custody and
canonical connection metadata primitives. Customer routes consume these
primitives; a stored cloud receipt never authorizes a native read by itself.

`models.py` declares the private `DeviceGrant` envelope and its secret-free public
projection. Grant generation changes only after retirement. Native account
identity includes both Beeper's account label and its actual native user ID;
`whatsapp` alone is not a person identifier.

`store.py` stores keychain-encrypted envelopes in the private local SQLite
`app_settings` namespace `personal_messaging_grants_v1`. This namespace does not
enter SettingsSync, model API-key storage, platform Vault or cloud metadata.
Writes use the existing settings lock and revision compare-and-swap. Failed
provider cleanup keeps encrypted cleanup credentials while refusing all reads;
confirmed retirement wipes credentials and preserves the cloud-retirement
receipt. Secrets never fall back to plaintext.

`beeper.py` pins the OAuth authority and callback to the discovered local-engine
port, requests public-client PKCE read consent, validates expiry/introspection,
and preserves every issued credential for cleanup before declaring it usable.
Native operations recheck the linked account's actual identity and selected-chat
binding. Message IDs, directions, timestamps and IR text parts come from the
provider. Opaque pagination cursors stay opaque. Unsupported attachments remain
explicitly unavailable. The provider has no refresh token; renewal means new
consent, not a claimed silent refresh.

`cloud.py` obtains a fresh owner access grant from the sync daemon for each
metadata operation. It reads only explicitly granted columns in
`users.integration_connections`. Writes use the registered fixed-shape
`users.personal_native_device_connection` database RPC: actor comes from
`auth.uid()`, the device must be active and owned by that actor, and no caller can
choose another provider, owner, organization, scope or secret custody. Existing
restrictive table policies remain intact. Connection updates compare the exact
last native revision and timestamp; zero rows mean a conflict requiring a fresh
read. Resource selection matches the same current parent and device-selected
chat. The cloud subject is a SHA-256 hash of the actual native user ID; raw native
user identity and OAuth credentials stay encrypted on the device.

The corresponding database declaration and rollback probes live in aidream's
`db/migrations/20261011223008_beeper_personal_device_connections.sql` and
`db/migrations/verification/`. That declaration must exist before customer
metadata writes. The unit fixtures prove protocol boundaries; only a real
customer consent/read/disconnect/reconnect run proves the customer lifecycle.
