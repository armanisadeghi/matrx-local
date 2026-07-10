use serde::Serialize;
use sysinfo::System;

#[derive(Debug, Clone, Serialize)]
pub struct HardwareProfile {
    pub total_ram_mb: u64,
    pub cpu_threads: usize,
    pub gpu_vram_mb: Option<u64>,
    pub supports_cuda: bool,
    /// True when a Vulkan-capable GPU is present (Windows/Linux).
    /// This covers NVIDIA, AMD, and Intel GPUs via the Vulkan API.
    pub supports_vulkan: bool,
    pub supports_metal: bool,
    pub is_apple_silicon: bool,
    /// Human-readable GPU name(s) discovered during detection (for UI display).
    pub gpu_name: Option<String>,
}

impl HardwareProfile {
    pub fn detect() -> Self {
        let mut sys = System::new();
        sys.refresh_memory();
        sys.refresh_cpu_list(sysinfo::CpuRefreshKind::new());

        let total_ram_mb = sys.total_memory() / 1024 / 1024;
        let cpu_threads = sys.cpus().len();

        let is_apple_silicon = detect_apple_silicon();
        let (gpu_vram_mb, supports_cuda, supports_vulkan, gpu_name) = detect_gpu_capabilities();

        // Metal is available on all macOS (10.11+)
        let supports_metal = cfg!(target_os = "macos");

        HardwareProfile {
            total_ram_mb,
            cpu_threads,
            gpu_vram_mb,
            supports_cuda,
            supports_vulkan,
            supports_metal,
            is_apple_silicon,
            gpu_name,
        }
    }
}

fn detect_apple_silicon() -> bool {
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("uname")
            .arg("-m")
            .output()
            .map(|o| String::from_utf8_lossy(&o.stdout).contains("arm64"))
            .unwrap_or(false)
    }
    #[cfg(not(target_os = "macos"))]
    {
        false
    }
}

/// Best-effort GPU detection.
///
/// Returns `(vram_mb, supports_cuda, supports_vulkan, gpu_name)`.
///
/// Detection order:
///   1. `nvidia-smi` — most reliable for NVIDIA CUDA; also implies Vulkan.
///   2. `vulkaninfo` — catches AMD, Intel, and NVIDIA when nvidia-smi is not on
///      PATH. Available from Vulkan SDK or bundled with most GPU drivers.
///   3. Windows `wmic path Win32_VideoController` — fallback that works without
///      any SDK, gives us at least a GPU name and approximate VRAM.
///   4. Linux `/sys/class/drm` VRAM files — no external tools required.
fn detect_gpu_capabilities() -> (Option<u64>, bool, bool, Option<String>) {
    #[cfg(target_os = "macos")]
    {
        return (None, false, false, None);
    }

    #[cfg(not(target_os = "macos"))]
    {
        // ── 1. nvidia-smi (NVIDIA CUDA + Vulkan) ─────────────────────────────
        if let Some(result) = try_nvidia_smi() {
            return result;
        }

        // ── 2. vulkaninfo (AMD / Intel / NVIDIA without nvidia-smi on PATH) ──
        if let Some(result) = try_vulkaninfo() {
            return result;
        }

        // ── 3. Windows wmic fallback ──────────────────────────────────────────
        #[cfg(target_os = "windows")]
        if let Some(result) = try_wmic_gpu() {
            return result;
        }

        // ── 4. Linux /sys/class/drm VRAM ─────────────────────────────────────
        #[cfg(target_os = "linux")]
        if let Some(result) = try_linux_drm_vram() {
            return result;
        }

        (None, false, false, None)
    }
}

/// Return a list of nvidia-smi binary paths to try, in priority order.
///
/// On Windows the Tauri sidecar may inherit a stripped PATH that omits
/// C:\Windows\System32, so we probe the canonical locations directly.
/// On WSL (Linux target compiled for WSL host) the NVIDIA userspace bridge
/// exposes nvidia-smi at /usr/lib/wsl/lib/nvidia-smi.
#[cfg(not(target_os = "macos"))]
fn nvidia_smi_candidates() -> Vec<String> {
    let mut candidates: Vec<String> = vec!["nvidia-smi".to_string()];

    #[cfg(target_os = "windows")]
    {
        for path in &[
            r"C:\Windows\System32\nvidia-smi.exe",
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        ] {
            if std::path::Path::new(path).exists() {
                candidates.push(path.to_string());
            }
        }
    }

    #[cfg(target_os = "linux")]
    {
        // WSL bridge paths
        for path in &["/usr/lib/wsl/lib/nvidia-smi"] {
            if std::path::Path::new(path).exists() {
                candidates.push(path.to_string());
            }
        }
    }

    candidates
}

