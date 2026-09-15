//! FS-C5's unit battery — SPEC-CUSTODY §13, "the seams, the mocks".
//!
//! Every test here runs against `FakeAuthServer`, `FakeKeychain`, `FakeCloudStateWriter`,
//! `RecordingNotifier` and `TestClock`. **They are mocks and they prove the state machine only**
//! (SCOPE §6 attestation rule). The evidence that counts is the real-machine proof recorded in
//! `crates/matrx-syncd/README.md` § "FS-C5 proof".

use chrono::{TimeZone, Utc};
use matrx_sync::custody::{
    CloudWriteOutcome, Custodian, CustodyConfig, CustodyError, FakeAuthServer,
    FakeCloudStateWriter, FakeFailure, FakeKeychain, RecordingNotifier, SessionState, SessionStore,
    TestClock, World, DESKTOP_CLIENT_ID,
};
use matrx_sync::journal::Journal;
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// A JWT whose payload carries `sub` and `email`. Not signed — custody never verifies a signature;
/// the server does, on every call the token is used for.
fn jwt(user: &str, email: &str) -> String {
    use base64::engine::general_purpose::URL_SAFE_NO_PAD;
    use base64::Engine as _;
    format!(
        "hdr.{}.sig",
        URL_SAFE_NO_PAD.encode(format!(r#"{{"sub":"{user}","email":"{email}"}}"#))
    )
}

struct Rig {
    custodian: Custodian,
    auth: Arc<FakeAuthServer>,
    keychain: Arc<FakeKeychain>,
    cloud: Arc<FakeCloudStateWriter>,
    notifier: Arc<RecordingNotifier>,
    clock: Arc<TestClock>,
    journal: Arc<Mutex<Journal>>,
}

impl Rig {
    fn new() -> Self {
        let journal = Arc::new(Mutex::new(Journal::open_in_memory().expect("journal")));
        let auth = Arc::new(FakeAuthServer::new());
        let keychain = Arc::new(FakeKeychain::new());
        let cloud = Arc::new(FakeCloudStateWriter::new());
        let notifier = Arc::new(RecordingNotifier::new());
        let clock = Arc::new(TestClock::new(
            Utc.with_ymd_and_hms(2026, 9, 13, 12, 0, 0).unwrap(),
        ));
        let custodian = Custodian::new(
            CustodyConfig {
                world: World::Dev,
                supabase_url: "https://db.matrxserver.com".into(),
                publishable_key: "sb_publishable_test".into(),
                client_id: DESKTOP_CLIENT_ID.into(),
            },
            Arc::clone(&journal),
            Arc::clone(&auth) as Arc<_>,
            Arc::clone(&keychain) as Arc<_>,
            Arc::clone(&cloud) as Arc<_>,
            Arc::clone(&notifier) as Arc<_>,
            Arc::clone(&clock) as Arc<_>,
        )
        .expect("custodian");
        Rig {
            custodian,
            auth,
            keychain,
            cloud,
            notifier,
            clock,
            journal,
        }
    }

    /// Register a device row so §8b has a `device_id` (C13) to filter on.
    fn register_device(&self, app_instance_id: &str) {
        let j = self.journal.lock().unwrap();
        j.connection()
            .execute(
                "INSERT INTO device (id, app_instance_id, world) VALUES ('singleton', ?1, 'dev')",
                [app_instance_id],
            )
            .expect("device row");
    }

    fn row(&self) -> matrx_sync::custody::SessionRow {
        SessionStore::new(Arc::clone(&self.journal))
            .read()
            .expect("read")
            .expect("a session row must exist")
    }

    /// Drive a complete sign-in through the daemon's own PKCE transaction.
    async fn sign_in(&self, user: &str, email: &str, refresh: &str, expires_in: i64) {
        self.auth
            .push_ok(&jwt(user, email), Some(refresh), expires_in);
        // The dev world's redirect is the loopback one, which binds a real port; the deep-link leg
        // exercises the identical state machine without one.
        let start = self
            .custodian
            .begin_sign_in(Some(matrx_sync::custody::RedirectKind::DeepLink))
            .await
            .expect("begin");
        assert_eq!(start.redirect_kind, "deep_link");
        let state = state_of(&start.authorize_url);
        self.custodian
            .complete_sign_in("the-code", &state)
            .await
            .expect("callback");
    }
}

fn state_of(authorize_url: &str) -> String {
    let query = authorize_url.split_once('?').expect("query").1;
    for pair in query.split('&') {
        if let Some(v) = pair.strip_prefix("state=") {
            return urlencoding::decode(v).expect("decode").into_owned();
        }
    }
    panic!("no state in {authorize_url}");
}

// ---------------------------------------------------------------- S1, S5, §4

#[tokio::test]
async fn sign_in_puts_the_refresh_token_in_the_keychain_and_nothing_else_anywhere() {
    let rig = Rig::new();
    rig.sign_in("user-1", "admin@admin.com", "refresh-1", 3600).await;

    assert!(rig.keychain.contains("user-1"), "the keychain holds the item");
    let row = rig.row();
    assert_eq!(row.state, SessionState::SignedIn);
    assert_eq!(row.user_id.as_deref(), Some("user-1"));
    assert_eq!(row.email.as_deref(), Some("admin@admin.com"));
    assert_eq!(row.world, World::Dev);

    // The access token is never on disk (§4): the journal row carries only its EXPIRY.
    let serialized = serde_json::to_string(&row).expect("row");
    assert!(!serialized.contains("refresh-1"));
    assert!(!serialized.contains("hdr."));
}

#[tokio::test]
async fn a_callback_whose_state_this_daemon_never_issued_is_refused_honestly() {
    let rig = Rig::new();
    rig.auth.push_ok(&jwt("u", "e"), Some("r"), 3600);
    rig.custodian
        .begin_sign_in(Some(matrx_sync::custody::RedirectKind::DeepLink))
        .await
        .expect("begin");
    let err = rig
        .custodian
        .complete_sign_in("code", "a-state-from-another-world")
        .await
        .expect_err("must refuse");
    assert!(matches!(err, CustodyError::UnknownTransaction));
    assert_eq!(err.code(), "unknown_transaction");
    // S14: the refusal carries the sentence the UI shows; it is never a silent 409.
    assert!(err.remedy().contains("different copy of AI Matrx"));
}

#[tokio::test]
async fn a_transaction_is_single_use() {
    let rig = Rig::new();
    rig.sign_in("user-1", "a@b.c", "refresh-1", 3600).await;
    // The same state cannot be replayed: the transaction was taken before the first await.
    let err = rig
        .custodian
        .complete_sign_in("the-code", "anything")
        .await
        .expect_err("replay must fail");
    assert!(matches!(err, CustodyError::UnknownTransaction));
}

#[tokio::test]
async fn a_second_sign_in_cancels_the_first() {
    let rig = Rig::new();
    let first = rig
        .custodian
        .begin_sign_in(Some(matrx_sync::custody::RedirectKind::DeepLink))
        .await
        .expect("first");
    let _second = rig
        .custodian
        .begin_sign_in(Some(matrx_sync::custody::RedirectKind::DeepLink))
        .await
        .expect("second");
    let err = rig
        .custodian
        .complete_sign_in("code", &state_of(&first.authorize_url))
        .await
        .expect_err("the first transaction is gone");
    assert!(matches!(err, CustodyError::UnknownTransaction));
}

// ------------------------------------------------------------------ S9, S11

#[tokio::test]
async fn the_handout_serves_the_cached_token_while_it_has_a_minute_of_life() {
    let rig = Rig::new();
    rig.sign_in("u", "e@x.y", "r1", 3600).await;
    let first = rig.custodian.token().await.expect("token");

    rig.clock.advance(Duration::from_secs(1800));
    let second = rig.custodian.token().await.expect("token");
    assert_eq!(first.access_token, second.access_token, "no rotation was needed");
    assert!(
        rig.auth.presented_refresh_tokens().is_empty(),
        "the refresh grant was never used"
    );
}

#[tokio::test]
async fn the_schedule_comes_from_the_access_tokens_own_lifetime_not_the_sessions() {
    let rig = Rig::new();
    // MXL-D-046's exact shape: a session that lives seven days, an access token that lives an
    // hour. The rotation must be scheduled off the hour.
    rig.sign_in("u", "e@x.y", "r1", 3600).await;
    rig.auth.push_ok(&jwt("u", "e@x.y"), Some("r2"), 3600);

    // 0.6 × 3600 = 2160 s; 3600 − 300 = 3300 s. The sooner is 2160 s (T+36 m).
    rig.clock.advance(Duration::from_secs(2159));
    rig.custodian.tick().await;
    assert!(
        rig.auth.presented_refresh_tokens().is_empty(),
        "not due at T+35m59s"
    );

    rig.clock.advance(Duration::from_secs(2));
    rig.custodian.tick().await;
    assert_eq!(
        rig.auth.presented_refresh_tokens(),
        vec!["r1".to_string()],
        "due at T+36m"
    );
}

#[tokio::test]
async fn a_rotated_refresh_token_is_never_presented_twice() {
    let rig = Rig::new();
    rig.sign_in("u", "e@x.y", "r1", 3600).await;
    for n in 2..=5 {
        rig.auth
            .push_ok(&jwt("u", "e@x.y"), Some(&format!("r{n}")), 3600);
        rig.clock.advance(Duration::from_secs(2161));
        rig.custodian.tick().await;
    }
    let presented = rig.auth.presented_refresh_tokens();
    assert_eq!(presented, vec!["r1", "r2", "r3", "r4"]);
    let mut sorted = presented.clone();
    sorted.sort();
    sorted.dedup();
    assert_eq!(sorted.len(), presented.len(), "no token was reused");
}

#[tokio::test]
async fn a_server_that_does_not_rotate_keeps_the_token_it_gave_us() {
    let rig = Rig::new();
    rig.sign_in("u", "e@x.y", "r1", 3600).await;
    rig.auth.push_ok(&jwt("u", "e@x.y"), None, 3600);
    rig.clock.advance(Duration::from_secs(2161));
    rig.custodian.tick().await;
    rig.auth.push_ok(&jwt("u", "e@x.y"), None, 3600);
    rig.clock.advance(Duration::from_secs(2161));
    rig.custodian.tick().await;
    assert_eq!(rig.auth.presented_refresh_tokens(), vec!["r1", "r1"]);
}

#[tokio::test]
async fn concurrent_callers_await_one_rotation_rather_than_starting_two() {
    let rig = Rig::new();
    rig.sign_in("u", "e@x.y", "r1", 3600).await;
    rig.auth.push_ok(&jwt("u", "e@x.y"), Some("r2"), 3600);
    rig.clock.advance(Duration::from_secs(3600));

    let a = rig.custodian.clone();
    let b = rig.custodian.clone();
    let (ra, rb) = tokio::join!(a.token(), b.token());
    assert_eq!(
        ra.expect("a").access_token,
        rb.expect("b").access_token,
        "both callers were served the same rotation"
    );
    assert_eq!(
        rig.auth.presented_refresh_tokens().len(),
        1,
        "exactly one rotation happened (S8 single-flight)"
    );
}

// ----------------------------------------------------------------- S8, S7

#[tokio::test]
async fn a_keychain_that_refuses_the_write_fails_the_rotation_and_does_not_reuse_the_old_token() {
    let rig = Rig::new();
    rig.sign_in("u", "e@x.y", "r1", 3600).await;
    rig.keychain.refuse_writes("the keychain is locked");
    rig.auth.push_ok(&jwt("u", "e@x.y"), Some("r2"), 3600);
    rig.clock.advance(Duration::from_secs(3600));

    let refusal = rig.custodian.token().await.expect_err("must refuse");
    assert_eq!(refusal.state, SessionState::CredentialStoreUnavailable);
    assert!(refusal.state_reason.contains("keyring"));
    assert_eq!(rig.row().state, SessionState::CredentialStoreUnavailable);

    // S8: the failed rotation must not leave r2 half-adopted, and r1 is never presented again
    // as if nothing had happened.
    assert_eq!(rig.auth.presented_refresh_tokens(), vec!["r1"]);
    // S8's write-ahead rule: r2 was never adopted, in memory or in the store, because the store
    // refused it. The device still holds exactly the token the server last confirmed.
    assert_eq!(rig.keychain.stored_refresh_token("u").as_deref(), Some("r1"));

    // And when the store comes back, the very next rotation succeeds from that same r1 — the
    // failure cost nothing but a delay.
    rig.keychain.allow_writes();
    rig.auth.push_ok(&jwt("u", "e@x.y"), Some("r2"), 3600);
    rig.custodian.token().await.expect("rotation after the store returns");
    assert_eq!(rig.keychain.stored_refresh_token("u").as_deref(), Some("r2"));
    assert_eq!(rig.auth.presented_refresh_tokens(), vec!["r1", "r1"]);
}

#[tokio::test]
async fn a_locked_keychain_at_start_is_a_named_state_never_a_disk_fallback() {
    let fresh = Rig::new();
    // A daemon starting over a journal that remembers a user, with a keychain that will not open
    // — the ordinary Linux headless case (S7).
    {
        let j = fresh.journal.lock().unwrap();
        j.connection()
            .execute(
                "INSERT INTO session_state (id, state, since, world, updated_at)
                 VALUES (1, 'signed_in', 't', 'dev', 't')",
                [],
            )
            .unwrap();
        j.connection()
            .execute("UPDATE session_state SET user_id = 'u', email = 'e@x.y'", [])
            .unwrap();
    }
    fresh.keychain.lock("no Secret Service on this session");
    let snapshot = fresh.custodian.resume().await;
    assert_eq!(snapshot.state, SessionState::CredentialStoreUnavailable);
    assert!(!snapshot.signed_in);
    assert!(snapshot
        .state_reason
        .as_deref()
        .unwrap_or_default()
        .contains("gnome-keyring"));
}

// --------------------------------------------------------------- §5 offline

#[tokio::test]
async fn an_unreachable_network_is_offline_with_a_next_attempt_never_a_lost_session() {
    let rig = Rig::new();
    rig.sign_in("u", "e@x.y", "r1", 3600).await;
    rig.auth.push_err(FakeFailure::Offline);
    rig.clock.advance(Duration::from_secs(3600));

    let refusal = rig.custodian.token().await.expect_err("offline");
    assert_eq!(refusal.state, SessionState::Offline);
    let row = rig.row();
    assert_eq!(row.state, SessionState::Offline);
    assert!(row.next_attempt_at.is_some(), "the user is told when we retry next");
    assert_eq!(row.failure_count, 1);
    // An aeroplane is not a revocation: the keychain item stays.
    assert!(rig.keychain.contains("u"));
    assert!(rig.notifier.posted().is_empty(), "offline never notifies");
}

#[tokio::test]
async fn a_captive_portal_html_200_is_offline_not_a_revocation() {
    let rig = Rig::new();
    rig.sign_in("u", "e@x.y", "r1", 3600).await;
    rig.auth.push_err(FakeFailure::CaptivePortal);
    rig.clock.advance(Duration::from_secs(3600));
    assert_eq!(
        rig.custodian.token().await.expect_err("captive").state,
        SessionState::Offline
    );
    assert!(rig.keychain.contains("u"));
}

#[tokio::test]
async fn a_5xx_is_offline_and_the_backoff_climbs() {
    let rig = Rig::new();
    rig.sign_in("u", "e@x.y", "r1", 3600).await;
    rig.clock.advance(Duration::from_secs(3600));
    for _ in 0..4 {
        rig.auth.push_err(FakeFailure::ServerError(503));
        let _ = rig.custodian.token().await;
        rig.clock.advance(Duration::from_secs(600));
        rig.custodian.tick().await;
    }
    assert!(rig.row().failure_count >= 4);
    assert_eq!(rig.row().state, SessionState::Offline);
}

// ------------------------------------------------------------- S16, S18, C14

#[tokio::test]
async fn invalid_grant_writes_the_cloud_state_first_on_the_still_valid_token() {
    let rig = Rig::new();
    rig.register_device("device-uuid-1");
    rig.sign_in("u", "admin@admin.com", "r1", 3600).await;
    rig.auth.push_err(FakeFailure::InvalidGrant);
    rig.clock.advance(Duration::from_secs(3600));

    let refusal = rig.custodian.token().await.expect_err("terminal");
    assert_eq!(refusal.state, SessionState::SignInNeeded);

    // (1) the mapping rows, with the token still in hand.
    let calls = rig.cloud.calls();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].0, "device-uuid-1");
    assert_eq!(calls[0].1, "sign_in_needed");
    assert!(calls[0].2.contains("admin@admin.com"));

    // (2) the journal row.
    let row = rig.row();
    assert_eq!(row.state, SessionState::SignInNeeded);
    assert!(!row.cloud_state_write_pending);
    assert!(row.cloud_state_written_at.is_some());
    assert_eq!(row.email.as_deref(), Some("admin@admin.com"));

    // (3) exactly one OS notification.
    assert_eq!(rig.notifier.posted().len(), 1);
    assert!(rig.notifier.posted()[0].1.contains("admin@admin.com"));

    // (4) the worthless refresh token is gone.
    assert!(!rig.keychain.contains("u"));
}

