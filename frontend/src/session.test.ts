import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CHAT_KEY, PLAID_PENDING_KEY, clearDeviceState } from "./session";

// A stand-in for the browser's storage: the tests run under node.
function fakeStorage() {
  const map = new Map<string, string>();
  return {
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
    keys: () => [...map.keys()],
  };
}

describe("clearDeviceState", () => {
  let store: ReturnType<typeof fakeStorage>;
  beforeEach(() => {
    store = fakeStorage();
    vi.stubGlobal("localStorage", store);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("forgets the chat thread and a pending Plaid token, and keeps preferences", () => {
    store.setItem(CHAT_KEY, JSON.stringify([{ role: "user", content: "What is safe to spend?" }]));
    store.setItem(PLAID_PENDING_KEY, JSON.stringify({ token: "link-sandbox-x", purpose: "bank" }));
    store.setItem("fa.prefs.v1", JSON.stringify({ view: "river", look: "dark" }));
    store.setItem("fa.showas.v1", JSON.stringify({ paycheck: "beads" }));
    clearDeviceState();
    expect(store.getItem(CHAT_KEY)).toBeNull();
    expect(store.getItem(PLAID_PENDING_KEY)).toBeNull();
    expect(store.keys().sort()).toEqual(["fa.prefs.v1", "fa.showas.v1"]);
  });

  it("does not throw when storage is blocked", () => {
    vi.stubGlobal("localStorage", {
      removeItem: () => {
        throw new Error("blocked");
      },
    });
    expect(() => clearDeviceState()).not.toThrow();
  });
});
