"""The MCP server: the same tools as the REST API, for Claude, Open WebUI and any other MCP client.

Bearer API keys (made on the Clients page) are verified here; M3 adds the OAuth flow that lets claude.ai
sign in by itself. Every call is audited. Tool names use underscores; the docs write them with dots.
"""

from __future__ import annotations

import logging
from datetime import date

from mcp.server import MCPServer
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from . import auth, services
from .config import settings
from .db import session_scope
from .engine.snapshot import finance_snapshot
from .jobs.scheduler import jobs
from .version import __version__

log = logging.getLogger("fa.mcp")

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)

try:  # the SDK exposes the verified token to tool code; the import path has moved between releases
    from mcp.server.auth.middleware.auth_context import get_access_token
except Exception:  # noqa: BLE001

    def get_access_token() -> AccessToken | None:  # type: ignore[misc]
        return None


class KeyVerifier(TokenVerifier):
    """A bearer token is one of the app's API keys."""

    async def verify_token(self, token: str) -> AccessToken | None:
        with session_scope() as session:
            key = auth.lookup_key(session, token)
            if key is None:
                return None
            return AccessToken(token=token, client_id=key.name, scopes=sorted(key.scope_set), expires_at=None, claims={"redaction": key.redaction})


def _caller() -> tuple[str, str]:
    token = get_access_token()
    if token is None:
        return "mcp", "full"
    redaction = (token.claims or {}).get("redaction", "full") if hasattr(token, "claims") else "full"
    return token.client_id, redaction


def _today() -> date:
    from .api.routes import today

    return today()


def build_server() -> MCPServer:
    issuer = settings.public_url or f"http://localhost:{settings.port}"
    server = MCPServer(
        "financialauthority",
        instructions=(
            "FinancialAuthority: the owner's money, computed by a deterministic engine. Amounts are positive for "
            "money out and negative for money in. Start with finance_snapshot: it carries balances, the next "
            "paycheck, what is due before it, advances, the window's bills, set-asides and safe-to-spend, each "
            "with a source. Explain the numbers it gives; never compute your own. It is not financial advice."
        ),
        version=__version__,
        token_verifier=KeyVerifier(),
        auth=AuthSettings(issuer_url=issuer, resource_server_url=issuer, required_scopes=["read"], validate_token_resource=False),
        log_level="WARNING",
    )

    @server.tool(name="finance_snapshot", annotations=READ_ONLY)
    def finance_snapshot_tool() -> dict:
        """Balances, the next paycheck, what is due before it, advances, the window's bills, set-asides and
        safe-to-spend, each line with its source. Call this first."""
        client, redaction = _caller()
        with session_scope() as session:
            snap = finance_snapshot(session, _today())
            services.audit(session, client=client, client_kind="key", tool="finance_snapshot", rows=len(snap["projection"]), redaction=redaction)
            if redaction != "full":
                for a in snap["accounts"]:
                    a["name"] = a["kind"]
            return snap

    @server.tool(name="accounts_list", annotations=READ_ONLY)
    def accounts_list() -> dict:
        """Every account with its balance, institution, kind, connector and when the balance was last known."""
        client, redaction = _caller()
        with session_scope() as session:
            rows = services.list_accounts(session)
            services.audit(session, client=client, client_kind="key", tool="accounts_list", rows=len(rows), redaction=redaction)
            return {"accounts": rows}

    @server.tool(name="transactions_search", annotations=READ_ONLY)
    def transactions_search(
        text: str | None = None,
        category: str | None = None,
        account: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        min_amount: float | None = None,
        max_amount: float | None = None,
        include_non_spending: bool = False,
        limit: int = 50,
    ) -> dict:
        """Find transactions, newest first.

        Args:
            text: Substring of merchant, bank name, description or note.
            category: Exact category name.
            account: Substring of the account or institution name.
            start_date: YYYY-MM-DD.
            end_date: YYYY-MM-DD.
            min_amount: Minimum amount (positive = money out).
            max_amount: Maximum amount.
            include_non_spending: Also include transfers, card payments, income and ignored rows.
            limit: Max rows (500).
        """
        client, redaction = _caller()
        with session_scope() as session:
            result = services.search_transactions(
                session, redaction=redaction, text=text, category=category, account=account,
                start=date.fromisoformat(start_date) if start_date else None, end=date.fromisoformat(end_date) if end_date else None,
                min_amount=min_amount, max_amount=max_amount, include_non_spending=include_non_spending, limit=limit,
            )
            services.audit(session, client=client, client_kind="key", tool="transactions_search", args={"text": text, "category": category, "account": account}, rows=result["returned"], redaction=redaction)
            return result

    @server.tool(name="recurring_list", annotations=READ_ONLY)
    def recurring_list(include_classified: bool = True) -> dict:
        """Recurring charges and paychecks found in the ledger: merchant, cadence, typical amount, next expected
        date, and whether the owner has confirmed what each one is (bill, subscription, income, installment, ignored).

        Args:
            include_classified: False lists only the series still waiting for the owner.
        """
        from .api.registers import series_out
        from .jobs.detect import pending_series
        from .models import RecurringSeries
        from sqlmodel import select

        client, redaction = _caller()
        with session_scope() as session:
            rows = session.exec(select(RecurringSeries).order_by(RecurringSeries.typical_amount.desc())).all() if include_classified else pending_series(session)
            out = [series_out(s) for s in rows]
            if redaction == "aggregates":
                for s in out:
                    s["merchant"] = "(redacted)"
            services.audit(session, client=client, client_kind="key", tool="recurring_list", rows=len(out), redaction=redaction)
            return {"series": out, "pending": len(pending_series(session))}

    @server.tool(name="bills_list", annotations=READ_ONLY)
    def bills_list() -> dict:
        """Every bill and subscription the owner has confirmed: amount, due day, autopay, whether the date can move."""
        from .api.registers import bill_out
        from .models import Bill
        from sqlmodel import select

        client, redaction = _caller()
        with session_scope() as session:
            rows = session.exec(select(Bill).where(Bill.active == True).order_by(Bill.due_day)).all()  # noqa: E712
            services.audit(session, client=client, client_kind="key", tool="bills_list", rows=len(rows), redaction=redaction)
            return {"bills": [bill_out(b) for b in rows]}

    @server.tool(name="jobs_status", annotations=READ_ONLY)
    def jobs_status() -> dict:
        """Whether the app is healthy: the scheduled jobs, their last runs, and the last self-check."""
        with session_scope() as session:
            from .store import get_setting

            runs = jobs.recent(20)
            return {
                "scheduler": "running" if jobs.scheduler else "stopped",
                "jobs": [{"name": s.name, "every_seconds": s.seconds, "description": s.description} for s in jobs.specs.values()],
                "recent_runs": [{"name": r.name, "started_at": r.started_at.isoformat(), "ok": r.ok, "summary": r.summary} for r in runs],
                "self_check": get_setting(session, "selfcheck", {}),
                "version": __version__,
            }

    return server


mcp_server = build_server()


def asgi_routes():
    """The MCP endpoint and the OAuth protected-resource metadata, as routes to add to the main app."""
    app = mcp_server.streamable_http_app(
        streamable_http_path="/mcp",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return list(app.routes)