#[tokio::test]
async fn branch_b_records_the_pending_write_and_never_fails_the_daemon() {
    let rig = Rig::new();
    rig.register_device("device-uuid-1");
    rig.cloud.set_outcome(CloudWriteOutcome::TablePending {
        detail: "HTTP 404: PGRST205 Could not find the table 'files.sync_mappings'".into(),
    });
    rig.sign_in("u", "admin@admin.com", "r1", 3600).await;
    rig.auth.push_err(FakeFailure::InvalidGrant);
    rig.clock.advance(Duration::from_secs(3600));

    let refusal = rig.custodian.token().await.expect_err("terminal");
    assert_eq!(refusal.state, SessionState::SignInNeeded);
    let row = rig.row();
    assert!(
        row.cloud_state_write_pending,
        "S16 branch B is recorded, not pretended"
    );
    assert!(row.cloud_state_written_at.is_none());
    // The device-side state and the notification still appear.
    assert_eq!(row.state, SessionState::SignInNeeded);
    assert_eq!(rig.notifier.posted().len(), 1);
}

#[tokio::test]
async fn the_notification_fires_once_per_state_entry_and_the_retry_loop_never_repeats_it() {
    let rig = Rig::new();
    rig.sign_in("u", "e@x.y", "r1", 3600).await;
    rig.auth.push_err(FakeFailure::InvalidGrant);
    rig.clock.advance(Duration::from_secs(3600));
    let _ = rig.custodian.token().await;
    assert_eq!(rig.notifier.posted().len(), 1);

    for _ in 0..5 {
        rig.clock.advance(Duration::from_secs(600));
        rig.custodian.tick().await;
        let _ = rig.custodian.token().await;
    }
    assert_eq!(
        rig.notifier.posted().len(),
        1,
        "S18 deduplicates by (state, since)"
    );
}

