use serde::Serialize;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use tauri::{Emitter, Manager};
#[cfg(unix)]
use libc;

/// Global flag: set to true once graceful_shutdown_sync has run.
/// Prevents the cleanup from running twice (tray quit → ExitRequested both fire).
static SHUTDOWN_DONE: AtomicBool = AtomicBool::new(false);
use tauri::{
    menu::{MenuBuilder, MenuItemBuilder},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
};
use tauri_plugin_autostart::MacosLauncher;
use tauri_plugin_deep_link::DeepLinkExt;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::UpdaterExt;

mod transcription;
use transcription::commands::*;
use transcription::wake_word::WakeWordState; // needed for WakeWordState::new() in .manage()

mod llm;
use llm::commands::*;

mod downloads;
use downloads::commands::*;

mod floating_overlay;
use floating_overlay::*;

// ── proxy_fetch types ────────────────────────────────────────────────────────
#[derive(Serialize)]
struct FetchResponse {
    status: u16,
    content_type: String,
    body_b64: String, // base64-encoded body
    final_url: String,
}

/// Kill orphaned engine sidecar processes from a previous session.
///
/// **Ownership note (see docs/official/lifecycle-ownership.md):**
/// This function ONLY targets the engine sidecar binaries. It does NOT
/// touch cloudflared, llama-server, or any other service the engine owns.
/// Each level of the ownership tree only kills its own children:
///
///   Rust (this file) → engine sidecar
///   Engine sidecar    → cloudflared, scraper, scheduler, etc.
///   LlmServer (Rust)  → llama-server
///
/// When the engine starts, its own preflight (`app/preflight.py
/// clean_orphans`) sweeps any cloudflared/llama-server processes left
/// behind by a previous crashed engine — that's the engine reclaiming its
/// own children, not Rust reaching across.
///
/// Targets here are only the engine sidecar binaries:
///   macOS / Linux: pkill -f matches the full command line; one regex
///     covers all known engine names (current and legacy).
///       • "Matrx Engine"          (macOS Helper-app binary, with space)
///       • "matrx-engine"          (Linux flat binary; also legacy macOS
///                                  builds where it lived in Contents/MacOS/)
///       • "aimatrx-engine"        (legacy installs that pre-date the
///                                  Helper-app rename — sweep them so
///                                  upgrade paths don't leave orphans)
///   Windows: taskkill /IM matches by exact image (process) name, so we
///     have to issue one call per image variant.
///
/// Discovery file safety: only deletes local.json if the PID it contains
/// is no longer alive. This prevents a race where Rust cleanup runs AFTER
/// the new engine has already written its own PID — we would otherwise
/// delete the new engine's discovery record on startup.
fn kill_orphaned_sidecars() {
    #[cfg(unix)]
    {
        // Single regex covers the macOS Helper-app process name ("Matrx Engine"),
        // the Linux flat binary ("matrx-engine"), and the legacy "aimatrx-engine"
        // name from older installs. pkill -f matches against the full command
        // line, so a substring of the executable path is enough.
        const ENGINE_PATTERN: &str = "Matrx Engine|matrx-engine|aimatrx-engine";

        let _ = std::process::Command::new("pkill")
            .args(["-TERM", "-f", ENGINE_PATTERN])
            .output();
        std::thread::sleep(std::time::Duration::from_millis(500));
        let _ = std::process::Command::new("pkill")
            .args(["-KILL", "-f", ENGINE_PATTERN])
            .output();
        // NOTE: cloudflared is intentionally NOT killed here. It is the
        // engine's child, and the engine's preflight reclaims it on startup.
        // Killing it from Rust during a normal shutdown races against the
        // engine's tunnel.stop() and produces "ended unexpectedly" errors.
    }
    #[cfg(windows)]
    {
        // /F = force, /T = kill entire process tree (children: uvicorn workers,
        // Playwright Chromium, etc.) — without /T those children survive and
        // continue to hold port 22140 and file system locks.
        //
        // We issue one taskkill per known image name. The current build target
        // is "matrx-engine-x86_64-pc-windows-msvc.exe"; "aimatrx-engine-*.exe"
        // is the legacy name that may still be running on machines that just
        // upgraded to a build that uses the new name — sweep both so the
        // upgrade is clean. /T cascades to the engine's process tree
        // (cloudflared, etc.) without us having to name them explicitly —
        // that is the engine signaling its children via process-tree
        // termination, not Rust reaching across.
        for image in [
            "matrx-engine-x86_64-pc-windows-msvc.exe",
            "matrx-engine.exe",
            "aimatrx-engine-x86_64-pc-windows-msvc.exe",
            "aimatrx-engine.exe",
        ] {
            let _ = std::process::Command::new("taskkill")
                .args(["/F", "/T", "/IM", image])
                .output();
        }
        // NOTE: cloudflared.exe is intentionally NOT killed here. /T above
        // already cascades to it as a child of the engine process tree. A
        // standalone taskkill /IM cloudflared.exe would also kill cloudflared
        // instances spawned by other applications on the same machine.
    }

    // Remove the discovery file only if its recorded PID is no longer alive.
    let discovery_path = {
        #[cfg(unix)]
        { std::env::var("HOME").ok().map(|h| std::path::PathBuf::from(h).join(".matrx").join("local.json")) }
        #[cfg(windows)]
        { std::env::var("USERPROFILE").ok().map(|h| std::path::PathBuf::from(h).join(".matrx").join("local.json")) }
    };

    if let Some(path) = discovery_path {
        if path.exists() {
            // Parse the "pid" value out of local.json without a JSON dependency.
            let recorded_pid: Option<u32> = std::fs::read_to_string(&path).ok().and_then(|s| {
                // Scan for: "pid": 12345
                s.split('"')
                    .skip_while(|tok| *tok != "pid")
                    .nth(2)
                    .and_then(|tok| {
                        let digits: String = tok.chars().skip_while(|c| !c.is_ascii_digit()).take_while(|c| c.is_ascii_digit()).collect();
                        digits.parse().ok()
                    })
            });

            let pid_is_alive = recorded_pid.map(|pid| {
                #[cfg(unix)]
                { let rc = unsafe { libc::kill(pid as libc::pid_t, 0) }; rc == 0 }
                #[cfg(not(unix))]
                { let _ = pid; false } // Windows: after taskkill above, assume dead
            }).unwrap_or(false);

            if !pid_is_alive {
                let _ = std::fs::remove_file(&path);
            }
        }
    }
}

/// Kills any orphaned llama-server processes left over from a previous crashed session.
/// Called ONLY once at app startup (.setup) to avoid race conditions where the UI
/// starts the LLM server concurrently with the Python sidecar.
fn kill_orphaned_llama_server() {
    #[cfg(unix)]
    {
        let _ = std::process::Command::new("pkill")
            .args(["-9", "-f", "llama-server"])
            .output();
    }
    #[cfg(windows)]
    {
        let _ = std::process::Command::new("taskkill")
            .args(["/F", "/T", "/IM", "llama-server.exe"])
            .output();
    }
}

/// Holds the sidecar child process handle for lifecycle management.
struct SidecarState {
    child: Mutex<Option<tauri_plugin_shell::process::CommandChild>>,
}

/// Ring buffer of recent sidecar stdout/stderr lines for frontend diagnostics.
#[derive(Clone)]
struct SidecarLogs {
    lines: Arc<Mutex<Vec<String>>>,
}

/// Controls whether window close hides to tray or quits the app.
struct CloseToTray(AtomicBool);

/// Holds a pending OAuth deep-link URL that arrived before the frontend
/// mounted its listener. The frontend polls this via get_pending_oauth_url
/// and clears it after consuming.
struct PendingOAuthUrl(Mutex<Option<String>>);

#[derive(Serialize)]
struct SidecarStatus {
    running: bool,
    port: u16,
}

/// Resolve the path to the Helper-app engine binary on macOS production builds.
///
/// On macOS we ship the engine as a Helper .app bundle inside the parent app
/// (Contents/Frameworks/Matrx Engine.app) so Activity Monitor can display it
/// as "Matrx Engine" with its own icon. Tauri's `app.shell().sidecar()` API
/// resolves names relative to the resource directory, which doesn't match
/// the /Frameworks layout — so we compute the absolute path from the running
/// executable's location and spawn it as a regular shell command.
///
/// Returns Some(path) only if the Helper app is present at the expected
/// location. In dev mode (`pnpm tauri:dev`) the bundle doesn't exist, so the
/// caller falls back to the engine launched separately via `uv run python run.py`.
#[cfg(target_os = "macos")]
fn macos_helper_engine_path() -> Option<std::path::PathBuf> {
    // current_exe() points at AI Matrx.app/Contents/MacOS/aimatrx-desktop.
    // Walk up two levels to land on Contents/, then descend into Frameworks/.
    let exe = std::env::current_exe().ok()?;
    let contents = exe.parent()?.parent()?;
    let helper = contents
        .join("Frameworks")
        .join("Matrx Engine.app")
        .join("Contents")
        .join("MacOS")
        .join("Matrx Engine");
    if helper.exists() {
        Some(helper)
    } else {
        None
    }
}

