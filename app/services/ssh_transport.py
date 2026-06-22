"""SSH transport for the node provisioner — pluggable so the onboarding state
machine is CI-testable with a fake (review O6).

The real implementation shells `ssh`/`scp` (key-based; host-key pinning is
Slice C). Elevation: an optional sudo password is fed to `sudo -S` over stdin
(dev) — never placed in argv/logs; production swaps to a pre-seeded scoped
NOPASSWD rule (Slice C). The shared dev sudo password is held only in memory for
the provisioning run (cf. feedback_sudo_password_handling).
"""
from __future__ import annotations

import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Tuple


@dataclass(frozen=True)
class RunResult:
    rc: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.rc == 0


class Transport(Protocol):
    host: str

    def run(self, cmd: str, *, sudo: bool = False, timeout: float = 60.0) -> RunResult: ...

    def push(self, local_path: str, remote_path: str, *, timeout: float = 120.0) -> RunResult: ...


class SSHTransport:
    """Key-based SSH/SCP to a node. `sudo=True` runs via `sudo -S` with the
    password on stdin (never in argv)."""

    def __init__(self, host: str, *, user: str = "boris",
                 sudo_password: Optional[str] = None, key_path: Optional[str] = None,
                 host_key: Optional[str] = None, connect_timeout: int = 5):
        self.host = host
        self.user = user
        self._sudo_password = sudo_password
        self._key_path = key_path
        self._host_key = host_key
        self._known_hosts: Optional[str] = None
        self._connect_timeout = connect_timeout

    def _opts(self) -> List[str]:
        opts = ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={self._connect_timeout}"]
        if self._host_key:
            # PIN: verify against the enrolled host key (no TOFU on this connect).
            if self._known_hosts is None:
                fh = tempfile.NamedTemporaryFile("w", suffix=".known_hosts", delete=False)
                fh.write(self._host_key.strip() + "\n")
                fh.close()
                self._known_hosts = fh.name
            opts += ["-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={self._known_hosts}"]
        else:
            # first contact only (key captured at enrollment, then pinned thereafter)
            opts += ["-o", "StrictHostKeyChecking=accept-new"]
        if self._key_path:
            opts += ["-i", self._key_path]
        return opts

    def _ssh_base(self) -> List[str]:
        return ["ssh", *self._opts(), f"{self.user}@{self.host}"]

    def run(self, cmd: str, *, sudo: bool = False, timeout: float = 60.0) -> RunResult:
        stdin: Optional[str] = None
        if sudo:
            remote = f"sudo -S -p '' bash -c {shlex.quote(cmd)}"
            stdin = (self._sudo_password or "") + "\n"   # consumed by sudo, not logged
        else:
            remote = cmd
        try:
            p = subprocess.run(self._ssh_base() + [remote], input=stdin,
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return RunResult(124, "", f"ssh timeout after {timeout}s")
        return RunResult(p.returncode, p.stdout, p.stderr)

    def push(self, local_path: str, remote_path: str, *, timeout: float = 120.0) -> RunResult:
        try:
            p = subprocess.run(["scp", "-q", *self._opts(),
                                local_path, f"{self.user}@{self.host}:{remote_path}"],
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return RunResult(124, "", f"scp timeout after {timeout}s")
        return RunResult(p.returncode, p.stdout, p.stderr)


class FakeTransport:
    """In-memory transport for tests — records calls, returns scripted results
    keyed by a substring of the command (default: success)."""

    def __init__(self, host: str = "fake", *, responses: Optional[Dict[str, RunResult]] = None):
        self.host = host
        self.calls: List[Tuple[str, object, bool]] = []   # (kind, cmd|args, sudo)
        self._responses = responses or {}

    def run(self, cmd: str, *, sudo: bool = False, timeout: float = 60.0) -> RunResult:
        self.calls.append(("run", cmd, sudo))
        for needle, result in self._responses.items():
            if needle in cmd:
                return result
        return RunResult(0)

    def push(self, local_path: str, remote_path: str, *, timeout: float = 120.0) -> RunResult:
        self.calls.append(("push", (local_path, remote_path), False))
        for needle, result in self._responses.items():
            if needle in remote_path or needle in local_path:
                return result
        return RunResult(0)


def capture_host_key(host: str, *, timeout: int = 8) -> str:
    """Capture a node's SSH host key (a known_hosts line) via ssh-keyscan. Returns
    "" on failure. ssh-keyscan trusts first contact, so a captured key is only a
    CANDIDATE — pin it via accept-new (dev) or, in prod, after out-of-band
    fingerprint confirmation (:func:`host_key_fingerprint` + the confirm endpoint)."""
    try:
        p = subprocess.run(["ssh-keyscan", "-T", "5", "-t", "ed25519", host],
                           capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""


def host_key_fingerprint(known_hosts_line: str, *, timeout: int = 5) -> str:
    """SHA256 fingerprint of a captured known_hosts line, in the `ssh-keygen -lf`
    form (e.g. ``SHA256:abc…``). The operator compares this against
    ``ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`` run on the node's console —
    that out-of-band match is the trust anchor for pinning (no TOFU). Returns "" if
    the line is empty or unparseable."""
    line = (known_hosts_line or "").strip()
    if not line:
        return ""
    try:
        p = subprocess.run(["ssh-keygen", "-lf", "-"], input=line + "\n",
                           capture_output=True, text=True, timeout=timeout)
        for tok in p.stdout.split():
            if tok.startswith("SHA256:"):
                return tok
    except (subprocess.SubprocessError, OSError):
        pass
    return ""