/// Try `nvidia-smi` for NVIDIA CUDA detection.
/// Probes multiple candidate paths to handle PATH-restricted sidecar environments.
#[cfg(not(target_os = "macos"))]
fn try_nvidia_smi() -> Option<(Option<u64>, bool, bool, Option<String>)> {
    let args = [
        "--query-gpu=memory.total,name",
        "--format=csv,noheader,nounits",
    ];

    for smi in nvidia_smi_candidates() {
        let output = std::process::Command::new(&smi).args(args).output();

        let output = match output {
            Ok(o) => o,
            Err(_) => continue,
        };

        if !output.status.success() {
            continue;
        }

        let text = String::from_utf8_lossy(&output.stdout);
        let line = match text.lines().next() {
            Some(l) => l.trim().to_string(),
            None => continue,
        };

        if line.is_empty() {
            continue;
        }

        // Format: "VRAM_MiB, GPU Name"
        let mut parts = line.splitn(2, ',');
        let vram_str = parts.next()?.trim();
        let name = parts.next().map(|s| s.trim().to_string());
        let vram_mb = match vram_str.parse::<u64>() {
            Ok(v) => v,
            Err(_) => continue,
        };

        // nvidia-smi success → CUDA present, Vulkan also present on any NVIDIA driver
        return Some((Some(vram_mb), true, true, name));
    }

    None
}

/// Try `vulkaninfo --summary` to detect Vulkan-capable GPUs (AMD, Intel, NVIDIA).
/// Falls back to `vulkaninfo` without --summary for older SDK versions.
#[cfg(not(target_os = "macos"))]
fn try_vulkaninfo() -> Option<(Option<u64>, bool, bool, Option<String>)> {
    // Try --summary first (Vulkan SDK 1.3.204+)
    let output = std::process::Command::new("vulkaninfo")
        .args(["--summary"])
        .output()
        .or_else(|_| std::process::Command::new("vulkaninfo").output())
        .ok()?;

    if !output.status.success() && output.stdout.is_empty() {
        return None;
    }

    let text = String::from_utf8_lossy(&output.stdout);

    // Extract GPU name from "deviceName = ..." line
    let gpu_name = text
        .lines()
        .find(|l| l.contains("deviceName"))
        .and_then(|l| l.split('=').nth(1))
        .map(|s| s.trim().trim_matches('"').to_string());

    if gpu_name.is_none() && !text.contains("Vulkan") {
        return None;
    }

    // We found a Vulkan device — try to parse VRAM from the output.
    // vulkaninfo reports heap sizes in bytes under "memoryHeaps" sections.
    // Device-local heaps (flags include DEVICE_LOCAL_BIT = 0x1) hold GPU VRAM.
    let vram_mb = parse_vulkan_vram(&text);

    Some((vram_mb, false, true, gpu_name))
}

