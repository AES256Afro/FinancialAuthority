# FinancialAuthority roadmap

*Written 2026-10-14. The thinking behind it is in [PLAN.md](PLAN.md). Marks: ☐ planned, 🔄 in progress, ✅ done (version). Effort is for one developer working with coding agents.*

Order matters more than dates. Real data is queryable by any model at the end of M3. The paycheck plan exists at the end of M5. v1.0 ships at the end of M8, about fifteen weeks in.

## M0 — Foundations (1 week)

- ☐ **M0.1 Repository and skeleton.** `backend/` (FastAPI), `frontend/` (Vite, React, TypeScript, Tailwind), `docker/Dockerfile` that builds the frontend in a first stage, `deploy/compose/` for standalone installs, CI that tests, builds the image on every push and publishes `ghcr.io/aes256afro/financialauthority` on a `v*` tag. One version string in `backend/app/version.py`.
- ☐ **M0.2 Owner sign-in.** Username and password from `FA_USERNAME` and `FA_PASSWORD`, session cookie signed with `FA_SESSION_SECRET`, CSRF, lockout after repeated failures. An API keys table with scopes `read`, `confirm`, `admin`.
- ☐ **M0.3 Data layer.** SQLite at `/data/financialauthority.db`, versioned migrations, encrypted columns (AES-GCM keyed by `FA_DATA_KEY`) for tokens, passwords and account numbers.
- ☐ **M0.4 Health, logs, time.** `/api/health` for the Docker `HEALTHCHECK`, structured logs that never carry secrets, every date computed in the owner's time zone (`TZ`).
- ☐ **M0.5 The synthetic household.** A generated twelve-month dataset: two paychecks a month, fourteen bills, two cards, three installment plans, a price increase, a duplicate subscription. Every test and demo mode run on it.

Done when: the image builds in CI, runs on BigBox by hand with `docker compose`, sign-in works, health is green, and demo mode shows the fixture.

## M1 — The ledger (2 weeks)

- ☐ **M1.1 Import.** Rocket Money CSV, ported from `MCPManagement/rocketmoney-mcp` (its columns, de-duplication, newest-export-wins, and the is-spending rule). Generic CSV with column mapping. OFX and QFX. Files arrive by upload or in `/imports`, which is watched.
- ☐ **M1.2 Accounts and balances.** Accounts created from imports, balances entered or taken from the export, balance snapshots over time.
- ☐ **M1.3 Normalisation.** Merchant cleanup, categories seeded from Rocket Money's and re-mappable, flags for transfers, card payments, income and ignored rows.
- ☐ **M1.4 Recurring detection.** Port the cadence detection (weekly, biweekly, monthly, quarterly, yearly) into `RecurringSeries` with amount tolerance and next expected date. The seed for bills, subscriptions and paychecks.
- ☐ **M1.5 Paycheck detection.** Recurring income becomes `Income` after the owner confirms employer, cadence, net amount and next date.
- ☐ **M1.6 Read tools, first set.** `transactions.search`, `spending.summary`, `recurring.list`, with the same meaning as the existing MCP server so nothing is lost.

Done when: the owner's real exports load cleanly, the recurring series and paychecks found match what Rocket Money shows, and the fixture tests pass.

## M2 — Obligations: bills, debts, subscriptions (1.5 weeks)

- ☐ **M2.1 Bill registry.** Each recurring series is confirmed as a bill, a subscription, income, or ignored. Manual add. Fields: biller, amount (fixed or estimated from history), due day, autopay and the account it draws from, grace days, movable and the allowed window.
- ☐ **M2.2 Debts.** Cards, loans, medical plans: balance, APR, minimum, statement day, due day, linked to an account where one exists.
- ☐ **M2.3 Installment plans.** Affirm, Klarna, Afterpay, PayPal Pay in 4 and the like: provider, merchant, total, schedule. Detected from transactions (equal amounts, same merchant, two-week spacing) and confirmed; or entered in a thirty-second form.
- ☐ **M2.4 Subscriptions.** Price history, last charge, category, and duplicate detection (two music services, two video services).
- ☐ **M2.5 Calendar.** Paydays and due dates on one month grid, sixty days ahead. The first real screen.
- ☐ **M2.6 Tools.** `finance.snapshot`, `bills.upcoming`, `debts.list`, `subscriptions.list`, `installments.list`.

