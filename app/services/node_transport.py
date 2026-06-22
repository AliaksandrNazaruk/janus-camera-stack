"""Node SSH transport factory (Phase 3 — A-02 residual-glue extraction).

Builds an :class:`SSHTransport` against a remote node with a PINNED host key, enforcing the
host-key-confirmation policy: refuse unless the key was confirmed out-of-band, or TOFU-pin it
on first contact via the dev/bench escape hatch. Extracted verbatim from
``routes/stream_bindings._transport_for`` so the host-key policy lives WITH the transport
construction instead of at the HTTP boundary.

Pure infra: on a missing/unconfirmed key it raises :class:`HostKeyNotConfirmed` (a plain domain
signal) rather than an HTTP error — the route maps that to 412. Every collaborator (host-key
capture, fingerprint, store, audit) is INJECTED so the route can forward its own monkeypatchable
references and keep its test oracles intact.
"""
from __future__ import annotations

from app.services.ssh_transport import SSHTransport


class HostKeyNotConfirmed(Exception):
    """The node's SSH host key was not confirmed out-of-band and TOFU was not allowed.

    Carries the exact operator-facing remediation message; the route maps it to 412."""


def build_transport(
    node,
    sudo_password,
    *,
    allow_tofu: bool = False,
    capture_host_key,
    fingerprint_fn,
    store,
    state_path,
    audit_fn,
    ssh_user: str,
    ssh_key: str,
) -> SSHTransport:
    """Build an SSH transport against ``node`` with a PINNED host key.

    If the key was not confirmed out-of-band (``node.host_key`` unset), refuse unless
    ``allow_tofu`` — the dev/bench escape hatch that pins on first contact (TOFU). Production
    onboarding must confirm the fingerprint first (P4-SEC, Gap 2). The TOFU pin is explicit
    + audited via the injected ``store``/``audit_fn``.
    """
    if not node.host_key:
        if not allow_tofu:
            raise HostKeyNotConfirmed(
                f"host key for {node.node_id} not confirmed — GET "
                f"/api/v1/admin/nodes/{node.node_id}/host-key, verify the SHA256 "
                f"out-of-band, then POST .../host-key/confirm (or pass allow_tofu=true for dev)")
        hk = capture_host_key(node.host)
        if hk:
            store.set_host_key(node.node_id, hk, state_path=state_path)
            audit_fn("stream_bindings.node.host_key_tofu",     # TOFU pin is explicit + audited
                     {"node_id": node.node_id, "fingerprint": fingerprint_fn(hk)},
                     outcome="applied")
            node = store.get_node(node.node_id, state_path=state_path)
    return SSHTransport(node.host, user=ssh_user, key_path=ssh_key,
                        sudo_password=sudo_password, host_key=node.host_key)
