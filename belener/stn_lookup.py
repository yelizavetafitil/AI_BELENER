"""Проверка нормативов на normy.stn.by (фонд ТНПА РБ)."""

from __future__ import annotations

import http.cookiejar
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from html import unescape
from typing import Any

from belener.config import (
    stn_base_url,
    stn_login,
    stn_lookup_enabled,
    stn_max_queries,
    stn_max_refs,
    stn_parallel_workers,
    stn_password,
    stn_timeout_sec,
)

log = logging.getLogger("belener.stn_lookup")

# Приоритетные типы фонда STN (остальные тоже пробуем искать).
STN_FUND_KINDS: frozenset[str] = frozenset(
    {"ГОСТ", "ОСТ", "СТБ", "СТП", "СНиП", "ТКП", "СП", "ТР"}
)
STN_CHECKABLE_KINDS = STN_FUND_KINDS  # совместимость

_STN_DOCTYPES = {
    "doctype[13]": "on",
    "doctype[14]": "on",
    "doctype[23]": "on",
    "doctype[5]": "on",
    "doctype[6]": "on",
    "doctype[2]": "on",
    "doctype[4]": "on",
}

_CARD_ROW = re.compile(
    r'<tr><td class="doc-card-header">([^<]+)</td><td>(.*?)</td></tr>',
    re.I | re.S,
)
_CARD_ROW_DIV = re.compile(
    r'<div class="col-xs-[^"]* cardheader">([^<]+)</div>\s*'
    r'<div class="col-xs-[^"]* carddata">(.*?)</div>',
    re.I | re.S,
)
_STN_FORM_EMPTY: dict[str, str] = {
    "code": "",
    "name": "",
    "datefrom": "",
    "dateto": "",
    "block": "0",
    "kgs": "",
    "mks": "",
}
_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_NORM_TYPE_RX = (
    r"(?:ГОСТ|GOST|ОСТ|OST|OCT|ТКП|TKP|СНиП|SNIP|СП|SP|"
    r"ТУ|TU|СТП|STP|РД|RD|СО|CO|SO|СТБ|STB)\b"
)
_PART_SPEC_PREFIX = re.compile(
    rf"^(?:(?!\d{{2}}\s)(?:\d+[xх×][\d.,\-–—]+|\d+[\-–—][А-ЯA-Za-z][\w\-–—]*|[А-ЯA-Za-z][\w\-–—]*)\s+)+"
    rf"(?={_NORM_TYPE_RX})",
    re.I,
)


def _strip_part_spec_prefix(ref: str) -> str:
    """Убрать материал/позицию таблицы перед типом норматива (только для запроса STN)."""
    s = _light_clean(ref)
    if not s:
        return s
    s = _PART_SPEC_PREFIX.sub("", s)
    s = re.sub(
        rf"^(?!(?:0[1-9]|1[0-9]|20)\s)\d{{1,3}}\s+(?={_NORM_TYPE_RX})",
        "",
        s,
        flags=re.I,
    )
    return s.strip()


@dataclass
class StnCheckResult:
    kind: str
    ref: str
    query: str
    found: bool
    stn_code: str = ""
    stn_name: str = ""
    intro_date: str = ""
    cancel_date: str = ""
    status: str = ""
    doc_id: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "ref": self.ref,
            "query": self.query,
            "found": "1" if self.found else "0",
            "stn_code": self.stn_code,
            "stn_name": self.stn_name,
            "intro_date": self.intro_date,
            "cancel_date": self.cancel_date,
            "status": self.status,
            "doc_id": self.doc_id,
            "error": self.error,
        }


def is_stn_checkable(kind: str) -> bool:
    return (kind or "").strip() in STN_FUND_KINDS


def _norm_code(s: str) -> str:
    s = (s or "").replace("–", "-").replace("—", "-")
    s = re.sub(r"\(\s*\d{4,6}\s*\)", "", s)  # (02250) на сайте
    return re.sub(r"\s+", "", s).casefold()


def _light_clean(raw: str) -> str:
    s = (raw or "").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s.strip())