// -------------------------------------------------------------------- §9, S20

#[tokio::test]
async fn sign_out_wipes_the_keychain_writes_signed_out_and_revokes_nothing() {
    let rig = Rig::new();
    rig.register_device("device-uuid-1");
    rig.sign_in("u", "admin@admin.com", "r1", 3600).await;
    assert!(rig.keychain.contains("u"));

    rig.custodian.sign_out().await.expect("sign out");

    assert!(!rig.keychain.contains("u"), "the keychain item is gone");
    let row = rig.row();
    assert_eq!(row.state, SessionState::SignedOut);
    assert!(row.state_reason.as_deref().unwrap().contains("Signed out on this device"));

    let calls = rig.cloud.calls();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].1, "signed_out", "never disguised as `paused` (C3)");

    // S20: no revocation exists on this path, so none was attempted — the fake OAuth server was
    // never asked for anything beyond the sign-in grant.
    assert!(rig.auth.presented_refresh_tokens().is_empty());

    // The hand-out now refuses honestly.
    let refusal = rig.custodian.token().await.expect_err("signed out");
    assert_eq!(refusal.state, SessionState::SignedOut);
}

#[tokio::test]
async fn signing_back_in_after_sign_out_works_from_a_clean_state() {
    let rig = Rig::new();
    rig.sign_in("u", "admin@admin.com", "r1", 3600).await;
    rig.custodian.sign_out().await.expect("sign out");
    rig.sign_in("u", "admin@admin.com", "r9", 3600).await;
    assert_eq!(rig.row().state, SessionState::SignedIn);
    assert!(rig.keychain.contains("u"));
}