Done when: every recurring charge in the fixture and in the real data is classified, and the calendar shows the next sixty days correctly.

## M3 — Any model gets in (1.5 weeks)

- ☐ **M3.1 MCP server.** Streamable HTTP at `/mcp` plus a stdio launcher, bearer API keys, read-only annotations on every read tool.
- ☐ **M3.2 REST and schemas.** `/api/v1` mirrors the tools. `/api/tools/openai.json`, `/api/tools/anthropic.json`, and a prompt pack for chat apps that have no MCP.
- ☐ **M3.3 Providers.** Ollama (default, `host.docker.internal:11434`), any OpenAI-compatible server, Anthropic. A settings page with a test button per provider.
- ☐ **M3.4 Built-in chat.** Streaming, tools on, each answer shows the rows it used. Works with a 3 to 8 B local model on the fixture.
- ☐ **M3.5 Redaction and audit.** A redaction level per key and per provider (full, merchants only, aggregates only). An audit log of every tool call: key, model, tool, argument hash, rows, with a page.
- ☐ **M3.6 Connect the clients.** Claude Desktop and Claude Code over Tailscale, Open WebUI on BigBox. Both documented with the exact commands.

Done when: Claude Code and a local Ollama model both answer "what is due before my next paycheck?" correctly from the real data, and the audit log shows both calls.

## M4 — Email ingestion (2 weeks)

- ☐ **M4.1 Generic IMAP connector.** Host, port, user, app password, folders to watch. Read-only: never marks, moves or deletes. Incremental by UID. Stores message references and hashes, not bodies, unless the owner turns bodies on.
- ☐ **M4.2 Proton Mail through Bridge.** Port the reader from `MCPManagement/protonmail-mcp`, including draft creation. Bridge runs on BigBox in its own container (decision D3 in the plan).
- ☐ **M4.3 Gmail.** IMAP with an app password. OAuth stays off the roadmap until it can be done without weekly re-authorisation.
- ☐ **M4.4 Classifier.** Bill due, statement, installment schedule, renewal or price change, payday, late notice, or none. Sender and subject rules first, a model second, with confidence.
- ☐ **M4.5 Extraction.** Amount, due date, account last four, period, installment schedule, into pending `ExtractedFact` rows with the excerpt that produced them. Schema and bounds validation. The body is boxed as untrusted data before any model sees it.
- ☐ **M4.6 Confirm inbox.** The owner confirms or rejects; confirmed facts update bills, debts and plans; a confirmed sender becomes a rule so the next message from it needs no model.
- ☐ **M4.7 Rocket Money export reminder.** The connector recognises the export email and reminds the owner to drop the CSV. Nothing is downloaded automatically.

Done when: thirty days of the real mailbox yields bills with the right due dates and amounts for the known billers at ninety percent precision after confirmation, and the red-team suite shows that no email can reach a tool call as an instruction.

## M5 — The guidance engine (2.5 weeks)

- ☐ **M5.1 Cash-flow projection.** Daily balance per account for ninety days from confirmed bills, series, paychecks, minimums and installments, with a what-if layer.
- ☐ **M5.2 Paycheck plan.** Window, obligations, set-asides, minimums, buffer, goals, safe-to-spend, with every line's source.
- ☐ **M5.3 Purchase check.** Yes, wait or no; the first thing it breaks; the first date it would pass; the BNPL variant.
- ☐ **M5.4 The don't-buy list.** Buffer breaches, categories over target, duplicate subscriptions, price increases, too many active plans, a pay-in-full card carrying a balance.
- ☐ **M5.5 Billing-date optimiser.** The objective and search from the plan; a ranked change list with before-and-after minimum balances, per-biller instructions, and drafts in Proton or Gmail where the biller takes one.
- ☐ **M5.6 Rules and goals.** Buffer, pay-in-full, never-touch accounts, category targets, goal priorities. `notes.remember` stores a preference stated in chat as a pending rule.
- ☐ **M5.7 Evaluation.** Golden outputs on the fixture; property tests (a plan never spends below the buffer, totals reconcile, the optimiser never worsens the minimum balance); the citation test for explanations.
- ☐ **M5.8 Tools.** `cashflow.forecast`, `paycheck.plan`, `purchase.check`, `billing_dates.suggest`, `notes.remember`.

