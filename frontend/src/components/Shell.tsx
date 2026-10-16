// The app shell: a rail on wide screens, tabs on a phone, and the header with the payday chip.
import { ArrowLeftRight, BarChart3, BellDot, BookOpen, Clock, Download, Eye, EyeOff, FileText, Gauge, Landmark, LayoutGrid, List, Mail, MessageSquare, Mountain, PiggyBank, Receipt, Repeat, Settings, Telescope, TrendingUp, Truck, Waves, type LucideIcon } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { brandMark, brandName, saveAppearance, useAppearance } from "../appearance";
import { DEMO } from "../demo/shim";
import { NextStepsStrip } from "./NextSteps";
import { daysWord, longDate, shortDate } from "../format";
import type { Snapshot } from "../types";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  group: "daily" | "plan" | "records" | "foot";
  hint: string;
  end?: boolean;
  phone?: boolean;
  rail?: boolean;
}

// Three groups: what is due, what to plan, and the records. Settings is kept apart at the rail's foot. A phone
// shows the daily four and More, which lists everything.
export const NAV_ITEMS: NavItem[] = [
  { to: "/today", label: "Today", icon: Clock, group: "daily", hint: "Safe to spend, this morning's brief and the next steps." },
  { to: "/", label: "Home", icon: Waves, end: true, group: "daily", hint: "Your home view: the picture you chose in Appearance." },
  { to: "/bills", label: "Bills", icon: Receipt, group: "daily", hint: "Every bill and subscription, its due day and who pays it." },
  { to: "/confirm", label: "Needs you", icon: BellDot, group: "daily", hint: "What waits for your answer before it counts." },
  { to: "/chat", label: "Chat", icon: MessageSquare, group: "daily", phone: false, hint: "Ask about your plan in your own words." },
  { to: "/cover", label: "Cover", icon: Gauge, group: "plan", phone: false, hint: "Before each payday: what every account needs against what it holds." },
  { to: "/saving", label: "Saving", icon: PiggyBank, group: "plan", phone: false, hint: "The saving ladder and how much of each check to keep." },
  { to: "/debts", label: "Debts", icon: Mountain, group: "plan", phone: false, hint: "What you owe, what it costs, and the order to pay it down." },
  { to: "/borrowing", label: "Borrowing", icon: Repeat, group: "plan", phone: false, hint: "Advance apps and pay-later plans, and their fees." },
  { to: "/move", label: "Moving home", icon: Truck, group: "plan", phone: false, hint: "Plan a move: the costs, the dates and whether the money holds." },
  { to: "/switch", label: "Switch banks", icon: ArrowLeftRight, group: "plan", phone: false, hint: "Move your paycheck and bills from one bank to another, step by step." },
  { to: "/outlook", label: "Outlook", icon: Telescope, group: "plan", phone: false, hint: "Savings and investments over the years ahead." },
  { to: "/accounts", label: "Accounts", icon: Landmark, group: "records", phone: false, hint: "Every account and its balance." },
  { to: "/investments", label: "Investing", icon: TrendingUp, group: "records", phone: false, hint: "Holdings in your investment accounts." },
  { to: "/transactions", label: "Transactions", icon: List, group: "records", phone: false, hint: "Search everything that went in or out." },
  { to: "/paystubs", label: "Paystubs", icon: FileText, group: "records", phone: false, hint: "Your paycheck, gross to net, and each stub." },
  { to: "/mail", label: "Mail", icon: Mail, group: "records", phone: false, hint: "Messages from banks and billers, and what the app took from them." },
  { to: "/reports", label: "Reports", icon: BarChart3, group: "records", phone: false, hint: "Spending by month and by merchant." },
  { to: "/exports", label: "Exports", icon: Download, group: "records", phone: false, hint: "Your data as files, and the calendar feed." },
  { to: "/more", label: "More", icon: LayoutGrid, group: "foot", rail: false, hint: "Every section." },
  { to: "/settings", label: "Settings", icon: Settings, group: "foot", phone: false, hint: "Setting up, appearance, connections, backups and more." },
];
export const NAV_GROUPS: { id: NavItem["group"]; label: string }[] = [{ id: "daily", label: "Every day" }, { id: "plan", label: "Plan" }, { id: "records", label: "Records" }];

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
    <div role="status" style={{ position: "sticky", top: 0, zIndex: 20, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", padding: "6px 16px", fontSize: 12, fontWeight: 700, background: "var(--warning)", color: "#141518" }}>
      <span>Demo · a synthetic household · nothing here is real · changes are not kept</span>
      {flash && <span style={{ fontWeight: 500 }}>· {flash}</span>}
    </div>
  );
}