// ------------------------------------------------------------------- S10, S13

#[tokio::test]
async fn a_wall_clock_jump_forces_a_rotation_rather_than_postponing_one() {
    let rig = Rig::new();
    rig.sign_in("u", "e@x.y", "r1", 3600).await;
    rig.custodian.tick().await; // seed the skew detector
    rig.auth.push_ok(&jwt("u", "e@x.y"), Some("r2"), 3600);

    // The machine slept: the wall clock advanced hours, the monotonic clock barely moved.
    rig.clock.advance(Duration::from_secs(15));
    rig.clock.jump_wall(chrono::Duration::hours(4));
    rig.custodian.tick().await;
    assert_eq!(
        rig.auth.presented_refresh_tokens(),
        vec!["r1".to_string()],
        "the gap between the two clocks forced a rotation"
    );
}

#[tokio::test]
async fn a_forced_refresh_is_rate_limited_to_one_a_minute() {
    let rig = Rig::new();
    rig.sign_in("u", "e@x.y", "r1", 3600).await;
    rig.auth.push_ok(&jwt("u", "e@x.y"), Some("r2"), 3600);
    rig.custodian.force_refresh().await.expect("first");
    assert_eq!(rig.auth.presented_refresh_tokens().len(), 1);

    rig.clock.advance(Duration::from_secs(30));
    rig.custodian.force_refresh().await.expect("second");
    assert_eq!(
        rig.auth.presented_refresh_tokens().len(),
        1,
        "the second force inside the minute served the cached token"
    );

    rig.auth.push_ok(&jwt("u", "e@x.y"), Some("r3"), 3600);
    rig.clock.advance(Duration::from_secs(31));
    rig.custodian.force_refresh().await.expect("third");
    assert_eq!(rig.auth.presented_refresh_tokens().len(), 2);
}

