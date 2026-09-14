//! Conflict-copy naming (D7) and client-side name safety.
//!
//! Both are **pure**: a name is computed from values the caller supplies — the device name and the
//! date arrive as strings, never from a clock or a hostname lookup — so the planner stays a
//! function of its arguments.

use crate::knobs::Knobs;
use crate::model::ConflictKind;

/// A conflict copy's name, or the reason no legal one exists.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CopyName {
    /// A legal, free name for the losing bytes.
    Ok(String),
    /// No legal name exists — the parent path, the device name or the template itself makes every
    /// candidate unrepresentable. The planner turns this into a conflict row for the path and
    /// **does not** plan the download that would replace the original.
    Unrepresentable(NameVerdict),
}

/// Render a conflict-copy name from `sync.conflict_copy_template` (D7), **uniquified and
/// name-guarded**.
///
/// Three things have to be true of the returned name, and each one was a defect before it was
/// checked here:
///
/// 1. **It must be free.** Two conflicts on one path, on one day, from one device rendered the
///    same name and the second overwrote the first (F1). A ` (2)`, ` (3)` … suffix is inserted
///    before the extension until a free one is found — what Dropbox and OneDrive do.
/// 2. **"Free" must mean what it means everywhere else in this crate.** `taken` is asked about the
///    candidate's [`collision_key`], not its bytes: on a case-insensitive volume
///    `X (Conflicted Copy …).txt` and `x (conflicted copy …).txt` are one file, and an exact-match
///    check walked straight onto the existing copy (G2). Mixed-case copy names are ordinary —
///    `sync.conflict_copy_template` is an org knob, and copies migrated in from Dropbox or
///    OneDrive carry their own capitalisation.
/// 3. **It must be creatable.** The template adds forty-odd characters to the stem, so a file whose
///    own name is legal can render a copy name that is not. The copy write then fails on a real
///    volume while the download that replaces the original succeeds — the F1 outcome again, and
///    invisible to a mock filesystem with no name limits (G1). The stem is shortened to fit, as
///    Dropbox does; if no length of stem can be made legal, the answer is
///    [`CopyName::Unrepresentable`] and the caller must not plan the replacement.
///
/// `taken` is a pure predicate the caller supplies over every name it knows about: all three trees,
/// plus any copy the same plan has already claimed.
pub fn unique_conflict_copy_path(
    path_nfc: &str,
    device: &str,
    date: &str,
    knobs: &Knobs,
    taken: impl Fn(&str) -> bool,
) -> CopyName {
    let (parent, file) = split_parent(path_nfc);
    let (stem, ext) = split_stem_ext(file);
    let stem_chars: Vec<char> = stem.chars().collect();

    // `None` is the plain rendered name; `Some(n)` adds the ` (n)` uniquifier.
    let suffixes = std::iter::once(None).chain((2..10_000u32).map(Some));
    let mut last_verdict = NameVerdict::Ok;
    for suffix in suffixes {
        // Shorten the stem until the whole rendered path is legal. Longest first, so a name that
        // already fits is never truncated.
        let mut fitted: Option<String> = None;
        for keep in (1..=stem_chars.len()).rev() {
            let short: String = stem_chars[..keep].iter().collect();
            let candidate = render(parent, &short, ext, device, date, suffix, knobs);
            match check_name(&candidate, knobs) {
                NameVerdict::Ok => {
                    fitted = Some(candidate);
                    break;
                }
                // Truncating the stem cannot fix an illegal character or a reserved word coming
                // from the parent, the device name or the template.
                v @ NameVerdict::Illegal => {
                    last_verdict = v;
                    break;
                }
                v @ NameVerdict::TooLong(_) => last_verdict = v,
            }
        }
        match fitted {
            Some(candidate) if !taken(&candidate) => return CopyName::Ok(candidate),
            Some(_) => continue, // legal but occupied: try the next suffix
            None => return CopyName::Unrepresentable(last_verdict),
        }
    }
    CopyName::Unrepresentable(last_verdict)
}

