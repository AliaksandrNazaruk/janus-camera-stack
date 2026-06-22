"""Track A step 1 — Settings fail-safe parsers + call-time reads for ice_policy/turn_cred_ttl.

Proves the apply mechanism (default_factory + cache_clear) AND the fail-safety that makes
the now-operator-writable env file safe (a malformed value must never crash get_settings()).
Backward-compatible: reads ICE_POLICY wherever it currently lives (no IaC change needed yet).
"""
import pytest

from app.core.settings import Settings, _int_env, _str_env, get_settings


@pytest.fixture(autouse=True)
def _fresh_settings():
    # Isolate the global lru_cache so per-test os.environ changes don't leak.
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ── _int_env (fail-safe + clamp) ─────────────────────────────────────────────

def test_int_env_valid(monkeypatch):
    monkeypatch.setenv("X_TTL", "1800")
    assert _int_env("X_TTL", 3600) == 1800

def test_int_env_unset_uses_default(monkeypatch):
    monkeypatch.delenv("X_TTL", raising=False)
    assert _int_env("X_TTL", 3600) == 3600

@pytest.mark.parametrize("bad", ["abc", "3600  # 1h", "3600s", "", "  ", "12.5", "0x10"])
def test_int_env_malformed_falls_back_no_raise(monkeypatch, bad):
    monkeypatch.setenv("X_TTL", bad)
    # MUST NOT raise — this is the app-wide DoS guard
    assert _int_env("X_TTL", 3600) == 3600

def test_int_env_clamps_to_bounds(monkeypatch):
    monkeypatch.setenv("X_TTL", "99999")
    assert _int_env("X_TTL", 3600, lo=300, hi=3600) == 3600   # above hi → hi
    monkeypatch.setenv("X_TTL", "10")
    assert _int_env("X_TTL", 3600, lo=300, hi=3600) == 300    # below lo → lo
    monkeypatch.setenv("X_TTL", "1800")
    assert _int_env("X_TTL", 3600, lo=300, hi=3600) == 1800   # in range → unchanged


# ── _str_env (allowlist) ─────────────────────────────────────────────────────

def test_str_env_in_allowed(monkeypatch):
    monkeypatch.setenv("X_POL", "relay")
    assert _str_env("X_POL", "all", allowed={"all", "relay"}) == "relay"

def test_str_env_not_in_allowed_falls_back(monkeypatch):
    monkeypatch.setenv("X_POL", "rely")  # typo must not slip through
    assert _str_env("X_POL", "all", allowed={"all", "relay"}) == "all"

def test_str_env_unset_uses_default(monkeypatch):
    monkeypatch.delenv("X_POL", raising=False)
    assert _str_env("X_POL", "all", allowed={"all", "relay"}) == "all"


# ── the two fields via get_settings() + cache_clear (the apply mechanism) ────

def test_cache_clear_refreshes_both_fields(monkeypatch):
    monkeypatch.setenv("ICE_POLICY", "all")
    monkeypatch.setenv("TURN_CRED_TTL", "3600")
    get_settings.cache_clear()
    assert get_settings().ice_policy == "all" and get_settings().turn_cred_ttl == 3600
    # mutate env + cache_clear → next get_settings() reflects (this is the apply primitive)
    monkeypatch.setenv("ICE_POLICY", "relay")
    monkeypatch.setenv("TURN_CRED_TTL", "1800")
    get_settings.cache_clear()
    assert get_settings().ice_policy == "relay" and get_settings().turn_cred_ttl == 1800

def test_cache_clear_is_mandatory(monkeypatch):
    monkeypatch.setenv("ICE_POLICY", "all")
    get_settings.cache_clear()
    assert get_settings().ice_policy == "all"
    monkeypatch.setenv("ICE_POLICY", "relay")
    # WITHOUT cache_clear → stale cached instance (proves cache_clear necessity)
    assert get_settings().ice_policy == "all"

def test_malformed_ttl_does_not_crash_get_settings(monkeypatch):
    # THE critical DoS regression: a bad runtime value must not 500 the whole app.
    monkeypatch.setenv("TURN_CRED_TTL", "3600  # operator inline comment")
    get_settings.cache_clear()
    s = get_settings()          # must not raise
    assert s.turn_cred_ttl == 3600

def test_invalid_ice_policy_falls_back_to_all(monkeypatch):
    monkeypatch.setenv("ICE_POLICY", "garbage")
    get_settings.cache_clear()
    assert get_settings().ice_policy == "all"

def test_ttl_out_of_range_clamped_via_settings(monkeypatch):
    monkeypatch.setenv("TURN_CRED_TTL", "999999")
    get_settings.cache_clear()
    assert get_settings().turn_cred_ttl == 3600   # clamped to hi


# ── backward-compat: safe BEFORE any IaC change (old Environment=ICE_POLICY) ──

def test_backward_compatible_reads_env_like_before(monkeypatch):
    # Whether ICE_POLICY comes from systemd Environment= or rs-runtime.env, the field
    # reads os.environ identically → no behavior change at this step.
    monkeypatch.setenv("ICE_POLICY", "relay")
    monkeypatch.setenv("TURN_CRED_TTL", "3600")
    get_settings.cache_clear()
    s = get_settings()
    assert s.ice_policy == "relay" and s.turn_cred_ttl == 3600
    # a direct Settings() also works (used by test fixtures) — but is NOT the lru_cache
    # path, so the regression guard above deliberately goes through get_settings().
    assert Settings().ice_policy == "relay"
