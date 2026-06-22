"""Unit-тесты для signing.py — HMAC fingerprint signing.
"""
from __future__ import annotations

from camera_bringup.signing import (
    _canonical_json,
    attach_signature,
    generate_secret,
    sign,
    verify,
)


class TestCanonicalJson:
    def test_sort_keys(self):
        a = {"b": 1, "a": 2}
        b = {"a": 2, "b": 1}
        assert _canonical_json(a) == _canonical_json(b)

    def test_excludes_hmac_field(self):
        """Поле _hmac исключается из payload-под-подпись (chicken-egg)."""
        a = {"x": 1, "_hmac": "deadbeef"}
        b = {"x": 1, "_hmac": "different"}
        assert _canonical_json(a) == _canonical_json(b)

    def test_no_whitespace(self):
        result = _canonical_json({"a": 1, "b": 2})
        assert b" " not in result
        assert b"\n" not in result


class TestSignVerify:
    def test_sign_deterministic(self):
        secret = b"x" * 32
        payload = {"a": 1, "b": 2}
        assert sign(payload, secret) == sign(payload, secret)

    def test_different_secrets_different_sigs(self):
        payload = {"a": 1}
        s1 = sign(payload, b"x" * 32)
        s2 = sign(payload, b"y" * 32)
        assert s1 != s2

    def test_verify_valid(self):
        secret = generate_secret()
        payload = {"camera": {"serial": "123"}}
        signed = attach_signature(payload, secret)
        assert verify(signed, secret) is True

    def test_verify_tampered_content(self):
        secret = generate_secret()
        payload = {"camera": {"serial": "123"}}
        signed = attach_signature(payload, secret)
        # Подделать content
        signed["camera"]["serial"] = "999"
        assert verify(signed, secret) is False

    def test_verify_tampered_signature(self):
        secret = generate_secret()
        payload = {"camera": {"serial": "123"}}
        signed = attach_signature(payload, secret)
        signed["_hmac"] = "0" * 64  # fake sig
        assert verify(signed, secret) is False

    def test_verify_wrong_secret(self):
        secret1 = generate_secret()
        secret2 = generate_secret()
        payload = {"camera": {"serial": "123"}}
        signed = attach_signature(payload, secret1)
        assert verify(signed, secret2) is False

    def test_verify_unsigned_payload(self):
        secret = generate_secret()
        assert verify({"a": 1}, secret) is False   # no _hmac field

    def test_generate_secret_is_32_bytes(self):
        s = generate_secret()
        assert len(s) == 32

    def test_generate_secret_uniqueness(self):
        s1 = generate_secret()
        s2 = generate_secret()
        assert s1 != s2

    def test_attach_signature_idempotent(self):
        """attach_signature не модифицирует existing _hmac payload — пере-подписывает."""
        secret = generate_secret()
        original = {"a": 1}
        signed_once = attach_signature(original, secret)
        signed_twice = attach_signature(signed_once, secret)
        # Both должны иметь одинаковую signature
        assert signed_once["_hmac"] == signed_twice["_hmac"]
