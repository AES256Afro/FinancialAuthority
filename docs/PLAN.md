# FinancialAuthority: the plan

*Written 2026-10-14, revised the same day for always-on access, payments, paystubs and both mailboxes, and again for free-only data access, the switchable views and looks, and the advisor and reviewer models. The milestones are in [ROADMAP.md](ROADMAP.md). What the owner grants and what Claude already reaches is in [ACCESS.md](ACCESS.md). What the leading apps do and what we take from them is in [FEATURES.md](FEATURES.md). The mock-ups are in [mockups/](mockups/) and on the design canvas. Rules for working in this repository are in [AGENTS.md](../AGENTS.md).*

## 1. What it is

FinancialAuthority is a self-hosted financial context engine that runs itself. It reads everything about a household's money, keeps it current without being asked, and hands one clean picture of it to any language model, from anywhere, at any time.

It reads:

- bank, card and loan **transactions and balances**, live, from a read-only aggregator, with years of history backfilled from exports;
- **paystubs**: gross, net, taxes, deductions, pay period and pay date, matched to the deposit that landed;
- **paychecks** as a schedule: who pays, how much, how often, when the next one lands;
- **monthly bills** and their due dates, which account pays them, whether they are on autopay, and which can be moved;
- **payments**: which bill each payment settled, what is scheduled, what is late, what is past due;
- **debts**, including the ones that live in third-party apps: Affirm, Klarna, Afterpay, PayPal Pay in 4, store cards, medical plans, and earned-wage advance apps whose repayment comes straight out of the next paycheck;
- **subscriptions**, with price history;
- **both mailboxes**, Proton Mail and Gmail, in full: the emails that carry bills, statements, paystubs, installment schedules, payment confirmations, past-due notices and payday notices.

It gives back three things:

1. **The paycheck plan.** For the paycheck that just landed, or the next one: what must be paid before the one after it, what the advance apps will take on payday, what to set aside for bigger bills further out, and what is safe to spend.
2. **The purchase check.** "Can I buy X for $Y?" answered yes, wait, or no, naming the bill or buffer it would break, and when it would be fine.
3. **Billing-date changes.** Which due dates to move and to which day so that bills land after paydays rather than before them, with the request to each biller drafted, and sent once approved.

Any model can do the talking: Claude on the phone, on the desktop or in Claude Code through a custom connector; a small model on BigBox through Ollama; Open WebUI; anything that speaks the OpenAI tool format. FinancialAuthority exposes the same tools to all of them over MCP and a REST API, and has its own chat. Inside the app, a local **advisor** answers everyday questions at no cost, and a **reviewer** the owner chooses, Claude by API or any OpenAI-compatible model, reads the plan and the rules and says where it would push back.

It is a visual planner first. The owner learns by seeing the shape of a thing before reading into it, so every screen leads with a picture that carries the answer, and the numbers sit under it for the dig-down. Eight home views and three looks are switchable in Settings; the data and the engine behind them never change.

It never moves money. It reads, plans, drafts and, with an approval, sends. The person pays.

## 2. How it operates: autonomy and approvals

The owner asked for two things of the people and agents working on this: do everything yourselves, and ask only for what you cannot get. The app works the same way.

- **It keeps itself current.** Mail is read every fifteen minutes. Bank data syncs daily and on demand. Paystubs and statements are picked up the moment they arrive as attachments or files. Bills are marked paid when the matching transaction clears. The plan is recomputed the morning a paycheck lands.
- **It confirms what it can.** A fact from a sender whose previous facts the owner confirmed twice is confirmed automatically, with a note. Only a new sender, a new kind of fact, or an amount outside its usual range waits for the owner.
- **It says what needs a human.** A morning brief on the phone and a `brief.today` tool for any model: what is due in three days and not yet funded, what is past due, a paystub that does not match the expected net, a new installment plan or advance, a price increase, a connector that stopped working.
- **Outward actions go through approvals.** Sending an email is an action: the app drafts it, asks once, sends on approval and records it. The owner can grant a standing allowance for a recipient (a landlord's office, a lender's billing address), after which that recipient needs no further approvals. Nothing is ever sent to a recipient without one or the other.
- **Money never moves.** No tool, job or button initiates a payment or transfer, with or without an approval. The app says what to pay and where; the owner pays.

## 3. Rules the design does not bend