/// Start the Python/FastAPI engine sidecar.
///
/// In production, this spawns the bundled PyInstaller binary.
/// The sidecar listens on the configured port (default 22140).
/// We set TAURI_SIDECAR=1 so that run.py skips the pystray tray icon —
/// Tauri already owns the single system-tray icon for the whole app.
///
/// Spawn path by platform:
///   macOS production: absolute path to the Helper app's inner binary at
///     Contents/Frameworks/Matrx Engine.app/Contents/MacOS/Matrx Engine
///     (computed from current_exe()). The shell-plugin sidecar API can't
///     reach into /Frameworks, so we use shell().command() directly.
///   macOS dev:        the Helper app doesn't exist on disk, so we fall back
///     to the standard sidecar API. In practice nobody calls start_sidecar
///     in dev mode (the engine runs separately via `uv run python run.py`),
///     but the fallback gives a clear error if they do.
///   Windows / Linux:  Tauri's externalBin places the renamed `matrx-engine`
///     binary at the expected location; sidecar() resolves it correctly.
///
/// Before spawning, kills any orphaned engine processes left over from a
/// previous crash or unclean shutdown.  This prevents the new sidecar from
/// failing with "address already in use" on port 22140.
///
/// Self-healing: if we hold a child handle but the process has already
/// exited (e.g. killed by macOS watchdog or SIGKILL), we clear the stale
/// handle so we can respawn cleanly rather than returning early.
#[tauri::command]
async fn start_sidecar(
    app: tauri::AppHandle,
    state: tauri::State<'_, SidecarState>,
) -> Result<(), String> {
    // Check if already running — but also detect and clear stale handles
    // where the process exited without going through stop_sidecar().
    {
        let mut guard = state.child.lock().unwrap();
        if guard.is_some() {
            // On Unix we can probe liveness with kill(pid, 0): if it returns
            // ESRCH the process is gone and the handle is stale.
            #[cfg(unix)]
            {
                let pid = guard.as_ref().unwrap().pid();
                let alive = unsafe { libc::kill(pid as libc::pid_t, 0) } == 0;
                if !alive {
                    eprintln!(
                        "[sidecar] Stale child handle (pid={}) detected — process is gone. Clearing.",
                        pid
                    );
                    *guard = None;
                    // Fall through to respawn below.
                } else {
                    return Ok(()); // genuinely still running
                }
            }
            // On Windows we have no cheap liveness check via the plugin API;
            // treat a held handle as running. The watchdog / restart path
            // always calls stop_sidecar() first on Windows, so this is fine.
            #[cfg(not(unix))]
            return Ok(());
        }
    }

    // Spawn-in-progress guard: the handle check above releases its lock
    // before we spawn, so two concurrent invocations (recovery modal + init
    // effect) could both pass it — the second's kill_orphaned_sidecars()
    // would then kill the first's freshly spawned engine mid-boot and
    // overwrite (leak) its child handle.
    static SIDECAR_SPAWNING: std::sync::atomic::AtomicBool =
        std::sync::atomic::AtomicBool::new(false);
    struct SpawnGuard;
    impl Drop for SpawnGuard {
        fn drop(&mut self) {
            SIDECAR_SPAWNING.store(false, std::sync::atomic::Ordering::SeqCst);
        }
    }
    if SIDECAR_SPAWNING.swap(true, std::sync::atomic::Ordering::SeqCst) {
        return Ok(()); // another start_sidecar is mid-spawn
    }
    let _spawn_guard = SpawnGuard;

    // Kill any orphaned sidecar processes from a previous session before
    // spawning a new one.  Without this, port 22140 may still be held by
    // a zombie from a crash/force-quit, and the new sidecar will fail.
    kill_orphaned_sidecars();

    // Build the engine command. On macOS production the engine lives inside
    // a Helper .app sub-bundle; everywhere else Tauri's externalBin places
    // it where the sidecar() API expects it.
    let sidecar_command = {
        #[cfg(target_os = "macos")]
        {
            if let Some(helper_path) = macos_helper_engine_path() {
                app.shell().command(helper_path)
            } else {
                // Dev mode (no .app bundle) — fall through to sidecar() so the
                // user sees a clear "binary not found" if they actually invoke
                // start_sidecar from `pnpm tauri:dev`.
                app.shell()
                    .sidecar("matrx-engine")
                    .map_err(|e| format!("Failed to create sidecar command: {}", e))?
            }
        }
        #[cfg(not(target_os = "macos"))]
        {
            app.shell()
                .sidecar("matrx-engine")
                .map_err(|e| format!("Failed to create sidecar command: {}", e))?
        }
    };

    let sidecar = sidecar_command
        // Signal to run.py that it is running inside Tauri — suppress pystray tray icon.
        .env("TAURI_SIDECAR", "1")
        // Pass the Tauri app's own PID so the Python watchdog can watch the
        // correct process on Windows.  On Windows, Tauri spawns the sidecar
        // via an intermediate shim/pipe helper whose PPID is NOT the Tauri
        // process itself — so os.getppid() in Python points to a short-lived
        // launcher that exits immediately, causing a false "parent gone" kill.
        .env("TAURI_APP_PID", std::process::id().to_string());

    let (mut rx, child) = sidecar
        .spawn()
        .map_err(|e| format!("Failed to spawn sidecar: {}", e))?;

    *state.child.lock().unwrap() = Some(child);

    // Forward sidecar output to Tauri logs AND to the frontend via events.
    // The SidecarLogs ring buffer stores the last 200 lines so the frontend
    // can retrieve them on demand (e.g. when the recovery modal opens).
    let app_handle = app.clone();
    let log_lines = app.state::<SidecarLogs>().lines.clone();
    tauri::async_runtime::spawn(async move {
        use tauri_plugin_shell::process::CommandEvent;
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    let text = String::from_utf8_lossy(&line).to_string();
                    println!("[engine] {}", text);
                    {
                        let mut lines = log_lines.lock().unwrap();
                        lines.push(format!("[stdout] {}", text));
                        let excess = lines.len().saturating_sub(200);
                        if excess > 0 {
                            lines.drain(..excess);
                        }
                    }
                    let _ = app_handle.emit("sidecar-log", format!("[stdout] {}", text));
                }
                CommandEvent::Stderr(line) => {
                    let text = String::from_utf8_lossy(&line).to_string();
                    eprintln!("[engine] {}", text);
                    {
                        let mut lines = log_lines.lock().unwrap();
                        lines.push(format!("[stderr] {}", text));
                        let excess = lines.len().saturating_sub(200);
                        if excess > 0 {
                            lines.drain(..excess);
                        }
                    }
                    let _ = app_handle.emit("sidecar-log", format!("[stderr] {}", text));
                }
                CommandEvent::Terminated(status) => {
                    let msg = format!("[terminated] Process exited: {:?}", status);
                    eprintln!("[engine] {}", msg);
                    {
                        let mut lines = log_lines.lock().unwrap();
                        lines.push(msg.clone());
                    }
                    let _ = app_handle.emit("sidecar-log", msg);
                    break;
                }
                _ => {}
            }
        }
    });

    Ok(())
}

/// Stop the Python/FastAPI engine sidecar gracefully.
///
/// Uses sigterm_then_kill() which sends SIGTERM first (giving Python's signal
/// handler time to run lifespan teardown) and falls back to SIGKILL after 8s.
/// Always clears the child handle regardless of whether a child was held —
/// this ensures a subsequent start_sidecar() can always respawn cleanly.
#[tauri::command]
async fn stop_sidecar(state: tauri::State<'_, SidecarState>) -> Result<(), String> {
    let child = state.child.lock().unwrap().take();
    // sigterm_then_kill std::thread::sleep-waits up to 20s — run it on a
    // blocking thread so it doesn't pin a tokio worker and starve other
    // commands (status polls, health checks) for the whole shutdown wait.
    let _ = tauri::async_runtime::spawn_blocking(move || {
        if let Some(c) = child {
            sigterm_then_kill(c);
        }
        // Also nuke any orphaned processes by name — covers the case where
        // the process was SIGKILLed by the OS (e.g. macOS watchdog) before we
        // could clear the handle, leaving port 22140 still bound.
        kill_orphaned_sidecars();
    })
    .await;
    Ok(())
}

/// Send SIGTERM to the sidecar's PID and wait for it to exit cleanly,
/// then SIGKILL if it is still alive. This gives Python's signal handler
/// (_handle_exit) time to set uvicorn.should_exit, which triggers the
/// FastAPI lifespan teardown (closes proxy, scraper, Playwright browsers,
/// SQLite, etc.) before the OS reclaims the ports and file handles.
///
/// Timeout is 8 s: lifespan teardown stops the proxy (4 s timeout),
/// scraper Playwright pool (~1-2 s), and SQLite — all sequential.
#[cfg(unix)]
fn sigterm_then_kill(child: tauri_plugin_shell::process::CommandChild) {
    use std::time::{Duration, Instant};

    let pid = child.pid();

    // Send SIGTERM — gives Python's signal handler time to run lifespan teardown.
    // SAFETY: kill(2) with SIGTERM is safe; the PID comes from our own child.
    let term_sent = unsafe { libc::kill(pid as libc::pid_t, libc::SIGTERM) } == 0;

    if term_sent {
        // Wait up to 20 seconds for clean exit. The Python lifespan teardown
        // budget is ~25s total (wake-word 3s + scheduler 3s + proxy 4s + tunnel 5s
        // + scraper 5s + browsers 3s + margin). 20s lets most phases complete;
        // if it's still alive, SIGKILL ensures we don't hang the Tauri exit.
        let deadline = Instant::now() + Duration::from_secs(20);
        loop {
            std::thread::sleep(Duration::from_millis(100));
            // kill(pid, 0) = existence check; ESRCH means the process exited.
            let alive = unsafe { libc::kill(pid as libc::pid_t, 0) } == 0;
            if !alive || Instant::now() >= deadline {
                break;
            }
        }
    }

    // Final guarantee: SIGKILL if it did not exit in time (or SIGTERM failed).
    let _ = child.kill();
}

