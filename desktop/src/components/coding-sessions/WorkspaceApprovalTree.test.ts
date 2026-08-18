import { describe, expect, it } from "vitest";

import { hasProject, isWithin } from "./WorkspaceApprovalTree";
import type { WorkspaceDiscoveryNode } from "@/lib/api";

function node(
  path: string,
  kind: WorkspaceDiscoveryNode["kind"] = "directory",
  children: WorkspaceDiscoveryNode[] = [],
): WorkspaceDiscoveryNode {
  const parts = path.split("/");
  return {
    path,
    name: parts[parts.length - 1] || path,
    kind,
    project_kinds: kind === "directory" ? [] : ["git"],
    children,
    truncated: false,
  };
}

describe("workspace approval tree", () => {
  it("recognizes exact and inherited approval without matching sibling prefixes", () => {
    expect(isWithin("/code", "/code")).toBe(true);
    expect(isWithin("/code", "/code/matrx-local")).toBe(true);
    expect(isWithin("/code", "/code-other/repo")).toBe(false);
  });

  it("keeps project ancestry and prunes empty directory branches", () => {
    const project = node("/code/team/app", "git_repository");
    const team = node("/code/team", "directory", [project]);
    expect(hasProject(team)).toBe(true);
    expect(hasProject(node("/code/archive"))).toBe(false);
  });
});
