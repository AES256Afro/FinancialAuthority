# FinancialAuthority: the plan

*Written 2026-10-14. The milestones are in [ROADMAP.md](ROADMAP.md). Rules for working in this repository are in [AGENTS.md](../AGENTS.md).*

## 1. What it is

FinancialAuthority is a self-hosted financial context engine. It reads everything about a household's money and hands one clean picture of it to any language model.

It reads:

- bank, card and loan **transactions and balances**;
- **paychecks**: who pays, how much, how often, when the next one lands;
- **bills** and their due dates, including which can be moved;
- **debts**, including the ones that live in third-party apps: Affirm, Klarna, Afterpay, PayPal Pay in 4, store cards, medical payment plans;
- **subscriptions**, with price history;
- the **emails** that carry bills, statements, installment schedules and payment reminders.

It gives back three things:

1. **The paycheck plan.** For the paycheck that just landed, or the next one: what must be paid before the one after it, what to set aside for bigger bills further out, and what is safe to spend.
2. **The purchase check.** "Can I buy X for $Y?" answered yes, wait, or no, naming the bill or buffer it would break, and when it would be fine.
3. **Billing-date changes.** Which due dates to move and to which day so that bills land after paydays rather than before them, with the request to each biller drafted and ready to send.

Any model can do the talking: a small model on BigBox through Ollama, Claude through Claude Desktop or Claude Code, Open WebUI, or anything that speaks the OpenAI tool format. FinancialAuthority exposes the same tools to all of them over MCP and a REST API, and has its own chat.

It never moves money. It reads, plans and drafts. The person pays.

## 2. Rules the design does not bend

- **Read-only, everywhere.** No bank logins are stored in the app. Transactions come from exports or a read-only aggregator token. Email is read over IMAP in read-only mode and never marked, moved or deleted. Drafts are written; nothing is sent.
- **Numbers come from the engine; words come from the model.** Every figure in a recommendation is computed by deterministic code with the rows it came from attached. The model explains, converses and asks follow-ups. A model that invents a number fails a test.
- **Email is untrusted input.** Messages are parsed into facts with a confidence score. A fact extracted by a model is *pending* until the owner confirms it, and only confirmed facts shape a plan. Message text is boxed as data before any model sees it, and nothing an email says can become a tool call.
- **Local first, minimum out.** Data lives on BigBox in SQLite under `/data`. Cloud models get only what the question needs, at a redaction level set per provider: full, merchants only, or aggregates only. Account numbers never leave the box.
- **Show the work.** Every plan, verdict and suggested change shows its arithmetic and the source rows. The audit log shows which key and which model read what.
- **It is not advice.** The app says so on every recommendation. It is a calculator with good manners.

## 3. Where the data comes from

| Source | How it gets in | What it yields | Status today |
| --- | --- | --- | --- |
| Rocket Money | CSV export dropped in `/imports` or uploaded. Rocket Money has no API; the export link arrives by email. | Transactions across every account Rocket Money sees, categories, account names, ignored flags. | A loader exists in `MCPManagement/rocketmoney-mcp`. Port it. |
| Any bank or card | CSV with column mapping, OFX/QFX. | Transactions, sometimes balances. | New. |
| SimpleFIN Bridge | Read-only aggregator token, cheap, no developer programme. Used by Actual Budget. | Daily transactions and balances. | Later (L1). |
| Plaid or Teller | Developer account, production approval. | Same, more institutions. | Later (L1). |
| Proton Mail | Through Proton Mail Bridge over IMAP. | Bills, statements, installment schedules, renewals, payday notices. | A reader exists in `MCPManagement/protonmail-mcp`. Port it. Bridge must run on BigBox for the server to reach it (decision D3). |
| Gmail | IMAP with an app password first. OAuth later. | Same. | New. OAuth in "testing" status expires refresh tokens after seven days, which is wrong for a self-hosted app, so app passwords come first. |
| Any mailbox | Generic IMAP. | Same. | New. |
| Third-party debt apps | No public APIs. Derived from transactions (equal amounts, same merchant, two-week spacing), from emails (purchase confirmations carry the schedule), and from a thirty-second manual form. | BNPL plans with installment schedules; card and loan balances, APRs, minimums. | New. |
| Credit reports | Credit Karma's connector is reachable only from a Claude session; statement import otherwise. | Utilisation, balances, inquiries. | Later (L2). |
| Era Context | MCP and REST aggregator with bank connections. | Transactions and balances as an upstream. | Later (L6). |

## 4. The model of the money

The entities, in the order the engine needs them:

- **Account**: bank, card, loan, BNPL app, cash. Type, institution, last four (encrypted), current balance and when it was last known.
- **Transaction**: date, account, merchant (cleaned), raw description, amount (positive is money out), category, flags: transfer, card payment, income, ignored. Source file and row hash for de-duplication.
- **RecurringSeries**: merchant, cadence (weekly, biweekly, monthly, quarterly, yearly), typical amount and tolerance, next expected date. The seed for bills, subscriptions and paychecks.
- **Income**: employer or payer, cadence, net amount, next date, account it lands in. Confirmed from a series or entered.
- **Bill**: biller, amount (fixed or estimated from history), due day, autopay and the account it draws from, grace days, whether the due date can be moved and within what window. Linked to the series and to confirmed email facts.
- **Debt**: card, loan, medical plan: balance, APR, minimum payment, statement day, due day. Linked to an account where one exists.
- **InstallmentPlan**: a BNPL purchase: provider, merchant, total, schedule of installments with dates and amounts, what is paid. A plan is a Debt with a schedule.
- **Subscription**: a bill that is a service: price history, last charge, category, the duplicate it may be of.
- **MessageRef**: a mailbox message by folder, UID and hash: sender, subject, date. The body is not stored by default; a redacted excerpt is kept with each fact.
- **ExtractedFact**: kind (bill due, statement, installment schedule, renewal, price change, payday, late notice), fields, confidence, status (pending, confirmed, rejected), the message and excerpt it came from, and which rule or model produced it.
- **Rule**: an owner preference the engine obeys: minimum buffer, pay the card in full, never touch this account, category targets, goal priority.
- **Goal**: emergency fund, payoff, saving for a thing: target, priority, pace.
- **Recommendation**: kind (paycheck plan, purchase check, billing-date change), inputs, outputs, explanation, which key and model asked, and the owner's feedback.
- **AuditEntry**: every tool call: key, model, tool, argument hash, rows touched, redaction level.

## 5. How a model gets in

One access layer, three doors, the same tools behind each:

- **MCP** at `/mcp` (streamable HTTP) with a stdio launcher for local clients. Bearer API keys. Tool annotations mark every read as read-only. This is how Claude Desktop, Claude Code and any MCP client connect, over Tailscale from anywhere.
- **REST** under `/api/v1` mirroring the tools, for scripts and for chat apps without MCP.
- **Tool schemas** at `/api/tools/openai.json` and `/api/tools/anthropic.json`, plus a prompt pack (a system prompt that explains the tools and the rules) for Open WebUI, LibreChat and home-grown agents.

The tools, by milestone:

| Tool | Returns | From |
| --- | --- | --- |
| `transactions.search` | Rows by date, merchant, category, amount, account. | M1 |
| `spending.summary` | Totals by category and merchant for a period, period over period. | M1 |
| `recurring.list` | Series with cadence and next expected date. | M1 |
| `finance.snapshot` | Balances, next paycheck, bills due before it, debts, pending facts: one call that answers most questions. | M2 |
| `bills.upcoming`, `debts.list`, `subscriptions.list`, `installments.list` | The obligation registers. | M2 |
| `facts.pending`, `facts.confirm` | What needs a human; confirming needs the `confirm` scope. | M4 |
| `cashflow.forecast` | Daily projected balance per account for N days, with the events that move it. | M5 |
| `paycheck.plan` | The plan for a paycheck with every line's source. | M5 |
| `purchase.check` | Verdict, what it breaks, when it would be fine, the BNPL variant. | M5 |
| `billing_dates.suggest` | The ranked change list with before-and-after minimum balances. | M5 |
| `notes.remember` | A preference the owner stated in chat, stored as a pending rule. | M5 |

Built-in chat uses the same tools through a provider layer: Ollama on BigBox by default (reached at `host.docker.internal:11434`, as LLMCoach does), any OpenAI-compatible server (llama.cpp, vLLM, LocalAI, OpenAI), and Anthropic. Each provider has a redaction level. A 3 to 8 B local model can give a correct paycheck plan because the plan is computed before the model speaks; the model's job is to read it out and answer "why".

BoxPilot's own agents cannot call an app's API today (their tools are a fixed catalog and their connectors are uploads, folders, Notion, Slack and SearXNG). Two paths: FinancialAuthority uses BoxPilot's Ollama, which works on day one; and later a `finance.*` read tool in BoxPilot behind a stored credential (L5), which is a BoxPilot change.

## 6. The guidance engine

All deterministic, all tested against a synthetic household and golden outputs.