- **Read-only against every source.** No bank logins are stored in the app. Transactions come from a read-only aggregator token and from exports. Email is read over IMAP in read-only mode and never marked, moved or deleted.
- **Nothing is sent without an approval**, as section 2 describes, and nothing moves money.
- **Numbers come from the engine; words come from the model.** Every figure in a recommendation is computed by deterministic code with the rows it came from attached. The model explains, converses and asks follow-ups. A model that invents a number fails a test.
- **Email is untrusted input.** Messages are parsed into facts with a confidence score. A fact is *pending* until the owner, or an auto-confirm rule the owner earned, confirms it. Message text is boxed as data before any model sees it, and nothing an email says can become a tool call or an approval.
- **Local first, minimum out.** Data lives on BigBox in SQLite under `/data`. Cloud models get only what the question needs, at a redaction level set per client: full, merchants only, or aggregates only. Account numbers never leave the box.
- **Show the work.** Every plan, verdict and suggested change shows its arithmetic and the source rows. The audit log shows which client and which model read what, and every approval and send.
- **It is not advice.** The app says so on every recommendation. It is a calculator with good manners.
- **Free to run.** No paid aggregator, no paid subscription, no service that charges to connect a bank. Bank data comes from free developer tiers, from the free tier the owner already has, from exports, and from the alert and statement mail banks already send. A frontier model by API is optional and costs cents per review; a local model does the whole job for nothing.

## 4. Where the data comes from

| Source | How it gets in | What it yields | When |
| --- | --- | --- | --- |
| Plaid, Trial plan | Free for up to ten connected logins on a new developer account; the owner signs up once and pastes the keys into the app; each bank is connected through the bank's own login page inside the app. Capital One and most credit unions are there. | Daily transactions and balances. The first live source. | M1 |
| Teller, developer tier | Free for up to a hundred connected logins, rate-limited; the owner signs up once. Strong on the big banks. | The same; the second live source, for whatever Plaid's trial does not cover. | M1 |
| Era Context, free tier | Already connected to one of the owner's banks over MCP with OAuth; two connections, a three-week history window, a daily sync. The app connects as an MCP client. | A third live source for the two institutions it allows. | M1 |
| Bank alert mail | Banks already email alerts: a purchase, a deposit, an overdraft, a declined charge, a payment received, a statement ready. The mail connector turns each into a signal the same quarter-hour. | Same-day awareness between syncs; overdraft and decline alerts straight into the brief. | M1 |
| Rocket Money | No API. Three ways in, all in the app's Rocket Money connector: the export email is recognised and its download link followed within its seven-day life, so the owner taps Export once and the app does the rest; a button opens Rocket Money's export page; a file can still be dropped or uploaded. Rocket Money's own weekly upcoming-bills, income and balance alerts are read as cross-checks. The app exports its own ledger in the same CSV shape. | Years of history for backfill, categories, ignored flags, and a second opinion on upcoming charges. | M1 |
| Any bank or card | CSV with column mapping, OFX/QFX, downloaded from the bank's site. | Transactions, sometimes balances. For anything no connector covers. | M1 |
| Paystubs | PDFs: dropped in `/imports/paystubs`, uploaded, or taken from mail attachments, including ones the owner forwards to themselves. A parser per payroll format, a model second, confirmation for anything new. | Gross, net, taxes, deductions, pay period, pay date, employer. Matched to the deposit. | M2 |
| Proton Mail | Through Proton Mail Bridge running beside the app on BigBox, over IMAP. The reader exists in `MCPManagement/protonmail-mcp`; port it. | Bills, statements, paystubs, installment schedules, payment confirmations, past-due and payday notices. The owner's Proton mailbox also carries their Gmail, so one connector covers most of both. | M4 |
| Gmail | IMAP with an app password. OAuth stays off until it works without weekly re-authorisation. | The same, directly, for anything Proton does not carry. | M4 |
| Any mailbox | Generic IMAP. | The same. | M4 |
| Third-party debt and advance apps | No public APIs. Derived from transactions (equal amounts, same merchant, two-week spacing; advance repayments on payday), from emails (confirmations carry the schedule), and from a thirty-second manual form. | Installment plans with schedules; advances with repayment dates; card and loan balances, APRs, minimums. | M2 |
| Paid aggregators | SimpleFIN, Plaid production, MX, Finicity. | Not used. The owner pays nobody to see their own money. | never |
| Credit reports | Credit Karma's connector is reachable only from a Claude session; statement import otherwise. | Utilisation, balances, inquiries. | L2 |

