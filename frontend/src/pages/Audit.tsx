// Audit: every tool call any model or script made, with who, what, how many rows, and what it could see.
import { useApi } from "../api";
import { when } from "../format";
import { Failed, Loading } from "../components/State";
import { SEES } from "./Clients";

interface Entry {
  id: number;
  at: string;
  client: string;
  client_kind: string;
  tool: string;
  rows: number;
  redaction: string;
}

interface AuditOut {
  entries: Entry[];
  week: { calls: number; by_tool: Record<string, number>; by_client: Record<string, number> };
}

// What each tool read or did, in plain words. The chat uses the same words for the tools the advisor calls.
const TOOL_WORD: Record<string, string> = {
  finance_snapshot: "The overview of your money",
  accounts_list: "Your accounts",
  accounts_cover: "Cover",
  transactions_search: "Searched your transactions",
  recurring_list: "Repeating charges",
  bills_list: "Your bills list",
  payments_list: "Bill payments",
  paystubs_list: "Paystubs",
  spending_summary: "Spending summary",
  debts_list: "Debts",
  borrowing: "Borrowing",
  scenario_run: "A moving-home scenario",
  readiness: "Moving-home readiness",
  move_start: "Started a move",
  saving_plan: "Saving plan",
  today_one_thing: "Today's one thing",
  weekly_checkin: "Weekly check-in",
  account_move: "Switch banks",
  glance: "The glance (Siri, widgets)",
  paycheck_views: "Each paycheck's plan",
  billing_date_check: "Checked a billing date",
  billing_dates_suggest: "Billing dates that could move",
  purchase_check: "Purchase check",
  watchlist: "Hold off on",
  investments_list: "Investments",
  next_steps: "Next steps",
  outlook: "Outlook",
  where_to_put: "Where to keep savings",
  brief_today: "Today's brief",
  mail_search: "Searched your mail",
  mail_read: "Read a message",
  mail_attachment: "Opened an attachment",
  facts_pending: "Details waiting on Needs you",
  facts_confirm: "Confirmed a detail",
  mail_send_request: "Asked to send an email",
  calendar_feed_create: "Made a calendar feed address",
  calendar_feed_revoke: "Turned the calendar feed off",
  import_undo: "Undid an import",
  restore: "Put a copy back",
  jobs_status: "The jobs",
};

export function toolWord(tool: string): string {
  if (tool.startsWith("export:")) return `Export (${tool.slice(7)})`;
  return TOOL_WORD[tool] ?? tool.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

const KIND_WORD: Record<string, string> = { owner: "you", key: "key or app" };

export function Audit() {
  const state = useApi<AuditOut>("/api/v1/audit?limit=300");
  const { data, error } = state;
  const tools = data ? Object.entries(data.week.by_tool).sort((a, b) => b[1] - a[1]) : [];
  const clients = data ? Object.entries(data.week.by_client).sort((a, b) => b[1] - a[1]) : [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 980 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Audit</h1>
      <p style={{ margin: 0, color: "var(--ink-2)" }}>Every read a model, an app or a script made through the tools, newest first. Your own use of these screens is not logged, except a few pages and exports, which show "you" below.</p>
      {error && !data && <Failed error={error} what="the audit log" onRetry={state.reload} />}
      {!data && !error && <Loading what="the audit log" />}
      {data && (
        <>
          <section className="card" style={{ padding: 14, display: "flex", gap: 24, flexWrap: "wrap" }}>
            <h2 className="sr-only">This week</h2>
            <div><div className="lab">Calls this week</div><div className="num" style={{ fontSize: 28, fontWeight: 800 }}>{data.week.calls}</div></div>
            <div style={{ fontSize: 13 }}>
              <div className="lab">By what was read</div>
              {tools.length === 0 ? <div style={{ color: "var(--ink-2)" }}>none</div> : tools.slice(0, 8).map(([t, n]) => <div key={t}>{toolWord(t)} <span className="num" style={{ color: "var(--ink-2)" }}>{n}</span></div>)}
            </div>
            <div style={{ fontSize: 13 }}>
              <div className="lab">By client</div>
              {clients.length === 0 ? <div style={{ color: "var(--ink-2)" }}>none</div> : clients.slice(0, 8).map(([c, n]) => <div key={c}>{c} <span className="num" style={{ color: "var(--ink-2)" }}>{n}</span></div>)}
            </div>
          </section>
          <section className="card" style={{ padding: "6px 14px", overflowX: "auto" }}>
            <h2 className="sr-only">Every call</h2>
            <table className="ledger">
              <thead><tr><th>When</th><th>Client</th><th>What it read</th><th style={{ textAlign: "right" }}>Rows</th><th>What it could see</th></tr></thead>
              <tbody>
                {data.entries.map((e) => (
                  <tr key={e.id}>
                    <td style={{ color: "var(--ink-2)", whiteSpace: "nowrap" }}>{when(e.at)}</td>
                    <td><strong>{e.client}</strong> <span style={{ color: "var(--muted)", fontSize: 11 }}>{KIND_WORD[e.client_kind] ?? e.client_kind}</span></td>
                    <td>{toolWord(e.tool)}</td>
                    <td className="num" style={{ textAlign: "right" }}>{e.rows}</td>
                    <td style={{ color: "var(--ink-2)" }}>{SEES[e.redaction] ?? e.redaction}</td>
                  </tr>
                ))}
                {data.entries.length === 0 && <tr><td colSpan={5} style={{ color: "var(--ink-2)" }}>No calls yet.</td></tr>}
              </tbody>
            </table>
          </section>
        </>
      )}
    </div>
  );
}