**Cash-flow projection.** For each account, a daily balance for the next 90 days from the current balance, confirmed bills, recurring series, paychecks, minimum payments and installments. A what-if layer adds hypothetical events without saving them. Everything below is a question asked of this projection.

**Paycheck plan.** The window is from this paycheck to the next. Inside it: bills and installments due, minimums due, autopay already committed. Beyond it: set-asides for bills larger than one paycheck comfortably carries, spread over the paychecks until they are due. Then the buffer rule, then goals in priority order, then safe-to-spend is what is left. Every line names its bill, series or rule. A "don't buy" list sits under it (below).

**Purchase check.** Add the purchase to the projection on its date. It passes when the projected balance stays above the buffer through the next paycheck and one more, and no bill becomes unfunded. If it fails, the answer names the first thing it breaks and the first date it would pass. The BNPL variant adds the installments instead of the whole price and checks again, and says what the plan adds to the next three paychecks.

**"Don't buy" list.** Standing flags from rules and the registers: anything that would breach the buffer before the next paycheck, categories over their target this period, a second subscription in a category that already has one, a price increase detected from email, more than N active installment plans, and a card the owner has said to pay in full that is carrying a balance.

**Billing-date optimiser.** Inputs: paychecks, bills with a movable flag and allowed window, the buffer. Score for a schedule: the sum of shortfalls below the buffer over the projection, plus a penalty for each bill due in the three days before a payday, plus a penalty when more than a set share of one paycheck is due in any three-day window. Movable bills are few and candidate days are at most 28, so coordinate descent over the bills with a few sweeps is enough and is deterministic. Output: changes ranked by benefit, each with the before-and-after minimum balance, the instruction for that biller (where the setting lives, whether the current cycle must be paid first), and a draft message when the biller takes one. Drafts go to Proton's Drafts folder or Gmail's drafts; they are never sent.

**Explanations.** The engine returns structured results with sources. The model gets those, not the raw ledger, when it is asked to explain. A citation test checks that every number in an explanation appears in the result it was given.

## 7. The web app

Phone first, because the paycheck routine happens on payday morning. Pages:

- **This paycheck**: safe-to-spend, what is due, set-asides, the don't-buy list, one button to re-plan.
- **Calendar**: paydays and due dates on one grid, 60 days ahead, with the projected low point of each week.
- **Bills**, **Debts** (payoff order and the interest each month costs), **Subscriptions**, **Transactions**.
- **Confirm**: the inbox of pending facts from email and detection, with the excerpt that produced each.
- **Chat**: the built-in chat with a model switcher; every page has "ask about this".
- **Connectors**, **API keys**, **Audit**, **Settings** (rules, goals, providers, redaction, time zone).

Installable to a phone's home screen. Light and dark.

## 8. Architecture and stack

The same shape as LLMCoach, which already ships to BigBox this way:

- **Backend**: Python 3.12, FastAPI, SQLModel over SQLite at `/data/financialauthority.db`, versioned migrations, the official `mcp` SDK mounted at `/mcp`, `httpx` for model providers, `imaplib` for mail. Field-level AES-GCM for tokens, passwords and account numbers with a key from `FA_DATA_KEY`.
- **Frontend**: React 19, Vite, TypeScript, Tailwind 4, built into the image and served by the backend.
- **One image**: `ghcr.io/aes256afro/financialauthority`, port 8000 inside, `/data` for everything that must survive, `/imports` as a drop folder for exports. A `HEALTHCHECK` on `/api/health`.
- **Tests**: pytest for the engine and connectors with injected clocks; vitest for the UI; a synthetic household fixture used by every test and by demo mode.
- **Versioning**: one version string in `backend/app/version.py`, read by the image build and shown in the UI. Tags `vX.Y.Z` publish `:X.Y.Z` and `:latest`.

```
financialauthority/
  backend/app/        FastAPI app: api/, mcp/, engine/, connectors/, models/, migrations/
  backend/tests/      pytest, with fixtures/household/
  frontend/           Vite + React
  docker/Dockerfile   one image, frontend built in a first stage
  deploy/boxpilot/    the catalog manifest
  deploy/compose/     docker-compose.yml and .env.example for standalone installs
  docs/               this plan, the roadmap, connector and tool references
```

## 9. Deployment

**BoxPilot on BigBox** is the primary target. The app is a catalog entry: a manifest in `deploy/boxpilot/financialauthority.yaml` (a draft is already there) pointing at the published image, with `/data` backed up by BoxPilot, secrets generated by BoxPilot at install, a `signIn` block so the card shows the way in, and a connection to the Ollama app. Reach is LAN or tailnet, never public. Install, update, back up and restore then work the way they do for every other app.

