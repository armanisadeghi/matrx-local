//! `matrx-syncd` — the AI Matrx folder-sync daemon.
//!
//! **FS-C5's slice.** It is the device's only session holder (SPEC-CUSTODY, D17): it runs the PKCE
//! transaction, owns the OS keychain item, rotates headlessly, and serves the five auth routes
//! plus `GET /v1/health`, `GET /v1/version`, `GET /v1/events` and `POST /v1/shutdown` over the
//! per-user socket/pipe and one loopback listener in the daemon band.
//!
//! It does **not** sync files yet — the mapping routes, the watchers, the planner execution and the
//! lifecycle registration are FS-L2a's, and a route that does not exist answers 404 with the error
//! envelope rather than a stub (law 4).
//!
//! Specs: `common-docs/projects/folder-sync/specs/SPEC-CUSTODY.md` @ `c5ae35ad`,
//! `SPEC-ENGINE.md` @ `6c226529`, `CONTRACT-RULINGS.md`.

#![warn(missing_docs)]
#![warn(clippy::all)]

mod api;
mod bootstrap;
mod discovery;
mod paths;
#[cfg(windows)]
mod windows_user;

use discovery::{ClobberCheck, Discovery, ScopedTokens};
use matrx_sync::custody::{Custodian, CustodyConfig, World, DESKTOP_CLIENT_ID};
use matrx_sync::journal::Journal;
use paths::Paths;
use std::sync::{Arc, Mutex};

const NAME: &str = env!("CARGO_PKG_NAME");
const VERSION: &str = env!("CARGO_PKG_VERSION");

/// SPEC-ENGINE §1.3's teardown budget knob (`sync.shutdown_budget_s`), default 20 s, range 5–120.
const DEFAULT_SHUTDOWN_BUDGET_S: u64 = 20;

fn help() -> String {
    format!(
        "{NAME} {VERSION}\n\
         AI Matrx folder-sync daemon — the device's only session holder.\n\n\
         USAGE:\n    \
             {NAME} [OPTIONS]\n\n\
         OPTIONS:\n    \
             --world <live|dev>        Which world to run in. Defaults to dev for a source build\n    \
                                       and live for a release build (Hard Rule 9); a release build\n    \
                                       refuses the live position unless MATRX_LIVE_SYNCD=1 when a\n    \
                                       live daemon is already publishing.\n    \
             --supabase-url <url>      Override the packaged Supabase URL.\n    \
             --supabase-key <key>      Override the packaged publishable key.\n    \
             --supervised              Started by launchd/systemd/Task Scheduler rather than by hand.\n    \
             --print-endpoint          Print this world's discovery path and exit.\n    \
             --print-read-token        Print this world's READ-scope token and exit. The control\n    \
                                       token is never printed: it authorises sign-out and shutdown,\n    \
                                       and only the Tauri host and the engine hold it (S17).\n    \
             -V, --version             Print the version and exit.\n    \
             -h, --help                Print this help and exit.\n\n\
         ENVIRONMENT:\n    \
             MATRX_HOME_DIR            This world's home; defaults to ~/.matrx or ~/.matrx-dev.\n    \
             MATRX_LIVE_SYNCD=1        Let a source build take the live position (Hard Rule 9).\n"
    )
}

struct Options {
    world: World,
    supabase_url: String,
    supabase_key: String,
}

