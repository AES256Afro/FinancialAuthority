# FinancialAuthority

A self-hosted, visual-first financial planner that runs itself. It reads live bank and card transactions, paystubs, monthly bills, payments, debts (including the ones in third-party apps such as Affirm, Klarna, Afterpay and paycheck-advance apps), subscriptions and both of the owner's mailboxes, keeps all of it current without being asked, and hands one model of it to any language model, from anywhere, at any time, over MCP, a REST API and its own chat.

It answers three questions:

1. **What should this paycheck go on?** What the advance apps take first, what must be paid before the next paycheck, what to set aside, what is safe to spend.
2. **Should I buy this?** Yes, wait, or no, with the bill or buffer it would break.
3. **Which billing dates should move?** So bills land after paydays, with the request to each biller drafted and sent once approved.

Every morning it says what needs attention. Eight home views and three looks are switchable in Settings › Appearance; all of them render the same numbers. It never moves money. It reads, plans, drafts, and sends only with an approval. It pays nobody to see your own money.

**Status: M0, foundations.** The backend (FastAPI, SQLite, the engine's first snapshot, the Rocket Money loader and recurring detection, owner sign-in, API keys, the scheduler, the MCP endpoint) and the frontend shell (the River view, Today, the ledger, Settings with Appearance, Clients, Connectors and Jobs) build and pass their tests. Nothing is deployed yet.

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

Make a key on Settings › Clients, then:

```bash
claude mcp add --transport http financialauthority http://<host>:8430/mcp --header "Authorization: Bearer <key>"
```

The tools: `finance_snapshot`, `accounts_list`, `transactions_search`, `jobs_status`, growing with each milestone. The REST mirror is under `/api/v1`.

FinancialAuthority is not financial advice. It is a calculator that shows its work.
