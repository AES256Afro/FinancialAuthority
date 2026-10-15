// The app shell: a rail on wide screens, tabs on a phone, and the header with the payday chip.
import { BarChart3, BellDot, BookOpen, Clock, Download, FileText, Gauge, Landmark, List, Mail, MessageSquare, Mountain, PiggyBank, Receipt, Repeat, Settings, Telescope, TrendingUp, Truck, Waves } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { brandMark, brandName, useAppearance } from "../appearance";
import { DEMO } from "../demo/shim";
import { NextStepsStrip } from "./NextSteps";
import { daysWord, longDate, shortDate } from "../format";
import type { Snapshot } from "../types";


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

export function Shell({ children, snapshot, title }: { children: ReactNode; snapshot: Snapshot | null; title?: string }) {
  useAppearance();
  const today = snapshot?.as_of ?? new Date().toISOString().slice(0, 10);
  const pending = snapshot ? snapshot.pending.confirmations + snapshot.pending.approvals : 0;
  const NAV = [
    { to: "/today", label: "Today", icon: Clock },
    { to: "/", label: "Home", icon: Waves, end: true },
    { to: "/bills", label: "Bills", icon: Receipt },
    { to: "/cover", label: "Cover", icon: Gauge, phone: false },
    { to: "/saving", label: "Saving", icon: PiggyBank, phone: false },
    { to: "/confirm", label: "Needs you", icon: BellDot, badge: pending },
    { to: "/chat", label: "Chat", icon: MessageSquare },
    { to: "/paystubs", label: "Paystubs", icon: FileText, phone: false },
    { to: "/debts", label: "Debts", icon: Mountain, phone: false },
    { to: "/borrowing", label: "Borrowing", icon: Repeat, phone: false },
    { to: "/move", label: "Move", icon: Truck, phone: false },
    { to: "/accounts", label: "Accounts", icon: Landmark, phone: false },
    { to: "/investments", label: "Investing", icon: TrendingUp, phone: false },
    { to: "/outlook", label: "Outlook", icon: Telescope, phone: false },
    { to: "/reports", label: "Reports", icon: BarChart3, phone: false },
    { to: "/transactions", label: "Ledger", icon: List, phone: false },
    { to: "/mail", label: "Mail", icon: Mail, phone: false },
    { to: "/exports", label: "Exports", icon: Download, phone: false },
    { to: "/settings", label: "Settings", icon: Settings },
  ];
  const item = ({ to, label, icon: Icon, end, badge }: (typeof NAV)[number], size: number) => (
    <NavLink key={to} to={to} end={end} className={({ isActive }) => (isActive ? "on" : "")} style={{ position: "relative" }}>
      <Icon size={size} strokeWidth={1.8} aria-hidden="true" />
      {label}
      {badge ? <span aria-label={`${badge} waiting`} style={{ position: "absolute", top: 4, right: 8, minWidth: 16, height: 16, borderRadius: 8, background: "var(--warning)", color: "#141518", fontSize: 10, fontWeight: 800, display: "grid", placeItems: "center", padding: "0 4px" }}>{badge}</span> : null}
    </NavLink>
  );
  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column", background: "var(--ground)" }}>
    {DEMO && <DemoBanner />}
    <div style={{ flexGrow: 1, display: "flex", flexWrap: "wrap" }}>
      <nav className="rail hidden md:flex" aria-label="Sections" style={{ flex: "0 0 76px", flexDirection: "column", gap: 2, padding: "14px 8px", boxSizing: "border-box", borderRight: "1px solid var(--hairline)", position: "sticky", top: DEMO ? 30 : 0, maxHeight: "100vh", overflowY: "auto", alignSelf: "flex-start" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: 36, marginBottom: 10 }}>
          <img src={brandMark()} width={28} height={28} alt={brandName()} style={{ objectFit: "contain", borderRadius: 6 }} />
        </div>
        {NAV.map((n) => item(n, 20))}
      </nav>
      <main style={{ flex: "999 1 320px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16, boxSizing: "border-box" }} className="px-4 pt-3.5 pb-24 md:px-6 md:pb-6">
        <header style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12, minHeight: 40 }}>
          <div style={{ fontSize: 13, color: "var(--ink-2)", fontWeight: 600 }}>{longDate(today)}</div>
          {snapshot && (
            <div style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 13, fontWeight: 700, padding: "6px 12px", borderRadius: 999, background: "var(--paycheck-soft)", color: "var(--paycheck-ink)" }}>
              <span className="sw" style={{ background: "var(--paycheck)", margin: 0 }} />
              {snapshot.next_paycheck.known === false ? <>No payday on file · <Link to="/confirm" style={{ color: "inherit", textDecoration: "underline" }}>name your paycheck</Link></> : <>Payday {daysWord(snapshot.next_paycheck.days)} · {shortDate(snapshot.next_paycheck.date)}</>}
            </div>
          )}
          {title && <h1 style={{ margin: 0, fontSize: 16, fontWeight: 800 }}>{title}</h1>}
          <div style={{ flexGrow: 1 }} />
          <div style={{ fontSize: 12, color: "var(--muted)" }}>{snapshot ? `${snapshot.accounts.length} accounts` : ""}</div>
          <Link to="/glossary" aria-label="The words the app uses" title="The words the app uses" style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12, color: "var(--ink-2)" }}><BookOpen size={15} aria-hidden="true" />Words</Link>
        </header>
        <NextStepsStrip />
        {children}
      </main>
      <nav className="tabs fixed left-0 right-0 bottom-0 flex justify-around items-start pt-2 md:hidden" aria-label="Tabs" style={{ height: 72, background: "var(--surface)", borderTop: "1px solid var(--hairline)" }}>
        {NAV.filter((n) => n.phone !== false).map((n) => item(n, 22))}
      </nav>
    </div>
    </div>
  );
}
