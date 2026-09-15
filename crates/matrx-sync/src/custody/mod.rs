//! FS-C5 — credential custody. **The daemon is the device's only session holder.**
//!
//! Contract: `common-docs/projects/folder-sync/specs/SPEC-CUSTODY.md` @ `c5ae35ad` (S1–S21), under
//! `CONTRACT-RULINGS.md` C1/C3/C5/C7/C8/C9/C14 and DECISIONS D17/D18.
//!
//! Sign-in is an OAuth 2.1 authorization-code-with-PKCE transaction whose code verifier is
//! generated inside the daemon, never leaves it, and whose resulting **refresh token is written to
//! the OS keychain and rotated only by the daemon**. Every other process — the Tauri host, the
//! webview, the Python engine — obtains a short-lived access token from the control API and holds
//! it in memory only. **No second refresh token exists anywhere on the device**, so the MXL-D-046
//! class (a UI-pushed token that nothing headless can renew) is structurally impossible.
//!
//! # Module map
//!
//! | Module | What it owns |
//! |---|---|
//! | [`world`] | The one dev/live word, the daemon port band, the keychain service name. |
//! | [`pkce`] | The transaction: verifier, S256 challenge, 256-bit state, TTL, authorize URL. |
//! | [`store`] | The OS keychain item (§4) and `FakeKeychain`. |
//! | [`oauth`] | The two grants (§5) and `FakeAuthServer`. |
//! | [`session`] | The journal's `session_state` row (§8a) and the five session states. |
//! | [`cloud`] | §8b's write into this device's `files.sync_mappings` rows, and S16 branch B. |
//! | [`notify`] | S18's one OS notification per state entry. |
//! | [`loopback`] | S3's fixed loopback redirect listener, and the deep-link parser. |
//! | [`clock`] | S9/S10's monotonic scheduling seam. |

pub mod clock;
pub mod cloud;
pub mod error;
pub mod loopback;
pub mod notify;
pub mod oauth;
pub mod pkce;
pub mod session;
pub mod store;
pub mod world;

pub use clock::{rfc3339, Clock, SystemClock, TestClock};
pub use cloud::{CloudStateWriter, CloudWriteOutcome, FakeCloudStateWriter, PostgrestCloudStateWriter};
pub use error::{CustodyError, Result};
pub use loopback::{parse_deep_link, LoopbackCallback, LoopbackListener};
pub use notify::{Notifier, OsNotifier, RecordingNotifier};
pub use oauth::{FakeAuthServer, FakeFailure, OAuthProvider, SupabaseOAuth, TokenResponse};
pub use pkce::{RedirectKind, Transaction};
pub use session::{SessionRow, SessionState, SessionStore};
pub use store::{CredentialStore, FakeKeychain, KeyringStore, StoredCredential};
pub use world::World;

use crate::journal::Journal;
use chrono::{DateTime, Duration as ChronoDuration, Utc};
use serde::Serialize;
use std::sync::{Arc, Mutex as StdMutex};
use std::time::Duration;
use tokio::sync::{broadcast, Mutex};

/// The desktop app's registered public OAuth 2.1 client id.
///
/// VERIFIED `desktop/src/lib/oauth.ts`. It is a **public** client: no secret exists, and none is
/// ever sent.
pub const DESKTOP_CLIENT_ID: &str = "af37ec97-3e0c-423c-a205-3d6c5adc5645";

/// S11: `GET /v1/token` serves the cached access token while it has at least this much life.
const MIN_SERVED_LIFE: Duration = Duration::from_secs(60);

/// S11: the hand-out never blocks longer than this.
const HANDOUT_BUDGET: Duration = Duration::from_secs(5);

/// S10: at most one consumer-forced refresh per minute.
const FORCED_REFRESH_INTERVAL: Duration = Duration::from_secs(60);

/// How long any OS credential-store call may take before it is treated as unavailable (S7).
///
/// A keychain read is milliseconds. A keychain read that is **waiting for a human to approve it**
/// never returns — and on macOS an item's ACL is bound to the creating binary's designated
/// requirement, so a rebuilt or re-signed binary prompts. S15 names this: "a login-time daemon has
/// no UI to answer a prompt — that is MXL-D-046's shape wearing a new hat." Without this bound the
/// daemon simply hangs on start, holding a session it can neither use nor report. With it, the
/// hang becomes `credential_store_unavailable` and its remedy. Observed live on 2026-09-15: a
/// rebuilt dev binary blocked `resume()` indefinitely.
const CREDENTIAL_STORE_BUDGET: Duration = Duration::from_secs(3);

/// §5: jittered exponential backoff, 1 s → 5 min cap, forever.
const BACKOFF_FLOOR: Duration = Duration::from_secs(1);
const BACKOFF_CAP: Duration = Duration::from_secs(300);

/// S10: a wall-clock jump larger than this, relative to the monotonic clock, forces a refresh
/// rather than any reasoning about which clock is right.
const SKEW_TOLERANCE: Duration = Duration::from_secs(120);

/// SPEC-ENGINE §1.10's tick — also custody's wake and skew detector.
const TICK: Duration = Duration::from_secs(15);

/// Everything the custodian needs that is not code.
///
/// None of it comes from a `.env` at run time: the daemon is handed these by the app at start, or
/// falls back to the values compiled into the binary from the app's own bootstrap source of truth.
#[derive(Debug, Clone)]
pub struct CustodyConfig {
    /// Which world this custody belongs to. Two worlds never see each other's keychain items.
    pub world: World,
    /// e.g. `https://db.matrxserver.com`. Addressed only by URL, never by project ref.
    pub supabase_url: String,
    /// The publishable (anon) key — the `apikey` header on §8b's write. Not a secret.
    pub publishable_key: String,
    /// The registered public OAuth client id.
    pub client_id: String,
}

impl CustodyConfig {
    /// Refuse a configuration that cannot work, rather than failing later with a 401 nobody can
    /// read.
    pub fn validate(&self) -> Result<()> {
        if !self.supabase_url.starts_with("https://") {
            return Err(CustodyError::Configuration {
                setting: "supabase url",
                detail: format!(
                    "{:?} is not an https URL; the database is addressed only by URL, never by \
                     project ref",
                    self.supabase_url
                ),
            });
        }
        if self.publishable_key.is_empty() {
            return Err(CustodyError::Configuration {
                setting: "supabase publishable key",
                detail: "it is empty".into(),
            });
        }
        if self.client_id.is_empty() {
            return Err(CustodyError::Configuration {
                setting: "oauth client id",
                detail: "it is empty".into(),
            });
        }
        Ok(())
    }
}

