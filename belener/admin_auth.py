"""Active Directory authentication (ldap3), adapted from enterprise hub pattern."""

from __future__ import annotations

import collections
import logging
import re
import threading
from typing import Any

log = logging.getLogger(__name__)

# ldap3 on Python 3.10+
if not hasattr(collections, "MutableMapping"):
    import collections.abc

    collections.MutableMapping = collections.abc.MutableMapping
if not hasattr(collections, "Sequence"):
    import collections.abc

    collections.Sequence = collections.abc.Sequence

from ldap3 import ALL, SUBTREE, Connection, Server
from ldap3.core.exceptions import LDAPException

_AD_GROUPS_CACHE: dict[str, list[str]] = {}
_CACHE_LOCK = threading.Lock()

_SAFE_LOGIN = re.compile(r"^[a-z0-9._@-]{1,64}$", re.I)


def normalize_ad_username(raw_username: str) -> str:
    username = (raw_username or "").strip().lower()
    if not username:
        return ""
    if "\\" in username:
        username = username.split("\\")[-1].strip()
    if "@" in username:
        username = username.split("@")[0].strip()
    return username


def _escape_filter(value: str) -> str:
    from ldap3.utils.conv import escape_filter_chars

    return escape_filter_chars(value, encoding="utf-8")


def load_ad_config() -> dict[str, str]:
    from belener.settings_store import get_setting

    return {
        "uri": get_setting("ad.uri"),
        "base": get_setting("ad.base_dn"),
        "bind_dn": get_setting("ad.bind_dn"),
        "bind_password": get_setting("ad.bind_password"),
        "user_attr": get_setting("ad.user_attr", "sAMAccountName"),
        "admin_users": get_setting("ad.admin_users"),
        "admin_group": get_setting("ad.admin_group"),
    }


def admin_usernames(cfg: dict[str, str] | None = None) -> set[str]:
    import os

    cfg = cfg or load_ad_config()
    raw = (cfg.get("admin_users") or "").strip()
    env_raw = (os.environ.get("ADMIN_USERS") or "").strip()
    names: set[str] = set()
    for chunk in (raw, env_raw):
        for part in chunk.replace(";", ",").split(","):
            u = normalize_ad_username(part)
            if u:
                names.add(u)
    return names


def get_user_ad_groups(conn: Connection, user_dn: str, base_dn: str) -> list[str]:
    search_filter = f"(member:1.2.840.113556.1.4.1941:={user_dn})"
    conn.search(base_dn, search_filter, SUBTREE, attributes=["sAMAccountName", "cn"])
    groups: list[str] = []
    for entry in conn.entries:
        if entry.sAMAccountName:
            groups.append(str(entry.sAMAccountName).lower())
        if entry.cn:
            groups.append(str(entry.cn).lower())
    return list(set(groups))


def check_ldap_auth(username: str, password: str, cfg: dict[str, str] | None = None) -> tuple[bool, str, dict[str, Any]]:
    login = normalize_ad_username(username)
    if not login or not password:
        return False, "Укажите логин и пароль", {}
    if not _SAFE_LOGIN.match(login):
        return False, "Недопустимый формат логина", {}

    cfg = cfg or load_ad_config()
    uri = (cfg.get("uri") or "").strip()
    base = (cfg.get("base") or "").strip()
    bind_dn = (cfg.get("bind_dn") or "").strip()
    bind_password = cfg.get("bind_password") or ""
    user_attr = (cfg.get("user_attr") or "sAMAccountName").strip()

    if not uri or not base or not bind_dn:
        return False, "Active Directory не настроен", {}

    try:
        server = Server(uri, get_info=ALL, connect_timeout=8)
        conn = Connection(server, user=bind_dn, password=bind_password, auto_bind=True)
        search_filter = f"({user_attr}={_escape_filter(login)})"
        conn.search(base, search_filter, SUBTREE, attributes=["distinguishedName", "displayName", "cn"])
        if not conn.entries:
            return False, "Пользователь не найден в AD", {}
        user_entry = conn.entries[0]
        user_dn = user_entry.distinguishedName.value
        display_name = ""
        if user_entry.displayName:
            display_name = str(user_entry.displayName).strip()
        if not display_name and user_entry.cn:
            display_name = str(user_entry.cn).strip()
        Connection(server, user=user_dn, password=password, auto_bind=True)
        groups = get_user_ad_groups(conn, user_dn, base)
        with _CACHE_LOCK:
            _AD_GROUPS_CACHE[login] = groups
        meta = {"display_name": display_name or login, "groups": groups}
        return True, "", meta
    except LDAPException as exc:
        log.warning("LDAP auth failed for %s: %s", login, exc)
        return False, "Неверный логин или пароль AD", {}
    except Exception as exc:
        log.exception("Unexpected LDAP error for %s: %s", login, exc)
        return False, "Ошибка подключения к AD", {}


def _ldap_error_message(exc: Exception) -> str:
    text = str(exc).casefold()
    if "invalidcredentials" in text.replace(" ", "").replace("_", ""):
        return "Неверный пароль или логин служебной учётки (Bind DN). Проверьте Bind DN и пароль."
    if "server down" in text or "can't contact ldap" in text or "connection" in text:
        return "Не удалось подключиться к контроллеру домена. Проверьте LDAP URI и доступность сервера."
    if "nosuchobject" in text.replace(" ", ""):
        return "Base DN или Bind DN указаны неверно — объект не найден в каталоге."
    return f"Ошибка LDAP: {exc}"


def test_ldap_connection(cfg: dict[str, str]) -> tuple[bool, str]:
    uri = (cfg.get("uri") or "").strip()
    base = (cfg.get("base") or "").strip()
    bind_dn = (cfg.get("bind_dn") or "").strip()
    bind_password = cfg.get("bind_password") or ""
    if not uri or not base or not bind_dn:
        return False, "Заполните URI, Base DN и Bind DN"
    try:
        server = Server(uri, get_info=ALL, connect_timeout=8)
        conn = Connection(server, user=bind_dn, password=bind_password, auto_bind=True)
        conn.search(base, "(objectClass=*)", SUBTREE, attributes=["distinguishedName"], size_limit=1)
        return True, "Подключение к AD успешно"
    except LDAPException as exc:
        return False, _ldap_error_message(exc)
    except Exception as exc:
        return False, _ldap_error_message(exc)


def user_is_admin(username: str, groups: list[str] | None = None) -> bool:
    login = normalize_ad_username(username)
    if not login:
        return False
    cfg = load_ad_config()
    allowed = admin_usernames(cfg)
    if login in allowed:
        return True
    admin_group = (cfg.get("admin_group") or "").strip().lower()
    if not admin_group:
        return False
    grp = groups
    if grp is None:
        with _CACHE_LOCK:
            grp = _AD_GROUPS_CACHE.get(login, [])
    return admin_group in {g.lower() for g in (grp or [])}


def clear_user_cache(username: str = "") -> None:
    login = normalize_ad_username(username)
    with _CACHE_LOCK:
        if login:
            _AD_GROUPS_CACHE.pop(login, None)
        else:
            _AD_GROUPS_CACHE.clear()
