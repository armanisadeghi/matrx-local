//! Whole-file SHA-256, with bounded concurrency.
//!
//! SCOPE §3.1 item 3: *"content = whole-file SHA-256 (matches the server `checksum`/ETag)"*, and
//! SPEC-ENGINE §5 is explicit that it is **SHA-256, not blake3**, because the server's checksum is
//! SHA-256 — a faster hash that does not match the server is not faster, it is a second hash.
//!
//! Bounded because hashing is the one part of a scan that can saturate a laptop:
//! `sync.hash_concurrency` defaults to `max(2, cores/2)`, which leaves the machine usable while a
//! first sync walks a large tree. It is a knob, so it is an argument here, never a constant.

use sha2::{Digest, Sha256};
use std::io::Read;
use std::path::{Path, PathBuf};

/// One file to hash: its key in the tree, and where its bytes actually are.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HashRequest {
    /// The tree key (NFC).
    pub path_nfc: String,
    /// The real path on disk — invariant I8: bytes are never found through `path_nfc`.
    pub on_disk: PathBuf,
}

/// Why a file could not be hashed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HashError {
    /// The file is gone. Between the walk and the hash is a real window, and a file that vanishes
    /// in it is not an error — it is a change the next scan will see.
    Vanished,
    /// Another process holds it. On Windows this is ordinary (Office, antivirus); the retry
    /// schedule is `sync.locked_file_retry_base_s` and friends.
    Locked(String),
    /// Anything else the OS said.
    Io(String),
}

impl HashError {
    /// A short stable token for activity rows.
    pub const fn kind(&self) -> &'static str {
        match self {
            HashError::Vanished => "vanished",
            HashError::Locked(_) => "locked",
            HashError::Io(_) => "io_error",
        }
    }
}

/// What hashing one file produced.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HashOutcome {
    /// The tree key.
    pub path_nfc: String,
    /// The hex digest, or why not.
    pub result: Result<String, HashError>,
}

/// `sync.hash_concurrency`'s default: `max(2, cores/2)`.
///
/// Half the cores, so a first sync of a large tree does not make the machine feel broken; never
/// below two, so a single-core VM still overlaps IO with hashing.
pub fn hash_concurrency_default() -> usize {
    let cores = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(2);
    (cores / 2).max(2)
}

/// Hash one file, streaming. Never reads the whole file into memory: the published maximum is 5 GB.
pub fn sha256_file(path: &Path) -> Result<String, HashError> {
    let mut file = match std::fs::File::open(path) {
        Ok(f) => f,
        Err(e) => return Err(classify(&e)),
    };
    let mut hasher = Sha256::new();
    // 1 MiB: large enough that syscall overhead disappears, small enough that a hundred concurrent
    // hashes do not add up to anything a laptop notices.
    let mut buffer = vec![0u8; 1024 * 1024];
    loop {
        match file.read(&mut buffer) {
            Ok(0) => break,
            Ok(n) => hasher.update(&buffer[..n]),
            Err(ref e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(classify(&e)),
        }
    }
    Ok(format!("{:x}", hasher.finalize()))
}

fn classify(e: &std::io::Error) -> HashError {
    match e.kind() {
        std::io::ErrorKind::NotFound => HashError::Vanished,
        std::io::ErrorKind::PermissionDenied => HashError::Locked(e.to_string()),
        _ => {
            // Windows sharing violations arrive as raw OS errors 32 and 33, which have no
            // `ErrorKind` of their own and would otherwise be filed as generic IO — and then never
            // retried on the one platform where a locked file is routine.
            #[cfg(windows)]
            if matches!(e.raw_os_error(), Some(32) | Some(33)) {
                return HashError::Locked(e.to_string());
            }
            HashError::Io(e.to_string())
        }
    }
}

/// Hash many files with at most `concurrency` running at once.
///
/// Results come back in the SAME ORDER as the requests, whatever order the threads finished in —
/// a scan whose output depends on thread scheduling is not reproducible, and an irreproducible
/// scan cannot be diffed against the last one.
pub fn hash_files(requests: &[HashRequest], concurrency: usize) -> Vec<HashOutcome> {
    if requests.is_empty() {
        return Vec::new();
    }
    let workers = concurrency.max(1).min(requests.len());
    let next = std::sync::atomic::AtomicUsize::new(0);
    let slots: Vec<std::sync::Mutex<Option<HashOutcome>>> =
        (0..requests.len()).map(|_| std::sync::Mutex::new(None)).collect();

    std::thread::scope(|scope| {
        for _ in 0..workers {
            scope.spawn(|| loop {
                let i = next.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                let Some(request) = requests.get(i) else {
                    break;
                };
                let outcome = HashOutcome {
                    path_nfc: request.path_nfc.clone(),
                    result: sha256_file(&request.on_disk),
                };
                *slots[i].lock().expect("a hashing thread panicked") = Some(outcome);
            });
        }
    });

    slots
        .into_iter()
        .map(|slot| {
            slot.into_inner()
                .expect("a hashing thread panicked")
                .expect("every slot is filled before the scope ends")
        })
        .collect()
}