#[tokio::test]
async fn every_refusal_is_one_of_the_four_states_never_an_error_and_never_an_empty_ok() {
    // S13/A5: a consumer's switch over the four refusal states plus the 200 is exhaustive.
    let rig = Rig::new();
    let refusal = rig.custodian.token().await.expect_err("no session");
    assert!(matches!(
        refusal.state,
        SessionState::SignedOut
            | SessionState::SignInNeeded
            | SessionState::Offline
            | SessionState::CredentialStoreUnavailable
    ));
    assert!(!refusal.state_reason.is_empty(), "a refusal always carries its sentence");
    assert!(!refusal.since.is_empty());
}

#[tokio::test]
async fn resume_adopts_the_keychain_item_and_rotates_immediately() {
    let rig = Rig::new();
    rig.sign_in("u", "admin@admin.com", "r1", 3600).await;

    // A second custodian over the SAME journal and keychain — a daemon restart (S19).
    let restarted = Custodian::new(
        CustodyConfig {
            world: World::Dev,
            supabase_url: "https://db.matrxserver.com".into(),
            publishable_key: "k".into(),
            client_id: DESKTOP_CLIENT_ID.into(),
        },
        Arc::clone(&rig.journal),
        Arc::clone(&rig.auth) as Arc<_>,
        Arc::clone(&rig.keychain) as Arc<_>,
        Arc::clone(&rig.cloud) as Arc<_>,
        Arc::clone(&rig.notifier) as Arc<_>,
        Arc::clone(&rig.clock) as Arc<_>,
    )
    .expect("restarted custodian");

    rig.auth.push_ok(&jwt("u", "admin@admin.com"), Some("r2"), 3600);
    let snapshot = restarted.resume().await;
    assert!(snapshot.signed_in, "no keychain prompt, no re-sign-in (S19)");
    assert_eq!(snapshot.email.as_deref(), Some("admin@admin.com"));
    assert_eq!(rig.auth.presented_refresh_tokens(), vec!["r1".to_string()]);
    assert!(restarted.token().await.is_ok());
}

