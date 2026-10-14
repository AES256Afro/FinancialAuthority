// The sign-in-at-the-vendor flow through Plaid Link, shared by the bank and the investment screens.
// The vendor's own page opens (its OAuth where it offers one); the app receives a read token and never a password.
import { api } from "./api";

const scripts: Record<string, Promise<void>> = {};
export function loadScript(src: string): Promise<void> {
  if (!scripts[src]) {
    scripts[src] = new Promise((resolve, reject) => {
      const el = document.createElement("script");
      el.src = src;
      el.async = true;
      el.onload = () => resolve();
      el.onerror = () => reject(new Error(`Could not load ${src}`));
      document.head.appendChild(el);
    });
  }
  return scripts[src];
}

type PlaidGlobal = { create: (opts: { token: string; onSuccess: (publicToken: string, metadata: { institution?: { name?: string } }) => void; onExit?: (err: unknown) => void }) => { open: () => void } };

export interface LinkResult {
  link: { id: number; institution: string };
  sync: { summary: string };
}

/** Open Plaid Link for a new bank (`purpose: "bank"`) or a retirement or investment vendor (`purpose: "investments"`),
 * or sign in again on an existing link. Resolves with what the server said once the vendor hands the token back. */
export async function openPlaidLink(opts: { purpose?: "bank" | "investments"; linkId?: number; onDone: (message: string) => void; onExit?: () => void }): Promise<void> {
  const made = await api<{ link_token: string }>("/api/v1/banks/plaid/link_token", { method: "POST", body: JSON.stringify({ link_id: opts.linkId ?? null, purpose: opts.purpose ?? "bank" }) });
  await loadScript("https://cdn.plaid.com/link/v2/stable/link-initialize.js");
  const Plaid = (window as unknown as { Plaid?: PlaidGlobal }).Plaid;
  if (!Plaid) throw new Error("Plaid Link did not load");
  const handler = Plaid.create({
    token: made.link_token,
    onSuccess: async (publicToken, metadata) => {
      try {
        if (opts.linkId) {
          const r = await api<{ summary: string }>(`/api/v1/banks/${opts.linkId}/sync`, { method: "POST" });
          opts.onDone(`Signed in again. ${r.summary}`);
        } else {
          const r = await api<LinkResult>("/api/v1/banks/plaid/exchange", { method: "POST", body: JSON.stringify({ public_token: publicToken, institution: metadata.institution?.name ?? "" }) });
          opts.onDone(`${r.link.institution || "The vendor"} is linked. ${r.sync.summary}`);
        }
      } catch (e) {
        opts.onDone((e as Error).message);
      }
    },
    onExit: () => opts.onExit?.(),
  });
  handler.open();
}
