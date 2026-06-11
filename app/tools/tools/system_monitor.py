"""System monitoring tools — CPU, memory, disk, battery, and top processes."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from typing import Any

from app.common.platform_ctx import PLATFORM
from app.tools.session import ToolSession
from app.tools.types import ToolResult, ToolResultType

logger = logging.getLogger(__name__)


def _disk_usage_macos(path: str = "/") -> tuple[float, float, float, float] | None:
    """Get disk usage on macOS via df -P for accurate APFS metrics.

    psutil.disk_usage() uses os.statvfs() which overreports free space on APFS
    (ignores reserved storage, purgeable space, local snapshots). df -P output
    matches "About This Mac" storage. Returns (total_gb, used_gb, free_gb, percent)
    or None on failure.
    """
    try:
        result = subprocess.run(
            ["df", "-P", path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        lines = result.stdout.strip().split("\n")
        if len(lines) < 2:
            return None
        # df -P format: Filesystem 512-blocks Used Available Capacity Mounted
        parts = lines[1].split()
        if len(parts) < 5:
            return None
        total_blocks = int(parts[1])
        used_blocks = int(parts[2])
        avail_blocks = int(parts[3])
        block_size = 512
        total_bytes = total_blocks * block_size
        used_bytes = used_blocks * block_size
        avail_bytes = avail_blocks * block_size
        total_gb = total_bytes / (1024**3)
        used_gb = used_bytes / (1024**3)
        free_gb = avail_bytes / (1024**3)
        percent = (used_bytes / total_bytes * 100) if total_bytes > 0 else 0.0
        return (total_gb, used_gb, free_gb, percent)
    except (subprocess.TimeoutExpired, ValueError, IndexError) as e:
        logger.debug("macOS df disk usage failed: %s", e)
        return None


def _memory_usage_macos() -> dict[str, float] | None:
    """Get memory stats matching macOS Activity Monitor's display.

    Activity Monitor's "Memory Used" = (active + wired_down + compressor)
    * page_size. psutil.virtual_memory() on Darwin reports `used = active +
    wired`, missing the compressor pool — which on Apple Silicon machines is
    typically 1-4 GB and grows under memory pressure. That gap is why values
    look "wrong" compared to Activity Monitor.

    This implementation uses `sysctl hw.memsize` for total physical RAM and
    `vm_stat` for page-level counters, applying the same formula Activity
    Monitor uses.

    Returns a dict with total_gb, used_gb, available_gb, percent, wired_gb,
    active_gb, compressed_gb, or None on failure. macOS only.
    """
    try:
        # Total physical memory — sysctl is the authoritative source on macOS.
        total_result = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if total_result.returncode != 0 or not total_result.stdout.strip():
            return None
        total_bytes = int(total_result.stdout.strip())

        # Page-level breakdown via vm_stat.
        vm_result = subprocess.run(
            ["vm_stat"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if vm_result.returncode != 0 or not vm_result.stdout.strip():
            return None

        lines = vm_result.stdout.splitlines()
        # First line carries the page size: "Mach Virtual Memory Statistics: (page size of 16384 bytes)"
        page_size = 4096
        if lines and "page size of" in lines[0]:
            try:
                page_size = int(
                    lines[0].split("page size of", 1)[1].split("bytes", 1)[0].strip()
                )
            except (ValueError, IndexError):
                pass

        counters: dict[str, int] = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().strip('"')
            value = value.strip().rstrip(".")
            try:
                counters[key] = int(value)
            except ValueError:
                continue

        active = counters.get("Pages active", 0)
        wired = counters.get("Pages wired down", 0)
        compressed = counters.get("Pages occupied by compressor", 0)
        free = counters.get("Pages free", 0)
        inactive = counters.get("Pages inactive", 0)
        speculative = counters.get("Pages speculative", 0)

        used_bytes = (active + wired + compressed) * page_size
        # Activity Monitor's "available" = pages the kernel can hand back
        # immediately (free + inactive + speculative).
        available_bytes = (free + inactive + speculative) * page_size

        if total_bytes <= 0:
            return None

        return {
            "total_gb": total_bytes / (1024**3),
            "used_gb": used_bytes / (1024**3),
            "available_gb": available_bytes / (1024**3),
            "percent": used_bytes / total_bytes * 100,
            "wired_gb": wired * page_size / (1024**3),
            "active_gb": active * page_size / (1024**3),
            "compressed_gb": compressed * page_size / (1024**3),
        }
    except (
        subprocess.TimeoutExpired,
        FileNotFoundError,
        ValueError,
        IndexError,
    ) as e:
        logger.debug("macOS vm_stat memory query failed: %s", e)
        return None


async def tool_system_resources(
    session: ToolSession,
) -> ToolResult:
    """Get current system resource usage: CPU, RAM, disk, and network I/O."""
    try:
        import psutil

        # CPU
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_count = psutil.cpu_count()
        cpu_count_logical = psutil.cpu_count(logical=True)
        cpu_freq = psutil.cpu_freq()
        freq_str = f"{cpu_freq.current:.0f}MHz" if cpu_freq else "N/A"

        # Memory — on macOS use vm_stat to match Activity Monitor (psutil
        # under-reports because it omits the compressor pool). Fall back to
        # psutil on every other platform and if the vm_stat probe fails.
        swap = psutil.swap_memory()
        mac_mem = _memory_usage_macos() if PLATFORM["is_mac"] else None
        if mac_mem is not None:
            ram_total_gb = round(mac_mem["total_gb"], 1)
            ram_used_gb = round(mac_mem["used_gb"], 1)
            ram_available_gb = round(mac_mem["available_gb"], 1)
            ram_percent = round(mac_mem["percent"], 1)
            ram_wired_gb: float | None = round(mac_mem["wired_gb"], 1)
            ram_compressed_gb: float | None = round(mac_mem["compressed_gb"], 1)
        else:
            mem = psutil.virtual_memory()
            ram_total_gb = round(mem.total / (1024**3), 1)
            ram_used_gb = round(mem.used / (1024**3), 1)
            ram_available_gb = round(mem.available / (1024**3), 1)
            ram_percent = round(mem.percent, 1)
            ram_wired_gb = None
            ram_compressed_gb = None

        # Disk (root / or system drive on Windows)
        # On macOS, psutil uses statvfs which overreports free space on APFS
        # (ignores purgeable, reserved, snapshots). Use df -P for accuracy.
        disk_path = "/" if not PLATFORM["is_windows"] else os.environ.get("SystemDrive", "C:") + "\\"
        if PLATFORM["is_mac"]:
            mac_disk = _disk_usage_macos(disk_path)
        else:
            mac_disk = None
        if mac_disk is not None:
            total_gb, used_gb, free_gb, percent = mac_disk
            disk_total_gb = round(total_gb, 1)
            disk_used_gb = round(used_gb, 1)
            disk_free_gb = round(free_gb, 1)
            disk_percent = round(percent, 1)
        else:
            disk = psutil.disk_usage(disk_path)
            disk_total_gb = round(disk.total / (1024**3), 1)
            disk_used_gb = round(disk.used / (1024**3), 1)
            disk_free_gb = round(disk.free / (1024**3), 1)
            disk_percent = round(disk.percent, 1)

        # Network I/O
        net = psutil.net_io_counters()

        # Uptime
        import time

        boot_time = psutil.boot_time()
        uptime_seconds = time.time() - boot_time
        uptime_hours = uptime_seconds / 3600

        info: dict[str, Any] = {
            "cpu_percent": cpu_percent,
            "cpu_cores": cpu_count,
            "cpu_logical": cpu_count_logical,
            "cpu_freq": freq_str,
            # Memory — both `ram_*` (canonical) and `memory_*` (alias) keys are
            # emitted so frontend code using either name works.
            "ram_total_gb": ram_total_gb,
            "ram_used_gb": ram_used_gb,
            "ram_available_gb": ram_available_gb,
            "ram_percent": ram_percent,
            "memory_total_gb": ram_total_gb,
            "memory_used_gb": ram_used_gb,
            "memory_available_gb": ram_available_gb,
            "memory_percent": ram_percent,
            "memory_source": "vm_stat" if mac_mem is not None else "psutil",
            "swap_total_gb": round(swap.total / (1024**3), 1),
            "swap_used_gb": round(swap.used / (1024**3), 1),
            "disk_total_gb": disk_total_gb,
            "disk_used_gb": disk_used_gb,
            "disk_free_gb": disk_free_gb,
            "disk_percent": disk_percent,
            "net_sent_gb": round(net.bytes_sent / (1024**3), 2),
            "net_recv_gb": round(net.bytes_recv / (1024**3), 2),
            "uptime_hours": round(uptime_hours, 1),
            "uptime_seconds": round(uptime_seconds, 0),
        }
        if ram_wired_gb is not None:
            info["ram_wired_gb"] = ram_wired_gb
        if ram_compressed_gb is not None:
            info["ram_compressed_gb"] = ram_compressed_gb

        ram_breakdown = ""
        if ram_wired_gb is not None and ram_compressed_gb is not None:
            ram_breakdown = (
                f" [wired {ram_wired_gb:.1f} GB · compressed {ram_compressed_gb:.1f} GB]"
            )

        lines = [
            "System Resources:",
            f"  CPU:  {cpu_percent}% ({cpu_count} cores / {cpu_count_logical} threads @ {freq_str})",
            f"  RAM:  {ram_used_gb:.1f} / {ram_total_gb:.1f} GB ({ram_percent:.0f}%){ram_breakdown}",
            f"  Swap: {info['swap_used_gb']:.1f} / {info['swap_total_gb']:.1f} GB",
            f"  Disk: {disk_used_gb:.1f} / {disk_total_gb:.1f} GB ({disk_percent:.0f}%)",
            f"  Net:  Sent {info['net_sent_gb']:.2f} GB / Recv {info['net_recv_gb']:.2f} GB",
            f"  Uptime: {info['uptime_hours']:.1f} hours",
        ]

        return ToolResult(output="\n".join(lines), metadata=info)

    except ImportError:
        return _system_resources_fallback()


def _system_resources_fallback() -> ToolResult:
    """Fallback using system commands."""
    try:
        if PLATFORM["is_mac"]:
            result = subprocess.run(
                ["top", "-l", "1", "-s", "0"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            # Extract key lines
            lines = []
            for line in result.stdout.split("\n")[:12]:
                if any(kw in line for kw in ["CPU", "PhysMem", "Disk", "Networks"]):
                    lines.append(line.strip())
            return ToolResult(
                output="\n".join(lines) if lines else result.stdout[:2000]
            )
        elif PLATFORM["is_windows"]:
            result = subprocess.run(
                ["systeminfo"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return ToolResult(output=result.stdout[:3000])
        else:
            result = subprocess.run(
                ["free", "-h"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            result2 = subprocess.run(
                ["df", "-h", "/"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return ToolResult(
                output=f"Memory:\n{result.stdout}\nDisk:\n{result2.stdout}"
            )
    except Exception as e:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=(
                "System Monitoring is not installed. "
                "Go to Settings → Capabilities to install it, or open the Devices & Permissions page.\n"
                f"Developer info: pip install psutil. Error: {e}"
            ),
            metadata={"fix_capability_id": "system_monitoring"},
        )


async def tool_battery_status(
    session: ToolSession,
) -> ToolResult:
    """Get battery level, charging status, and time remaining."""
    try:
        import psutil

        battery = psutil.sensors_battery()
        if battery is None:
            return ToolResult(output="No battery detected (desktop system).")

        status = "Charging" if battery.power_plugged else "Discharging"
        time_left = ""
        if battery.secsleft > 0:
            hours = battery.secsleft // 3600
            minutes = (battery.secsleft % 3600) // 60
            time_left = f" ({hours}h {minutes}m remaining)"
        elif battery.secsleft == -1:
            time_left = " (calculating...)"
        elif battery.power_plugged:
            time_left = " (plugged in)"

        info = {
            "percent": battery.percent,
            "plugged_in": battery.power_plugged,
            "status": status,
            "seconds_left": battery.secsleft if battery.secsleft > 0 else None,
        }

        return ToolResult(
            output=f"Battery: {battery.percent}% — {status}{time_left}",
            metadata=info,
        )

    except ImportError:
        # Fallback
        try:
            if PLATFORM["is_mac"]:
                result = subprocess.run(
                    ["pmset", "-g", "batt"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                return ToolResult(output=result.stdout.strip())
            elif PLATFORM["is_windows"]:
                result = subprocess.run(
                    [
                        "powershell",
                        "-Command",
                        "(Get-WmiObject Win32_Battery | Select-Object EstimatedChargeRemaining, BatteryStatus | Format-List)",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                return ToolResult(
                    output=result.stdout.strip() or "No battery detected."
                )
            else:
                try:
                    with open("/sys/class/power_supply/BAT0/capacity") as f:
                        percent = f.read().strip()
                    with open("/sys/class/power_supply/BAT0/status") as f:
                        status = f.read().strip()
                    return ToolResult(output=f"Battery: {percent}% — {status}")
                except FileNotFoundError:
                    return ToolResult(output="No battery detected.")
        except Exception as e:
            return ToolResult(
                type=ToolResultType.ERROR, output=f"Battery check failed: {e}"
            )


async def tool_disk_usage(
    session: ToolSession,
    path: str | None = None,
) -> ToolResult:
    """Get disk usage for all mounted volumes or a specific path."""
    try:
        import psutil

        if path:
            resolved = session.resolve_path(path)
            usage = psutil.disk_usage(resolved)
            return ToolResult(
                output=f"Disk usage for {resolved}:\n"
                f"  Total: {usage.total / (1024**3):.1f} GB\n"
                f"  Used:  {usage.used / (1024**3):.1f} GB ({usage.percent}%)\n"
                f"  Free:  {usage.free / (1024**3):.1f} GB",
                metadata={
                    "path": resolved,
                    "total_gb": round(usage.total / (1024**3), 1),
                    "used_gb": round(usage.used / (1024**3), 1),
                    "free_gb": round(usage.free / (1024**3), 1),
                    "percent": usage.percent,
                },
            )

        partitions = psutil.disk_partitions()
        volumes = []
        lines = [
            f"{'MOUNT':<30} {'DEVICE':<25} {'TOTAL':>8} {'USED':>8} {'FREE':>8} {'USE%':>5}"
        ]
        lines.append("-" * 90)

        for part in partitions:
            try:
                usage = psutil.disk_usage(part.mountpoint)
                vol = {
                    "mount": part.mountpoint,
                    "device": part.device,
                    "fstype": part.fstype,
                    "total_gb": round(usage.total / (1024**3), 1),
                    "used_gb": round(usage.used / (1024**3), 1),
                    "free_gb": round(usage.free / (1024**3), 1),
                    "percent": usage.percent,
                }
                volumes.append(vol)
                lines.append(
                    f"{part.mountpoint:<30} {part.device:<25} "
                    f"{vol['total_gb']:>7.1f}G {vol['used_gb']:>7.1f}G "
                    f"{vol['free_gb']:>7.1f}G {usage.percent:>4.0f}%"
                )
            except (PermissionError, OSError):
                continue

        return ToolResult(
            output="\n".join(lines),
            metadata={"volumes": volumes},
        )

    except ImportError:
        try:
            if PLATFORM["is_windows"]:
                result = subprocess.run(
                    ["wmic", "logicaldisk", "get", "size,freespace,caption"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            else:
                result = subprocess.run(
                    ["df", "-h"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            return ToolResult(output=result.stdout)
        except Exception as e:
            return ToolResult(
                type=ToolResultType.ERROR, output=f"Disk usage failed: {e}"
            )


async def tool_top_processes(
    session: ToolSession,
    sort_by: str = "cpu",
    limit: int = 15,
) -> ToolResult:
    """Get top processes by CPU or memory usage."""
    try:
        import psutil

        # cpu_percent needs two samples: the first call on a fresh Process
        # object always returns 0.0, so sorting by a single process_iter pass
        # produced an arbitrary order. Prime, wait briefly, then read.
        if sort_by != "memory":
            procs = list(
                psutil.process_iter(["pid", "name", "memory_info", "memory_percent"])
            )
            for proc in procs:
                try:
                    proc.cpu_percent(interval=None)  # prime
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            await asyncio.sleep(0.25)
        else:
            procs = list(
                psutil.process_iter(["pid", "name", "memory_info", "memory_percent"])
            )

        processes = []
        for proc in procs:
            try:
                info = proc.info
                mem = info.get("memory_info")
                try:
                    cpu = proc.cpu_percent(interval=None) if sort_by != "memory" else 0.0
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    cpu = 0.0
                processes.append(
                    {
                        "pid": info["pid"],
                        "name": info.get("name", "?"),
                        "cpu_percent": round(cpu, 1),
                        "memory_mb": round(mem.rss / (1024 * 1024), 1) if mem else 0,
                        "memory_percent": round(
                            info.get("memory_percent", 0.0) or 0.0, 1
                        ),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        key = "memory_mb" if sort_by == "memory" else "cpu_percent"
        processes.sort(key=lambda p: p.get(key, 0), reverse=True)
        top = processes[:limit]

        lines = [
            f"Top {limit} processes by {'memory' if sort_by == 'memory' else 'CPU'}:"
        ]
        lines.append(f"{'PID':>8}  {'CPU%':>6}  {'MEM MB':>8}  {'MEM%':>5}  NAME")
        lines.append("-" * 55)
        for p in top:
            lines.append(
                f"{p['pid']:>8}  {p['cpu_percent']:>6.1f}  {p['memory_mb']:>8.1f}  "
                f"{p['memory_percent']:>4.1f}%  {p['name']}"
            )

        return ToolResult(output="\n".join(lines), metadata={"processes": top})

    except ImportError:
        try:
            if PLATFORM["is_windows"]:
                result = subprocess.run(
                    ["tasklist", "/FO", "TABLE", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            else:
                sort_flag = "-%mem" if sort_by == "memory" else "-%cpu"
                result = subprocess.run(
                    ["ps", "aux", "--sort", sort_flag],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            output = "\n".join(result.stdout.split("\n")[: limit + 1])
            return ToolResult(output=output)
        except Exception as e:
            return ToolResult(
                type=ToolResultType.ERROR, output=f"Top processes failed: {e}"
            )