/// Windows: no SIGTERM concept. Use `taskkill /F /T /PID` to forcibly
/// terminate the entire process tree rooted at the sidecar PID.
///
/// `child.kill()` only kills the immediate process (TerminateProcess API),
/// leaving child processes (Playwright chromium, uvicorn workers, etc.) alive.
/// Those orphaned processes hold port bindings and file system locks, which
/// causes "address already in use" and "file in use" errors on reinstall or
/// update — requiring a Windows restart to clear. /T kills the entire tree.
#[cfg(not(unix))]
fn sigterm_then_kill(child: tauri_plugin_shell::process::CommandChild) {
    let pid = child.pid();

    // Kill the entire process tree with /F (force) /T (tree) — this terminates
    // Playwright chromium children, uvicorn workers, and any other subprocesses.
    let _ = std::process::Command::new("taskkill")
        .args(["/F", "/T", "/PID", &pid.to_string()])
        .output();

    // Fall back to the Tauri kill() in case taskkill was unavailable or failed.
    let _ = child.kill();
}

/// Spawn a detached shell subprocess that will reliably terminate every
/// managed sidecar process on a SIGTERM-then-SIGKILL ladder — even if our
/// own process SIGABRTs partway through the GGML cleanup below.
///
/// Why this exists: `graceful_shutdown_sync` races with GGML's C atexit
/// handlers — if the wake-word thread, llama-server, or main WhisperContext
/// aren't fully torn down in time, GGML calls `abort()` → SIGABRT on the
/// Tauri main process → "AI Matrx quit unexpectedly" crash report. When
/// that happens, every step BELOW the SIGABRT in `graceful_shutdown_sync`
/// is skipped, including the steps that kill the Python sidecar (steps
/// 3-5). The user is left with (a) a crash dialog and (b) live sidecar
/// processes holding ports — exactly the bug we observed in production
/// with sidecar PIDs surviving for 10+ minutes after Quit.
///
/// The detached subprocess is created in its own session via `setsid`
/// (Unix) or `DETACHED_PROCESS` (Windows), so it survives our SIGABRT
/// and runs to completion regardless of what happens to us.
///
/// Sequence (executed inside the detached subprocess):
///   T+1s:   pkill -TERM   ← graceful; Python's lifespan teardown gets to run
///   T+26s:  pkill -KILL   ← force; fires only after the primary 20s
///           SIGTERM-wait in `sigterm_then_kill` has fully elapsed
///
/// BRACKET TRICK: `pkill -f` matches the FULL COMMAND LINE of every process —
/// including the `sh -c <script>` running this net, whose argv contains the
/// patterns. Without `[M]atrx`-style bracketing the first pkill SIGTERM'd the
/// net's own shell and the rest of the ladder never executed — the engine
/// survived Quit, which is the exact production bug this net exists to fix.
/// `[X]` the regex matches literal `X`, but the argv text `[X]` is not
/// matched by it. The same bracketing protects the net from the pkills in
/// `graceful_shutdown_sync` / `kill_orphaned_sidecars`.
///
/// Patterns match the bundled macOS Helper-app ("Matrx Engine"), the Linux
/// flat binary ("matrx-engine"), the legacy "aimatrx-engine" name, plus
/// cloudflared and llama-server. They do NOT match "AI Matrx" (the parent
/// app's process name), so this subprocess can never accidentally kill us
/// or any other parent app instance.
///
/// **Ownership note (see docs/official/lifecycle-ownership.md):**
/// In normal operation, the engine kills cloudflared during its own lifespan
/// teardown — Rust does NOT reach across to kill the engine's children.
/// This safety net is the ONE legitimate exception: it only fires AFTER
/// `graceful_shutdown_sync` has had a chance to complete (we sleep 5s before
/// SIGKILL), and only catches strays the engine couldn't clean up because
/// it was killed mid-teardown by GGML's SIGABRT. If the engine's own teardown
/// runs to completion, every pkill below is a no-op (nothing left to match).
/// It is the parachute, not the primary chute.
fn spawn_detached_shutdown_safety_net() {
    #[cfg(unix)]
    {
        let script = "\
            sleep 1; \
            pkill -TERM -f '[M]atrx Engine|[m]atrx-engine|[a]imatrx-engine' 2>/dev/null; \
            sleep 25; \
            pkill -KILL -f '[M]atrx Engine|[m]atrx-engine|[a]imatrx-engine' 2>/dev/null; \
            pkill -KILL -f '[c]loudflared tunnel' 2>/dev/null; \
            pkill -KILL -f '[l]lama-server' 2>/dev/null; \
            true";

        let mut cmd = std::process::Command::new("sh");
        cmd.args(["-c", script])
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null());

        // setsid creates a new session and process group, detaching this
        // child entirely from our process group. If we receive SIGABRT, the
        // OS does not propagate it to processes outside our group, so the
        // killer keeps running.
        //
        // SAFETY: setsid(2) has no preconditions and only fails if we're
        // already a process-group leader, which is fine to ignore.
        unsafe {
            use std::os::unix::process::CommandExt;
            cmd.pre_exec(|| {
                libc::setsid();
                Ok(())
            });
        }

        let _ = cmd.spawn();
    }

    #[cfg(windows)]
    {
        // /T = kill process tree, /F = force.
        // `ping -n 26` ~= 25s sleep. `timeout /t` exits IMMEDIATELY with
        // "Input redirection is not supported" when stdin is NUL (it is,
        // below), so the force-kills used to land at T+0 and beat the
        // graceful ladder on every Windows quit. 25s also exceeds the 20s
        // SIGTERM-wait in sigterm_then_kill, so this net can never preempt
        // a healthy graceful shutdown.
        let script = "\
            timeout /t 1 /nobreak >nul & \
            taskkill /F /T /IM \"matrx-engine.exe\" >nul 2>&1 & \
            taskkill /F /T /IM \"matrx-engine-x86_64-pc-windows-msvc.exe\" >nul 2>&1 & \
            taskkill /F /T /IM \"aimatrx-engine.exe\" >nul 2>&1 & \
            taskkill /F /T /IM \"aimatrx-engine-x86_64-pc-windows-msvc.exe\" >nul 2>&1 & \
            taskkill /F /T /IM \"cloudflared.exe\" >nul 2>&1 & \
            taskkill /F /T /IM \"llama-server.exe\" >nul 2>&1 & \
            exit /b 0";

        use std::os::windows::process::CommandExt;
        const DETACHED_PROCESS: u32 = 0x00000008;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x00000200;

        let _ = std::process::Command::new("cmd")
            .args(["/c", script])
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
            .spawn();
    }
}

