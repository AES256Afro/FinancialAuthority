"""Tool schemas for anything that is not an MCP client: the same tools in OpenAI's and Anthropic's shapes,
and a prompt pack for Open WebUI, LibreChat and home-grown agents. Each schema points at the REST mirror."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from .. import auth
from ..config import settings
from ..llm.advisor import SYSTEM, tool_specs
from ..mcp_server import mcp_server
from ..version import __version__

router = APIRouter(prefix="/api/tools", tags=["tools"])

# Every tool, with the REST route that mirrors it, so an agent without MCP can call the same thing.
REST_MIRROR = {
    "finance_snapshot": "GET /api/v1/snapshot", "brief_today": "GET /api/v1/snapshot (the brief field)", "purchase_check": "GET /api/v1/check?amount=&on=&installments=",
    "watchlist": "GET /api/v1/watchlist", "billing_dates_suggest": "GET /api/v1/bills/suggestions", "spending_summary": "GET /api/v1/reports/spending?months=&by=",
    "debts_list": "GET /api/v1/debts", "investments_list": "GET /api/v1/investments", "accounts_list": "GET /api/v1/accounts", "transactions_search": "GET /api/v1/transactions/search?text=&category=&limit=",
    "recurring_list": "GET /api/v1/recurring", "bills_list": "GET /api/v1/bills", "payments_list": "GET /api/v1/payments", "paystubs_list": "GET /api/v1/paystubs",
    "mail_search": "GET /api/v1/mail/messages?text=&sender=&kind=", "mail_read": "GET /api/v1/mail/messages/{id}", "mail_attachment": "GET /api/v1/mail/messages/{id}/attachments/{name}", "facts_pending": "GET /api/v1/facts?status=pending",
    "facts_confirm": "POST /api/v1/facts/{id}/confirm", "mail_send_request": "POST /api/v1/approvals", "jobs_status": "GET /api/v1/jobs", "next_steps": "GET /api/v1/next_steps",
}


def _all_specs():
    names = [t.name for t in mcp_server._tool_manager.list_tools()]
    return tool_specs(names)


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