## 5. The model of the money

- **Account**: bank, card, loan, BNPL app, advance app, cash. Type, institution, last four (encrypted), current balance and when it was last known, which connector feeds it.
- **Transaction**: date, account, merchant (cleaned), raw description, amount (positive is money out), category, flags: transfer, card payment, income, ignored. Source and row hash for de-duplication across connectors and exports.
- **RecurringSeries**: merchant, cadence (weekly, biweekly, monthly, quarterly, yearly), typical amount and tolerance, next expected date. The seed for bills, subscriptions, paychecks and advances.
- **Income**: employer or payer, cadence, expected net, next date, account it lands in. Confirmed from a series or from paystubs.
- **Paystub**: employer, payroll provider, pay period, pay date, gross, net, each tax and deduction line, the source document, the matched deposit, and the difference between expected and actual net.
- **Bill**: biller, amount (fixed or estimated from history), due day, autopay and the account it draws from, grace days, late fee, whether the due date can be moved and within what window. Linked to the series, to confirmed email facts, and to its payments.
- **Payment**: a bill period settled or not: scheduled, paid (with the transaction and any confirmation email), late, past due, missed. The register that answers "did rent go out?"
- **Debt**: card, loan, medical plan: balance, APR, minimum payment, statement day, due day. Linked to an account where one exists.
- **InstallmentPlan**: a BNPL purchase: provider, merchant, total, schedule of installments with dates and amounts, what is paid. A Debt with a schedule.
- **Advance**: an earned-wage or cash advance: provider, amount, fee, repayment date (usually the next payday), repaid or not. A Debt the paycheck plan subtracts first.
- **Subscription**: a bill that is a service: price history, last charge, category, the duplicate it may be of.
- **MessageRef**: a mailbox message by account, folder, UID and hash: sender, subject, date, attachments. Bodies are kept only when the owner turns that on; a redacted excerpt stays with each fact.
- **ExtractedFact**: kind (bill due, statement, paystub, installment schedule, advance, renewal, price change, payday, payment confirmation, past due), fields, confidence, status (pending, confirmed, auto-confirmed, rejected), the message or document it came from, and which rule or model produced it.
- **Rule**: an owner preference the engine obeys: minimum buffer, pay the card in full, never touch this account, category targets, goal priority, sender auto-confirm, standing send allowance.
- **Goal**: emergency fund, payoff, saving for a thing: target, priority, pace.
- **Approval**: an outward action (today: a send), who or what asked, the draft, status, who approved, when it ran.
- **Recommendation**: kind (paycheck plan, purchase check, billing-date change), inputs, outputs, explanation, which client and model asked, the owner's feedback.
- **Job**: every scheduled or on-demand run: connector syncs, parsers, re-plans, briefs, with timing and outcome.
- **AuditEntry**: every tool call: client, model, tool, argument hash, rows touched, redaction level.

## 6. How a model gets in, from anywhere

One access layer, three doors, the same tools behind each, reachable at all times:

- **MCP** at `/mcp` (streamable HTTP) with the MCP authorization flow built in: OAuth 2.1 with PKCE, dynamic client registration and protected-resource metadata, served by the app itself for its one owner. This is what lets claude.ai add FinancialAuthority as a custom connector on the web, the desktop and the phone, and lets Claude Code add it with one command. Tokens are long-lived with refresh, revocable per client on the API keys page. A stdio launcher exists for local clients that want one.
- **REST** under `/api/v1` mirroring the tools, with bearer API keys, for scripts and for chat apps without MCP.
- **Tool schemas** at `/api/tools/openai.json` and `/api/tools/anthropic.json`, plus a prompt pack for Open WebUI, LibreChat and home-grown agents.

**Reach.** The app is published through BoxPilot's Cloudflare Tunnel at `https://<name>.<domain>`, so nothing is opened on the router and the address works from any network. The web interface has its own sign-in; `/mcp` and `/api` are guarded by the app's OAuth and keys; Cloudflare's edge rate-limits and shields the rest. Inside the tailnet the same app answers at BigBox's tailnet address, which is the fallback if the tunnel is down.

The tools:

| Tool | Returns | From |
| --- | --- | --- |
| `transactions.search`, `spending.summary`, `recurring.list` | The ledger. | M1 |
| `accounts.list` | Accounts, balances, when each was last synced. | M1 |
| `finance.snapshot` | Balances, next paycheck, advances due on it, bills due before it, debts, pending facts: one call that answers most questions. | M2 |
| `bills.upcoming`, `payments.list`, `debts.list`, `installments.list`, `advances.list`, `subscriptions.list` | The registers. | M2 |
| `paystubs.list`, `paystubs.get` | Paystubs with every line and the matched deposit. | M2 |
| `mail.folders`, `mail.search`, `mail.read`, `mail.attachment` | Both mailboxes, read-only, boxed as untrusted data. | M4 |
| `mail.draft`, `mail.send` | A draft in the owner's Drafts folder; a send that waits for approval or matches a standing allowance. | M4 |
| `facts.pending`, `facts.confirm` | What needs a human; confirming needs the `confirm` scope. | M4 |
| `cashflow.forecast`, `paycheck.plan`, `purchase.check`, `billing_dates.suggest` | The engine. | M5 |
| `notes.remember` | A preference the owner stated in chat, stored as a pending rule. | M5 |
| `brief.today`, `approvals.list`, `jobs.status` | What needs attention, what is waiting for approval, whether the app is healthy. | M6 |

Built-in chat uses the same tools through a provider layer: Ollama on BigBox by default (reached at `host.docker.internal:11434`, as LLMCoach does), any OpenAI-compatible server, and Anthropic. Each client and provider has a redaction level. A 3 to 8 B local model can give a correct paycheck plan because the plan is computed before the model speaks.

Two model roles sit on top of the providers:

- **The advisor** answers questions and explains plans. Default: the local model, free, always on. The owner can point it at any provider.
- **The reviewer** is a second opinion. On payday, after any confirmed change, and on request, it receives the engine's plan, the owner's rules and goals, and a short history of alerts, never raw mail, attachments or account numbers, and returns notes: where it disagrees, what it would tighten, what it would ask the owner. Each note carries an action the owner can apply with one tap; the reviewer cannot change a number itself. Default: none; recommended: Claude by API, which costs cents a review and the app shows the cost. A local model can review too, and the citation test applies to both roles.

BoxPilot's own agents cannot call an app's API today. FinancialAuthority uses BoxPilot's Ollama from day one; a `finance.*` read tool in BoxPilot's agent catalog is L5.

## 7. The guidance engine

All deterministic, all tested against a synthetic household and golden outputs.

**Cash-flow projection.** For each account, a daily balance for the next 90 days from the current balance, confirmed bills, recurring series, paychecks, advance repayments, minimum payments and installments. A what-if layer adds hypothetical events without saving them.

**Payment matching.** Each bill period looks for its payment: a transaction to the biller within the window and near the amount, or a confirmation email. Matched is paid; past the due day with no match is late; past the grace period is past due. A past-due notice in mail marks the bill past due even without a match. This is the register the "pay this now" list reads.

**Paystub reconciliation.** Each paystub is matched to its deposit. The expected net (from the last few paystubs) is compared with the actual; a difference beyond a tolerance is flagged with the lines that moved, so a discrepancy is noticed on payday, not at tax time.

**Paycheck plan.** The window is from this paycheck to the next. First the advances that repay themselves on payday, then bills, installments and minimums due inside the window, autopay already committed, then set-asides for bills larger than one paycheck comfortably carries, then the buffer rule, then goals, then safe-to-spend. Every line names its bill, series, advance or rule. The "pay this now" list sits beside it: unpaid bills due within three days with amounts and where to pay.

**Purchase check.** Add the purchase to the projection on its date. It passes when the projected balance stays above the buffer through the next paycheck and one more, and no bill becomes unfunded. If it fails, the answer names the first thing it breaks and the first date it would pass. The BNPL variant adds the installments instead of the price and says what the plan adds to the next three paychecks.

**"Don't buy" list.** Anything that would breach the buffer before the next paycheck, categories over their target this period, a second subscription in a category that already has one, a price increase detected from email, more than N active installment plans or any active advance, and a card the owner has said to pay in full that is carrying a balance.

**Billing-date optimiser.** Inputs: paychecks, bills with a movable flag and allowed window, the buffer. Score for a schedule: the sum of shortfalls below the buffer over the projection, plus a penalty for each bill due in the three days before a payday, plus a penalty when more than a set share of one paycheck is due in any three-day window. Coordinate descent over the movable bills, deterministic. Output: changes ranked by benefit, each with the before-and-after minimum balance, the instruction for that biller, and a draft message when the biller takes one, queued for approval.