fn render(
    parent: &str,
    stem: &str,
    ext: &str,
    device: &str,
    date: &str,
    suffix: Option<u32>,
    knobs: &Knobs,
) -> String {
    let base = knobs
        .conflict_copy_template
        .replace("{stem}", stem)
        .replace("{ext}", "")
        .replace("{device}", device)
        .replace("{YYYY-MM-DD}", date);
    let name = match suffix {
        None => format!("{base}{ext}"),
        Some(n) => format!("{base} ({n}){ext}"),
    };
    match parent {
        "" => name,
        p => format!("{p}/{name}"),
    }
}

/// Render a conflict-copy name from `sync.conflict_copy_template` (D7) — the raw template only.
///
/// **Never plan with this.** It neither uniquifies nor name-guards, which is two of the three
/// defects [`unique_conflict_copy_path`] exists to prevent. It is public so the template itself can
/// be tested and so a surface can show a user what a copy would be called.
pub fn conflict_copy_path(path_nfc: &str, device: &str, date: &str, knobs: &Knobs) -> String {
    let (parent, file) = split_parent(path_nfc);
    let (stem, ext) = split_stem_ext(file);
    render(parent, stem, ext, device, date, None, knobs)
}

/// Split a mapping-relative path into `(parent, file name)`. The parent is `""` at the root.
pub fn split_parent(path_nfc: &str) -> (&str, &str) {
    match path_nfc.rfind('/') {
        Some(i) => (&path_nfc[..i], &path_nfc[i + 1..]),
        None => ("", path_nfc),
    }
}

/// Split a file name into `(stem, extension including the dot)`.
///
/// A leading dot is part of the stem, so `.env` keeps its name and does not become `` + `.env`.
pub fn split_stem_ext(file: &str) -> (&str, &str) {
    match file.rfind('.') {
        Some(i) if i > 0 => (&file[..i], &file[i..]),
        _ => (file, ""),
    }
}

/// What a name-safety check concluded. `Ok` is the only verdict that allows a create.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NameVerdict {
    /// The name is safe to create on the other side.
    Ok,
    /// A segment or the whole path exceeds its knob.
    TooLong(ConflictKind),
    /// The name cannot exist on one of the three platforms we ship to.
    Illegal,
}

/// Characters no Windows path may carry, plus the ASCII control range.
const WINDOWS_FORBIDDEN: &[char] = &['<', '>', ':', '"', '|', '?', '*', '\\'];

/// Names Windows reserves regardless of extension.
const WINDOWS_RESERVED: &[&str] = &[
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8",
    "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
];

/// Check a mapping-relative path against the name-safety knobs and the cross-platform rules.
///
/// The limits are knobs, never constants: `files.max_path_chars` (default 400, the cloud limit) and
/// `sync.max_segment_chars` (default 255) both arrive in [`Knobs`].
///
/// A failing verdict is a **plan item** — [`crate::planner::PlanOp::RecordConflict`] — never a
/// rename the engine performs behind the user's back (invariant I8's spirit: never a silent
/// rename).
pub fn check_name(path_nfc: &str, knobs: &Knobs) -> NameVerdict {
    if path_nfc.chars().count() > knobs.max_path_chars as usize {
        return NameVerdict::TooLong(ConflictKind::PathTooLong);
    }
    for segment in path_nfc.split('/') {
        if segment.is_empty() {
            return NameVerdict::Illegal;
        }
        if segment.chars().count() > knobs.max_segment_chars as usize {
            return NameVerdict::TooLong(ConflictKind::PathTooLong);
        }
        if segment.chars().any(|c| {
            WINDOWS_FORBIDDEN.contains(&c) || (c as u32) < 0x20 || c == '\u{7f}'
        }) {
            return NameVerdict::Illegal;
        }
        if segment.ends_with(' ') || segment.ends_with('.') {
            return NameVerdict::Illegal;
        }
        let (stem, _) = split_stem_ext(segment);
        if WINDOWS_RESERVED
            .iter()
            .any(|r| stem.eq_ignore_ascii_case(r))
        {
            return NameVerdict::Illegal;
        }
    }
    NameVerdict::Ok
}

/// The case-folded, whitespace-trimmed key two paths collide on.
///
/// Two distinct NFC paths sharing a key are a `case_collision` (they differ only in case) or a
/// `whitespace_collision` (they differ only in trailing/leading whitespace) — on a
/// case-insensitive volume one of them cannot exist, so it becomes a conflict row rather than a
/// silent overwrite.
pub fn collision_key(path_nfc: &str) -> String {
    path_nfc
        .split('/')
        .map(|s| nfc(s.trim()).to_lowercase())
        .collect::<Vec<_>>()
        .join("/")
}

