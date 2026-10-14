# FinancialAuthority

A self-hosted, visual-first financial planner that runs itself. It reads live bank and card transactions, paystubs, monthly bills, payments, debts (including the ones in third-party apps such as Affirm, Klarna, Afterpay and paycheck-advance apps), subscriptions and both of the owner's mailboxes, keeps all of it current without being asked, and hands one model of it to any language model, from anywhere, at any time, over MCP, a REST API and its own chat.

It answers three questions:

1. **What should this paycheck go on?** What the advance apps take first, what must be paid before the next paycheck, what to set aside, what is safe to spend.
2. **Should I buy this?** Yes, wait, or no, with the bill or buffer it would break.
3. **Which billing dates should move?** So bills land after paydays, with the request to each biller drafted and sent once approved.

Every morning it says what needs attention. Eight home views and three looks are switchable in Settings › Appearance; all of them render the same numbers. It never moves money. It reads, plans, drafts, and sends only with an approval. It pays nobody to see your own money.

**Status: M0 through M5, first cuts.** The engine's snapshot (windows, calendar, payments, paystubs, the usual pace, the brief), the purchase check and the billing-date optimiser, reports, net worth and the debt mountain; the Rocket Money loader with recurring detection and the export captured from mail; payment matching; live bank links through Plaid and Teller; all eight home views and the Today, Bills, Debts, Accounts, Reports, Paystubs, Mail, Ledger and Chat pages; the Needs-you flow; owner sign-in, the OAuth 2.1 server for claude.ai and Claude Code, API keys, the audit log; the scheduler; the MCP endpoint with sixteen tools; the mail connector for Proton (through the Bridge), Gmail and any IMAP mailbox with a rules-first classifier; and the two model roles, the advisor in the built-in chat and the reviewer with one-tap notes, over Ollama, any OpenAI-compatible server or Claude by API. Not deployed yet; that needs a shell on BigBox and the owner's keys.

- [docs/PLAN.md](docs/PLAN.md): what it is, how it operates, where the data comes from, the engine, reach from anywhere, views and looks, security, decisions.
- [docs/ROADMAP.md](docs/ROADMAP.md): milestones M0 to M8 and what comes later.
- [docs/FEATURES.md](docs/FEATURES.md): what Monarch, Empower, Simplifi, YNAB, Copilot, Cleo, Origin and Rocket Money do, and what this app takes from them.
- [docs/ACCESS.md](docs/ACCESS.md): what the coding agent already reaches and the few things only the owner can grant.
- [docs/mockups/](docs/mockups/): the eighteen mock-up artboards.
- [AGENTS.md](AGENTS.md): rules for working in this repository, for people and coding agents.
- [deploy/boxpilot/financialauthority.yaml](deploy/boxpilot/financialauthority.yaml): the draft BoxPilot catalog manifest, with the Proton Mail Bridge as a sidecar.

## Run it

Backend (Python 3.12 or newer, [uv](https://docs.astral.sh/uv/)):

```bash
cd backend && uv sync && uv run uvicorn app.main:app --port 8000
```

With `FA_DEMO=true` (see `deploy/compose/.env.example` for every variable) the synthetic household loads, so the screens can be seen without real data. With no `FA_PASSWORD`, sign-in is off; that is only for a developer's own machine.

Frontend (Node 24):

```bash
cd frontend && npm ci && npm run dev
```

The dev server proxies `/api` and `/mcp` to the backend. `npm run build` writes `frontend/dist`, which the backend serves when it exists.

Tests: `uv run pytest` in `backend/`, `npm test` in `frontend/`.

One image, published by CI as `ghcr.io/aes256afro/financialauthority` (`:edge` from main, `:X.Y.Z` from tags): `deploy/compose/docker-compose.yml` runs it anywhere; on BigBox it is a [BoxPilot](https://github.com/AES256Afro/BoxPilot) catalog app.

## Any model

The app is its own OAuth 2.1 server. claude.ai (web, desktop, phone): Settings › Connectors › Add custom connector with `https://<your-address>/mcp`; the app's consent page appears once. Claude Code:

```bash
claude mcp add --transport http financialauthority http://<host>:8430/mcp
```

Anything without a browser takes an API key from Settings › Clients as a bearer token instead.

The tools: `finance_snapshot`, `brief_today`, `purchase_check`, `watchlist`, `billing_dates_suggest`, `spending_summary`, `debts_list`, `accounts_list`, `transactions_search`, `recurring_list`, `bills_list`, `payments_list`, `paystubs_list`, `mail_search`, `mail_read`, `facts_pending`, `facts_confirm`, `jobs_status`. The REST mirror is under `/api/v1`.

## Two models, neither of which calculates

Settings › Models holds two roles. The **advisor** answers in the Chat page, with the plan already computed in its prompt and the read tools at hand; Ollama on the host by default, so it costs nothing. The **reviewer** is a second opinion: on payday, after a confirmed change and on request it reads the plan and your rules, never the mail, and returns notes you apply with one tap. Claude by API is the recommended reviewer; any OpenAI-compatible model works; keys are stored encrypted. Every figure either model states is checked against what the engine returned, and anything else is marked.

FinancialAuthority is not financial advice. It is a calculator that shows its work.
