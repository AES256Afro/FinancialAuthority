// What this browser keeps about a signed-in person, and the one place it is all forgotten: when they sign out, and
// when the sign-in page is shown because the session is gone. Preferences (the look, the home view, which picture a
// section shows) are not about the person and stay.

/** The chat thread, kept so a reload does not lose it. It holds the advisor's words about the person's money. */
export const CHAT_KEY = "fa.chat.v1";
/** A Plaid Link token kept while an OAuth bank sends the browser away and back. */
export const PLAID_PENDING_KEY = "fa.plaid.pending";

const PERSONAL_KEYS = [CHAT_KEY, PLAID_PENDING_KEY];

/** Forget everything about the person that this browser kept. Safe to call when storage is absent or blocked. */
export function clearDeviceState(): void {
  for (const key of PERSONAL_KEYS) {
    try {
      localStorage.removeItem(key);
    } catch {
      // storage may be unavailable; there is then nothing to clear
    }
  }
}
