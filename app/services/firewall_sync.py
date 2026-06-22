"""P3 — per-node fail-closed RTP firewall reconciler (single writer).

Computes the desired iptables rules from the StreamBinding store and reconciles
INPUT to them: one ACCEPT per (remote node IP, rtp/rtcp port) + a backstop DROP
over the remote RTP range, so ONLY a binding's own node may send RTP to its port
(review S1/S2/S11). The reconciler is the SOLE writer of its rules — each carries
a `camnode:<id>` iptables comment, and stale-tagged rules are removed (review
L2/O5: no rule leak on node churn).

Caveats (complementary controls, not done here): UDP source IPs are spoofable
on-LAN, so the /32 ACCEPT is defence-in-depth — pair with rp_filter + SRTP for a
real authenticity guarantee. Applying live is a separate, ordered, verified step
(don't narrow a running gateway blindly); `reconcile(apply=False)` returns the
plan for review.
"""
from __future__ import annotations

import logging
import shlex
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional

from app.services import mountpoint_allocator
from app.services import stream_binding_store as sbs

log = logging.getLogger(__name__)

TAG = "camnode"
# Fail-closed backstop DROP must cover the WHOLE planned remote pool, not just
# the ordinal-0 window: node N's RTP lands in [REMOTE_PORT_MIN + N*NODE_PORT_WINDOW,
# +NODE_PORT_WINDOW). The old hardcoded "5100:5199" left ordinal>0 ports outside
# the explicit DROP (review M2). Derive from the store so it can't drift.
REMOTE_RTP_RANGE = f"{sbs.REMOTE_PORT_MIN}:{sbs.REMOTE_PORT_MIN + sbs.MAX_REMOTE_NODES * sbs.NODE_PORT_WINDOW - 1}"
CHAIN = "INPUT"

# Injectable command runner: (argv) -> (rc, stdout). Default shells iptables.
Runner = Callable[[List[str]], "CmdResult"]


@dataclass(frozen=True)
class CmdResult:
    rc: int
    out: str = ""


def _real_run(argv: List[str]) -> CmdResult:
    p = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    return CmdResult(p.returncode, p.stdout)


@dataclass(frozen=True)
class Rule:
    """One desired INPUT rule, as the args after `-A INPUT` (comment-tagged)."""
    spec: tuple
    comment: str

    def args(self) -> List[str]:
        return [*self.spec, "-m", "comment", "--comment", f"{TAG}:{self.comment}"]


def desired_rules(*, state_path=sbs.DEFAULT_STATE_PATH,
                  alloc_state_path=mountpoint_allocator.DEFAULT_STATE_PATH) -> List[Rule]:
    """Desired fail-closed rules from the active remote bindings: per node-IP+port
    ACCEPTs, then a single backstop DROP for the whole remote RTP range."""
    nodes = sbs.list_nodes(state_path=state_path)
    bindings = sbs.list_bindings(state_path=state_path, alloc_state_path=alloc_state_path)
    rules: List[Rule] = []
    for b in bindings.values():
        if b.mode != sbs.StreamMode.REMOTE_PRODUCER:
            continue
        node = nodes.get(b.node_id)
        if not node or not node.host:
            continue
        for port in (b.transport.rtp_port, b.transport.rtp_port + 1):   # RTP + RTCP
            rules.append(Rule(
                spec=("-s", f"{node.host}/32", "-p", "udp", "--dport", str(port), "-j", "ACCEPT"),
                comment=f"{b.binding_id}:{port}"))   # unique per (binding, port)
    # Backstop: anything else in the remote RTP range is dropped (fail-closed).
    rules.append(Rule(spec=("-p", "udp", "--dport", REMOTE_RTP_RANGE, "-j", "DROP"),
                      comment="backstop"))
    return rules


def _current_tagged_lines(run: Runner) -> List[str]:
    """Full `-A INPUT ...` rule lines carrying a camnode:* comment (via iptables-save).
    Matches both the bare and the quoted (`--comment "camnode:..."`) forms — nft's
    iptables-save quotes comments, so a bare-only match would miss every rule."""
    res = run(["iptables-save"])
    return [ln for ln in res.out.splitlines()
            if ln.startswith("-A ")
            and (f"--comment {TAG}:" in ln or f'--comment "{TAG}:' in ln)]


