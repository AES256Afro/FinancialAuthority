# FinancialAuthority

A self-hosted, visual-first financial planner that runs itself. It reads live bank and card transactions, paystubs, monthly bills, payments, debts (including the ones in third-party apps such as Affirm, Klarna, Afterpay and paycheck-advance apps), subscriptions and both of the owner's mailboxes, keeps all of it current without being asked, and hands one model of it to any language model, from anywhere, at any time, over MCP, a REST API and its own chat.

It answers three questions:

1. **What should this paycheck go on?** What the advance apps take first, what must be paid before the next paycheck, what to set aside, what is safe to spend.
2. **Should I buy this?** Yes, wait, or no, with the bill or buffer it would break.
3. **Which billing dates should move?** So bills land after paydays, with the request to each biller drafted and sent once approved.

Every morning it says what needs attention. Eight home views and three looks are switchable in Settings › Appearance; all of them render the same numbers. Retirement and investment accounts (a 401(k), an IRA, a brokerage account, an HSA) come in by signing in at the vendor through Plaid's investments product, or by hand from each statement. Everything the app still needs from you is a tappable next step on every screen, landing on the exact button or box. It never moves money. It reads, plans, drafts, and sends only with an approval. It pays nobody to see your own money.

**Status:** running daily for its owner since October 2026; version in `backend/app/version.py`. **To install it, start with [docs/SETUP.md](docs/SETUP.md)**: BoxPilot, Docker Compose on Linux, or Docker Desktop on Windows or Mac, then mail (the Proton Mail Bridge step by step), your bank, your phone and Claude.

- [docs/SETUP.md](docs/SETUP.md): install and set up, step by step, for each kind of server.
- [docs/PLAN.md](docs/PLAN.md): what it is, how it operates, where the data comes from, the engine, reach from anywhere, views and looks, security, decisions.
- [docs/ROADMAP.md](docs/ROADMAP.md): milestones M0 to M8 and what comes later.
- [docs/FEATURES.md](docs/FEATURES.md): what Monarch, Empower, Simplifi, YNAB, Copilot, Cleo, Origin and Rocket Money do, and what this app takes from them.
- [docs/ACCESS.md](docs/ACCESS.md): what the coding agent already reaches and the few things only the owner can grant.
- [docs/DEMO.md](docs/DEMO.md): the shareable demo, the same screens over a synthetic household, at demo.aes256afro.com and cybercard.net, and how to regenerate it.
- [docs/SECURITY.md](docs/SECURITY.md): what the app holds, who can reach what, each risk and its control.
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

The app is its own OAuth 2.1 server. claude.ai (web, desktop, phone): Customize › Connectors › + Add › Add custom connector with `https://<your-address>/mcp`; the app's consent page appears once. Claude Code:

```bash
claude mcp add --transport http financialauthority http://<host>:8430/mcp
```

Anything without a browser takes an API key from Settings › Clients as a bearer token instead.

The tools: `finance_snapshot`, `brief_today`, `next_steps`, `purchase_check`, `watchlist`, `billing_dates_suggest`, `spending_summary`, `debts_list`, `investments_list`, `accounts_list`, `transactions_search`, `recurring_list`, `bills_list`, `payments_list`, `paystubs_list`, `mail_search`, `mail_read`, `mail_attachment`, `facts_pending`, `facts_confirm`, `mail_send_request`, `jobs_status`. The REST mirror is under `/api/v1`.

## Two models, neither of which calculates

Settings › Models holds two roles. The **advisor** answers in the Chat page, with the plan already computed in its prompt and the read tools at hand; Ollama on the host by default, so it costs nothing. The **reviewer** is a second opinion: on payday, after a confirmed change and on request it reads the plan and your rules, never the mail, and returns notes you apply with one tap. Claude by API is the recommended reviewer; any OpenAI-compatible model works; keys are stored encrypted. Every figure either model states is checked against what the engine returned, and anything else is marked.

FinancialAuthority is not financial advice. It is a calculator that shows its work.