#[tokio::test]
async fn a_journal_that_remembers_a_user_whose_keychain_item_is_gone_says_sign_in_needed() {
    let rig = Rig::new();
    rig.sign_in("u", "admin@admin.com", "r1", 3600).await;
    rig.keychain.delete_for_test("u");

    let restarted = Custodian::new(
        CustodyConfig {
            world: World::Dev,
            supabase_url: "https://db.matrxserver.com".into(),
            publishable_key: "k".into(),
            client_id: DESKTOP_CLIENT_ID.into(),
        },
        Arc::clone(&rig.journal),
        Arc::clone(&rig.auth) as Arc<_>,
        Arc::clone(&rig.keychain) as Arc<_>,
        Arc::clone(&rig.cloud) as Arc<_>,
        Arc::clone(&rig.notifier) as Arc<_>,
        Arc::clone(&rig.clock) as Arc<_>,
    )
    .expect("restarted");
    let snapshot = restarted.resume().await;
    assert_eq!(snapshot.state, SessionState::SignInNeeded);
    assert!(snapshot
        .state_reason
        .as_deref()
        .unwrap()
        .contains("admin@admin.com"));
}

#[tokio::test]
async fn session_changed_is_published_on_sign_in_rotation_and_sign_out() {
    let rig = Rig::new();
    let mut events = rig.custodian.subscribe();
    rig.sign_in("u", "e@x.y", "r1", 3600).await;
    let first = events.recv().await.expect("sign-in event");
    assert!(first.session.signed_in);
    assert!(first.rotated, "the webview re-auths realtime on this (S17/A4)");

    rig.auth.push_ok(&jwt("u", "e@x.y"), Some("r2"), 3600);
    rig.clock.advance(Duration::from_secs(2161));
    rig.custodian.tick().await;
    let rotation = events.recv().await.expect("rotation event");
    assert!(rotation.rotated);

    rig.custodian.sign_out().await.expect("sign out");
    let out = events.recv().await.expect("sign-out event");
    assert!(!out.session.signed_in);
    assert_eq!(out.session.state, SessionState::SignedOut);
}

