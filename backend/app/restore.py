"""Restore points: a copy of the whole database, taken before every import, once a day, and on request, kept
beside the database under the data directory. Restoring one puts every table back as it was at that moment; a
restore point of the moment before is taken first, so a restore can itself be undone.

Copies are made with SQLite's own online backup, so they are consistent while the app is running. They hold
the same data as the database and nothing more: secrets in them stay encrypted with the data key. Nothing here
sends a copy anywhere.

The files kept on Documents live once each under the data folder's documents/ (named by their SHA-256, encrypted
with the data key). A restore point names the files its database holds, and a file stays on disk while the live
database or any restore point names it: a document removed after a point comes back with its file when that point
is restored, and its file goes only when the last point that names it is pruned."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import settings

FOLDER = "restore-points"
# How many of each kind are kept; the oldest beyond that are removed.
KEEP = {"import": 15, "daily": 7, "manual": 10, "before-restore": 5, "offbox": 3}
NAME = re.compile(r"^\d{8}-\d{6}-\d{6}-[a-z-]+\.db$")
SHA = re.compile(r"^[0-9a-f]{64}$")
DOCUMENTS = "documents"  # the data folder's kept files, <sha256>.bin
# Who may get in, what was sent, and what happened stay as they are now: a restore never brings back a revoked
# key or a closed session, never makes a sent email look unsent, and never erases the activity log.
CARRIED = ("api_keys", "oauth_clients", "oauth_codes", "oauth_tokens", "web_sessions", "audit_entries", "approvals", "passkeys", "people", "mail_accounts")
# The same for settings: privacy (which model may receive anything, and its keys), the bank and backup keys, the
# phone's push keys and devices, and what reminders were already sent. A model switched off since stays off; a
# leaked passphrase rotated since is not used again; a phone added since keeps receiving.
CARRIED_SETTINGS = ("calendar.feed", "auth.totp", "auth.totp.last_step", "send.allowances", "webpush.subs", "webpush.vapid", "push", "push.brief_day", "reminders.sent",
                    "models", "models.secrets", "banks.secrets", "banks.plaid", "backups.offbox")
# What a restore leaves as it is now, in words for the Backups page.
KEPT_AS_NOW = [
    "Sign-in: your password, second step, passkeys, sessions, keys and connected apps",
    "The people you let in, and which accounts are shared",
    "Sends: approvals, and the emails already sent",
    "The activity log",
    "Mailboxes, bank links and their keys: a link removed since stays removed, a link made since stays and reads its bank again",
    "Privacy: which model may receive anything, and the model keys",
    "Push to your phone: the devices and their signing key, and the reminders already sent",
    "The off-box copy's folder and passphrase, and the calendar feed",
]


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
    dst.chmod(0o600)  # the copy holds the household: the app's user only, like the database itself


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
            "tables": verdict["tables"], "transactions": verdict["transactions"], "documents": sorted(document_shas(path))}
    path.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")
    _prune(kind)
    return meta


# ---- the kept files ------------------------------------------------------------------------------------------


def documents_folder() -> Path:
    return Path(settings.data_dir) / DOCUMENTS


def document_shas(db: Path) -> set[str]:
    """The files a database names. A database from before documents were kept names none. Any other failure to
    read it raises, so nothing is ever removed on a guess."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {r[0] for r in con.execute("SELECT DISTINCT sha256 FROM documents WHERE sha256 != ''") if r[0] and SHA.fullmatch(r[0])}
    except sqlite3.OperationalError as error:
        if "no such table" in str(error):
            return set()
        raise
    finally:
        con.close()


def point_documents(name: str) -> set[str]:
    """The files one restore point names: kept in its description when it is made, read from the copy once for an
    older point and written down then."""
    path = _path(name)
    meta_path = path.with_suffix(".json")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    except ValueError:
        meta = {}
    if isinstance(meta.get("documents"), list):
        return {str(s) for s in meta["documents"]}
    shas = document_shas(path)
    if meta:
        meta["documents"] = sorted(shas)
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return shas


def named_documents() -> set[str]:
    """Every file the live database or any restore point names. Raises when one cannot be read."""
    named = document_shas(settings.db_path) if settings.db_path.exists() else set()
    for p in list_points():
        named |= point_documents(p["name"])
    return named


