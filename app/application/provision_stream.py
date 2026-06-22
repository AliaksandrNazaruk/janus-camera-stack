"""Use-case for the end-to-end /streams/provision route: create mountpoint + write env
+ start encoder.

The mountpoint-create step is INJECTED (a callable) so this use-case never imports the
routes layer — fixing the old route→route call (provision_stream calling the
create_mountpoint route handler). Extracted from admin_dashboard (C-04); behavior +
audit string preserved.
"""
from __future__ import annotations

from typing import Any, Callable, Dict

from app.application import encoder_admin as enc_uc
from app.services import encoder_env
from app.services.audit_log import audit


def provision_stream(*, mountpoint_req: Any, encoder_family: str, encoder_instance: str,
                     encoder_env_spec: Any, rtp_port: int, mp_id: Any,
                     create_mountpoint: Callable[[Any], Any]) -> Dict[str, Any]:
    """Returns a plain dict {mountpoint, env_files, encoder, error} that the route maps
    to ProvisionStreamResponse. `create_mountpoint(req)` -> CreateMountpointResponse."""
    mp_resp = create_mountpoint(mountpoint_req)
    if not mp_resp.created:
        return {"mountpoint": mp_resp, "env_files": [], "encoder": None,
                "error": "mountpoint create failed — encoder skipped"}
    try:
        env_files = encoder_env.write_env_files(encoder_family, encoder_instance,
                                                encoder_env_spec, rtp_port)
    except OSError as exc:
        return {"mountpoint": mp_resp, "env_files": [], "encoder": None,
                "error": f"env file write failed: {exc}"}

    enc_resp = enc_uc.encoder_action("start", encoder_family, encoder_instance)
    audit("admin_dashboard.provision_stream",
          {"mp_id": mp_id, "encoder": f"{encoder_family}@{encoder_instance}", "ok": enc_resp.ok})
    return {"mountpoint": mp_resp, "env_files": env_files, "encoder": enc_resp,
            "error": None if enc_resp.ok else "encoder start failed (mountpoint+env still in place)"}