**Independent** means the same image anywhere Docker runs: `deploy/compose/docker-compose.yml` with a `.env`. For reach from outside the LAN the recipes are Tailscale (nothing to open) or a Cloudflare Tunnel with Access in front of it. There is no reason to run this on a serverless platform: it holds a mailbox connection, a SQLite file and an encrypted store, and it wants to sit next to a local model.

## 10. Security and threat model

What we protect: the ledger, the mailbox connection, the aggregator tokens, the owner's preferences. From whom: anyone on the LAN who is not the owner, a cloud model provider, an attacker who can send the owner an email, and a careless future commit.

- **Secrets**: `FA_PASSWORD`, `FA_SESSION_SECRET` and `FA_DATA_KEY` are generated by BoxPilot at install and live only in the container's environment. Connector credentials are entered in the UI and stored encrypted with `FA_DATA_KEY`. Nothing secret is logged.
- **Sign-in**: one owner, password, session cookie with CSRF protection, lockout after repeated failures. Passkeys later.
- **API keys**: hashed at rest, scoped (`read`, `confirm`, `admin`), each with a redaction level, revocable, listed with last use.
- **Prompt injection**: mail bodies and statement text are boxed as untrusted data before a model sees them, extraction output is schema-validated and bounds-checked, and a fact is pending until a human confirms it. A red-team suite of malicious emails runs in CI. A model holding a `read` key cannot confirm anything.
- **Data minimisation**: cloud providers get the redaction level the owner set; account numbers never leave; bodies are not stored unless the owner turns that on, and then for ninety days.
- **Network**: LAN or tailnet. The compose file binds to the host only. The catalog manifest never exposes the port publicly.
- **Backups**: BoxPilot backs up `/data` and keeps encrypted copies off the server. Restore is rehearsed by BoxPilot's own restore test.
- **Exit**: export everything as CSV and JSON; delete everything with one confirmed action; rotate `FA_DATA_KEY` with a re-encrypt job.

## 11. Decisions made now

- **D1 Stack: Python backend, React frontend, one image.** The two existing data readers are Python on the `mcp` SDK and LLMCoach proved the packaging on BigBox. Node and Express, as BoxPilot uses, would mean rewriting both readers.
- **D2 Engine first, model second.** The engine must produce a correct plan with no model at all; the model is an interface to it. This is what makes "any model" true, small local ones included.
- **D3 Proton Mail Bridge runs on BigBox**, in its own container, so the server can reach it; the Bridge on the Windows PC only listens on that PC. The community headless Bridge image needs one interactive sign-in at first start. It becomes its own BoxPilot catalog app with loopback exposure (M7.4). If that proves fragile, the fallback is a small relay on the PC over Tailscale.
- **D4 Gmail by IMAP app password in v1.** OAuth in "testing" status expires refresh tokens weekly; app verification is for a product, not a home server.
- **D5 Transactions from exports in v1; live sync later.** Rocket Money's CSV already covers every account the owner has linked there. SimpleFIN is the first live source when the time comes: cheap, read-only, no developer programme.
- **D6 Field-level encryption, not SQLCipher.** Secrets and account numbers are encrypted in their columns; BoxPilot's backups are encrypted off the server. SQLCipher stays an option if the threat model changes.
- **D7 One owner in v1.** Household views and a second sign-in are L4.
- **D8 Env names are prefixed `FA_`**, data is at `/data`, imports at `/imports`, the web port inside is 8000 and BoxPilot's host port is 8430 (free in the catalog today).

## 12. Risks and open questions

- **Email parsing varies by biller.** Rules per sender first, model extraction second, confirmation always. Expect to add a sender rule the first time each biller shows up.
- **BNPL has no APIs.** Detection from transactions and emails will miss some plans; the manual form must take under a minute.
- **Rocket Money's export is manual.** The email connector can notice the export email and remind; downloading the file stays a human step. A BoxPilot SMB share on `/imports` makes it a drag and drop.
- **A model that invents numbers.** Mitigated by D2 and the citation test. The chat shows the engine's table beside the model's prose.
- **Proton Bridge on a server** is community-supported, not Proton-supported. D3 names the fallback.
- **Scope.** The temptation is dashboards. The value is the three outputs in section 1; the roadmap orders work so that real data is queryable by any model by the end of M3 and the paycheck plan exists by the end of M5.
- **Whose money?** One owner in v1. Shared accounts with a partner are L4 and will need per-account visibility rules.
