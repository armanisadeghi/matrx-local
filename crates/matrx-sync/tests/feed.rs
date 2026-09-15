//! FS-L1 unit 3 — the change feed.
//!
//! The pure half (folding pages into `tree_remote`) runs everywhere. The LIVE half runs against
//! `https://files.matrxserver.com` as `admin@admin.com` when credentials are present, and is
//! **read-only** in this unit: it reads the feed and asserts the contract the engine was built
//! against is the contract the service actually serves.
//!
//! The live test refuses to pass quietly when it cannot run: with no credentials it prints why and
//! skips, and `MATRX_LIVE_FEED_REQUIRED=1` turns that skip into a failure, so a pipeline that is
//! supposed to exercise the service cannot silently stop doing it.

use matrx_sync::feed::{
    apply_file_page, apply_folder_page, ApplyContext, FileEntry, FilePage, FolderEntry, FolderPage,
};

fn entry(path: &str, checksum: Option<&str>, device: Option<&str>) -> FileEntry {
    FileEntry {
        file_id: format!("id-{path}"),
        file_path: path.to_string(),
        file_name: None,
        folder_id: None,
        checksum: checksum.map(str::to_string),
        size_bytes: Some(10),
        version: Some(3),
        deleted_at: None,
        updated_at: Some("2026-09-15T00:00:00Z".to_string()),
        created_at: None,
        origin_device_id: device.map(str::to_string),
        client_modified_at: Some("2026-09-14T09:00:00Z".to_string()),
        organization_id: Some("org-1".to_string()),
        mime_type: None,
    }
}

fn page(files: Vec<FileEntry>) -> FilePage {
    FilePage {
        files,
        next_cursor: Some("cursor-2".to_string()),
        has_more: true,
        server_time: Some("2026-09-15T00:00:10Z".to_string()),
    }
}

fn ctx(device: Option<&str>, prefix: &str) -> ApplyContext {
    ApplyContext {
        this_device_id: device.map(str::to_string),
        cloud_prefix: prefix.to_string(),
    }
}

#[test]
fn a_page_becomes_remote_rows_and_advances_the_cursor() {
    let applied = apply_file_page(
        &page(vec![entry("Docs/a.txt", Some("c1"), Some("other-device"))]),
        &ctx(Some("this-device"), "Docs"),
    );
    assert_eq!(applied.rows.len(), 1);
    let row = &applied.rows[0];
    assert_eq!(row.path_nfc, "a.txt", "paths are mapping-relative");
    assert_eq!(row.checksum.as_deref(), Some("c1"));
    assert_eq!(row.remote_version, Some(3), "the 412 precondition's version");
    assert_eq!(
        row.client_modified_at.as_deref(),
        Some("2026-09-14T09:00:00Z"),
        "the USER's clock, not the server's — what a surface shows as 'modified'"
    );
    assert_eq!(applied.next_cursor.as_deref(), Some("cursor-2"));
    assert!(applied.has_more);
}

#[test]
fn a_tombstone_is_a_row_and_loses_the_checksum_it_had_when_it_was_alive() {
    let mut dead = entry("Docs/gone.txt", Some("c-old"), Some("other"));
    dead.deleted_at = Some("2026-09-15T01:00:00Z".to_string());
    let applied = apply_file_page(&page(vec![dead]), &ctx(Some("this"), "Docs"));

    let row = &applied.rows[0];
    assert!(row.deleted_at.is_some(), "a tombstone is a ROW, never a missing row");
    assert!(!row.is_live());
    assert_eq!(
        row.checksum, None,
        "it must not keep the checksum it had when it was alive — the planner reads a checksum as \
         'this content is in the cloud'"
    );
}

#[test]
fn our_own_writes_are_suppressed_and_counted_not_silently_dropped() {
    let applied = apply_file_page(
        &page(vec![
            entry("Docs/mine.txt", Some("c1"), Some("this-device")),
            entry("Docs/theirs.txt", Some("c2"), Some("other-device")),
        ]),
        &ctx(Some("this-device"), "Docs"),
    );
    assert_eq!(applied.rows.len(), 1);
    assert_eq!(applied.rows[0].path_nfc, "theirs.txt");
    assert_eq!(
        applied.suppressed_echoes, 1,
        "counted, so a log can tell 'nothing happened' from 'it was all our own echo'"
    );
}

