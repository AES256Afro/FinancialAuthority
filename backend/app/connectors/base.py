"""The connector contract (AGENTS.md: how to add a connector)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SyncResult:
    ok: bool
    summary: str
    inserted: int = 0
    duplicates: int = 0
    accounts: int = 0
    details: dict = field(default_factory=dict)
    at: datetime | None = None


class Connector:
    id: str = ""
    title: str = ""
    yields: str = ""
    settings_schema: list[dict] = []  # [{"name", "label", "secret": bool, "help"}]

    def sync(self, session, **kwargs) -> SyncResult:  # noqa: ANN001
        raise NotImplementedError
