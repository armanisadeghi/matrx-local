//! Dependency order: the one thing the planner deliberately does not decide.
//!
//! [`crate::plan`] emits ops in **path order**, because a pure function of three trees has no
//! reason to prefer one order over another and path order is what makes its output diffable. The
//! executor cannot use that order, because path order puts `Notes/plan.md` before `Notes` is
//! created, and it puts a `Download{Create}` at `a.txt` before the `Rename` that frees `a.txt`.
//!
//! So ordering lives here, where it belongs, as a **stable** sort: ops of equal rank keep the
//! planner's own order, so a plan's execution order is still a deterministic function of the plan.
//!
//! The ranks, and the failure each one prevents:
//!
//! | Rank | Ops | Without it |
//! |---|---|---|
//! | 0 | `HashRequest` | every later op on that path acts on an unknown hash (I9) |
//! | 1 | `MkdirLocal`, `MkdirRemote` (shallowest first) | a write into a directory that does not exist |
//! | 2 | `ConflictCopy` | the losing bytes are overwritten before they are copied (D7) |
//! | 3 | `Rename` | a create reusing the freed path lands first and the rename then collides |
//! | 4 | `Upload`, `Download` | — |
//! | 5 | `FlagLocalEdit`, `RecordConflict`, `RecordSynced`, `ForgetSynced` | bookkeeping before the act it records |
//! | 6 | `DeleteLocalToTrash`, `DeleteRemoteTombstone` (deepest first) | a parent directory removed before its children, and a delete racing the rename that was going to save the file |
//!
//! Deletes are last **and** deepest-first: a delete is the only irreversible act in the list, so
//! it happens after everything that might have moved the bytes somewhere safe.

use crate::planner::PlanOp;

/// The rank of one op. Lower runs first.
pub fn rank(op: &PlanOp) -> u8 {
    match op {
        PlanOp::HashRequest { .. } => 0,
        PlanOp::MkdirLocal { .. } | PlanOp::MkdirRemote { .. } => 1,
        PlanOp::ConflictCopy { .. } => 2,
        PlanOp::Rename { .. } => 3,
        PlanOp::Upload { .. } | PlanOp::Download { .. } => 4,
        PlanOp::FlagLocalEdit { .. }
        | PlanOp::RecordConflict { .. }
        | PlanOp::RecordSynced { .. }
        | PlanOp::ForgetSynced { .. } => 5,
        PlanOp::DeleteLocalToTrash { .. } | PlanOp::DeleteRemoteTombstone { .. } => 6,
    }
}

/// How deep a path sits, counted in separators. `""` is the mapping root.
pub fn depth(path: &str) -> usize {
    if path.is_empty() {
        0
    } else {
        path.matches('/').count() + 1
    }
}

/// Order a plan's ops for execution.
///
/// Stable within a rank, so two runs of the same plan execute identically — which is what makes a
/// crash-and-resume test reproducible.
pub fn order(ops: &[PlanOp]) -> Vec<PlanOp> {
    let mut out: Vec<PlanOp> = ops.to_vec();
    out.sort_by_key(|op| {
        let r = rank(op);
        // Directories shallowest-first so a parent exists before its child; deletes deepest-first
        // so a child is gone before its parent. Everything else keeps the planner's path order,
        // which the stable sort preserves.
        let within = match op {
            PlanOp::MkdirLocal { path } | PlanOp::MkdirRemote { path } => depth(path) as i64,
            PlanOp::DeleteLocalToTrash { path, .. } | PlanOp::DeleteRemoteTombstone { path, .. } => {
                -(depth(path) as i64)
            }
            _ => 0,
        };
        (r, within)
    });
    out
}

/// Whether `path` is `ancestor` itself or sits underneath it.
///
/// Used to skip the descendants of an op that failed: if `Notes` could not be created, planning to
/// write `Notes/plan.md` into it would produce a second, less honest failure for the same cause.
pub fn is_under(path: &str, ancestor: &str) -> bool {
    if ancestor.is_empty() {
        return true;
    }
    path == ancestor || path.starts_with(&format!("{ancestor}/"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::ConflictKind;
    use crate::planner::{Change, Side};

    fn upload(path: &str) -> PlanOp {
        PlanOp::Upload {
            path: path.to_string(),
            change: Change::Create,
            expected_version: None,
            expected_checksum: None,
            local_hash: "h".to_string(),
        }
    }

    #[test]
    fn directories_come_before_the_files_inside_them() {
        let ops = vec![
            upload("a/b/c.txt"),
            PlanOp::MkdirLocal {
                path: "a/b".to_string(),
            },
            PlanOp::MkdirLocal {
                path: "a".to_string(),
            },
        ];
        let ordered = order(&ops);
        assert_eq!(ordered[0].path(), "a");
        assert_eq!(ordered[1].path(), "a/b");
        assert_eq!(ordered[2].path(), "a/b/c.txt");
    }

    #[test]
    fn a_rename_frees_its_path_before_a_create_reuses_it() {
        let ops = vec![
            upload("a.txt"),
            PlanOp::Rename {
                side: Side::Local,
                from: "a.txt".to_string(),
                to: "b.txt".to_string(),
                expected_version: None,
                expected_checksum: None,
            },
        ];
        let ordered = order(&ops);
        assert!(matches!(ordered[0], PlanOp::Rename { .. }));
    }

    #[test]
    fn the_losing_copy_is_written_before_the_winner_overwrites_it() {
        let ops = vec![
            PlanOp::Download {
                path: "a.txt".to_string(),
                change: Change::Update,
                remote_file_id: None,
                remote_version: None,
                checksum: "remote".to_string(),
                expected_local_hash: Some("local".to_string()),
            },
            PlanOp::ConflictCopy {
                path: "a.txt".to_string(),
                copy_path: "a (conflicted copy).txt".to_string(),
                kind: ConflictKind::BothModified,
                local_hash: Some("local".to_string()),
            },
        ];
        let ordered = order(&ops);
        assert!(matches!(ordered[0], PlanOp::ConflictCopy { .. }));
    }

    #[test]
    fn deletes_run_last_and_deepest_first() {
        let ops = vec![
            PlanOp::DeleteLocalToTrash {
                path: "a".to_string(),
                to_trash: true,
                expected_local_hash: None,
            },
            PlanOp::DeleteLocalToTrash {
                path: "a/b/c.txt".to_string(),
                to_trash: true,
                expected_local_hash: None,
            },
            upload("z.txt"),
        ];
        let ordered = order(&ops);
        assert_eq!(ordered[0].path(), "z.txt");
        assert_eq!(ordered[1].path(), "a/b/c.txt");
        assert_eq!(ordered[2].path(), "a");
    }

    #[test]
    fn equal_rank_keeps_the_planners_own_order() {
        let ops = vec![upload("z.txt"), upload("a.txt"), upload("m.txt")];
        let ordered = order(&ops);
        let paths: Vec<&str> = ordered.iter().map(PlanOp::path).collect();
        assert_eq!(paths, vec!["z.txt", "a.txt", "m.txt"]);
    }

    #[test]
    fn descendants_are_recognised_without_swallowing_a_sibling_prefix() {
        assert!(is_under("Photos/a.jpg", "Photos"));
        assert!(is_under("Photos", "Photos"));
        assert!(!is_under("PhotosOld/a.jpg", "Photos"));
    }
}