// ------------------------------------------- the fixed loopback port (S3, S5, S21)

#[tokio::test]
async fn cancelling_a_sign_in_releases_the_fixed_loopback_port() {
    // Found by running it: S5 says a second `POST /v1/sign-in` cancels the first, and S3 fixes the
    // redirect port because Supabase matches redirect URIs EXACTLY. A cancelled transaction that
    // kept its listener therefore made every later sign-in fail `loopback_port_unavailable` until
    // the daemon restarted. Proven failing before the fix.
    let rig = Rig::new();
    let first = rig
        .custodian
        .begin_sign_in(Some(matrx_sync::custody::RedirectKind::Loopback))
        .await
        .expect(
            "first sign-in must bind the fixed dev callback port 22261. If this says \
             `Address already in use`, a dev daemon is running on this machine — stop it with \
             POST /v1/shutdown; the port is fixed by S3 and cannot be moved for a test.",
        );
    assert_eq!(first.redirect_uri, "http://localhost:22261/oauth/callback");

    let second = rig
        .custodian
        .begin_sign_in(Some(matrx_sync::custody::RedirectKind::Loopback))
        .await
        .expect("the second must rebind the same fixed port, not collide with the first");
    assert_eq!(second.redirect_uri, first.redirect_uri);
    assert_ne!(second.transaction_id, first.transaction_id);

    // And completing one releases it, so a later sign-in can bind again.
    rig.auth.push_ok(&jwt("u", "e@x.y"), Some("r1"), 3600);
    rig.custodian
        .complete_sign_in("code", &state_of(&second.authorize_url))
        .await
        .expect("callback");
    rig.custodian
        .begin_sign_in(Some(matrx_sync::custody::RedirectKind::Loopback))
        .await
        .expect("the port is free after the transaction is spent");
    rig.custodian.sign_out().await.expect("sign out releases it too");
    rig.custodian
        .begin_sign_in(Some(matrx_sync::custody::RedirectKind::Loopback))
        .await
        .expect("free after sign-out");
}

