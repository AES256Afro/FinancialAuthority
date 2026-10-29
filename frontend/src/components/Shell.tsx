// The app shell: a rail on wide screens, tabs on a phone, and the header with the payday chip and Sign out. A second
// person who sees the shared accounts only gets a shell of their own: Shared money, their sign-in, and Sign out;
// nothing here asks the server for what that sign-in cannot reach.
import { ArrowLeftRight, BarChart3, BellDot, BookOpen, Briefcase, Clock, Download, Eye, EyeOff, FileText, FolderOpen, Gauge, History, Home, KeyRound, Landmark, LayoutGrid, List, LogOut, Mail, MessageSquare, Mountain, PieChart, PiggyBank, Receipt, Repeat, Scale, Settings, Telescope, TrendingUp, Truck, UserRound, Wallet, Waves, type LucideIcon } from "lucide-react";
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { api, useApi } from "../api";
import { appearance, brandMark, brandName, saveAppearance, useAppearance } from "../appearance";
import { DEMO } from "../demo/shim";
import { clearDeviceState } from "../session";
import { NextStepsStrip } from "./NextSteps";
import { daysWord, localToday, longDate, shortDate } from "../format";
import type { Snapshot } from "../types";
import { roleOf, type MePerson } from "../types-people";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  group: "daily" | "plan" | "records" | "foot";
  hint: string;
  end?: boolean;
  phone?: boolean;
  rail?: boolean;
  /** In the rail only once someone else signs in too (More lists it always). */
  people?: boolean;
}

// Three groups: what is due, what to plan, and the records. Settings is kept apart at the rail's foot. A phone
// shows the daily four and More, which lists everything.
export const NAV_ITEMS: NavItem[] = [
  { to: "/today", label: "Today", icon: Clock, group: "daily", hint: "Safe to spend, this morning's brief and the next steps." },
  { to: "/", label: "Home", icon: Waves, end: true, group: "daily", hint: "Your home view: the picture you chose in Appearance." },
  { to: "/bills", label: "Bills", icon: Receipt, group: "daily", hint: "Every bill and subscription, its due day and who pays it." },
  { to: "/confirm", label: "Needs you", icon: BellDot, group: "daily", hint: "What waits for your answer before it counts." },
  { to: "/chat", label: "Chat", icon: MessageSquare, group: "daily", phone: false, hint: "Ask about your plan in your own words." },
  { to: "/budgets", label: "Budgets", icon: PieChart, group: "plan", phone: false, hint: "An amount for each kind of spending, for each paycheck, and what is left of it." },
  { to: "/recap", label: "How it went", icon: History, group: "plan", phone: false, hint: "Each paycheck's plan beside what really happened, and this week in one line." },
  { to: "/cover", label: "Cover", icon: Gauge, group: "plan", phone: false, hint: "Before each payday: what every account needs against what it holds." },
  { to: "/saving", label: "Saving", icon: PiggyBank, group: "plan", phone: false, hint: "The saving ladder and how much of each check to keep." },
  { to: "/debts", label: "Debts", icon: Mountain, group: "plan", phone: false, hint: "What you owe, what it costs, and the order to pay it down." },
  { to: "/borrowing", label: "Borrowing", icon: Repeat, group: "plan", phone: false, hint: "Advance apps and pay-later plans, and their fees." },
  { to: "/housing", label: "Housing", icon: Home, group: "plan", phone: false, hint: "Your rent or mortgage, its fees, your utilities and your lease, in one place." },
  { to: "/move", label: "Moving home", icon: Truck, group: "plan", phone: false, hint: "The cost to get into your next home, and whether the money holds." },
  { to: "/switch", label: "Switch banks", icon: ArrowLeftRight, group: "plan", phone: false, hint: "Move your paycheck and bills from one bank to another, step by step." },
  { to: "/outlook", label: "Outlook", icon: Telescope, group: "plan", phone: false, hint: "Savings and investments over the years ahead." },
  { to: "/business", label: "Business", icon: Briefcase, group: "plan", phone: false, hint: "Your business: setting it up, its money, invoices and what may be written off." },
  { to: "/taxes", label: "Taxes", icon: Scale, group: "plan", phone: false, hint: "The tax year as it goes: dates, deductions to check, and what never counts." },
  { to: "/accounts", label: "Accounts", icon: Landmark, group: "records", phone: false, hint: "Every account and its balance." },
  { to: "/investments", label: "Investing", icon: TrendingUp, group: "records", phone: false, hint: "Holdings in your investment accounts." },
  { to: "/transactions", label: "Transactions", icon: List, group: "records", phone: false, hint: "Search everything that went in or out." },
  { to: "/paystubs", label: "Paystubs", icon: FileText, group: "records", phone: false, hint: "Your paycheck, gross to net, and each stub." },
  { to: "/documents", label: "Documents", icon: FolderOpen, group: "records", phone: false, hint: "Receipts, statements, paystubs and your lease, by month, with return and warranty dates." },
  { to: "/mail", label: "Mail", icon: Mail, group: "records", phone: false, hint: "Messages from banks and billers, and what the app took from them." },
  { to: "/reports", label: "Reports", icon: BarChart3, group: "records", phone: false, hint: "Spending by month and by merchant." },
  { to: "/exports", label: "Exports", icon: Download, group: "records", phone: false, hint: "Your data as files, and the calendar feed." },
  { to: "/shared", label: "Shared money", icon: Wallet, group: "records", phone: false, people: true, hint: "The accounts you share with someone you invited: balances, the bills paid from them, and recent spending." },
  { to: "/more", label: "More", icon: LayoutGrid, group: "foot", rail: false, hint: "Every section." },
  { to: "/settings", label: "Settings", icon: Settings, group: "foot", phone: false, hint: "Setting up, appearance, connections, backups and more." },
];
export const NAV_GROUPS: { id: NavItem["group"]; label: string }[] = [{ id: "daily", label: "Every day" }, { id: "plan", label: "Plan" }, { id: "records", label: "Records" }];

