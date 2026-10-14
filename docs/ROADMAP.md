# FinancialAuthority roadmap

*Written 2026-10-14, revised the same day. The thinking behind it is in [PLAN.md](PLAN.md); what the owner grants is in [ACCESS.md](ACCESS.md). Marks: ☐ planned, 🔄 in progress, ✅ done (version). Effort is for one developer working with coding agents. Each milestone is also a GitHub milestone on the repository.*

Order matters more than dates. Real transactions are live by the end of M1. Claude can reach the data from the phone by the end of M3. Both mailboxes are in by the end of M4. The paycheck plan exists at the end of M5. The app runs itself at the end of M6. All eight views and three looks are in by the end of M7. v1.0 ships at the end of M8, about eighteen weeks in. The feature inventory from the leading apps is in [FEATURES.md](FEATURES.md); each row there names its milestone below.

## M0 — Access and foundations (1 week)

- ☐ **M0.1 Access on BigBox.** A shell account for the coding agent on BigBox with sudo and the docker group, reachable over Tailscale SSH from this PC; verified by a deploy of the empty image. The owner's step is in ACCESS.md. *Waiting on the owner (2026-10-14).*
- 🔄 **M0.2 Repository and skeleton** (2026-10-14). `backend/` (FastAPI), `frontend/` (Vite, React, TypeScript, Tailwind), `docker/Dockerfile` that builds the frontend in a first stage, `deploy/compose/` for standalone installs, CI that tests, builds the image on every push and publishes `ghcr.io/aes256afro/financialauthority` on a `v*` tag. One version string in `backend/app/version.py`. *Left: the first CI run, and making the package public.*
- ✅ **M0.3 Owner sign-in** (2026-10-14). Username and password from `FA_USERNAME` and `FA_PASSWORD`, session cookie signed with `FA_SESSION_SECRET`, CSRF header on writes, lockout after ten failures, API keys with scopes `read`, `confirm`, `admin` and a redaction level; no key can hold `approve`. Re-authentication for admin pages comes with M3.
- 🔄 **M0.4 Data layer** (2026-10-14). SQLite at `/data/financialauthority.db`, the tables of PLAN section 5, AES-GCM field encryption keyed by `FA_DATA_KEY`. *Left: versioned migrations once the first schema change arrives.*
- ✅ **M0.5 Health, logs, time, jobs** (2026-10-14). `/api/health`, logs that never carry secrets, the owner's time zone, APScheduler with an hourly self-check and a five-minute import-folder watch, a Jobs page.
- 🔄 **M0.6 The synthetic household** (2026-10-14). A generated twelve-month dataset: two paychecks a month with one short one, thirteen bills, two cards, an auto loan, three installment plans, two advances, a price increase, a duplicate subscription. *Left: paystubs in M2.6 and the mailbox fixture in M4.*
- ☐ **M0.7 First deploy.** The image running on BigBox from `deploy/compose`, health green, sign-in working, demo mode showing the fixture. *Blocked on M0.1.*
- ✅ **M0.8 The first screens** (2026-10-14, pulled forward from M7). The shell with a rail and phone tabs; the River as the default home view, drawn from `finance_snapshot` v0 with two series (at the usual pace, and if nothing else is spent); Today; the ledger; Settings with Appearance (eight views registered, the River ready, the rest marked for M7.6; three looks), Clients, Connectors (Rocket Money in and out) and Jobs. The MCP endpoint serves `finance_snapshot`, `accounts_list`, `transactions_search` and `jobs_status` behind bearer keys.

Done when: CI publishes the image, it runs on BigBox, and the coding agent did the deploy itself.

## M1 — The ledger, live (2 weeks)

