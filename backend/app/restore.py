"""Restore points: a copy of the whole database, taken before every import, once a day, and on request, kept
beside the database under the data directory. Restoring one puts every table back as it was at that moment; a
restore point of the moment before is taken first, so a restore can itself be undone.

Copies are made with SQLite's own online backup, so they are consistent while the app is running. They hold
the same data as the database and nothing more: secrets in them stay encrypted with the data key. Nothing here
sends a copy anywhere."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import settings

FOLDER = "restore-points"
# How many of each kind are kept; the oldest beyond that are removed.
KEEP = {"import": 15, "daily": 7, "manual": 10, "before-restore": 5}
NAME = re.compile(r"^\d{8}-\d{6}-\d{6}-[a-z-]+\.db$")
# Who may get in, what was sent, and what happened stay as they are now: a restore never brings back a revoked
# key or a closed session, never makes a sent email look unsent, and never erases the activity log.
CARRIED = ("api_keys", "oauth_clients", "oauth_codes", "oauth_tokens", "web_sessions", "audit_entries", "approvals", "passkeys", "people")
CARRIED_SETTINGS = ("calendar.feed", "auth.totp", "auth.totp.last_step", "send.allowances", "webpush.subs", "push")


def folder() -> Path:
    path = settings.restore_dir or settings.data_dir / FOLDER
    path.mkdir(parents=True, exist_ok=True)
    return path


def check(path: Path, quick: bool = False) -> dict[str, Any]:
    """Open a copy on its own and have SQLite check every page of it (or, quick, the structure without the
    indexes' contents): a copy counts only once it reads back."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = con.execute("PRAGMA quick_check" if quick else "PRAGMA integrity_check").fetchone()[0]
        tables = con.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0]
        try:
            rows = con.execute("SELECT count(*) FROM transactions").fetchone()[0]
        except sqlite3.Error:
            rows = None
    finally:
        con.close()
    return {"ok": result == "ok", "result": result, "tables": tables, "transactions": rows}


def _copy(src: Path, dst: Path) -> None:
    source = sqlite3.connect(str(src))
    target = sqlite3.connect(str(dst))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def make_point(kind: str, reason: str) -> dict[str, Any]:
    """Copy the database now. Returns the point's description."""
    if kind not in KEEP:
        raise ValueError("unknown kind of restore point")
    now = datetime.now(UTC)
    name = f"{now:%Y%m%d-%H%M%S-%f}-{kind}.db"
    path = folder() / name
    _copy(settings.db_path, path)
    verdict = check(path, quick=kind == "import")  # the daily copy and the drill check every page
    if not verdict["ok"] or not verdict["tables"]:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"The copy did not read back ({verdict['result']}); nothing was kept")
    meta = {"name": name, "kind": kind, "reason": reason[:200], "created_at": now.isoformat(), "bytes": path.stat().st_size, "verified": True,
            "tables": verdict["tables"], "transactions": verdict["transactions"]}
    path.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")
    _prune(kind)
    return meta


def _prune(kind: str) -> None:
    points = [p for p in list_points() if p["kind"] == kind]
    for p in points[KEEP[kind]:]:
        remove_point(p["name"])


def list_points() -> list[dict[str, Any]]:
    """Every restore point, newest first."""
    out = []
    for path in folder().glob("*.db"):
        if not NAME.fullmatch(path.name):
            continue
        meta_path = path.with_suffix(".json")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        except ValueError:
            meta = {}
        out.append({"name": path.name, "kind": meta.get("kind", path.stem.split("-", 3)[-1]), "reason": meta.get("reason", ""),
                    "created_at": meta.get("created_at", ""), "bytes": path.stat().st_size, "verified": bool(meta.get("verified")), "transactions": meta.get("transactions")})
    return sorted(out, key=lambda p: p["name"], reverse=True)


_live: dict[str, Any] = {}
LIVE_SECONDS = 3 * 3600  # the hourly self-check keeps this fresh; a page shows its last answer


