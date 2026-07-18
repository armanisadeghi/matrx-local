import { describe, expect, it } from "vitest";
import { cloudModelDisplayName, type CloudModelOption } from "./cloud-chat-models";

const models: CloudModelOption[] = [
  {
    id: "claude-sonnet-4-5",
    catalogId: "617abdcd-79e2-4a4b-be76-4a9960cdffa1",
    label: "Claude Sonnet 4.5",
    provider: "anthropic",
  },
];

describe("cloud model display names", () => {
  it("resolves both catalog UUIDs and API model names", () => {
    const model = models[0]!;
    expect(cloudModelDisplayName(model.catalogId, models)).toBe("Claude Sonnet 4.5");
    expect(cloudModelDisplayName(model.id, models)).toBe("Claude Sonnet 4.5");
  });

  it("keeps an unknown reference visible", () => {
    expect(cloudModelDisplayName("future-model", models)).toBe("future-model");
  });
});