/// What `POST /v1/sign-in` hands back. The verifier is **not** in it.
#[derive(Debug, Clone, Serialize)]
pub struct SignInStart {
    /// The opaque transaction id.
    pub transaction_id: String,
    /// The URL the host opens in the SYSTEM browser.
    pub authorize_url: String,
    /// The redirect URI this transaction was opened against.
    pub redirect_uri: String,
    /// Which leg it took.
    pub redirect_kind: &'static str,
}

/// What `POST /v1/sign-in/callback` hands back.
#[derive(Debug, Clone, Serialize)]
pub struct SignedIn {
    /// Supabase user id (uuid).
    pub user_id: String,
    /// The account's email, for every "signed in as …" surface.
    pub email: String,
}

/// A granted access token (`GET /v1/token` 200).
#[derive(Debug, Clone, Serialize)]
pub struct TokenGrant {
    /// The short-lived Supabase JWT.
    pub access_token: String,
    /// RFC3339. A consumer caches in memory until this minus 30 s and never persists (S11, S12).
    pub expires_at: String,
    /// Supabase user id (uuid).
    pub user_id: String,
    /// Always `Bearer`.
    pub token_type: &'static str,
}

/// A refusal (`GET /v1/token` 409). Never a 500, never an empty 200 — a consumer's switch over the
/// four refusal states plus the 200 is exhaustive (A5).
#[derive(Debug, Clone, Serialize)]
pub struct SessionRefusal {
    /// One of `sign_in_needed`, `signed_out`, `offline`, `credential_store_unavailable`.
    pub state: SessionState,
    /// The remedy sentence every surface shows verbatim.
    pub state_reason: String,
    /// RFC3339 — when this state was entered.
    pub since: String,
    /// For "Sign back in as …", when the device remembers who it was.
    pub email: Option<String>,
}

/// The `GET /v1/session` payload — what a surface reads on mount, before the first
/// `session.changed` arrives.
#[derive(Debug, Clone, Serialize)]
pub struct SessionSnapshot {
    /// Whether a usable session exists right now.
    pub signed_in: bool,
    /// Supabase user id (uuid), when known.
    pub user_id: Option<String>,
    /// The account's email, when known.
    pub email: Option<String>,
    /// The honest state.
    pub state: SessionState,
    /// The remedy sentence.
    pub state_reason: Option<String>,
    /// RFC3339 — when this state was entered.
    pub since: String,
    /// What the `offline` state shows the user.
    pub next_attempt_at: Option<String>,
    /// S16 branch B: this device's cloud rows do not yet reflect the state.
    pub cloud_state_write_pending: bool,
}

/// The event custody publishes on SPEC-ENGINE's one SSE stream (C9): `session.changed`.
///
/// It carries the snapshot, never a token. The webview's `supabase.realtime.setAuth()` re-auth
/// depends on it (S17/A4): realtime-js calls the `accessToken` callback on connect and on
/// resubscribe but **never on a timer**, so a socket open past the hour would carry a dead JWT.
#[derive(Debug, Clone, Serialize)]
pub struct SessionChanged {
    /// The snapshot as of the change.
    pub session: SessionSnapshot,
    /// True when the change was a token rotation rather than a state change — the signal the
    /// webview turns into `supabase.realtime.setAuth()`.
    pub rotated: bool,
}

/// The live half of a session. **Held in memory only** — the access token is never on disk (§4).
#[derive(Debug, Clone)]
struct LiveSession {
    user_id: String,
    email: String,
    /// The one refresh token on the device; the keychain item is its only durable copy.
    refresh_token: String,
    access_token: String,
    /// Monotonic reading at which the access token dies. Scheduling uses this (S9, S10).
    expires_mono: Duration,
    /// Wall-clock expiry, for display and for the journal only.
    expires_wall: DateTime<Utc>,
    /// Monotonic reading at which a rotation is due (S9).
    refresh_at_mono: Duration,
    issued_at: String,
}

struct Inner {
    config: CustodyConfig,
    oauth: Arc<dyn OAuthProvider>,
    store: Arc<dyn CredentialStore>,
    cloud: Arc<dyn CloudStateWriter>,
    notifier: Arc<dyn Notifier>,
    clock: Arc<dyn Clock>,
    sessions: SessionStore,
    journal: Arc<StdMutex<Journal>>,
    /// The whole mutable session, behind one lock. A concurrent `GET /v1/token` awaits the same
    /// rotation rather than starting a second one (S8): one refresher, single-flight.
    live: Mutex<Option<LiveSession>>,
    /// At most one live transaction per user (S5). A second `POST /v1/sign-in` cancels the first.
    transaction: StdMutex<Option<Transaction>>,
    /// The loopback listener serving the live transaction, when it took that leg. Cancelling a
    /// transaction must release its socket too: the redirect port is FIXED (exact matching, §3.1),
    /// so a leaked listener makes every subsequent sign-in fail with `loopback_port_unavailable`
    /// until the daemon restarts. Found by running it, not by reading it.
    loopback: StdMutex<Option<tokio::task::JoinHandle<()>>>,
    events: broadcast::Sender<SessionChanged>,
    /// The monotonic reading of the last consumer-forced refresh (S10).
    last_forced: StdMutex<Option<Duration>>,
    /// Wall/monotonic pair at the last tick — the skew and wake detector (S10, §1.10).
    last_tick: StdMutex<Option<(DateTime<Utc>, Duration)>>,
}

/// The device's only session holder.
#[derive(Clone)]
pub struct Custodian {
    inner: Arc<Inner>,
}

impl std::fmt::Debug for Custodian {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Custodian")
            .field("world", &self.inner.config.world)
            .finish_non_exhaustive()
    }
}

