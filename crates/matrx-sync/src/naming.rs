//! Conflict-copy naming (D7) and client-side name safety.
//!
//! Both are **pure**: a name is computed from values the caller supplies — the device name and the
//! date arrive as strings, never from a clock or a hostname lookup — so the planner stays a
//! function of its arguments.

use crate::knobs::Knobs;
use crate::model::ConflictKind;

/// Render a conflict-copy name from `sync.conflict_copy_template` (D7), **uniquified**.
///
/// The template alone renders the same name for two conflicts on one path, on one day, from one
/// device — and the second copy then overwrites the first, destroying the very thing a conflict
/// copy exists to protect. Independent verification reproduced that data loss (F1). So the
/// rendered name is tried first, and if it is `taken` a ` (2)`, ` (3)` … suffix is inserted before
/// the extension until a free one is found — which is what Dropbox and OneDrive do.
///
/// `taken` is a pure predicate the caller supplies over every name it knows about: all three
/// trees, plus any copy the same plan has already claimed.
pub fn unique_conflict_copy_path(
    path_nfc: &str,
    device: &str,
    date: &str,
    knobs: &Knobs,
    taken: impl Fn(&str) -> bool,
) -> String {
    let base = conflict_copy_path(path_nfc, device, date, knobs);
    if !taken(&base) {
        return base;
    }
    let (parent, file) = split_parent(&base);
    let (stem, ext) = split_stem_ext(file);
    // Bounded so a pathological tree cannot spin: past the bound the name is made unique by the
    // counter itself, which cannot collide with any earlier candidate.
    for n in 2..10_000u32 {
        let candidate = match parent {
            "" => format!("{stem} ({n}){ext}"),
            p => format!("{p}/{stem} ({n}){ext}"),
        };
        if !taken(&candidate) {
            return candidate;
        }
    }
    match parent {
        "" => format!("{stem} (10000){ext}"),
        p => format!("{p}/{stem} (10000){ext}"),
    }
}

/// Render a conflict-copy name from `sync.conflict_copy_template` (D7), without uniquifying.
///
/// Prefer [`unique_conflict_copy_path`]: this one can collide with an existing copy.
///
/// The default template is the Dropbox convention,
/// `{stem} (conflicted copy from {device} {YYYY-MM-DD}){ext}`, which is what users of every
/// champion already recognise. Supported placeholders: `{stem}`, `{ext}`, `{device}`,
/// `{YYYY-MM-DD}`.
///
/// `path_nfc` is a mapping-relative path; the returned name keeps the same parent directory, so a
/// conflict copy never escapes the folder its original lives in.
pub fn conflict_copy_path(path_nfc: &str, device: &str, date: &str, knobs: &Knobs) -> String {
    let (parent, file) = split_parent(path_nfc);
    let (stem, ext) = split_stem_ext(file);
    let rendered = knobs
        .conflict_copy_template
        .replace("{stem}", stem)
        .replace("{ext}", ext)
        .replace("{device}", device)
        .replace("{YYYY-MM-DD}", date);
    match parent {
        "" => rendered,
        p => format!("{p}/{rendered}"),
    }
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
    if nfc(a) == nfc(b) {
        // Same characters, different Unicode spelling: NFC against NFD (I8).
        ConflictKind::UnicodeCollision
    } else if nfc(a).to_lowercase() == nfc(b).to_lowercase() {
        // Case-folding alone makes them identical: they differ only in case.
        ConflictKind::CaseCollision
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

    #[test]
    fn a_conflict_copy_never_overwrites_an_earlier_one() {
        // F1, from independent verification: without a uniquifier, the second conflict on one
        // path on one day from one device destroyed the first copy.
        let k = Knobs::default();
        let first = unique_conflict_copy_path("x.txt", "device-a", "2026-09-13", &k, |_| false);
        assert_eq!(first, "x (conflicted copy from device-a 2026-09-13).txt");
        let second = unique_conflict_copy_path("x.txt", "device-a", "2026-09-13", &k, |c| {
            c == first
        });
        assert_eq!(second, "x (conflicted copy from device-a 2026-09-13) (2).txt");
        let third = unique_conflict_copy_path("x.txt", "device-a", "2026-09-13", &k, |c| {
            c == first || c == second
        });
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
