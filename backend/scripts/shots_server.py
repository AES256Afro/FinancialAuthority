"""Serve the synthetic household for the README's screenshots: the demo's extras (scripts/capture_demo.py: mail and
its facts, a 401(k), a recorded review, a move, receipts, a send waiting for approval) plus a small business with
its write-offs, invoices and miles, and a filing status. A throwaway data folder, sign-in off as in the demo, the
date held at the demo's day. Nothing real is involved; nothing leaves the machine.

    uv run --directory backend python scripts/shots_server.py      then http://127.0.0.1:8012
    cd frontend && npm run shots                                    starts it and writes docs/screenshots/
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import capture_demo  # noqa: E402  (sets the throwaway data folder, the demo household and the demo's day)
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


# Purchases a side business would have, so the write-off finder has something to ask about. Synthetic rows.
WORK_BUYS = [("2026-02-11", "Best Buy", 849.99), ("2026-04-03", "Staples", 46.20), ("2026-05-19", "USPS", 9.85), ("2026-06-02", "Udemy", 14.99),
             ("2026-07-01", "GitHub", 4.00), ("2026-08-01", "GitHub", 4.00), ("2026-09-01", "GitHub", 4.00), ("2026-09-14", "Backblaze", 9.00)]


def seed_work_purchases() -> None:
    from sqlmodel import Session, select

    from app.db import engine
    from app.models import Account, Transaction

    with Session(engine) as session:
        card = session.exec(select(Account).where(Account.kind == "card", Account.active == True)).first() or session.exec(select(Account).where(Account.active == True)).first()  # noqa: E712
        for i, (day, merchant, amount) in enumerate(WORK_BUYS):
            from datetime import date as _date

            session.add(Transaction(account_id=card.id, date=_date.fromisoformat(day), amount=amount, merchant=merchant, raw_name=merchant.upper(), category="Shopping", row_hash=f"shots-work-{i}", source="demo"))
        session.commit()


def seed_business(client: TestClient) -> None:
    """A one-person business on the side, as the Business pages would hold it. All of it synthetic."""
    made = client.post("/api/v1/business", json={"name": "Example Repair Studio", "kind": "sole_prop", "state": "TX", "started_on": "2026-03-01",
                                                  "what_it_does": "Computer repair and IT help for small offices"}).json()
    bid = next(b["id"] for b in made["businesses"] if b["name"] == "Example Repair Studio")
    for step in ("record", "account", "invoices"):
        client.post(f"/api/v1/business/setup/{step}", json={"done": True})
    found = client.get("/api/v1/business/finder").json()
    for group in found.get("groups", []):  # supplies answered; the rest stay for the finder to ask about
        if group["key"] != "office":
            continue
        ids = [i["id"] for i in group["items"]]
        if ids:
            client.post("/api/v1/business/finder/answer", json={"ids": ids, "answer": "business", "business_id": bid, "tax_line": group["key"]})
    client.post(f"/api/v1/business/{bid}/invoices", json={"direction": "out", "counterparty": "Example Dental Office", "issued_on": "2026-09-20", "due_on": "2026-10-20", "status": "sent",
                                                         "lines": [{"what": "Office network setup", "quantity": 1, "price": 450}, {"what": "On-site hours", "quantity": 3, "price": 65}]})
    paid = client.post(f"/api/v1/business/{bid}/invoices", json={"direction": "out", "counterparty": "Example Law Office", "issued_on": "2026-08-28", "due_on": "2026-09-27", "status": "sent",
                                                                "lines": [{"what": "Laptop repair", "quantity": 1, "price": 180}]}).json()
    client.post(f"/api/v1/business/invoices/{paid['id']}/paid", json={"paid_on": "2026-09-25"})
    client.post(f"/api/v1/business/{bid}/invoices", json={"direction": "in", "counterparty": "Example Parts Supply", "issued_on": "2026-10-02", "due_on": "2026-11-01", "status": "sent",
                                                         "lines": [{"what": "Replacement drives", "quantity": 4, "price": 38.5}]})
    for day, miles, purpose in (("2026-09-20", 14.2, "Network setup at Example Dental Office"), ("2026-09-24", 8.6, "Parts pickup at Example Parts Supply"),
                                ("2026-10-03", 22.4, "Laptop drop-off at Example Law Office")):
        client.post(f"/api/v1/business/{bid}/trips", json={"day": day, "miles": miles, "purpose": purpose, "start": "Home", "end": "Client"})
    client.post("/api/v1/taxes/answers", json={"answers": {"filing_status": "single"}})


def seed_when_up(port: int) -> None:
    """The app's MCP session starts once per process, so the rows go in through the running server itself, over
    HTTP, once it answers. The screenshot run waits for the business to appear before it starts."""
    import time

    import httpx

    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=120) as client:
        for _ in range(240):
            try:
                if client.get("/api/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        seed_work_purchases()
        capture_demo.seed_extras(client)  # type: ignore[arg-type]  (the same calls a TestClient takes)
        client.post("/api/v1/data-check/run")
        seed_business(client)  # type: ignore[arg-type]  (last: its business is the sign that the rows are in)
    print("shots: the household is ready", flush=True)


if __name__ == "__main__":
    import threading

    import uvicorn

    port = int(os.environ.get("FA_SHOTS_PORT", "8012"))
    threading.Thread(target=seed_when_up, args=(port,), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=port)
