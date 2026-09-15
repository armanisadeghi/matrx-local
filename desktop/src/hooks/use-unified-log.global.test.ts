// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import {
  clearClientLog,
  getClientLogBuffer,
  installGlobalErrorCapture,
} from "./use-unified-log";

describe("global renderer failure capture", () => {
  it("routes uncaught errors and unhandled rejections through the unified log", () => {
    clearClientLog();
    installGlobalErrorCapture();

    window.dispatchEvent(
      new ErrorEvent("error", {
        message: "render exploded",
        error: new Error("render exploded"),
      }),
    );
    const rejection = new Event("unhandledrejection") as PromiseRejectionEvent;
    Object.defineProperty(rejection, "reason", {
      value: new Error("promise exploded"),
    });
    window.dispatchEvent(rejection);

    expect(
      getClientLogBuffer().map(({ level, message, source }) => ({
        level,
        message,
        source,
      })),
    ).toEqual([
      {
        level: "error",
        message: "Error: render exploded",
        source: "runtime-exception",
      },
      {
        level: "error",
        message: "Error: promise exploded",
        source: "unhandled-rejection",
      },
    ]);
  });
});
