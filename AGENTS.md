# Working on FinancialAuthority

Read this before changing anything. It applies to people and coding agents alike.

## What it is

A self-hosted financial context engine: it reads transactions, paychecks, bills, debts (including third-party installment apps), subscriptions and bill-carrying email, and exposes one model of them to any language model over MCP, REST and a built-in chat. It produces a paycheck plan, a purchase check and billing-date changes. The authoritative plan is `docs/PLAN.md`; the order of work is `docs/ROADMAP.md`.

## Rules that do not bend

- **It never moves money and never sends anything.** No tool, job or button initiates a payment, transfer or email. Drafts only.
- **Read-only against every source.** IMAP opens folders read-only and never marks, moves or deletes. No bank credentials are stored; exports and read-only tokens only.
- **Numbers come from the engine.** A recommendation's figures are computed in `backend/app/engine/` with their source rows attached. Models explain; they never calculate. A test fails when an explanation contains a number the engine did not return.
- **Email is untrusted.** Message text is boxed as data before a model sees it. Extracted facts are pending until the owner confirms them. A `read` key cannot confirm.
- **No personal data in committed files.** No real account numbers, balances, merchants, email addresses, hostnames or exports. Tests use the synthetic household in `backend/tests/fixtures/household/`. Private notes go in `*.local.md`, which is gitignored.
- **Secrets never reach logs or the frontend bundle.** Anything secret is in an `FA_*` environment variable or encrypted in the database with `FA_DATA_KEY`.

## How to add things

- **A connector** (a source of transactions, balances or mail): one module in `backend/app/connectors/` with `id`, `title`, the settings it needs, `sync()`, and what it yields. Credentials go through the encrypted store. It is off until the owner turns it on.
- **A tool** (something a model may call): one entry in `backend/app/mcp/tools.py` with the scope it needs, a read-only annotation where true, the redaction it applies, and a REST route generated from the same definition. Every call writes an audit entry.
- **A recommendation kind**: a pure function in the engine that returns a structured result with sources, a golden-output test on the fixture, and a property test for the invariant it must keep.
- **A screen**: one route in `frontend/src/pages/`; data from the same REST the tools use; nothing a model can say that the screen cannot show.

## Conventions

- Backend: Python 3.12, FastAPI, SQLModel over SQLite at `/data`, the official `mcp` SDK, `pytest` with injected clocks (`now`), never the wall clock against dated fixtures.
- Frontend: React 19, Vite, TypeScript, Tailwind 4; `vitest` for logic.
- One image, `docker/Dockerfile`, frontend built in a first stage; `HEALTHCHECK` on `/api/health`.
- One version string: `backend/app/version.py`. Tags `vX.Y.Z` publish the image.
- Environment variables are prefixed `FA_`. Data at `/data`, import drop folder at `/imports`, web port 8000 inside the container.
- Copy says what a thing does, in plain words. Every recommendation carries its sources and the line that it is not financial advice.
- Commit messages: imperative subject, short body explaining why.