impl Custodian {
    /// Assemble a custodian from its seams. Every seam is a trait so the §13 battery can drive the
    /// whole state machine with no network, no keychain and no clock.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        config: CustodyConfig,
        journal: Arc<StdMutex<Journal>>,
        oauth: Arc<dyn OAuthProvider>,
        store: Arc<dyn CredentialStore>,
        cloud: Arc<dyn CloudStateWriter>,
        notifier: Arc<dyn Notifier>,
        clock: Arc<dyn Clock>,
    ) -> Result<Self> {
        config.validate()?;
        let (events, _) = broadcast::channel(64);
        Ok(Custodian {
            inner: Arc::new(Inner {
                config,
                oauth,
                store,
                cloud,
                notifier,
                clock,
                sessions: SessionStore::new(Arc::clone(&journal)),
                journal,
                live: Mutex::new(None),
                transaction: StdMutex::new(None),
                loopback: StdMutex::new(None),
                events,
                last_forced: StdMutex::new(None),
                last_tick: StdMutex::new(None),
            }),
        })
    }

    /// Build the production custodian: real OAuth, real keychain, real PostgREST, real notifier.
    pub fn production(
        config: CustodyConfig,
        journal: Arc<StdMutex<Journal>>,
    ) -> Result<Self> {
        config.validate()?;
        let oauth = Arc::new(SupabaseOAuth::new(&config.supabase_url, &config.client_id)?);
        let store = Arc::new(KeyringStore::new(config.world));
        let cloud = Arc::new(PostgrestCloudStateWriter::new(
            &config.supabase_url,
            &config.publishable_key,
        )?);
        let notifier = Arc::new(OsNotifier::new("AI Matrx Sync"));
        Custodian::new(
            config,
            journal,
            oauth,
            store,
            cloud,
            notifier,
            Arc::new(SystemClock::new()),
        )
    }

    /// Subscribe to `session.changed`.
    pub fn subscribe(&self) -> broadcast::Receiver<SessionChanged> {
        self.inner.events.subscribe()
    }

    /// The world this custody belongs to.
    pub fn world(&self) -> World {
        self.inner.config.world
    }

    // ------------------------------------------------------------------ start

    /// Adopt whatever the keychain holds and rotate immediately (S9's "immediate refresh on daemon
    /// start"). Never fails the daemon: a keychain that will not open is a named state (S7).
    pub async fn resume(&self) -> SessionSnapshot {
        let row = self.inner.sessions.read().ok().flatten();
        let known_user = row.as_ref().and_then(|r| r.user_id.clone());

        let Some(user_id) = known_user else {
            // Nothing was ever signed in on this journal. Say so as a state, not as silence.
            if row.is_none() {
                let now = rfc3339(self.inner.clock.now_wall());
                let _ = self
                    .inner
                    .sessions
                    .write(&SessionRow::signed_out(self.inner.config.world, &now));
            }
            return self.session().await;
        };

        let loaded = {
            let store = Arc::clone(&self.inner.store);
            let id = user_id.clone();
            bounded_store_call("read the credential item", async move {
                tokio::task::spawn_blocking(move || store.load(&id)).await
            })
            .await
        };

        match loaded {
            Ok(Some(credential)) => {
                {
                    let mut live = self.inner.live.lock().await;
                    *live = Some(LiveSession {
                        user_id: credential.user_id.clone(),
                        email: credential.email.clone(),
                        refresh_token: credential.refresh_token.clone(),
                        access_token: String::new(),
                        expires_mono: self.inner.clock.now_mono(),
                        expires_wall: self.inner.clock.now_wall(),
                        refresh_at_mono: self.inner.clock.now_mono(),
                        issued_at: credential.issued_at.clone(),
                    });
                }
                // S9: an immediate refresh on daemon start. A failure here is a state, never a
                // reason not to start.
                let _ = self.token().await;
            }
            Ok(None) => {
                // The journal remembers a user but the keychain item is gone — the machine was
                // wiped, or another tool deleted it. That is `sign_in_needed`, stated plainly.
                self.enter_state(
                    SessionState::SignInNeeded,
                    &format!(
                        "Sign back in as {} on this computer to resume syncing.",
                        row.as_ref()
                            .and_then(|r| r.email.clone())
                            .unwrap_or_else(|| "this account".into())
                    ),
                    Some(user_id),
                    row.as_ref().and_then(|r| r.email.clone()),
                )
                .await;
            }
            Err(e) => {
                self.enter_state(
                    SessionState::CredentialStoreUnavailable,
                    e.remedy(),
                    Some(user_id),
                    row.as_ref().and_then(|r| r.email.clone()),
                )
                .await;
            }
        }
        self.session().await
    }

    /// Run the refresh loop until the returned handle's task is cancelled.
    ///
    /// Exactly one task in one process rotates (S8). The loop ticks every 15 s — SPEC-ENGINE
    /// §1.10's tick — which is also the wake and clock-skew detector (S10).
    pub fn spawn_refresh_loop(&self) -> tokio::task::JoinHandle<()> {
        let custodian = self.clone();
        tokio::spawn(async move {
            loop {
                tokio::time::sleep(TICK).await;
                custodian.tick().await;
            }
        })
    }

    /// One pass of the refresh loop. Public so the battery can drive it without waiting 15 s.
    pub async fn tick(&self) {
        let wall = self.inner.clock.now_wall();
        let mono = self.inner.clock.now_mono();

        // Wake and skew detection: compare the wall delta to the monotonic delta. `wall − mono`
        // over the tolerance means the machine slept or the clock jumped; either way the answer is
        // an immediate rotation rather than reasoning about which clock is right (S10, §1.10).
        let jumped = {
            let mut last = self.inner.last_tick.lock().expect("tick lock");
            let jumped = match *last {
                Some((prev_wall, prev_mono)) => {
                    let wall_delta = (wall - prev_wall).num_milliseconds().max(0) as u64;
                    let mono_delta = mono.saturating_sub(prev_mono).as_millis() as u64;
                    wall_delta.abs_diff(mono_delta) > SKEW_TOLERANCE.as_millis() as u64
                }
                None => false,
            };
            *last = Some((wall, mono));
            jumped
        };

        let due = {
            let live = self.inner.live.lock().await;
            match live.as_ref() {
                Some(s) => jumped || mono >= s.refresh_at_mono,
                None => false,
            }
        };
        if due {
            if jumped {
                // S10: the machine slept or the clock was corrected. Bring the schedule to now so
                // the rotation actually happens instead of being coalesced away.
                let mut live = self.inner.live.lock().await;
                if let Some(s) = live.as_mut() {
                    s.refresh_at_mono = mono;
                }
            }
            let _ = self.rotate().await;
        }
    }

    // --------------------------------------------------------------- sign-in

    /// S1/S2/S3/S5 — open a transaction and return the URL the host opens in the system browser.
    ///
    /// A second call cancels the first: at most one live transaction per user.
    pub async fn begin_sign_in(&self, prefer: Option<RedirectKind>) -> Result<SignInStart> {
        let world = self.inner.config.world;
        let (redirect_uri, kind) = match prefer.unwrap_or(match world {
            World::Live => RedirectKind::DeepLink,
            World::Dev => RedirectKind::Loopback,
        }) {
            RedirectKind::DeepLink => (
                pkce::DEEP_LINK_REDIRECT_URI.to_string(),
                RedirectKind::DeepLink,
            ),
            RedirectKind::Loopback => (world.loopback_redirect_uri(), RedirectKind::Loopback),
        };

        let transaction = Transaction::open(redirect_uri, kind, self.inner.clock.now_mono())?;
        let start = SignInStart {
            transaction_id: transaction.transaction_id.clone(),
            authorize_url: transaction
                .authorize_url(&self.inner.config.supabase_url, &self.inner.config.client_id),
            redirect_uri: transaction.redirect_uri.clone(),
            redirect_kind: kind.as_str(),
        };

        // Cancelling the previous transaction (S5) must release its listener before the new one
        // binds the same fixed port.
        self.stop_loopback().await;

        // The loopback leg binds the fixed port BEFORE the browser opens, so the redirect can
        // never arrive at a closed socket. A port already held is named, not silently retried.
        if kind == RedirectKind::Loopback {
            let listener = LoopbackListener::bind(world.oauth_callback_port()).await?;
            let custodian = self.clone();
            let task = tokio::spawn(async move {
                match listener.accept_callback().await {
                    Ok(callback) => {
                        // Clear our own handle FIRST. `accept_callback` consumed the listener, so
                        // the port is already released — and `complete_sign_in` calls
                        // `stop_loopback`, which would otherwise abort THIS task in the middle of
                        // the token exchange and lose the sign-in silently. Found by running it.
                        {
                            let mut slot = custodian.inner.loopback.lock().expect("loopback lock");
                            *slot = None;
                        }
                        if let Err(e) = custodian.complete_sign_in(&callback.code, &callback.state).await {
                            log_line(&format!("loopback sign-in could not complete: {e}"));
                        } else {
                            log_line("loopback sign-in completed");
                        }
                    }
                    Err(e) => log_line(&format!("loopback sign-in listener stopped: {e}")),
                }
            });
            *self.inner.loopback.lock().expect("loopback lock") = Some(task);
        }

        *self.inner.transaction.lock().expect("transaction lock") = Some(transaction);
        Ok(start)
    }

    /// S1/S5/S14 — exchange the code the host forwarded. **The daemon holds the verifier**; the
    /// webview never receives a code, a verifier, or a refresh token.
    pub async fn complete_sign_in(&self, code: &str, state: &str) -> Result<SignedIn> {
        // Take the transaction before the first network await, as today's code already does.
        let transaction = {
            let mut slot = self.inner.transaction.lock().expect("transaction lock");
            match slot.as_ref() {
                Some(t)
                    if pkce::state_matches(&t.state, state)
                        && !t.expired(self.inner.clock.now_mono()) =>
                {
                    slot.take().expect("checked above")
                }
                // An unknown or expired state is refused honestly — never silently accepted, and
                // never silently dropped. S14: the host then tries the other world's daemon.
                _ => return Err(CustodyError::UnknownTransaction),
            }
        };
        // The transaction is spent; its listener has no reason to hold the fixed port.
        self.stop_loopback().await;

        let response = self
            .inner
            .oauth
            .exchange_code(code, &transaction.code_verifier, &transaction.redirect_uri)
            .await?;

        let refresh_token = response.refresh_token.clone().ok_or_else(|| {
            CustodyError::AmbiguousResponse {
                status: 200,
                detail: "the sign-in response carried no refresh token, so this device could \
                         never renew its session"
                    .into(),
            }
        })?;
        let identity = oauth::identity_from_jwt(&response.access_token)?;
        let now_wall = self.inner.clock.now_wall();
        let now_str = rfc3339(now_wall);

        // An account switch suspends nothing here, but it must be visible: signing in as a
        // different user than the journal's last one is stated, never silently swapped (§9).
        if let Some(previous) = self
            .inner
            .sessions
            .read()
            .ok()
            .flatten()
            .and_then(|r| r.user_id)
        {
            if previous != identity.user_id {
                log_line(&format!(
                    "signing in as a different account than this device last held \
                     (was {previous}, now {})",
                    identity.user_id
                ));
            }
        }

        // S8's write-ahead rule: the refresh token reaches the keychain BEFORE any access token is
        // handed to anybody.
        let credential = StoredCredential {
            v: StoredCredential::VERSION,
            refresh_token: refresh_token.clone(),
            user_id: identity.user_id.clone(),
            email: identity.email.clone(),
            client_id: self.inner.config.client_id.clone(),
            issued_at: now_str.clone(),
            rotated_at: now_str.clone(),
            world: self.inner.config.world,
        };
        if let Err(e) = self.save_credential(credential).await {
            // S7: no disk fallback. The session runs in memory for this process and says so.
            self.enter_state(
                SessionState::CredentialStoreUnavailable,
                e.remedy(),
                Some(identity.user_id.clone()),
                Some(identity.email.clone()),
            )
            .await;
        }

        // A sign-in puts a brand-new access token in the daemon's hand, which is exactly the
        // condition the webview turns into `supabase.realtime.setAuth()` (S17/A4) — so it is
        // published as a rotation, through the same path a refresh uses.
        let adopted = self
            .adopt(&identity.user_id, &identity.email, &refresh_token, &response, &now_str)
            .await;
        self.record_success(&adopted).await;

        Ok(SignedIn {
            user_id: identity.user_id,
            email: identity.email,
        })
    }

    // ---------------------------------------------------------------- tokens

    /// `GET /v1/token` — the hand-out (S11, S12).
    ///
    /// Serves the cached access token while it has at least 60 s of life; otherwise joins the
    /// single-flight rotation. Never blocks longer than 5 s and never returns an expired token.
    pub async fn token(&self) -> std::result::Result<TokenGrant, SessionRefusal> {
        match tokio::time::timeout(HANDOUT_BUDGET, self.token_inner()).await {
            Ok(result) => result,
            // The rotation is still running. The honest answer is the state the loop is already
            // publishing, not a fabricated token and not a 500.
            Err(_) => Err(self.refusal(SessionState::Offline).await),
        }
    }

    async fn token_inner(&self) -> std::result::Result<TokenGrant, SessionRefusal> {
        {
            let live = self.inner.live.lock().await;
            if let Some(s) = live.as_ref() {
                let now = self.inner.clock.now_mono();
                if !s.access_token.is_empty() && s.expires_mono.saturating_sub(now) >= MIN_SERVED_LIFE
                {
                    return Ok(TokenGrant {
                        access_token: s.access_token.clone(),
                        expires_at: rfc3339(s.expires_wall),
                        user_id: s.user_id.clone(),
                        token_type: "Bearer",
                    });
                }
            } else {
                drop(live);
                return Err(self.refusal(SessionState::SignedOut).await);
            }
        }

        match self.rotate().await {
            Ok(grant) => Ok(grant),
            Err(e) => Err(self.refusal(state_for(&e)).await),
        }
    }

    /// S10 — a consumer's 401 may force one rotation, rate-limited to one per 60 s.
    pub async fn force_refresh(&self) -> std::result::Result<TokenGrant, SessionRefusal> {
        let now = self.inner.clock.now_mono();
        let allowed = {
            let mut last = self.inner.last_forced.lock().expect("forced lock");
            let allowed = match *last {
                Some(prev) => now.saturating_sub(prev) >= FORCED_REFRESH_INTERVAL,
                None => true,
            };
            if allowed {
                *last = Some(now);
            }
            allowed
        };
        if !allowed {
            // Not an error: the caller gets the token we have, which is what the rate limit exists
            // to protect. Saying "refused" here would push a consumer into a retry storm.
            return self.token().await;
        }
        {
            let mut live = self.inner.live.lock().await;
            if let Some(s) = live.as_mut() {
                s.refresh_at_mono = now;
                s.expires_mono = now;
            }
        }
        self.token().await
    }

    /// One rotation, behind the one lock (S8). Concurrent callers await this same rotation.
    async fn rotate(&self) -> Result<TokenGrant> {
        let mut live = self.inner.live.lock().await;
        let Some(session) = live.as_ref().cloned() else {
            return Err(CustodyError::NotSignedIn);
        };

        // Another caller may have rotated while we waited for the lock. The test is whether the
        // NEXT rotation is still in the future — a peer's successful rotation pushes
        // `refresh_at_mono` forward — not merely whether the token has life left, which would make
        // a scheduled rotation unreachable for the whole first 0.6 of every token's life.
        let now = self.inner.clock.now_mono();
        if !session.access_token.is_empty()
            && now < session.refresh_at_mono
            && session.expires_mono.saturating_sub(now) >= MIN_SERVED_LIFE
        {
            return Ok(TokenGrant {
                access_token: session.access_token.clone(),
                expires_at: rfc3339(session.expires_wall),
                user_id: session.user_id.clone(),
                token_type: "Bearer",
            });
        }

        let attempt_at = rfc3339(self.inner.clock.now_wall());
        let response = match self.inner.oauth.refresh(&session.refresh_token).await {
            Ok(r) => r,
            Err(e) if e.retryable() => {
                drop(live);
                self.record_retryable_failure(&e, &attempt_at).await;
                return Err(e);
            }
            Err(e) => {
                drop(live);
                // The terminal path. S16's order of operations is inside.
                self.go_terminal(&session, &e).await;
                return Err(e);
            }
        };

        // S8's write-ahead rule: the NEW refresh token reaches the keychain before the new access
        // token is handed to anybody. A failed write is a failed rotation, and the old token is
        // NOT reused — presenting a rotated-away token twice is what terminates a session.
        let rotated_token = response.refresh_token.clone();
        if let Some(new_refresh) = rotated_token.clone() {
            let now_str = rfc3339(self.inner.clock.now_wall());
            let credential = StoredCredential {
                v: StoredCredential::VERSION,
                refresh_token: new_refresh,
                user_id: session.user_id.clone(),
                email: session.email.clone(),
                client_id: self.inner.config.client_id.clone(),
                issued_at: session.issued_at.clone(),
                rotated_at: now_str,
                world: self.inner.config.world,
            };
            if let Err(e) = self.save_credential(credential).await {
                drop(live);
                self.enter_state(
                    SessionState::CredentialStoreUnavailable,
                    e.remedy(),
                    Some(session.user_id.clone()),
                    Some(session.email.clone()),
                )
                .await;
                return Err(e);
            }
        }

        let (expires_mono, expires_wall, refresh_at) = self.schedule(response.expires_in);
        let updated = LiveSession {
            user_id: session.user_id.clone(),
            email: session.email.clone(),
            refresh_token: rotated_token.unwrap_or(session.refresh_token.clone()),
            access_token: response.access_token.clone(),
            expires_mono,
            expires_wall,
            refresh_at_mono: refresh_at,
            issued_at: session.issued_at.clone(),
        };
        *live = Some(updated.clone());
        drop(live);

        self.record_success(&updated).await;
        Ok(TokenGrant {
            access_token: updated.access_token,
            expires_at: rfc3339(updated.expires_wall),
            user_id: updated.user_id,
            token_type: "Bearer",
        })
    }

    /// S9 — schedule from the access token's OWN lifetime, never from the session's.
    ///
    /// Refresh at `issued + 0.6 × expires_in`, or 5 minutes before expiry, whichever is sooner.
    /// This is the direct guard against MXL-D-046, whose comment records the failure verbatim: a
    /// stored expiry carrying the SESSION expiry (~7 days) while the access token was already dead
    /// (`app/services/local_db/repositories.py`).
    fn schedule(&self, expires_in: i64) -> (Duration, DateTime<Utc>, Duration) {
        let now_mono = self.inner.clock.now_mono();
        let life = Duration::from_secs(expires_in.max(0) as u64);
        let at_60_percent = life.mul_f64(0.6);
        let five_before = life.saturating_sub(Duration::from_secs(300));
        // `max(30 s)` so a pathologically short token cannot produce a hot loop; the floor is
        // stated rather than hidden.
        let ahead = at_60_percent.min(five_before).max(Duration::from_secs(30));
        (
            now_mono + life,
            self.inner.clock.now_wall() + ChronoDuration::seconds(expires_in.max(0)),
            now_mono + ahead,
        )
    }

    // ---------------------------------------------------------------- states

    async fn record_success(&self, session: &LiveSession) {
        let now = rfc3339(self.inner.clock.now_wall());
        let mut row = self
            .inner
            .sessions
            .read()
            .ok()
            .flatten()
            .unwrap_or_else(|| SessionRow::signed_out(self.inner.config.world, &now));
        let changed = row.state != SessionState::SignedIn;
        if changed {
            row.since = now.clone();
            row.notified_state = None;
            row.notified_since = None;
            // The pending write belonged to the state this one replaces; carrying it forward would
            // make the snapshot claim the cloud is behind when there is nothing left to write.
            row.cloud_state_write_pending = false;
        }
        row.state = SessionState::SignedIn;
        row.state_reason = Some("Signed in and syncing.".into());
        row.user_id = Some(session.user_id.clone());
        row.email = Some(session.email.clone());
        row.world = self.inner.config.world;
        row.access_token_expires_at = Some(rfc3339(session.expires_wall));
        row.last_refresh_at = Some(now.clone());
        row.last_attempt_at = Some(now.clone());
        row.next_attempt_at = None;
        row.failure_count = 0;
        row.updated_at = now;
        let _ = self.inner.sessions.write(&row);
        self.publish(true).await;
    }

    async fn record_retryable_failure(&self, error: &CustodyError, attempt_at: &str) {
        let now = rfc3339(self.inner.clock.now_wall());
        let mut row = self
            .inner
            .sessions
            .read()
            .ok()
            .flatten()
            .unwrap_or_else(|| SessionRow::signed_out(self.inner.config.world, &now));
        let failures = row.failure_count.saturating_add(1);
        let backoff = backoff_for(failures);
        if row.state != SessionState::Offline {
            row.since = now.clone();
            row.notified_state = None;
            row.notified_since = None;
        }
        row.state = SessionState::Offline;
        row.state_reason = Some(format!(
            "Not connected — retrying. ({}) {}",
            error,
            error.remedy()
        ));
        row.failure_count = failures;
        row.last_attempt_at = Some(attempt_at.to_string());
        row.next_attempt_at = Some(rfc3339(
            self.inner.clock.now_wall() + ChronoDuration::from_std(backoff).unwrap_or_default(),
        ));
        row.updated_at = now;
        let _ = self.inner.sessions.write(&row);

        // The retry is the loop's, not a sleeping task's: the next tick sees `refresh_at_mono`.
        {
            let mut live = self.inner.live.lock().await;
            if let Some(s) = live.as_mut() {
                s.refresh_at_mono = self.inner.clock.now_mono() + backoff;
            }
        }
        self.publish(false).await;
    }

    /// S16 — the state is written BEFORE the session is classified terminal, on the still-valid
    /// access token, in this exact order.
    async fn go_terminal(&self, session: &LiveSession, error: &CustodyError) {
        let now = rfc3339(self.inner.clock.now_wall());
        let reason = format!(
            "Sign back in as {} on this computer to resume syncing.",
            if session.email.is_empty() {
                "this account"
            } else {
                &session.email
            }
        );

        // (1) the cloud rows, with the token still in hand.
        let write_pending = if session.access_token.is_empty() {
            true
        } else {
            !self
                .write_cloud_state(
                    &session.access_token,
                    SessionState::SignInNeeded,
                    &reason,
                    &now,
                )
                .await
        };

        // (2) the journal state row.
        let mut row = self
            .inner
            .sessions
            .read()
            .ok()
            .flatten()
            .unwrap_or_else(|| SessionRow::signed_out(self.inner.config.world, &now));
        if row.state != SessionState::SignInNeeded {
            row.since = now.clone();
            row.notified_state = None;
            row.notified_since = None;
        }
        row.state = SessionState::SignInNeeded;
        row.state_reason = Some(reason.clone());
        row.user_id = Some(session.user_id.clone());
        row.email = Some(session.email.clone());
        row.world = self.inner.config.world;
        row.access_token_expires_at = None;
        row.last_attempt_at = Some(now.clone());
        row.next_attempt_at = None;
        row.failure_count = row.failure_count.saturating_add(1);
        row.cloud_state_write_pending = write_pending;
        if !write_pending {
            row.cloud_state_written_at = Some(now.clone());
        }
        row.updated_at = now.clone();
        let _ = self.inner.sessions.write(&row);
        log_line(&format!(
            "session lost terminally ({error}); cloud_state_write_pending={write_pending}"
        ));

        // (3) the OS notification, once per state entry (S18).
        self.notify_once(&mut row).await;

        // (4) delete the now-worthless refresh token, keeping user_id/email in the journal so the
        // prompt can still say who to sign back in as.
        if let Err(e) = self.delete_credential(session.user_id.clone()).await {
            log_line(&format!("the worthless refresh token could not be deleted: {e}"));
        }

        // (5) drop the access token.
        *self.inner.live.lock().await = None;
        self.publish(false).await;
    }

    /// `POST /v1/sign-out` (§9, S20). **No server-side revocation**: the only documented
    /// revocation on this path is per grant, and a grant is per (user, client) — revoking would
    /// sign the user out of every Matrx device they own, silently. What the system does instead of
    /// lying about a revocation is state it.
    pub async fn sign_out(&self) -> Result<()> {
        let session = self.inner.live.lock().await.clone();
        let now = rfc3339(self.inner.clock.now_wall());
        let reason = "Signed out on this device — sign in again to resume syncing.";

        // In order: the mapping rows on the still-valid token, leaving `desired_state` untouched
        // so a later sign-in resumes exactly what the user had chosen (C4).
        let mut write_pending = true;
        if let Some(s) = session.as_ref() {
            if !s.access_token.is_empty() {
                write_pending = !self
                    .write_cloud_state(&s.access_token, SessionState::SignedOut, reason, &now)
                    .await;
            }
        }

        // Then the keychain item.
        let known_user = session
            .as_ref()
            .map(|s| s.user_id.clone())
            .or_else(|| self.inner.sessions.read().ok().flatten().and_then(|r| r.user_id));
        if let Some(user_id) = known_user.clone() {
            self.delete_credential(user_id).await?;
        }

        // Then the in-memory access token and the single-flight cache.
        *self.inner.live.lock().await = None;
        *self.inner.transaction.lock().expect("transaction lock") = None;
        self.stop_loopback().await;

        let mut row = self
            .inner
            .sessions
            .read()
            .ok()
            .flatten()
            .unwrap_or_else(|| SessionRow::signed_out(self.inner.config.world, &now));
        row.state = SessionState::SignedOut;
        row.state_reason = Some(reason.to_string());
        row.since = now.clone();
        // The email is kept so the sign-in screen can offer the account back; the user id is kept
        // so a re-sign-in as the same account is recognised as a resume, not a switch.
        row.user_id = known_user;
        row.access_token_expires_at = None;
        row.next_attempt_at = None;
        row.failure_count = 0;
        row.cloud_state_write_pending = write_pending && session.is_some();
        row.notified_state = None;
        row.notified_since = None;
        row.updated_at = now;
        self.inner.sessions.write(&row)?;
        self.publish(false).await;
        Ok(())
    }

    /// Enter a state that is not the product of a rotation (start-up adoption, keychain refusal).
    async fn enter_state(
        &self,
        state: SessionState,
        reason: &str,
        user_id: Option<String>,
        email: Option<String>,
    ) {
        let now = rfc3339(self.inner.clock.now_wall());
        let mut row = self
            .inner
            .sessions
            .read()
            .ok()
            .flatten()
            .unwrap_or_else(|| SessionRow::signed_out(self.inner.config.world, &now));
        if row.state != state {
            row.since = now.clone();
            row.notified_state = None;
            row.notified_since = None;
        }
        row.state = state;
        row.state_reason = Some(reason.to_string());
        if user_id.is_some() {
            row.user_id = user_id;
        }
        if email.is_some() {
            row.email = email;
        }
        row.world = self.inner.config.world;
        row.updated_at = now;
        let _ = self.inner.sessions.write(&row);
        if state.notifies() {
            self.notify_once(&mut row).await;
        }
        self.publish(false).await;
    }

    /// S18 — one notification per state entry, deduplicated by `(state, since)`.
    async fn notify_once(&self, row: &mut SessionRow) {
        if !row.state.notifies() || row.already_notified() {
            return;
        }
        let title = match row.state {
            SessionState::SignInNeeded => "AI Matrx needs you to sign in",
            SessionState::CredentialStoreUnavailable => "AI Matrx cannot reach your keychain",
            _ => return,
        };
        let body = row
            .state_reason
            .clone()
            .unwrap_or_else(|| "Open AI Matrx to continue.".into());
        match self.inner.notifier.post(title, &body) {
            Ok(()) => {
                row.notified_state = Some(row.state.as_str().to_string());
                row.notified_since = Some(row.since.clone());
                let _ = self.inner.sessions.write(row);
            }
            // The notification is one of five surfaces; a refusal is logged and the state stands.
            Err(e) => log_line(&format!("could not post the state notification: {e}")),
        }
    }

    /// §8b's write. Returns whether it landed.
    async fn write_cloud_state(
        &self,
        access_token: &str,
        state: SessionState,
        reason: &str,
        at: &str,
    ) -> bool {
        let Some(device_id) = self.device_id() else {
            log_line(
                "no device is registered on this journal yet, so this device owns no sync rows to \
                 update — recorded, not pretended",
            );
            return false;
        };
        match self
            .inner
            .cloud
            .write_mapping_state(access_token, &device_id, state, reason, at)
            .await
        {
            Ok(CloudWriteOutcome::Written { rows }) => {
                log_line(&format!("wrote {state:?} to {rows} sync mapping row(s)"));
                true
            }
            Ok(CloudWriteOutcome::NoDeviceRegistered) => false,
            Ok(CloudWriteOutcome::TablePending { detail }) => {
                // S16 branch B: the table or the RPC is absent on this server build. Named, never
                // silent, and never fatal.
                log_line(&format!("cloud_state_write_pending — {detail}"));
                false
            }
            Err(e) => {
                log_line(&format!("cloud_state_write_pending — {e}"));
                false
            }
        }
    }

    /// `public.app_instances.id` for this device (C13), as the journal's `device` row records it.
    fn device_id(&self) -> Option<String> {
        let journal = self.inner.journal.lock().expect("journal mutex");
        journal
            .connection()
            .query_row(
                "SELECT app_instance_id FROM device WHERE id = 'singleton'",
                [],
                |r| r.get::<_, String>(0),
            )
            .ok()
            .filter(|id| !id.is_empty())
    }

    // -------------------------------------------------------------- snapshot

    /// `GET /v1/session`.
    pub async fn session(&self) -> SessionSnapshot {
        let now = rfc3339(self.inner.clock.now_wall());
        let row = self
            .inner
            .sessions
            .read()
            .ok()
            .flatten()
            .unwrap_or_else(|| SessionRow::signed_out(self.inner.config.world, &now));
        SessionSnapshot {
            signed_in: row.state == SessionState::SignedIn,
            user_id: row.user_id.clone(),
            email: row.email.clone(),
            state: row.state,
            state_reason: row.state_reason.clone(),
            since: row.since.clone(),
            next_attempt_at: row.next_attempt_at.clone(),
            cloud_state_write_pending: row.cloud_state_write_pending,
        }
    }

    async fn refusal(&self, fallback: SessionState) -> SessionRefusal {
        let snapshot = self.session().await;
        // The journal's state wins whenever it is already a refusal; the fallback only names a
        // condition the row has not caught up with yet.
        let state = match snapshot.state {
            SessionState::SignedIn => fallback,
            other => other,
        };
        SessionRefusal {
            state_reason: snapshot
                .state_reason
                .clone()
                .unwrap_or_else(|| default_reason(state).to_string()),
            state,
            since: snapshot.since,
            email: snapshot.email,
        }
    }

    async fn publish(&self, rotated: bool) {
        // A receiverless broadcast is normal — nobody is listening before the first SSE client.
        let _ = self.inner.events.send(SessionChanged {
            session: self.session().await,
            rotated,
        });
    }

    async fn adopt(
        &self,
        user_id: &str,
        email: &str,
        refresh_token: &str,
        response: &TokenResponse,
        issued_at: &str,
    ) -> LiveSession {
        let (expires_mono, expires_wall, refresh_at) = self.schedule(response.expires_in);
        let session = LiveSession {
            user_id: user_id.to_string(),
            email: email.to_string(),
            refresh_token: refresh_token.to_string(),
            access_token: response.access_token.clone(),
            expires_mono,
            expires_wall,
            refresh_at_mono: refresh_at,
            issued_at: issued_at.to_string(),
        };
        *self.inner.live.lock().await = Some(session.clone());
        session
    }

    /// Release the loopback listener, if one is bound.
    ///
    /// **The abort must be awaited.** `JoinHandle::abort` only *requests* cancellation; the task
    /// still owns the listener until it actually stops, so returning early and binding the same
    /// fixed port immediately fails with `Address already in use`. Awaiting the handle — which
    /// resolves to `Err(cancelled)` — is what guarantees the socket is unbound before the caller
    /// binds it again. Proven by the regression test failing on the abort-without-await version.
    async fn stop_loopback(&self) {
        let task = self.inner.loopback.lock().expect("loopback lock").take();
        if let Some(task) = task {
            task.abort();
            let _ = task.await;
        }
    }

    async fn save_credential(&self, credential: StoredCredential) -> Result<()> {
        let store = Arc::clone(&self.inner.store);
        bounded_store_call("write the credential item", async move {
            tokio::task::spawn_blocking(move || store.save(&credential)).await
        })
        .await
    }

    async fn delete_credential(&self, user_id: String) -> Result<()> {
        let store = Arc::clone(&self.inner.store);
        bounded_store_call("delete the credential item", async move {
            tokio::task::spawn_blocking(move || store.delete(&user_id)).await
        })
        .await
    }
}