fn parse_args() -> Result<Options, std::process::ExitCode> {
    // Hard Rule 9, mirrored from `run.py` and `desktop/src-tauri/src/lib.rs`: a source build lives
    // in the DEV world and never reads the installed app's discovery file or adopts its daemon.
    let mut world = if cfg!(debug_assertions) {
        World::Dev
    } else {
        World::Live
    };
    let mut supabase_url = bootstrap::SUPABASE_URL.to_string();
    let mut supabase_key = bootstrap::SUPABASE_PUBLISHABLE_KEY.to_string();

    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "-V" | "--version" => {
                println!("{NAME} {VERSION} (matrx-sync {})", matrx_sync::VERSION);
                return Err(std::process::ExitCode::SUCCESS);
            }
            "-h" | "--help" => {
                print!("{}", help());
                return Err(std::process::ExitCode::SUCCESS);
            }
            "--supervised" => {}
            "--print-endpoint" => {
                let paths = Paths::resolve(world).map_err(|e| {
                    eprintln!("{NAME}: {e}");
                    std::process::ExitCode::from(1)
                })?;
                println!("{}", paths.discovery.display());
                return Err(std::process::ExitCode::SUCCESS);
            }
            "--print-read-token" => {
                let paths = Paths::resolve(world).map_err(|e| {
                    eprintln!("{NAME}: {e}");
                    std::process::ExitCode::from(1)
                })?;
                match ScopedTokens::read(&paths.tokens) {
                    Ok(t) => {
                        println!("{}", t.read);
                        return Err(std::process::ExitCode::SUCCESS);
                    }
                    Err(e) => {
                        eprintln!(
                            "{NAME}: could not read {}: {e}. Is a {} daemon running?",
                            paths.tokens.display(),
                            world.as_str()
                        );
                        return Err(std::process::ExitCode::from(1));
                    }
                }
            }
            "--world" => {
                i += 1;
                let value = args.get(i).map(String::as_str).unwrap_or_default();
                match World::parse(value) {
                    Some(w) => world = w,
                    None => {
                        eprintln!("{NAME}: --world takes `live` or `dev`, not {value:?}");
                        return Err(std::process::ExitCode::from(2));
                    }
                }
            }
            "--supabase-url" => {
                i += 1;
                supabase_url = args.get(i).cloned().unwrap_or_default();
            }
            "--supabase-key" => {
                i += 1;
                supabase_key = args.get(i).cloned().unwrap_or_default();
            }
            other => {
                eprintln!("{NAME}: unrecognised argument {other:?}");
                eprint!("{}", help());
                return Err(std::process::ExitCode::from(2));
            }
        }
        i += 1;
    }

    Ok(Options {
        world,
        supabase_url,
        supabase_key,
    })
}

fn main() -> std::process::ExitCode {
    let options = match parse_args() {
        Ok(o) => o,
        Err(code) => return code,
    };

    let runtime = match tokio::runtime::Runtime::new() {
        Ok(r) => r,
        Err(e) => {
            eprintln!("{NAME}: could not start the async runtime: {e}");
            return std::process::ExitCode::from(1);
        }
    };
    runtime.block_on(run(options))
}

