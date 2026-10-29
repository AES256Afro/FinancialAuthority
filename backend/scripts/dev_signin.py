"""Run the app on this computer with sign-in on, the synthetic household and a throwaway data folder, to try the
sign-in screens (a second person joining, passkeys at localhost, the second step). The password is the test
suite's own (tests/conftest.py); nothing real is involved.

    uv run --directory backend python scripts/dev_signin.py        then open http://localhost:8001
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.update({
    "FA_DATA_DIR": tempfile.mkdtemp(prefix="fa-signin-"), "FA_DEMO": "true", "TZ": "America/Chicago", "FA_JOBS": "false", "FA_USERNAME": "owner",
    "FA_PASSWORD": "correct horse battery", "FA_SESSION_SECRET": "dev-signin-session", "FA_DATA_KEY": "dev-signin-data-key", "FA_PUBLIC_URL": "",
})
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=int(os.environ.get("FA_DEV_PORT", "8001")))