/// NFC-normalise a string.
///
/// Invariant I8: the journal is NFC everywhere, and NFD on disk against NFC in the cloud is a
/// `unicode_collision`, **never a silent rename**. On APFS and NTFS the two spellings are one
/// filesystem entry, so without this the executor would clobber one with the other and say
/// nothing — which is exactly what independent verification reproduced (F5).
pub fn nfc(s: &str) -> String {
    use unicode_normalization::UnicodeNormalization;
    s.nfc().collect()
}

/// Which collision class two colliding paths are in.
pub fn collision_kind(a: &str, b: &str) -> ConflictKind {
    // Precedence rule: **normalisation outranks case.** Fold case out of both sides first, then
    // ask whether normalisation is what reconciles them. If the case-folded forms still differ but
    // their NFC forms agree, the pair differs in Unicode spelling — `unicode_collision` — whether
    // or not it ALSO differs in case. Only when case folding alone makes them identical is it a
    // `case_collision`.
    //
    // Testing raw NFC equality first reported a twin that is also a case twin
    // (`cafe\u{301}.txt` against `CAFÉ.txt`) as a mere `case_collision`: safe, but it tells the
    // user the wrong reason to rename, and `unicode_collision` exists precisely so the real reason
    // can be shown (G6).
    let (la, lb) = (a.to_lowercase(), b.to_lowercase());
    if nfc(&la) == nfc(&lb) {
        if la != lb {
            ConflictKind::UnicodeCollision
        } else if a != b {
            ConflictKind::CaseCollision
        } else {
            ConflictKind::WhitespaceCollision
        }
    } else {
        // They collide only after whitespace is trimmed too.
        ConflictKind::WhitespaceCollision
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_default_template_is_the_dropbox_convention() {
        let k = Knobs::default();
        assert_eq!(
            conflict_copy_path("notes/report.md", "Arman's MacBook", "2026-09-13", &k),
            "notes/report (conflicted copy from Arman's MacBook 2026-09-13).md"
        );
    }

    #[test]
    fn a_conflict_copy_stays_in_its_own_folder() {
        let k = Knobs::default();
        let copy = conflict_copy_path("a/b/c/file.txt", "dev", "2026-09-13", &k);
        assert!(copy.starts_with("a/b/c/"), "{copy}");
    }

    #[test]
    fn a_dotfile_keeps_its_name() {
        assert_eq!(split_stem_ext(".env"), (".env", ""));
        assert_eq!(split_stem_ext("archive.tar.gz"), ("archive.tar", ".gz"));
        assert_eq!(split_stem_ext("README"), ("README", ""));
    }

    #[test]
    fn name_safety_reads_its_limits_from_knobs_not_constants() {
        let tight = Knobs {
            max_segment_chars: 4,
            ..Knobs::default()
        };
        assert_eq!(check_name("abcde", &tight), NameVerdict::TooLong(ConflictKind::PathTooLong));
        assert_eq!(check_name("abcd", &tight), NameVerdict::Ok);
    }

    #[test]
    fn windows_hostile_names_are_illegal_on_every_platform() {
        let k = Knobs::default();
        assert_eq!(check_name("a<b.txt", &k), NameVerdict::Illegal);
        assert_eq!(check_name("CON.txt", &k), NameVerdict::Illegal);
        assert_eq!(check_name("trailing .txt", &k), NameVerdict::Ok);
        assert_eq!(check_name("trailing. ", &k), NameVerdict::Illegal);
        assert_eq!(check_name("ok/name.txt", &k), NameVerdict::Ok);
    }

    fn free(_: &str) -> bool {
        false
    }

    #[test]
    fn a_conflict_copy_that_cannot_be_named_is_reported_not_guessed() {
        // G1, from hostile re-verification — data loss. The template adds forty-odd characters, so
        // a file whose own name is legal renders a copy name that is not. The copy write then
        // fails on a real volume while the download replacing the original succeeds.
        let k = Knobs::default();
        let long = format!("{}.txt", "s".repeat(240));
        let name = unique_conflict_copy_path(&long, "device-a", "2026-09-13", &k, free);
        let CopyName::Ok(copy) = name else {
            panic!("a 240-character stem can be shortened to fit: {name:?}");
        };
        assert_eq!(
            check_name(&copy, &k),
            NameVerdict::Ok,
            "the planned copy name must itself be creatable: {copy}"
        );
        assert!(
            copy.contains("conflicted copy from device-a"),
            "and still recognisable: {copy}"
        );

        // When no stem length can be made legal, say so rather than inventing a name.
        let hostile = Knobs {
            max_segment_chars: 8,
            ..Knobs::default()
        };
        assert!(matches!(
            unique_conflict_copy_path("x.txt", "device-a", "2026-09-13", &hostile, free),
            CopyName::Unrepresentable(_)
        ));
    }

    #[test]
    fn copy_names_are_free_by_the_crates_own_fold_not_by_bytes() {
        // G2, from hostile re-verification — data loss. On a case-insensitive volume
        // `X (Conflicted Copy …)` and `x (conflicted copy …)` are ONE file, so an exact-match
        // `taken` walked straight onto the existing copy.
        let k = Knobs::default();
        let existing = "x (Conflicted Copy From device-a 2026-09-13).txt";
        let name = unique_conflict_copy_path("x.txt", "device-a", "2026-09-13", &k, |c| {
            collision_key(c) == collision_key(existing)
        });
        let CopyName::Ok(copy) = name else {
            panic!("a free name exists: {name:?}");
        };
        assert_ne!(
            collision_key(&copy),
            collision_key(existing),
            "the copy must not fold onto the existing one: {copy}"
        );
        assert!(copy.ends_with(" (2).txt"), "{copy}");
    }

    #[test]
    fn a_conflict_copy_never_overwrites_an_earlier_one() {
        // F1, from independent verification: without a uniquifier, the second conflict on one
        // path on one day from one device destroyed the first copy.
        let k = Knobs::default();
        let CopyName::Ok(first) = unique_conflict_copy_path("x.txt", "device-a", "2026-09-13", &k, free)
        else {
            panic!("a free name exists")
        };
        assert_eq!(first, "x (conflicted copy from device-a 2026-09-13).txt");
        let CopyName::Ok(second) =
            unique_conflict_copy_path("x.txt", "device-a", "2026-09-13", &k, |c| c == first)
        else {
            panic!("a free name exists")
        };
        assert_eq!(second, "x (conflicted copy from device-a 2026-09-13) (2).txt");
        let CopyName::Ok(third) =
            unique_conflict_copy_path("x.txt", "device-a", "2026-09-13", &k, |c| {
                c == first || c == second
            })
        else {
            panic!("a free name exists")
        };
        assert_eq!(third, "x (conflicted copy from device-a 2026-09-13) (3).txt");
        assert_ne!(first, second);
        assert_ne!(second, third);
    }

    #[test]
    fn nfc_and_nfd_twins_collide_and_are_named_as_such() {
        // "café" spelled decomposed (e + combining acute) and precomposed. On APFS and NTFS these
        // are ONE filesystem entry.
        let nfd = "cafe\u{301}.txt";
        let nfc_name = "caf\u{e9}.txt";
        assert_ne!(nfd, nfc_name, "the two spellings differ byte for byte");
        assert_eq!(
            collision_key(nfd),
            collision_key(nfc_name),
            "they must fold to one collision key (I8)"
        );
        assert_eq!(
            collision_kind(nfd, nfc_name),
            ConflictKind::UnicodeCollision
        );
        // G6: a twin that is ALSO a case twin is still a unicode collision — normalisation
        // outranks case, because that is the reason the user has to act on.
        assert_eq!(
            collision_kind("cafe\u{301}.txt", "CAF\u{c9}.txt"),
            ConflictKind::UnicodeCollision
        );
    }

    #[test]
    fn collisions_are_classified_not_silently_merged() {
        assert_eq!(collision_key("A/B.txt"), "a/b.txt");
        assert_eq!(collision_key("a/b.txt "), "a/b.txt");
        assert_eq!(collision_kind("A.txt", "a.txt"), ConflictKind::CaseCollision);
        assert_eq!(
            collision_kind("a.txt", "a.txt "),
            ConflictKind::WhitespaceCollision
        );
    }
}
