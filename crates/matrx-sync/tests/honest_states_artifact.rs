//! E16: `contracts/honest_states.json` is generated from the Rust enum and checked in.
//!
//! Three consumers read that file — SPEC-ENGINE §3.6's tables, SPEC-SERVER's generated
//! `files.sync_mappings.state` CHECK, and SPEC-SERVER's divergence test. A stale file would
//! therefore ship a database CHECK that disagrees with the daemon's vocabulary, so this test
//! fails when the file on disk is not exactly what the enum renders.
//!
//! To regenerate after changing the enum:
//!
//! ```text
//! UPDATE_CONTRACTS=1 cargo test -p matrx-sync --test honest_states_artifact
//! ```

use std::path::PathBuf;

fn artifact_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("contracts/honest_states.json")
}

#[test]
fn checked_in_artifact_matches_the_enum() {
    let expected = matrx_sync::states::artifact_json();
    let path = artifact_path();

    if std::env::var("UPDATE_CONTRACTS").is_ok() {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir).expect("create contracts/");
        }
        std::fs::write(&path, &expected).expect("write the artifact");
        return;
    }

    let actual = std::fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!(
            "{} is missing or unreadable ({e}); regenerate with \
             UPDATE_CONTRACTS=1 cargo test -p matrx-sync --test honest_states_artifact",
            path.display()
        )
    });
    assert_eq!(
        actual,
        expected,
        "\n{} is stale. Regenerate with:\n  \
         UPDATE_CONTRACTS=1 cargo test -p matrx-sync --test honest_states_artifact\n",
        path.display()
    );
}

#[test]
fn artifact_shape_is_what_spec_server_reads() {
    let parsed: serde_json::Value =
        serde_json::from_str(&matrx_sync::states::artifact_json()).expect("valid JSON");
    let arr = parsed.as_array().expect("a JSON array");
    assert!(!arr.is_empty());
    for entry in arr {
        let o = entry.as_object().expect("each entry is an object");
        assert!(o.contains_key("value"), "every entry carries `value`");
        assert!(o.contains_key("scopes"), "every entry carries `scopes`");
        assert!(o.contains_key("title"), "every entry carries `title`");
        assert!(
            o.contains_key("remedy_action"),
            "every entry carries `remedy_action`"
        );
        let scopes = o["scopes"].as_array().expect("scopes is an array");
        assert!(!scopes.is_empty(), "{} has no scope", o["value"]);
        for s in scopes {
            let s = s.as_str().expect("a scope is a string");
            assert!(
                matches!(s, "session" | "device" | "mapping"),
                "unknown scope {s}"
            );
        }
    }
}