/// Shared graceful-shutdown sequence used by both Quit and Restart.
///
/// SIGABRT root cause: whisper.cpp and llama.cpp embed the GGML C library
/// which stores thread-local state. When `std::process::exit()` fires (via
/// tao's `AppState::exit`) without first dropping the WhisperContext / LLM
/// server, the GGML atexit handlers call `ggml_abort()` → C `abort()` →
/// SIGABRT. macOS records this as a crash report even though the user
/// triggered an intentional quit or update restart.
///
/// Fix: always explicitly drop all GGML-bearing state before calling any
/// Tauri exit/restart function so the Rust destructors run in order and
/// GGML's own cleanup completes before the process terminates.
///
/// IMPORTANT: LlmServerState uses std::sync::Mutex (not tokio::sync::Mutex)
/// so this synchronous function can lock it reliably. tokio::sync::Mutex
/// try_lock() returns TryLockError whenever any async command holds it,
/// which caused GGML cleanup to be silently skipped → SIGABRT on every quit.
///
/// Even with the GGML fix, we ALSO spawn a detached safety-net subprocess at
/// the very top of this function (see `spawn_detached_shutdown_safety_net`).
/// That subprocess kills the sidecars on a SIGTERM-then-SIGKILL ladder
/// regardless of whether the GGML cleanup below succeeds, completes, or
/// crashes. It is the load-bearing guarantee that quit always shuts down
/// the sidecars; the per-step kills below are belt-and-suspenders.
fn graceful_shutdown_sync(
    sidecar_state: &SidecarState,
    transcription_state: &TranscriptionState,
    llm_process: &llm::commands::LlmProcessHandle,
    llm_server_state: Option<&llm::commands::LlmServerState>,
    wake_word_state: Option<&WakeWordAppState>,
    recording_state: Option<&RecordingState>,
) {
    // Idempotent guard: if already called (e.g. tray quit fires ExitRequested too),
    // skip the second run to avoid double-kill and spurious lock contention.
    if SHUTDOWN_DONE.swap(true, Ordering::SeqCst) {
        return;
    }

    // STEP 0 (LOAD-BEARING): Spawn a detached subprocess that will pkill all
    // managed sidecar processes on a 5-second SIGTERM-then-SIGKILL ladder.
    //
    // This is the safety net that guarantees quit shuts down the sidecars
    // even if the GGML cleanup steps below SIGABRT us before reaching the
    // existing per-step kills (steps 3-5). See spawn_detached_shutdown_safety_net
    // for the full rationale; in short, every step below this one is
    // best-effort, and this one always finishes because it lives in a
    // detached subprocess in its own session.
    spawn_detached_shutdown_safety_net();

    // 0. Signal the wake-word thread to stop, then join it with a timeout.
    //
    //    The wake-word thread holds its own WhisperContext (loaded from ggml-tiny.en.bin)
    //    as a local variable on the thread stack.  If the process exits while that context
    //    is still alive, GGML's C atexit handlers call ggml_abort() → SIGABRT → macOS
    //    crash report, even on an intentional quit.
    //
    //    We must JOIN the thread (not just sleep) to guarantee the GGML context is fully
    //    dropped before we proceed.  The thread's loop ticks every 50ms and checks
    //    `running` on each tick, so it will exit within one tick (≤50ms) under normal
    //    conditions.  We allow up to 2 seconds total — enough for a Whisper inference in
    //    progress to finish its current 2-second window and then exit.
    //
    //    A fixed 120ms sleep was the previous approach but is a race condition: if Whisper
    //    is mid-inference (which can take >300ms on CPU-only machines), the thread outlives
    //    the sleep and GGML state is dropped from underneath it → SIGABRT.
    if let Some(ww) = wake_word_state {
        *ww.0.running.lock().unwrap() = false;

        // Take the JoinHandle out of state so we own it for joining.
        let handle = ww.0.thread_handle.lock().unwrap().take();
        if let Some(handle) = handle {
            // Spawn a helper thread to join with a 2-second deadline.
            // std::thread::JoinHandle has no built-in timeout, so we use a channel.
            let (tx, rx) = std::sync::mpsc::channel::<()>();
            std::thread::spawn(move || {
                let _ = handle.join();
                let _ = tx.send(());
            });
            // Wait up to 2 seconds for the wake-word thread to finish.
            // If it times out, we proceed anyway — the GGML context will be
            // dropped when the OS reclaims the thread stack on process exit,
            // which is still safer than the fixed-sleep approach.
            let _ = rx.recv_timeout(std::time::Duration::from_secs(2));
        } else {
            // No handle stored (thread never started or already exited) — nothing to join.
        }
    }

    // 0b. Stop the active transcription recording thread and join it.
    //
    //     The audio capture + Whisper inference thread also holds a reference to the
    //     WhisperContext (cloned Arc from TranscriptionState).  If we drop TranscriptionState
    //     in step 2 while the thread is mid-inference, the last Arc reference disappears
    //     and GGML's destructor fires while the inference C function is still running →
    //     use-after-free → SIGABRT crash report.
    //
    //     We signal the thread to stop (set flag=false) and join it with a 5-second
    //     timeout.  The thread's loop drains all remaining accumulated audio before
    //     exiting — typically the user has already stopped recording before quitting,
    //     so there is nothing to drain.  In the worst case (quit while actively
    //     recording with a full 30-second buffer) the thread may need several seconds
    //     to flush all chunks through Whisper; the 5-second timeout is a best-effort
    //     safeguard and is sufficient for typical use.
    if let Some(rec) = recording_state {
        *rec.flag.lock().unwrap() = false;
        let handle = rec.thread_handle.lock().unwrap().take();
        if let Some(handle) = handle {
            let (tx, rx) = std::sync::mpsc::channel::<()>();
            std::thread::spawn(move || {
                let _ = handle.join();
                let _ = tx.send(());
            });
            let _ = rx.recv_timeout(std::time::Duration::from_secs(5));
        }
    }

    // 1. Kill llama-server child process FIRST — it holds GGML Metal GPU state.
    //    Killing it before exit() ensures the Metal device is freed by the child
    //    process (its own GGML cleanup) rather than our atexit handlers.
    //
    //    Three-pronged approach:
    //    a) Via LlmServerState (tokio::sync::Mutex — try_lock from sync context).
    //       This is the primary path since LlmServer.process is always populated.
    //    b) Via LlmProcessHandle (std::sync::Mutex — legacy/backup).
    //    c) Via OS kill-by-name as a final fallback.
    let mut llm_killed = false;
    if let Some(server_state) = llm_server_state {
        if let Ok(mut server) = server_state.try_lock() {
            server.stop_blocking();
            llm_killed = true;
        }
    }
    if !llm_killed {
        // Prong (b): the tokio mutex was held (e.g. a start mid health-wait).
        // The handle stores the PID published at spawn time — kill by PID.
        if let Ok(mut guard) = llm_process.lock() {
            if let Some(pid) = guard.take() {
                #[cfg(unix)]
                {
                    let _ = std::process::Command::new("kill")
                        .args(["-9", &pid.to_string()])
                        .output();
                }
                #[cfg(windows)]
                {
                    let _ = std::process::Command::new("taskkill")
                        .args(["/F", "/PID", &pid.to_string()])
                        .output();
                }
            }
        }
    }
    #[cfg(unix)]
    {
        let _ = std::process::Command::new("pkill")
            .args(["-9", "-f", "llama-server"])
            .output();
    }
    #[cfg(windows)]
    {
        let _ = std::process::Command::new("taskkill")
            .args(["/F", "/T", "/IM", "llama-server.exe"])
            .output();
    }

    // 2. Drop the main WhisperContext — this runs GGML cleanup in Rust before
    //    any C atexit handlers, preventing ggml_abort on process exit.
    if let Ok(mut guard) = transcription_state.0.lock() {
        *guard = None; // Drops TranscriptionManager → WhisperContext → GGML
    }

    // 3. Ask the Python sidecar to shut down its own children gracefully.
    //
    //    Ownership contract: cloudflared, the HTTP proxy, the scraper engine,
    //    Playwright, the file watchers, and every other long-running task in
    //    the engine are CHILDREN OF THE ENGINE — not of Rust. Rust must not
    //    pkill them directly; that races against the engine's own teardown
    //    and produces "ended unexpectedly" crash reports.
    //
    //    Preferred path: POST http://127.0.0.1:{port}/admin/shutdown. The
    //    engine self-signals SIGTERM after the HTTP response flushes, which
    //    triggers run.py's _handle_exit → uvicorn lifespan teardown →
    //    every child stops in reverse-startup order with per-child timeouts.
    //
    //    Fallback path: if the HTTP call fails (engine wedged, port not
    //    bound yet, etc.) we fall back to SIGTERM, which fires the same
    //    handler. sigterm_then_kill below escalates to SIGKILL after 20s
    //    as the last resort. After that point, the detached safety net
    //    spawned at the top of this function takes over.
    let admin_shutdown_ok = request_admin_shutdown();
    if let Some(child) = sidecar_state.child.lock().unwrap().take() {
        if admin_shutdown_ok {
            // Give the engine a head start on its lifespan teardown before
            // we send SIGTERM. The /admin/shutdown handler also sends
            // SIGTERM internally, so this is largely redundant — but keeps
            // the existing 20s wait→SIGKILL ladder as a safety belt for
            // engines that ignore the HTTP-driven self-signal.
            std::thread::sleep(std::time::Duration::from_millis(200));
        }
        sigterm_then_kill(child);
    }

    // 4. Kill any remaining orphan ENGINE sidecars + clean up discovery file.
    //    Note: this only kills engine binaries, NOT cloudflared. The engine
    //    owns cloudflared (and reclaims/kills any cloudflared orphans during
    //    its own preflight). See kill_orphaned_sidecars() for ownership rules.
    kill_orphaned_sidecars();
}