async fn run(options: Options) -> std::process::ExitCode {
    let world = options.world;
    let paths = match Paths::resolve(world) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("[syncd] {e}");
            return std::process::ExitCode::from(1);
        }
    };
    if let Err(e) = paths.create_dirs() {
        eprintln!(
            "[syncd] could not create {}: {e}. AI Matrx Sync cannot run without a home directory \
             it can write to.",
            paths.home.display()
        );
        return std::process::ExitCode::from(1);
    }

    // Hard Rule 9: one daemon per user per world, and a source build refuses the live position
    // unless it is asked for explicitly and nothing live is publishing.
    if world == World::Live && cfg!(debug_assertions) && std::env::var("MATRX_LIVE_SYNCD").as_deref() != Ok("1") {
        eprintln!(
            "[syncd] a source build refuses the live position. Set MATRX_LIVE_SYNCD=1 with the \
             installed app quit, or run with --world dev."
        );
        return std::process::ExitCode::from(1);
    }

    // SPEC-ENGINE §1.9's clobber rule, mirroring the engine's: never a silent second daemon, and
    // never a kill. It logs loudly and exits 0.
    if let ClobberCheck::Occupied { pid, endpoint } = discovery::clobber_check(&paths).await {
        eprintln!(
            "[syncd] a {} daemon is already running for this user (pid {pid}, {endpoint}); this \
             process is exiting without touching it.",
            world.as_str()
        );
        return std::process::ExitCode::SUCCESS;
    }

    let journal = match Journal::open(&paths.journal) {
        Ok(j) => Arc::new(Mutex::new(j)),
        Err(e) => {
            // §4.2: a journal written by a newer build is `daemon_older_than_journal` and exit 0,
            // never a migration backwards.
            eprintln!("[syncd] {e}");
            return std::process::ExitCode::SUCCESS;
        }
    };

    let custodian = match Custodian::production(
        CustodyConfig {
            world,
            supabase_url: options.supabase_url,
            publishable_key: options.supabase_key,
            client_id: DESKTOP_CLIENT_ID.to_string(),
        },
        Arc::clone(&journal),
    ) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[syncd] {e} — {}", e.remedy());
            return std::process::ExitCode::from(1);
        }
    };

    // S17/C5: two fresh 256-bit tokens per start, one file, two lines, mode 0600.
    let tokens = match ScopedTokens::mint().and_then(|t| t.write(&paths.tokens).map(|()| t)) {
        Ok(t) => t,
        Err(e) => {
            eprintln!(
                "[syncd] could not write {}: {e}. Without it no client can authenticate.",
                paths.tokens.display()
            );
            return std::process::ExitCode::from(1);
        }
    };

    // C6's per-user control endpoint.
    #[cfg(unix)]
    let endpoint = api::LocalEndpoint::Socket(paths.socket.clone());
    #[cfg(windows)]
    let endpoint = match paths.pipe_name() {
        Ok(n) => api::LocalEndpoint::Pipe(n),
        Err(e) => {
            eprintln!("[syncd] could not name this user's control pipe: {e}");
            return std::process::ExitCode::from(1);
        }
    };

    // Bind before building the state: the `Host` allow-list and `syncd.json` both need the port
    // the C7 allocator actually took, and no request may arrive before the state carries it.
    let bound = match api::bind(world, endpoint).await {
        Ok(b) => b,
        Err(e) => {
            eprintln!("[syncd] could not bind the control endpoint: {e}");
            return std::process::ExitCode::from(1);
        }
    };
    let control_endpoint = bound.endpoint.clone();
    let tcp_port = bound.tcp_port;

    let executable_path = std::env::current_exe()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|_| NAME.to_string());

    let state = Arc::new(api::ApiState {
        custodian: custodian.clone(),
        tokens,
        world,
        daemon_version: VERSION.to_string(),
        executable_path,
        tcp_port,
        shutdown: tokio::sync::Notify::new(),
        shutdown_budget_s: DEFAULT_SHUTDOWN_BUDGET_S,
    });
    let handles = api::serve(Arc::clone(&state), bound);

    let discovery_row = Discovery {
        version: 1,
        world: world.as_str().to_string(),
        pid: std::process::id(),
        socket_path: control_endpoint.clone(),
        tcp_port,
        daemon_version: VERSION.to_string(),
        started_at: matrx_sync::custody::rfc3339(chrono::Utc::now()),
    };
    if let Err(e) = discovery_row.write(&paths.discovery) {
        eprintln!(
            "[syncd] could not publish {}: {e}. Nothing would be able to find this daemon.",
            paths.discovery.display()
        );
        return std::process::ExitCode::from(1);
    }

    eprintln!(
        "[syncd] {NAME} {VERSION} serving the {} world on {} (loopback {})",
        world.as_str(),
        control_endpoint,
        tcp_port
            .map(|p| p.to_string())
            .unwrap_or_else(|| "unavailable".into()),
    );

    // S9: adopt whatever the keychain holds and rotate immediately — but **as a task, not
    // inline**. Adoption talks to the OS keychain and to the network, and a daemon that cannot be
    // stopped until it finishes adopting is a daemon nobody can stop on the one day adoption is
    // the thing that is stuck. The API is already serving; the journal already holds the last
    // known state; `session.changed` announces the outcome.
    let adopting = {
        let custodian = custodian.clone();
        tokio::spawn(async move {
            let snapshot = custodian.resume().await;
            eprintln!(
                "[syncd] session state on start: {} ({})",
                snapshot.state.as_str(),
                snapshot.state_reason.as_deref().unwrap_or("")
            );
        })
    };
    let refresher = custodian.spawn_refresh_loop();

    // Only `POST /v1/shutdown` stops it (SPEC-ENGINE rule 11). A terminal Ctrl-C is honoured too,
    // because a developer running it by hand is not a supervisor and has no other verb.
    let interrupt = async {
        let _ = tokio::signal::ctrl_c().await;
    };
    tokio::select! {
        _ = state.shutdown.notified() => eprintln!("[syncd] shutdown requested"),
        _ = interrupt => eprintln!("[syncd] interrupted"),
    }

    // §1.3's teardown, in order. Nothing is killed and nothing is signalled.
    handles.abort();
    refresher.abort();
    adopting.abort();
    {
        // Commit and checkpoint the journal WAL before reporting done — rule 12, and the guard
        // against the 60–76 MB WAL a skipped teardown leaves behind.
        let j = journal.lock().expect("journal mutex");
        if let Err(e) = j
            .connection()
            .execute_batch("PRAGMA wal_checkpoint(TRUNCATE);")
        {
            eprintln!("[syncd] the journal WAL could not be checkpointed: {e}");
        }
    }
    #[cfg(unix)]
    let _ = std::fs::remove_file(&paths.socket);
    if let Err(e) = Discovery::remove_if_ours(&paths.discovery) {
        eprintln!("[syncd] could not remove the discovery file: {e}");
    }
    eprintln!("[syncd] stopped cleanly");
    std::process::ExitCode::SUCCESS
}
