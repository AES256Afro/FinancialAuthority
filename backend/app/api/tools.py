"""Tool schemas for anything that is not an MCP client: the same tools in OpenAI's and Anthropic's shapes,
and a prompt pack for Open WebUI, LibreChat and home-grown agents. Each schema points at the REST mirror, and its
parameters carry the names the mirror takes, so an agent that passes each argument as it is named lands every one
(a query parameter the route does not know is dropped without a word, and the agent would explain unfiltered rows)."""

from __future__ import annotations

import copy
import re

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from .. import auth
from ..config import settings
from ..llm.advisor import SYSTEM, tool_specs
from ..mcp_server import mcp_server
from ..version import __version__

router = APIRouter(prefix="/api/tools", tags=["tools"])

# Every tool, with the REST route that mirrors it, so an agent without MCP can call the same thing. A {name} in a
# path is the tool parameter of that name; every other parameter goes in the query (GET) or the JSON body (POST).
REST_MIRROR = {
    "finance_snapshot": "GET /api/v1/snapshot", "brief_today": "GET /api/v1/snapshot (the brief field)", "purchase_check": "GET /api/v1/check?amount=&on=&installments=",
    "watchlist": "GET /api/v1/watchlist", "billing_dates_suggest": "GET /api/v1/bills/suggestions", "spending_summary": "GET /api/v1/reports/spending?months=&by=",
    "debts_list": "GET /api/v1/debts", "borrowing": "GET /api/v1/borrowing", "borrowing_plan": "GET /api/v1/borrowing/plan", "data_check": "GET /api/v1/data-check", "business_books": "GET /api/v1/business/{id}/books?year=", "write_off_finder": "GET /api/v1/business/finder?years=", "tax_year": "GET /api/v1/taxes/{year}", "planned_list": "GET /api/v1/planned", "scenario_run": "POST /api/v1/scenarios/run", "affirm_statement": "POST /api/v1/borrowing/statement", "readiness": "GET /api/v1/scenarios/readiness?move_date=&new_rent=", "accounts_cover": "GET /api/v1/cover", "saving_plan": "GET /api/v1/saving", "today_one_thing": "GET /api/v1/today", "weekly_checkin": "GET /api/v1/checkin", "paycheck_views": "GET /api/v1/paychecks", "glance": "GET /api/v1/glance",
    "account_move": "GET /api/v1/moves (each move with its id), then GET /api/v1/moves/{move_id} for one move's plan", "billing_date_check": "GET /api/v1/bills/{bill_id}/move_check?day=", "investments_list": "GET /api/v1/investments", "outlook": "GET /api/v1/projections",
    "where_to_put": "GET /api/v1/projections/where?amount=&percent=&basis=&years=&scenario=", "accounts_list": "GET /api/v1/accounts",
    "transactions_search": "GET /api/v1/transactions/search?text=&category=&account=&start=&end=&min_amount=&max_amount=&include_non_spending=&limit=",
    "recurring_list": "GET /api/v1/recurring?include_classified=", "bills_list": "GET /api/v1/bills", "payments_list": "GET /api/v1/payments", "paystubs_list": "GET /api/v1/paystubs",
    "mail_search": "GET /api/v1/mail/messages?q=&sender=&kind=&limit=", "mail_read": "GET /api/v1/mail/messages/{message_id}", "mail_attachment": "GET /api/v1/mail/messages/{message_id}/attachments/{name}", "facts_pending": "GET /api/v1/facts?status=pending",
    "housing_overview": "GET /api/v1/housing", "documents_search": "GET /api/v1/documents/search?q=", "document_read": "GET /api/v1/documents/{document_id}/pages/{page}", "receipts_list": "GET /api/v1/receipts",
    "facts_confirm": "POST /api/v1/facts/{fact_id}/confirm", "mail_send_request": "POST /api/v1/approvals", "jobs_status": "GET /api/v1/jobs", "next_steps": "GET /api/v1/next_steps",
}
# Where a route names a parameter otherwise than the MCP tool does: the published schema carries the route's name.
REST_NAMES = {
    "transactions_search": {"start_date": "start", "end_date": "end"},
    "where_to_put": {"amount_per_paycheck": "amount", "percent_of_paycheck": "percent"},
    "mail_search": {"text": "q"},
    "documents_search": {"query": "q"},
}


