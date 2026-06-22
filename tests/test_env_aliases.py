"""A0 config-drift fix — env name alias resolution.

Deployment manifests historically used different env names than the code reads
(JANUS_API_URL vs JANUS_URL, etc.), so containers silently fell back to
127.0.0.1 defaults. settings._aliased_env() accepts the legacy names for
backward-compat while the manifests are aligned to canonical names.
"""
from app.core import settings as S


def test_canonical_takes_precedence(monkeypatch):
    monkeypatch.setenv("JANUS_URL", "http://canonical:8088/janus")
    monkeypatch.setenv("JANUS_API_URL", "http://legacy:8088/janus")
    assert S._aliased_env("JANUS_URL") == "http://canonical:8088/janus"


def test_legacy_alias_used_when_canonical_absent(monkeypatch):
    monkeypatch.delenv("JANUS_URL", raising=False)
    monkeypatch.setenv("JANUS_API_URL", "http://legacy:8088/janus")
    S._ENV_DRIFT_USED.clear()
    assert S._aliased_env("JANUS_URL") == "http://legacy:8088/janus"
    assert any("JANUS_API_URL" in pair for pair in S._ENV_DRIFT_USED), \
        "legacy alias use must be recorded for the startup drift warning"


def test_default_when_neither_set(monkeypatch):
    monkeypatch.delenv("CAM_TYPE", raising=False)
    monkeypatch.delenv("CAMERA_TYPE", raising=False)
    assert S._aliased_env("CAM_TYPE", "color_camera") == "color_camera"
    assert S._aliased_env("JANUS_URL") is None  # no default -> None


def test_all_documented_aliases_resolve(monkeypatch):
    """Every (legacy -> canonical) mapping in _ENV_ALIASES must resolve."""
    cases = {
        "JANUS_URL": "JANUS_API_URL",
        "JANUS_WS_URL_1": "JANUS_WS_URL",
        "RELAY_URL": "RELAY_INTERNAL_URL",
        "TURN_SHARED_SECRET": "TURN_SECRET",
        "CAM_TYPE": "CAMERA_TYPE",
    }
    # mapping table and the test must stay in sync
    assert {k: v[0] for k, v in S._ENV_ALIASES.items()} == cases
    for canonical, legacy in cases.items():
        monkeypatch.delenv(canonical, raising=False)
        monkeypatch.setenv(legacy, f"val-{legacy}")
        assert S._aliased_env(canonical) == f"val-{legacy}"
