"""Sweep 14: process death. The heaviest jobs (bank_sync against the cursor-only scripted Plaid vendor, mail_sync
against the scripted IMAP box, import_watch over a folder of files, detect_recurring over the household's year
and the bank's new rows) run in a process of their own against a data folder of their own; the process is killed
with SIGKILL at a seeded random moment, a new process runs the same jobs to the end, and the end state must be
the state an uninterrupted run reaches: the same rows, no orphans, every restore point whole, the database
passing SQLite's integrity check, and nothing on disk holding a lock that would stop the next job.

The driver runs as its own script (python test_sweep14_bugs_kill.py <folder>): it builds the world once, then
forks one child per run, so each trial costs the jobs and not the app's import. Synthetic data only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TRIALS = 30
SEED = 14
JOBS = ("bank_sync", "mail_sync", "import_watch", "detect_recurring")


@pytest.mark.skipif(not hasattr(os, "fork"), reason="the driver forks its runs")
def test_jobs_killed_at_random_moments_end_as_an_uninterrupted_run(tmp_path: Path):
    env = {**os.environ, "FA_DATA_DIR": str(tmp_path / "data"), "FA_JOBS": "false", "FA_DEMO": "false", "PYTHONPATH": os.pathsep.join([str(Path(__file__).parent), str(Path(__file__).parents[1])])}
    env.pop("FA_RESTORE_DIR", None)
    done = subprocess.run([sys.executable, __file__, str(tmp_path)], env=env, cwd=str(Path(__file__).parents[1]), capture_output=True, text=True, timeout=240)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    report = json.loads(done.stdout.strip().splitlines()[-1])
    assert report["killed"] >= TRIALS // 2, report  # most trials died mid-run, not after it
    assert not report["findings"], "\n".join(report["findings"])


# ---- the driver ---------------------------------------------------------------------------------------------


def _driver(scratch: Path) -> dict:
    import random
    import signal
    import sqlite3
    import time
    import traceback

    from fastapi.testclient import TestClient
    from sqlmodel import Session, select
    from test_sweep14_bugs_common import build_world, diff, dump, make_due, orphans, put_back, snapshot

    from app import restore
    from app.config import settings
    from app.db import engine, init_db
    from app.jobs.scheduler import jobs
    from app.main import app
    from app.models import JobRun

    init_db()
    owner = TestClient(app)
    assert owner.post("/api/auth/login", json={"username": settings.username, "password": settings.password}).status_code == 200
    owner.headers["X-Requested-With"] = "FinancialAuthority"
    build_world(owner, bills=False)  # the bills list re-read is its own finding (test_sweep14_bugs.py), kept out of this count
    base = snapshot(scratch / "base")

    def run_jobs() -> None:
        for name in JOBS:
            jobs.run(name)

    def fork(work) -> int:  # noqa: ANN001
        engine.dispose()  # no connection crosses the fork
        pid = os.fork()
        if pid == 0:
            code = 0
            try:
                engine.dispose()
                work()
            except BaseException:  # noqa: BLE001
                traceback.print_exc()
                code = 1
            os._exit(code)
        return pid

    def finish(pid: int) -> int:
        _, status = os.waitpid(pid, 0)
        return os.waitstatus_to_exitcode(status)

    def restart_and_finish() -> tuple[int, list[str]]:
        make_due()  # a later hour: the link is due again
        since = time.time()
        code = finish(fork(run_jobs))
        engine.dispose()
        with Session(engine) as session:
            failed = [f"{r.name}: {r.summary[:200]}" for r in session.exec(select(JobRun)).all() if not r.ok and r.started_at.timestamp() >= since - 1]
        return code, failed

    def state() -> tuple[dict, list[str]]:
        engine.dispose()
        problems = list(orphans())
        con = sqlite3.connect(str(settings.db_path), timeout=0)
        try:
            verdict = con.execute("PRAGMA integrity_check").fetchone()[0]
            if verdict != "ok":
                problems.append(f"integrity_check: {verdict}")
            try:
                con.execute("BEGIN IMMEDIATE")  # nothing on disk still holds the write lock
                con.execute("ROLLBACK")
            except sqlite3.OperationalError as error:
                problems.append(f"the write lock is still held: {error}")
        finally:
            con.close()
        for p in restore.list_points():
            path = restore.folder() / p["name"]
            if not p["verified"] or not restore.check(path, quick=True)["ok"]:
                problems.append(f"restore point {p['name']} is listed but not whole")
        for stray in restore.folder().glob("*"):
            if stray.name.endswith(("-wal", "-shm")) and ".partial" not in stray.name:  # a half-made copy's journal goes with the copy, swept after an hour
                problems.append(f"a journal left beside a restore point: {stray.name}")
        return dump(), problems

    # The uninterrupted run, and a later one, as each trial's two processes are.
    put_back(base)
    started = time.time()
    assert finish(fork(run_jobs)) == 0
    took = time.time() - started
    code, failed = restart_and_finish()
    assert code == 0 and not failed, failed
    reference, problems = state()
    assert not problems, problems

    rng = random.Random(SEED)
    findings: list[str] = []
    killed = 0
    for trial in range(TRIALS):
        put_back(base)
        delay = rng.uniform(0.0, took)
        pid = fork(run_jobs)
        time.sleep(delay)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        killed += int(finish(pid) == -signal.SIGKILL)
        code, failed = restart_and_finish()
        got, problems = state()
        if code != 0 or failed:
            findings.append(f"trial {trial} (killed at {delay:.3f}s): the restarted jobs failed: {failed}")
        for line in diff(reference, got) + problems:
            findings.append(f"trial {trial} (killed at {delay:.3f}s): {line[:400]}")
    return {"trials": TRIALS, "killed": killed, "run_seconds": round(took, 3), "findings": findings}


if __name__ == "__main__":
    os.environ.setdefault("FA_PASSWORD", "correct horse battery")
    os.environ.setdefault("FA_USERNAME", "owner")
    os.environ.setdefault("FA_SESSION_SECRET", "test-session-secret")
    os.environ.setdefault("FA_DATA_KEY", "test-data-key")
    folder = Path(sys.argv[1])
    out = _driver(folder)
    print(json.dumps(out))