**Explanations.** The engine returns structured results with sources. The model gets those, not the raw ledger. A citation test checks that every number in an explanation appears in the result it was given.

## 8. It runs itself

Jobs, each idempotent, logged, visible on the Jobs page and through `jobs.status`:

| Job | When | What it does |
| --- | --- | --- |
| Mail sync | Every 15 minutes | New messages by UID from each mailbox; classify; extract; capture paystub and statement attachments; auto-confirm where earned. |
| Bank sync | Daily at 05:30 and on demand | SimpleFIN pull; de-duplicate against exports; recurring detection; payment matching. |
| Import watch | On file arrival | CSV, OFX, paystub PDFs from `/imports`. |
| Re-plan | 06:00 on a payday, and after any confirmed change | Recompute the projection and the paycheck plan. |
| Brief | 07:00 daily | What is due and unfunded in three days, past due, discrepancies, new plans or advances, price increases, connectors that failed, approvals waiting. Delivered as a push (ntfy or Gotify from BoxPilot's catalog), shown in the app, served by `brief.today`. |
| Export reminder | When the aggregator's export email arrives | Reminds the owner to drop the file. |
| Self-check | Hourly | Each connector's last success; stale data warnings; the tunnel reachable; disk and database size. |

## 9. The web app: views and looks

A web app, installable on the phone and the desktop, served by the same container as the engine. Not Electron: a server-hosted app gains nothing from a wrapper, and a native shell can be added later in an afternoon if a window of its own is ever wanted. Phone first, because the payday routine happens on payday morning.

**Views** are home layouts. All eight from the mock-ups ship and the owner switches between them in Settings › Appearance; the River is the default. Every view renders the same engine results (`finance.snapshot`, `paycheck.plan`, `cashflow.forecast`) and never computes a number of its own:

1. **The River**: the sixty-day band of what is free to spend, with Today fixed, paydays, bills and the buffer floor.
2. **Paycheck windows**: one column per paycheck, filled bottom-up with what leaves it.
3. **Month grid**: the calendar with paydays, due dates and each week's low point.
4. **Where it goes**: the Sankey of one paycheck.
5. **Envelopes**: every obligation as an envelope with its fill level.
6. **Bill board**: cards that move right as they are paid.
7. **Transit map**: bills as stations on lines, paydays as interchanges.
8. **Mission control**: the dark console of tiles, jobs and connectors.

**Looks** are themes: light, dark and console (the dark tiles look), each a validated palette for the chart colours and the status colours, kept per device as BoxPilot does, so the phone and the wall screen can differ.

**Pages** beyond the home: **Today** (the brief and approvals; the phone home), **Calendar**, **Bills**, **Payments**, **Paystubs**, **Debts** (the mountain), **Subscriptions**, **Transactions**, **Accounts** (balances and net worth), **Reports** (spending by category, merchant and tag over time, comparisons, savings rate), **Confirm** (pending facts with their excerpts), **Mail** (search both mailboxes; open a message boxed), **Chat** (the advisor beside the engine's table, the reviewer's notes), **Connectors**, **Clients** (OAuth clients and API keys), **Jobs**, **Audit**, **Settings** (appearance, rules, goals, watchlists, models, redaction, time zone, send allowances).

**Visual rules.** One horizontal time line everywhere, Today fixed at the same place. Paydays are always blue, bills always orange, debt and advances always violet, what is safe to spend always the aqua band, the buffer always the grey floor; late and past due use the status colours with an icon and a word, never colour alone. One idea per screen, the picture first, the table under it. The chart palette is validated for colour-blindness in both modes with the data-viz validator before it ships.

## 10. Architecture and stack

The same shape as LLMCoach, which already ships to BigBox this way:

- **Backend**: Python 3.12, FastAPI, SQLModel over SQLite at `/data/financialauthority.db`, versioned migrations, the official `mcp` SDK mounted at `/mcp` with its OAuth authorization server, APScheduler for jobs, `httpx` for providers and SimpleFIN, `imaplib` for mail, `pypdf` with OCR fallback for paystubs and statements. Field-level AES-GCM for tokens, passwords and account numbers with a key from `FA_DATA_KEY`.
- **Frontend**: React 19, Vite, TypeScript, Tailwind 4, built into the image and served by the backend; a web app manifest and a service worker for the installed app; views in `frontend/src/views/` and looks in `frontend/src/looks/`, both registries the Settings page reads; charts as inline SVG drawn from the engine's results.
- **One image**: `ghcr.io/aes256afro/financialauthority`, public (it holds no data), port 8000 inside, `/data` for everything that must survive, `/imports` as a drop folder. A `HEALTHCHECK` on `/api/health`. A Proton Mail Bridge sidecar in the same compose project.
- **Tests**: pytest for the engine, parsers and connectors with injected clocks; vitest for the UI; a synthetic household fixture used by every test and by demo mode; a red-team mailbox fixture.
- **Versioning**: one version string in `backend/app/version.py`. Tags `vX.Y.Z` publish `:X.Y.Z` and `:latest`.

```
financialauthority/
  backend/app/        FastAPI app: api/, mcp/ (tools + oauth), engine/, connectors/, parsers/, jobs/, models/, migrations/
  backend/tests/      pytest, with fixtures/household/ and fixtures/mail/
  frontend/           Vite + React
  docker/Dockerfile   one image, frontend built in a first stage
  deploy/boxpilot/    the catalog manifest (app + Bridge sidecar)
  deploy/compose/     docker-compose.yml and .env.example for standalone installs
  docs/               plan, roadmap, access, connector and tool references
```

## 11. Deployment and always-on

**BoxPilot on BigBox** is the primary target. The app is a catalog entry: [deploy/boxpilot/financialauthority.yaml](../deploy/boxpilot/financialauthority.yaml) points at the published image, keeps `/data` backed up by BoxPilot, has secrets generated by BoxPilot at install, a `signIn` block, a connection to the Ollama app, and the Proton Mail Bridge as a sidecar whose state is backed up so a restore needs no new sign-in. BoxPilot restarts it, updates it, and rolls back an update that does not come up healthy.

**Published** through BoxPilot's Cloudflare Tunnel (its Tunnel tab: connect with an API token once, then Publish the app under a name on one of the owner's domains). The app's `FA_PUBLIC_URL` is set to that address and becomes the OAuth issuer. Inside the tailnet the same app answers directly.

