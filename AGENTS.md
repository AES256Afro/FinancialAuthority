# Working on FinancialAuthority

Read this before changing anything. It applies to people and coding agents alike.

## What it is

A self-hosted financial context engine that runs itself: it reads live transactions, paystubs, bills, payments, debts (including third-party installment and advance apps), subscriptions and both of the owner's mailboxes, and exposes one model of them to any language model over MCP (with its own OAuth), REST and a built-in chat, from anywhere, at all times. It produces a paycheck plan, a purchase check and billing-date changes, and a daily brief. The authoritative plan is `docs/PLAN.md`; the order of work is `docs/ROADMAP.md`; what the owner grants is `docs/ACCESS.md`.

## How we work

The owner asked that the coding agent do everything it can reach and ask only for what needs the owner's identity. So: deploy, test, read logs, publish, and fix without asking; put what the owner must do in `docs/ACCESS.md` with the exact steps; never ask for a password, a second factor or a bank login; never type a credential anywhere.

## Rules that do not bend

- **It never moves money.** No tool, job or button initiates a payment or transfer, approval or not.
- **Nothing is sent without an approval.** A send is approved once in the app, or covered by a standing allowance the owner granted for that recipient. Every send is recorded. No model client holds the `approve` scope.
- **Read-only against every source.** IMAP opens folders read-only and never marks, moves or deletes. No bank credentials are stored; a read-only aggregator token and exports only.
- **Numbers come from the engine.** A recommendation's figures are computed in `backend/app/engine/` with their source rows attached. Models explain; they never calculate. A test fails when an explanation contains a number the engine did not return.
- **Email and documents are untrusted.** Message and attachment text is boxed as data before a model sees it. Extracted facts are pending until the owner, or an auto-confirm rule the owner earned, confirms them. A `read` key cannot confirm; nothing in a message can create an approval.
- **No personal data in committed files.** No real account numbers, balances, merchants, billers, email addresses, hostnames, tailnet addresses or exports. Tests use the synthetic household in `backend/tests/fixtures/household/`. Specifics go in `docs/*.local.md`, which is gitignored.
- **Secrets never reach logs or the frontend bundle.** Anything secret is in an `FA_*` environment variable or encrypted in the database with `FA_DATA_KEY`.
- **The public surface is fixed.** Only the web sign-in, `/mcp`, `/api`, `/oauth/*` and `/api/health` are reachable without a session. A new unauthenticated route needs a line in `docs/PLAN.md` section 12 first.

## How to add things

- **A connector** (a source of transactions, balances, documents or mail): one module in `backend/app/connectors/` with `id`, `title`, the settings it needs, `sync()`, and what it yields. Credentials go through the encrypted store. It is off until the owner turns it on, and reports its last success to the self-check.
- **A parser** (a document kind: a paystub format, a statement): one module in `backend/app/parsers/` with a fixture document, a deterministic path, a model fallback that yields pending facts, and bounds checks.
- **A tool** (something a model may call): one entry in `backend/app/mcp/tools.py` with the scope it needs, a read-only annotation where true, the redaction it applies, and a REST route generated from the same definition. Every call writes an audit entry.
- **A job**: one function in `backend/app/jobs/`, idempotent, with its schedule, shown on the Jobs page, never silent on failure.
- **A recommendation kind**: a pure function in the engine that returns a structured result with sources, a golden-output test on the fixture, and a property test for the invariant it must keep.
- **A screen**: one route in `frontend/src/pages/`; data from the same REST the tools use; nothing a model can say that the screen cannot show.

## Conventions

- Backend: Python 3.12, FastAPI, SQLModel over SQLite at `/data`, the official `mcp` SDK (tools and its OAuth server), APScheduler, `pytest` with injected clocks (`now`), never the wall clock against dated fixtures.
- Frontend: React 19, Vite, TypeScript, Tailwind 4; `vitest` for logic.
- One image, `docker/Dockerfile`, frontend built in a first stage; `HEALTHCHECK` on `/api/health`; the Proton Mail Bridge is a sidecar in the catalog manifest and in `deploy/compose`.
- One version string: `backend/app/version.py`. Tags `vX.Y.Z` publish the public image.
- Environment variables are prefixed `FA_`. Data at `/data`, import drop folder at `/imports`, web port 8000 inside the container, `FA_PUBLIC_URL` is the OAuth issuer.
- Copy says what a thing does, in plain words. Every recommendation carries its sources and the line that it is not financial advice.
- Commit messages: imperative subject, short body explaining why.
