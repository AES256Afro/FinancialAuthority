"""Small helpers over the Setting table: JSON values by key."""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from .models import Setting

DEFAULTS: dict[str, Any] = {
    "buffer": 200.0,
    "appearance": {"view": "river", "look": "light"},
    "imports.processed": [],
    "selfcheck": {},
}


def get_setting(session: Session, key: str, default: Any = None) -> Any:
    row = session.get(Setting, key)
    if row is None:
        return DEFAULTS.get(key, default)
    return json.loads(row.value)


def set_setting(session: Session, key: str, value: Any) -> None:
    row = session.get(Setting, key)
    if row is None:
        row = Setting(key=key, value=json.dumps(value))
        session.add(row)
    else:
        row.value = json.dumps(value)
    session.commit()