/// Run one credential-store call under [`CREDENTIAL_STORE_BUDGET`].
///
/// A store that does not answer inside the budget is **unavailable**, not slow: the only thing
/// that makes an OS keychain take seconds is a dialog nobody is there to click.
async fn bounded_store_call<T>(
    operation: &'static str,
    call: impl std::future::Future<Output = std::result::Result<Result<T>, tokio::task::JoinError>>,
) -> Result<T> {
    match tokio::time::timeout(CREDENTIAL_STORE_BUDGET, call).await {
        Ok(Ok(result)) => result,
        Ok(Err(join)) => Err(CustodyError::CredentialStore {
            operation,
            cause: join.to_string(),
        }),
        Err(_) => Err(CustodyError::CredentialStore {
            operation,
            cause: format!(
                "it did not answer within {}s. This usually means the system is showing a dialog                  asking someone to allow access, and a background service has no window to answer                  it in",
                CREDENTIAL_STORE_BUDGET.as_secs()
            ),
        }),
    }
}

/// §5's jittered exponential backoff: 1 s → 5 min cap, forever. An aeroplane is not a revocation.
fn backoff_for(failures: i64) -> Duration {
    let exponent = failures.clamp(1, 16) as u32 - 1;
    let base = BACKOFF_FLOOR.saturating_mul(2u32.saturating_pow(exponent.min(20)));
    let capped = base.min(BACKOFF_CAP);
    // Deterministic jitter derived from the failure count: ±12.5 %, and no dependence on a second
    // random source inside a scheduling decision.
    let jitter = capped.as_millis() as u64 / 8;
    let offset = failures.unsigned_abs() % (jitter.max(1) * 2);
    Duration::from_millis((capped.as_millis() as u64).saturating_sub(jitter) + offset)
}