#[test]
fn an_entry_we_cannot_attribute_is_applied_never_suppressed() {
    // Suppressing an unattributable entry would drop somebody else's change; applying our own echo
    // costs one wasted comparison. The asymmetry is deliberate.
    let applied = apply_file_page(
        &page(vec![entry("Docs/mystery.txt", Some("c1"), None)]),
        &ctx(Some("this-device"), "Docs"),
    );
    assert_eq!(applied.rows.len(), 1);
    assert_eq!(applied.suppressed_echoes, 0);

    // And a device that does not know its own id suppresses nothing either.
    let applied = apply_file_page(
        &page(vec![entry("Docs/x.txt", Some("c1"), Some("someone"))]),
        &ctx(None, "Docs"),
    );
    assert_eq!(applied.rows.len(), 1);
}

#[test]
fn the_mapping_prefix_ends_at_a_path_boundary() {
    let applied = apply_file_page(
        &page(vec![
            entry("Photos/holiday.jpg", Some("c1"), None),
            entry("PhotosOld/ancient.jpg", Some("c2"), None),
            entry("Other/thing.txt", Some("c3"), None),
        ]),
        &ctx(None, "Photos"),
    );
    assert_eq!(applied.rows.len(), 1, "{:?}", applied.rows);
    assert_eq!(applied.rows[0].path_nfc, "holiday.jpg");
    assert_eq!(
        applied.outside_mapping, 2,
        "'Photos' must not swallow 'PhotosOld' — a prefix that ignores path boundaries syncs the \
         wrong folder"
    );
}

#[test]
fn an_organization_root_mapping_takes_every_path_as_given() {
    let applied = apply_file_page(
        &page(vec![entry("Docs/deep/file.txt", Some("c1"), None)]),
        &ctx(None, ""),
    );
    assert_eq!(applied.rows[0].path_nfc, "Docs/deep/file.txt");
}

#[test]
fn feed_paths_are_normalised_the_same_way_the_scanner_normalises_them() {
    // The cloud stores NFC; a client that keyed on whatever bytes arrived would see one file as
    // two the moment a name carried a combining mark.
    let applied = apply_file_page(
        &page(vec![entry("Docs/cafe\u{301}.txt", Some("c1"), None)]),
        &ctx(None, "Docs"),
    );
    assert_eq!(applied.rows[0].path_nfc, "caf\u{e9}.txt");
}

#[test]
fn folders_become_directory_rows_with_no_content() {
    let page = FolderPage {
        folders: vec![
            FolderEntry {
                folder_id: "f1".to_string(),
                folder_path: "Docs/Empty".to_string(),
                folder_name: Some("Empty".to_string()),
                parent_id: Some("root".to_string()),
                deleted_at: None,
                updated_at: Some("2026-09-15T00:00:00Z".to_string()),
                created_at: None,
                is_system: Some(false),
            },
            // The mapping root itself is not a row inside the mapping.
            FolderEntry {
                folder_id: "f0".to_string(),
                folder_path: "Docs".to_string(),
                folder_name: Some("Docs".to_string()),
                parent_id: None,
                deleted_at: None,
                updated_at: None,
                created_at: None,
                is_system: Some(false),
            },
        ],
        next_cursor: Some("c".to_string()),
        has_more: false,
        server_time: None,
        truncated: false,
    };
    let applied = apply_folder_page(&page, &ctx(None, "Docs"));
    assert_eq!(applied.rows.len(), 1);
    let row = &applied.rows[0];
    assert!(row.is_dir && row.checksum.is_none() && row.size.is_none());
    assert_eq!(row.path_nfc, "Empty");
}