def status(fresh: bool = False) -> dict[str, Any]:
    """What a backup manager needs at a glance: the newest copy of each kind, how many, how much space, and
    whether the live database itself reads back (checked again when asked, else the last answer of the hour)."""
    import time

    points = list_points()
    newest = {kind: next((p for p in points if p["kind"] == kind), None) for kind in KEEP}
    if not settings.db_path.exists():
        live = {"ok": False, "result": "no database yet", "tables": 0, "transactions": None}
    elif fresh or not _live or time.monotonic() - _live["at"] > LIVE_SECONDS or _live.get("path") != str(settings.db_path):
        live = check(settings.db_path, quick=True)
        _live.update({"at": time.monotonic(), "path": str(settings.db_path), "result": live})
    else:
        live = _live["result"]
    latest = points[0] if points else None
    age_hours = None
    if latest and latest["created_at"]:
        age_hours = round((datetime.now(UTC) - datetime.fromisoformat(latest["created_at"])).total_seconds() / 3600, 1)
    note = folder() / "last-check.json"
    try:
        last_check = json.loads(note.read_text(encoding="utf-8")) if note.exists() else None
    except ValueError:
        last_check = None
    return {"folder": str(folder()), "count": len(points), "bytes": sum(p["bytes"] for p in points), "newest": newest, "latest_age_hours": age_hours,
            "database": {"bytes": settings.db_path.stat().st_size if settings.db_path.exists() else 0, **live}, "keep": KEEP, "last_check": last_check}


def _path(name: str) -> Path:
    if not NAME.fullmatch(name or ""):
        raise ValueError("No such restore point")
    path = folder() / name
    if not path.exists():
        raise ValueError("No such restore point")
    return path


def remove_point(name: str) -> None:
    path = _path(name)
    path.unlink(missing_ok=True)
    path.with_suffix(".json").unlink(missing_ok=True)


def restore(name: str) -> dict[str, Any]:
    """Put the whole database back as it was at a restore point. The moment before is kept as its own point."""
    from .db import engine, init_db

    path = _path(name)
    before = make_point("before-restore", f"Before restoring the point of {name[:15]}")
    engine.dispose()
    _copy(path, settings.db_path)
    engine.dispose()
    init_db()  # a point from an older version gains the columns this version added
    _carry_forward(folder() / before["name"], settings.db_path)
    engine.dispose()
    from .db import bump_version

    bump_version()  # every computed plan is of the database that was just replaced
    with (folder() / "restores.log").open("a", encoding="utf-8") as log:
        log.write(json.dumps({"at": datetime.now(UTC).isoformat(), "restored": name, "before": before["name"]}) + "\n")
    return {"restored": name, "before": before}


def _carry_forward(current: Path, db: Path) -> None:
    """Copy the security tables and settings from the moment before the restore into the restored database."""
    con = sqlite3.connect(str(db))
    try:
        con.execute("ATTACH DATABASE ? AS now", (str(current),))
        for table in CARRIED:
            mine = [r[1] for r in con.execute(f"PRAGMA main.table_info({table})")]
            theirs = {r[1] for r in con.execute(f"PRAGMA now.table_info({table})")}
            shared = ", ".join(f'"{c}"' for c in mine if c in theirs)
            if not shared:
                continue
            con.execute(f"DELETE FROM main.{table}")
            con.execute(f"INSERT INTO main.{table} ({shared}) SELECT {shared} FROM now.{table}")
        for key in CARRIED_SETTINGS:
            con.execute("DELETE FROM main.settings WHERE key = ?", (key,))
            con.execute("INSERT INTO main.settings (key, value) SELECT key, value FROM now.settings WHERE key = ?", (key,))
        con.commit()
        con.execute("DETACH DATABASE now")
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    """For a backup manager outside the app (BoxPilot runs it in the container): status, list, make, check.
    Prints JSON. Restoring stays in the app, where the owner signs in and confirms."""
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "status"
    try:
        if command == "status":
            out: Any = status(fresh=True)
        elif command == "list":
            out = list_points()
        elif command == "make":
            out = make_point("manual", " ".join(args[1:]) or "Taken by the backup manager")
        elif command == "check":
            out = {"database": check(settings.db_path), "points": {p["name"]: check(folder() / p["name"])["result"] for p in list_points()[:3]}}
            note = {"at": datetime.now(UTC).isoformat(), "by": " ".join(args[1:])[:60] or "a backup manager", "database": out["database"]["result"],
                    "points_ok": all(r == "ok" for r in out["points"].values()), "points": len(out["points"])}
            (folder() / "last-check.json").write_text(json.dumps(note), encoding="utf-8")
        else:
            print(json.dumps({"error": f"unknown command {command}; use status, list, make or check"}))
            return 2
    except Exception as error:  # noqa: BLE001 - the caller reads the reason
        print(json.dumps({"error": f"{type(error).__name__}: {error}"}))
        return 1
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