// All a "shared accounts only" sign-in reaches: the shared page and its own sign-in settings.
export const SHARED_NAV_ITEMS: NavItem[] = [
  { to: "/shared", label: "Shared money", icon: Wallet, group: "daily", hint: "The shared accounts: balances, the bills paid from them, and recent spending." },
  { to: "/settings/security", label: "Your sign-in", icon: KeyRound, group: "foot", hint: "Your second step, passkeys and the devices signed in as you." },
];

// Sign out: ends this browser's session, then opens the sign-in page. Drawn like the rail and tab links around it.
const SIGN_OUT_LOOK: Record<"rail" | "tab" | "header", CSSProperties> = {
  rail: { display: "flex", flexDirection: "column", alignItems: "center", gap: 4, width: "100%", padding: "10px 4px", borderRadius: 10, background: "none", border: 0, font: "inherit", fontSize: 10, fontWeight: 700, letterSpacing: ".02em", color: "var(--ink-2)", cursor: "pointer" },
  tab: { display: "flex", flexDirection: "column", alignItems: "center", gap: 3, flex: "1 1 0", minWidth: 0, minHeight: 44, background: "none", border: 0, padding: 0, font: "inherit", fontSize: 10, fontWeight: 700, color: "var(--muted)", cursor: "pointer", lineHeight: 1.15 },
  header: { display: "inline-flex", alignItems: "center", gap: 4, minHeight: 32, padding: "0 6px", background: "none", border: 0, font: "inherit", fontSize: 12, color: "var(--ink-2)", cursor: "pointer" },
};

function SignOut({ look }: { look: "rail" | "tab" | "header" }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function go() {
    setBusy(true);
    setError(null);
    try {
      await api("/api/auth/logout", { method: "POST" });
      clearDeviceState();
      window.location.assign("/");
    } catch (e) {
      setError(`Not signed out: ${(e as Error).message}`);
      setBusy(false);
    }
  }
  const size = look === "header" ? 15 : look === "tab" ? 22 : 20;
  return (
    <>
      <button type="button" onClick={go} disabled={busy} style={SIGN_OUT_LOOK[look]}>
        <LogOut size={size} strokeWidth={1.8} aria-hidden="true" />
        {busy ? "Signing out…" : "Sign out"}
      </button>
      {error && <span role="alert" style={{ fontSize: 12, color: "var(--critical-ink)" }}>{error}</span>}
    </>
  );
}