def _clean_stn_query(q: str) -> str:
    """Финальная строка для API: без лишних пробелов и символов."""
    s = (q or "").replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
    s = re.sub(r"[\–—−‑‒]", "-", s)
    s = re.sub(r'[«»"\'`]+', "", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*\.\s*", ".", s)
    s = re.sub(r"\s*-\s*", "-", s)
    return s.strip(" .-")


def _sanitize_stn_ref(kind: str, ref: str) -> str:
    """OCR-мусор перед поиском на normy.stn.by (обозначение на листе не меняем)."""
    s = _clean_stn_query(ref)
    s = _strip_part_spec_prefix(s)
    s = re.sub(r"\(\s*\d{4,6}\s*\)", "", s).strip()
    s = re.sub(r"(?i)т\s*к\s*п", "ТКП", s)
    s = re.sub(r"(?i)t\s*k\s*p", "ТКП", s)
    s = re.sub(r"(?i)с\s*н\s*и\s*п", "СНиП", s)
    s = re.sub(r"(?i)s\s*n\s*i\s*p", "СНиП", s)
    s = re.sub(r"(?i)g\s*o\s*s\s*t", "ГОСТ", s)
    s = re.sub(r"(?i)gost", "ГОСТ", s)
    s = re.sub(r"(?i)tkp", "ТКП", s)
    s = re.sub(r"(?i)snip", "СНиП", s)
    s = re.sub(r"(?i)stb", "СТБ", s)
    if kind:
        s = re.sub(rf"(?i)({re.escape(kind)})\s*(\d)", rf"\1 \2", s)
    return re.sub(r"\s+", " ", s).strip()


def _normalize_stn_number(num: str, kind: str = "") -> str:
    """Номер как на normy.stn.by: 45-4.03-267-2012, 34.10.761-97."""
    from belener.normative_refs import format_ost_number

    s = _clean_stn_query(num)
    if (kind or "").strip().casefold() in ("ост", "oct", "ost"):
        return format_ost_number(s).strip(" .-")
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"(\d)\s+(\d)", r"\1.\2", s)
    return s.strip(" .-")


def search_query(kind: str, ref: str) -> str:
    kind = (kind or "").strip()
    ref_s = _sanitize_stn_ref(kind, ref)
    if not kind or not ref_s:
        return ref_s
    m = re.search(
        rf"(?i)(?:{re.escape(kind)})\s+([\d\s.\-–—]+(?:-\d{{2,4}})?)",
        ref_s,
    )
    if m:
        num = _normalize_stn_number(m.group(1), kind)
        return _clean_stn_query(f"{kind} {num}")
    if ref_s.casefold().startswith(kind.casefold()):
        return _clean_stn_query(ref_s)
    return _clean_stn_query(f"{kind} {ref_s}")


def _extract_number_part(kind: str, ref: str) -> str:
    q = search_query(kind, ref)
    m = re.search(r"([\d][\d\s.\-–—]+(?:-\d{2,4})?)", q)
    return _normalize_stn_number(m.group(1), kind) if m else ""


def _core_digits(kind: str, ref: str) -> str:
    return re.sub(r"\D", "", _extract_number_part(kind, ref))


def _body_year_digits(digits: str) -> tuple[str, str]:
    """Разделить номер и год (2 или 4 цифры в конце)."""
    if len(digits) >= 6 and digits[-4:].isdigit() and 1900 <= int(digits[-4:]) <= 2039:
        return digits[:-4], digits[-4:]
    if len(digits) >= 3 and digits[-2:].isdigit():
        return digits[:-2], digits[-2:]
    return digits, ""


def _digits_compatible(target: str, candidate: str) -> bool:
    if not target or not candidate:
        return False
    if target == candidate:
        return True
    tb, ty = _body_year_digits(target)
    cb, cy = _body_year_digits(candidate)
    if ty and cy and ty != cy:
        return False
    if tb == cb:
        return True
    if len(tb) == len(cb) and sum(a != b for a, b in zip(tb, cb)) == 1:
        return True
    return False


