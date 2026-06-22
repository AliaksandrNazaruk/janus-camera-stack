"""Deeper executable architecture fitness tests.

Goes beyond `test_boundary_fitness.py` (which checks shell-out patterns).
This file enforces:

1. **L4 import boundaries** — L4 production code cannot import
   `camera_bringup` internals (only public api or CLI subprocess).
2. **Layer naming convention** — admin CLI binaries must follow
   `/usr/local/bin/{layer}-admin` pattern (no direct paths in code).
3. **Settings ownership** — env var lookups consolidated in settings.py
   (no scattered `os.getenv` calls in business logic).
4. **No legacy paths** — detects leftover references to removed
   files / dead code paths.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_APP_ROOT = Path(__file__).resolve().parent.parent / "app"


def _production_py_files() -> list[Path]:
    return [p for p in _APP_ROOT.rglob("*.py") if "__pycache__" not in p.parts]


# ── 1. Import boundary tests ──────────────────────────────────────────

def test_l4_does_not_import_camera_bringup_internals():
    """L4 may invoke camera_bringup CLI via subprocess but not import internals.

    Allowed:
      - subprocess.run([python_path, "-m", "camera_bringup", ...])
      - subprocess.run(["sudo", "/usr/local/bin/camera-admin", ...])

    Forbidden:
      - from camera_bringup.checks import ...
      - import camera_bringup.fixers
      etc.

    Public API exception: `from camera_bringup.api import L0` would be OK
    BUT currently L4 does not use the L0 facade directly (it goes via CLI).
    Add to ALLOWED_IMPORTS if/when public API consumed directly.
    """
    ALLOWED_IMPORTS = {"camera_bringup.api"}  # extensible whitelist

    violations: list[tuple[Path, int, str]] = []

    for f in _production_py_files():
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("camera_bringup"):
                        if alias.name not in ALLOWED_IMPORTS:
                            violations.append((f.relative_to(_APP_ROOT.parent),
                                               node.lineno, f"import {alias.name}"))
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod.startswith("camera_bringup"):
                    if mod not in ALLOWED_IMPORTS:
                        names = ", ".join(a.name for a in node.names)
                        violations.append((f.relative_to(_APP_ROOT.parent),
                                           node.lineno, f"from {mod} import {names}"))

    if violations:
        details = "\n".join(f"  {f}:{lineno}: {line}" for f, lineno, line in violations)
        pytest.fail(
            f"\nL4 production code imports camera_bringup internals (boundary violation):\n"
            f"{details}\n\n"
            f"L4 must invoke camera_bringup via subprocess (camera-admin CLI) or\n"
            f"public API only. Add to ALLOWED_IMPORTS if intentional."
        )


def test_l4_does_not_import_host_infra_anything():
    """host_infra is Ansible role — should never be Python-importable from L4."""
    violations: list[tuple[Path, int, str]] = []
    for f in _production_py_files():
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                else:
                    if node.module:
                        names = [node.module]
                for n in names:
                    if n.startswith("host_infra"):
                        violations.append((f.relative_to(_APP_ROOT.parent),
                                           node.lineno, n))
    if violations:
        details = "\n".join(f"  {f}:{lineno}: {n}" for f, lineno, n in violations)
        pytest.fail(f"\nL4 imports host_infra (Ansible role, not a library):\n{details}")


# ── 2. Admin CLI naming convention ────────────────────────────────────

def test_admin_cli_paths_follow_convention():
    """L4 subprocess calls to /usr/local/bin/*-admin MUST match approved set.

    Approved:
      janus-admin   (L3)
      encoder-admin (L2)
      camera-admin  (L0)

    Detects rogue admin binaries or typos.
    """
    APPROVED_ADMINS = {"janus-admin", "encoder-admin", "camera-admin", "service-admin"}
    # Match string literal '/usr/local/bin/<something>-admin'
    pattern = re.compile(r'/usr/local/bin/([a-z-]+-admin)')
    violations: list[tuple[Path, int, str]] = []

    for f in _production_py_files():
        for lineno, line in enumerate(f.read_text().splitlines(), start=1):
            if line.strip().startswith("#"):
                continue
            for m in pattern.finditer(line):
                name = m.group(1)
                if name not in APPROVED_ADMINS:
                    violations.append((f.relative_to(_APP_ROOT.parent), lineno, name))

    if violations:
        details = "\n".join(f"  {f}:{lineno}: {name}" for f, lineno, name in violations)
        pytest.fail(
            f"\nUnknown admin CLI binaries referenced. "
            f"Approved: {sorted(APPROVED_ADMINS)}\n{details}"
        )


def test_admin_cli_calls_use_sudo():
    """Admin CLI subprocess calls MUST go through sudo.

    Simple heuristic: any line that contains `/usr/local/bin/X-admin` string
    literal in a subprocess list must also contain `"sudo"` somewhere on the same
    line. Detects misuse where caller calls admin binary directly.
    """
    admin_pattern = re.compile(r'["\']\/usr\/local\/bin\/[a-z-]+-admin["\']')
    violations: list[tuple[Path, int, str]] = []

    for f in _production_py_files():
        for lineno, line in enumerate(f.read_text().splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if not admin_pattern.search(line):
                continue
            # Require sudo on same line — simplest check.
            if '"sudo"' not in line and "'sudo'" not in line:
                violations.append((f.relative_to(_APP_ROOT.parent), lineno, stripped))

    if violations:
        details = "\n".join(f"  {f}:{lineno}: {line}" for f, lineno, line in violations)
        pytest.fail(
            f"\nAdmin CLI invocations without sudo prefix:\n{details}\n"
            f"All admin binaries require sudo (NOPASSWD scoped in /etc/sudoers.d/)."
        )


# ── 3. Settings ownership (env vars in settings.py, not scattered) ────

def test_no_scattered_os_getenv_in_routes_only():
    """`os.getenv` / `os.environ.get` MUST NOT appear in `routes/`.

    Route handlers should accept settings via `Depends(get_settings)` or
    module-level Settings import. Scattered env reads in routes are a config
    drift signal (test setup becomes painful, behavior differs by env).

    `services/` modules are allowed module-level env constants (perf tunables,
    log paths) — they're encapsulated internal config, not request-path
    business state. See CONTRACT.md "Configuration" section.
    """
    pattern = re.compile(r'os\.(getenv|environ)')
    violations: list[tuple[Path, int, str]] = []

    routes_dir = _APP_ROOT / "routes"
    for f in routes_dir.rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        for lineno, line in enumerate(f.read_text().splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if pattern.search(line):
                violations.append((f.relative_to(_APP_ROOT.parent), lineno, stripped))

    if violations:
        details = "\n".join(f"  {f}:{lineno}: {line}" for f, lineno, line in violations)
        pytest.fail(
            f"\nScattered os.getenv/environ calls in routes/ (config drift risk):\n{details}\n"
            f"Move env reads to core/settings.py Settings class, "
            f"access via `Depends(get_settings)` or module import."
        )


# ── 4. Dead-path detection ────────────────────────────────────────────

def test_no_references_to_removed_paths():
    """Guards against stale references to paths that were removed.

    Includes recently-deprecated files documented in CHANGES.md.
    """
    DEAD_PATHS = [
        # janus-apply-config.sh — removed due to path injection vuln
        "janus-apply-config",
        # legacy cam-rgb.env single file — split into contract.env + tuning.env
        "/etc/robot/cam-rgb.env",
    ]
    violations: list[tuple[Path, int, str]] = []

    for f in _production_py_files():
        for lineno, line in enumerate(f.read_text().splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for dead in DEAD_PATHS:
                if dead in line:
                    violations.append((f.relative_to(_APP_ROOT.parent), lineno, stripped))

    if violations:
        details = "\n".join(f"  {f}:{lineno}: {line}" for f, lineno, line in violations)
        pytest.fail(
            f"\nReferences to removed/deprecated paths detected:\n{details}\n"
            f"See CHANGES.md for context. Update references to current architecture."
        )


# ── 5. Test infrastructure (sanity checks) ────────────────────────────

def test_approved_imports_set_is_documented_in_contract():
    """ALLOWED_IMPORTS additions MUST also appear in L4 CONTRACT.md
    (either as public API contract or as documented exception).

    Currently the set is empty/minimal; this test guards against
    silent expansion. If you add to ALLOWED_IMPORTS:
      1. Document why in CONTRACT.md
      2. This test will pass (mention found)
    """
    # Hardcoded sync with test_l4_does_not_import_camera_bringup_internals
    ALLOWED = {"camera_bringup.api"}
    contract = _APP_ROOT.parent / "docs" / "CONTRACT.md"
    assert contract.exists(), "L4 CONTRACT.md missing"
    text = contract.read_text()
    missing = [a for a in ALLOWED if a not in text]
    if missing:
        pytest.fail(
            f"\nALLOWED_IMPORTS entries not documented in CONTRACT.md: {missing}\n"
            f"Add description of why public API access is granted."
        )


# ── 6. Route purity: no infra primitives in routes/ (UNCONDITIONAL) ──
#
# C-04 Phase 4 ratchet, fully tightened by route-purity Phase 7. `routes/` is the HTTP
# boundary (auth + parse + delegate). Raw `subprocess` / `systemctl` / `httpx` are infra
# side-effects — they belong in `app/services/*` adapters, reached through `app/application/*`
# use-cases. Routes may still import the adapters (`from app.services import systemd`) — that's
# the intended delegation, not a violation.
#
# The campaign drained every route file infra-free (see docs/design/ROUTE_PURITY_CLOSEOUT.md):
# admin_dashboard (C-04 Phases 1-4), admin_config (Phase 5), depth (Phase 6). With the baseline
# empty, Phase 7 removed the exception mechanism entirely — this guard is now UNCONDITIONAL.
# There is intentionally no allowlist: a new infra primitive in routes/ is always a failure.


def _module_import_lines(tree: ast.AST, target: str) -> list[int]:
    """Lines where `target` (e.g. 'subprocess') is imported — incl. function-level imports."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == target or a.name.startswith(target + "."):
                    hits.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == target or mod.startswith(target + "."):
                hits.append(node.lineno)
    return hits


def test_routes_have_no_subprocess_systemctl_httpx():
    """No raw subprocess/httpx import and no `"systemctl"` command literal in ANY routes/
    file (unconditional — no allowlist). Prevents the C-04/route-purity split from unwinding.

    `subprocess`/`httpx` are detected via AST imports (so docstring mentions like
    "no raw systemctl" don't false-positive). `systemctl` is detected as a *quoted*
    command literal (`["systemctl", ...]`), which likewise skips prose mentions.
    """
    routes_dir = _APP_ROOT / "routes"
    systemctl_literal = re.compile(r'''["']systemctl["']''')
    violations: list[tuple[str, int, str]] = []

    for f in routes_dir.rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        rel = str(f.relative_to(_APP_ROOT.parent))
        src = f.read_text()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for mod in ("subprocess", "httpx"):
            for lineno in _module_import_lines(tree, mod):
                violations.append((rel, lineno, f"imports {mod}"))
        for lineno, line in enumerate(src.splitlines(), start=1):
            if line.strip().startswith("#"):
                continue
            if systemctl_literal.search(line):
                violations.append((rel, lineno, line.strip()))

    if violations:
        details = "\n".join(f"  {f}:{lineno}: {what}" for f, lineno, what in violations)
        pytest.fail(
            "\nInfra primitives (subprocess/systemctl/httpx) found in routes/:\n" + details +
            "\n\nRoutes are the HTTP boundary — move the side effect into an app/services "
            "adapter and call it via an app/application use-case (see admin_dashboard.py / "
            "admin_config.py / depth.py for the pattern). This guard is unconditional: there "
            "is no allowlist."
        )


# D3: application use-cases + service domain logic raise DOMAIN errors and the route maps them to
# HTTP. The ONLY legitimate HTTPException users are the HTTP/WS-proxy ADAPTERS — they ARE the HTTP
# boundary (take a fastapi Request, return a fastapi Response, pass upstream status through). They
# can't be "de-leaked" without ceasing to be proxies, so they're an explicit allowlist.
_HTTP_ADAPTER_ALLOWLIST = {
    # Phase 5: depth_mux_proxy moved application/ -> services/ so application/ is FastAPI-free with
    # NO allowlist. Every allowlisted adapter now lives in services/ (enforced below).
    "app/services/depth_mux_proxy.py",
    "app/services/proxy_base.py",
    "app/services/depth_camera_proxy.py",
    "app/services/janus_proxy.py",
    "app/services/realsense_mux_proxy.py",
    "app/services/ws_proxy.py",
}


def test_application_and_services_are_fastapi_free_except_proxy_adapters():
    """No `HTTPException` in app/application/** or app/services/** (D3) — domain code raises domain
    errors; the route maps them. The only exceptions are the allowlisted HTTP/WS-proxy adapters."""
    violations: list[tuple[str, int]] = []
    for sub in ("application", "services"):
        for f in (_APP_ROOT / sub).rglob("*.py"):
            if "__pycache__" in f.parts:
                continue
            rel = str(f.relative_to(_APP_ROOT.parent))
            if rel in _HTTP_ADAPTER_ALLOWLIST:
                continue
            for lineno, line in enumerate(f.read_text().splitlines(), start=1):
                if line.strip().startswith("#"):
                    continue
                if "HTTPException" in line:
                    violations.append((rel, lineno))
    if violations:
        details = "\n".join(f"  {f}:{lineno}" for f, lineno in violations)
        pytest.fail(
            "\nHTTPException found outside the route layer (D3 — application/services must be "
            "FastAPI-free):\n" + details +
            "\n\nRaise a DOMAIN error in the use-case/service and map it to HTTPException in the "
            "route (see application/stream_bindings/* or application/encoder_admin.py). If this is "
            "a genuine HTTP/WS-proxy adapter, add it to _HTTP_ADAPTER_ALLOWLIST."
        )


# ── 10. Routes do no durable file writes — persistence lives in app/services (A-10) ──
_ROUTE_FILE_WRITE = re.compile(
    r"\.(write_text|write_bytes|writelines)\s*\("
    r"|\bjson\.dump\s*\("
    r"|\bos\.(replace|rename)\s*\("
    r"|\bshutil\.(copy|copy2|copyfile|move)\s*\("
    r"|\bopen\s*\([^)]*[\"'][wax]\+?b?[\"']"
)


def test_routes_do_not_write_files():
    """Routes are the HTTP boundary; durable writes (state files, journals, rendered configs) belong
    in app/services adapters, invoked through app/application use-cases. Flags write primitives
    (write_text/write_bytes/writelines/json.dump/os.replace|rename/shutil.copy*|move/open(...,'w'|'a'|'x'))
    anywhere under routes/. Unconditional regression-lock — the layer is clean today."""
    routes_dir = _APP_ROOT / "routes"
    violations: list[tuple[str, int, str]] = []
    for f in routes_dir.rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        rel = str(f.relative_to(_APP_ROOT.parent))
        for lineno, line in enumerate(f.read_text().splitlines(), start=1):
            if line.strip().startswith("#"):
                continue
            if _ROUTE_FILE_WRITE.search(line):
                violations.append((rel, lineno, line.strip()))
    if violations:
        details = "\n".join(f"  {f}:{lineno}: {ln}" for f, lineno, ln in violations)
        pytest.fail(
            "\nFile-write primitive in routes/ (routes must not persist state):\n" + details +
            "\n\nMove the write into an app/services adapter (atomic flock'd write — see "
            "stream_binding_store/state_file.py or operation_journal.py) and call it from an "
            "app/application use-case. Unconditional: no allowlist."
        )


# ── 11. Routes do not import sibling routes — route->route coupling (A-01) ──
# UNCONDITIONAL (Phase 2B-6 emptied the ratchet allowlist). The device_camera coupling that seeded it
# was eliminated: CameraStreamConfig/CameraMode/CameraModesResponse -> application/camera/contracts;
# get/update_camera_stream_config -> application/camera/color_config; modes/sensors -> the v4l2 /
# realsense_catalog services directly; _api_prefix_from_request -> app/core/http_prefix. There is no
# allowlist — a new route->route import is always a failure.
def _app_routes_import_targets(tree: ast.AST) -> list[tuple[int, str]]:
    """(lineno, dotted-target) for every absolute import of an ``app.routes`` module."""
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "app.routes" or a.name.startswith("app.routes."):
                    out.append((node.lineno, a.name))
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            mod = node.module or ""
            if mod == "app.routes" or mod.startswith("app.routes."):
                out.append((node.lineno, mod))
    return out


def test_routes_do_not_import_sibling_routes():
    """A route importing ANOTHER route couples two HTTP boundaries; shared logic belongs in
    app/application (use-case) or app/services (adapter), not a sibling route. __init__.py is exempt
    (it mounts the routers). A route SUBPACKAGE's submodules may import their OWN package
    (app/routes/<pkg>/x.py -> app.routes.<pkg>) — that's intra-router cohesion (Cycle 5 split), not
    route->route coupling. Unconditional otherwise — there is no cross-route allowlist."""
    routes_dir = _APP_ROOT / "routes"
    violations: list[tuple[str, int]] = []
    for f in routes_dir.rglob("*.py"):
        if "__pycache__" in f.parts or f.name == "__init__.py":
            continue
        rel = str(f.relative_to(_APP_ROOT.parent))
        # If the file lives in a route SUBPACKAGE (app/routes/<pkg>/<file>.py), its own package is
        # an allowed import target (the submodules share the package's __init__ shared core).
        sub = f.relative_to(routes_dir).parts
        own_pkg = f"app.routes.{sub[0]}" if len(sub) > 1 else None
        for lineno, target in _app_routes_import_targets(ast.parse(f.read_text())):
            if own_pkg and (target == own_pkg or target.startswith(own_pkg + ".")):
                continue   # importing one's own router package — cohesion, not coupling
            violations.append((rel, lineno))
    if violations:
        details = "\n".join(f"  {f}:{lineno}" for f, lineno in violations)
        pytest.fail(
            "\nRoute->route import (routes must not import sibling routes):\n" + details +
            "\n\nExtract the shared helper into app/application or app/services and import THAT from "
            "both routes. This guard is unconditional — no allowlist."
        )


# ── 12. STRICT (Phase 5): app/application/** imports NO FastAPI — absolute, no allowlist ──
def test_application_layer_imports_no_fastapi():
    """app/application/** use-cases are pure orchestration over domain errors/results; the HTTP
    boundary (Request/Response/HTTPException) lives in routes and in the services/ proxy adapters.
    NO allowlist here — application/ is strictly FastAPI-free (depth_mux_proxy was moved to services/
    in Phase 5 to make this absolute). Stronger than guard #9, which only bans HTTPException."""
    violations: list[tuple[str, int]] = []
    for f in (_APP_ROOT / "application").rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        rel = str(f.relative_to(_APP_ROOT.parent))
        for lineno in _module_import_lines(ast.parse(f.read_text()), "fastapi"):
            violations.append((rel, lineno))
    if violations:
        details = "\n".join(f"  {f}:{lineno}" for f, lineno in violations)
        pytest.fail(
            "\nFastAPI imported in app/application/ (use-cases must be FastAPI-free — NO allowlist):\n"
            + details +
            "\n\nA genuine HTTP/WS-proxy adapter belongs in app/services/ (it IS the boundary); "
            "otherwise raise a domain error the route maps. application/ has no exceptions."
        )


def test_http_adapter_allowlist_is_services_only():
    """The FastAPI-boundary allowlist may contain ONLY services/ proxy adapters — never an
    application/ entry. Locks the Phase-5 invariant so an application-layer FastAPI exception cannot
    be re-introduced by quietly adding it to the allowlist."""
    app_entries = sorted(e for e in _HTTP_ADAPTER_ALLOWLIST if e.startswith("app/application/"))
    assert not app_entries, (
        "_HTTP_ADAPTER_ALLOWLIST must not contain application/ entries (move the adapter to "
        f"app/services/): {app_entries}"
    )


# ── 14. NO direct systemctl mutation in app/** — service control via the scoped service-admin CLI ──
# P1 closed the broad-sudo path: privileged start/stop/restart/reboot now go through
# services/service_control.py + recovery_executor → `sudo -n /usr/local/bin/service-admin ...` (a binary
# whose NOPASSWD sudoers grant is scoped to itself, with an internal unit allowlist). A
# `"systemctl","<action>"` argv literal anywhere in app/** bypasses that boundary. Reads
# (show/list-units / a bare systemctl_action with a variable action) are out of scope. ALLOWLIST
# EMPTIED by P1 — the guard is UNCONDITIONAL; it must stay empty.
_SYSTEMCTL_MUTATION = re.compile(
    r"""["']systemctl["']\s*,\s*["'](start|stop|restart|reload|reboot)["']""")
_SYSTEMCTL_MUTATION_ALLOWLIST: set[str] = set()


def test_systemctl_mutations_only_via_systemd_chokepoint():
    """No systemctl start|stop|restart|reload|reboot argv literal anywhere in app/** — all privileged
    service control goes through the scoped `service-admin` CLI (services/service_control.py +
    recovery_executor → `sudo -n /usr/local/bin/service-admin`). Reads (show/list-units / the bare
    systemctl_action) are out of scope. P1 EMPTIED the allowlist; the guard is unconditional."""
    violations: list[tuple[str, int, str]] = []
    for f in _production_py_files():
        rel = str(f.relative_to(_APP_ROOT.parent))
        if rel in _SYSTEMCTL_MUTATION_ALLOWLIST:
            continue
        for lineno, line in enumerate(f.read_text().splitlines(), start=1):
            if line.strip().startswith("#"):
                continue
            if _SYSTEMCTL_MUTATION.search(line):
                violations.append((rel, lineno, line.strip()))
    if violations:
        details = "\n".join(f"  {f}:{lineno}: {ln}" for f, lineno, ln in violations)
        pytest.fail(
            "\nDirect systemctl mutation in app/** (service-control boundary):\n" + details +
            "\n\nRoute the start/stop/restart/reboot through the scoped service-admin CLI "
            "(services/service_control.py). Do NOT add to _SYSTEMCTL_MUTATION_ALLOWLIST — it stays empty (P1)."
        )


# ── 15. No NEW hardcoded public URL endpoint in app/** ──
# Connection endpoints belong in settings/env, not source literals. Flags http(s):// and ws(s):// URLs
# whose host is PUBLIC (not localhost / 127.* / 0.0.0.0 / ::1 / private-LAN). URL-scoped on purpose (a
# bare "5.16.0.1" firmware string or a private-LAN os.getenv default is not an endpoint). ALLOWLIST =
# the existing legitimate config: core/app.py (CSP allowed-origins) + services/public_ip.py (external
# STUN/lookup services are inherent). Bans NEW hardcodes; it only shrinks.
_PUBLIC_URL = re.compile(
    r"""["'](?:https?|wss?)://"""
    r"""(?!localhost|127\.|0\.0\.0\.0|::1|192\.168\.|10\.|172\.(?:1[6-9]|2\d|3[01])\.)"""
    r"""[A-Za-z0-9*.\-]+\.""")
_PUBLIC_URL_ALLOWLIST = {
    "app/core/app.py",            # CSP allowed-origins (security config that names external origins)
    "app/services/public_ip.py",  # external STUN / public-IP lookup services (inherent to the feature)
}


def test_no_new_hardcoded_public_url_in_app():
    """No NEW hardcoded public http(s)/ws(s) endpoint in app/** — infra addresses belong in
    settings/env. URL-scoped (version strings / private-LAN os.getenv defaults are fine). Allowlist =
    the existing legitimate config (CSP origins + external public-IP services); it only shrinks."""
    violations: list[tuple[str, int, str]] = []
    for f in _production_py_files():
        rel = str(f.relative_to(_APP_ROOT.parent))
        if rel in _PUBLIC_URL_ALLOWLIST:
            continue
        for lineno, line in enumerate(f.read_text().splitlines(), start=1):
            if line.strip().startswith("#"):
                continue
            if _PUBLIC_URL.search(line):
                violations.append((rel, lineno, line.strip()))
    if violations:
        details = "\n".join(f"  {f}:{lineno}: {ln}" for f, lineno, ln in violations)
        pytest.fail(
            "\nHardcoded public URL in app/** (move the endpoint to settings/env):\n" + details +
            "\n\nRead it from app/core/settings or an env var. Do NOT add to _PUBLIC_URL_ALLOWLIST (it only shrinks)."
        )


# ── 16. SSHTransport constructed ONLY via the services/node_transport.build_transport adapter ──
# build_transport (services/node_transport.py) is the single place that constructs an SSHTransport,
# because it enforces the host-key-confirmation policy (refuse-unless-pinned + the audited TOFU pin).
# Constructing an SSHTransport anywhere else in app/** bypasses that policy — a node could be reached
# over an UNCONFIRMED host key (MITM exposure). Locks the Phase-3 (A-02) extraction. ALLOWLIST = the
# adapter only; it only shrinks. (The class decl `class SSHTransport:` has no paren, so this matches
# constructor calls only — not imports / annotations / the definition.)
_SSH_TRANSPORT_CTOR = re.compile(r"""\bSSHTransport\s*\(""")
_SSH_TRANSPORT_CTOR_ALLOWLIST = {
    "app/services/node_transport.py",  # build_transport — the host-key-policy-enforcing factory
}


def test_ssh_transport_built_only_via_node_transport_adapter():
    """An SSHTransport may only be constructed in services/node_transport.build_transport (the factory
    that enforces host-key confirmation + the audited TOFU pin). Building it anywhere else in app/**
    bypasses that policy — a node could be reached over an unconfirmed host key. Go through
    build_transport instead. The allowlist is the adapter only; it only shrinks."""
    violations: list[tuple[str, int, str]] = []
    for f in _production_py_files():
        rel = str(f.relative_to(_APP_ROOT.parent))
        if rel in _SSH_TRANSPORT_CTOR_ALLOWLIST:
            continue
        for lineno, line in enumerate(f.read_text().splitlines(), start=1):
            if line.strip().startswith("#"):
                continue
            if _SSH_TRANSPORT_CTOR.search(line):
                violations.append((rel, lineno, line.strip()))
    if violations:
        details = "\n".join(f"  {f}:{lineno}: {ln}" for f, lineno, ln in violations)
        pytest.fail(
            "\nSSHTransport constructed outside services/node_transport.build_transport "
            "(host-key-policy bypass):\n" + details +
            "\n\nGo through services/node_transport.build_transport (it enforces host-key confirmation "
            "+ the audited TOFU pin). Do NOT add to _SSH_TRANSPORT_CTOR_ALLOWLIST (it only shrinks)."
        )


# ── 17. No @app.on_event — startup/shutdown go through the lifespan context manager ──
# FastAPI/Starlette deprecated @app.on_event("startup"/"shutdown"); core/events.py now uses a lifespan
# asynccontextmanager (with a task registry that cancels the long-lived background loops on shutdown).
# An `.on_event(` anywhere in app/** reintroduces the deprecated API + an untracked-task lifecycle path.
# Unconditional — there is no legitimate use.
_ON_EVENT = re.compile(r"""\.on_event\s*\(""")


def test_no_on_event_use_lifespan_instead():
    """No @app.on_event in app/** — FastAPI deprecated it; wire startup/shutdown through the lifespan
    context manager (core/events._lifespan, set via register_event_handlers). Keeps the background-task
    registry (cancel-on-shutdown) as the single lifecycle path. Unconditional: no allowlist."""
    violations: list[tuple[str, int, str]] = []
    for f in _production_py_files():
        rel = str(f.relative_to(_APP_ROOT.parent))
        for lineno, line in enumerate(f.read_text().splitlines(), start=1):
            if line.strip().startswith("#"):
                continue
            if _ON_EVENT.search(line):
                violations.append((rel, lineno, line.strip()))
    if violations:
        details = "\n".join(f"  {f}:{lineno}: {ln}" for f, lineno, ln in violations)
        pytest.fail(
            "\n@app.on_event is deprecated — wire startup/shutdown through the lifespan ctx manager:\n"
            + details +
            "\n\nAdd it to core/events._lifespan (set via register_event_handlers -> "
            "app.router.lifespan_context), keeping the cancel-on-shutdown task registry. No allowlist."
        )


# ── 18. Secret/config/state stores must not FAIL OPEN on content corruption (Cycle 1) ──
# A store read that catches a CONTENT error (JSONDecodeError / UnicodeDecodeError / ValueError / broad
# Exception / bare except) and returns {} / [] / None silently regenerates secrets, loses state, or hides
# a corrupt revision as "not found" — the audit's High finding. Fail CLOSED: store_safety.quarantine_corrupt
# + raise StoreCorrupt. A PURE `except OSError`/`FileNotFoundError` access-error degrade is ALLOWED (the file
# may be fine, just unreadable; the write path fails too). Scoped to the secret/revision/topology stores that
# MUST fail closed. NOT mountpoint_allocator: it is a deliberate fail-SAFE operational store — a corrupt
# allocations map coerces to empty ON PURPOSE so live encoder streams are NOT torn down (its Cycle 1 fix was
# durability/fsync, not fail-closed reads).
_STORE_FILES = {
    "app/services/secret_store.py",
    "app/services/stream_binding_store/secrets.py",
    "app/services/stream_binding_store/state_file.py",
    "app/services/operation_journal.py",
    "app/services/runtime_revision_store.py",
    "app/services/runtime_config_apply.py",
}
_FAILOPEN_EXC = {"JSONDecodeError", "UnicodeDecodeError", "ValueError", "Exception"}


def _except_names(handler: ast.ExceptHandler) -> list[str]:
    if handler.type is None:
        return ["<bare>"]
    parts = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    names = []
    for p in parts:
        if isinstance(p, ast.Name):
            names.append(p.id)
        elif isinstance(p, ast.Attribute):
            names.append(p.attr)
    return names


def _except_returns_empty(handler: ast.ExceptHandler) -> bool:
    for stmt in ast.walk(handler):
        if isinstance(stmt, ast.Return):
            v = stmt.value
            if v is None or (isinstance(v, ast.Constant) and v.value is None):
                return True                                   # `return` / `return None`
            if isinstance(v, ast.Dict) and not v.keys:
                return True                                   # `return {}`
            if isinstance(v, ast.List) and not v.elts:
                return True                                   # `return []`
    return False


def test_stores_do_not_fail_open_on_corruption():
    """A secret/config/state store read must not swallow CONTENT corruption and return an empty store
    (Cycle 1). Catching JSONDecodeError/UnicodeDecodeError/ValueError/Exception/bare-except and returning
    {}/[]/None is fail-open → silent secret regen / lost state / corrupt-revision-hidden-as-not-found.
    Fail closed: quarantine + raise StoreCorrupt. A pure `except OSError` access degrade is allowed."""
    violations: list[tuple[str, int, str]] = []
    for f in _production_py_files():
        rel = str(f.relative_to(_APP_ROOT.parent))
        if rel not in _STORE_FILES:
            continue
        for h in ast.walk(ast.parse(f.read_text())):
            if isinstance(h, ast.ExceptHandler):
                names = _except_names(h)
                if any(n in _FAILOPEN_EXC or n == "<bare>" for n in names) and _except_returns_empty(h):
                    violations.append((rel, h.lineno, "+".join(names)))
    if violations:
        details = "\n".join(f"  {f}:{ln}  (except {names} -> return empty)" for f, ln, names in violations)
        pytest.fail(
            "\nFail-open store read (content corruption -> empty store) in the secret/config store set:\n"
            + details +
            "\n\nFail CLOSED: store_safety.quarantine_corrupt + raise StoreCorrupt. Only a pure "
            "`except OSError`/`FileNotFoundError` may degrade to empty (access error != content corruption)."
        )


# ── 19. No DESTRUCTIVE bare systemctl_action in app/** — mutations via the scoped service-admin port ──
# services/systemd.systemctl_action is the BARE (no-sudo) systemctl primitive, kept for READS
# (is-active / show / status). A `systemctl_action("restart"|"start"|"stop"|"reload", ...)` is a destructive
# mutation that BYPASSES the service-control boundary (Cycle 2 — closed the config_apply bypass). Route
# every destructive service mutation through services/service_control.py (the scoped service-admin CLI) or
# a scoped admin CLI (encoder-admin / janus-admin / camera-admin). Unconditional — no allowlist.
_SYSTEMCTL_ACTION_MUTATION = re.compile(
    r"""systemctl_action\(\s*["'](restart|start|stop|reload)["']""")


def test_no_destructive_systemctl_action_in_app():
    """A bare `systemctl_action(restart|start|stop|reload)` bypasses the service-control boundary —
    destructive service mutations go through services/service_control.py (scoped service-admin) or a
    scoped admin CLI. systemctl_action stays for READS (is-active/show/status). Unconditional."""
    violations: list[tuple[str, int, str]] = []
    for f in _production_py_files():
        rel = str(f.relative_to(_APP_ROOT.parent))
        for lineno, line in enumerate(f.read_text().splitlines(), start=1):
            if line.strip().startswith("#"):
                continue
            if _SYSTEMCTL_ACTION_MUTATION.search(line):
                violations.append((rel, lineno, line.strip()))
    if violations:
        details = "\n".join(f"  {f}:{lineno}: {ln}" for f, lineno, ln in violations)
        pytest.fail(
            "\nDestructive bare systemctl_action (bypasses the service-control boundary):\n" + details +
            "\n\nRoute restart/start/stop/reload through services/service_control.py (the scoped "
            "service-admin CLI) or a scoped admin CLI. systemctl_action is for reads (is-active/show)."
        )


# ── 20. Runtime-config capability surface must agree with the live routes (Cycle 3) ──
# The apply contract drifted: the AE-1 engine + POST /apply route + happy-path tests + runbook said
# apply is LIVE for NEW_SESSIONS_ONLY, while the capability surface (capability_report + docstrings)
# still said "no apply endpoint / apply NOT supported" and hardcoded apply_supported=False. Cycle 3
# picked ONE truth (apply IS live for NEW_SESSIONS_ONLY) and synced every surface. This guard locks it:
# WHILE POST /apply is a registered route, (a) no production runtime-config source may carry the stale
# "no apply endpoint / apply NOT supported" prose, and (b) capability_report() must not hardcode
# apply_supported=False — it must track the live applyability.
_RUNTIME_CONFIG_SRC = (
    _APP_ROOT / "routes" / "runtime_config.py",
    _APP_ROOT / "services" / "runtime_revision_store.py",
)
_STALE_APPLY_PROSE = re.compile(
    r"""no\s+/?apply\s+endpoint|apply\s+NOT\s+supported|awaiting\s+the\s+B2\s+apply\s+engine""",
    re.IGNORECASE,
)


def _apply_route_is_registered() -> bool:
    """True iff a POST route ending in /apply is registered on the runtime-config router."""
    from app.routes import runtime_config as rc
    for route in getattr(rc.router, "routes", []):
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if path.endswith("/apply") and "POST" in methods:
            return True
    return False


def _capability_report_hardcodes_false() -> bool:
    """AST: True iff capability_report() assigns/returns apply_supported as a constant False."""
    src = (_APP_ROOT / "services" / "runtime_revision_store.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "capability_report":
            for inner in ast.walk(node):
                # dict literal: {... "apply_supported": False ...}
                if isinstance(inner, ast.Dict):
                    for k, v in zip(inner.keys, inner.values):
                        if (isinstance(k, ast.Constant) and k.value == "apply_supported"
                                and isinstance(v, ast.Constant) and v.value is False):
                            return True
                # bare assignment: apply_supported = False
                if isinstance(inner, ast.Assign):
                    for tgt in inner.targets:
                        if (isinstance(tgt, ast.Name) and tgt.id == "apply_supported"
                                and isinstance(inner.value, ast.Constant)
                                and inner.value.value is False):
                            return True
    return False


def test_runtime_config_capability_surface_agrees_with_routes():
    """While POST /apply is registered, the runtime-config capability surface must not claim apply is
    absent/unsupported (stale prose) nor hardcode capability_report.apply_supported=False. One truth:
    apply is LIVE for NEW_SESSIONS_ONLY (AE-1); the report tracks live applyability (Cycle 3)."""
    # Positive anchor — the whole guard is premised on /apply being live. If apply is ever removed,
    # this fails loudly so the capability surface (and this guard) get revisited deliberately.
    assert _apply_route_is_registered(), (
        "POST /apply is no longer a registered runtime-config route — revisit the capability surface "
        "and this consistency guard before removing the apply contract."
    )

    violations: list[tuple[str, int, str]] = []
    for f in _RUNTIME_CONFIG_SRC:
        rel = str(f.relative_to(_APP_ROOT.parent))
        for lineno, line in enumerate(f.read_text().splitlines(), start=1):
            if _STALE_APPLY_PROSE.search(line):
                violations.append((rel, lineno, line.strip()))

    if _capability_report_hardcodes_false():
        violations.append(
            ("app/services/runtime_revision_store.py", 0,
             "capability_report() hardcodes apply_supported=False — must track live applyability"))

    if violations:
        details = "\n".join(f"  {f}:{lineno}: {ln}" for f, lineno, ln in violations)
        pytest.fail(
            "\nRuntime-config capability surface contradicts the live POST /apply route:\n" + details +
            "\n\nApply is LIVE for the NEW_SESSIONS_ONLY class (AE-1). Drop stale 'no apply endpoint / "
            "apply NOT supported' prose and let capability_report.apply_supported track live applyability."
        )


# ── 21. asyncio.create_task ONLY inside the task registry — own every long-lived task (Cycle 4) ──
# A bare `asyncio.create_task(...)` whose return value is dropped can be garbage-collected mid-flight
# (CPython's loop keeps only a weak ref → "Task was destroyed but it is pending"), and it bypasses the
# shutdown path so the work leaks across a restart (the events.py:173 boot-reconcile gap). Route every
# long-lived async task through services/task_registry.spawn (strong ref held + cancelled on shutdown).
# `tg.create_task(...)` on an asyncio.TaskGroup is a DIFFERENT construct — it owns + awaits its own
# children (request-scoped structured concurrency) — and is never flagged (the value isn't `asyncio`).
_CREATE_TASK_ALLOWED = {"app/services/task_registry.py"}


def _calls_asyncio_create_task(tree: ast.AST) -> bool:
    """AST: True iff the module calls `asyncio.create_task(...)` or a directly-imported
    `create_task(...)` (from asyncio). A `<taskgroup>.create_task(...)` (value != asyncio) is ignored."""
    # Names bound to asyncio.create_task via `from asyncio import create_task [as alias]`.
    direct_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "asyncio":
            for alias in node.names:
                if alias.name == "create_task":
                    direct_aliases.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # asyncio.create_task(...)
        if (isinstance(func, ast.Attribute) and func.attr == "create_task"
                and isinstance(func.value, ast.Name) and func.value.id == "asyncio"):
            return True
        # create_task(...) where create_task was imported directly from asyncio
        if isinstance(func, ast.Name) and func.id in direct_aliases:
            return True
    return False


def test_asyncio_create_task_only_in_task_registry():
    """Long-lived async tasks must be created via services/task_registry.spawn (held + cancelled on
    shutdown), not a bare asyncio.create_task (dropped-ref GC race + leaks past shutdown). The registry
    is the ONE sanctioned create_task site; TaskGroup.create_task (structured concurrency) is exempt."""
    violations: list[str] = []
    for f in _production_py_files():
        rel = str(f.relative_to(_APP_ROOT.parent))
        if rel in _CREATE_TASK_ALLOWED:
            continue
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:
            continue
        if _calls_asyncio_create_task(tree):
            violations.append(rel)
    if violations:
        pytest.fail(
            "\nBare asyncio.create_task outside the task registry:\n  " + "\n  ".join(violations) +
            "\n\nRoute long-lived async tasks through services/task_registry.spawn (strong ref + cancel "
            "on shutdown). Use asyncio.TaskGroup for request-scoped structured concurrency."
        )


# ── 22. stream_bindings stays a cohesive PACKAGE — full route surface + no submodule refat (Cycle 5) ──
# The 765-line fat route was split into app/routes/stream_bindings/{contracts,nodes,bindings,operations,
# fleet} + the __init__ shared core. This guard locks the split two ways: (a) the package still
# contributes the full admin route surface (a floor — the exact (method,path) set is the inventory lock
# in test_stream_bindings_route_inventory), and (b) no submodule grows back toward the old monolith.
_SB_PKG = _APP_ROOT / "routes" / "stream_bindings"
_SB_ROUTE_FLOOR = 27
_SB_LINE_CEILING = 340   # old monolith was 765; current max is nodes.py ~252 — generous headroom


def test_stream_bindings_is_a_cohesive_package():
    """stream_bindings is a package (not the old single module), still serves its full route surface,
    and no submodule exceeds the line ceiling (the 'fat doesn't silently regrow' lock)."""
    assert _SB_PKG.is_dir(), "app/routes/stream_bindings must be a package (Cycle 5 split)"
    assert not (_APP_ROOT / "routes" / "stream_bindings.py").exists(), \
        "the old single-module stream_bindings.py must not coexist with the package"

    from app.routes import stream_bindings as sb
    surface = {(m, r.path) for r in sb.router.routes
               for m in (getattr(r, "methods", set()) or set()) if m != "HEAD"}
    assert len(surface) >= _SB_ROUTE_FLOOR, (
        f"stream_bindings route surface shrank to {len(surface)} (< {_SB_ROUTE_FLOOR} floor) — "
        f"a handler was dropped in the split.")

    fat = {f.name: n for f in _SB_PKG.glob("*.py")
           if (n := len(f.read_text().splitlines())) > _SB_LINE_CEILING}
    assert not fat, (
        f"stream_bindings submodule(s) exceed the {_SB_LINE_CEILING}-line ceiling: {fat}. "
        f"Keep handlers split by cohesion (nodes/bindings/operations/fleet); don't refat one file.")


# ── 23. The NAT/TURN update operation must expose a structured, observable result (Cycle 7B) ──
# The audit's top residual risk was that POST /janus/nat applied multi-stage runtime mutations with no
# failure-stage reporting (gap G4: a partial failure was an opaque 500). Cycle 7B made it an explicit
# operation returning NatUpdateResult. This guard locks the observability contract so a future refactor
# can't silently drop a stage flag and regress to the opaque error model.
_NAT_RESULT_REQUIRED_FIELDS = {
    "ok", "failure_stage", "desired_persisted", "local_applied", "local_restarted", "depth_restarted",
}


def test_nat_update_result_exposes_observable_stage_fields():
    """NatUpdateResult must carry the per-stage observability fields (failure_stage + applied-flags) so
    an operator can tell exactly how far a NAT update got. Dropping one reopens audit gap G4."""
    from dataclasses import fields as dc_fields

    from app.application.janus_nat import NatUpdateResult

    names = {f.name for f in dc_fields(NatUpdateResult)}
    missing = _NAT_RESULT_REQUIRED_FIELDS - names
    assert not missing, (
        f"NatUpdateResult dropped observability field(s): {sorted(missing)} — the operation result must "
        f"keep failure_stage + the applied-flags (audit gap G4 / docs/design/JANUS_NAT_OPERATION_BOUNDARY.md)."
    )


# ── 24. Runtime-apply statuses must map to the canonical operation vocabulary (Cycle 8B) ──
# The 4 operation mechanisms share ONE read-model vocabulary (app/application/operations.OperationStatus).
# runtime_revision_store is the richest status producer (named STATUS_* constants). Each MUST have a
# canonical mapping so the shared read model can't silently fail-close a real runtime-apply state to
# FAILED. Adding a new STATUS_* without mapping it in app/application/operations fails here. (node-op +
# NAT sidecar statuses are string literals, covered by tests/test_operations_contract.py.)
def test_runtime_revision_statuses_have_canonical_mapping():
    """Every runtime_revision_store STATUS_* must be a key in the canonical operation map."""
    import app.services.runtime_revision_store as rrs
    from app.application.operations import KNOWN_DOMAIN_STATUSES

    statuses = {v for k, v in vars(rrs).items()
                if k.startswith("STATUS_") and isinstance(v, str)}
    missing = statuses - set(KNOWN_DOMAIN_STATUSES)
    assert not missing, (
        f"runtime_revision_store status(es) not mapped in app/application/operations: {sorted(missing)} "
        f"— add them to _DOMAIN_TO_CANONICAL so the shared read model doesn't fail-close them to FAILED."
    )


# ── 25. An env var owned by settings.py must not ALSO be read raw in services (G5 split-ownership) ──
# Two sources of truth for one env var drifts (a test/env override of one but not the other, or
# divergent defaults). settings.py is the owner; services read via get_settings(). Exception: the
# stream_binding_store leaf reads JANUS_MOUNT_ID raw ON PURPOSE so it imports without the settings stack
# (see _janus_mount_id "mirrors settings.janus_mount_id, decoupled") — documented + allowlisted.
_SETTINGS_ENV_DUP_ALLOW = {"JANUS_MOUNT_ID"}
_ENV_READ_RE = re.compile(
    r'''(?:os\.)?(?:getenv|environ\.get)\(\s*["']([A-Z_][A-Z0-9_]*)["']'''
    r'''|os\.environ\[\s*["']([A-Z_][A-Z0-9_]*)["']''')


def _env_vars_read(path: Path) -> set:
    out: set = set()
    for m in _ENV_READ_RE.finditer(path.read_text()):
        out.add(m.group(1) or m.group(2))
    return out


def test_settings_owned_env_not_reread_raw_in_services():
    """No env var is read in BOTH core/settings.py (the owner) AND raw via os.getenv/environ in
    services/ — one source of truth per setting (G5). The leaf-store JANUS_MOUNT_ID decoupling is the
    one documented exception."""
    settings_env = _env_vars_read(_APP_ROOT / "core" / "settings.py")
    service_env: set = set()
    for f in (_APP_ROOT / "services").rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        service_env |= _env_vars_read(f)
    dup = (settings_env & service_env) - _SETTINGS_ENV_DUP_ALLOW
    assert not dup, (
        f"\nenv var(s) owned by settings.py AND re-read raw in services/: {sorted(dup)}\n"
        f"Read them from get_settings() (one source of truth), or — if a deliberate leaf-store "
        f"decoupling — add to _SETTINGS_ENV_DUP_ALLOW with a documented reason."
    )


# ── 26. The allocator read API must stay fail-SAFE — never raise on a corrupt store (Cycle 14A) ──
# The allocator is the DELIBERATE counter-example to guard #18 (it fail-SAFEs to empty so live
# encoder streams are NOT torn down). Cycle 14A added an OBSERVABILITY surface
# (`allocator_corruption_status` -> /readyz NON-FATAL field) that depends on this invariant: a
# corrupt allocator must remain readable (-> empty), never make a reader raise and never fail
# readiness. This guard CALLS the readers on a truncated-JSON file to lock that contract — if
# anyone makes the allocator fail-CLOSED (raise / 503 on corrupt), this fails. Observability is
# the right answer here, not fail-closed reads.
def test_allocator_read_api_is_failsafe_on_corruption(tmp_path):
    from app.services import mountpoint_allocator as alloc
    p = tmp_path / "sensor_allocations.json"
    p.write_text('{"allocations": {"x"')   # invalid JSON — the worst-case corrupt store
    # Readers degrade to empty/None WITHOUT raising (the fail-safe the audit/Cycle 1 chose).
    assert alloc.list_allocations(p) == {}
    assert alloc.list_desired_active(p) == {}
    assert alloc.get_allocation("141722072135", "color", p) is None
    # And the probe must REPORT it as corrupt (not silently "ok"/empty) — that is the observability.
    assert alloc.allocator_corruption_status(p)["allocator_state"] == "corrupt"


# ── 27. Allocator WRITE paths must not propagate JSONDecodeError on a corrupt store (Cycle 15A) ──
# Companion to #26: reads fail-SAFE, and so must writes. A truncated allocator file used to crash
# the next mutation (`_flock_state` did an unwrapped `json.loads`) — which crashed the boot
# reconciler's seed write. The contract is now: content corruption is quarantined + reset, and the
# mutation PROCEEDS — no JSONDecodeError escapes a write path. This guard drives every mutator on a
# truncated-JSON file and asserts none leaks a decode error and the mutation lands. (IO errors are a
# SEPARATE axis — they correctly raise AllocationError; that is not what this guard checks.)
def test_allocator_write_paths_do_not_propagate_jsondecodeerror(tmp_path):
    import json as _json
    from app.services import mountpoint_allocator as alloc

    def _corrupt(name):
        q = tmp_path / name
        q.write_text('{"allocations": {"x"')   # truncated — the worst-case corrupt store
        return q

    # allocate: must land a real allocation, never raise JSONDecodeError.
    try:
        a = alloc.allocate("141722072135", "color", state_path=_corrupt("a.json"))
    except _json.JSONDecodeError as e:                       # pragma: no cover - guard failure path
        pytest.fail(f"allocate propagated JSONDecodeError on corrupt store: {e}")
    assert a.mp_id and a.rtp_port

    # ensure / release / migrate_color_key: must not leak a decode error either.
    for label, fn in (
        ("ensure", lambda p: alloc.ensure("local", "color", alloc.COLOR_MP_ID,
                                          alloc.COLOR_RTP_PORT, state_path=p)),
        ("release", lambda p: alloc.release("s", "color", state_path=p)),
        ("migrate_color_key", lambda p: alloc.migrate_color_key("141722072135", state_path=p)),
    ):
        try:
            fn(_corrupt(f"{label}.json"))
        except _json.JSONDecodeError as e:                  # pragma: no cover - guard failure path
            pytest.fail(f"{label} propagated JSONDecodeError on corrupt store: {e}")


# ── 28. The two axes — desired_up (Start/Stop) vs fdir.enabled (recovery) — stay correctly wired ──
# Unified node lifecycle, with FDIR owning recovery (docs/design/FDIR_RECOVERY_SEMANTICS.md):
#   * desired_up = is the stream wanted up? Drives MOUNTPOINT maintenance (the Janus listener).
#   * fdir.enabled = autonomous keep-alive: detect + RECOVER + escalate.
# Three regressions would re-conflate / mis-wire them and bring back the ".55 up but shown stopped /
# mountpoint drops on restart" bug or the "FDIR off yet still auto-recovers" surprise:
#   (a) the Stop use-case disabling FDIR again (set_fdir_enabled in stop_binding),
#   (b) the gateway gating MOUNTPOINT maintenance on fdir.enabled instead of desired_up, and
#   (c) the monitor's CONVERGE (recovery) action NOT gating on fdir.enabled — so a stream with FDIR
#       off would auto-recover anyway, contradicting the FDIR name. The converge line must require
#       BOTH desired_up AND fdir.enabled.
# This guard reads the sources and fails on any. Behavioural tests cover the runtime; this locks the
# invariant against a future refactor quietly re-wiring them.
def test_start_stop_decoupled_from_fdir():
    stop_src = (_APP_ROOT / "application" / "stream_bindings" / "stop_binding.py").read_text()
    assert "set_fdir_enabled" not in stop_src, (
        "stop_binding must NOT disable FDIR — Stop sets desired_up=False; FDIR is separate")

    recon_src = (_APP_ROOT / "services" / "binding_provision.py").read_text()
    assert "b.desired_up" in recon_src, (
        "reconcile_janus must gate Janus-mountpoint maintenance on desired_up (not fdir.enabled)")

    mon_src = (_APP_ROOT / "services" / "remote_stream_monitor.py").read_text()
    # The converge gate must require fdir.enabled — FDIR owns recovery. Match the assignment line so
    # a future edit that drops `b.fdir.enabled` from it trips this guard (not just any mention).
    converge_line = next((ln for ln in mon_src.splitlines()
                          if ln.lstrip().startswith("converge = ")), "")
    assert "b.desired_up" in converge_line and "b.fdir.enabled" in converge_line, (
        "remote_stream_monitor converge (recovery) must gate on desired_up AND fdir.enabled — "
        f"FDIR owns recovery; got: {converge_line.strip()!r}")
