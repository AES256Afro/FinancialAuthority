import { describe, expect, it } from "vitest";
import { exitMessage } from "./plaid";

describe("exitMessage", () => {
  it("says nothing when the owner closed Link", () => {
    expect(exitMessage(null, "Example Bank", "bank")).toBeNull();
    expect(exitMessage({}, "Example Bank", "bank")).toBeNull();
  });

  it("explains a bank that shared no account", () => {
    const said = exitMessage({ error_code: "NO_ACCOUNTS", display_message: "No valid accounts were found." }, "Example Bank", "bank");
    expect(said).toContain("Example Bank");
    expect(said).toContain("tick the checking account and the card");
  });

  it("points an investments link at a bank to the bank button", () => {
    expect(exitMessage({ error_code: "NO_ACCOUNTS" }, "Example Bank", "investments")).toContain("Connect a bank through Plaid");
  });

  it("keeps Plaid's own words for any other error", () => {
    expect(exitMessage({ error_code: "INSTITUTION_DOWN", display_message: "The bank is down." }, undefined, "bank")).toBe("Plaid stopped: The bank is down. (INSTITUTION_DOWN).");
  });
});
