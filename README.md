# FinancialAuthority

**A self-hosted financial planner that runs itself and shows its work.** It reads your bank and card transactions, paystubs, bills, debts (including Affirm-style pay-later plans and paycheck-advance apps), subscriptions and your mailboxes, keeps all of it current without being asked, and answers from one set of numbers on every screen, in its own chat, over a REST API and to any AI model over MCP.

It never moves money. It reads, plans, drafts, and sends only what you approve. It pays nobody to see your own money.

**[Try the demo](https://demo.aes256afro.com)**: the same screens over a made-up household, in your browser, nothing to install.

![The River: what is free to spend, day by day, with paydays, bills and the safety cushion](docs/screenshots/view-river.png)

<table>
<tr>
<td width="33%"><img src="docs/screenshots/phone-today.png" alt="Today on a phone"></td>
<td width="33%"><img src="docs/screenshots/phone-today-dark.png" alt="Today on a phone, Night look"></td>
<td width="33%"><img src="docs/screenshots/phone-borrowing.png" alt="The borrowing plan on a phone"></td>
</tr>
<tr>
<td align="center">Today, on a phone</td>
<td align="center">The same, in the Night look</td>
<td align="center">The borrowing plan</td>
</tr>
</table>

Every picture here is the real app over a **made-up household**: no account, balance, merchant or address in them belongs to anyone. They are regenerated with one command (see [Screenshots](#screenshots)).

---

## What it answers

1. **What should this paycheck go on?** What the advance apps take back first, what must be paid before the next paycheck, what to set aside, and what is safe to spend.
2. **Should I buy this?** Yes, wait, or no, naming the bill or cushion it would break and the first day it would be fine.
3. **Which billing dates should move?** So bills land after paydays, with the request to each biller drafted and sent only once you approve it.
4. **What do I owe, and in what order should it go?** Every loan, plan and advance in one place, with a payoff plan that shows its reasons.
5. **What could my side business write off, and what is due for taxes?** With the rule and its official source next to every suggestion.

Every morning it says the one thing that needs attention. Everything it still needs from you is a tappable next step that lands on the exact button.

## The screens

### Today

The one thing to do, safe to spend with the bills already taken off, and the morning brief. Every number opens up into the lines that make it.

<table>
<tr>
<td width="50%"><img src="docs/screenshots/today.png" alt="Today"></td>
<td width="50%"><img src="docs/screenshots/today-dark.png" alt="Today, Night look"></td>
</tr>
</table>

### Eight ways to see the same plan

Pick a home view in Settings › Appearance, start from a ready-made arrangement (Paycheck first, Calm, Planner, Out of debt), or arrange your own from its parts. Every one draws the same numbers from the engine; none computes its own.

<table>
<tr>
<td width="50%"><img src="docs/screenshots/view-river.png" alt="The River"><br><b>The River</b>: sixty days of what is free to spend.</td>
<td width="50%"><img src="docs/screenshots/view-windows.png" alt="Paycheck by paycheck"><br><b>Paycheck by paycheck</b>: each check filled from the bottom with what it pays.</td>
</tr>
<tr>
<td><img src="docs/screenshots/view-month.png" alt="Month grid"><br><b>Month grid</b>: the calendar with paydays, due dates and each week's low point.</td>
<td><img src="docs/screenshots/view-flow.png" alt="Where it goes"><br><b>Where it goes</b>: one paycheck flowing to advances, bills, the next check and you.</td>
</tr>
<tr>
<td><img src="docs/screenshots/view-envelopes.png" alt="Envelopes"><br><b>Envelopes</b>: every obligation with its fill level.</td>
<td><img src="docs/screenshots/view-board.png" alt="Bill board"><br><b>Bill board</b>: cards move right as they are paid; a stuck card is the problem.</td>
</tr>
<tr>
<td><img src="docs/screenshots/view-transit.png" alt="Transit map"><br><b>Transit map</b>: bills as stations, paydays as interchanges.</td>
<td><img src="docs/screenshots/view-console.png" alt="Mission control"><br><b>Mission control</b>: the numbers, the connections and the jobs at a glance.</td>
</tr>
</table>

Four looks (Paper, Night, Console and Clear, the high-contrast one), a calm mode with one card at a time, a more readable font, larger type and "Hide amounts" for when someone is looking over your shoulder. Every chart colour is checked for colour-blind readers, and status colours always come with an icon and a word.

<img src="docs/screenshots/view-river-dark.png" alt="The River in the Night look">

### Borrowing and debts

Advances, pay-later plans and cards, read from your transactions and the lenders' own emails. The plan simulates paying the closest-to-done first against paying the highest rate first, and picks with its reasons shown. Paid one early, or with a card the app cannot see? "I paid it" counts it now, and the bank's own record takes its place when it arrives, never twice.

<table>
<tr>
<td width="50%"><img src="docs/screenshots/borrowing.png" alt="Borrowing plan"></td>
<td width="50%"><img src="docs/screenshots/debts.png" alt="Debts"></td>
</tr>
</table>

### Bills, cover and the move

<table>
<tr>
<td width="33%"><img src="docs/screenshots/bills.png" alt="Bills"><br><b>Bills</b>: due dates, autopay, what is paid, and which dates could move.</td>
<td width="33%"><img src="docs/screenshots/cover.png" alt="Cover"><br><b>Cover</b>: whether each account holds what will come out of it.</td>
<td width="33%"><img src="docs/screenshots/move.png" alt="Move"><br><b>Move</b>: notice to give, move-in costs, the next lease and its utilities.</td>
</tr>
</table>

### Trust what is counted

The data check runs on its own every six hours, with no AI model. It looks for anything counted twice, bills that look alike, the same purchase arriving from two sources, and mail that may not be from who it says it is (checked against the mail server's own signature checks and look-alike domains). Nothing changes until you answer, and every answer teaches it.

<table>
<tr>
<td width="50%"><img src="docs/screenshots/data-check.png" alt="Data check"></td>
<td width="50%"><img src="docs/screenshots/confirm.png" alt="Needs you"></td>
</tr>
<tr>
<td align="center"><b>Data check</b></td>
<td align="center"><b>Needs you</b>: facts read from mail wait for a yes before they count.</td>
</tr>
</table>

### Business and taxes

For a side business, a sole proprietorship or a small LLC: a setup guide with every step's official source, a finder that asks about past purchases that could be business costs, receipts, invoices you send and receive, and a mileage log at the IRS rate. Taxes shows the year date by date, the deductions to check and what never counts. It explains general rules with their sources; a tax preparer decides.

<table>
<tr>
<td width="50%"><img src="docs/screenshots/business.png" alt="Business setup guide"><br><b>Business</b>: the setup, step by step.</td>
<td width="50%"><img src="docs/screenshots/write-offs.png" alt="Write-offs"><br><b>Write-offs</b>: purchases that could be business costs.</td>
</tr>
<tr>
<td><img src="docs/screenshots/invoices.png" alt="Invoices"><br><b>Invoices</b>: sent and received, paid and waiting.</td>
<td><img src="docs/screenshots/taxes.png" alt="Taxes"><br><b>Taxes</b>: the tax year, date by date.</td>
</tr>
</table>

### Accounts, reports and what comes next

<table>
<tr>
<td width="50%"><img src="docs/screenshots/accounts.png" alt="Accounts"><br><b>Accounts</b>: what you have and what you owe, on one scale.</td>
<td width="50%"><img src="docs/screenshots/reports.png" alt="Reports"><br><b>Reports</b>: where the money went, by category or merchant.</td>
</tr>
<tr>
<td><img src="docs/screenshots/outlook.png" alt="Outlook"><br><b>Outlook</b>: savings and retirement over the years, low, middle and high.</td>
<td><img src="docs/screenshots/investments.png" alt="Investing"><br><b>Investing</b>: a 401(k), an IRA or a brokerage account, linked or by hand.</td>
</tr>
</table>

### Chat, and a second opinion

The **advisor** explains the plan in the chat, with the plan already worked out in front of it. The **reviewer** reads the plan and your rules on payday, never your mail, and leaves notes you apply with one tap. Both are optional: a free local model through Ollama, or Claude or any OpenAI-compatible model by API. Every figure either one says is checked against the engine's own figures, and anything else is flagged.

<img src="docs/screenshots/chat.png" alt="Chat with the reviewer's notes">

### Connections and appearance

<table>
<tr>
<td width="50%"><img src="docs/screenshots/connectors.png" alt="Connections"><br><b>Connections</b>: banks, mailboxes and files, and how fresh each one is.</td>
<td width="50%"><img src="docs/screenshots/appearance.png" alt="Appearance"><br><b>Appearance</b>: views, looks, the arranger and the type.</td>
</tr>
</table>

---

## Features

**Where the numbers come from (all free)**
- Banks and cards, live: Plaid's free Trial plan or Teller's free developer tier. The bank's own sign-in page opens in the vendor's window; the app only ever holds an encrypted, read-only token. Each bank shows when the bank last sent data, not just when the app last asked.
- Your bank shows something else? Type what its app says, and the plan uses it until the bank sends something newer. A figure far above what the app holds is checked first (a dropped decimal point is caught).
- Files: any bank's CSV (columns and sign convention detected), OFX, QFX and QBO, from a drop folder or the browser; Rocket Money exports, picked up from the email that announces them.
- Mail: Proton Mail through the Bridge (as a sidecar container) and Gmail over IMAP, opened read-only. Bills, statements, paystubs, purchase alerts, lender notices and AutoPay changes become facts that wait for your yes.
- Documents: paystub PDFs, leases, receipts and photos, read with OCR when they are scans.
- Investments: through Plaid's investments product or typed in from each statement.

**The engine (no AI model needed)**
- Safe to spend, the paycheck plan, cover by account, the 60-day forecast, the purchase check and the billing-date check.
- Recurring charges and paychecks found from the ledger; bills matched to their payments, so a bill paid on time is marked paid and a late one is said.
- Borrowing: each advance paired with its repayment, pay-later plans followed payment by payment, AutoPay status from the lenders' mail, and a payoff plan with reasons.
- Goals, saving ladders, sinking funds, budgets, scenarios, an outlook to retirement, and where an extra $50 a month would do the most.
- Moving: notice dates, move-in costs, utilities and the next lease, all on the same timeline.
- A data check, suggestions that learn from your answers, and a self-check of every source and job.

**Reach it from anywhere**
- An MCP server with its own OAuth 2.1 sign-in: add it to claude.ai (web, desktop, phone), Claude Code or any MCP client. 44 tools, each with its scope, a read-only flag where true, redaction and an audit entry.
- The same tools over REST under `/api/v1`, with schemas for OpenAI and Anthropic tool use.
- An installable web app with push notifications (its own Web Push, ntfy or Gotify), a morning brief, reminders, a calendar feed, a glance view, Home Assistant sensors and exports in twelve formats.

**Safe by design**
- It never moves money: no tool, job or button can start a payment or transfer.
- Nothing is sent without your approval, and every send is recorded. No AI model can approve.
- Read-only against every source. No bank password is ever stored.
- Email and documents are treated as untrusted data: what they say waits for your confirmation and can never create an approval.
- Secrets are encrypted at rest and never reach logs or the browser. Sign-in supports passkeys and a second step. Restore points daily, with an optional off-box copy.
- See [docs/SECURITY.md](docs/SECURITY.md) for each risk and its control.

---

## Install

**Start with [docs/SETUP.md](docs/SETUP.md)**: [BoxPilot](https://github.com/AES256Afro/BoxPilot), Docker Compose on Linux, or Docker Desktop on Windows or Mac, then mail (the Proton Mail Bridge step by step), your bank, your phone and Claude.

One image, published by CI as `ghcr.io/aes256afro/financialauthority` (`:edge` from main, `:X.Y.Z` from tags). `deploy/compose/docker-compose.yml` runs it anywhere; on a BoxPilot server it is a catalog app.

## Run it from source

Backend (Python 3.12 or newer, [uv](https://docs.astral.sh/uv/)):

```bash
cd backend && uv sync && uv run uvicorn app.main:app --port 8000
```

With `FA_DEMO=true` (see `deploy/compose/.env.example` for every variable) the made-up household loads, so the screens can be seen without real data. With no `FA_PASSWORD`, sign-in is off; that is only for a developer's own machine.

Frontend (Node 24):

```bash
cd frontend && npm ci && npm run dev
```

The dev server proxies `/api` and `/mcp` to the backend. `npm run build` writes `frontend/dist`, which the backend serves when it exists.

**Tests:** `uv run pytest` in `backend/`, `npm test` in `frontend/`. Browser runs: `npm run build`, then `npm run e2e` in `frontend/` (sign-in on, the made-up household, a phone and a desktop screen). More made-up households: `FA_PROPERTY_SEEDS=300 uv run pytest tests/test_invariants.py`. When a known figure moves on purpose: `FA_GOLDEN_WRITE=1 uv run pytest tests/test_golden.py`.

### Screenshots

```bash
cd frontend && npm run shots
```

Builds the app, starts it over the made-up household with a small business added (`backend/scripts/shots_server.py`), walks every main screen on a desktop and a phone, and writes the pictures to `docs/screenshots/`.

## Connect an AI model

The app is its own OAuth 2.1 server. In claude.ai (web, desktop or phone): Customize › Connectors › + Add › Add custom connector, with `https://<your-address>/mcp`. The app's consent page appears once. In Claude Code:

```bash
claude mcp add --transport http financialauthority https://<your-address>/mcp
```

Anything without a browser takes an API key from Settings › Clients as a bearer token instead.

The tools: `finance_snapshot`, `brief_today`, `today_one_thing`, `next_steps`, `purchase_check`, `billing_date_check`, `billing_dates_suggest`, `watchlist`, `paycheck_views`, `accounts_cover`, `saving_plan`, `scenario_run`, `readiness`, `weekly_checkin`, `glance`, `spending_summary`, `accounts_list`, `account_move`, `transactions_search`, `recurring_list`, `bills_list`, `payments_list`, `paystubs_list`, `debts_list`, `borrowing`, `borrowing_plan`, `data_check`, `investments_list`, `outlook`, `where_to_put`, `housing_overview`, `business_books`, `write_off_finder`, `tax_year`, `documents_search`, `document_read`, `receipts_list`, `mail_search`, `mail_read`, `mail_attachment`, `facts_pending`, `facts_confirm`, `mail_send_request`, `jobs_status`.

## Documents

- [docs/SETUP.md](docs/SETUP.md): install and set up, step by step, for each kind of server.
- [docs/PLAN.md](docs/PLAN.md): what it is, how it operates, where the data comes from, the engine, reach from anywhere, views and looks, security, decisions.
- [docs/ROADMAP.md](docs/ROADMAP.md): the milestones, done and next.
- [docs/FEATURES.md](docs/FEATURES.md): what Monarch, Empower, Simplifi, YNAB, Copilot, Cleo, Origin and Rocket Money do, and what this app takes from them.
- [docs/SECURITY.md](docs/SECURITY.md): what the app holds, who can reach what, each risk and its control.
- [docs/DEMO.md](docs/DEMO.md): the shareable demo over the made-up household, and how to regenerate it.
- [docs/HOMEASSISTANT.md](docs/HOMEASSISTANT.md): the sensors and how to add them.
- [docs/ACCESS.md](docs/ACCESS.md): the few things only the owner can grant.
- [AGENTS.md](AGENTS.md): rules for working in this repository, for people and coding agents.

---

FinancialAuthority is not financial or tax advice. It is a calculator that shows its work: every figure comes from the engine with its sources attached.
