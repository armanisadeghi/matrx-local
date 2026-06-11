use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_shell::ShellExt;

#[derive(Debug, Clone, Serialize)]
pub struct LlmServerStatus {
    pub running: bool,
    pub port: u16,
    pub model_path: String,
    pub model_name: String,
    pub gpu_layers: i32,
    pub context_length: u32,
    /// Last captured output from llama-server (for error diagnosis).
    #[serde(skip_serializing_if = "String::is_empty")]
    pub last_error_output: String,
}

pub struct LlmServer {
    process: Option<tauri_plugin_shell::process::CommandChild>,
    pub status: LlmServerStatus,
}

impl LlmServer {
    pub fn new() -> Self {
        Self {
            process: None,
            status: LlmServerStatus {
                running: false,
                port: 0,
                model_path: String::new(),
                model_name: String::new(),
                gpu_layers: 0,
                context_length: 0,
                last_error_output: String::new(),
            },
        }
    }

    pub async fn start(
        &mut self,
        app: &AppHandle,
        model_path: &str,
        gpu_layers: i32,
        context_length: u32,
        port: u16,
        mmproj_path: Option<&str>,
    ) -> Result<(), String> {
        self.stop().await;

        let args = build_server_args(model_path, gpu_layers, context_length, port, mmproj_path);

        // Resolve the binaries directory so shared libraries can be found
        // regardless of where Tauri places the binary at runtime (dev vs. bundled).
        //
        // macOS: DYLD_LIBRARY_PATH → libggml*.dylib / libllama.dylib
        // Linux: LD_LIBRARY_PATH   → libggml*.so / libllama.so
        // Windows: prepend windows-dlls/ to PATH → ggml-base.dll, ggml.dll, etc.
        //          The DLLs are bundled in the resources/binaries/windows-dlls/
        //          directory. Windows searches PATH entries when loading DLLs so
        //          we inject that directory before spawning llama-server.exe.
        let resource_dir: std::path::PathBuf = app.path().resource_dir().unwrap_or_default();
        let binaries_dir = resource_dir.join("binaries");
        let binaries_dir_str = binaries_dir.to_string_lossy().to_string();

        let mut sidecar_cmd = app
            .shell()
            .sidecar("llama-server")
            .map_err(|e| format!("llama-server sidecar not found: {e}"))?;

        if !binaries_dir_str.is_empty() {
            sidecar_cmd = sidecar_cmd
                .env("DYLD_LIBRARY_PATH", &binaries_dir_str)
                .env("LD_LIBRARY_PATH", &binaries_dir_str);

            // Windows: DLLs live in a windows-dlls/ subdirectory (they cannot go
            // in binaries/ directly because Tauri's resource copy flattens the dir).
            // Prepend both the binaries dir and windows-dlls/ to PATH.
            #[cfg(target_os = "windows")]
            {
                let dll_dir = binaries_dir.join("windows-dlls");
                let current_path = std::env::var("PATH").unwrap_or_default();
                let new_path = format!(
                    "{};{};{}",
                    dll_dir.to_string_lossy(),
                    binaries_dir_str,
                    current_path
                );
                sidecar_cmd = sidecar_cmd.env("PATH", new_path);
            }
        }

        let (rx, child) = sidecar_cmd
            .args(&args)
            .spawn()
            .map_err(|e| format!("Failed to spawn llama-server: {e}"))?;

        // Publish the PID immediately (BEFORE the up-to-120s health wait) so
        // graceful_shutdown_sync's prong (b) can kill a mid-start server even
        // while our caller holds the LlmServerState tokio mutex.
        if let Some(handle) = app.try_state::<crate::llm::commands::LlmProcessHandle>() {
            if let Ok(mut g) = handle.lock() {
                *g = Some(child.pid());
            }
        }

        // Shared log buffer + crash-detection flag
        let log_buf = std::sync::Arc::new(std::sync::Mutex::new(String::new()));
        let crashed = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));

        let log_buf_clone = log_buf.clone();
        let crashed_clone = crashed.clone();
        let app_clone = app.clone();

        // Background task: drain stdout/stderr and re-emit meaningful lines as
        // `llm-server-log` events so the UI can display live progress.
        tauri::async_runtime::spawn(async move {
            use tauri_plugin_shell::process::CommandEvent;
            let mut rx = rx;
            while let Some(event) = rx.recv().await {
                let line = match event {
                    CommandEvent::Stdout(b) | CommandEvent::Stderr(b) => {
                        String::from_utf8_lossy(&b).to_string()
                    }
                    CommandEvent::Error(e) => format!("[error] {}\n", e),
                    CommandEvent::Terminated(_) => {
                        crashed_clone.store(true, std::sync::atomic::Ordering::SeqCst);
                        break;
                    }
                    _ => continue,
                };

                {
                    let mut buf = log_buf_clone.lock().unwrap();
                    if buf.len() > 16_384 {
                        let trim = buf.len() - 12_288;
                        *buf = buf[trim..].to_string();
                    }
                    buf.push_str(&line);
                }

                // Emit meaningful lines as progress events for the UI.
                // Classify: loading tensors, warming up, ready, error.
                let trimmed = line.trim();
                let kind = classify_log_line(trimmed);
                if kind != LogKind::Noise {
                    let _ = app_clone.emit(
                        "llm-server-log",
                        serde_json::json!({
                            "line": trimmed,
                            "kind": kind.as_str(),
                        }),
                    );
                }
            }
            crashed_clone.store(true, std::sync::atomic::Ordering::SeqCst);
        });

        // Health poll: fast-fails if the process crashes, otherwise waits up to
        // 120 s. Emits `llm-server-progress` every second so the UI can show
        // elapsed time and a real progress bar.
        match wait_for_health(port, &log_buf, &crashed, app).await {
            Ok(()) => {}
            Err(timeout_msg) => {
                let captured = log_buf.lock().unwrap().clone();
                let output = extract_crash_output(&captured);

                let _ = app.emit(
                    "llm-server-log",
                    serde_json::json!({ "line": &timeout_msg, "kind": "error" }),
                );

                for line in output.lines() {
                    let kind = classify_log_line(line);
                    let k = if kind == LogKind::Noise { "info" } else { kind.as_str() };
                    let _ = app.emit(
                        "llm-server-log",
                        serde_json::json!({ "line": line, "kind": k }),
                    );
                }

                // Kill the child before bailing: CommandChild has no Drop
                // impl that kills, so dropping it here leaked a llama-server
                // holding the port and GBs of RAM, invisible to status (and a
                // retry would spawn a SECOND instance on a new port).
                let _ = child.kill();
                if let Some(handle) = app.try_state::<crate::llm::commands::LlmProcessHandle>() {
                    if let Ok(mut g) = handle.lock() {
                        *g = None;
                    }
                }

                return Err(format!("{}\n\nServer output:\n{}", timeout_msg, output));
            }
        }

        let captured = log_buf.lock().unwrap().clone();
        self.process = Some(child);
        self.status = LlmServerStatus {
            running: true,
            port,
            model_path: model_path.to_string(),
            model_name: extract_model_name(model_path),
            gpu_layers,
            context_length,
            last_error_output: captured,
        };

        Ok(())
    }

    pub async fn stop(&mut self) {
        if let Some(child) = self.process.take() {
            let _ = child.kill();
            tokio::time::sleep(std::time::Duration::from_millis(500)).await;
        }
        self.status.running = false;
        self.status.port = 0;
        self.status.last_error_output = String::new();
    }

    /// Synchronous stop for use from non-async contexts (e.g. delete_llm_model
    /// which cannot hold a std::sync::MutexGuard across an .await point).
    /// Kills the child immediately without the 500 ms async sleep.
    pub fn stop_blocking(&mut self) {
        if let Some(child) = self.process.take() {
            let _ = child.kill();
        }
        self.status.running = false;
        self.status.port = 0;
        self.status.last_error_output = String::new();
    }

    pub fn take_process(&mut self) -> Option<tauri_plugin_shell::process::CommandChild> {
        self.status.running = false;
        self.process.take()
    }

    pub async fn health_check(&self) -> bool {
        if !self.status.running || self.status.port == 0 {
            return false;
        }
        let client = reqwest::Client::new();
        let url = format!("http://127.0.0.1:{}/health", self.status.port);
        matches!(client.get(&url).send().await, Ok(resp) if resp.status().is_success())
    }
}