function DemoBanner() {
  const [flash, setFlash] = useState<string | null>(null);
  useEffect(() => {
    const onFlash = (e: Event) => {
      setFlash((e as CustomEvent<string>).detail);
      window.setTimeout(() => setFlash(null), 3200);
    };
    window.addEventListener("fa-demo-flash", onFlash);
    return () => window.removeEventListener("fa-demo-flash", onFlash);
  }, []);
  return (
    <div role="status" className="demo-banner" style={{ position: "sticky", top: 0, zIndex: 20, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", padding: "6px 16px", fontSize: 12, fontWeight: 700, background: "var(--warning)", color: "#141518" }}>
      <span>Demo · a synthetic household · nothing here is real · changes are not kept</span>
      {flash && <span style={{ fontWeight: 500 }}>· {flash}</span>}
    </div>
  );
}

// One name per page: the menu label, the browser tab's title and the page's heading say the same words.
const PAGE_NAMES: Record<string, string> = { "/shared": "Shared money", "/settings/people": "People", "/glossary": "Words", "/checkin": "Weekly check-in", "/welcome": "Setup steps", "/settings/setup": "Setting up", "/settings/appearance": "Appearance", "/settings/connectors": "Connections", "/settings/models": "Models", "/settings/clients": "Apps and keys", "/settings/notifications": "Notifications", "/settings/backups": "Backups", "/settings/jobs": "Jobs", "/settings/audit": "Activity", "/settings/security": "Sign-in and security", "/settings/privacy": "Privacy", "/business/write-offs": "Write-offs", "/business/invoices": "Invoices", "/business/miles": "Miles", "/data-check": "Data check" };

/** The page's name in the browser tab and the window list, from the section it belongs to. A second person's
 * sign-in page is "Your sign-in", as their menu and the page say. */
function usePageTitle(brand: string, person: boolean) {
  const { pathname } = useLocation();
  useEffect(() => {
    const match = [...NAV_ITEMS].filter((n) => (n.end ? pathname === n.to : pathname === n.to || pathname.startsWith(n.to + "/"))).sort((a, b) => b.to.length - a.to.length)[0];
    const page = person && pathname === "/settings/security" ? "Your sign-in" : PAGE_NAMES[pathname] ?? match?.label;
    document.title = page ? `${page} · ${brandName()}` : brandName();
  }, [pathname, brand, person]);
}

export function Shell({ children, snapshot, me = null }: { children: ReactNode; snapshot: Snapshot | null; me?: MePerson | null; title?: string }) {
  const look = useAppearance();
  const role = roleOf(me);
  const limited = role === "shared";
  // Shared money joins the rail once someone else signs in: a person with everything, or an owner who invited one.
  const people = useApi<{ people: unknown[] }>(role === "owner" && me ? "/api/v1/people" : null);
  const hasPeople = role === "everything" || (people.data?.people.length ?? 0) > 0;
  const nav = limited ? SHARED_NAV_ITEMS : NAV_ITEMS.filter((n) => !n.people || hasPeople);
  usePageTitle(look.brand.name, role !== "owner");
  const [quietNote, setQuietNote] = useState<string | null>(null);
  async function toggleQuiet() {
    try {
      const kept = appearance(); // what is kept now, not this render's copy
      await saveAppearance({ ...kept, quiet: !kept.quiet });
      setQuietNote(null);
    } catch (e) {
      setQuietNote((e as Error).message);
    }
  }
  const today = snapshot?.as_of ?? localToday();
  // The snapshot counts the owner's own sends; a second person counts theirs from the approvals list.
  const own = useApi<{ pending: number }>(role === "everything" ? "/api/v1/approvals?limit=1" : null, { live: true });
  const approvalsMine = role === "everything" ? (own.data?.pending ?? 0) : (snapshot?.pending.approvals ?? 0);
  const pending = snapshot ? snapshot.pending.confirmations + approvalsMine : 0;
  const item = ({ to, label, icon: Icon, end }: NavItem, size: number) => {
    const badge = to === "/confirm" ? pending : 0;
    return (
      <NavLink key={to} to={to} end={end} className={({ isActive }) => (isActive ? "on" : "")} style={{ position: "relative" }}>
        <Icon size={size} strokeWidth={1.8} aria-hidden="true" />
        {label}
        {badge ? <><span aria-hidden="true" style={{ position: "absolute", top: 4, right: 8, minWidth: 16, height: 16, borderRadius: 8, background: "var(--warning)", color: "#141518", fontSize: 10, fontWeight: 800, display: "grid", placeItems: "center", padding: "0 4px" }}>{badge}</span><span className="sr-only">, {badge} waiting</span></> : null}
      </NavLink>
    );
  };
  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column", background: "var(--ground)" }}>
    {/* The first tab stop: past the rail's links straight to the page. Focus is moved by hand, so the hash router of
        the demo does not read "#main" as a page. */}
    <a href="#main" className="skip" onClick={(e) => { e.preventDefault(); document.getElementById("main")?.focus(); }}>Skip to the content</a>
    {DEMO && <DemoBanner />}
    {/* The blur that "Hide amounts" puts on figures drawn inside charts (tokens.css, svg .num). */}
    <svg width="0" height="0" aria-hidden="true" focusable="false" style={{ position: "absolute" }}>
      <filter id="fa-quiet-blur"><feGaussianBlur stdDeviation="4" /></filter>
    </svg>
    <div style={{ flexGrow: 1, display: "flex", flexWrap: "wrap" }}>
      <nav className="rail hidden md:flex" aria-label="Sections" style={{ flex: "0 0 76px", flexDirection: "column", boxSizing: "border-box", borderRight: "1px solid var(--hairline)", position: "sticky", top: DEMO ? 30 : 0, height: `calc(100dvh / var(--zoom) - ${DEMO ? 30 : 0}px)`, alignSelf: "flex-start" }}>
        <div style={{ flex: "0 0 auto", display: "flex", alignItems: "center", justifyContent: "center", height: 36, margin: "14px 0 10px" }}>
          <img src={brandMark()} width={28} height={28} alt={brandName()} style={{ objectFit: "contain", borderRadius: 6 }} />
        </div>
        <div className="rail-list">
          {NAV_GROUPS.map((g) => {
            const items = nav.filter((n) => n.group === g.id);
            return items.length ? (
              <div key={g.id} className="rail-group" role="group" aria-label={g.label}>
                {items.map((n) => item(n, 20))}
              </div>
            ) : null;
          })}
        </div>
        <div className="rail-foot">
          {nav.filter((n) => n.group === "foot" && n.rail !== false).map((n) => item(n, 20))}
          {limited && <SignOut look="rail" />}
        </div>
      </nav>
      <main id="main" tabIndex={-1} style={{ flex: "999 1 320px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16, boxSizing: "border-box", outline: "none" }} className="px-4 pt-3.5 pb-24 md:px-6 md:pb-6">
        <header style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12, minHeight: 40 }}>
          <div style={{ fontSize: 13, color: "var(--ink-2)", fontWeight: 600 }}>{longDate(today)}</div>
          {snapshot && (
            <div style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 13, fontWeight: 700, padding: "6px 12px", borderRadius: 999, background: "var(--paycheck-soft)", color: "var(--paycheck-ink)" }}>
              <span className="sw" style={{ background: "var(--paycheck)", margin: 0 }} />
              {snapshot.next_paycheck.known === false ? <>No payday on file · <Link to="/paystubs#paycheck" style={{ color: "inherit", textDecoration: "underline" }}>Add your paycheck</Link></> : <>Payday {daysWord(snapshot.next_paycheck.days)} · {shortDate(snapshot.next_paycheck.date)}</>}
            </div>
          )}
          <div style={{ flexGrow: 1 }} />
          {me && role !== "owner" && (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12, color: "var(--ink-2)" }}>
              <UserRound size={15} aria-hidden="true" />Signed in as <strong style={{ color: "var(--ink)" }}>{me.name}</strong>
            </span>
          )}
          {snapshot && <Link to="/accounts" style={{ display: "inline-flex", alignItems: "center", minHeight: 32, fontSize: 12, color: "var(--ink-2)" }}>{snapshot.accounts.length} account{snapshot.accounts.length === 1 ? "" : "s"}</Link>}
          {!limited && <button type="button" onClick={toggleQuiet} aria-pressed={look.quiet} title={look.quiet ? "Show the amounts again" : "Blur the amounts on screen; tap one to see it"} style={{ display: "inline-flex", alignItems: "center", gap: 4, minHeight: 32, padding: "0 6px", background: "none", border: 0, font: "inherit", fontSize: 12, color: "var(--ink-2)", cursor: "pointer" }}>
            {look.quiet ? <Eye size={15} aria-hidden="true" /> : <EyeOff size={15} aria-hidden="true" />}{look.quiet ? "Show amounts" : "Hide amounts"}
          </button>}
          {quietNote && <span role="alert" style={{ fontSize: 12, color: "var(--critical-ink)" }}>{quietNote}</span>}
          {!limited && <Link to="/glossary" title="The words the app uses" style={{ display: "inline-flex", alignItems: "center", gap: 4, minHeight: 32, fontSize: 12, color: "var(--ink-2)" }}><BookOpen size={15} aria-hidden="true" />Words</Link>}
          {/* Anyone signed in can end this browser's session here, the owner too (a borrowed or shared computer). */}
          {!limited && me?.auth_enabled && <SignOut look="header" />}
        </header>
        {!limited && <NextStepsStrip />}
        {children}
      </main>
      <nav className="tabs fixed left-0 right-0 bottom-0 flex justify-around items-start pt-2 md:hidden" aria-label="Tabs" style={{ minHeight: 72, paddingBottom: "env(safe-area-inset-bottom)", background: "var(--surface)", borderTop: "1px solid var(--hairline)" }}>
        {nav.filter((n) => n.phone !== false).map((n) => item(n, 22))}
        {limited && <SignOut look="tab" />}
      </nav>
    </div>
    </div>
  );
}
