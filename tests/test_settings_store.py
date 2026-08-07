"""Tests for tenant settings store."""

from belener.settings_store import _decrypt, _encrypt, invalidate_cache


def test_encrypt_roundtrip():
    raw = "secret-password-123"
    enc = _encrypt(raw)
    assert enc != raw
    assert _decrypt(enc) == raw


def test_invalidate_cache():
    invalidate_cache()