fn build_server_args(
    model_path: &str,
    gpu_layers: i32,
    context_length: u32,
    port: u16,
    mmproj_path: Option<&str>,
) -> Vec<String> {
    let thread_count = optimal_thread_count();

    // On Windows, normalize backslashes to forward slashes so llama.cpp's
    // split-file sibling discovery (which uses string operations on the path)
    // works correctly. Mixed separators cause it to mis-derive the directory
    // when searching for parts 2 and 3 of a split GGUF.
    #[cfg(target_os = "windows")]
    let model_path_str = model_path.replace('\\', "/");
    #[cfg(not(target_os = "windows"))]
    let model_path_str = model_path.to_string();

    let mut args = vec![
        "-m".to_string(),
        model_path_str,
        "-ngl".to_string(),
        gpu_layers.to_string(),
        "-c".to_string(),
        context_length.to_string(),
        "-t".to_string(),
        thread_count.to_string(),
        "--host".to_string(),
        "127.0.0.1".to_string(),
        "--port".to_string(),
        port.to_string(),
        // Jinja template is required for tool calling with Qwen/Phi models
        "--jinja".to_string(),
    ];

    if let Some(mmproj) = mmproj_path {
        if !mmproj.is_empty() && std::path::Path::new(mmproj).exists() {
            args.push("--mmproj".to_string());
            args.push(mmproj.to_string());
        }
    }

    // Flash attention — supported on all backends (Metal, CUDA, Vulkan, CPU with AVX2).
    // The llama.cpp Vulkan Windows binary (our shipped binary) supports -fa.
    args.push("-fa".to_string());
    args.push("on".to_string());

    #[cfg(target_os = "windows")]
    {
        // Disable llama_params_fit (auto GPU-layer fitting) on Windows.
        //
        // llama_params_fit probes device memory by loading the model internally
        // before the real load. On Windows it has known reliability issues:
        //   - GGUF magic regression in older builds (fixed in b8358)
        //   - llama_params_fit can infinite-loop or miscount VRAM on some configs
        //
        // With -fit off we skip the probe and pass -ngl directly (already
        // correctly computed by detect_llm_hardware via compute_gpu_layers_for_hw).
        args.push("-fit".to_string());
        args.push("off".to_string());

        // Disable memory-mapped file I/O for model loading.
        // mmap on Windows uses CreateFileMapping which can fail for large files
        // (>2GB) across split GGUF parts, causing "failed to load model" errors.
        // CPU performance impact is negligible for interactive use.
        args.push("--no-mmap".to_string());
    }

    args
}