_OCR_DIGIT_SWAPS: dict[str, str] = {
    "0": "68",
    "1": "7",
    "2": "9",
    "3": "8",
    "5": "6",
    "6": "580",
    "7": "1",
    "8": "63",
    "9": "2",
}
_OCR_PRIORITY_SWAPS: tuple[tuple[str, str], ...] = (
    ("2", "9"),
    ("9", "2"),
    ("6", "8"),
    ("8", "6"),
    ("0", "6"),
    ("5", "6"),
    ("1", "7"),
    ("3", "8"),
)


def _iter_ocr_digit_variants(kind: str, ref: str, *, limit: int = 20) -> list[str]:
    """Одна цифра OCR: 8962 → 8969 и т.п. — только для повторного поиска на STN."""
    num = _extract_number_part(kind, ref)
    if not num:
        return []
    base_ref = search_query(kind, ref)
    out: list[str] = []

    def _push_variant(variant_num: str) -> bool:
        variant_ref = search_query(kind, f"{kind} {variant_num}")
        if variant_ref and _norm_code(variant_ref) != _norm_code(base_ref):
            if variant_ref not in out:
                out.append(variant_ref)
        return len(out) >= limit

    for i, ch in enumerate(num):
        if not ch.isdigit():
            continue
        for src, alt in _OCR_PRIORITY_SWAPS:
            if ch != src:
                continue
            if _push_variant(_normalize_stn_number(num[:i] + alt + num[i + 1 :], kind)):
                return out
    for i, ch in enumerate(num):
        if not ch.isdigit():
            continue
        for alt in _OCR_DIGIT_SWAPS.get(ch, ""):
            if len(alt) != 1 or alt == ch:
                continue
            if _push_variant(_normalize_stn_number(num[:i] + alt + num[i + 1 :], kind)):
                return out
    for i, ch in enumerate(num):
        if not ch.isdigit():
            continue
        for alt in _OCR_DIGIT_SWAPS.get(ch, ""):
            if len(alt) == 1 or alt == ch:
                continue
            if _push_variant(_normalize_stn_number(num[:i] + alt + num[i + 1 :], kind)):
                return out
    return out


def search_queries(kind: str, ref: str) -> list[str]:
    """Несколько вариантов запроса — как в «быстром» и полном поиске на сайте."""
    kind = (kind or "").strip()
    full = search_query(kind, ref)
    num = _extract_number_part(kind, ref)
    compact = re.sub(r"\s+", "", num)
    dotted = num.replace(" ", "").replace("-", ".") if num else ""

    out: list[str] = []
    for q in (full, num, f"{kind} {num}".strip() if num else "", compact, dotted):
        q = _clean_stn_query(q)
        if q and q not in out:
            out.append(q)

    if num and "-" in num:
        parts = [p for p in num.replace(" ", "").split("-") if p]
        for end in range(len(parts), 1, -1):
            partial = "-".join(parts[:end])
            if len(partial) < 4:
                continue
            for q in (partial, f"{kind} {partial}".strip()):
                q = _clean_stn_query(q)
                if q and q not in out:
                    out.append(q)
    if kind == "ТКП" and num:
        # На сайте обозначение может быть с (02250); в быстром поиске — без.
        for q in (f"{kind} {num} (02250)", f"{num} (02250)"):
            q = _clean_stn_query(q)
            if q and q not in out:
                out.append(q)
    return out