Done when: on the fixture the plan matches its golden output, the optimiser finds the known better schedule, and a local 8 B model explains a plan without a number that is not in the engine's result.

## M6 — The web app, finished (2 weeks)

- ☐ **M6.1 This paycheck.** Safe-to-spend, what is due, set-asides, the don't-buy list, one button to re-plan. Phone first.
- ☐ **M6.2 The registers.** Calendar with the week's low point, Bills, Debts with payoff order and monthly interest, Subscriptions, Transactions.
- ☐ **M6.3 Confirm, Connectors, API keys, Audit.** Finished, not skeletons.
- ☐ **M6.4 Chat.** Model switcher, "ask about this" from every page, the engine's table beside the model's prose.
- ☐ **M6.5 Installable.** Web app manifest and icons, light and dark.

Done when: everything the tools can answer is visible without a model, and the payday routine takes under two minutes on a phone.

## M7 — Ship: BoxPilot on BigBox, or anywhere (1 week)

- ☐ **M7.1 Image.** Published on a `v*` tag as `:X.Y.Z` and `:latest`.
- ☐ **M7.2 Catalog.** `deploy/boxpilot/financialauthority.yaml` pinned to the release, sent as a pull request to BoxPilot. Install from the catalog, back up `/data`, restore it somewhere else, update to the next release.
- ☐ **M7.3 Standalone.** `deploy/compose/docker-compose.yml` with `.env.example`; recipes for Tailscale and for a Cloudflare Tunnel with Access.
- ☐ **M7.4 Proton Mail Bridge as a catalog app.** Loopback exposure, one-time sign-in documented. Only if D3 held.
- ☐ **M7.5 Docs.** Release notes, upgrade and backup notes, the connector reference.

Done when: a fresh install from the BoxPilot catalog reaches "This paycheck" with real data in under thirty minutes.

## M8 — Hardening and v1.0 (1.5 weeks)

- ☐ **M8.1 Threat model written down**, secret handling reviewed, dependency audit in CI.
- ☐ **M8.2 Red team.** The malicious-email suite grows; tool-misuse tests for every scope; rate limits and lockout verified.
- ☐ **M8.3 Keys and exit.** `FA_DATA_KEY` rotation with a re-encrypt job, export everything as CSV and JSON, delete everything with one confirmed action.
- ☐ **M8.4 Scale.** Five years of transactions load and query without the UI noticing.
- ☐ **M8.5 Docs.** README, tool reference, "how the numbers are made".

Done when: `v1.0.0` is tagged, every item in the security checklist is ticked, and every recommendation carries its sources and the not-advice line.

## Later

- ☐ **L1 Live bank sync.** SimpleFIN first; Plaid or Teller after.
- ☐ **L2 Credit.** Statement import, utilisation targets, inquiries. Credit Karma's connector is Claude-only, so it is a Claude session that writes into FinancialAuthority, not a direct integration.
- ☐ **L3 Calendar and notifications.** Paydays and due dates written to a calendar; push through BoxPilot or ntfy; a weekly digest.
- ☐ **L4 Household.** A second owner, shared and private accounts, per-account visibility.
- ☐ **L5 BoxPilot agents.** A `finance.*` read tool for BoxPilot's agents behind a stored credential. A BoxPilot change.
- ☐ **L6 Era Context** as an upstream connector over MCP or REST.
- ☐ **L7 Automation.** Billing-date drafts created on approval, reminders before due dates, a re-plan the morning a paycheck lands.

## Suggested first two weeks

All of M0, then M1.1 and M1.4 (the two ports from `MCPManagement`) so the owner's real transactions are in the app by day ten, then M1.5 so the next paycheck is known. Nothing in that fortnight needs a model.