- ☐ **M1.1 Live bank connectors, free tiers.** Plaid (Trial plan: the owner's developer keys pasted once; the bank's own login opened inside the app; daily and on-demand pulls; webhooks when available), Teller (developer tier, the same shape), and Era Context (the app as an MCP client with OAuth, for the two logins its free tier allows). De-duplication across connectors and exports; connector health on the page and in the self-check. Capital One and the credit union first.
- ☐ **M1.2 Rocket Money and exports.** The Rocket Money connector: the export email recognised and its link followed within its seven-day life, a button that opens Rocket Money's export page, drop-folder and upload; the loader ported from `MCPManagement/rocketmoney-mcp` (columns, de-duplication, newest-export-wins, the is-spending rule); Rocket Money's weekly upcoming-bills, income and balance mails read as cross-checks; the ledger exported in the same CSV shape. Generic CSV with column mapping; OFX and QFX.
- ☐ **M1.3 Accounts and balances.** Accounts from every connector, balances live or entered, snapshots over time, `accounts.list`.
- ☐ **M1.4 Normalisation and rules.** Merchant cleanup, categories seeded from the export and re-mappable, flags for transfers, card payments, income and ignored rows; a rules engine that matches on merchant, amount, account or text and can categorise, rename, tag, split or hide; every correction the owner makes becomes a rule (merchant memory).
- ☐ **M1.5 Recurring detection.** Port the cadence detection into `RecurringSeries` with amount tolerance and next expected date.
- ☐ **M1.6 Paycheck detection.** Recurring income becomes `Income` after the owner confirms employer, cadence, expected net and next date.
- ☐ **M1.7 Read tools.** `transactions.search`, `spending.summary`, `recurring.list`, `accounts.list`.
- ☐ **M1.8 Alert mail as signals.** The bank's alert mail (purchase, deposit, overdraft, negative balance, declined, payment received, statement ready) and the budget app's alerts become events the same quarter-hour: an overdraft or a decline reaches the brief at once, a deposit alert confirms a paycheck before the next sync, a statement notice fetches the PDF for reconciliation. Needs the mail connector's first slice, pulled forward from M4.1.

Done when: Capital One and the credit union sync daily with no hand in it, an overdraft alert shows in the app within fifteen minutes of the mail, three years of history are loaded from the export, and the recurring series and paychecks found match what the owner knows.

## M2 — Obligations, payments, paystubs (2 weeks)

- ☐ **M2.1 Bill registry.** Each recurring series is confirmed as a bill, a subscription, income, an advance, or ignored. Manual add. Fields: biller, amount (fixed or estimated), due day, autopay and its account, grace days, late fee, movable and the allowed window.
- ☐ **M2.2 Debts.** Cards, loans, medical plans: balance, APR, minimum, statement day, due day.
- ☐ **M2.3 Installment plans.** BNPL purchases with schedules, detected from transactions and confirmed, or entered in a thirty-second form.
- ☐ **M2.4 Advances.** Earned-wage and cash-advance apps: amount, fee, repayment date, repaid or not; detected from the pair of transactions and confirmed.
- ☐ **M2.5 Payment matching.** Each bill period finds its payment or becomes late, then past due; the Payments page and `payments.list`; the "pay this now" list.
- ☐ **M2.6 Paystubs.** PDFs from `/imports/paystubs`, upload, or forwarded mail; a parser for the owner's payroll format with a model fallback and confirmation; gross, net, taxes, deductions, period, pay date; matched to the deposit; expected-versus-actual net with a discrepancy flag; `paystubs.list`, `paystubs.get`.
- ☐ **M2.7 Subscriptions.** Price history, last charge, category, duplicate detection.
- ☐ **M2.8 Calendar and snapshot.** Paydays, due dates and advance repayments on one grid, sixty days ahead; `finance.snapshot`, `bills.upcoming`, `debts.list`, `installments.list`, `advances.list`, `subscriptions.list`.
- ☐ **M2.9 Accounts and net worth.** Every account with its balance, connector and freshness; net worth as assets minus debts, kept daily; investment balances read-only where a connector offers them; `networth.history`.

Done when: every recurring charge in the fixture and in the real data is classified, last month's bills each show their payment, the owner's last three paystubs are parsed and matched to their deposits, and the calendar shows the next sixty days correctly.

## M3 — Reach: any model, from anywhere, at all times (2 weeks)

- ☐ **M3.1 MCP server with OAuth 2.1.** Streamable HTTP at `/mcp`; authorization code with PKCE, dynamic client registration, protected-resource metadata; the owner signs in once per client; refresh tokens; revocation on the Clients page; read-only annotations on every read tool.
- ☐ **M3.2 REST and schemas.** `/api/v1` mirrors the tools with bearer keys. `/api/tools/openai.json`, `/api/tools/anthropic.json`, and a prompt pack.
- ☐ **M3.3 Published address.** The app published through BoxPilot's Cloudflare Tunnel at `https://<name>.<domain>`; `FA_PUBLIC_URL` set; rate limits and lockout verified from outside; the tailnet address as fallback.
- ☐ **M3.4 Clients connected.** claude.ai custom connector on the web, the desktop and the phone; Claude Code with `claude mcp add`; Open WebUI on BigBox. Each documented with the exact steps.
- ☐ **M3.5 Providers and chat.** Ollama (default), any OpenAI-compatible server, Anthropic; a settings page with a test button; built-in streaming chat with tools on and the rows it used.
- ☐ **M3.6 Redaction and audit.** A redaction level per client and per provider; an audit log of every tool call, with a page.
- ☐ **M3.7 Advisor and reviewer.** Two model roles in Settings › Models: the advisor (local by default) for chat and explanations; the reviewer (none by default; Claude by API recommended; any OpenAI-compatible model works) that reads the plan, rules, goals and recent alerts and returns notes with one-tap actions, on payday, after confirmed changes and on request, with the cost of each review shown. The citation test runs on both.

