import { describe, expect, it } from "vitest";

import { stringifyErrorDetail } from "./api";

describe("stringifyErrorDetail", () => {
  it("renders structured runtime refusals without object coercion", () => {
    expect(
      stringifyErrorDetail(
        { code: "workspace_not_approved", detail: "Approve this workspace first" },
        "fallback",
      ),
    ).toBe("Approve this workspace first (workspace_not_approved)");
  });

  it("renders FastAPI validation arrays with their field paths", () => {
    expect(
      stringifyErrorDetail(
        [{ loc: ["body", "folder"], msg: "Field required", type: "missing" }],
        "fallback",
      ),
    ).toBe("folder: Field required");
  });
});