#[tokio::test]
async fn a_keychain_that_shows_a_dialog_becomes_a_state_instead_of_hanging_the_daemon() {
    // Observed live on 2026-09-15: a rebuilt dev binary asked macOS for an item its previous
    // build had created, macOS put up an approval dialog, and `resume()` blocked forever — the
    // daemon served the socket but never reported a session state at all. S15 names this exact
    // shape ("a login-time daemon has no UI to answer a prompt — MXL-D-046's shape wearing a new
    // hat"), and law 4 forbids a surface that just stops. Proven failing before the bound.
    let rig = Rig::new();
    rig.sign_in("u", "admin@admin.com", "r1", 3600).await;
    rig.keychain.hang(Duration::from_secs(30));

    let started = std::time::Instant::now();
    let refusal = tokio::time::timeout(Duration::from_secs(25), async {
        rig.auth.push_ok(&jwt("u", "admin@admin.com"), Some("r2"), 3600);
        rig.custodian.force_refresh().await
    })
    .await
    .expect("the daemon must answer, not hang")
    .expect_err("a store that will not answer is unavailable");

    assert_eq!(refusal.state, SessionState::CredentialStoreUnavailable);
    assert!(
        started.elapsed() < Duration::from_secs(25),
        "it answered in {:?}",
        started.elapsed()
    );
    assert!(
        refusal.state_reason.contains("keyring"),
        "the remedy travels with the state: {}",
        refusal.state_reason
    );
}

#[tokio::test]
async fn a_refusals_sentence_always_belongs_to_the_state_it_reports() {
    // Observed live on 2026-09-15: `GET /v1/token` answered
    // {"state":"signed_out","state_reason":"Signed in and syncing."} — a refusal wearing the
    // sentence of the state it was refusing to be. Law 4 forbids a screen that lies as firmly as
    // one that is dead. Proven failing before the fix.
    let rig = Rig::new();
    rig.sign_in("u", "admin@admin.com", "r1", 3600).await;
    assert_eq!(rig.row().state, SessionState::SignedIn);

    // The journal still says `signed_in`; the live session is gone (a restart that could not
    // adopt the keychain item is exactly this shape).
    let stranded = Custodian::new(
        CustodyConfig {
            world: World::Dev,
            supabase_url: "https://db.matrxserver.com".into(),
            publishable_key: "k".into(),
            client_id: DESKTOP_CLIENT_ID.into(),
        },
        Arc::clone(&rig.journal),
        Arc::clone(&rig.auth) as Arc<_>,
        Arc::clone(&rig.keychain) as Arc<_>,
        Arc::clone(&rig.cloud) as Arc<_>,
        Arc::clone(&rig.notifier) as Arc<_>,
        Arc::clone(&rig.clock) as Arc<_>,
    )
    .expect("stranded custodian");

    let refusal = stranded.token().await.expect_err("no live session");
    assert_eq!(refusal.state, SessionState::SignedOut);
    assert!(
        !refusal.state_reason.contains("Signed in"),
        "the sentence must describe the refusal, not the state it is refusing to be: {}",
        refusal.state_reason
    );
    assert!(refusal.state_reason.contains("Sign in on this computer"));
}
