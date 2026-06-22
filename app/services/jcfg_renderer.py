"""Render Janus jcfg templates → JANUS_CFG_DIR.

Centralizes the sed-substitution logic that install.sh does. Used by:
- install.sh (via CLI: `python -m app.services.jcfg_renderer render`)
- admin_config routes (after rotation/edit)

Templates live in:
  <repo>/deploy/janus/etc/*.template
or:
  /opt/janus-camera-page/deploy/janus/etc/*.template

Substitutions read from /etc/robot/camera-secrets.env via secret_store.
Non-secret substitutions (JANUS_CFG_DIR, ICE_ENFORCE_LIST) computed
at render time.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from app.services import secret_store

log = logging.getLogger("jcfg_renderer")


@dataclass(frozen=True)
class JanusPaths:
    cfg_dir: Path
    plugins_dir: Path
    transports_dir: Path


def detect_janus_paths() -> Optional[JanusPaths]:
    """Mirror install.sh detect_janus_paths() — apt vs source build."""
    if Path("/opt/janus/etc/janus").is_dir():
        return JanusPaths(
            cfg_dir=Path("/opt/janus/etc/janus"),
            plugins_dir=Path("/opt/janus/lib/janus/plugins"),
            transports_dir=Path("/opt/janus/lib/janus/transports"),
        )
    if not Path("/etc/janus").is_dir():
        return None
    # apt-installed — multiarch detection
    multiarch = ""
    try:
        r = subprocess.run(
            ["dpkg-architecture", "-qDEB_HOST_MULTIARCH"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0:
            multiarch = r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    candidates = [
        Path(f"/usr/lib/{multiarch}/janus") if multiarch else None,
        Path("/usr/lib/janus"),
    ]
    for c in candidates:
        if c and (c / "plugins").is_dir():
            return JanusPaths(
                cfg_dir=Path("/etc/janus"),
                plugins_dir=c / "plugins",
                transports_dir=c / "transports",
            )
    # Last resort — probe via find
    try:
        r = subprocess.run(
            ["find", "/usr/lib", "-name", "libjanus_streaming*.so"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            so_path = Path(r.stdout.strip().splitlines()[0])
            return JanusPaths(
                cfg_dir=Path("/etc/janus"),
                plugins_dir=so_path.parent,
                transports_dir=so_path.parent.parent / "transports",
            )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def detect_template_dir() -> Optional[Path]:
    """Find templates — installed location or dev location."""
    candidates = [
        Path("/opt/janus-camera-page/deploy/janus/etc"),
        Path(__file__).resolve().parent.parent.parent / "deploy" / "janus" / "etc",
    ]
    for c in candidates:
        if c.is_dir() and any(c.glob("*.template")):
            return c
    return None


def detect_primary_iface() -> str:
    """Detect interface used for default route — for ice_enforce_list."""
    try:
        r = subprocess.run(
            ["ip", "-o", "-4", "route", "show", "to", "default"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0:
            parts = r.stdout.split()
            if "dev" in parts:
                return parts[parts.index("dev") + 1]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "eth0"


def _read_current_nat_mapping(janus_jcfg: Path) -> str:
    """Preserve operator-set nat_1_1_mapping across re-renders."""
    if not janus_jcfg.exists():
        return "REPLACE_WITH_PUBLIC_IP"
    text = janus_jcfg.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'nat_1_1_mapping\s*=\s*"([^"]+)"', text)
    if m:
        return m.group(1)
    return "REPLACE_WITH_PUBLIC_IP"


def _build_substitutions(
    paths: JanusPaths,
    nat_mapping: Optional[str] = None,
    iface: Optional[str] = None,
) -> Dict[str, str]:
    """Build placeholder → value map. Pulls secrets from secret_store."""
    values = secret_store._load()  # raw values, never mask here
    iface_resolved = iface or detect_primary_iface()
    janus_jcfg = paths.cfg_dir / "janus.jcfg"
    nat = nat_mapping if nat_mapping else _read_current_nat_mapping(janus_jcfg)
    subs = {
        "JANUS_CFG_DIR":         str(paths.cfg_dir),
        "JANUS_PLUGINS_DIR":     str(paths.plugins_dir),
        "JANUS_TRANSPORTS_DIR":  str(paths.transports_dir),
        "ICE_ENFORCE_LIST":      iface_resolved,
        "RELAY_PORT":            "9000",
        "JANUS_ADMIN_SECRET":    values.get("JANUS_ADMIN_SECRET", "REPLACE_ME"),
        "STREAMING_ADMIN_KEY":   values.get("STREAMING_ADMIN_KEY")
                                  or values.get("JANUS_STREAMING_ADMIN_KEY", "REPLACE_ME"),
        "STREAMING_RGB_MP_SECRET": values.get("STREAMING_RGB_MP_SECRET", "REPLACE_ME"),
        "TEXTROOM_ROOM_SECRET":  values.get("TEXTROOM_ROOM_SECRET", "REPLACE_ME"),
        "NAT_1_1_MAPPING":       nat,
    }
    return subs


def _apply(text: str, subs: Dict[str, str]) -> str:
    """Replace @PLACEHOLDER@ tokens."""
    out = text
    for k, v in subs.items():
        out = out.replace(f"@{k}@", v)
    return out


@dataclass
class RenderResult:
    rendered: List[Path]
    skipped_templates: List[str]
    paths: JanusPaths


def render(
    nat_mapping: Optional[str] = None,
    iface: Optional[str] = None,
    backup_existing: bool = True,
) -> RenderResult:
    """Render all *.template files in template dir to JANUS_CFG_DIR.

    Returns list of rendered files. Backs up existing as .pre-render-<ts>
    once per session — won't re-backup on subsequent renders.
    """
    paths = detect_janus_paths()
    if paths is None:
        raise RuntimeError("Janus install not found (no /opt/janus/etc/janus or /etc/janus)")

    tpl_dir = detect_template_dir()
    if tpl_dir is None:
        raise RuntimeError("Template dir not found (looked in /opt/janus-camera-page/deploy/janus/etc and dev dir)")

    subs = _build_substitutions(paths, nat_mapping=nat_mapping, iface=iface)

    rendered = []
    skipped = []
    for tpl in sorted(tpl_dir.glob("*.template")):
        # template file looks like "janus.jcfg.template" → "janus.jcfg"
        dst = paths.cfg_dir / tpl.name[: -len(".template")]
        try:
            text = tpl.read_text(encoding="utf-8")
            new_text = _apply(text, subs)
            if backup_existing and dst.exists():
                backup = dst.with_suffix(dst.suffix + ".pre-render")
                if not backup.exists():
                    backup.write_bytes(dst.read_bytes())
            tmp = dst.with_suffix(dst.suffix + ".tmp")
            tmp.write_text(new_text, encoding="utf-8")
            os.chmod(tmp, 0o640)
            os.rename(tmp, dst)
            rendered.append(dst)
            log.info("rendered %s → %s", tpl.name, dst)
        except Exception as exc:
            log.error("render %s failed: %s", tpl.name, exc)
            skipped.append(tpl.name)

    # Ensure streams.d/ exists for dynamic mountpoints
    streams_d = paths.cfg_dir / "streams.d"
    streams_d.mkdir(mode=0o755, exist_ok=True)

    return RenderResult(rendered=rendered, skipped_templates=skipped, paths=paths)


# ── CLI entrypoint for install.sh use ─────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if len(sys.argv) < 2 or sys.argv[1] != "render":
        print("Usage: python -m app.services.jcfg_renderer render [--nat-mapping=IP]", file=sys.stderr)
        sys.exit(2)
    nat = None
    for arg in sys.argv[2:]:
        if arg.startswith("--nat-mapping="):
            nat = arg.split("=", 1)[1]
    try:
        result = render(nat_mapping=nat)
        for p in result.rendered:
            print(p)
        sys.exit(0)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
