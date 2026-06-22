"""Use-case: reconcile the per-node RTP firewall from the binding store (dry-run unless apply).
Verbatim from routes/stream_bindings.firewall_reconcile (Phase 12.3C). Returns the plan summary the
route serves. firewall_sync is called, never changed."""
from __future__ import annotations

from app.services.audit_log import audit

from app.application.stream_bindings.commands import FirewallReconcileCommand


def firewall_reconcile(cmd: FirewallReconcileCommand) -> dict:
    from app.services import firewall_sync
    plan = firewall_sync.reconcile(state_path=cmd.bind_state_path,
                                   alloc_state_path=cmd.alloc_state_path, apply=cmd.apply)
    audit("stream_bindings.firewall.reconcile",
          {"apply": cmd.apply, "added": len(plan.add), "removed": len(plan.remove_comments)})
    return {"apply": cmd.apply, "added": [r.comment for r in plan.add],
            "removed": plan.remove_comments}