#[test]
fn a_non_2xx_is_classified_into_something_with_a_remedy() {
    use matrx_sync::feed::{classify_status, FeedError};

    // The exact body the live service returns without the organization header.
    let org = classify_status(
        400,
        r#"{"error":"organization_required","code":"organization_required","message":"This request carried an identity but no organization."}"#,
    );
    assert!(matches!(org, FeedError::OrganizationRefused(_)), "{org:?}");
    assert_eq!(org.honest_state(), "admission_revoked");
    assert!(!org.retryable(), "retrying without the header will fail forever");

    assert_eq!(classify_status(401, "expired").honest_state(), "sign_in_needed");
    assert!(classify_status(401, "expired").retryable(), "after the custodian refreshes");
    assert_eq!(classify_status(503, "down").honest_state(), "offline");
    assert!(classify_status(503, "down").retryable());
    assert!(matches!(
        classify_status(400, r#"{"detail":"invalid cursor"}"#),
        FeedError::CursorRejected(_)
    ));
}

// ------------------------------------------- the LIVE service, read-only

/// Credentials for the live read. Never printed, never asserted on.
struct LiveCreds {
    token: String,
    organization_id: String,
    base_url: String,
}

/// Read them from the environment, or say precisely why the live test cannot run.
///
/// `MATRX_LIVE_FEED_REQUIRED=1` turns a skip into a failure, so a pipeline that is meant to
/// exercise the service cannot quietly stop doing it — a live test that skips itself into
/// permanent green is worse than no live test.
fn live_creds() -> Option<LiveCreds> {
    let token = std::env::var("MATRX_FEED_TOKEN").ok().filter(|t| !t.is_empty());
    let org = std::env::var("MATRX_FEED_ORG").ok().filter(|t| !t.is_empty());
    let base = std::env::var("MATRX_FEED_URL")
        .ok()
        .filter(|t| !t.is_empty())
        .unwrap_or_else(|| "https://files.matrxserver.com".to_string());
    match (token, org) {
        (Some(token), Some(organization_id)) => Some(LiveCreds {
            token,
            organization_id,
            base_url: base,
        }),
        _ => {
            let required = std::env::var("MATRX_LIVE_FEED_REQUIRED").as_deref() == Ok("1");
            assert!(
                !required,
                "MATRX_LIVE_FEED_REQUIRED=1 but MATRX_FEED_TOKEN / MATRX_FEED_ORG are not set: \
                 this run was supposed to exercise the live service and did not"
            );
            eprintln!(
                "SKIPPING the live feed test: set MATRX_FEED_TOKEN and MATRX_FEED_ORG (an \
                 admin@admin.com access token and an organization it belongs to) to run it."
            );
            None
        }
    }
}

/// READ-ONLY against the live dev-world service: does the contract this engine was built against
/// match the one the service actually serves?
///
/// This is the only kind of evidence that counts for a wire contract. Every mock in this crate was
/// written from the same reading of the spec as the client, so a mock agreeing with the client
/// proves only that one person was consistent.
#[tokio::test]
async fn the_live_feed_serves_the_contract_this_client_was_built_against() {
    use matrx_sync::feed::{FeedAuth, FeedConfig, FeedTransport, HttpFeed};

    let Some(creds) = live_creds() else { return };
    let mut config = FeedConfig::production();
    config.base_url = creds.base_url.clone();
    config.page_size = 25;
    let feed = HttpFeed::new(config).expect("build a client");
    let auth = FeedAuth {
        access_token: creds.token,
        organization_id: creds.organization_id,
    };

    let page = feed
        .file_page(&auth, None)
        .await
        .expect("the live file feed answers");
    assert!(
        !page.files.is_empty(),
        "the admin account's cloud is empty, so this test proves nothing about the shape"
    );
    assert!(
        page.server_time.is_some(),
        "the engine trusts the SERVER's clock for feed ordering, so it has to be there"
    );

    // Every field the engine depends on, on a real row.
    let live = page
        .files
        .iter()
        .find(|e| !e.is_tombstone())
        .expect("at least one live file");
    assert!(!live.file_id.is_empty() && !live.file_path.is_empty());
    assert!(
        live.version.is_some(),
        "no version means no 412 precondition, which means every write can clobber"
    );
    assert!(
        live.checksum.is_some(),
        "SPEC-SERVER §5: a checksum is never NULL for a sync upload"
    );
    assert!(
        live.organization_id.is_some(),
        "SPEC-SERVER §2.3 makes organization_id NOT optional on the entry"
    );
    // The v2 fields must be PRESENT, which is a different claim from "they deserialised": serde
    // cannot tell a missing field from a null one, so the typed page would parse identically
    // against a v1 server. Every value is legitimately NULL today — `origin_device_id` and
    // `client_modified_at` are written by a daemon, and no daemon exists yet — so the contract
    // check is on the KEYS, read from the raw JSON.
    let raw: serde_json::Value = reqwest::Client::new()
        .get(format!("{}/files/sync/changes?limit=5", creds.base_url.trim_end_matches('/')))
        .header("Authorization", format!("Bearer {}", auth.access_token))
        .header("X-Organization-Id", &auth.organization_id)
        .send()
        .await
        .expect("raw feed read")
        .json()
        .await
        .expect("raw feed json");
    let first = raw["files"]
        .as_array()
        .and_then(|a| a.first())
        .and_then(|e| e.as_object())
        .expect("at least one entry");
    for field in [
        "origin_device_id",
        "client_modified_at",
        "organization_id",
        "version",
        "checksum",
        "deleted_at",
    ] {
        assert!(
            first.contains_key(field),
            "the live feed does not serve `{field}`, which the engine depends on \
             (SPEC-SERVER §2.3); keys served: {:?}",
            first.keys().collect::<Vec<_>>()
        );
    }

    // Paging: the cursor the service hands back is one it accepts.
    if let (true, Some(cursor)) = (page.has_more, page.next_cursor.clone()) {
        let second = feed
            .file_page(&auth, Some(&cursor))
            .await
            .expect("the cursor the service gave us is one it accepts");
        let first_ids: Vec<&str> = page.files.iter().map(|e| e.file_id.as_str()).collect();
        assert!(
            second.files.iter().all(|e| !first_ids.contains(&e.file_id.as_str())),
            "a keyset cursor must not repeat the page it came from"
        );
    }

    // The folder feed adopts the file feed's contract verbatim (SPEC-SERVER §2.4).
    let folders = feed
        .folder_page(&auth, None)
        .await
        .expect("the live folder feed answers");
    assert!(
        !folders.truncated,
        "SPEC-SERVER §2.4: `truncated` stays for old clients and is now always false"
    );
    if !folders.folders.is_empty() {
        assert!(!folders.folders[0].folder_id.is_empty());
        assert!(!folders.folders[0].folder_path.is_empty());
    }
}

/// The live service refuses a request with no organization, and the client says so usefully.
///
/// SPEC-SERVER names `organization_id` on the ENTRY but never names the request header; a client
/// built from the spec alone gets a 400 on its first call, which is why this is pinned against the
/// real service rather than trusted to prose.
#[tokio::test]
async fn the_live_service_requires_the_organization_header() {
    use matrx_sync::feed::{FeedAuth, FeedConfig, FeedError, FeedTransport, HttpFeed};

    let Some(creds) = live_creds() else { return };
    let mut config = FeedConfig::production();
    config.base_url = creds.base_url.clone();
    let feed = HttpFeed::new(config).expect("build a client");

    let error = feed
        .file_page(
            &FeedAuth {
                access_token: creds.token,
                organization_id: String::new(),
            },
            None,
        )
        .await
        .expect_err("an identity with no organization must be refused, not guessed at");
    assert!(
        matches!(error, FeedError::OrganizationRefused(_)),
        "got {error:?}"
    );
    assert!(!error.retryable(), "retrying without the header fails forever");
}

// ------------------------------------ the Realtime trigger, and the polling fallback

use matrx_sync::feed::{
    heartbeat_frame, interpret, join_frame, LiveState, LiveTracker, PhoenixFrame, Signal,
    WatchedTable,
};

#[test]
fn the_join_frame_subscribes_to_the_table_and_carries_the_token() {
    let frame = join_frame(WatchedTable::Files, "tok-123", "1");
    assert_eq!(frame.topic, "realtime:files:files");
    assert_eq!(frame.event, "phx_join");
    assert_eq!(frame.payload["access_token"], "tok-123");
    let changes = &frame.payload["config"]["postgres_changes"][0];
    assert_eq!(changes["schema"], "files");
    assert_eq!(changes["table"], "files");
    assert_eq!(changes["event"], "*");
    assert_eq!(
        frame.payload["config"]["broadcast"]["self"], false,
        "we never want our own writes echoed back over the socket"
    );

    // The folder channel is the same shape on the other table.
    assert_eq!(
        join_frame(WatchedTable::Folders, "t", "2").topic,
        "realtime:files:folders"
    );

    // The frame serialises with Phoenix's field names, including `ref` (not `message_ref`).
    let json = serde_json::to_value(&frame).expect("serialise");
    assert!(json.get("ref").is_some() && json.get("topic").is_some());
}

#[test]
fn a_change_is_a_trigger_and_its_payload_is_deliberately_discarded() {
    // Realtime cannot carry a delete: Postgres replica identity does not put the old row on the
    // wire. An engine that read rows from here would resurrect every deleted file on reconnect.
    let frame = PhoenixFrame {
        join_ref: None,
        message_ref: None,
        topic: "realtime:files:files".to_string(),
        event: "postgres_changes".to_string(),
        payload: serde_json::json!({"data": {"record": {"file_path": "Docs/a.txt"}}}),
    };
    assert_eq!(
        interpret(&frame),
        Signal::ChangedGoRead,
        "the only thing a change means is: read the feed now"
    );
}

#[test]
fn replies_errors_and_heartbeats_are_told_apart() {
    let ok = PhoenixFrame {
        join_ref: Some("1".into()),
        message_ref: Some("1".into()),
        topic: "realtime:files:files".into(),
        event: "phx_reply".into(),
        payload: serde_json::json!({"status": "ok", "response": {}}),
    };
    assert_eq!(interpret(&ok), Signal::Joined);

    let refused = PhoenixFrame {
        payload: serde_json::json!({"status": "error", "response": {"reason": "expired token"}}),
        ..ok.clone()
    };
    assert!(matches!(interpret(&refused), Signal::JoinRefused(_)));

    let closed = PhoenixFrame {
        event: "phx_close".into(),
        ..ok.clone()
    };
    assert!(matches!(interpret(&closed), Signal::JoinRefused(_)));

    let beat = PhoenixFrame {
        topic: "phoenix".into(),
        ..ok.clone()
    };
    assert_eq!(interpret(&beat), Signal::HeartbeatAck);
    assert_eq!(heartbeat_frame("9").topic, "phoenix");
}

#[test]
fn one_dropped_socket_is_not_news_but_a_pattern_is_announced() {
    let mut tracker = LiveTracker::new();
    assert_eq!(tracker.state(), LiveState::Connecting);
    assert_eq!(
        tracker.state().honest_state(),
        None,
        "a state that flickers on every reconnect is noise, and the poll already covers it"
    );

    tracker.note_joined(100);
    assert_eq!(tracker.state(), LiveState::Live);

    // A laptop lid-close drops one socket. That is ordinary.
    tracker.note_failure(200);
    assert_eq!(tracker.state(), LiveState::Live);

    // Twice is a pattern, and the user is told.
    tracker.note_failure(210);
    assert_eq!(tracker.state(), LiveState::PollingFallback);
    assert_eq!(tracker.state().honest_state(), Some("polling_fallback"));

    // Reconnecting clears it.
    tracker.note_joined(300);
    assert_eq!(tracker.state(), LiveState::Live);
    assert_eq!(tracker.state().honest_state(), None);
}

#[test]
fn a_socket_that_is_open_but_dead_is_caught_by_silence() {
    // The failure mode a plain "is it connected?" check never sees: the TCP connection is up, the
    // server has forgotten us, and nothing ever arrives again.
    let mut tracker = LiveTracker::with_tolerance(1);
    tracker.note_joined(1_000);
    tracker.note_tick(1_030, 60);
    assert_eq!(tracker.state(), LiveState::Live, "half a minute of quiet is quiet");

    tracker.note_tick(1_100, 60);
    assert_eq!(
        tracker.state(),
        LiveState::PollingFallback,
        "past the silence limit the socket is not believed, however open it looks"
    );
}

#[test]
fn the_fallback_says_what_is_happening_and_that_syncing_continues() {
    // SCOPE §3.1 item 8 requires this be "announced on screen". A sync engine that silently
    // degrades to a one-minute poll is one users describe as "sometimes slow for no reason".
    let message = LiveState::PollingFallback
        .message(60)
        .expect("the fallback must have something to say");
    assert!(
        message.contains("Live updates are unavailable"),
        "almost the spec's own words: {message}"
    );
    assert!(message.contains("minute"), "and the cadence: {message}");
    assert!(
        message.contains("still syncs"),
        "and that nothing is broken — a state that is only a complaint is not a state: {message}"
    );
    assert_eq!(LiveState::Live.message(60), None);
}