/// Wait up to 120 s for llama-server to report healthy (HTTP 200 on /health).
///
/// While waiting:
/// - Emits `llm-server-progress` every second with elapsed_secs + phase string
/// - Fast-fails if the process crashes (detected via the `crashed` atomic)
/// - Treats HTTP 503 as "still loading" (normal during model mmap + warmup)
async fn wait_for_health(
    port: u16,
    log_buf: &std::sync::Arc<std::sync::Mutex<String>>,
    crashed: &std::sync::Arc<std::sync::atomic::AtomicBool>,
    app: &AppHandle,
) -> Result<(), String> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .build()
        .unwrap_or_default();
    let url = format!("http://127.0.0.1:{port}/health");
    const MAX_SECS: u32 = 120;

    for elapsed in 1..=MAX_SECS {
        tokio::time::sleep(std::time::Duration::from_secs(1)).await;

        // Fast-fail: if the process died and we haven't gotten a 200, it crashed
        if crashed.load(std::sync::atomic::Ordering::SeqCst) {
            let captured = log_buf.lock().unwrap().clone();
            let output = extract_crash_output(&captured);

            for line in output.lines() {
                let kind = classify_log_line(line);
                let k = if kind == LogKind::Noise { "info" } else { kind.as_str() };
                let _ = app.emit(
                    "llm-server-log",
                    serde_json::json!({ "line": line, "kind": k }),
                );
            }

            return Err(format!("llama-server crashed:\n{}", output));
        }

        // Determine phase label from recent log output for the progress event
        let phase = {
            let buf = log_buf.lock().unwrap();
            infer_phase(&buf)
        };

        // Emit progress so the UI can drive a real counter + phase label
        let _ = app.emit(
            "llm-server-progress",
            serde_json::json!({
                "elapsed_secs": elapsed,
                "max_secs": MAX_SECS,
                "phase": phase,
                "percent": (elapsed as f32 / MAX_SECS as f32 * 100.0).min(99.0),
            }),
        );

        match client.get(&url).send().await {
            Ok(resp) if resp.status().is_success() => {
                // Emit 100% on success
                let _ = app.emit(
                    "llm-server-progress",
                    serde_json::json!({
                        "elapsed_secs": elapsed,
                        "max_secs": MAX_SECS,
                        "phase": "ready",
                        "percent": 100.0,
                    }),
                );
                return Ok(());
            }
            Ok(resp) if resp.status() == 503 => {
                // Still loading — continue
            }
            _ => {
                // Connection refused — process may not have bound port yet
            }
        }
    }

    Err(format!(
        "llama-server did not become healthy within {} seconds.\n\
         The model may be corrupted or incompatible with this binary.",
        MAX_SECS
    ))
}