def parse_card_html(html: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for m in _CARD_ROW.finditer(html or ""):
        key = unescape(m.group(1)).strip()
        val = unescape(re.sub(r"<[^>]+>", " ", m.group(2)))
        val = re.sub(r"\s+", " ", val).strip()
        fields[key] = val
    for m in _CARD_ROW_DIV.finditer(html or ""):
        key = unescape(m.group(1)).strip()
        if key in fields:
            continue
        val = unescape(re.sub(r"<[^>]+>", " ", m.group(2)))
        val = re.sub(r"\s+", " ", val).strip()
        fields[key] = val
    return fields


def parse_ru_date(raw: str) -> date | None:
    s = (raw or "").strip()
    if not s or s in ("—", "-", "нет", "не указано"):
        return None
    m = _DATE_RE.search(s)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def validity_status(
    intro: date | None,
    cancel: date | None,
    *,
    today: date | None = None,
) -> str:
    today = today or date.today()
    if intro and today < intro:
        return "не введён"
    if cancel and today >= cancel:
        return "отменён"
    if intro or cancel:
        return "актуален"
    return "неизвестно"


def _pick_best_match(kind: str, ref: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None

    target_full = _norm_code(search_query(kind, ref))
    target_digits = _core_digits(kind, ref)
    best: dict[str, Any] | None = None
    best_score = -999

    for row in rows:
        code = str(row.get("code") or "")
        name = str(row.get("name") or "")
        code_n = _norm_code(code)
        name_n = _norm_code(name)
        row_digits = re.sub(r"\D", "", code_n)
        name_digits = re.sub(r"\D", "", name_n)

        if target_digits and len(target_digits) >= 4:
            code_ok = _digits_compatible(target_digits, row_digits) or target_full in code_n
            name_ok = _digits_compatible(target_digits, name_digits) or target_full in name_n
            if not code_ok and not name_ok:
                continue

        score = 0
        if re.search(r"изменение", code, re.I):
            score -= 40
        if code_n == target_full:
            score += 120
        elif target_full and target_full in code_n:
            score += 105
        elif target_digits and _digits_compatible(target_digits, row_digits):
            score += 95
        elif target_digits and _digits_compatible(target_digits, name_digits):
            score += 75
        elif kind.casefold() in code_n:
            score += 35
        else:
            score += 15

        if score > best_score:
            best_score = score
            best = row

    return best if best_score >= 35 else None


def _parse_list_response(raw: str) -> tuple[list[dict[str, Any]], bool]:
    data = json.loads(raw)
    if not isinstance(data, list) or len(data) < 2 or not isinstance(data[1], list):
        return [], True
    session_ok = not (len(data) > 3 and data[3] not in (0, "0", None))
    return list(data[1]), session_ok


class StnClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        login: str = "",
        password: str = "",
        timeout_sec: int | None = None,
    ) -> None:
        self.base = (base_url or stn_base_url()).rstrip("/") + "/"
        self.timeout = timeout_sec if timeout_sec is not None else stn_timeout_sec()
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._jar))
        self._logged_in = False
        self._login_user = (login or "").strip()
        self._login_pass = (password or "").strip()
        self._http_lock = threading.RLock()

    @property
    def _portal_page(self) -> str:
        return "ips.php" if self._logged_in else "fond.php"

    @property
    def _list_endpoint(self) -> str:
        return "ips_list.php" if self._logged_in else "fond_list.php"

    @property
    def _card_endpoint(self) -> str:
        return "ips_card.php" if self._logged_in else "fond_card.php"

    def _open(self, req: urllib.request.Request, *, retries: int = 2):
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                with self._http_lock:
                    return self._opener.open(req, timeout=self.timeout)
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = e
                if attempt < retries:
                    time.sleep(0.6 * (attempt + 1))
                    continue
                raise
        if last_err:
            raise last_err
        raise RuntimeError("STN request failed")

    def _ensure_logged_in(self) -> None:
        if getattr(self, "_logged_in", False):
            return
        login_user = getattr(self, "_login_user", "")
        login_pass = getattr(self, "_login_pass", "")
        if not (login_user and login_pass):
            return
        with self._http_lock:
            if self._logged_in:
                return
            self.login(self._login_user, self._login_pass)

    def login(self, login: str, password: str) -> None:
        portal = self.base + "ips.php"
        with self._open(urllib.request.Request(portal, headers=self._headers())) as resp:
            resp.read()
        body = urllib.parse.urlencode({"login": login, "pass": password}).encode("utf-8")
        req = urllib.request.Request(
            self.base + "authorization.php",
            data=body,
            headers=self._headers(referer=portal),
            method="POST",
        )
        with self._open(req) as resp:
            resp.read()
        warm = urllib.request.Request(portal, headers=self._headers())
        with self._open(warm) as resp:
            resp.read()
        self._logged_in = True

    def _headers(self, *, referer: str | None = None) -> dict[str, str]:
        h = {
            "User-Agent": "Mozilla/5.0 (compatible; Belener/1.0; +local normative checker)",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        if referer:
            h["Referer"] = referer
        if self._logged_in:
            h["X-Requested-With"] = "XMLHttpRequest"
        return h

    def _search_form(self, **overrides: str) -> dict[str, str]:
        fields = {**_STN_FORM_EMPTY, **_STN_DOCTYPES}
        fields.update(overrides)
        return fields

    def _post_list(self, fields: dict[str, str]) -> list[dict[str, Any]]:
        self._ensure_logged_in()
        payload = dict(fields)
        payload.setdefault("page", "0")
        body = urllib.parse.urlencode(payload, encoding="utf-8").encode("utf-8")
        portal = self.base + self._portal_page
        req = urllib.request.Request(
            self.base + self._list_endpoint,
            data=body,
            headers=self._headers(referer=portal),
            method="POST",
        )
        with self._open(req) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        rows, session_ok = _parse_list_response(raw)
        if self._logged_in and not session_ok:
            log.warning("STN IPS session expired during search")
        return rows

    def search_quick(self, query: str) -> list[dict[str, Any]]:
        return self._post_list(self._search_form(codename=query, mode="true"))

    def search_quick_pages(
        self,
        query: str,
        *,
        max_pages: int = 4,
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for page in range(max_pages):
            rows = self._post_list(self._search_form(codename=query, mode="true", page=str(page)))
            if not rows:
                break
            for row in rows:
                merged[str(row.get("docid") or "")] = row
            if len(rows) < 50:
                break
        return [v for k, v in merged.items() if k]

    def search_full(self, query: str) -> list[dict[str, Any]]:
        return self._post_list(self._search_form(code=query, codename="", mode="false"))

    def search(self, query: str) -> list[dict[str, Any]]:
        return self.search_quick(query)

    def search_all(self, queries: list[str]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for raw_q in queries:
            q = _clean_stn_query(raw_q)
            if not q:
                continue
            for row in self.search_quick(q):
                merged[str(row.get("docid") or "")] = row
            for row in self.search_full(q):
                merged[str(row.get("docid") or "")] = row
        return [v for k, v in merged.items() if k]

    def search_escalated(
        self,
        kind: str,
        ref: str,
        queries: list[str],
        *,
        max_queries: int | None = None,
        deadline: float | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """Быстрый поиск: quick (+ doctypes), full — только если quick что-то нашёл."""
        limit = max_queries if max_queries is not None else stn_max_queries()
        if kind == "ТКП":
            limit = min(max(limit, 3), 4)
        tried: list[str] = []
        rows: list[dict[str, Any]] = []
        for raw_q in queries[:limit]:
            if deadline is not None and time.monotonic() >= deadline:
                break
            q = _clean_stn_query(raw_q)
            if not q or q in tried:
                continue
            tried.append(q)
            max_pages = 1 if kind != "ТКП" else 2
            quick_rows = self.search_quick_pages(q, max_pages=max_pages)
            if quick_rows:
                rows.extend(quick_rows)
                match = _pick_best_match(kind, ref, rows)
                if match:
                    return match, "; ".join(tried[:4])
                if deadline is not None and time.monotonic() >= deadline:
                    break
                full_rows = self.search_full(q)
                if full_rows:
                    rows.extend(full_rows)
                    match = _pick_best_match(kind, ref, rows)
                    if match:
                        return match, "; ".join(tried[:4])
        return None, "; ".join(tried[:4])

    def fetch_card(self, doc_id: str) -> str:
        self._ensure_logged_in()
        body = urllib.parse.urlencode({"id": doc_id}).encode("utf-8")
        portal = self.base + self._portal_page
        req = urllib.request.Request(
            self.base + self._card_endpoint,
            data=body,
            headers=self._headers(referer=portal),
            method="POST",
        )
        with self._open(req) as resp:
            return resp.read().decode("utf-8", errors="replace")


def _lookup_match(
    kind: str,
    ref: str,
    *,
    client: StnClient,
    queries: list[str],
    deadline: float | None = None,
) -> tuple[dict[str, Any] | None, str]:
    if hasattr(client, "search_escalated"):
        return client.search_escalated(kind, ref, queries, deadline=deadline)
    rows = client.search_all(queries)
    match = _pick_best_match(kind, ref, rows)
    return match, "; ".join(queries[:4])


def lookup_one(
    kind: str,
    ref: str,
    *,
    client: StnClient | None = None,
    today: date | None = None,
    deadline: float | None = None,
) -> StnCheckResult:
    kind = (kind or "").strip()
    ref = (ref or "").strip()
    sheet_ref = ref
    queries = search_queries(kind, ref)
    query = queries[0] if queries else search_query(kind, ref)
    out = StnCheckResult(kind=kind, ref=sheet_ref, query=query, found=False)

    if not is_stn_checkable(kind):
        out.status = "не в фонде STN"
        out.query = ""
        return out

    cli = client or _default_client()
    t0 = time.monotonic()
    try:
        if deadline is not None and time.monotonic() >= deadline:
            out.status = "пропущено (бюджет времени)"
            return out
        cli._ensure_logged_in()
        match, used_q = _lookup_match(kind, ref, client=cli, queries=queries, deadline=deadline)

        if not match:
            if getattr(cli, "_logged_in", False):
                out.status = "нет в ИПС"
            else:
                out.status = "нет в открытом фонде (нужен вход IPS)"
            out.query = used_q
            return out

        doc_id = str(match.get("docid") or "")
        card_html = cli.fetch_card(doc_id)
        fields = parse_card_html(card_html)
        intro = parse_ru_date(fields.get("Дата введения", ""))
        cancel = parse_ru_date(fields.get("Дата отмены", ""))

        stn_code = fields.get("Обозначение") or str(match.get("code") or "")
        out.found = True
        out.doc_id = doc_id
        out.stn_code = stn_code
        out.stn_name = fields.get("Наименование") or str(match.get("name") or "")
        out.intro_date = fields.get("Дата введения", "")
        out.cancel_date = fields.get("Дата отмены", "")
        out.status = validity_status(intro, cancel, today=today)
        out.query = used_q
        if _norm_code(stn_code) != _norm_code(sheet_ref):
            out.ref = sheet_ref
        log.info("STN lookup %s %s -> %s in %.1fs", kind, ref, out.status, time.monotonic() - t0)
        return out
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        log.warning("STN lookup failed kind=%s ref=%s: %s", kind, ref, e)
        out.error = str(e)
        if "timed out" in str(e).casefold():
            out.error = "таймаут normy.stn.by"
        out.status = "ошибка проверки"
        log.info("STN lookup %s %s -> error in %.1fs", kind, ref, time.monotonic() - t0)
        return out


def refine_and_check_normative_refs(
    refs: list[dict[str, str]],
    *,
    client: StnClient | None = None,
    today: date | None = None,
    deadline: float | None = None,
) -> tuple[list[dict[str, str]], list[StnCheckResult]]:
    """Проверка нормативов на STN; обозначения с листа не меняются."""
    if not stn_lookup_enabled():
        return list(refs or []), []

    checkable_refs = [
        dict(item)
        for item in (refs or [])
        if str(item.get("kind") or "").strip() and str(item.get("ref") or "").strip()
    ]

    t0 = time.monotonic()
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in checkable_refs:
        kind = str(item.get("kind") or "").strip()
        ref = str(item.get("ref") or "").strip()
        key = (kind.casefold(), _norm_code(search_query(kind, ref)))
        if key in seen:
            continue
        seen.add(key)
        items.append(dict(item))
    max_refs = stn_max_refs()
    skipped = 0
    if len(items) > max_refs:
        skipped = len(items) - max_refs
        items = items[:max_refs]

    if not items:
        return list(refs or []), []

    shared_cli = client or _default_client()
    workers = min(stn_parallel_workers(), len(items))
    refined_map: dict[tuple[str, str], dict[str, str]] = {}
    checks_map: dict[tuple[str, str], StnCheckResult] = {}
    lock = threading.Lock()

    def _run_one(item: dict[str, str]) -> tuple[tuple[str, str], dict[str, str], StnCheckResult]:
        kind = str(item.get("kind") or "").strip()
        ref = str(item.get("ref") or "").strip()
        key = (kind.casefold(), _norm_code(search_query(kind, ref)))
        if deadline is not None and time.monotonic() >= deadline:
            check = StnCheckResult(
                kind=kind,
                ref=ref,
                query=search_query(kind, ref),
                found=False,
                status="пропущено (бюджет времени)",
            )
            return key, dict(item), check
        try:
            check = lookup_one(kind, ref, client=shared_cli, today=today, deadline=deadline)
        except (urllib.error.URLError, TimeoutError) as e:
            log.warning("STN lookup crashed kind=%s ref=%s: %s", kind, ref, e)
            check = StnCheckResult(
                kind=kind,
                ref=ref,
                query=search_query(kind, ref),
                found=False,
                status="ошибка проверки",
                error=str(e),
            )
        return key, dict(item), check

    if workers <= 1:
        for item in items:
            key, out_item, check = _run_one(item)
            refined_map[key] = out_item
            checks_map[key] = check
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_run_one, item) for item in items]
            for fut in as_completed(futures):
                try:
                    key, out_item, check = fut.result()
                except Exception as e:
                    log.warning("STN worker failed: %s", e)
                    continue
                with lock:
                    refined_map[key] = out_item
                    checks_map[key] = check

    refined: list[dict[str, str]] = []
    checks: list[StnCheckResult] = []
    seen_out: set[tuple[str, str]] = set()
    for item in items:
        kind = str(item.get("kind") or "").strip()
        ref = str(item.get("ref") or "").strip()
        key = (kind.casefold(), _norm_code(search_query(kind, ref)))
        if key in seen_out:
            continue
        seen_out.add(key)
        out_item = refined_map.get(key)
        check = checks_map.get(key)
        if out_item is None or check is None:
            continue
        refined.append(out_item)
        checks.append(check)

    log.info(
        "STN batch: %s refs in %.1fs (%s workers, skipped=%s)",
        len(checks),
        time.monotonic() - t0,
        workers,
        skipped,
    )
    return list(refs or []), checks


_client: StnClient | None = None
_client_lock = threading.Lock()


def _default_client() -> StnClient:
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            _client = StnClient(
                login=stn_login(),
                password=stn_password(),
            )
        return _client


def check_normative_refs_stn(
    refs: list[dict[str, str]],
    *,
    client: StnClient | None = None,
    today: date | None = None,
) -> list[StnCheckResult]:
    """Проверить на normy.stn.by каждую уникальную ссылку из списка OCR."""
    _, checks = refine_and_check_normative_refs(refs, client=client, today=today)
    return checks


def stn_checks_to_markdown(
    checks: list[StnCheckResult],
    *,
    check_date: date | None = None,
) -> list[str]:
    if not checks:
        return []
    lines = [
        "## Проверка нормативов на normy.stn.by",
        "",
    ]
    if check_date:
        lines.append(f"*Дата проверки актуальности: {check_date.strftime('%d.%m.%Y')}*")
        lines.append("")
    found_checks = [c for c in checks if c.found]
    if not found_checks:
        lines.append("*В ИПС normy.stn.by не найдено ни одного документа из списка.*")
        lines.append("")
    else:
        lines.extend([
            "| Тип | Обозначение (лист) | Дата введения | Дата отмены | Статус |",
            "| --- | --- | --- | --- | --- |",
        ])
        for c in found_checks:
            intro = c.intro_date or "—"
            cancel = c.cancel_date or "—"
            status = c.status or "—"
            if c.error and c.status == "ошибка проверки":
                status = f"{status} ({c.error[:60]})"
            designation = c.ref
            lines.append(f"| {c.kind} | {designation} | {intro} | {cancel} | {status} |")
        lines.append("")
    checked = len(checks)
    found = len(found_checks)
    active = sum(1 for c in found_checks if c.status == "актуален")
    lines.append(
        f"*Проверено на листе: {checked}; найдено в ИПС: {found}; актуально: {active}*"
    )
    lines.append("")
    return lines
