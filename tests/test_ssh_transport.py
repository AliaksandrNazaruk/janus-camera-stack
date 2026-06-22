"""P3 — SSHTransport host-key pinning: accept-new only until a key is pinned."""
import os
import shutil
import subprocess

import pytest

from app.services import ssh_transport
from app.services.ssh_transport import SSHTransport


def test_opts_accept_new_without_pinned_key():
    opts = SSHTransport("10.0.0.5")._opts()
    assert "StrictHostKeyChecking=accept-new" in opts
    assert "StrictHostKeyChecking=yes" not in opts


def test_opts_pins_when_host_key_set():
    t = SSHTransport("10.0.0.5", host_key="10.0.0.5 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5xxxx")
    opts = t._opts()
    assert "StrictHostKeyChecking=yes" in opts
    assert "StrictHostKeyChecking=accept-new" not in opts
    khf = [o.split("=", 1)[1] for o in opts if o.startswith("UserKnownHostsFile=")][0]
    assert os.path.exists(khf)
    assert "ssh-ed25519" in open(khf).read()


# ── P4-SEC: out-of-band host-key fingerprint (Gap 2) ──────────────────

def test_host_key_fingerprint_empty_or_garbage_is_blank():
    assert ssh_transport.host_key_fingerprint("") == ""
    assert ssh_transport.host_key_fingerprint("   ") == ""


def test_host_key_fingerprint_matches_ssh_keygen(tmp_path):
    if not shutil.which("ssh-keygen"):
        pytest.skip("ssh-keygen not available")
    key = tmp_path / "k"
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key), "-q"], check=True)
    pub = (tmp_path / "k.pub").read_text().split()           # ["ssh-ed25519", "AAAA...", comment]
    line = f"node.example {pub[0]} {pub[1]}"                  # ssh-keyscan / known_hosts form
    fp = ssh_transport.host_key_fingerprint(line)
    out = subprocess.run(["ssh-keygen", "-lf", str(tmp_path / "k.pub")],
                         capture_output=True, text=True).stdout
    expected = [t for t in out.split() if t.startswith("SHA256:")][0]
    assert fp.startswith("SHA256:") and fp == expected       # same anchor the operator reads off the node