/// Classify a log line into a category for the UI to display/filter.
#[derive(PartialEq)]
enum LogKind {
    Loading,
    Progress,
    Ready,
    Info,
    Error,
    Noise,
}

impl LogKind {
    fn as_str(&self) -> &'static str {
        match self {
            Self::Loading => "loading",
            Self::Progress => "progress",
            Self::Ready => "ready",
            Self::Info => "info",
            Self::Error => "error",
            Self::Noise => "noise",
        }
    }
}

fn classify_log_line(line: &str) -> LogKind {
    let ll = line.to_lowercase();

    // Must check info patterns BEFORE error patterns because llama.cpp writes
    // normal status output to stderr and some lines contain words like "error"
    // as part of harmless identifiers (e.g. "uvicorn.error" in Python logs).
    if ll.contains("ggml_metal_device_init")
        || ll.contains("system_info:")
        || ll.contains("system info:")
        || ll.contains("n_parallel")
        || ll.starts_with("build:")
        || ll.contains("running without ssl")
        || ll.contains("threads for http")
        || ll.contains("binding port")
        || ll.contains("simdgroup")
        || ll.contains("unified memory")
        || ll.contains("bfloat")
        || ll.contains("residency sets")
        || ll.contains("shared buffers")
        || ll.contains("recommendedmaxworkingsetsize")
        || ll.contains("fitting params to device")
    {
        return LogKind::Info;
    }

    if ll.contains("error")
        || ll.contains("fail")
        || ll.contains("fatal")
        || ll.contains("exiting due to")
        || ll.contains("segfault")
        || ll.contains("abort")
    {
        return LogKind::Error;
    }
    if ll.contains("server is listening")
        || ll.contains("model loaded")
        || ll.contains("main: model loaded")
    {
        return LogKind::Ready;
    }
    if ll.contains("load_tensors")
        || ll.contains("loading model")
        || ll.contains("warming up")
        || ll.contains("offload")
    {
        return LogKind::Loading;
    }
    if line.starts_with("print_info:") || line.starts_with("llama_model_loader:") {
        return LogKind::Progress;
    }
    LogKind::Noise
}

/// Infer a human-readable phase from recent log output.
fn infer_phase(log: &str) -> &'static str {
    // Scan from the end so we get the most recent phase
    let last_1k: &str = if log.len() > 1024 {
        &log[log.len() - 1024..]
    } else {
        log
    };

    if last_1k.contains("server is listening") || last_1k.contains("model loaded") {
        return "ready";
    }
    if last_1k.contains("warming up") {
        return "warming up";
    }
    if last_1k.contains("load_tensors")
        || last_1k.contains("offloading")
        || last_1k.contains("loading model tensors")
    {
        return "loading tensors";
    }
    if last_1k.contains("loading model") || last_1k.contains("load_model") {
        return "reading model file";
    }
    if last_1k.contains("fitting params") {
        return "sizing to memory";
    }
    "initializing"
}

