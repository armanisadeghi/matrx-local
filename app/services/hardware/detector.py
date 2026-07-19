"""System hardware detection — single source of truth.

Collects a full hardware inventory for the local machine:
  • CPUs (model, cores, threads, frequency, architecture)
  • GPUs (name, VRAM, driver version, compute backend: Metal/CUDA/Vulkan)
  • RAM (total, available, type hint, speed when available)
  • Audio input devices (microphones — name, channels, sample rate)
  • Audio output devices (speakers/headphones)
  • Video capture devices (webcams, capture cards)
  • Monitors (name, resolution, refresh rate, primary flag)
  • Network adapters (name, type WiFi/Ethernet, MAC, IP, connected)
  • Storage devices (mount, total, free, type SSD/HDD, device name)

Detection is intentionally best-effort: every section is wrapped in a
try/except so a failure in one section never prevents others from running.
The caller always receives a complete dict with None / empty-list defaults
for any section that could not be probed.

Usage:
    from app.services.hardware.detector import detect_all

    profile = await detect_all()          # full async detection
    profile = detect_all_sync()           # sync fallback (blocks)
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import shutil
import subprocess
import time
from typing import Any

from app.common.process_shutdown import process_shutdown_requested

logger = logging.getLogger(__name__)

# ── Platform helpers ──────────────────────────────────────────────────────────

_SYS = platform.system()   # 'Darwin', 'Linux', 'Windows'
_IS_MAC = _SYS == "Darwin"
_IS_WIN = _SYS == "Windows"
_IS_LIN = _SYS == "Linux"


def _is_wsl() -> bool:
    """Return True when running inside Windows Subsystem for Linux."""
    if not _IS_LIN:
        return False
    try:
        return "microsoft" in open("/proc/version").read().lower()
    except Exception:
        return False


_IS_WSL = _is_wsl()


def _nvidia_smi_candidates() -> list[list[str]]:
    """Return candidate nvidia-smi invocations to try, in priority order.

    On WSL the NVIDIA userspace bridge exposes nvidia-smi at several well-known
    locations.  On a native Windows build the Python sidecar may inherit a PATH
    that does not include System32, so we probe the canonical paths directly.
    """
    candidates: list[list[str]] = []

    # Prefer whatever is on PATH first (works for native Linux / most WSL setups)
    smi = shutil.which("nvidia-smi")
    if smi:
        candidates.append([smi])

    if _IS_WSL:
        # WSL NVIDIA bridge — provided by the Windows NVIDIA driver
        for path in (
            "/usr/lib/wsl/lib/nvidia-smi",
            "/mnt/c/Windows/System32/nvidia-smi.exe",
        ):
            if os.path.isfile(path) and [path] not in candidates:
                candidates.append([path])
    elif _IS_WIN:
        # Bundled app may have a stripped PATH; probe System32 directly
        for path in (
            r"C:\Windows\System32\nvidia-smi.exe",
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        ):
            if os.path.isfile(path) and [path] not in candidates:
                candidates.append([path])

    # Fallback: just try the bare name (subprocess will search PATH again)
    if not candidates:
        candidates.append(["nvidia-smi"])

    return candidates


def _run(cmd: list[str], timeout: int = 5) -> str:
    """Run a probe command, aborting promptly during process shutdown."""
    if process_shutdown_requested():
        return ""

    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + timeout
        while True:
            if process_shutdown_requested():
                process.terminate()
                try:
                    process.communicate(timeout=0.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                return ""

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                process.communicate()
                return ""

            try:
                stdout, _stderr = process.communicate(timeout=min(0.1, remaining))
                return stdout.strip() if process.returncode == 0 else ""
            except subprocess.TimeoutExpired:
                continue
    except Exception:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate()
        return ""


def _run_nvidia_smi(args: list[str], timeout: int = 5) -> str:
    """Try each nvidia-smi candidate and return first successful stdout."""
    for base in _nvidia_smi_candidates():
        out = _run(base + args, timeout=timeout)
        if out:
            return out
    return ""


# ── CPU detection ─────────────────────────────────────────────────────────────

def _detect_cpus() -> list[dict[str, Any]]:
    """Detect all CPUs / sockets.

    Returns a list so multi-socket systems are handled correctly.
    For consumer machines this is almost always a list of one entry.
    """
    cpus: list[dict[str, Any]] = []

    try:
        import psutil  # type: ignore[import]

        freq = psutil.cpu_freq(percpu=False)
        logical = psutil.cpu_count(logical=True) or 0
        physical = psutil.cpu_count(logical=False) or 0

        model = platform.processor() or "Unknown"

        # macOS: richer model string from sysctl
        if _IS_MAC:
            brand = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
            if brand:
                model = brand
        elif _IS_LIN:
            for line in _run(["cat", "/proc/cpuinfo"]).splitlines():
                if "model name" in line.lower():
                    model = line.split(":", 1)[-1].strip()
                    break
        elif _IS_WIN:
            out = _run(["wmic", "cpu", "get", "Name", "/value"])
            for line in out.splitlines():
                if line.lower().startswith("name="):
                    model = line.split("=", 1)[-1].strip()
                    break

        cpus.append({
            "model": model,
            "physical_cores": physical,
            "logical_cores": logical,
            "threads_per_core": (logical // physical) if physical else None,
            "architecture": platform.machine(),
            "frequency_mhz": round(freq.current, 0) if freq else None,
            "frequency_max_mhz": round(freq.max, 0) if freq and freq.max else None,
        })
    except Exception as exc:
        logger.debug("CPU detection failed: %s", exc)
        cpus.append({
            "model": platform.processor() or "Unknown",
            "physical_cores": os.cpu_count(),
            "logical_cores": os.cpu_count(),
            "threads_per_core": None,
            "architecture": platform.machine(),
            "frequency_mhz": None,
            "frequency_max_mhz": None,
        })

    return cpus


# ── GPU detection ─────────────────────────────────────────────────────────────

def _detect_gpus() -> list[dict[str, Any]]:
    """Detect all GPUs with name, VRAM, and compute backend."""
    gpus: list[dict[str, Any]] = []

    # ── Apple Silicon / macOS (Metal) ────────────────────────────────────────
    if _IS_MAC:
        try:
            out = _run(
                ["system_profiler", "SPDisplaysDataType", "-json"],
                timeout=8,
            )
            import json as _json
            data = _json.loads(out)
            for i, gpu in enumerate(data.get("SPDisplaysDataType", [])):
                vram_str = gpu.get("spdisplays_vram") or gpu.get("spdisplays_vram_shared", "")
                vram_mb = _parse_vram_str(vram_str)
                # _name is the standard display name in system_profiler JSON output
                name = (
                    gpu.get("_name")
                    or gpu.get("spdisplays_device-id")
                    or "Unknown GPU"
                )
                gpus.append({
                    "name": name,
                    "vram_mb": vram_mb,
                    "driver_version": gpu.get("spdisplays_metalversion") or None,
                    "backend": "metal",
                    "is_primary": i == 0,
                })
        except Exception as exc:
            logger.debug("macOS GPU (json) detection failed: %s — trying text fallback", exc)
            # Text fallback
            try:
                out = _run(["system_profiler", "SPDisplaysDataType"], timeout=8)
                for line in out.splitlines():
                    line = line.strip()
                    if "Chipset Model:" in line:
                        name = line.split(":", 1)[-1].strip()
                        gpus.append({
                            "name": name,
                            "vram_mb": None,
                            "driver_version": None,
                            "backend": "metal",
                            "is_primary": len(gpus) == 0,
                        })
            except Exception:
                pass

        # Apple Silicon unified memory — fill vram_mb from RAM if not found
        if platform.machine() == "arm64":
            for gpu in gpus:
                gpu["backend"] = "metal"
                if gpu["vram_mb"] is None:
                    try:
                        import psutil  # type: ignore[import]
                        gpu["vram_mb"] = psutil.virtual_memory().total // (1024 * 1024)
                        gpu["vram_note"] = "unified_memory"
                    except Exception:
                        pass

        if not gpus:
            gpus.append({
                "name": "Apple GPU (Metal)",
                "vram_mb": None,
                "driver_version": None,
                "backend": "metal",
                "is_primary": True,
            })
        return gpus

    # ── NVIDIA via nvidia-smi (also covers WSL passthrough) ─────────────────
    logger.debug(
        "[hardware/gpu] WSL=%s, WIN=%s, LIN=%s — nvidia-smi candidates: %s",
        _IS_WSL, _IS_WIN, _IS_LIN,
        [c[0] for c in _nvidia_smi_candidates()],
    )
    try:
        out = _run_nvidia_smi(
            [
                "--query-gpu=name,memory.total,driver_version,index",
                "--format=csv,noheader,nounits",
            ],
            timeout=8,
        )
        logger.debug("[hardware/gpu] nvidia-smi output: %r", out[:200] if out else "(empty)")
        if out:
            for i, line in enumerate(out.splitlines()):
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    vram_mib = None
                    try:
                        vram_mib = int(parts[1])
                    except ValueError:
                        pass
                    gpus.append({
                        "name": parts[0],
                        "vram_mb": vram_mib,
                        "driver_version": parts[2] if len(parts) > 2 else None,
                        "backend": "cuda",
                        "is_primary": i == 0,
                    })
    except Exception:
        pass

    # ── Vulkan (covers AMD, Intel, and NVIDIA without nvidia-smi) ────────────
    vulkan_gpus: list[dict[str, Any]] = []
    try:
        vk_out = _run(["vulkaninfo", "--summary"], timeout=6)
        if not vk_out:
            vk_out = _run(["vulkaninfo"], timeout=6)
        if vk_out:
            # Parse "deviceName = ..." and "driverVersion = ..." blocks
            current: dict[str, Any] = {}
            for line in vk_out.splitlines():
                line = line.strip()
                if "deviceName" in line and "=" in line:
                    if current:
                        vulkan_gpus.append(current)
                        current = {}
                    current["name"] = line.split("=", 1)[-1].strip()
                    current["backend"] = "vulkan"
                    current["is_primary"] = len(vulkan_gpus) == 0
                elif "driverVersion" in line and "=" in line and current:
                    current["driver_version"] = line.split("=", 1)[-1].strip()
                elif "deviceType" in line and "=" in line and current:
                    current["device_type"] = line.split("=", 1)[-1].strip().lower()
            if current:
                vulkan_gpus.append(current)

            # Fill VRAM from Vulkan heap info (best-effort)
            _fill_vram_from_vulkan(vk_out, vulkan_gpus)
    except Exception:
        pass

    # Merge: if nvidia-smi already found the same GPU, just upgrade its backend
    # tag to "cuda+vulkan" rather than adding a second entry.  New devices
    # (AMD, Intel iGPU) get appended directly to gpus only — we must NOT
    # mutate vulkan_gpus while iterating over it.
    nvidia_names = {g["name"].lower() for g in gpus}
    new_from_vulkan: list[dict[str, Any]] = []
    for vk_gpu in vulkan_gpus:
        name_lc = vk_gpu.get("name", "").lower()
        if any(name_lc in nn or nn in name_lc for nn in nvidia_names):
            # Already covered by nvidia-smi — upgrade backend flag
            for g in gpus:
                if g["name"].lower() in name_lc or name_lc in g["name"].lower():
                    g["backend"] = "cuda+vulkan"
        else:
            # New device (AMD, Intel iGPU, etc.) — add to gpus
            vk_gpu.setdefault("vram_mb", None)
            vk_gpu.setdefault("driver_version", None)
            new_from_vulkan.append(vk_gpu)
    gpus.extend(new_from_vulkan)

    # ── Windows / WSL fallback: wmic for GPU name/VRAM if nothing found ─────
    # On WSL the wmic.exe binary is accessible via /mnt/c/Windows/System32/wmic.exe
    # or via the bare name if the Windows system path is in $PATH.
    if ((_IS_WIN or _IS_WSL) and not gpus):
        wmic_cmd: list[str] | None = None
        if _IS_WIN:
            wmic_cmd = ["wmic", "path", "win32_VideoController", "get", "Name,AdapterRAM", "/value"]
        else:
            # WSL: try native wmic.exe path
            for wmic_path in (
                "/mnt/c/Windows/System32/wmic.exe",
                shutil.which("wmic.exe") or "",
                shutil.which("wmic") or "",
            ):
                if wmic_path and os.path.isfile(wmic_path):
                    wmic_cmd = [wmic_path, "path", "win32_VideoController", "get", "Name,AdapterRAM", "/value"]
                    break

        if wmic_cmd:
            try:
                out = _run(wmic_cmd, timeout=8)
                current_gpu: dict[str, str] = {}

                def _flush_gpu(d: dict) -> None:
                    name = d.get("Name", "").strip()
                    if not name:
                        return
                    vram_bytes: int | None = None
                    try:
                        vram_bytes = int(d.get("AdapterRAM", "0") or "0")
                    except ValueError:
                        pass
                    gpus.append({
                        "name": name,
                        "vram_mb": vram_bytes // (1024 * 1024) if vram_bytes else None,
                        "driver_version": None,
                        "backend": "unknown",
                        "is_primary": len(gpus) == 0,
                    })

                for line in out.splitlines():
                    line = line.strip()
                    if not line:
                        if current_gpu:
                            _flush_gpu(current_gpu)
                            current_gpu = {}
                    elif "=" in line:
                        key, _, val = line.partition("=")
                        current_gpu[key.strip()] = val.strip()
                if current_gpu:
                    _flush_gpu(current_gpu)
            except Exception:
                pass

    # ── Linux fallback: /sys/class/drm ───────────────────────────────────────
    # Only look at card* entries, not renderD* or other device nodes, to avoid
    # creating duplicate entries for the same physical GPU.
    # Skip on WSL — DRM sysfs is either absent or maps to a virtual device that
    # does not represent the actual Windows GPU.
    if _IS_LIN and not _IS_WSL and not gpus:
        import pathlib
        try:
            for drm_dir in sorted(pathlib.Path("/sys/class/drm").iterdir()):
                # Only process card* entries (not renderD128, etc.)
                if not drm_dir.name.startswith("card"):
                    continue
                vendor_file = drm_dir / "device" / "vendor"
                model_file = drm_dir / "device" / "product_name"
                if not vendor_file.exists():
                    continue
                name = model_file.read_text().strip() if model_file.exists() else drm_dir.name
                drm_vram_mb: int | None = None
                mem_file = drm_dir / "device" / "mem_info_vram_total"
                if mem_file.exists():
                    try:
                        drm_vram_mb = int(mem_file.read_text().strip()) // (1024 * 1024)
                    except Exception:
                        pass
                gpus.append({
                    "name": name,
                    "vram_mb": drm_vram_mb,
                    "driver_version": None,
                    "backend": "vulkan",
                    "is_primary": len(gpus) == 0,
                })
        except Exception:
            pass

    # ROCm / AMD on Linux (not WSL — ROCm is not supported in WSL)
    if _IS_LIN and not _IS_WSL:
        try:
            out = _run(["rocm-smi", "--showproductname", "--csv"], timeout=5)
            if out:
                for line in out.splitlines()[1:]:
                    parts = line.split(",")
                    if len(parts) >= 2:
                        name = parts[1].strip()
                        if name and not any(g["name"] == name for g in gpus):
                            gpus.append({
                                "name": name,
                                "vram_mb": None,
                                "driver_version": None,
                                "backend": "rocm",
                                "is_primary": len(gpus) == 0,
                            })
        except Exception:
            pass

    if not gpus:
        gpus.append({
            "name": "No GPU detected",
            "vram_mb": None,
            "driver_version": None,
            "backend": "cpu",
            "is_primary": True,
        })

    return gpus


def _fill_vram_from_vulkan(vk_out: str, gpu_list: list[dict]) -> None:
    """Best-effort: parse heap sizes from vulkaninfo and assign to GPUs."""
    heaps: list[int] = []
    for line in vk_out.splitlines():
        line = line.strip()
        if re.search(r"size\s*=\s*\d+", line, re.IGNORECASE):
            m = re.search(r"size\s*=\s*(\d+)", line, re.IGNORECASE)
            if m:
                heaps.append(int(m.group(1)))
    if heaps and gpu_list:
        biggest = max(heaps)
        if biggest > 64 * 1024 * 1024:  # filter out tiny descriptor heaps
            for g in gpu_list:
                if g.get("vram_mb") is None:
                    g["vram_mb"] = biggest // (1024 * 1024)


def _parse_vram_str(vram_str: str) -> int | None:
    """Parse strings like '16384 MB', '16 GB', '16 Go' into MB integer."""
    if not vram_str:
        return None
    m = re.search(r"(\d[\d,\.]*)\s*(MB|MiB|GB|GiB|Go|Mo)", vram_str, re.IGNORECASE)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    unit = m.group(2).upper()
    if unit in ("GB", "GIB", "GO"):
        return int(value * 1024)
    return int(value)


# ── RAM detection ─────────────────────────────────────────────────────────────

def _detect_ram() -> dict[str, Any]:
    """Return total, available, type, and speed when available."""
    info: dict[str, Any] = {
        "total_mb": None,
        "available_mb": None,
        "type": None,
        "speed_mhz": None,
    }

    try:
        import psutil  # type: ignore[import]
        vm = psutil.virtual_memory()
        info["total_mb"] = vm.total // (1024 * 1024)
        info["available_mb"] = vm.available // (1024 * 1024)
    except Exception as exc:
        logger.debug("RAM psutil failed: %s", exc)

    # Memory type / speed (best effort per OS)
    if _IS_MAC:
        try:
            out = _run(["system_profiler", "SPMemoryDataType"], timeout=5)
            for line in out.splitlines():
                line = line.strip()
                if line.lower().startswith("type:"):
                    info["type"] = line.split(":", 1)[-1].strip()
                elif line.lower().startswith("speed:"):
                    speed_str = line.split(":", 1)[-1].strip()
                    m = re.search(r"(\d+)", speed_str)
                    if m:
                        info["speed_mhz"] = int(m.group(1))
        except Exception:
            pass
    elif _IS_WIN:
        try:
            # Use /value format to get key=value pairs — avoids CSV column-order
            # ambiguity (wmic sorts CSV columns alphabetically, which would put
            # Capacity before MemoryType and Speed).
            out = _run(
                ["wmic", "memorychip", "get",
                 "MemoryType,Speed", "/value"],
                timeout=5,
            )
            # SMBIOS memory type codes
            _MEM_TYPE_MAP = {
                20: "DDR", 21: "DDR2", 24: "DDR3",
                26: "DDR4", 34: "DDR5",
            }
            for line in out.splitlines():
                line = line.strip()
                if line.lower().startswith("memorytype=") and info["type"] is None:
                    try:
                        code = int(line.split("=", 1)[-1].strip())
                        if code > 0:
                            info["type"] = _MEM_TYPE_MAP.get(code, f"Type {code}")
                    except ValueError:
                        pass
                elif line.lower().startswith("speed=") and info["speed_mhz"] is None:
                    try:
                        speed = int(line.split("=", 1)[-1].strip())
                        if speed > 0:
                            info["speed_mhz"] = speed
                    except ValueError:
                        pass
        except Exception:
            pass
    elif _IS_LIN:
        try:
            out = _run(["sudo", "dmidecode", "-t", "17"], timeout=5)
            for line in out.splitlines():
                line = line.strip()
                if line.lower().startswith("type:") and info["type"] is None:
                    info["type"] = line.split(":", 1)[-1].strip()
                elif line.lower().startswith("speed:") and info["speed_mhz"] is None:
                    m = re.search(r"(\d+)", line)
                    if m:
                        info["speed_mhz"] = int(m.group(1))
        except Exception:
            pass

    return info


# ── Audio device detection ────────────────────────────────────────────────────

def _detect_audio_devices() -> dict[str, Any]:
    """Return input and output audio device lists."""
    inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []

    try:
        import sounddevice as sd  # type: ignore[import]
        # sd.query_devices() returns DeviceList, a *tuple* subclass — the old
        # `isinstance(devices, list)` check wrapped the whole list into a
        # 1-element list and the .get() below raised, so the profile always
        # reported zero audio devices. Only a single-device dict needs wrapping.
        devices = sd.query_devices()
        if isinstance(devices, dict):
            devices = [devices]

        host_apis = sd.query_hostapis()
        if isinstance(host_apis, dict):
            host_apis = [host_apis]

        host_api_names = {i: h.get("name", f"API {i}") for i, h in enumerate(host_apis)}

        for d in devices:
            entry: dict[str, Any] = {
                "name": d.get("name", "Unknown"),
                "host_api": host_api_names.get(d.get("hostapi", 0), "Unknown"),
                "channels": None,
                "default_sample_rate": d.get("default_samplerate"),
            }
            if d.get("max_input_channels", 0) > 0:
                entry["channels"] = d["max_input_channels"]
                inputs.append(entry.copy())
            if d.get("max_output_channels", 0) > 0:
                entry["channels"] = d["max_output_channels"]
                outputs.append(entry.copy())

    except Exception as exc:
        logger.debug("Audio device detection failed: %s", exc)

    return {"inputs": inputs, "outputs": outputs}


# ── Video capture device detection ────────────────────────────────────────────

def _detect_video_devices() -> list[dict[str, Any]]:
    """Enumerate camera / video capture devices."""
    devices: list[dict[str, Any]] = []

    if _IS_MAC:
        try:
            out = _run(["system_profiler", "SPCameraDataType"], timeout=5)
            for line in out.splitlines():
                line = line.strip()
                if line.endswith(":") and "camera" in line.lower():
                    devices.append({"name": line.rstrip(":"), "index": len(devices)})
                elif not devices and line and not line.startswith("Camera"):
                    pass
        except Exception:
            pass

    elif _IS_WIN:
        # Use wmic to get PnP device list, filter for image/capture
        try:
            out = _run(
                ["wmic", "path", "win32_PnPEntity",
                 "where", "PNPClass='Camera' OR PNPClass='Image'",
                 "get", "Name", "/format:list"],
                timeout=5,
            )
            for line in out.splitlines():
                line = line.strip()
                if line.lower().startswith("name="):
                    name = line.split("=", 1)[-1].strip()
                    if name:
                        devices.append({"name": name, "index": len(devices)})
        except Exception:
            pass

    elif _IS_LIN:
        import pathlib
        try:
            for dev in sorted(pathlib.Path("/dev").iterdir()):
                if dev.name.startswith("video"):
                    # Read the device name from /sys if possible
                    sys_name_path = (
                        pathlib.Path("/sys/class/video4linux") / dev.name / "name"
                    )
                    name = dev.name
                    if sys_name_path.exists():
                        name = sys_name_path.read_text().strip() or dev.name
                    devices.append({"name": name, "device": str(dev), "index": len(devices)})
        except Exception:
            pass

    return devices


# ── Monitor detection ─────────────────────────────────────────────────────────

def _detect_monitors() -> list[dict[str, Any]]:
    """Enumerate connected displays."""
    monitors: list[dict[str, Any]] = []

    # Try screeninfo first (cross-platform)
    try:
        import screeninfo  # type: ignore[import]
        for i, m in enumerate(screeninfo.get_monitors()):
            monitors.append({
                "name": getattr(m, "name", None) or f"Display {i + 1}",
                "width_px": m.width,
                "height_px": m.height,
                "width_mm": getattr(m, "width_mm", None),
                "height_mm": getattr(m, "height_mm", None),
                "x": m.x,
                "y": m.y,
                "is_primary": getattr(m, "is_primary", i == 0),
                "refresh_hz": None,
            })
    except Exception:
        pass

    # macOS: enrich with refresh rate via system_profiler
    if _IS_MAC and monitors:
        try:
            out = _run(["system_profiler", "SPDisplaysDataType"], timeout=8)
            idx = 0
            for line in out.splitlines():
                line = line.strip()
                if "Resolution:" in line and idx < len(monitors):
                    m = re.search(r"(\d+)\s*x\s*(\d+)\s*@\s*(\d+(?:\.\d+)?)", line)
                    if m:
                        monitors[idx]["refresh_hz"] = float(m.group(3))
                        idx += 1
        except Exception:
            pass

    elif _IS_WIN and not monitors:
        # Fallback: wmic desktopmonitor — use /value to avoid CSV column-sort issues
        try:
            out = _run(
                ["wmic", "desktopmonitor", "get",
                 "Name,ScreenWidth,ScreenHeight", "/value"],
                timeout=5,
            )
            # Collect key=value pairs for each monitor block
            current_mon: dict[str, str] = {}
            for line in out.splitlines():
                line = line.strip()
                if not line:
                    if current_mon:
                        try:
                            monitors.append({
                                "name": current_mon.get("Name", f"Display {len(monitors) + 1}"),
                                "width_px": int(current_mon["ScreenWidth"]) if current_mon.get("ScreenWidth", "").isdigit() else None,
                                "height_px": int(current_mon["ScreenHeight"]) if current_mon.get("ScreenHeight", "").isdigit() else None,
                                "is_primary": len(monitors) == 0,
                                "refresh_hz": None,
                            })
                        except (ValueError, KeyError):
                            pass
                        current_mon = {}
                elif "=" in line:
                    key, _, val = line.partition("=")
                    current_mon[key.strip()] = val.strip()
            # Final block (no trailing blank line)
            if current_mon:
                try:
                    monitors.append({
                        "name": current_mon.get("Name", f"Display {len(monitors) + 1}"),
                        "width_px": int(current_mon["ScreenWidth"]) if current_mon.get("ScreenWidth", "").isdigit() else None,
                        "height_px": int(current_mon["ScreenHeight"]) if current_mon.get("ScreenHeight", "").isdigit() else None,
                        "is_primary": len(monitors) == 0,
                        "refresh_hz": None,
                    })
                except (ValueError, KeyError):
                    pass
        except Exception:
            pass

    elif _IS_LIN and not monitors:
        try:
            out = _run(["xrandr", "--query"], timeout=5)
            for line in out.splitlines():
                if " connected " in line:
                    parts = line.split()
                    name = parts[0]
                    is_primary = "primary" in parts
                    m = re.search(r"(\d+)x(\d+)\+\d+\+\d+", line)
                    if m:
                        monitors.append({
                            "name": name,
                            "width_px": int(m.group(1)),
                            "height_px": int(m.group(2)),
                            "is_primary": is_primary,
                            "refresh_hz": None,
                        })
        except Exception:
            pass

    return monitors


# ── Network adapter detection ─────────────────────────────────────────────────

def _detect_network_adapters() -> list[dict[str, Any]]:
    """Enumerate network interfaces with name, type, MAC, IP, and link status."""
    adapters: list[dict[str, Any]] = []

    try:
        import psutil  # type: ignore[import]

        net_if_addrs = psutil.net_if_addrs()
        net_if_stats = psutil.net_if_stats()

        import socket

        AF_INET = socket.AF_INET
        AF_INET6 = socket.AF_INET6
        AF_PACKET = getattr(socket, "AF_PACKET", None)    # Linux
        AF_LINK = getattr(socket, "AF_LINK", None)        # macOS

        for name, addrs in net_if_addrs.items():
            mac: str | None = None
            ipv4: list[str] = []
            ipv6: list[str] = []

            for addr in addrs:
                if addr.family == AF_INET:
                    ipv4.append(addr.address)
                elif addr.family == AF_INET6:
                    ipv6.append(addr.address.split("%")[0])
                elif (AF_PACKET and addr.family == AF_PACKET) or \
                     (AF_LINK and addr.family == AF_LINK):
                    mac = addr.address

            stats = net_if_stats.get(name)
            is_up = stats.isup if stats else False
            speed_mbps = stats.speed if stats else None

            # Heuristic adapter type
            name_lc = name.lower()
            if any(x in name_lc for x in ("wi-fi", "wifi", "wlan", "wireless", "airport", "wlp", "wl0")):
                adapter_type = "wifi"
            elif any(x in name_lc for x in ("eth", "en0", "en1", "eno", "enp", "ethernet")):
                adapter_type = "ethernet"
            elif "lo" in name_lc or name_lc == "loopback":
                adapter_type = "loopback"
            elif any(x in name_lc for x in ("vpn", "tun", "tap", "utun")):
                adapter_type = "vpn"
            elif any(x in name_lc for x in ("bt", "bluetooth")):
                adapter_type = "bluetooth"
            else:
                adapter_type = "other"

            adapters.append({
                "name": name,
                "type": adapter_type,
                "mac": mac,
                "ipv4": ipv4,
                "ipv6": ipv6,
                "is_up": is_up,
                "speed_mbps": speed_mbps if speed_mbps and speed_mbps > 0 else None,
            })

    except Exception as exc:
        logger.debug("Network adapter detection failed: %s", exc)

    return adapters


# ── Storage device detection ──────────────────────────────────────────────────

def _detect_storage() -> list[dict[str, Any]]:
    """Enumerate partitions/mount points with capacity and type."""
    disks: list[dict[str, Any]] = []

    try:
        import psutil  # type: ignore[import]

        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue

            # Infer storage type from fstype/device name
            dev_lc = (part.device or "").lower()
            fs_lc = (part.fstype or "").lower()
            if any(x in dev_lc for x in ("nvme", "ssd", "solid")):
                disk_type = "ssd"
            elif any(x in dev_lc for x in ("hdd", "hd", "disk")) and "ssd" not in dev_lc:
                disk_type = "hdd"
            elif "apfs" in fs_lc or "hfs" in fs_lc:
                disk_type = "ssd"   # macOS APFS on SSDs by default
            elif "fat" in fs_lc or "cdfs" in fs_lc:
                disk_type = "optical_or_usb"
            else:
                disk_type = "unknown"

            disks.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "disk_type": disk_type,
                "total_gb": round(usage.total / (1024 ** 3), 2),
                "used_gb": round(usage.used / (1024 ** 3), 2),
                "free_gb": round(usage.free / (1024 ** 3), 2),
                "percent_used": usage.percent,
            })

    except Exception as exc:
        logger.debug("Storage detection failed: %s", exc)

    return disks


# ── Full profile assembly ─────────────────────────────────────────────────────

def detect_all_sync() -> dict[str, Any]:
    """Run full hardware detection synchronously (blocks the calling thread)."""
    from datetime import datetime, timezone

    logger.info("[hardware] Starting full hardware detection...")

    profile: dict[str, Any] = {
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "cpus": [],
        "gpus": [],
        "ram": {},
        "audio_inputs": [],
        "audio_outputs": [],
        "video_devices": [],
        "monitors": [],
        "network_adapters": [],
        "storage": [],
    }

    try:
        profile["cpus"] = _detect_cpus()
        logger.debug("[hardware] CPUs: %d detected", len(profile["cpus"]))
    except Exception as exc:
        logger.warning("[hardware] CPU detection error: %s", exc)
    if process_shutdown_requested():
        logger.info("[hardware] Detection stopped for process shutdown")
        return profile

    try:
        profile["gpus"] = _detect_gpus()
        logger.debug("[hardware] GPUs: %d detected", len(profile["gpus"]))
        for g in profile["gpus"]:
            logger.info(
                "[hardware/gpu] Detected: name=%r backend=%r vram_mb=%s wsl=%s",
                g.get("name"), g.get("backend"), g.get("vram_mb"), g.get("wsl", False),
            )
    except Exception as exc:
        logger.warning("[hardware] GPU detection error: %s", exc)
    if process_shutdown_requested():
        logger.info("[hardware] Detection stopped for process shutdown")
        return profile

    try:
        profile["ram"] = _detect_ram()
        logger.debug("[hardware] RAM: %s MB total", profile["ram"].get("total_mb"))
    except Exception as exc:
        logger.warning("[hardware] RAM detection error: %s", exc)
    if process_shutdown_requested():
        logger.info("[hardware] Detection stopped for process shutdown")
        return profile

    try:
        audio = _detect_audio_devices()
        profile["audio_inputs"] = audio["inputs"]
        profile["audio_outputs"] = audio["outputs"]
        logger.debug(
            "[hardware] Audio: %d inputs, %d outputs",
            len(profile["audio_inputs"]),
            len(profile["audio_outputs"]),
        )
    except Exception as exc:
        logger.warning("[hardware] Audio detection error: %s", exc)
    if process_shutdown_requested():
        logger.info("[hardware] Detection stopped for process shutdown")
        return profile

    try:
        profile["video_devices"] = _detect_video_devices()
        logger.debug("[hardware] Cameras: %d detected", len(profile["video_devices"]))
    except Exception as exc:
        logger.warning("[hardware] Video device detection error: %s", exc)
    if process_shutdown_requested():
        logger.info("[hardware] Detection stopped for process shutdown")
        return profile

    try:
        profile["monitors"] = _detect_monitors()
        logger.debug("[hardware] Monitors: %d detected", len(profile["monitors"]))
    except Exception as exc:
        logger.warning("[hardware] Monitor detection error: %s", exc)
    if process_shutdown_requested():
        logger.info("[hardware] Detection stopped for process shutdown")
        return profile

    try:
        profile["network_adapters"] = _detect_network_adapters()
        logger.debug("[hardware] Network adapters: %d detected", len(profile["network_adapters"]))
    except Exception as exc:
        logger.warning("[hardware] Network adapter detection error: %s", exc)
    if process_shutdown_requested():
        logger.info("[hardware] Detection stopped for process shutdown")
        return profile

    try:
        profile["storage"] = _detect_storage()
        logger.debug("[hardware] Storage: %d partitions", len(profile["storage"]))
    except Exception as exc:
        logger.warning("[hardware] Storage detection error: %s", exc)

    logger.info("[hardware] Detection complete.")
    return profile


async def detect_all() -> dict[str, Any]:
    """Run full hardware detection off the event loop (non-blocking)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, detect_all_sync)