/// Parse the largest device-local heap size from vulkaninfo output (bytes → MiB).
#[cfg(not(target_os = "macos"))]
fn parse_vulkan_vram(vulkaninfo_output: &str) -> Option<u64> {
    // Real vulkaninfo heap blocks look like:
    //   memoryHeaps[0]:
    //       size   = 25233588224 (0x5e0000000) (23.50 GiB)
    //       budget = ...
    //       usage  = ...
    //       flags: count = 1
    //           MEMORY_HEAP_DEVICE_LOCAL_BIT
    //
    // i.e. `size =` comes BEFORE the DEVICE_LOCAL flag line. The previous
    // parser armed on DEVICE_LOCAL and then looked for a following `size`
    // line, which never exists inside the same heap block — so AMD/Intel
    // VRAM was never detected and tier selection lowballed (24 GB GPU →
    // tiny model). Track both orders: remember the current heap's size and
    // record it when its DEVICE_LOCAL flag appears (and vice versa).
    let mut max_vram_bytes: u64 = 0;
    let mut current_heap_size: Option<u64> = None;
    let mut current_heap_device_local = false;

    let record = |size: Option<u64>, device_local: bool, max: &mut u64| {
        if device_local {
            if let Some(bytes) = size {
                if bytes > *max {
                    *max = bytes;
                }
            }
        }
    };

    for line in vulkaninfo_output.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with("memoryHeaps[") {
            // New heap block — reset per-heap state.
            current_heap_size = None;
            current_heap_device_local = false;
        } else if trimmed.starts_with("size") {
            // "size   = 8589934592 (0x200000000) (8.00 GiB)"
            if let Some(num_str) = trimmed.split('=').nth(1) {
                let num_part = num_str.trim().split_whitespace().next().unwrap_or("");
                if let Ok(bytes) = num_part.parse::<u64>() {
                    current_heap_size = Some(bytes);
                    record(current_heap_size, current_heap_device_local, &mut max_vram_bytes);
                }
            }
        } else if trimmed.contains("DEVICE_LOCAL") {
            current_heap_device_local = true;
            record(current_heap_size, current_heap_device_local, &mut max_vram_bytes);
        }
    }

    if max_vram_bytes > 0 {
        Some(max_vram_bytes / 1024 / 1024)
    } else {
        None
    }
}

/// Windows fallback: use `wmic` to query VideoController info.
/// Works without any GPU-specific SDK; gives GPU name and AdapterRAM.
#[cfg(target_os = "windows")]
fn try_wmic_gpu() -> Option<(Option<u64>, bool, bool, Option<String>)> {
    let output = std::process::Command::new("wmic")
        .args([
            "path",
            "Win32_VideoController",
            "get",
            "Name,AdapterRAM",
            "/format:csv",
        ])
        .output()
        .ok()?;

    if !output.status.success() {
        return None;
    }

    let text = String::from_utf8_lossy(&output.stdout);
    // CSV format: Node,AdapterRAM,Name
    // Skip header row
    let mut best_vram: u64 = 0;
    let mut best_name: Option<String> = None;

    for line in text.lines().skip(1) {
        let parts: Vec<&str> = line.split(',').collect();
        if parts.len() < 3 {
            continue;
        }
        let adapter_ram_str = parts[1].trim();
        let name = parts[2].trim().to_string();
        if name.is_empty() || name == "Name" {
            continue;
        }

        let vram_bytes = adapter_ram_str.parse::<u64>().unwrap_or(0);
        if vram_bytes > best_vram {
            best_vram = vram_bytes;
            best_name = Some(name);
        }
    }

    if best_name.is_none() {
        return None;
    }

    // wmic AdapterRAM is bytes; convert to MiB
    // Note: AdapterRAM often under-reports (BIOS-reported value), especially
    // for dedicated GPUs where it's capped at 4GB by WDDM. Use it as a lower
    // bound but treat any value >= 512MB as a usable discrete GPU.
    let vram_mb = if best_vram >= 512 * 1024 * 1024 {
        Some(best_vram / 1024 / 1024)
    } else {
        None
    };

    // We can't tell CUDA vs Vulkan from wmic alone. Assume Vulkan is available
    // for any discrete GPU (all modern GPUs support Vulkan 1.1+).
    // The Vulkan llama-server binary will handle the actual backend selection.
    Some((vram_mb, false, true, best_name))
}

/// Linux fallback: read VRAM from DRM sysfs (works for AMD and Intel).
#[cfg(target_os = "linux")]
fn try_linux_drm_vram() -> Option<(Option<u64>, bool, bool, Option<String>)> {
    let drm_path = std::path::Path::new("/sys/class/drm");
    if !drm_path.exists() {
        return None;
    }

    let mut max_vram_mb: u64 = 0;
    let entries = std::fs::read_dir(drm_path).ok()?;

    for entry in entries.flatten() {
        let mem_info = entry.path().join("device/mem_info_vram_total");
        if let Ok(content) = std::fs::read_to_string(&mem_info) {
            if let Ok(bytes) = content.trim().parse::<u64>() {
                let mb = bytes / 1024 / 1024;
                if mb > max_vram_mb {
                    max_vram_mb = mb;
                }
            }
        }
    }

    if max_vram_mb >= 512 {
        Some((Some(max_vram_mb), false, true, None))
    } else {
        None
    }
}
