import { describe, expect, it } from "vitest";
import { createNativeVaultActionCoordinator } from "./native-vault-action-coordinator";

describe("native Vault action presentation", () => {
  it("keeps a newer Settings result when an older Enable action finishes later", () => {
    const coordinator = createNativeVaultActionCoordinator();
    const enable = coordinator.start();
    const settings = coordinator.start();
    expect(coordinator.isCurrent(settings)).toBe(true);
    expect(coordinator.isCurrent(enable)).toBe(false);
  });
});
