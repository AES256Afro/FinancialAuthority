"""One writer at a time. The jobs and the routes that rebuild what the app works out from the ledger (detection,
payment matching, a bank sync, an import, an undo, a restore) run in their own threads; each reads the registers,
then writes what it found. Two at once would each read before the other wrote and both add the same series, the
same rows or the same bills. They take this lock, so the second waits for the first and reads what it wrote.

The lock is re-entrant: a bank sync that runs detection inside it does not wait on itself."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

_lock = threading.RLock()


@contextmanager
def one_writer() -> Iterator[None]:
    with _lock:
        yield
