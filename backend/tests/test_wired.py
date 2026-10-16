"""The features connected on 2026-10-16: each answers at its address, and a statement PDF imports."""

import io

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import engine
from app.engine.household import seed


def _pdf(lines: list[str]) -> bytes:
    from fpdf import FPDF

    doc = FPDF()
    doc.add_page()
    doc.set_font("Helvetica", size=10)
    for line in lines:
        doc.cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(doc.output())


def test_each_new_feature_answers(owner: TestClient, client: TestClient):
    with Session(engine) as session:
        seed(session)
    for path in ("/api/v1/watch", "/api/v1/ha", "/api/v1/reports/custom?rows=category&period=last_90", "/api/v1/reports/custom/saved", "/api/v1/reports/custom/year",
                 "/api/v1/reports/custom/tax", "/api/v1/goals/sinking", "/api/v1/debts/payoff?extra=50", "/api/v1/recap"):
        r = owner.get(path)
        assert r.status_code == 200, (path, r.text[:200])
    ha = owner.get("/api/v1/ha").json()
    assert set(ha) >= {"safe_to_spend", "days_to_payday", "next_bill_name", "needs_you", "status_word"}
    # a key that sees totals only reads the sensors, with names held back, and nothing else on /api
    key = owner.post("/api/v1/keys", json={"name": "Home Assistant", "scopes": ["read"], "redaction": "aggregates"}).json()
    reader = TestClient(client.app)
    bearer = {"Authorization": f"Bearer {key['token']}"}
    sensors = reader.get("/api/v1/ha", headers=bearer).json()
    assert sensors["sentence"] is None and sensors["next_bill_name"] in (None, "A bill")
    assert reader.get("/api/v1/snapshot", headers=bearer).status_code == 403


def test_a_statement_pdf_previews_and_imports(owner: TestClient):
    pdf = _pdf(["Example Credit Union", "Statement Period: 09/01/2026 - 09/30/2026", "09/05 EXAMPLE GROCER 123 54.12 3,385.88", "09/07 FUEL STOP 55 41.10 3,344.78", "09/30 Ending balance 3,344.78"])
    preview = owner.post("/api/v1/imports/preview", files={"file": ("sept.pdf", io.BytesIO(pdf), "application/pdf")}).json()
    assert preview["kind"] == "statement" and preview["total"] == 2 and preview["sample"][0]["amount"] == 54.12
    landed = owner.post("/api/v1/imports/file", data={"account_name": "Checking"}, files={"file": ("sept.pdf", io.BytesIO(pdf), "application/pdf")}).json()
    assert landed["inserted"] == 2
    blank = owner.post("/api/v1/imports/preview", files={"file": ("scan.pdf", io.BytesIO(_pdf([])), "application/pdf")})
    assert blank.status_code == 400 and "no text" in blank.json()["detail"]
