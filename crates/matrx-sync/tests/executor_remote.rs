//! FS-L1 unit 4c — the live file service's answers, classified.
//!
//! Every body below was captured from `files.matrxserver.com` / `server.app.matrxserver.com` as
//! `admin@admin.com` on 2026-09-24. The classification is the part of the HTTP client most likely
//! to be wrong in a way that costs data — a 412 read as a transient is a blind retry, an
//! organization refusal read as offline spins forever — so it is pinned against real bytes.
//! The round trip itself is the live tier (`tests/live_service.rs`, gated).

use matrx_sync::exec::remote::{classify, HttpRemote, RemoteConfig, StaticToken};
use matrx_sync::exec::ExecError;
use matrx_sync::knobs::TransferKnobs;
use std::sync::Arc;

const LIVE_412: &str = r#"{"detail":{"error":"precondition_failed","message":"the file changed on the server since you last read it","file_id":"784af070-3f5f-4989-ba59-79361477b53e","current_checksum":"dbc26759b6cc156c9dd67e4fb56981c1823c13450d36106d508b26abd27678a7","current_version":1,"current_size_bytes":11,"current_updated_at":"2026-09-24 16:37:39.681528+00:00","origin_device_id":null,"expected_checksum":"absent"}}"#;
const LIVE_404: &str = r#"{"detail":{"error":"not_found","message":"File '784af070-3f5f-4989-ba59-79361477b53e' not found"}}"#;
const LIVE_ORG_REQUIRED: &str = r#"{"error":"organization_required","code":"organization_required","message":"This request carried an identity but no organization.","details":null}"#;
const LIVE_ORG_FORBIDDEN: &str = r#"{"error":"organization_forbidden","code":"organization_forbidden","message":"This request named organization 00000000-0000-0000-0000-000000000001 and you are not a member of it."}"#;

#[test]
fn a_live_412_carries_the_whole_conflict_input_out_of_the_detail_wrapper() {
    let e = classify(412, LIVE_412, "a.txt");
    let ExecError::PreconditionFailed(p) = &e else {
        panic!("{e:?}");
    };
    assert_eq!(p.current_version, Some(1));
    assert_eq!(
        p.current_checksum.as_deref(),
        Some("dbc26759b6cc156c9dd67e4fb56981c1823c13450d36106d508b26abd27678a7")
    );
    assert_eq!(p.expected_checksum.as_deref(), Some("absent"));
    assert!(e.is_race(), "a 412 re-plans");
    assert!(!e.retryable(), "and is never retried blind");
}

#[test]
fn the_organization_gate_is_a_state_not_an_outage() {
    for body in [LIVE_ORG_REQUIRED, LIVE_ORG_FORBIDDEN] {
        let e = classify(400, body, "a.txt");
        assert!(matches!(e, ExecError::OrganizationRefused { .. }), "{e:?}");
        assert_eq!(e.honest_state(), Some("admission_revoked"));
        assert!(!e.retryable());
    }
}

#[test]
fn only_an_unverifiable_organization_is_worth_retrying() {
    let body = r#"{"error":"organization_unverifiable","code":"organization_unverifiable"}"#;
    let e = classify(503, body, "a.txt");
    assert!(e.retryable(), "{e:?}");
    assert!(!matches!(e, ExecError::OrganizationRefused { .. }));
}

#[test]
fn a_missing_row_is_gone_and_a_refused_token_needs_sign_in() {
    assert!(matches!(
        classify(404, LIVE_404, "a.txt"),
        ExecError::RemoteGone { .. }
    ));
    let e = classify(401, "{}", "a.txt");
    assert_eq!(e.honest_state(), Some("sign_in_needed"));
}

#[test]
fn a_reused_idempotency_key_is_a_loud_defect_and_a_path_collision_is_a_conflict() {
    let reuse = classify(
        409,
        r#"{"detail":{"error":"idempotency_key_reuse"}}"#,
        "a.txt",
    );
    assert!(matches!(reuse, ExecError::Refused(_)), "{reuse:?}");
    let collide = classify(409, r#"{"detail":{"error":"path_conflict"}}"#, "a.txt");
    assert!(matches!(collide, ExecError::PathConflict { .. }), "{collide:?}");
}

#[test]
fn a_full_organization_is_over_quota() {
    for (status, body) in [(507, "{}"), (413, "{}"), (400, r#"{"detail":{"error":"quota_exceeded"}}"#)] {
        let e = classify(status, body, "a.txt");
        assert_eq!(e.honest_state(), Some("over_quota"), "{status} {body}");
    }
}

#[test]
fn server_trouble_is_retried_with_backoff() {
    for status in [500, 502, 503, 429] {
        assert!(classify(status, "boom", "a.txt").retryable(), "{status}");
    }
}

#[test]
fn a_folder_mapping_joins_keys_onto_its_own_prefix_and_never_swallows_the_root() {
    let make = |prefix: &str| {
        HttpRemote::new(
            RemoteConfig {
                files_base_url: "https://files.example".into(),
                host_base_url: "https://host.example".into(),
                organization_id: "org".into(),
                device_id: None,
                cloud_prefix: prefix.into(),
            },
            Arc::new(StaticToken("t".into())),
            &TransferKnobs::default(),
        )
        .expect("client")
    };
    assert_eq!(make("").cloud_path("Notes/a.md"), "Notes/a.md");
    assert_eq!(make("Projects/X").cloud_path("Notes/a.md"), "Projects/X/Notes/a.md");
    assert_eq!(make("/Projects/X/").cloud_path(""), "Projects/X");
}

#[test]
fn transfer_knobs_are_refused_out_of_range_never_clamped() {
    assert!(TransferKnobs::default().validate().is_ok());
    let zero = TransferKnobs {
        transfer_concurrency: 0,
        ..TransferKnobs::default()
    };
    assert!(zero.validate().is_err());
    let hang = TransferKnobs {
        request_timeout_s: 0,
        ..TransferKnobs::default()
    };
    assert!(hang.validate().is_err());
}