def release_document_file(sha256: str) -> bool:
    """Call after a document's row is deleted and committed: its file is removed only when nothing names it any
    more (no row in the live database and no restore point), else it stays so a restore can bring it back. True
    when the file was removed."""
    if not SHA.fullmatch(sha256 or ""):
        return False
    path = documents_folder() / f"{sha256}.bin"
    if not path.exists():
        return False
    try:
        if sha256 in named_documents():
            return False
    except (sqlite3.Error, OSError, ValueError):
        return False  # a copy that cannot be read might name it: keep the file
    path.unlink(missing_ok=True)
    return True


def prune_documents(min_age_seconds: float = 3600) -> int:
    """Remove the kept files nothing names any more: no row in the live database and no restore point. A file
    newer than an hour is left (it may belong to a document being saved right now). Returns how many went."""
    where = documents_folder()
    if not where.is_dir():
        return 0
    try:
        named = named_documents()
    except (sqlite3.Error, OSError, ValueError):
        return 0  # something could not be read: remove nothing
    now, removed = time.time(), 0
    for path in where.glob("*.bin"):
        if path.stem in named or not SHA.fullmatch(path.stem):
            continue
        try:
            if now - path.stat().st_mtime < min_age_seconds:
                continue
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def missing_documents(db: Path | None = None) -> list[str]:
    """Files a database names that are not on disk (removed before files were kept for restore points)."""
    where = documents_folder()
    return sorted(s for s in document_shas(db or settings.db_path) if not (where / f"{s}.bin").exists())


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
    prune_documents()  # a file only this point named goes with it


def restore(name: str) -> dict[str, Any]:
    """Put the whole database back as it was at a restore point. The moment before is kept as its own point, and
    with it the files of documents added since, so the restore can itself be undone."""
    from .db import engine, init_db
    from .jobs.writing import one_writer

    path = _path(name)
    with one_writer():  # no job or sync writes while the database file is replaced
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
    try:
        missing = missing_documents()
    except (sqlite3.Error, OSError):
        missing = []
    return {"restored": name, "before": before, "documents_missing": len(missing)}


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
        _carry_bank_links(con)
        # Who sees an account is access, not data: each account keeps today's sharing; one not here today is private.
        cols = {r[1] for r in con.execute("PRAGMA main.table_info(accounts)")}
        if {"shared", "person_id"} <= cols and {"shared", "person_id"} <= {r[1] for r in con.execute("PRAGMA now.table_info(accounts)")}:
            con.execute("UPDATE main.accounts SET shared = 0, person_id = NULL")
            con.execute("UPDATE main.accounts SET shared = (SELECT n.shared FROM now.accounts n WHERE n.id = main.accounts.id), "
                        "person_id = (SELECT n.person_id FROM now.accounts n WHERE n.id = main.accounts.id) WHERE id IN (SELECT id FROM now.accounts)")
        con.commit()
        con.execute("DETACH DATABASE now")
    finally:
        con.close()


def _carry_bank_links(con: sqlite3.Connection) -> None:
    """Bank links are access too. A link that exists in both keeps the restored cursor (so the next sync brings
    back what happened since) with today's token and state: a link removed since stays removed, its token gone at
    the vendor. A link made since is kept and reads its bank from the start, since its rows went with the restore."""
    mine = [r[1] for r in con.execute("PRAGMA main.table_info(bank_links)")]
    theirs = {r[1] for r in con.execute("PRAGMA now.table_info(bank_links)")}
    if not mine or not {"provider", "external_id", "active", "access_token_enc"} <= theirs:
        return
    same = "n.provider = main.bank_links.provider AND n.external_id = main.bank_links.external_id"
    for column in ("active", "access_token_enc", "needs_login", "webhook", "environment"):
        if column in mine and column in theirs:
            con.execute(f'UPDATE main.bank_links SET "{column}" = (SELECT n."{column}" FROM now.bank_links n WHERE {same}) '
                        f"WHERE EXISTS (SELECT 1 FROM now.bank_links n WHERE {same})")
    top = con.execute("SELECT coalesce(max(id), 0) FROM main.bank_links").fetchone()[0]
    shared = ", ".join(f'"{c}"' for c in mine if c in theirs and c != "id")
    con.execute(f"INSERT INTO main.bank_links ({shared}) SELECT {shared} FROM now.bank_links n "
                "WHERE NOT EXISTS (SELECT 1 FROM main.bank_links m WHERE m.provider = n.provider AND m.external_id = n.external_id)")
    if "cursor" in mine:
        con.execute("UPDATE main.bank_links SET cursor = '' WHERE id > ?", (top,))


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
