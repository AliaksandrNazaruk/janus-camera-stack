"""Structural guard: the node bootstrap must be node-only / default-deny.

A camera node must never hold the gateway's Janus admin secret or run a
TURN/Janus/Cloudflare control plane (DYNAMIC_CAMERA_ONBOARDING.md §7; review
finding S7). This pins those invariants on the bundle's bootstrap.sh without
running it (CI-safe, no hardware).
"""
from pathlib import Path

BUNDLE = Path(__file__).resolve().parents[1] / "host_infra" / "node-bundle"
BOOTSTRAP = BUNDLE / "bootstrap.sh"

# Tokens that, in EXECUTABLE (non-comment) lines, would mean the node bundle can
# provision gateway-only / secret-bearing components.
FORBIDDEN = [
    "coturn", "turnserver", "cloudflared",
    "janus_admin", "streaming_admin", "turn_secret", "turn_shared",
    "generate_secret", "openssl rand", "install_janus",
]


def _code_lines(text: str) -> str:
    """Strip full-line and inline shell comments, lowercased."""
    out = []
    for line in text.splitlines():
        code = line.split("#", 1)[0]
        if code.strip():
            out.append(code)
    return "\n".join(out).lower()


def test_bootstrap_exists():
    assert BOOTSTRAP.is_file(), f"missing {BOOTSTRAP}"


def test_bootstrap_is_default_deny_node_only():
    code = _code_lines(BOOTSTRAP.read_text())
    for tok in FORBIDDEN:
        assert tok not in code, f"node bootstrap must not contain gateway/secret token: {tok!r}"


def test_bootstrap_has_safety_and_uniform_modes():
    text = BOOTSTRAP.read_text()
    assert "set -euo pipefail" in text, "bootstrap must use strict bash mode"
    # sensor-AGNOSTIC modes: deploy the pipe, then activate any subset of streams;
    # set-token rotates the per-node agent token (P4-SEC)
    for mode in ("probe)", "deploy)", "activate)", "deactivate)", "set-token)"):
        assert mode in text, f"bootstrap must support mode {mode}"
    assert "--sensor" in text, "activation must be per-sensor (uniform across streams)"
    # contract path is parameterized by sensor — no single hardcoded stream
    assert "rs-${SENSOR}.contract.env" in text, "contract must be sensor-parameterized"
    assert "RTP_TARGET_HOST" in text


def test_bootstrap_set_token_rotates_agent_only():
    import re
    text = BOOTSTRAP.read_text()
    assert "cmd_set_token" in text
    m = re.search(r"cmd_set_token\(\)\s*\{(.*?)\n\}", text, re.S)
    assert m, "cmd_set_token function not found"
    body = m.group(1)
    # rewrites the agent env + restarts ONLY the agent — the mux/encoders keep flowing
    assert "node-agent.env" in body
    assert "camera-node-agent" in body
    assert "realsense-mux" not in body, "token rotation must not restart the mux (streams must not blip)"


def test_node_agent_tuning_validation():
    """The node-agent /tuning writer validates inputs before touching tuning.env."""
    import importlib.util
    p = BUNDLE / "node-agent" / "camera-node-agent.py"
    spec = importlib.util.spec_from_file_location("_camera_node_agent", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m._validate_tuning({"rotation": 90})[1] is None
    assert m._validate_tuning({"rotation": 45})[1]          # rotation must be 0/90/180/270
    assert m._validate_tuning({"fps": 1000})[1]             # out of range
    assert m._validate_tuning({"width": "x"})[1]            # non-integer
    assert m._validate_tuning({})[1]                        # nothing provided
    upd, err = m._validate_tuning({"width": 1280, "height": 720, "rotation": 180})
    assert err is None and upd == {"WIDTH": "1280", "HEIGHT": "720", "ROTATION": "180"}


def test_bootstrap_deploy_restarts_agent_to_load_new_code():
    """Regression (found on .55 bench): a re-deploy installs new agent code but must
    actually RESTART the agent — `enable --now` only starts a stopped unit, so new
    endpoints would silently not load on an already-running agent."""
    import re
    m = re.search(r"cmd_deploy\(\)\s*\{(.*?)\n\}", BOOTSTRAP.read_text(), re.S)
    assert m, "cmd_deploy function not found"
    body = m.group(1)
    assert "daemon-reload" in body, "deploy must daemon-reload after installing the unit file"
    assert re.search(r"systemctl restart camera-node-agent", body), \
        "deploy must RESTART the node-agent (enable --now won't reload new code on a running agent)"


def test_bootstrap_no_eval_and_validates_untrusted_args():
    """H2: bootstrap must not eval, and untrusted activate args (rtp host/port/
    agent-token) must be validated so they cannot carry shell metacharacters."""
    text = BOOTSTRAP.read_text()
    code = _code_lines(text)
    assert "eval " not in code and "eval\t" not in code, "bootstrap must not use eval (review H2)"
    for fn in ("_valid_ipv4", "_valid_port", "_valid_token"):
        assert fn in text, f"missing untrusted-arg validator {fn}"
    import re
    body = re.search(r"cmd_activate\(\)\s*\{(.*?)\n\}", text, re.S)
    assert body and "_valid_ipv4" in body.group(1) and "_valid_port" in body.group(1), \
        "cmd_activate must validate rtp-target-host (IPv4) + rtp-port"


def test_node_agent_fail_closed_without_token():
    """H4: node-agent must refuse a non-loopback bind when NODE_AGENT_TOKEN is
    unset — no unauthenticated control plane exposed on the camera LAN."""
    agent = (BUNDLE / "node-agent" / "camera-node-agent.py").read_text()
    assert "_LOOPBACK_BINDS" in agent
    assert "not TOKEN and BIND not in _LOOPBACK_BINDS" in agent, "missing fail-closed guard"
    assert "sys.exit(1)" in agent, "fail-closed must exit, not warn-and-serve"


def test_build_script_does_not_bundle_gateway_files():
    # NB: a bare "janus" token would false-positive on the legitimate
    # janus_camera_page/installer/wheels/ path; forbid actual gateway artifacts.
    code = _code_lines((BUNDLE / "build-bundle.sh").read_text())
    for tok in ("coturn", "camera-secrets", "janus_admin", "streaming_admin",
                "generate_secret", ".jcfg"):
        assert tok not in code, f"build-bundle must not stage gateway artifact: {tok!r}"