/// POST http://127.0.0.1:{port}/admin/shutdown to ask the engine to
/// gracefully tear down its children before exiting.
///
/// Returns true when the engine accepts the request (so the caller can
/// give it a small head start before sending SIGTERM as a belt-and-suspenders
/// signal). Returns false if anything fails — port discovery, connection,
/// timeout — so the caller can fall back to SIGTERM immediately.
///
/// Why a hand-rolled HTTP request instead of pulling in `reqwest`:
///   - We block on this from the synchronous shutdown handler. reqwest's
///     blocking client opens its own tokio runtime which conflicts with
///     the runtime Tauri already has alive at this point.
///   - The payload is empty and the response is small. A dependency on
///     reqwest would add ~600 KB to the binary for one POST.
///   - We need a tight timeout (1.5s) so a wedged engine doesn't delay
///     the shutdown sequence past the detached safety net's window.
fn request_admin_shutdown() -> bool {
    let Some(port) = read_engine_port_from_discovery() else {
        return false;
    };

    let addr = format!("127.0.0.1:{port}");
    let stream = match std::net::TcpStream::connect_timeout(
        &match addr.parse() {
            Ok(a) => a,
            Err(_) => return false,
        },
        std::time::Duration::from_millis(500),
    ) {
        Ok(s) => s,
        Err(_) => return false,
    };
    let _ = stream.set_read_timeout(Some(std::time::Duration::from_millis(1500)));
    let _ = stream.set_write_timeout(Some(std::time::Duration::from_millis(500)));

    let request = format!(
        "POST /admin/shutdown HTTP/1.1\r\n\
         Host: 127.0.0.1:{port}\r\n\
         User-Agent: matrx-tauri/shutdown\r\n\
         Content-Length: 0\r\n\
         Connection: close\r\n\r\n"
    );

    use std::io::{Read, Write};
    let mut s = stream;
    if s.write_all(request.as_bytes()).is_err() {
        return false;
    }

    // Read just enough to confirm a 2xx status line; we don't need the body.
    let mut buf = [0u8; 64];
    match s.read(&mut buf) {
        Ok(n) if n > 0 => {
            let head = std::str::from_utf8(&buf[..n]).unwrap_or("");
            head.starts_with("HTTP/1.1 2") || head.starts_with("HTTP/1.0 2")
        }
        _ => false,
    }
}

/// Read the engine port from ~/.matrx/local.json without pulling in serde_json.
///
/// Mirrors the parsing logic in kill_orphaned_sidecars() — find the "port":N
/// key in the JSON without a real parser. Returns None on any failure;
/// callers should treat that as "engine isn't reachable, fall back to SIGTERM".
fn read_engine_port_from_discovery() -> Option<u16> {
    let path = {
        #[cfg(unix)]
        { std::env::var("HOME").ok().map(|h| std::path::PathBuf::from(h).join(".matrx").join("local.json")) }
        #[cfg(windows)]
        { std::env::var("USERPROFILE").ok().map(|h| std::path::PathBuf::from(h).join(".matrx").join("local.json")) }
    }?;
    let text = std::fs::read_to_string(path).ok()?;
    text.split('"')
        .skip_while(|tok| *tok != "port")
        .nth(2)
        .and_then(|tok| {
            let digits: String = tok
                .chars()
                .skip_while(|c| !c.is_ascii_digit())
                .take_while(|c| c.is_ascii_digit())
                .collect();
            digits.parse().ok()
        })
}

/// Restart the app after an update with a clean shutdown sequence.
///
/// Unlike calling `relaunch()` directly from the frontend (which terminates
/// the process without going through the Cocoa/WinRT shutdown sequence and
/// causes macOS to log a crash report), this command:
///   1. Drops all GGML state (WhisperContext, llama-server) so the GGML C
///      library can clean up without calling abort().
///   2. Kills the Python sidecar.
///   3. Calls `app.request_restart()` which goes through the proper Tauri/
///      Cocoa termination handshake before relaunching.
#[tauri::command]
async fn restart_for_update(
    app: tauri::AppHandle,
    sidecar_state: tauri::State<'_, SidecarState>,
    transcription_state: tauri::State<'_, TranscriptionState>,
    llm_process: tauri::State<'_, llm::commands::LlmProcessHandle>,
    llm_server_state: tauri::State<'_, llm::commands::LlmServerState>,
    wake_word_state: tauri::State<'_, WakeWordAppState>,
    recording_state: tauri::State<'_, RecordingState>,
) -> Result<(), String> {
    graceful_shutdown_sync(
        &sidecar_state,
        &transcription_state,
        &llm_process,
        Some(&*llm_server_state),
        Some(&wake_word_state),
        Some(&recording_state),
    );

    // Give child processes a moment to exit cleanly before we restart.
    // This avoids orphaned port bindings on the new instance's startup.
    tokio::time::sleep(std::time::Duration::from_millis(500)).await;

    // `request_restart()` fires RunEvent::ExitRequested + RunEvent::Exit and
    // then relaunches through the proper OS shutdown sequence, so macOS does
    // not generate a crash report. We use request_restart() (non-diverging)
    // over restart() (diverging / never returns) because we are on an async
    // task and need the Tokio runtime to wind down cleanly.
    app.request_restart();
    Ok(())
}

/// Get sidecar status.
///
/// On Unix, cross-checks the stored PID with the OS to detect stale handles
/// (process died without going through stop_sidecar). On Windows we trust the
/// handle — a held handle means the process is alive.
#[tauri::command]
async fn sidecar_status(state: tauri::State<'_, SidecarState>) -> Result<SidecarStatus, String> {
    let mut guard = state.child.lock().unwrap();
    let running = if let Some(ref child) = *guard {
        #[cfg(unix)]
        {
            let pid = child.pid();
            let alive = unsafe { libc::kill(pid as libc::pid_t, 0) } == 0;
            if !alive {
                // Clear the stale handle so start_sidecar() can respawn.
                eprintln!("[sidecar] sidecar_status: pid={} is gone, clearing stale handle", pid);
                *guard = None;
                false
            } else {
                true
            }
        }
        #[cfg(not(unix))]
        {
            let _ = child; // suppress unused warning
            true
        }
    } else {
        false
    };
    Ok(SidecarStatus {
        running,
        // Real port from the engine's discovery file (~/.matrx/local.json);
        // 22140 is only the fallback when the file isn't readable yet. The
        // engine auto-scans 22140–22159, so hardcoding lied whenever 22140
        // was already taken.
        port: read_engine_port_from_discovery().unwrap_or(22140),
    })
}

/// Check if the engine health endpoint is responding on a given port.
///
/// This runs from Rust (not the WebView), so it is not subject to Windows'
/// WebView2 loopback network isolation restriction that blocks `fetch()`
/// calls to 127.0.0.1 from the JS layer on Windows.
#[tauri::command]
async fn check_engine_health(port: u16) -> Result<bool, String> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_millis(2000))
        .build()
        .map_err(|e| e.to_string())?;

    let url = format!("http://127.0.0.1:{}/tools/list", port);
    match client.get(&url).send().await {
        Ok(resp) => Ok(resp.status().is_success()),
        Err(_) => Ok(false),
    }
}

/// Scan the engine port range (22140–22159) and return the first port that responds.
///
/// Same rationale as check_engine_health — runs from Rust to bypass Windows
/// WebView2 loopback isolation that prevents JS fetch() from reaching 127.0.0.1.
#[tauri::command]
async fn discover_engine_port() -> Result<Option<u16>, String> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_millis(1000))
        .build()
        .map_err(|e| e.to_string())?;

    for port in 22140u16..22160u16 {
        let url = format!("http://127.0.0.1:{}/tools/list", port);
        if let Ok(resp) = client.get(&url).send().await {
            if resp.status().is_success() {
                return Ok(Some(port));
            }
        }
    }
    Ok(None)
}

/// Get recent sidecar output lines (for recovery modal diagnostics).
#[tauri::command]
async fn get_sidecar_logs(state: tauri::State<'_, SidecarLogs>) -> Result<Vec<String>, String> {
    let lines = state.lines.lock().unwrap();
    Ok(lines.clone())
}

/// Set whether closing the window hides to tray or quits the app.
#[tauri::command]
async fn set_close_to_tray(
    enabled: bool,
    state: tauri::State<'_, CloseToTray>,
) -> Result<(), String> {
    state.0.store(enabled, Ordering::Relaxed);
    Ok(())
}

/// Get current close-to-tray setting.
#[tauri::command]
async fn get_close_to_tray(state: tauri::State<'_, CloseToTray>) -> Result<bool, String> {
    Ok(state.0.load(Ordering::Relaxed))
}

/// Resize the main window to a compact recorder size or restore to full size.
///
/// Compact size: 420 × 240 px — just enough for a mic button + live transcript.
/// The minimum-size constraints are temporarily lifted so the window can shrink
/// below the normal 900 × 600 minimum. Restoring re-applies the original min size.
#[tauri::command]
async fn set_compact_mode(app: tauri::AppHandle, enabled: bool) -> Result<(), String> {
    use tauri::{LogicalPosition, LogicalSize};

    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "Main window not found".to_string())?;

    if enabled {
        // Remove min-size constraint first so the window can actually shrink.
        window
            .set_min_size(Some(LogicalSize::new(200u32, 160u32)))
            .map_err(|e| e.to_string())?;
        window
            .set_size(LogicalSize::new(420u32, 260u32))
            .map_err(|e| e.to_string())?;
        // Position in the upper-right corner so the macOS Dock and menu bar
        // don't obscure it. Leave a 20px gap from the top (menu bar) and
        // 20px from the right edge.
        if let Ok(monitor) = window.current_monitor() {
            if let Some(m) = monitor {
                let size = m.size();
                let scale = m.scale_factor();
                let sw = (size.width as f64 / scale) as u32;
                // 20 px below the macOS menu bar, 20 px from the right edge.
                let x = sw.saturating_sub(440) as i32;
                let y = 40_i32; // below menu bar
                let _ = window.set_position(LogicalPosition::new(x, y));
            }
        }
        // Keep decorations OFF (we draw our own title bar with data-tauri-drag-region).
        // always_on_top ensures it floats above other apps.
        window.set_decorations(false).map_err(|e| e.to_string())?;
        window.set_always_on_top(true).map_err(|e| e.to_string())?;
    } else {
        // Re-apply the normal minimum before resizing up.
        window.set_decorations(true).map_err(|e| e.to_string())?;
        window.set_always_on_top(false).map_err(|e| e.to_string())?;
        window
            .set_min_size(Some(LogicalSize::new(900u32, 600u32)))
            .map_err(|e| e.to_string())?;
        window
            .set_size(LogicalSize::new(1400u32, 900u32))
            .map_err(|e| e.to_string())?;
        window.center().map_err(|e| e.to_string())?;
    }

    Ok(())
}

