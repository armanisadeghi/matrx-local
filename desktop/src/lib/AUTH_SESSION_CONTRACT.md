# Desktop authentication session contract

Supabase is the identity provider. The native `matrx-syncd` daemon is this device's
only session and refresh-token owner. It signs in with OAuth PKCE, stores the
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
