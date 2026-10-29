"""Round thirteen: the box around the app. The image, the pipeline that builds it and the files the app leaves on the
server. These read the repository's own Dockerfile, workflow and compose file, and the restore folder's modes."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import restore
from app.config import settings

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = (REPO / "docker" / "Dockerfile").read_text(encoding="utf-8")
DOCKERIGNORE = (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
WORKFLOWS = sorted((REPO / ".github" / "workflows").glob("*.yml"))
COMPOSE = (REPO / "deploy" / "compose" / "docker-compose.yml").read_text(encoding="utf-8")


# ---- the image ---------------------------------------------------------------------------------------------------


def test_every_base_and_tool_image_is_pinned_by_digest():
    froms = re.findall(r"^FROM\s+(\S+)", DOCKERFILE, flags=re.M)
    assert froms, "no FROM line"
    for ref in froms:
        assert re.search(r"@sha256:[0-9a-f]{64}$", ref), f"{ref} is not pinned by digest"


def test_uv_is_never_fetched_unpinned_and_never_ships():
    """It was `pip install uv` (the newest release, no version or hash) and stayed in the image as /usr/local/bin/uv."""
    code = "\n".join(line for line in DOCKERFILE.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"pip3? install[^\n]*\buv\b", code)
    assert "--mount=from=uv" in DOCKERFILE


def test_only_locked_dependencies_are_installed():
    """Installing the project itself builds it with hatchling, which uv.lock does not pin: an unlocked fetch."""
    syncs = re.findall(r"uv sync[^\n\\]*", DOCKERFILE)
    assert syncs
    for line in syncs:
        assert "--frozen" in line and "--no-dev" in line and "--no-install-project" in line, line


def test_the_image_takes_debian_security_fixes_and_drops_setuid_and_pip():
    assert "apt-get upgrade" in DOCKERFILE
    assert re.search(r"find / -xdev -type f -perm /6000 -exec chmod a-s", DOCKERFILE)
    assert "pip uninstall -y" in DOCKERFILE


def test_the_app_runs_as_its_own_user():
    users = re.findall(r"^USER\s+(\S+)", DOCKERFILE, flags=re.M)
    assert users and users[-1] == "app"


@pytest.mark.parametrize("path", ["backend/tests", "backend/scripts", ".env", ".git", "**/node_modules", "**/.venv"])
def test_what_should_not_ship_is_kept_out_of_the_build_context(path: str):
    assert path in DOCKERIGNORE


# ---- the pipeline ------------------------------------------------------------------------------------------------


def _uses(text: str) -> list[str]:
    return re.findall(r"^\s*(?:-\s*)?uses:\s*(\S+)", text, flags=re.M)


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_commit(workflow: Path):
    for ref in _uses(workflow.read_text(encoding="utf-8")):
        if ref.startswith("./"):
            continue
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref), f"{workflow.name}: {ref} is not pinned to a commit"


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_no_event_text_reaches_a_shell_and_no_privileged_pull_request_trigger(workflow: Path):
    text = workflow.read_text(encoding="utf-8")
    assert "pull_request_target" not in text
    assert "workflow_run" not in text
    # A PR title, branch name or comment interpolated into a run: script is a shell injection; none is used at all.
    assert not re.search(r"\$\{\{\s*github\.(event\.|head_ref)", text)


def test_the_token_is_read_only_except_where_the_publish_job_needs_more():
    text = (REPO / ".github" / "workflows" / "image.yml").read_text(encoding="utf-8")
    top = text.split("\njobs:", 1)[0]
    assert re.search(r"^permissions:\s*\n\s+contents: read\s*$", top, flags=re.M)
    assert text.count("packages: write") == 1
    assert "if: github.event_name != 'pull_request'" in text  # a pull request never reaches the package write


def test_each_published_image_carries_a_build_provenance_attestation():
    text = (REPO / ".github" / "workflows" / "image.yml").read_text(encoding="utf-8")
    assert text.count("actions/attest-build-provenance@") == 2
    assert "subject-digest: ${{ steps.build.outputs.digest }}" in text
    assert "subject-digest: ${{ steps.build-bridge.outputs.digest }}" in text
    assert "id-token: write" in text and "attestations: write" in text


def test_the_lockfiles_carry_integrity_hashes():
    uv = (REPO / "backend" / "uv.lock").read_text(encoding="utf-8")
    npm = (REPO / "frontend" / "package-lock.json").read_text(encoding="utf-8")
    assert uv.count('hash = "sha256:') > 100
    assert npm.count('"resolved"') == npm.count('"integrity": "sha512-')


# ---- the standalone compose file ---------------------------------------------------------------------------------


def test_the_standalone_app_is_read_only_without_capabilities_and_bound_to_loopback():
    app_service = COMPOSE.split("\n  bridge:", 1)[0]
    assert "cap_drop: [ALL]" in app_service
    assert "read_only: true" in app_service
    assert "- /tmp" in app_service
    assert "no-new-privileges:true" in app_service
    assert '"127.0.0.1:${FA_PORT:-8430}:8000"' in app_service
    bridge = COMPOSE.split("\n  bridge:", 1)[1]
    assert "ports:" not in bridge  # its IMAP and SMTP take the vault password: the app's network only
    assert "no-new-privileges:true" in bridge


# ---- the files the app leaves on the server ----------------------------------------------------------------------


def test_restore_points_and_documents_an_older_version_left_readable_are_made_owner_only(tmp_path: Path, monkeypatch):
    """0.10.4 on the server still had restore points, their -shm files and a kept document at 0644 (made before 0.10.2
    set new ones to 0600); each holds the household, so the start brings them into line."""
    points = tmp_path / "points"
    data = tmp_path / "data"
    points.mkdir()
    (data / restore.DOCUMENTS).mkdir(parents=True)
    monkeypatch.setattr(settings, "restore_dir", points)
    monkeypatch.setattr(settings, "data_dir", data)
    old = [points / "20261022-194858-076647-daily.db", points / "20261022-194858-076647-daily.db-shm",
           points / "20261022-194858-076647-daily.json", data / restore.DOCUMENTS / ("a" * 64 + ".bin")]
    for path in old:
        path.write_bytes(b"x")
        path.chmod(0o644)
    fine = points / "20261028-194858-081059-daily.db"
    fine.write_bytes(b"x")
    fine.chmod(0o600)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_bytes(b"x")
    elsewhere.chmod(0o644)
    (points / "link.db").symlink_to(elsewhere)

    assert restore.owner_only_files() == len(old)
    for path in [*old, fine]:
        assert path.stat().st_mode & 0o777 == 0o600, path.name
    assert elsewhere.stat().st_mode & 0o777 == 0o644  # a link is never followed out of the folder
    assert restore.owner_only_files() == 0


def test_a_new_restore_point_and_its_description_are_owner_only(tmp_path: Path, monkeypatch):
    import sqlite3

    db = tmp_path / "live.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (x)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()
    monkeypatch.setattr(settings, "restore_dir", tmp_path / "points")
    monkeypatch.setattr(type(settings), "db_path", property(lambda self: db))
    made = restore.make_point("manual", "test")
    point = tmp_path / "points" / made["name"]
    assert point.stat().st_mode & 0o777 == 0o600
    assert point.with_suffix(".json").stat().st_mode & 0o777 == 0o600
