"""P3 — per-node RTP firewall reconciler: desired rules + dry-run + apply diff."""
from app.services import firewall_sync as fw
from app.services import stream_binding_store as sbs


def _remote_binding(tmp_path, host="192.168.1.55", sensor="color", mp=2001, port=5102):
    sp = tmp_path / "sb.json"
    ap = tmp_path / "al.json"
    n = sbs.add_node_by_host(host, state_path=sp)
    b = sbs.StreamBinding(
        binding_id=f"{n.node_id}:{sensor}", node_id=n.node_id, sensor=sensor,
        mode=sbs.StreamMode.REMOTE_PRODUCER,
        transport=sbs.StreamTransport(rtp_port=port, payload_type=96, codec="h264"),
        janus=sbs.StreamJanusConfig(mountpoint_id=mp, rtp_iface="192.168.1.10"))
    sbs.upsert_binding(b, state_path=sp, alloc_state_path=ap)
    return sp, ap, n, b


def test_desired_rules_per_node_32_and_backstop(tmp_path):
    sp, ap, n, b = _remote_binding(tmp_path)
    specs = [" ".join(r.args()) for r in fw.desired_rules(state_path=sp, alloc_state_path=ap)]
    # per-node /32 ACCEPT for RTP (5102) and RTCP (5103)
    assert any("-s 192.168.1.55/32" in s and "--dport 5102" in s and "-j ACCEPT" in s for s in specs)
    assert any("--dport 5103" in s and "-j ACCEPT" in s for s in specs)
    # fail-closed backstop DROP over the remote range
    assert any(f"--dport {fw.REMOTE_RTP_RANGE}" in s and "-j DROP" in s and "camnode:backstop" in s for s in specs)
    # M2: the backstop must cover beyond ordinal-0 — node N's window is
    # REMOTE_PORT_MIN + N*NODE_PORT_WINDOW, so a single 5100:5199 band left
    # ordinal>0 RTP outside the explicit DROP.
    _lo, _hi = (int(x) for x in fw.REMOTE_RTP_RANGE.split(":"))
    assert _lo == sbs.REMOTE_PORT_MIN
    assert _hi >= sbs.REMOTE_PORT_MIN + sbs.NODE_PORT_WINDOW   # covers at least node ordinal 1
    # every rule is tagged (single-writer), and no local/loopback rule leaks in
    assert all("camnode:" in s for s in specs)
    assert not any("127.0.0.1" in s for s in specs)


def test_reconcile_dryrun_is_safe_and_plans(tmp_path):
    sp, ap, *_ = _remote_binding(tmp_path)
    calls = []

    def fake_run(argv):
        calls.append(argv)
        return fw.CmdResult(0, "")          # iptables-save empty -> all desired are adds

    plan = fw.reconcile(state_path=sp, alloc_state_path=ap, apply=False, run=fake_run)
    assert not plan.is_noop
    assert any(r.comment == "backstop" for r in plan.add)
    assert any(r.comment.endswith(":5102") for r in plan.add)
    assert calls == [["iptables-save"]]      # dry-run touched nothing else


def test_reconcile_apply_inserts_desired_and_removes_stale(tmp_path):
    sp, ap, *_ = _remote_binding(tmp_path)
    cmds = []

    def fake_run(argv):
        cmds.append(argv)
        if argv == ["iptables-save"]:
            # nft quotes comments — the reconciler must still match + delete this.
            return fw.CmdResult(0,
                '-A INPUT -s 10.0.0.9/32 -p udp -m udp --dport 9999 '
                '-m comment --comment "camnode:node-stale:color:9999" -j ACCEPT\n')
        return fw.CmdResult(0, "")

    plan = fw.reconcile(state_path=sp, alloc_state_path=ap, apply=True, run=fake_run)
    assert "camnode:node-stale:color:9999" in plan.remove_comments   # stale detected
    joined = [" ".join(a) for a in cmds]
    assert any("iptables -I INPUT 1" in j and "--dport 5102" in j and "ACCEPT" in j for j in joined)
    assert any("iptables -I INPUT 1" in j and f"--dport {fw.REMOTE_RTP_RANGE}" in j and "DROP" in j for j in joined)
    # stale rule deleted via precise argv, comment unquoted by shlex (NOT a bash pipeline)
    assert any(a[:2] == ["iptables", "-D"] and "camnode:node-stale:color:9999" in a for a in cmds)
    assert not any(a and a[0] == "bash" for a in cmds)
    # ADD BEFORE REMOVE: every insert precedes every delete (no RTP-drop window on re-tag)
    last_add = max(i for i, a in enumerate(cmds) if a[:2] == ["iptables", "-I"])
    first_del = min(i for i, a in enumerate(cmds) if a[:2] == ["iptables", "-D"])
    assert last_add < first_del
