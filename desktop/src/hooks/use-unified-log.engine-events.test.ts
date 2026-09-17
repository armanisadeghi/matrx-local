// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from "vitest";
import {
  clearClientLog,
  emitEngineLog,
  getClientLogBuffer,
  isEngineCrashSignal,
  recordTauriSidecarLog,
} from "./use-unified-log";

describe("canonical engine log events", () => {
  beforeEach(() => clearClientLog());

  it("coalesces one timestamped engine warning mirrored by server, syslog, and Tauri", () => {
    emitEngineLog(
      "server",
      "2026-09-16 12:00:00,123 - WARNING - [transport] reconnecting after timeout",
      "warn",
    );
    emitEngineLog(
      "syslog",
      "2026-09-16 12:00:00,123 - WARNING - [transport] reconnecting after timeout",
    );
    emitEngineLog("tauri", "[stdout] WARNING - [transport] reconnecting after timeout");

    expect(getClientLogBuffer()).toMatchObject([
      {
        level: "warn",
        message: "[transport] reconnecting after timeout",
        transportSources: ["server", "syslog", "tauri"],
      },
    ]);
  });

  it("keeps repeated same-message failures as separate timestamped occurrences", () => {
    const first = "2026-09-16 12:00:00,123 - ERROR - [transport] request failed";
    const second = "2026-09-16 12:00:01,123 - ERROR - [transport] request failed";
    emitEngineLog("server", first, "error");
    emitEngineLog("server", second, "error");
    emitEngineLog("syslog", first);
    emitEngineLog("syslog", second);

    const lines = getClientLogBuffer();
    expect(lines).toHaveLength(2);
    expect(lines.every((line) => line.level === "error")).toBe(true);
    expect(lines.map((line) => line.transportSources)).toEqual([
      ["server", "syslog"],
      ["server", "syslog"],
    ]);
  });

  it("keeps traceback frames as linked evidence for the preceding error", () => {
    emitEngineLog("tauri", "[stderr] ERROR - [transport] request failed");
    emitEngineLog("tauri", "[stderr] Traceback (most recent call last):");
    emitEngineLog("tauri", "[stderr]   File \"app/client.py\", line 10, in call");
    emitEngineLog("tauri", "[stderr] TimeoutError: request timed out");
    emitEngineLog("tauri", "[stdout] INFO - recovery queued");

    const lines = getClientLogBuffer();
    const parent = lines[0]!;
    expect(lines.filter((line) => line.level === "error")).toEqual([parent]);
    expect(lines.slice(1, 4)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ diagnosticContext: true, diagnosticParentId: parent.id }),
      ]),
    );
    expect(lines[4]).toMatchObject({ level: "info", message: "recovery queued" });
  });

  it("creates one exception incident for an orphan traceback and retains its final exception", () => {
    emitEngineLog("tauri", "[stderr] Traceback (most recent call last):");
    emitEngineLog("tauri", "[stderr]   File \"app/worker.py\", line 32, in run");
    emitEngineLog("tauri", "[stderr] KeyboardInterrupt");
    emitEngineLog("tauri", "[stdout] INFO - worker stopped");

    const lines = getClientLogBuffer();
    const parent = lines[0]!;
    expect(lines.filter((line) => line.level === "error")).toEqual([parent]);
    expect(parent.message).toBe("[traceback] exception details follow");
    expect(lines.slice(1, 4)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ diagnosticContext: true, diagnosticParentId: parent.id }),
      ]),
    );
    expect(lines[4]).toMatchObject({ level: "info", message: "worker stopped" });
  });

  it("does not attach an orphan traceback to an error before a recovery event", () => {
    emitEngineLog("tauri", "[stderr] ERROR - [transport] request failed");
    emitEngineLog("tauri", "[stdout] INFO - recovery queued");
    emitEngineLog("tauri", "[stderr] Traceback (most recent call last):");
    emitEngineLog("tauri", "[stderr] KeyboardInterrupt");

    const lines = getClientLogBuffer();
    const errors = lines.filter((line) => line.level === "error");
    expect(errors).toHaveLength(2);
    expect(errors[1]).toMatchObject({ message: "[traceback] exception details follow" });
    expect(lines[lines.length - 1]).toMatchObject({ diagnosticContext: true, diagnosticParentId: errors[1]?.id });
  });

  it("captures a real abnormal sidecar exit once and keeps normal lifecycle output ordinary", () => {
    expect(isEngineCrashSignal("[preflight] ✓ pid 42 terminated")).toBe(false);
    expect(isEngineCrashSignal("[terminated] Process exited: ExitStatus(unix_wait_status(0))")).toBe(false);
    expect(isEngineCrashSignal("[terminated] Process exited: signal: Some(15) expected=true")).toBe(false);
    expect(isEngineCrashSignal("[terminated] Process exited: signal: Some(15) expected=false")).toBe(true);
    [1, 2, 4, 8].forEach((signal) => {
      expect(isEngineCrashSignal(`[terminated] Process exited: signal: Some(${signal}) expected=false`)).toBe(true);
    });

    const lifecycle = recordTauriSidecarLog("[preflight] ✓ pid 42 terminated");
    recordTauriSidecarLog("[stdout] INFO - preparing request");
    const crash = recordTauriSidecarLog("[terminated] Process exited: signal: Some(9) expected=false");
    const lines = getClientLogBuffer();

    expect(lines.filter((line) => line.level === "error")).toEqual([crash]);
    expect(lifecycle).toMatchObject({ level: "info", message: "[preflight] ✓ pid 42 terminated" });
    expect(lines.filter((line) => line.diagnosticContext)).toHaveLength(5);
    expect(lines.filter((line) => line.diagnosticContext).every((line) => line.diagnosticParentId === crash.id)).toBe(true);
  });
});
