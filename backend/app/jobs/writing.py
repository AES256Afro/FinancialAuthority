"""One writer at a time. The jobs and the routes that rebuild what the app works out from the ledger (detection,
payment matching, a bank sync, an import, an undo, a restore) run in their own threads; each reads the registers,
then writes what it found. Two at once would each read before the other wrote and both add the same series, the
same rows or the same bills. They take this lock, so the second waits for the first and reads what it wrote.

The lock is re-entrant: a bank sync that runs detection inside it does not wait on itself.

An erase or a restore replaces what everything else was working from, so it does two more things (sweep 26). A job begun
before it is stopped at its next write, whether or not the job takes this lock: a job that read, told the phone and then
wrote its record put back what the erase had just removed. And while it runs no new write request starts, and those on
the way are given a moment to finish: a key revoked or a send approved in that moment answered "ok", and then the restore
carried in the state from before it."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_lock = threading.RLock()


@contextmanager
def one_writer() -> Iterator[None]:
    with _lock:
        yield


class DataReplaced(RuntimeError):
    """The data this job began from was erased or restored while it ran."""


STOPPED = "not due: the data was erased or restored while this ran; the next run starts from what is there now"
_era = [0]
_job = threading.local()
_gate = threading.Condition()
_writes_open = [0]
_closed = [0]
_a_write = ContextVar("fa_a_write_request", default=False)
DRAIN_SECONDS = 5.0  # how long a replacement waits for write requests under way; a chat's long stream does not hold it up


def era() -> int:
    """Moves when the data is replaced. A long read that began under one era writes nothing under another."""
    return _era[0]


@contextmanager
def job_era() -> Iterator[None]:
    """Mark this thread as a job, so its writes stop if the data is replaced under it."""
    _job.era = era()
    try:
        yield
    finally:
        _job.era = None


def job_outlived() -> bool:
    """Whether this thread is a job that began before the data was replaced."""
    begun = getattr(_job, "era", None)
    return begun is not None and begun != era()


def refuse_if_outlived() -> None:
    if job_outlived():
        raise DataReplaced(STOPPED)


def enter_write() -> bool:
    """A write request begins; False while an erase or a restore runs, and it is refused with a sentence."""
    with _gate:
        if _closed[0]:
            return False
        _writes_open[0] += 1
        _a_write.set(True)
        return True


def leave_write() -> None:
    with _gate:
        _writes_open[0] -= 1
        _gate.notify_all()


@contextmanager
def replacing_data() -> Iterator[None]:
    """Around an erase or a restore, inside the writer lock: no new write request, those under way drained (the one asking
    is one of them), and every job begun before this stopped at its next write."""
    import time

    mine = 1 if _a_write.get() else 0
    with _gate:
        _closed[0] += 1
        deadline = time.monotonic() + DRAIN_SECONDS
        while _writes_open[0] > mine and (left := deadline - time.monotonic()) > 0:
            _gate.wait(left)
    _era[0] += 1
    try:
        yield
    finally:
        _era[0] += 1
        with _gate:
            _closed[0] -= 1
