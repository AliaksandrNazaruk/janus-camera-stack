"""Use-case: read the admin config snapshot (secrets masked + janus/relay active state).
Extracted from admin_config (route-purity Phase 5); behavior verbatim. The SecretSnapshot /
ConfigSnapshot models + the age humanizer live here. Active-state probes go through the BARE
systemctl adapter (services/systemd.is_active).
"""
from __future__ import annotations

import time
from typing import Optional

from pydantic import BaseModel

from app.services import jcfg_renderer, secret_store, systemd


class SecretSnapshot(BaseModel):
    key: str
    masked: str
    is_set: bool
    is_sensitive: bool
    last_rotated_ts: Optional[int] = None
    last_rotated_human: Optional[str] = None


class ConfigSnapshot(BaseModel):
    secrets: list[SecretSnapshot]
    janus_cfg_dir: Optional[str] = None
    nat_1_1_mapping: Optional[str] = None
    ice_enforce_list: Optional[str] = None
    janus_active: bool = False
    relay_active: bool = False
    template_dir: Optional[str] = None


def _humanize_age(ts: Optional[int]) -> Optional[str]:
    if ts is None:
        return None
    delta = max(0, int(time.time()) - int(ts))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def snapshot() -> ConfigSnapshot:
    snap = secret_store.snapshot()
    secrets_list = [
        SecretSnapshot(
            key=v.key,
            masked=v.masked,
            is_set=v.is_set,
            is_sensitive=v.is_sensitive,
            last_rotated_ts=v.last_rotated_ts,
            last_rotated_human=_humanize_age(v.last_rotated_ts),
        )
        for v in sorted(snap.values(), key=lambda x: x.key)
    ]

    paths = jcfg_renderer.detect_janus_paths()
    tpl_dir = jcfg_renderer.detect_template_dir()

    nat = None
    if paths is not None:
        try:
            nat = jcfg_renderer._read_current_nat_mapping(paths.cfg_dir / "janus.jcfg")
        except Exception:
            nat = None

    return ConfigSnapshot(
        secrets=secrets_list,
        janus_cfg_dir=str(paths.cfg_dir) if paths else None,
        nat_1_1_mapping=nat,
        ice_enforce_list=jcfg_renderer.detect_primary_iface(),
        janus_active=systemd.is_active("janus") or systemd.is_active("janus.service"),
        relay_active=systemd.is_active("janus-textroom-relay") or systemd.is_active("janus_camera_page_hook"),
        template_dir=str(tpl_dir) if tpl_dir else None,
    )