/// Fetch a URL from Rust (bypasses all browser security restrictions).
/// Returns base64-encoded body + headers so the frontend can display it.
#[tauri::command]
async fn proxy_fetch(url: String) -> Result<FetchResponse, String> {
    use base64::Engine;

    let client = reqwest::Client::builder()
        .user_agent(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 \
             (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        )
        .danger_accept_invalid_certs(true)
        .redirect(reqwest::redirect::Policy::limited(10))
        .build()
        .map_err(|e| e.to_string())?;

    let resp = client.get(&url).send().await.map_err(|e| e.to_string())?;
    let final_url = resp.url().to_string();
    let status = resp.status().as_u16();
    let content_type = resp
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("text/html")
        .to_string();

    let bytes = resp.bytes().await.map_err(|e| e.to_string())?;
    let body_b64 = base64::engine::general_purpose::STANDARD.encode(&bytes);

    Ok(FetchResponse {
        status,
        content_type,
        body_b64,
        final_url,
    })
}

#[derive(Clone, Serialize, Debug)]
struct UpdateProgress {
    status: String,
    version: Option<String>,
    body: Option<String>,
    content_length: Option<u64>,
    downloaded: u64,
}

/// Check for app updates and optionally install them.
///
/// When `install` is true, downloads and installs the update, emitting
/// `update-progress` events with cumulative byte counts so the frontend
/// can render an accurate progress bar.
#[tauri::command]
async fn check_for_updates(app: tauri::AppHandle, install: bool) -> Result<UpdateProgress, String> {
    let updater = app
        .updater()
        .map_err(|e| format!("Updater not available: {}", e))?;

    let update = updater
        .check()
        .await
        .map_err(|e| format!("Update check failed: {}", e))?;

    match update {
        Some(update) => {
            let version = update.version.clone();
            let body = update.body.clone();

            if install {
                let app_handle = app.clone();
                let ver = version.clone();
                let total_downloaded = std::sync::Arc::new(std::sync::atomic::AtomicU64::new(0));
                let dl = total_downloaded.clone();

                update
                    .download_and_install(
                        move |chunk_length, content_length| {
                            let cumulative = dl.fetch_add(
                                chunk_length as u64,
                                std::sync::atomic::Ordering::Relaxed,
                            ) + chunk_length as u64;
                            let _ = app_handle.emit(
                                "update-progress",
                                UpdateProgress {
                                    status: "downloading".to_string(),
                                    version: Some(ver.clone()),
                                    body: None,
                                    content_length,
                                    downloaded: cumulative,
                                },
                            );
                        },
                        || {},
                    )
                    .await
                    .map_err(|e| format!("Update install failed: {}", e))?;

                let final_downloaded = total_downloaded.load(std::sync::atomic::Ordering::Relaxed);

                let result = UpdateProgress {
                    status: "installed".to_string(),
                    version: Some(version),
                    body,
                    content_length: None,
                    downloaded: final_downloaded,
                };

                let _ = app.emit("update-progress", result.clone());
                Ok(result)
            } else {
                Ok(UpdateProgress {
                    status: "available".to_string(),
                    version: Some(version),
                    body,
                    content_length: None,
                    downloaded: 0,
                })
            }
        }
        None => Ok(UpdateProgress {
            status: "up_to_date".to_string(),
            version: None,
            body: None,
            content_length: None,
            downloaded: 0,
        }),
    }
}

/// Return the pending OAuth deep-link URL (if one arrived before the frontend
/// listener was ready) and clear it from state. Returns null if none pending.
#[tauri::command]
fn get_pending_oauth_url(state: tauri::State<'_, PendingOAuthUrl>) -> Option<String> {
    state.0.lock().unwrap().take()
}

/// Bring the main window to front.
///
/// On macOS, `window.show()` alone is not enough when the app has been hidden
/// via `window.hide()` — the app process may not be the frontmost application,
/// so the window appears but receives no focus. We must call both `show()` and
/// `set_focus()`, and additionally `unminimize()` in case the window was
/// minimized into the Dock rather than hidden.
///
/// On macOS, we also switch the activation policy back to Regular so the Dock
/// icon appears (it was set to Accessory when the window was hidden to tray).
fn show_main_window(app: &tauri::AppHandle) {
    #[cfg(target_os = "macos")]
    {
        // Switch to Regular policy BEFORE show() so the Dock icon appears
        // at the same moment the window becomes visible.  Must be called on
        // the main thread — all callers of show_main_window run on the main
        // thread (tray event, Reopen event, single-instance callback).
        let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);
    }
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

/// Set up the system tray icon and menu.
///
/// Only ONE tray icon is created here — the auto-trayIcon in tauri.conf.json
/// has been removed to prevent a second blank icon from appearing.
fn setup_tray(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let show = MenuItemBuilder::with_id("show", "Show AI Matrx").build(app)?;
    let status = MenuItemBuilder::with_id("status", "Status: Starting...")
        .enabled(false)
        .build(app)?;
    let quit = MenuItemBuilder::with_id("quit", "Quit AI Matrx").build(app)?;

    let menu = MenuBuilder::new(app)
        .item(&show)
        .separator()
        .item(&status)
        .separator()
        .item(&quit)
        .build()?;

    // Use the application's default window icon for the tray.
    // `app.default_window_icon()` returns the icon configured by the Tauri build system
    // (from the icon list in tauri.conf.json), so the tray icon always matches the
    // dock / taskbar icon without any runtime file path resolution.
    let tray_builder = TrayIconBuilder::new()
        .menu(&menu)
        .tooltip("AI Matrx")
        .on_menu_event(move |app, event| match event.id().as_ref() {
            "show" => {
                show_main_window(app);
            }
            "quit" => {
                // Run graceful shutdown on a background thread so the main thread
                // stays responsive to macOS's NSApplication watchdog (which sends
                // SIGKILL if the main thread is unresponsive for ~5-10s).
                //
                // If SHUTDOWN_DONE is already true (e.g. the window CloseRequested
                // handler already started cleanup), we exit immediately instead of
                // spawning a redundant thread.
                if SHUTDOWN_DONE.load(Ordering::SeqCst) {
                    app.exit(0);
                    return;
                }
                let app_handle = app.clone();
                // AppHandle implements Send+Sync and can retrieve managed state
                // from any thread — use it instead of raw pointer casts.
                std::thread::spawn(move || {
                    if let (Some(sidecar), Some(transcription), Some(llm_proc)) = (
                        app_handle.try_state::<SidecarState>(),
                        app_handle.try_state::<TranscriptionState>(),
                        app_handle.try_state::<llm::commands::LlmProcessHandle>(),
                    ) {
                        let llm_srv = app_handle.try_state::<llm::commands::LlmServerState>();
                        let ww = app_handle.try_state::<WakeWordAppState>();
                        let rec = app_handle.try_state::<RecordingState>();
                        graceful_shutdown_sync(
                            &sidecar,
                            &transcription,
                            &llm_proc,
                            llm_srv.as_deref(),
                            ww.as_deref(),
                            rec.as_deref(),
                        );
                    }
                    app_handle.exit(0);
                });
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_main_window(tray.app_handle());
            }
        });

    let _tray = if let Some(icon) = app.default_window_icon() {
        tray_builder.icon(icon.clone()).build(app)?
    } else {
        tray_builder.build(app)?
    };

    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // Single-instance must be registered before deep-link so that on Windows,
        // when the OS launches a second instance to deliver an aimatrx:// deep-link
        // URL, this plugin intercepts it, terminates the new instance, and forwards
        // the argv (which contains the aimatrx:// URL) to the already-running app.
        // The callback below handles that forwarded URL the same way on_open_url does.
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            // Find the aimatrx:// URL in the forwarded arguments and process it.
            if let Some(url_str) = argv.iter().find(|a| a.starts_with("aimatrx://")) {
                println!("[single-instance] Received deep-link via argv: {}", url_str);
                show_main_window(app);
                if let Some(state) = app.try_state::<PendingOAuthUrl>() {
                    *state.0.lock().unwrap() = Some(url_str.clone());
                }
                let _ = app.emit("oauth-callback", url_str.clone());
            } else {
                // No deep-link URL — just bring the existing window to front.
                show_main_window(app);
            }
        }))
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_macos_permissions::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .manage(SidecarState {
            child: Mutex::new(None),
        })
        .manage(SidecarLogs {
            lines: Arc::new(Mutex::new(Vec::new())),
        })
        .manage(CloseToTray(AtomicBool::new(true)))
        .manage(PendingOAuthUrl(Mutex::new(None)))
        .manage(TranscriptionState(Mutex::new(None)))
        .manage(RecordingState::new())
        .manage(WakeWordAppState(Arc::new(WakeWordState::new())))
        .manage(transcription::commands::WhisperDownloadCancelState(
            std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)),
        ))
        .manage(
            std::sync::Arc::new(tokio::sync::Mutex::new(llm::server::LlmServer::new()))
                as llm::commands::LlmServerState,
        )
        .manage::<llm::commands::LlmProcessHandle>(std::sync::Arc::new(
            // PID slot (see LlmProcessHandle docs) — populated at spawn.
            std::sync::Mutex::new(None),
        ))
        .manage(llm::commands::LlmDownloadCancelState(
            std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)),
        ))
        // Universal download manager — initialized in .setup() below after AppHandle is available
        .invoke_handler(tauri::generate_handler![
            start_sidecar,
            stop_sidecar,
            restart_for_update,
            sidecar_status,
            get_sidecar_logs,
            check_engine_health,
            discover_engine_port,
            set_close_to_tray,
            get_close_to_tray,
            check_for_updates,
            set_compact_mode,
            proxy_fetch,
            get_pending_oauth_url,
            // Transcription commands
            detect_hardware,
            download_whisper_model,
            download_vad_model,
            cancel_whisper_download,
            init_transcription,
            check_model_exists,
            get_active_model,
            list_downloaded_models,
            delete_model,
            start_transcription,
            stop_transcription,
            list_audio_input_devices,
            get_voice_setup_status,
            // Wake word commands
            check_kws_model_exists,
            start_wake_word,
            stop_wake_word,
            mute_wake_word,
            unmute_wake_word,
            dismiss_wake_word,
            trigger_wake_word,
            configure_wake_word,
            get_wake_word_mode,
            is_wake_word_running,
            // LLM commands
            start_llm_server,
            stop_llm_server,
            get_llm_server_status,
            check_llm_server_health,
            check_llm_model_exists,
            download_llm_model,
            cancel_llm_download,
            import_local_llm_model,
            list_llm_models,
            delete_llm_model,
            detect_llm_hardware,
            get_llm_setup_status,
            save_hf_token,
            get_hf_token,
            // Floating overlay commands
            show_transcript_overlay,
            hide_transcript_overlay,
            // Universal download manager commands
            dm_enqueue,
            dm_cancel,
            dm_list,
            dm_get,
        ])
        .setup(|app| {
            // Kill orphaned LLM server immediately before the JS frontend has
            // a chance to issue commands to start it. This prevents the race condition
            // where `start_sidecar` used to blindly kill `llama-server` *while* it
            // was booting up.
            kill_orphaned_llama_server();

            // ── Universal download manager ─────────────────────────────────
            // Must be initialized here (after AppHandle is available) so the
            // worker tokio task has a live AppHandle for event emission.
            {
                let db_path = downloads::manager::default_db_path(app.handle());
                let dm = downloads::manager::DownloadManager::new(app.handle().clone(), db_path);
                app.manage(dm as downloads::commands::DownloadManagerState);
            }

            // ── Auto-initialize transcription model on startup ──────────────
            // If a model was previously set up, load it into memory immediately
            // so the Transcribe tab works without requiring the user to
            // re-run setup every session. This is fire-and-forget — a failure
            // here is non-fatal; the user can still use Setup tab to init.
            {
                let handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    use transcription::{
                        config::TranscriptionConfig, downloader, manager::TranscriptionManager,
                    };
                    let config_dir = match handle.path().app_data_dir() {
                        Ok(d) => d,
                        Err(_) => return,
                    };
                    let config = TranscriptionConfig::load(&config_dir);

                    // Apply persisted wake word keyword so the thread uses the
                    // saved value instead of the hardcoded default.
                    if !config.wake_keyword.is_empty() {
                        let ww = handle.state::<WakeWordAppState>();
                        *ww.0.keyword.lock().unwrap() = config.wake_keyword.clone();
                    }

                    if !config.setup_complete {
                        return;
                    }
                    let Some(filename) = config.selected_model else {
                        return;
                    };
                    let model_path = config_dir.join("models").join(&filename);
                    if !downloader::is_valid_model(&model_path) {
                        // Config says ready but file is gone — reset the flag so
                        // the UI prompts setup again instead of being stuck.
                        let reset = TranscriptionConfig {
                            setup_complete: false,
                            selected_model: None,
                            ..config
                        };
                        let _ = reset.save(&config_dir);
                        return;
                    }
                    let state = handle.state::<TranscriptionState>();
                    // Only load if not already initialized (another path may have beaten us).
                    if state.0.lock().unwrap().is_some() {
                        return;
                    }
                    match tokio::task::spawn_blocking(move || {
                        TranscriptionManager::load(model_path)
                    })
                    .await
                    {
                        Ok(Ok(manager)) => {
                            *state.0.lock().unwrap() = Some(manager);
                            println!("[transcription] Auto-loaded model: {}", filename);
                        }
                        Ok(Err(e)) => eprintln!("[transcription] Auto-load failed: {}", e),
                        Err(e) => eprintln!("[transcription] Auto-load task panicked: {}", e),
                    }
                });
            }

            // ── Auto-start LLM server on startup ───────────────────────────
            // OWNERSHIP: Rust owns llama-server. The Python engine MUST NOT
            // touch it (preflight excludes it from its SERVICES list, see
            // `app/preflight.py`). Spawning here is legitimate — Rust is
            // managing its own child.
            //
            // If a model was previously set up and its file exists on disk,
            // spawn llama-server in the background so the Confidential Chat
            // page is ready without user intervention. This runs after
            // Whisper init and is fully fire-and-forget — failures are
            // logged but do not affect app startup. The frontend hook
            // handles `llm-server-ready` and `get_llm_setup_status` to
            // reflect the running state.
            //
            // EVERY no-op exit path here logs WHY it skipped, with a
            // `[llm-autostart]` prefix so the operator can grep the unified
            // log to confirm whether this path ran or not. Without these
            // logs we have no way to distinguish "Rust auto-started it" from
            // "the user clicked Start" from "something else spawned it" —
            // which is the exact debugging hole that masked the engine vs.
            // Rust ownership conflict for so long.
            {
                let handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    use crate::transcription::hardware::HardwareProfile;
                    use llm::config::LlmConfig;
                    use llm::model_selector::compute_gpu_layers_for_hw;
                    use llm::server::find_free_port;

                    println!("[llm-autostart] entry");

                    let config_dir = match handle.path().app_data_dir() {
                        Ok(d) => d,
                        Err(e) => {
                            println!("[llm-autostart] skip: app_data_dir unavailable: {}", e);
                            return;
                        }
                    };
                    let config = LlmConfig::load(&config_dir);
                    if !config.setup_complete {
                        println!(
                            "[llm-autostart] skip: setup_complete=false — \
                             user has not finished LLM setup yet"
                        );
                        return;
                    }
                    let Some(filename) = config.selected_model.clone() else {
                        println!(
                            "[llm-autostart] skip: setup_complete=true but \
                             selected_model is None"
                        );
                        return;
                    };
                    let model_path = config_dir.join("models").join(&filename);
                    if !model_path.exists() {
                        println!(
                            "[llm-autostart] skip: selected_model={} but file \
                             not found at {}",
                            filename,
                            model_path.display()
                        );
                        return;
                    }

                    let llm_state = handle.state::<llm::commands::LlmServerState>();
                    {
                        let server = llm_state.lock().await;
                        if server.status.running {
                            println!(
                                "[llm-autostart] skip: server already running on port {}",
                                server.status.port
                            );
                            return;
                        }
                    }

                    println!(
                        "[llm-autostart] proceeding: model={} setup_complete=true file_exists=true running=false",
                        filename
                    );

                    // Detect hardware to pick the right gpu_layers value
                    let hw = HardwareProfile::detect();
                    let gpu_vram_gb = hw.gpu_vram_mb.map(|v| v as f32 / 1024.0).unwrap_or(0.0);
                    let gpu_layers = compute_gpu_layers_for_hw(&hw, gpu_vram_gb);
                    // Prefer last successful start context (custom models often need
                    // >>8192). Fall back to catalog value, then 8192.
                    let catalog_ctx = llm::model_selector::LLM_MODELS
                        .iter()
                        .find(|m| m.filename == filename)
                        .map(|m| m.context_length)
                        .or_else(|| {
                            llm::model_selector::LLM_MODELS.iter().find_map(|m| {
                                m.variants
                                    .iter()
                                    .find(|v| v.filename == filename)
                                    .map(|_| m.context_length)
                            })
                        });
                    let ctx = config
                        .last_context_length
                        .filter(|&c| c > 0)
                        .or(catalog_ctx)
                        .unwrap_or(8192);

                    let port = match find_free_port(11434) {
                        Ok(p) => p,
                        Err(e) => {
                            eprintln!("[llm-autostart] FAIL: could not find free port: {}", e);
                            return;
                        }
                    };

                    println!(
                        "[llm-autostart] spawning llama-server: model={} port={} gpu_layers={} ctx={}",
                        filename, port, gpu_layers, ctx
                    );

                    let _ = handle.emit(
                        "llm-server-starting",
                        serde_json::json!({
                            "model_filename": &filename,
                            "port": port,
                        }),
                    );

                    let model_path_str = model_path.to_string_lossy().to_string();

                    // Check if this model has an mmproj file for multimodal input
                    let mmproj_path: Option<String> = llm::model_selector::LLM_MODELS
                        .iter()
                        .find(|m| m.filename == filename)
                        .filter(|m| !m.mmproj_filename.is_empty())
                        .map(|m| config_dir.join("models").join(m.mmproj_filename))
                        .filter(|p| p.exists())
                        .map(|p| p.to_string_lossy().to_string());

                    // start_server does NOT hold the LlmServerState mutex across
                    // the health wait — status commands stay responsive while the
                    // model loads, and a user-triggered start during auto-start is
                    // rejected by its one-start-at-a-time guard.
                    match llm::server::start_server(
                        &handle,
                        llm_state.inner(),
                        &model_path_str,
                        gpu_layers,
                        ctx,
                        port,
                        mmproj_path.as_deref(),
                    )
                    .await
                    {
                        Ok(status) => {
                            // Persist last_port / last_context_length — reload to preserve
                            // hf_token and any other fields, then update only these.
                            let mut updated = LlmConfig::load(&config_dir);
                            updated.last_port = Some(port);
                            updated.last_context_length = Some(ctx);
                            let _ = updated.save(&config_dir);
                            let _ = handle.emit("llm-server-ready", &status);
                            println!(
                                "[llm-autostart] SUCCESS: llama-server ready on port {} (model={})",
                                port, filename
                            );
                        }
                        Err(e) => eprintln!("[llm-autostart] FAIL: start_server returned: {}", e),
                    }
                });
            }

            // Register the deep-link listener for OAuth callbacks.
            // When Supabase redirects to aimatrx://auth/callback?..., the OS fires this handler.
            let handle = app.handle().clone();
            app.deep_link().on_open_url(move |event| {
                let urls = event.urls();
                if let Some(url) = urls.first() {
                    let url_str = url.to_string();
                    println!("[deep-link] Received URL: {}", url_str);

                    // Bring the window to front
                    show_main_window(&handle);

                    // Store in app state — OAuthPending.tsx will poll this via
                    // get_pending_oauth_url() in case it wasn't mounted yet when
                    // the event fired (race condition on app activation).
                    if let Some(state) = handle.try_state::<PendingOAuthUrl>() {
                        *state.0.lock().unwrap() = Some(url_str.clone());
                    }

                    // Also emit the event for the case where OAuthPending IS
                    // already mounted and listening — whichever wins, the other
                    // is ignored via the handled.current guard.
                    let _ = handle.emit("oauth-callback", url_str);
                }
            });

            // Set up ONE system tray icon for the whole application.
            // The trayIcon declaration in tauri.conf.json has been removed to prevent
            // a second blank icon from appearing alongside this one.
            if let Err(e) = setup_tray(app) {
                eprintln!("Failed to setup tray: {}", e);
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                // Only act on the main window.  The transcript overlay has its own
                // lifecycle and should close freely without triggering app shutdown.
                if window.label() != "main" {
                    return;
                }

                let close_to_tray = window
                    .app_handle()
                    .try_state::<CloseToTray>()
                    .map(|s| s.0.load(Ordering::Relaxed))
                    .unwrap_or(true);

                if close_to_tray {
                    // Hide the window instead of closing — the Python sidecar keeps running
                    // in the background (accessible via the system tray icon).
                    // We do NOT kill the sidecar — it serves as the "server" half and must
                    // stay alive for background jobs and cloud connectivity.
                    let _ = window.hide();
                    api.prevent_close();

                    // On macOS: switch to Accessory policy so the Dock icon disappears
                    // when the window is hidden.  This prevents the confusing state where
                    // the Dock shows an icon for a window the user cannot find, which
                    // causes them to relaunch the app and produce two Dock icons.
                    // Must be called on the main thread — on_window_event runs on the main thread.
                    #[cfg(target_os = "macos")]
                    {
                        let _ = window.app_handle().set_activation_policy(tauri::ActivationPolicy::Accessory);
                    }
                } else {
                    // User explicitly chose "Quit" — run graceful shutdown on a background
                    // thread so the main thread stays responsive to the macOS watchdog.
                    // Prevent the close until cleanup is done, then close programmatically.
                    //
                    // If SHUTDOWN_DONE is already true (tray quit handler beat us here),
                    // let the close proceed immediately — cleanup is already running.
                    if SHUTDOWN_DONE.load(Ordering::SeqCst) {
                        // Cleanup already in progress — let window close naturally.
                        return;
                    }

                    // Prevent the close now; we will close the window after cleanup.
                    api.prevent_close();

                    let app_handle = window.app_handle().clone();
                    let window_label = window.label().to_string();

                    // AppHandle is Send+Sync — use it to retrieve state inside the thread.
                    std::thread::spawn(move || {
                        if let (Some(sidecar), Some(transcription), Some(llm_proc)) = (
                            app_handle.try_state::<SidecarState>(),
                            app_handle.try_state::<TranscriptionState>(),
                            app_handle.try_state::<llm::commands::LlmProcessHandle>(),
                        ) {
                            let llm_srv = app_handle.try_state::<llm::commands::LlmServerState>();
                            let ww = app_handle.try_state::<WakeWordAppState>();
                            let rec = app_handle.try_state::<RecordingState>();
                            graceful_shutdown_sync(
                                &sidecar,
                                &transcription,
                                &llm_proc,
                                llm_srv.as_deref(),
                                ww.as_deref(),
                                rec.as_deref(),
                            );
                        }
                        // Cleanup done — close the window programmatically.
                        // This fires CloseRequested again, but SHUTDOWN_DONE=true
                        // causes it to be a no-op, allowing the close to proceed.
                        if let Some(w) = app_handle.get_webview_window(&window_label) {
                            let _ = w.close();
                        }
                    });
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            match event {
                // On macOS, clicking the Dock icon when all windows are hidden fires
                // RunEvent::Reopen. Without this handler the click does nothing — the
                // app stays invisible. We re-show the main window here so the standard
                // Mac UX (click Dock icon → app comes back) works as expected.
                #[cfg(target_os = "macos")]
                tauri::RunEvent::Reopen {
                    has_visible_windows,
                    ..
                } => {
                    if !has_visible_windows {
                        show_main_window(app);
                    }
                }

                // RunEvent::ExitRequested fires when macOS (or the OS) sends a
                // termination signal — e.g. from Activity Monitor, `kill`, system
                // shutdown, or logout. Without this handler the process is terminated
                // immediately by the OS without running Rust destructors or child
                // process cleanup, which causes:
                //   • macOS crash reports ("did not exit cleanly")
                //   • The Python sidecar left running with its ports still bound
                //   • GGML atexit handlers calling abort() → SIGABRT
                //
                // We intentionally do NOT call api.prevent_exit() here — we just
                // run cleanup synchronously before the exit proceeds.
                tauri::RunEvent::ExitRequested { api, code: _, .. } => {
                    // If SHUTDOWN_DONE is true, cleanup has already run (from the tray
                    // quit handler or the window CloseRequested handler).  Let this
                    // ExitRequested proceed immediately — calling prevent_exit here would
                    // create an infinite loop: app.exit(0) → ExitRequested → prevent_exit
                    // → app.exit(0) → ExitRequested → …
                    //
                    // code mapping:
                    //   code=None     → native Cmd+Q / NSApplication terminate: → needs cleanup
                    //   code=Some(0)  → app.exit(0) from our own handlers → SHUTDOWN_DONE=true
                    //   code=Some(_)  → update restart (request_restart) → SHUTDOWN_DONE=true
                    if SHUTDOWN_DONE.load(Ordering::SeqCst) {
                        // Cleanup already complete — let the exit proceed.
                        return;
                    }

                    // Native OS quit (Cmd+Q, Activity Monitor, system shutdown).
                    // Prevent the exit, run cleanup on a background thread so the
                    // main thread stays responsive to macOS's NSApplication watchdog,
                    // then call app.exit(0) from the background thread when done.
                    api.prevent_exit();

                    let app_handle = app.clone();
                    // AppHandle is Send+Sync — use it to retrieve state inside the thread.
                    std::thread::spawn(move || {
                        if let (Some(sidecar), Some(transcription), Some(llm_proc)) = (
                            app_handle.try_state::<SidecarState>(),
                            app_handle.try_state::<TranscriptionState>(),
                            app_handle.try_state::<llm::commands::LlmProcessHandle>(),
                        ) {
                            let llm_srv = app_handle.try_state::<llm::commands::LlmServerState>();
                            let ww = app_handle.try_state::<WakeWordAppState>();
                            let rec = app_handle.try_state::<RecordingState>();
                            graceful_shutdown_sync(
                                &sidecar,
                                &transcription,
                                &llm_proc,
                                llm_srv.as_deref(),
                                ww.as_deref(),
                                rec.as_deref(),
                            );
                        }
                        app_handle.exit(0);
                    });
                }

                _ => {}
            }
        });
}
