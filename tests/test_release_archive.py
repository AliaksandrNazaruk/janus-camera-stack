"""Release-archive no-secrets gate (#1 hardening, post 2026-06-20 leak).

The incident: a gitignored `host_infra/secrets.yml` was FOLLOWED into a hand-built
tar — `.gitignore` protects git, not release artifacts. This test runs the real
`scripts/build_release_archive.sh` (which uses the real `scripts/release_excludes.txt`)
against a fixture tree and asserts secrets are excluded and the placeholder ships.

Because the test invokes the actual script + exclude list, it also satisfies
"fails if the exclude list is gutted": drop the secrets.yml pattern and the script's
fail-closed verify aborts (returncode != 0) → this test fails.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tarfile

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SCRIPT = os.path.join(_SERVICE_ROOT, "scripts", "build_release_archive.sh")


def test_build_script_and_excludes_exist():
    assert os.path.exists(_SCRIPT), "release build script missing"
    assert os.access(_SCRIPT, os.X_OK), "release build script not executable"
    assert os.path.exists(os.path.join(_SERVICE_ROOT, "scripts", "release_excludes.txt"))


def test_release_archive_excludes_real_secrets(tmp_path):
    # --- fixture project tree ---
    proj = tmp_path / "proj"
    (proj / "host_infra").mkdir(parents=True)
    (proj / "app").mkdir()
    (proj / "host_infra" / "secrets.yml").write_text("janus_streaming_admin_key: REALSECRET\n")
    (proj / "host_infra" / "secrets.yml.example").write_text("janus_streaming_admin_key: CHANGEME\n")
    (proj / "host_infra" / "node.pre-rotate-20260620_090725").write_text("old dead secret\n")
    (proj / "host_infra" / "tls.key").write_text("PRIVATE KEY\n")
    (proj / "host_infra" / "tls.pem").write_text("CERT\n")
    (proj / "host_infra" / "agent.token").write_text("bearer\n")
    (proj / "camera-secrets.env").write_text("JANUS_STREAMING_ADMIN_KEY=secret\n")
    (proj / "app" / "main.py").write_text("print('hi')\n")
    pyc = proj / "app" / "__pycache__"
    pyc.mkdir()
    (pyc / "main.cpython-312.pyc").write_text("bytecode\n")
    # A1 hygiene artifacts (post 2026-06-21 review) — must ALSO be excluded
    (proj / "old_review_20260101.tar.gz").write_text("stale nested archive\n")
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.local.json").write_text('{"local": true}\n')
    (proj / "scripts").mkdir()
    (proj / "scripts" / "soak_20260101_0000.csv").write_text("a,b\n1,2\n")

    out = tmp_path / "release.tar.gz"
    r = subprocess.run(["bash", _SCRIPT, str(proj), str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"build failed (fail-closed?):\nSTDOUT:{r.stdout}\nSTDERR:{r.stderr}"

    names = tarfile.open(out).getnames()

    # MUST be excluded
    assert not any(n.endswith("host_infra/secrets.yml") for n in names), names
    assert not any(".pre-rotate-" in n for n in names), names
    assert not any(n.endswith(".key") for n in names), names
    assert not any(n.endswith(".pem") for n in names), names
    assert not any(n.endswith(".token") for n in names), names
    assert not any(n.endswith("/camera-secrets.env") for n in names), names
    assert not any("__pycache__" in n for n in names), names
    # A1 hygiene: nested archives / local dev settings / generated soak runs must NOT ship
    assert not any(n.endswith((".tar", ".tgz", ".tar.gz")) for n in names), names
    assert not any("/.claude/" in n or n.endswith("/.claude") for n in names), names
    assert not any(n.endswith(".local.json") for n in names), names
    assert not any("soak_" in n and n.endswith(".csv") for n in names), names

    # MUST be present (placeholder + real code)
    assert any(n.endswith("host_infra/secrets.yml.example") for n in names), names
    assert any(n.endswith("app/main.py") for n in names), names


def test_real_release_archive_is_clean(tmp_path):
    """A1 regression lock: build the REAL service archive (not a fixture) and assert no nested
    archives, no .claude/, no local settings, and no secrets ship. Catches a stray artifact in the
    actual tree + a gutted exclude list (the script fails closed → returncode != 0 here)."""
    out = tmp_path / "real_release.tar.gz"
    r = subprocess.run(["bash", _SCRIPT, _SERVICE_ROOT, str(out)], capture_output=True, text=True)
    assert r.returncode == 0, f"real build failed (fail-closed?):\nSTDOUT:{r.stdout}\nSTDERR:{r.stderr}"
    names = tarfile.open(out).getnames()
    nested = [n for n in names if n.endswith((".tar", ".tgz", ".tar.gz"))]
    claude = [n for n in names if "/.claude/" in n]
    locals_ = [n for n in names if n.endswith(".local.json")]
    assert not nested, f"nested archives shipped: {nested}"
    assert not claude, f".claude/ shipped: {claude}"
    assert not locals_, f"local settings shipped: {locals_}"
    assert not any(n.endswith("host_infra/secrets.yml") for n in names), "real secrets.yml shipped!"
    # sanity: real code + the placeholder DO ship (no over-exclusion)
    assert any(n.endswith("/requirements.txt") for n in names)
    assert any(n.endswith("app/services/stream_binding_store/__init__.py") for n in names)
    assert any(n.endswith("host_infra/secrets.yml.example") for n in names)


def test_gutting_the_exclude_list_makes_the_build_fail_closed(tmp_path):
    """Directly prove the fail-closed contract: with an exclude list missing the
    secrets pattern, the script's verify must abort (so the secret can't ship)."""
    proj = tmp_path / "proj"
    (proj / "host_infra").mkdir(parents=True)
    (proj / "host_infra" / "secrets.yml").write_text("k: REALSECRET\n")
    (proj / "host_infra" / "secrets.yml.example").write_text("k: CHANGEME\n")

    # a gutted exclude list (no secrets.yml pattern) + a copy of the script pointed at it
    gutted = tmp_path / "scripts"
    gutted.mkdir()
    (gutted / "release_excludes.txt").write_text("__pycache__\n*.pyc\n")
    import shutil
    shutil.copy(_SCRIPT, gutted / "build_release_archive.sh")

    out = tmp_path / "bad.tar.gz"
    r = subprocess.run(["bash", str(gutted / "build_release_archive.sh"), str(proj), str(out)],
                       capture_output=True, text=True)
    assert r.returncode != 0, "build should FAIL CLOSED when secrets.yml is not excluded"
    assert not out.exists(), "bad archive must be deleted on fail-closed"
