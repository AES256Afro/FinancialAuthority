# The demo

*Written 2026-10-14.*

There are two ways to show FinancialAuthority. The **live app** on BigBox runs the engine on the owner's own data, behind the owner's sign-in; it is never shown to anyone else. The **demo** is the same screens over a synthetic household, published as a private page at [claude.ai/artifact/HbpRKLiQRBghHPqTkEb1sm](https://claude.ai/artifact/HbpRKLiQRBghHPqTkEb1sm); the owner shares it from the page's Share menu. Nothing in it is real, changes are not kept, and a banner says so on every screen.

## What the demo is

The real frontend, built once with `VITE_STATIC_DEMO=1`, routed after the hash, answering every API call from `demo-data.json` instead of a server. That file is the engine's own output for the synthetic household: the snapshot, the brief, the windows, the reports, the debt mountain, the date moves, the don't-buy list, the goals, a recorded review with its notes, a send waiting for approval, a few mail messages with their facts, a connected app and a key. The purchase check is recorded for every amount from $0 to $1,000 in steps of ten and to $3,000 in steps of fifty, with and without pay-in-four, so the "Should I buy this?" card answers from the engine too; an amount is rounded to the nearest recorded one and the card shows the amount it was answered for. The chat has no model behind it and says so; the engine answers in its own sentences.

No figure is computed in the browser. Writes are acknowledged with a line in the banner and forgotten.

## Regenerating it

After a change to the screens or the engine:

```bash
cd backend && uv run python scripts/capture_demo.py
```

```bash
cd frontend && npm run build:demo
```

The first command runs the app in-process on a throwaway data directory with the demo household, adds the few rows that make the screens worth showing, records every route the screens call, and writes `frontend/demo/demo-data.json` (about 0.4 MB, never committed). The second builds `frontend/dist-demo/`: `page.html` (the fragment an artifact publishes), `assets/`, `demo-data.json`, the icons. Publishing the same page path again keeps the demo's address.

The capture script asserts that no password reached the data; the household is synthetic by construction, so no account, balance, merchant or address in the demo is real.

## Showing both together

Open the demo on one screen and the live app on the other. The demo walks through the idea on a household anyone may see; the live app shows the same screens on the owner's own registers, and only the owner is signed in to it. The two are built from the same code, so what the demo shows is what the live app does.