Done when: Claude on the phone, away from home, answers "what is due before my next paycheck and is it funded?" correctly from live data, and the audit log shows it.

## M4 — Mail: both mailboxes, always on (2 weeks)

- ☐ **M4.1 Generic IMAP connector.** Host, port, user, app password, folders to watch; read-only; incremental by UID; references and hashes, not bodies, unless the owner turns bodies on; attachments captured when a parser wants them.
- ☐ **M4.2 Proton Mail through the Bridge sidecar.** The sidecar in the catalog manifest, its state volume backed up; the owner's one-time sign-in documented; the reader ported from `MCPManagement/protonmail-mcp`, including draft creation.
- ☐ **M4.3 Gmail.** IMAP with an app password, for what Proton does not carry.
- ☐ **M4.4 Mail tools.** `mail.folders`, `mail.search`, `mail.read`, `mail.attachment`, `mail.draft` for both mailboxes, every body boxed as untrusted data.
- ☐ **M4.5 Send with approval.** `mail.send` creates an approval; the owner approves in the app or from the push; standing allowances per recipient; every send recorded. No model client can approve.
- ☐ **M4.6 Classifier and extraction.** Bill due, statement, paystub, installment schedule, advance, renewal or price change, payday, payment confirmation, past due, or none. Sender and subject rules first, a model second; schema and bounds validation; pending facts with excerpts.
- ☐ **M4.7 Confirm inbox and earned auto-confirm.** The owner confirms or rejects; two confirmations for a sender earn auto-confirm for that sender and kind, shown and revocable; confirmed facts update bills, payments, debts, plans and advances.
- ☐ **M4.8 Past-due detection.** A past-due notice marks the bill and reaches the brief the same hour.

Done when: thirty days of both real mailboxes yield bills, statements, paystubs and payment confirmations for the known senders at ninety percent precision, the owner has approved one send and granted one allowance, and the red-team mailbox shows no email can reach a tool call or an approval.

## M5 — The guidance engine (2.5 weeks)

- ☐ **M5.1 Cash-flow projection.** Daily balance per account for ninety days from confirmed bills, series, paychecks, advances, minimums and installments, with a what-if layer.
- ☐ **M5.2 Paycheck plan.** Advances first, then obligations in the window, set-asides, minimums, buffer, goals, safe-to-spend, with every line's source; the "pay this now" list beside it.
- ☐ **M5.3 Purchase check.** Yes, wait or no; the first thing it breaks; the first date it would pass; the BNPL variant.
- ☐ **M5.4 The don't-buy list and watchlists.** Buffer breaches, categories over target, duplicate subscriptions, price increases, too many plans or any active advance, a pay-in-full card carrying a balance. Watchlists: a soft cap on any category, merchant or tag with a month-end projection; they warn and project, never block.
- ☐ **M5.5 Billing-date optimiser.** The objective and search from the plan; a ranked change list with before-and-after minimum balances, per-biller instructions, and drafts queued for approval.
- ☐ **M5.6 Rules and goals.** Buffer, pay-in-full, never-touch accounts, category targets, goal priorities; `notes.remember`. Save-up goals (target, date, pace, funded from the aqua band, with on-track, ahead and at-risk states) and pay-down goals (which debt, how much extra, the payoff date it buys), both visible in the paycheck plan and the debt mountain.
- ☐ **M5.7 Evaluation.** Golden outputs on the fixture; property tests (never below the buffer, totals reconcile, the optimiser never worsens the minimum balance); the citation test.
- ☐ **M5.8 Tools.** `cashflow.forecast`, `paycheck.plan`, `purchase.check`, `billing_dates.suggest`, `notes.remember`.

Done when: on the fixture the plan matches its golden output, the optimiser finds the known better schedule, and a local 8 B model explains a plan without a number that is not in the engine's result.

## M6 — It runs itself (1.5 weeks)

- ☐ **M6.1 Schedules.** Mail every fifteen minutes, bank daily and on demand, import watch, re-plan on payday and after confirmed changes, the brief at 07:00, the self-check hourly; all idempotent, all on the Jobs page.
- ☐ **M6.2 The brief, the recap, the report.** Daily: due in three days and unfunded, past due, paystub discrepancies, new plans or advances, price increases, failed connectors, approvals waiting; `brief.today`. Monday: the week's recap, what was spent against the plan, what changed. Month end: the report, by category, merchant and tag, against last month and the same month last year, with the savings rate. All three readable by any model.
- ☐ **M6.3 Push.** Delivery through ntfy or Gotify from BoxPilot's catalog, with an approve action for sends; the Today page on the phone.
- ☐ **M6.4 Self-check.** Each connector's last success, stale-data warnings, the public address reachable, disk and database size; `jobs.status`.
- ☐ **M6.5 Export reminder.** The aggregator's export email triggers a reminder; nothing is downloaded automatically.