/** The page's name in the browser tab and the window list, from the section it belongs to. */
function usePageTitle(brand: string) {
  const { pathname } = useLocation();
  useEffect(() => {
    const match = [...NAV_ITEMS].filter((n) => (n.end ? pathname === n.to : pathname === n.to || pathname.startsWith(n.to + "/"))).sort((a, b) => b.to.length - a.to.length)[0];
    const extra: Record<string, string> = { "/glossary": "Words", "/checkin": "Check-in", "/welcome": "Welcome", "/settings/setup": "Setting up", "/settings/appearance": "Appearance", "/settings/connectors": "Connections", "/settings/models": "Models", "/settings/clients": "Apps and keys", "/settings/notifications": "Notifications", "/settings/backups": "Backups", "/settings/jobs": "Jobs", "/settings/audit": "Activity" };
    const page = extra[pathname] ?? match?.label;
    document.title = page ? `${page} · ${brandName()}` : brandName();
  }, [pathname, brand]);
}

export function Shell({ children, snapshot }: { children: ReactNode; snapshot: Snapshot | null; title?: string }) {
  const look = useAppearance();
  usePageTitle(look.brand.name);
  const [quietNote, setQuietNote] = useState<string | null>(null);
  async function toggleQuiet() {
    try {
      await saveAppearance({ ...look, quiet: !look.quiet });
      setQuietNote(null);
    } catch (e) {
      setQuietNote((e as Error).message);
    }
  }
  const today = snapshot?.as_of ?? new Date().toISOString().slice(0, 10);
  const pending = snapshot ? snapshot.pending.confirmations + snapshot.pending.approvals : 0;
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
    {DEMO && <DemoBanner />}
    <div style={{ flexGrow: 1, display: "flex", flexWrap: "wrap" }}>
      <nav className="rail hidden md:flex" aria-label="Sections" style={{ flex: "0 0 76px", flexDirection: "column", boxSizing: "border-box", borderRight: "1px solid var(--hairline)", position: "sticky", top: DEMO ? 30 : 0, height: `calc(100dvh / var(--zoom) - ${DEMO ? 30 : 0}px)`, alignSelf: "flex-start" }}>
        <div style={{ flex: "0 0 auto", display: "flex", alignItems: "center", justifyContent: "center", height: 36, margin: "14px 0 10px" }}>
          <img src={brandMark()} width={28} height={28} alt={brandName()} style={{ objectFit: "contain", borderRadius: 6 }} />
        </div>
        <div className="rail-list">
          {NAV_GROUPS.map((g) => (
            <div key={g.id} className="rail-group" role="group" aria-label={g.label}>
              {NAV_ITEMS.filter((n) => n.group === g.id).map((n) => item(n, 20))}
            </div>
          ))}
        </div>
        <div className="rail-foot">{NAV_ITEMS.filter((n) => n.group === "foot" && n.rail !== false).map((n) => item(n, 20))}</div>
      </nav>
      <main style={{ flex: "999 1 320px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16, boxSizing: "border-box" }} className="px-4 pt-3.5 pb-24 md:px-6 md:pb-6">
        <header style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12, minHeight: 40 }}>
          <div style={{ fontSize: 13, color: "var(--ink-2)", fontWeight: 600 }}>{longDate(today)}</div>
          {snapshot && (
            <div style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 13, fontWeight: 700, padding: "6px 12px", borderRadius: 999, background: "var(--paycheck-soft)", color: "var(--paycheck-ink)" }}>
              <span className="sw" style={{ background: "var(--paycheck)", margin: 0 }} />
              {snapshot.next_paycheck.known === false ? <>No payday on file · <Link to="/paystubs#paycheck" style={{ color: "inherit", textDecoration: "underline" }}>Add your paycheck</Link></> : <>Payday {daysWord(snapshot.next_paycheck.days)} · {shortDate(snapshot.next_paycheck.date)}</>}
            </div>
          )}
          <div style={{ flexGrow: 1 }} />
          {snapshot && <Link to="/accounts" style={{ fontSize: 12, color: "var(--ink-2)" }}>{snapshot.accounts.length} account{snapshot.accounts.length === 1 ? "" : "s"}</Link>}
          <button type="button" onClick={toggleQuiet} aria-pressed={look.quiet} title={look.quiet ? "Show every amount" : "Hide every amount: each one shows when tapped"} style={{ display: "inline-flex", alignItems: "center", gap: 4, minHeight: 32, padding: "0 6px", background: "none", border: 0, font: "inherit", fontSize: 12, color: "var(--ink-2)", cursor: "pointer" }}>
            {look.quiet ? <Eye size={15} aria-hidden="true" /> : <EyeOff size={15} aria-hidden="true" />}{look.quiet ? "Show amounts" : "Hide amounts"}
          </button>
          {quietNote && <span role="alert" style={{ fontSize: 12, color: "var(--critical-ink)" }}>{quietNote}</span>}
          <Link to="/glossary" title="The words the app uses" style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12, color: "var(--ink-2)" }}><BookOpen size={15} aria-hidden="true" />Words</Link>
        </header>
        <NextStepsStrip />
        {children}
      </main>
      <nav className="tabs fixed left-0 right-0 bottom-0 flex justify-around items-start pt-2 md:hidden" aria-label="Tabs" style={{ minHeight: 72, paddingBottom: "env(safe-area-inset-bottom)", background: "var(--surface)", borderTop: "1px solid var(--hairline)" }}>
        {NAV_ITEMS.filter((n) => n.phone !== false).map((n) => item(n, 22))}
      </nav>
    </div>
    </div>
  );
}