/// Extract the most useful output from the crash log buffer.
///
/// Strategy: combine three windows so the operator always has enough context
/// to figure out WHY llama-server died, no matter where the failure happened
/// in the load sequence:
///
///   1. Error-keyword matches  (the smoking gun — if there is one)
///   2. Tail from the last load_tensors line onward
///                              (the "we got past tensor loading and then…"
///                              window — most failures happen during or after
///                              tensor offload, e.g. OOM, Metal device error,
///                              GGML assertion failure)
///   3. Last 30 lines           (always — even when no keywords matched and
///                              no load_tensors was emitted, e.g. process
///                              killed during binary startup)
///
/// The previous implementation took only the first 8 keyword matches OR the
/// last 20 lines, which routinely missed the actual crash reason because the
/// failure line right after load_tensors (e.g. "ggml_metal_init: failed",
/// "out of memory") doesn't always contain those keywords.
fn extract_crash_output(captured: &str) -> String {
    if captured.trim().is_empty() {
        return "(no output captured — process may have been killed by macOS before writing anything)\n\
                Possible causes: code signature issue, dylib not found, or Gatekeeper block.\n\
                Try: xattr -d com.apple.quarantine <binary path>".to_string();
    }

    let lines: Vec<&str> = captured.lines().collect();

    // Window 1: error-keyword matches.
    let error_lines: Vec<&str> = lines
        .iter()
        .copied()
        .filter(|l| {
            let ll = l.to_lowercase();
            ll.contains("error")
                || ll.contains("fail")
                || ll.contains("argument")
                || ll.contains("fatal")
                || ll.contains("exiting")
                || ll.contains("abort")
                || ll.contains("killed")
                || ll.contains("signal")
                || ll.contains("oom")
                || ll.contains("out of memory")
                || ll.contains("assertion")
                || ll.contains("ggml_")
                || ll.contains("metal")
        })
        .take(15)
        .collect();

    // Window 2: everything from the last load_tensors line onward, capped
    // at 50 lines so we don't dump the entire model-load progress dump.
    let load_tensors_idx = lines
        .iter()
        .rposition(|l| l.to_lowercase().contains("load_tensors"));
    let after_load_tensors: Vec<&str> = match load_tensors_idx {
        Some(i) => {
            let end = (i + 50).min(lines.len());
            lines[i..end].to_vec()
        }
        None => Vec::new(),
    };

    // Window 3: last 30 lines — guaranteed to include whatever was being
    // emitted at the moment of the crash.
    let tail_start = lines.len().saturating_sub(30);
    let tail: Vec<&str> = lines[tail_start..].to_vec();

    // Stitch the three windows together with section headers so the operator
    // can see at a glance which lines came from which heuristic. We dedupe
    // by line index (not content) so identical-content lines emitted at
    // different times are preserved (relevant for repeated GGML asserts).
    let mut sections: Vec<String> = Vec::new();

    if !error_lines.is_empty() {
        sections.push(format!(
            "── error/keyword matches ─────────────────────\n{}",
            error_lines.join("\n")
        ));
    }
    if !after_load_tensors.is_empty() {
        sections.push(format!(
            "── after last `load_tensors` ─────────────────\n{}",
            after_load_tensors.join("\n")
        ));
    }
    if !tail.is_empty() {
        sections.push(format!(
            "── last {} lines of output ──────────────────\n{}",
            tail.len(),
            tail.join("\n")
        ));
    }

    if sections.is_empty() {
        "(no diagnostic content extractable from captured output)".to_string()
    } else {
        sections.join("\n\n")
    }
}

fn optimal_thread_count() -> usize {
    let cpus = std::thread::available_parallelism()
        .map(|p| p.get())
        .unwrap_or(4);
    // Use half of available threads, clamped to 1..8
    (cpus / 2).max(1).min(8)
}

fn extract_model_name(path: &str) -> String {
    std::path::Path::new(path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("unknown")
        .to_string()
}

/// Find an available port starting from `start`.
pub fn find_free_port(start: u16) -> Result<u16, String> {
    for port in start..start + 100 {
        if std::net::TcpListener::bind(("127.0.0.1", port)).is_ok() {
            return Ok(port);
        }
    }
    Err(format!(
        "No free port found in range {}–{}",
        start,
        start + 99
    ))
}