**Independent** means the same image anywhere Docker runs: `deploy/compose/docker-compose.yml` with a `.env`, plus a `cloudflared` or Tailscale recipe for reach.

**Always-on means**: the host stays up (BoxPilot already watches it and the battery backup), the app restarts itself, the self-check job notices a connector or the tunnel failing and says so in the brief, and the Bridge sidecar keeps its session across restarts. The sources that are not always-on are the exports; the aggregator replaces them for live data, and the export is only for history.

## 12. Security and threat model

What we protect: the ledger, two mailboxes, paystubs, the aggregator token, the owner's preferences, the OAuth and API credentials. From whom: anyone on the internet, now that the app is published; anyone on the LAN who is not the owner; a cloud model provider; an attacker who can send the owner an email; a careless future commit.

- **Public surface**: only the web sign-in, `/mcp`, `/api`, `/oauth/*` metadata and endpoints, and `/api/health` are reachable. One route answers without a session or key: `/api/calendar/{token}.ics`, the read-only calendar feed of paydays and due dates, which needs a 256-bit token the owner creates in Exports (stored hashed, shown once, replaced or revoked in one tap), carries amounts only if the owner turns them on, and is limited to sixty requests an hour per address. OAuth tokens and API keys are hashed at rest, scoped (`read`, `confirm`, `approve`, `admin`), revocable, listed with last use. Model clients get `read` by default and never `approve`. Sign-in has lockout; every public route is rate-limited; Cloudflare's WAF sits in front; admin pages require a recent re-authentication.
- **Secrets**: `FA_PASSWORD`, `FA_SESSION_SECRET` and `FA_DATA_KEY` are generated by BoxPilot at install and live only in the container's environment. Connector credentials (the aggregator token, mailbox app passwords, the Bridge's local password) are entered in the UI and stored encrypted with `FA_DATA_KEY`. Nothing secret is logged.
- **Prompt injection**: mail bodies, attachments and statement text are boxed as untrusted data before a model sees them, extraction output is schema-validated and bounds-checked, a fact is pending until confirmed, and no model can approve a send. A red-team mailbox runs in CI.
- **Data minimisation**: cloud providers and clients get the redaction level the owner set; account numbers never leave; bodies are not stored unless the owner turns that on, and then for ninety days.
- **Backups**: BoxPilot backs up `/data` and the Bridge state and keeps encrypted copies off the server. Restore is rehearsed by BoxPilot's own restore test.
- **Exit**: export everything as CSV and JSON; delete everything with one confirmed action; rotate `FA_DATA_KEY` with a re-encrypt job; revoke every client at once.

