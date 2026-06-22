"""Cycle 5 — route-inventory lock for the stream_bindings router.

Behavior-preserving ORACLE for the fat-route split (single module -> package). The exact set of
(method, path) the stream_bindings router contributes under /api/v1/admin must NOT change when the
handlers are physically relocated into submodules. This test stays green verbatim across the split;
if a route is dropped, renamed, or its method changes, it fails loudly.
"""
from app.routes import stream_bindings as sb

# The frozen public surface (27 routes), captured 2026-06-21 before the split.
EXPECTED_ROUTES = {
    # nodes
    ("GET", "/api/v1/admin/nodes"),
    ("POST", "/api/v1/admin/nodes"),
    ("POST", "/api/v1/admin/nodes/register"),
    ("POST", "/api/v1/admin/nodes/check"),
    ("POST", "/api/v1/admin/nodes/{node_id}/provision"),
    ("POST", "/api/v1/admin/nodes/{node_id}/rotate-token"),
    ("POST", "/api/v1/admin/nodes/{node_id}/maintenance"),
    ("DELETE", "/api/v1/admin/nodes/{node_id}"),
    ("GET", "/api/v1/admin/nodes/{node_id}/host-key"),
    ("POST", "/api/v1/admin/nodes/{node_id}/host-key/confirm"),
    ("POST", "/api/v1/admin/nodes/{node_id}/streams"),
    # operations
    ("GET", "/api/v1/admin/operations"),
    ("GET", "/api/v1/admin/operations/{operation_id}"),
    # fleet + reconcile + firewall
    ("GET", "/api/v1/admin/fleet/plan"),
    ("POST", "/api/v1/admin/fleet/reconcile"),
    ("POST", "/api/v1/admin/firewall/reconcile"),
    ("GET", "/api/v1/admin/reconcile/drift"),
    ("POST", "/api/v1/admin/reconcile/janus/run-once"),
    # bindings
    ("GET", "/api/v1/admin/stream-bindings"),
    ("POST", "/api/v1/admin/stream-bindings"),
    ("POST", "/api/v1/admin/stream-bindings/{binding_id}/ensure-janus"),
    ("POST", "/api/v1/admin/stream-bindings/{binding_id}/remove"),
    ("POST", "/api/v1/admin/stream-bindings/{binding_id}/restart"),
    ("POST", "/api/v1/admin/stream-bindings/{binding_id}/stop"),
    ("POST", "/api/v1/admin/stream-bindings/{binding_id}/fdir"),
    ("GET", "/api/v1/admin/stream-bindings/{binding_id}/tuning"),
    ("POST", "/api/v1/admin/stream-bindings/{binding_id}/tuning"),
    ("GET", "/api/v1/admin/stream-bindings/{binding_id}/modes"),   # added: remote mode list
}


def _actual_routes() -> set:
    out = set()
    for r in sb.router.routes:
        for m in getattr(r, "methods", set()) or set():
            if m == "HEAD":
                continue
            out.add((m, r.path))
    return out


def test_stream_bindings_route_set_is_frozen():
    """The (method, path) set is exactly the expected surface — no unexpected add/drop/rename.
    The /modes GET was added intentionally (remote tuning dropdown); the oracle was updated with it."""
    actual = _actual_routes()
    assert actual == EXPECTED_ROUTES, (
        f"\nstream_bindings route surface drifted.\n"
        f"  missing: {sorted(EXPECTED_ROUTES - actual)}\n"
        f"  extra:   {sorted(actual - EXPECTED_ROUTES)}"
    )


def test_stream_bindings_route_count_is_28():
    assert len(_actual_routes()) == 28