def _tag_of_line(line: str) -> Optional[str]:
    """The camnode:* tag in an iptables-save rule line, quotes stripped (shlex)."""
    try:
        parts = shlex.split(line)        # strips the quotes nft wraps comments in
    except ValueError:
        return None
    if "--comment" in parts:
        c = parts[parts.index("--comment") + 1]
        if c.startswith(f"{TAG}:"):
            return c
    return None


def _current_tagged(run: Runner) -> List[str]:
    """Comment tags of the camnode:* rules currently in INPUT."""
    tags: List[str] = []
    for line in _current_tagged_lines(run):
        t = _tag_of_line(line)
        if t:
            tags.append(t)
    return tags


@dataclass
class Plan:
    add: List[Rule]
    remove_comments: List[str]

    @property
    def is_noop(self) -> bool:
        return not self.add and not self.remove_comments


def reconcile(*, state_path=sbs.DEFAULT_STATE_PATH,
              alloc_state_path=mountpoint_allocator.DEFAULT_STATE_PATH,
              apply: bool = False, run: Optional[Runner] = None) -> Plan:
    """Diff desired vs current camnode:* rules. apply=False returns the plan only
    (dry-run, safe). apply=True inserts missing ACCEPTs above the backstop above
    the catch-all DROP, and deletes stale-tagged rules — via `run`."""
    run = run or _real_run
    desired = desired_rules(state_path=state_path, alloc_state_path=alloc_state_path)
    want = {f"{TAG}:{r.comment}": r for r in desired}
    have = set(_current_tagged(run))
    add = [r for tag, r in want.items() if tag not in have]
    remove = [t for t in have if t not in want]
    plan = Plan(add=add, remove_comments=remove)

    if not apply:
        for r in plan.add:
            log.info("[firewall dry-run] +ACCEPT/DROP %s", " ".join(r.args()))
        for t in plan.remove_comments:
            log.info("[firewall dry-run] -%s", t)
        return plan

    # ADD BEFORE REMOVE: insert the desired rules first, then delete the stale ones.
    # A binding_id change (e.g. node-keyed → serial-keyed migration) re-tags a rule
    # whose EFFECTIVE match (src /32, port) is unchanged — only its comment differs.
    # Adding the new-tagged ACCEPT before deleting the old-tagged one means the
    # `<ip>/32 → port` ACCEPT is continuously present, so a node's RTP is never
    # momentarily exposed to the backstop DROP during a reconcile (no drop window).
    # Order among adds: the per-node ACCEPTs must sit ABOVE the backstop DROP, and the
    # backstop ABOVE any broad rule + the catch-all DROP. Insert the backstop at the
    # top FIRST, then the ACCEPTs at the top → final order ACCEPTs ... backstop ...
    # (broad/catch-all below). All via -I 1 (never -A, which lands below the catch-all).
    accepts = [r for r in plan.add if r.comment != "backstop"]
    backstop = [r for r in plan.add if r.comment == "backstop"]
    for r in backstop:
        run(["iptables", "-I", CHAIN, "1", *r.args()])
    for r in accepts:
        run(["iptables", "-I", CHAIN, "1", *r.args()])
    # Now remove stale tagged rules. Parse each camnode line from iptables-save with
    # shlex (which strips the quotes nft wraps the comment in) and issue a precise
    # `iptables -D` with the same args. Shelling `iptables $l` would split the quoted
    # comment into a token that still carries literal quotes and never matches — so
    # we delete via argv, not a shell pipeline.
    if plan.remove_comments:
        stale = set(plan.remove_comments)
        for line in _current_tagged_lines(run):
            if _tag_of_line(line) in stale:
                parts = shlex.split(line)        # ["-A", "INPUT", ..., "-j", "ACCEPT"]
                run(["iptables", "-D", *parts[1:]])
    log.info("firewall reconcile applied: +%d -%d", len(plan.add), len(plan.remove_comments))
    return plan