def _renamed(name: str, description: str, parameters: dict) -> tuple[str, dict]:
    """A tool's description and parameters with the route's names (REST_NAMES); the MCP tool itself is unchanged."""
    names = REST_NAMES.get(name)
    if not names:
        return description, parameters
    params = copy.deepcopy(parameters)
    props = params.get("properties") or {}
    params["properties"] = {names.get(k, k): v for k, v in props.items()}
    if "required" in params:
        params["required"] = [names.get(k, k) for k in params["required"]]
    for old, new in names.items():
        # an Args line ("    start_date: YYYY-MM-DD."), and the name wherever it is written out with an underscore
        description = re.sub(rf"(?m)^(\s*){re.escape(old)}:", rf"\g<1>{new}:", description)
        if "_" in old:
            description = re.sub(rf"\b{re.escape(old)}\b", new, description)
    return description, params


def _all_specs():
    """Every tool as the REST mirror takes it: the route's parameter names, never the MCP spec objects themselves."""
    from ..llm.providers import ToolSpec

    names = [t.name for t in mcp_server._tool_manager.list_tools()]
    out = []
    for t in tool_specs(names):
        description, parameters = _renamed(t.name, t.description, t.parameters)
        out.append(ToolSpec(t.name, description, parameters))
    return out


@router.get("/openai.json")
def openai_tools(_: auth.Principal = Depends(auth.require_read)) -> dict:
    return {
        "version": __version__,
        "base_url": (settings.public_url or f"http://localhost:{settings.port}").rstrip("/"),
        "tools": [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}} for t in _all_specs()],
        "rest": REST_MIRROR,
    }


@router.get("/anthropic.json")
def anthropic_tools(_: auth.Principal = Depends(auth.require_read)) -> dict:
    return {
        "version": __version__,
        "base_url": (settings.public_url or f"http://localhost:{settings.port}").rstrip("/"),
        "tools": [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in _all_specs()],
        "rest": REST_MIRROR,
    }


@router.get("/prompt.md", response_class=PlainTextResponse)
def prompt_pack(_: auth.Principal = Depends(auth.require_read)) -> str:
    base = (settings.public_url or f"http://localhost:{settings.port}").rstrip("/")
    system = SYSTEM.replace("{rules}", "").replace("The plan, as of {as_of}:\n{plan}", "Call finance_snapshot first; it returns the plan with every figure's source.")
    lines = [
        "# FinancialAuthority prompt pack", "",
        f"Version {__version__}. The app at {base}. MCP at `{base}/mcp` (OAuth 2.1, or a bearer API key). REST mirror under `{base}/api/v1` with the same bearer; OpenAPI at `{base}/openapi.json` when docs are on.", "",
        "## System prompt", "", "```", system.strip(), "```", "",
        "## Tools and their REST mirrors", "",
    ]
    for t in _all_specs():
        lines.append(f"- `{t.name}`: {t.description.splitlines()[0] if t.description else ''} Mirror: `{REST_MIRROR.get(t.name, '')}`.")
    lines += ["", "## Open WebUI", "", "Settings › Tools › add an OpenAPI tool server with the base URL above and the API key as the bearer; or add the MCP address as a connection where the build supports MCP.", "",
              "## Anything else", "", "Fetch `/api/tools/openai.json` or `/api/tools/anthropic.json` with the bearer and hand the `tools` list to the model; route each call to the REST mirror beside it."]
    return "\n".join(lines) + "\n"
