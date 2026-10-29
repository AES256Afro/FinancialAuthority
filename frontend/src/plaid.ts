// The sign-in-at-the-vendor flow through Plaid Link, shared by the bank and the investment screens.
// The vendor's own page opens (its OAuth where it offers one); the app receives a read token and never a password.
// An OAuth bank sends the browser back to the Connectors page; the flow resumes there with the same token.
import { api } from "./api";
import { PLAID_PENDING_KEY } from "./session";

const scripts: Record<string, Promise<void>> = {};
export function loadScript(src: string): Promise<void> {
  if (!scripts[src]) {
    scripts[src] = new Promise((resolve, reject) => {
      const el = document.createElement("script");
      el.src = src;
      el.async = true;
      el.onload = () => resolve();
      el.onerror = () => {
        // Not kept: a press after the connection comes back tries again.
        delete scripts[src];
        el.remove();
        reject(new Error(`Could not reach ${new URL(src).hostname} to open its sign-in window. Check this device is online (an ad or tracker blocker can stop it too), then try again.`));
      };
      document.head.appendChild(el);
    });
  }
  return scripts[src];
}

type PlaidExitError = { error_code?: string; error_message?: string; display_message?: string | null } | null;
type PlaidGlobal = { create: (opts: { token: string; receivedRedirectUri?: string; onSuccess: (publicToken: string, metadata: { institution?: { name?: string } }) => void; onExit?: (err: PlaidExitError, metadata?: { institution?: { name?: string } | null }) => void }) => { open: () => void } };

/** What to tell the owner when Link closes on an error; null when they closed it themselves. Plaid shows its
 * own words inside Link, which are gone once it closes, so the page keeps them with what to do next. */
export function exitMessage(err: PlaidExitError, institution: string | undefined, purpose: "bank" | "investments"): string | null {
  if (!err || !err.error_code) return null;
  const bank = institution || "The bank";
  if (err.error_code === "NO_ACCOUNTS") {
    return purpose === "investments"
      ? `${bank} has no retirement or investment account Plaid can read (Plaid: NO_ACCOUNTS). To link a checking account or a card there, use Connect a bank through Plaid instead.`
      : `${bank} signed you in but shared no account Plaid can read (Plaid: NO_ACCOUNTS). On ${bank}'s own page, tick the checking account and the card before you continue, then connect again. A bank that signs in on its own site also needs this app's redirect address registered in Plaid; the next steps say so when it is not.`;
  }
  return `Plaid stopped: ${err.display_message || err.error_message || "no reason given"} (${err.error_code}).`;
}

export interface LinkResult {
  link: { id: number; institution: string };
  sync: { summary: string };
  /** The same sign-in linked a second time: the server undid the new link and says why. */
  duplicate?: boolean;
  said?: string;
}

interface Pending {
  token: string;
  purpose: "bank" | "investments";
  linkId?: number;
}

const PENDING = PLAID_PENDING_KEY;

function remember(p: Pending) {
  try {
    localStorage.setItem(PENDING, JSON.stringify(p));
  } catch {
    // storage may be unavailable; the flow still works for banks without OAuth
  }
}

function recall(): Pending | null {
  try {
    const raw = localStorage.getItem(PENDING);
    return raw ? (JSON.parse(raw) as Pending) : null;
  } catch {
    return null;
  }
}

function forget() {
  try {
    localStorage.removeItem(PENDING);
  } catch {
    // ignore
  }
}

async function finish(publicToken: string, institution: string, linkId: number | undefined, onDone: (message: string) => void) {
  try {
    if (linkId) {
      const r = await api<{ summary: string }>(`/api/v1/banks/${linkId}/sync`, { method: "POST" });
      onDone(`Signed in again. ${r.summary}`);
    } else {
      const r = await api<LinkResult>("/api/v1/banks/plaid/exchange", { method: "POST", body: JSON.stringify({ public_token: publicToken, institution }) });
      onDone(r.duplicate && r.said ? r.said : `${r.link.institution || "The vendor"} is linked. ${r.sync.summary}`);
    }
  } catch (e) {
    onDone((e as Error).message);
  } finally {
    forget();
  }
}

async function plaidGlobal(): Promise<PlaidGlobal> {
  await loadScript("https://cdn.plaid.com/link/v2/stable/link-initialize.js");
  const Plaid = (window as unknown as { Plaid?: PlaidGlobal }).Plaid;
  if (!Plaid) throw new Error("Plaid Link did not load");
  return Plaid;
}

/** Open Plaid Link for a new bank (`purpose: "bank"`) or a retirement or investment vendor (`purpose: "investments"`),
 * or sign in again on an existing link. `onNote` gets anything the server wants said before Link opens. */
export async function openPlaidLink(opts: { purpose?: "bank" | "investments"; linkId?: number; onDone: (message: string) => void; onExit?: (message: string | null) => void; onNote?: (message: string) => void }): Promise<void> {
  const made = await api<{ link_token: string; oauth_redirect?: { ok: boolean; uri: string | null; note?: string } }>("/api/v1/banks/plaid/link_token", { method: "POST", body: JSON.stringify({ link_id: opts.linkId ?? null, purpose: opts.purpose ?? "bank" }) });
  if (made.oauth_redirect && !made.oauth_redirect.ok && made.oauth_redirect.note) opts.onNote?.(made.oauth_redirect.note);
  const Plaid = await plaidGlobal();
  remember({ token: made.link_token, purpose: opts.purpose ?? "bank", linkId: opts.linkId });
  const handler = Plaid.create({
    token: made.link_token,
    onSuccess: (publicToken, metadata) => finish(publicToken, metadata.institution?.name ?? "", opts.linkId, opts.onDone),
    onExit: (err, metadata) => {
      forget();
      opts.onExit?.(exitMessage(err, metadata?.institution?.name, opts.purpose ?? "bank"));
    },
  });
  handler.open();
}

/** When an OAuth bank has sent the browser back here (the address carries `oauth_state_id`), reopen Link with
 * the token the flow started with so Plaid can finish it. Returns false when there is nothing to resume. */
export async function resumePlaidLink(opts: { onDone: (message: string) => void; onExit?: (message: string | null) => void }): Promise<boolean> {
  if (!new URLSearchParams(window.location.search).has("oauth_state_id")) return false;
  const pending = recall();
  if (!pending) return false;
  const Plaid = await plaidGlobal();
  const handler = Plaid.create({
    token: pending.token,
    receivedRedirectUri: window.location.href,
    onSuccess: (publicToken, metadata) => finish(publicToken, metadata.institution?.name ?? "", pending.linkId, opts.onDone),
    onExit: (err, metadata) => {
      forget();
      opts.onExit?.(exitMessage(err, metadata?.institution?.name, pending.purpose));
    },
  });
  handler.open();
  return true;
}
