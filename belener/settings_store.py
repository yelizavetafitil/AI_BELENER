"""Tenant settings in PostgreSQL with encrypted secrets."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

_CACHE: dict[str, str] = {}
_CACHE_AT = 0.0
_CACHE_TTL = 5.0
_LOCK = threading.RLock()

_DB_GETTER: Any = None


def bind_db_getter(getter) -> None:
    global _DB_GETTER
    _DB_GETTER = getter


def _fernet():
    from cryptography.fernet import Fernet

    raw = (
        os.environ.get("SETTINGS_ENCRYPTION_KEY")
        or os.environ.get("AI_FLASK_SECRET")
        or "change-this-ai-flask-secret"
    )
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())
    return Fernet(key)


def _encrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except Exception:
        log.warning("settings_store: decrypt failed for key blob")
        return ""


def invalidate_cache() -> None:
    global _CACHE_AT
    with _LOCK:
        _CACHE.clear()
        _CACHE_AT = 0.0


def _load_all() -> dict[str, str]:
    global _CACHE, _CACHE_AT
    now = time.monotonic()
    with _LOCK:
        if _CACHE and now - _CACHE_AT < _CACHE_TTL:
            return dict(_CACHE)
    if _DB_GETTER is None:
        return {}
    out: dict[str, str] = {}
    try:
        with _DB_GETTER() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT key, value_enc, is_secret FROM tenant_settings")
                for row in cur.fetchall():
                    key = str(row["key"])
                    raw = str(row["value_enc"] or "")
                    if row["is_secret"]:
                        out[key] = _decrypt(raw)
                    else:
                        out[key] = raw
    except Exception as e:
        log.warning("settings_store: load failed: %s", e)
        return {}
    with _LOCK:
        _CACHE = dict(out)
        _CACHE_AT = now
    return dict(out)


def get_setting(key: str, default: str = "") -> str:
    val = _load_all().get(key)
    if val is None or val == "":
        return default
    return val


def get_settings_by_prefix(prefix: str) -> dict[str, str]:
    pref = prefix if prefix.endswith(".") else prefix + "."
    data = _load_all()
    return {k[len(pref):]: v for k, v in data.items() if k.startswith(pref) and v != ""}


def is_ad_configured() -> bool:
    data = _load_all()
    return bool(data.get("ad.uri") and data.get("ad.base_dn") and data.get("ad.bind_dn"))


def set_setting(key: str, value: str, *, is_secret: bool = False, category: str = "general", label: str = "", updated_by: str = "") -> None:
    if _DB_GETTER is None:
        raise RuntimeError("settings_store: DB not bound")
    stored = _encrypt(value) if is_secret else (value or "")
    with _DB_GETTER() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tenant_settings (key, value_enc, is_secret, category, label, updated_by, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (key) DO UPDATE SET
                  value_enc = EXCLUDED.value_enc,
                  is_secret = EXCLUDED.is_secret,
                  category = EXCLUDED.category,
                  label = COALESCE(NULLIF(EXCLUDED.label, ''), tenant_settings.label),
                  updated_by = EXCLUDED.updated_by,
                  updated_at = NOW()
                """,
                (key, stored, is_secret, category, label or None, updated_by or None),
            )
        conn.commit()
    invalidate_cache()


def set_many(items: list[dict[str, Any]], *, updated_by: str = "") -> None:
    for item in items:
        set_setting(
            item["key"],
            str(item.get("value") or ""),
            is_secret=bool(item.get("is_secret")),
            category=str(item.get("category") or "general"),
            label=str(item.get("label") or ""),
            updated_by=updated_by,
        )


def delete_setting(key: str) -> None:
    if _DB_GETTER is None:
        return
    with _DB_GETTER() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tenant_settings WHERE key = %s", (key,))
        conn.commit()
    invalidate_cache()


def migrate_env_if_empty() -> None:
    """One-time import from .env when DB has no integration secrets."""
    if _DB_GETTER is None:
        return
    data = _load_all()
    if any(k.startswith("stn.") for k in data):
        return
    login = (os.environ.get("PDF_STN_LOGIN") or os.environ.get("STN_LOGIN") or "").strip()
    password = (os.environ.get("PDF_STN_PASSWORD") or os.environ.get("STN_PASSWORD") or "").strip()
    if not login and not password:
        return
    base = (os.environ.get("PDF_STN_BASE_URL") or "https://normy.stn.by").strip()
    set_many(
        [
            {"key": "stn.base_url", "value": base, "category": "integration", "label": "URL Стройдок"},
            {"key": "stn.login", "value": login, "category": "integration", "label": "Логин Стройдок"},
            {"key": "stn.password", "value": password, "is_secret": True, "category": "integration", "label": "Пароль Стройдок"},
        ],
        updated_by="env-migrate",
    )
    log.info("settings_store: migrated STN credentials from environment")
