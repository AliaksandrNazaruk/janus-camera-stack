"""Cycle 10 — turn_credentials (extracted from nat_config): coturn TURN REST-API ephemeral creds."""
import base64
import hashlib
import hmac

from app.services.turn_credentials import generate_turn_credentials


def test_username_is_expiry_colon_user():
    user, _cred = generate_turn_credentials("secret", user="webrtc", ttl=3600)
    expiry, name = user.split(":", 1)
    assert name == "webrtc" and int(expiry) > 0


def test_credential_is_hmac_sha1_base64_of_username():
    secret = "shared-secret"
    user, cred = generate_turn_credentials(secret, user="alice", ttl=60)
    expected = base64.b64encode(
        hmac.new(secret.encode(), user.encode(), hashlib.sha1).digest()).decode()
    assert cred == expected   # exact coturn use-auth-secret scheme


def test_default_user_and_distinct_secrets_differ():
    u1, c1 = generate_turn_credentials("s1")
    u2, c2 = generate_turn_credentials("s2", user="webrtc")
    assert u1.endswith(":webrtc")
    # same username window but different shared secret → different credential
    c2_with_u1 = base64.b64encode(
        hmac.new(b"s2", u1.encode(), hashlib.sha1).digest()).decode()
    assert c1 != c2_with_u1