fn state_for(error: &CustodyError) -> SessionState {
    match error {
        CustodyError::CredentialStore { .. } => SessionState::CredentialStoreUnavailable,
        CustodyError::GrantRefused { .. } => SessionState::SignInNeeded,
        CustodyError::NotSignedIn => SessionState::SignedOut,
        _ => SessionState::Offline,
    }
}

fn default_reason(state: SessionState) -> &'static str {
    match state {
        SessionState::SignedIn => "Signed in and syncing.",
        SessionState::SignInNeeded => "Sign in again on this computer to resume syncing.",
        SessionState::SignedOut => "Sign in on this computer to start syncing your folders.",
        SessionState::CredentialStoreUnavailable => {
            "Install and unlock a system keyring — on Linux gnome-keyring or kwallet — or this \
             device will need to sign in again after every restart."
        }
        SessionState::Offline => "Not connected — retrying.",
    }
}

/// Custody's log line. stderr is the daemon's log: launchd, systemd and the Windows task all
/// redirect it to `syncd.err.log` (SPEC-ENGINE §1.4).
fn log_line(message: &str) {
    eprintln!("[syncd/custody] {message}");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backoff_climbs_to_the_cap_and_never_past_it() {
        for failures in 1..=30 {
            let d = backoff_for(failures);
            assert!(d <= BACKOFF_CAP + BACKOFF_CAP / 8, "failure {failures}: {d:?}");
        }
        assert!(backoff_for(1) < backoff_for(6));
        assert!(backoff_for(20) >= BACKOFF_CAP - BACKOFF_CAP / 8);
    }

    #[test]
    fn a_configuration_that_cannot_work_is_refused_up_front() {
        let bad = CustodyConfig {
            world: World::Dev,
            supabase_url: "txzxabzwovsujtloxrus".into(),
            publishable_key: "k".into(),
            client_id: DESKTOP_CLIENT_ID.into(),
        };
        assert!(matches!(
            bad.validate(),
            Err(CustodyError::Configuration { .. })
        ));
    }

    #[test]
    fn every_error_maps_to_one_of_the_five_session_states() {
        assert_eq!(
            state_for(&CustodyError::GrantRefused {
                error: "invalid_grant".into(),
                description: None
            }),
            SessionState::SignInNeeded
        );
        assert_eq!(
            state_for(&CustodyError::Transport {
                endpoint: "x",
                cause: "y".into()
            }),
            SessionState::Offline
        );
        assert_eq!(
            state_for(&CustodyError::CredentialStore {
                operation: "read the credential item",
                cause: "locked".into()
            }),
            SessionState::CredentialStoreUnavailable
        );
        assert_eq!(state_for(&CustodyError::NotSignedIn), SessionState::SignedOut);
    }
}