Done when: a week passes with no hand on the app and the brief is right every morning.

## M7 — The web app: views, looks and the registers (3.5 weeks)

- ☐ **M7.1 Today and the River.** The phone home (the brief, approvals, safe-to-spend, what is due) and the default desktop home, the River, drawn from the engine's results with hover detail and a table twin.
- ☐ **M7.2 The registers.** Calendar with the week's low point, Bills, Payments, Paystubs (the anatomy bar and the six-paycheck strip), Debts (the mountain, the order, the bump), Subscriptions, Transactions, Accounts and net worth, Mail (the lens).
- ☐ **M7.3 Confirm, Connectors, Clients, Jobs, Audit, Settings.** Finished, not skeletons; send allowances, auto-confirm rules, watchlists and goals visible and editable.
- ☐ **M7.4 Chat.** The advisor beside the engine's table with citations, the reviewer's notes with their one-tap actions, "ask about this" from every page.
- ☐ **M7.5 Installable.** Web app manifest, icons, a service worker for the shell, light and dark.
- ☐ **M7.6 The other seven home views.** Paycheck windows, Month grid, Where it goes, Envelopes, Bill board, Transit map, Mission control, each a layout over the same four engine results, each with a thumbnail in Settings › Appearance, switchable per device.
- ☐ **M7.7 Looks.** Light, dark and console as token sets; the chart palette of each validated in both modes; Settings › Appearance with a live preview; kept per device.
- ☐ **M7.8 Reports.** Spending by category, merchant and tag over a chosen period, against the previous period and the same period last year; income, spending, net and savings rate; every chart with a table twin.

Done when: everything the tools can answer is visible without a model, every view renders the fixture correctly, switching views and looks takes one tap, and the payday routine takes under two minutes on a phone.

## M8 — Ship and harden to v1.0 (1.5 weeks)

- ☐ **M8.1 Catalog.** `deploy/boxpilot/financialauthority.yaml` pinned to the release and sent as a pull request to BoxPilot; install from the catalog; back up `/data` and the Bridge state; restore elsewhere; update to the next release.
- ☐ **M8.2 Standalone.** `deploy/compose` with `.env.example`; `cloudflared` and Tailscale recipes.
- ☐ **M8.3 Threat model written down**, public-surface review, secret handling reviewed, dependency audit in CI.
- ☐ **M8.4 Red team.** The malicious-mailbox suite grows; tool-misuse tests for every scope; rate limits, lockout and OAuth revocation verified from outside.
- ☐ **M8.5 Keys and exit.** `FA_DATA_KEY` rotation, revoke-all, export everything, delete everything.
- ☐ **M8.6 Scale and docs.** Five years of transactions and a hundred thousand messages without the UI noticing; README, connector and tool references, "how the numbers are made".

Done when: `v1.0.0` is tagged, every item in the security checklist is ticked, and every recommendation carries its sources and the not-advice line.

## Later

- ☐ **L1 Plaid or Teller** for institutions SimpleFIN misses.
- ☐ **L2 Credit.** Statement import, utilisation targets, inquiries; Credit Karma through a Claude session.
- ☐ **L3 Calendar.** Paydays, due dates and advance repayments written to the owner's calendar.
- ☐ **L4 Household.** A second owner, shared and private accounts, per-account visibility.
- ☐ **L5 BoxPilot agents.** A `finance.*` read tool for BoxPilot's agents. A BoxPilot change.
- ☐ **L6 Era Context** as a second live source over MCP or REST.
- ☐ **L7 Automation.** Reminders before due dates; billing-date drafts created on approval; a monthly review the model writes from the engine's numbers.
- ☐ **L8 Investments and fees.** Holdings, allocation and fee read-outs when an investment connector exists.
- ☐ **L9 Long-range planner.** A multi-year projection from the same engine: savings rate, debt-free date, a retirement estimate with a few scenarios.
- ☐ **L10 Receipts.** Photographed receipts matched to transactions and split into lines.
- ☐ **L11 A read-only client for a helper.** A family member or an adviser, with its own redaction level.

## Suggested first two weeks

M0 in full, with M0.1 first because everything after it is faster with a shell on BigBox. Then M1.1 (SimpleFIN) the day the setup token arrives, and M1.2 and M1.5 (the two ports from `MCPManagement`) so three years of history and the recurring series are in by day ten, then M1.6 so the next paycheck is known. Nothing in that fortnight needs a model.