## 13. Decisions made now

- **D1 Stack: Python backend, React frontend, one image.** The two existing data readers are Python on the `mcp` SDK, and LLMCoach proved the packaging on BigBox.
- **D2 Engine first, model second.** The engine must produce a correct plan with no model at all. This is what makes "any model" true.
- **D3 Proton Mail Bridge is a sidecar of the app on BigBox.** The Bridge on the owner's PC only listens on that PC and only while it runs. The sidecar's state volume is backed up so a restore needs no new sign-in. The one interactive sign-in at first start is the owner's (see ACCESS.md). Fallback if the community image proves fragile: a separate catalog app, then a relay from the PC.
- **D4 Gmail by IMAP app password.** OAuth in "testing" status expires refresh tokens weekly.
- **D5 Live bank data from free tiers only, in M1.** Plaid's Trial plan and Teller's developer tier each cost nothing for a household's handful of logins; Era Context's free tier already holds one bank and has room for the credit union; the banks' own alert mail fills the gaps the same quarter-hour. Capital One and the credit union are the two that matter first. No SimpleFIN, no paid plan, ever.
- **D6 Field-level encryption, not SQLCipher.**
- **D7 One owner in v1.** Household views are L4.
- **D8 Env names are prefixed `FA_`**, data at `/data`, imports at `/imports`, web port 8000 inside, BoxPilot host port 8430.
- **D9 The app is its own OAuth 2.1 server.** It is the only way claude.ai's custom connectors, including the phone, can sign in to a self-hosted MCP server, and it keeps the credential on BigBox.
- **D10 Public image, private repository.** BoxPilot's catalog cannot pull from a private registry. The image holds no data.
- **D11 Sends need an approval or a standing allowance; money never moves.** Section 2.
- **D12 Auto-confirm is earned per sender**, two confirmations by the owner, revocable.
- **D13 Every mock-up direction ships as a switchable view.** The owner asked to keep them all and choose in Settings. One engine, eight renderings, three looks.
- **D14 A web app, not Electron.** Installable on phone and desktop from the same container; a native shell only if a reason appears.
- **D15 Picture first.** Every screen leads with the visual that carries the answer; the numbers sit under it. The chart palette is validated, both modes.
- **D16 Two model roles: advisor and reviewer.** The advisor is local and free by default; the reviewer is the owner's choice, Claude by API recommended, and sees the plan and rules, never the mail. Neither can change a number.
- **D17 Rocket Money in, three ways, and out.** Export captured from mail, a button to the export page, a file drop; and the ledger exported in the same shape.

## 14. Risks and open questions

- **Publishing the app.** A public address is the price of "from anywhere, at all times". The mitigations in section 12 are not optional, and M3 does not ship without them.
- **Free tiers have edges.** Plaid's trial caps logins at ten and does not free a slot when one is removed; Teller's developer tier is rate-limited and its coverage of small credit unions is uneven; Era's free tier sees three weeks back and allows two logins. The app stacks all three and the alert mail, so no single edge is fatal, and the ledger keeps everything it has ever seen.
- **Fintech accounts** (payment apps, advance apps, some cards) often are not connectable anywhere. Those come from email and the manual form; the plan treats them as first-class.
- **Eight views is a lot of UI.** They share one component library and one data contract; a view is a layout over the same four engine results. M7 budgets the time, and the River ships first.
- **Paystub formats.** One parser per payroll provider, and the owner's provider is the first. The owner forwards PDFs today; that keeps working.
- **Email parsing varies by biller.** Rules per sender first, model extraction second, confirmation until earned.
- **A model that invents numbers.** Mitigated by D2 and the citation test.
- **Proton Bridge in a container** is community-supported. D3 names the fallbacks.
- **Scope.** The value is the three outputs in section 1 and the brief in section 8. The roadmap orders work so real data is reachable by Claude from the phone by the end of M3.
