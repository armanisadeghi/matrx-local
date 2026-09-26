/** @vitest-environment jsdom */
/**
 * THE organization selector (Arman, 2026-09-21): one control, at the top,
 * that SHOWS the true value and CHANGES the only state + stored value.
 *
 * These tests drive the real store (`lib/org/active-org`) behind the real
 * component, with only the network (Supabase, the engine) and the session
 * faked — a switcher that kept its own copy of the organization, or one that
 * showed a name it was not actually using, fails here.
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  rpc: vi.fn(),
  from: vi.fn(),
  getAuthedSession: vi.fn(),
  currentSession: vi.fn(),
  enginePut: vi.fn(),
  engineDelete: vi.fn(),
}));

vi.mock("@/lib/supabase", () => ({
  default: { rpc: mocks.rpc, schema: vi.fn(() => ({ from: mocks.from })) },
}));
vi.mock("@/lib/custodian", () => ({
  getAuthedSession: mocks.getAuthedSession,
  currentSession: mocks.currentSession,
}));
vi.mock("@/lib/api", () => ({ engine: { put: mocks.enginePut, delete: mocks.engineDelete } }));
vi.mock("@/lib/app-config", () => ({
  getAppRuntimeConfig: () => ({ webAppOrigin: "https://web.example.test" }),
}));
vi.mock("@/lib/open-external", () => ({ openExternal: vi.fn() }));
// The design system's menu is a portal + pointer-event machine; the test is
// about the STATE the switcher shows and writes, so render its pieces flat.
vi.mock("@ai-matrx/design-system", () => {
  const Pass = ({ children }: { children?: ReactNode }) => <>{children}</>;
  const Div = ({ children, ...props }: HTMLAttributes<HTMLDivElement>) => (
    <div {...props}>{children}</div>
  );
  return {
    Button: ({ children, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) => (
      <button {...props}>{children}</button>
    ),
    DropdownMenu: Pass,
    DropdownMenuTrigger: Pass,
    DropdownMenuContent: Div,
    DropdownMenuLabel: Div,
    DropdownMenuSeparator: () => <hr />,
    DropdownMenuItem: ({
      children,
      onSelect,
      disabled,
      ...props
    }: HTMLAttributes<HTMLDivElement> & {
      onSelect?: (event: Event) => void;
      disabled?: boolean;
    }) => (
      <div
        role="menuitem"
        aria-disabled={disabled}
        onClick={(event) => {
          if (!disabled) onSelect?.(event.nativeEvent);
        }}
        {...props}
      >
        {children}
      </div>
    ),
    Tooltip: Pass,
    TooltipTrigger: Pass,
    TooltipContent: () => null,
  };
});

import { getActiveOrganizationId } from "@/lib/org/active-org";
import { OrganizationSwitcher } from "./OrganizationSwitcher";

const STORAGE_KEY = "matrx-local.active-organization.v2";

class MemoryStorage {
  private values = new Map<string, string>();
  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
  removeItem(key: string): void {
    this.values.delete(key);
  }
  clear(): void {
    this.values.clear();
  }
}
const storage = new MemoryStorage();

let container: HTMLDivElement;
let root: Root;
// The store is module-level state that recomputes per signed-in user; a
// distinct user per test is what keeps one test's pick out of the next.
let testUser = "";
let testNo = 0;

function mockMemberships(orgs: Array<{ id: string; name: string }>) {
  mocks.rpc.mockResolvedValue({
    data: orgs.map((o) => ({ container_id: o.id })),
    error: null,
  });
  const inFn = vi.fn().mockResolvedValue({ data: orgs, error: null });
  const select = vi.fn().mockReturnValue({ in: inFn });
  mocks.from.mockImplementation(() => ({ select }));
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  (globalThis as unknown as { localStorage: MemoryStorage }).localStorage = storage;
  storage.clear();
  testNo += 1;
  testUser = `user-${testNo}`;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  mocks.rpc.mockReset();
  mocks.from.mockReset();
  mocks.enginePut.mockReset().mockResolvedValue({ organization_id: null });
  mocks.engineDelete.mockReset().mockResolvedValue({ organization_id: null });
  mocks.getAuthedSession.mockReset().mockResolvedValue({ user: { id: testUser } });
  mocks.currentSession.mockReset().mockReturnValue({ signed_in: true, user_id: testUser });
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function mount() {
  await act(async () => {
    root.render(<OrganizationSwitcher />);
  });
  // Let the membership load settle.
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function trigger(): HTMLButtonElement {
  return container.querySelector('[data-testid="organization-switcher"]') as HTMLButtonElement;
}

describe("OrganizationSwitcher", () => {
  it("shows the TRUE value: 'Choose organization' in attention colors when nothing is set, the name when one is", async () => {
    mockMemberships([
      { id: "org-1", name: "First Org" },
      { id: "org-2", name: "Second Org" },
    ]);
    await mount();

    expect(trigger().textContent).toContain("Choose organization");
    expect(trigger().className).toContain("amber");
    expect(trigger().dataset.organizationId).toBe("");

    // The list is the user's real memberships — nothing pre-selected.
    const items = [...container.querySelectorAll('[role="menuitem"][data-organization-id]')];
    expect(items.map((el) => el.textContent)).toEqual(["First Org", "Second Org"]);
  });

  it("changes THE ONE state and stored value, and the engine mirror, from one click", async () => {
    mockMemberships([
      { id: "org-1", name: "First Org" },
      { id: "org-2", name: "Second Org" },
    ]);
    await mount();

    const second = container.querySelector(
      '[role="menuitem"][data-organization-id="org-2"]',
    ) as HTMLElement;
    await act(async () => {
      second.click();
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    // The control shows what it just set.
    expect(trigger().textContent).toContain("Second Org");
    expect(trigger().className).not.toContain("amber");
    expect(trigger().dataset.organizationId).toBe("org-2");

    // The ONE stored value, scoped to this user.
    const stored = JSON.parse(storage.getItem(STORAGE_KEY) ?? "{}") as {
      users: Record<string, { id: string }>;
    };
    expect(stored.users[testUser]?.id).toBe("org-2");

    // The engine mirror got the same value.
    expect(mocks.enginePut).toHaveBeenCalledWith("/organization/active", {
      organization_id: "org-2",
    });

    // And the request boundary reads the same value — one state, not two.
    expect(await getActiveOrganizationId()).toBe("org-2");
  });

  it("renders the stored organization for the signed-in user on mount", async () => {
    storage.setItem(
      STORAGE_KEY,
      JSON.stringify({ users: { [testUser]: { id: "org-1", name: "First Org" } } }),
    );
    mockMemberships([
      { id: "org-1", name: "First Org" },
      { id: "org-2", name: "Second Org" },
    ]);
    await mount();
    expect(trigger().textContent).toContain("First Org");
    expect(trigger().dataset.organizationId).toBe("org-1");
  });

  it("renders nothing when signed out — never a control that lies", async () => {
    mocks.currentSession.mockReturnValue({ signed_in: false, user_id: null });
    await mount();
    expect(trigger()).toBeNull();
  });
});
