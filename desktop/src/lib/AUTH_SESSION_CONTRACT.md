# Desktop authentication session contract

Supabase is the identity provider. The native `matrx-syncd` daemon is this device's
only general app session and refresh-token owner. It signs in with OAuth PKCE, stores the
refresh credential in the OS credential store, and renews it through the Supabase
OAuth token endpoint with the registered client ID. Ordinary sign-out clears this
device's session; it does not revoke other devices' sessions.

React and Python consume the daemon's access tokens. React's Supabase client uses
`accessToken: getToken`; `supabase.auth` is intentionally unavailable. There is no
auth-js session, refresh credential in localStorage, or React-to-Python token POST.
Embedded web applications retain their own session owner; URLs never copy access
or refresh credentials into another client.

`native-vault-auth.ts` publishes one daemon lifecycle envelope. Protected requests
wait for current account reconciliation and engine alignment, then re-read the
identity and token. Alignment also runs after engine discovery when the initial
auth event arrived before Python was available. Concurrent requests share that
alignment operation. An obsolete account or engine generation cannot be adopted.

`use-engine.ts` serializes configure/reconfigure, WebSocket connection, and
background startup. Refresh events reconnect through that same sequence. A local
connection failure preserves the daemon identity and offers Retry; a failed
account cleanup hides account-dependent UI until reconciliation succeeds.
AppLayout waits for discovery before mounting pages marked `requiresEngine`.

Python background index reads and writability scans must not block the async
request loop. Scanner helpers are owned children and are reaped on cancellation.
Rust OAuth parses both standard OAuth errors and Supabase `error_code`/`msg`;
definitively invalid refresh tokens enter sign-in-needed without an infinite retry.
Unknown failures and temporary outages remain recoverable.

Guards cover cold discovery, refresh, concurrent requests, stale account rejection,
failed alignment, retry, local tool requests, scanner responsiveness, and actual
Supabase refresh-error response shapes. Acceptance additionally requires real
Supabase OAuth, private daemon restart with OS credential restore, authenticated
engine responses, and the startup smoke harness. Unit tests alone do not prove a
packaged or deployed application.

Python reads an atomic token-and-owner grant for every cloud operation. Settings
sync checks that owner again before applying responses. The AI SDK accepts an
async token provider, so headless operations no longer depend on a startup cache
or an incoming React bearer. Scheduler HTTP requests also resolve the current
daemon grant. Realtime re-authenticates on account changes and sign-out as well
as token rotation.

The optional macOS credential-provider extension has a separately scoped OAuth
client and provider-only Keychain under its accepted native enrollment contract.
It is absent from host-only release builds and is not a second general app login.
Its unfinished signed-provider acceptance is not evidence about desktop session
custody or readiness.

The Python lifespan owns one daemon event listener. It subscribes before reading
its reconnect snapshot, retires the previous account's dependent services, and
restores scrape delivery, Vault keys, remote tool definitions, and the broadcast
subscription after sign-in or recovery. Failed adoption retries; shutdown cancels
and awaits its owned tasks. No listener asks React to push a token into Python.
