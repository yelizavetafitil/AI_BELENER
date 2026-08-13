"""Проверка нормативов на tnpa.by (Национальный фонд ТНПА)."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

from belener.config import (
    stn_lookup_enabled,
    stn_max_refs,
    tnpa_max_queries,
    tnpa_parallel_workers,
    tnpa_timeout_sec,
)
from belener.stn_lookup import (
    StnCheckResult,
    _clean_stn_query,
    _core_digits,
    _digits_compatible,
    _norm_code,
    is_stn_checkable,
    search_query,
    validity_status,
)

log = logging.getLogger("belener.tnpa_lookup")


def tnpa_base_url() -> str:
    try:
        from belener.integration_store import get_tnpa_credentials

        creds = get_tnpa_credentials()
        if creds.get("base_url"):
            return creds["base_url"].rstrip("/")
    except Exception:
        pass
    return (os.environ.get("PDF_TNPA_BASE_URL") or "https://tnpa.by").strip().rstrip("/")


class TnpaClient:
    def __init__(self, base_url: str | None = None, *, timeout_sec: int | None = None) -> None:
        self.base = (base_url or tnpa_base_url()).rstrip("/")
        self.timeout = timeout_sec if timeout_sec is not None else tnpa_timeout_sec()
        self._cache: dict[tuple[str, int, int], list[dict]] = {}
        self._cache_lock = threading.Lock()

    def search_docs(self, query: str, *, page: int = 1, per_page: int = 30) -> list[dict]:
        q = (query or "").strip()
        cache_key = (q.upper(), page, per_page)
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return [dict(x) for x in cached]

        params = urllib.parse.urlencode(
            {
                "page": page,
                "per-page": per_page,
                "sort": "b.KL",
                "SearchParam": q.upper(),
                "lang": "ru",
                "stateID": -1,
                "onlyActive": "null",
            }
        )
        req = urllib.request.Request(
            f"{self.base}/api/tnpadocs?{params}",
            headers={
                "Accept": "application/json, text/plain, */*",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Referer": f"{self.base}/",
            },
        )
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
                rows: list[dict] = []
                if isinstance(data, list):
                    rows = [dict(x) for x in data if isinstance(x, dict)]
                elif isinstance(data, dict):
                    for key in ("items", "data", "models"):
                        if isinstance(data.get(key), list):
                            rows = [dict(x) for x in data[key] if isinstance(x, dict)]
                            break
                # Пустой ответ не кэшируем: tnpa.by иногда отдаёт [] при перегрузке.
                if rows:
                    with self._cache_lock:
                        self._cache[cache_key] = [dict(x) for x in rows]
                return rows
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
                last_err = e
                msg = str(e).casefold()
                retryable = any(
                    x in msg
                    for x in (
                        "timed out",
                        "timeout",
                        "temporarily",
                        "reset",
                        "refused",
                        "unreachable",
                        "ssl",
                        "eof",
                        "503",
                        "502",
                        "429",
                    )
                )
                if attempt < 2 and retryable:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise
        if last_err is not None:
            raise last_err
        return []


def _tnpa_search_queries(kind: str, ref: str) -> list[str]:
    """Короткий приоритетный список запросов для tnpa.by (без лишних OCR-вариантов)."""
    from belener.stn_lookup import _extract_number_part

    kind = (kind or "").strip()
    full = search_query(kind, ref)
    num = _extract_number_part(kind, ref)
    out: list[str] = []
    for q in (full, num, f"{kind} {num}".strip() if num else ""):
        q = _clean_stn_query(q)
        if q and q not in out:
            out.append(q)
    return out[: tnpa_max_queries()]


def _parse_tnpa_date(raw: object) -> date | None:
    s = str(raw or "").strip()
    if not s or s.lower() in ("null", "none"):
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y"):
        try:
            return datetime.strptime(s[:10] if fmt.startswith("%Y") else s, fmt).date()
        except ValueError:
            continue
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def _format_tnpa_date(raw: object) -> str:
    d = _parse_tnpa_date(raw)
    return d.strftime("%d.%m.%Y") if d else ""


def _tnpa_designation(row: dict) -> str:
    parts = [
        str(row.get("Number") or "").strip(),
        str(row.get("OND") or "").strip(),
        str(row.get("OND1") or "").strip(),
    ]
    if not any(parts):
        parts = [str(row.get("NumRes") or "").strip()]
    return " ".join(p for p in parts if p).strip()


def _pick_best_tnpa_match(kind: str, ref: str, rows: list[dict]) -> dict | None:
    if not rows:
        return None
    target_full = _norm_code(search_query(kind, ref))
    target_digits = _core_digits(kind, ref)
    if len(rows) == 1:
        row = rows[0]
        code_n = _norm_code(_tnpa_designation(row))
        row_digits = re.sub(r"\D", "", code_n)
        if code_n == target_full or (
            target_digits and len(target_digits) >= 4
            and (_digits_compatible(target_digits, row_digits) or target_full in code_n)
        ):
            return row
    best: dict | None = None
    best_score = -999
    for row in rows:
        code = _tnpa_designation(row)
        name = str(row.get("NND") or "")
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
        if code_n == target_full or (
            target_digits
            and row_digits == target_digits
            and (kind.casefold() in code_n or not kind)
        ):
            score += 100
        elif target_full and (target_full in code_n or code_n in target_full):
            score += 80
        elif target_digits and target_digits in row_digits:
            score += 60
        elif target_full and target_full in name_n:
            score += 40
        if score > best_score:
            best_score = score
            best = row
    # Порог ниже, чем в STN, потому что формат номера на tnpa.by
    # может отличаться (доп. пробелы/дефисы/части обозначения).
    # При этом фильтр по digits/совпадению остаётся строгим.
    return best if best_score >= 20 else None


def _tnpa_cancel_raw(row: dict) -> object:
    """Дата отмены/окончания действия на tnpa.by — только DTTK.

    DSMSOS нельзя брать как отмену: у действующих («Взамен» и др.)
    оно часто равно DTTN (дате введения), из‑за чего в таблице
    появлялось «Отменен = Введен».
    """
    return row.get("DTTK")


def _tnpa_status(row: dict, *, today: date | None = None) -> str:
    intro = _parse_tnpa_date(row.get("DTTN"))
    cancel = _parse_tnpa_date(_tnpa_cancel_raw(row))

    # PRIZN_BD=0 — отменён в фонде; без DTTK не подставляем фейковую дату.
    if str(row.get("PRIZN_BD") or "").strip() == "0":
        if cancel is not None:
            return validity_status(intro, cancel, today=today)
        return "отменён"

    return validity_status(intro, cancel, today=today)


def lookup_one_tnpa(
    kind: str,
    ref: str,
    *,
    client: TnpaClient | None = None,
    today: date | None = None,
    deadline: float | None = None,
) -> StnCheckResult:
    kind = (kind or "").strip()
    ref = (ref or "").strip()
    sheet_ref = ref
    queries = _tnpa_search_queries(kind, ref)
    query = queries[0] if queries else search_query(kind, ref)
    out = StnCheckResult(kind=kind, ref=sheet_ref, query=query, found=False)

    if not is_stn_checkable(kind):
        out.status = "не в фонде ТНПА"
        out.query = ""
        return out

    cli = client or _default_client()
    t0 = time.monotonic()
    skipped_budget = False
    try:
        if deadline is not None and time.monotonic() >= deadline:
            out.status = "пропущено (бюджет времени)"
            return out
        tried: list[str] = []
        match: dict | None = None
        for raw_q in queries:
            if deadline is not None and time.monotonic() >= deadline:
                skipped_budget = True
                break
            q = _clean_stn_query(raw_q)
            if not q or q in tried:
                continue
            tried.append(q)
            rows = cli.search_docs(q)
            match = _pick_best_tnpa_match(kind, ref, rows)
            if match:
                break
        if not match:
            out.query = "; ".join(tried[:4])
            out.status = "пропущено (бюджет времени)" if skipped_budget else "нет в ТНПА"
            return out

        rn = str(match.get("RN") or "")
        idglobal = str(match.get("IDGLOBAL") or "")
        code = _tnpa_designation(match)
        out.found = True
        out.doc_id = f"{rn}/{idglobal}" if rn and idglobal else idglobal or rn
        out.stn_code = code
        out.stn_name = str(match.get("NND") or "")
        out.intro_date = _format_tnpa_date(match.get("DTTN"))
        out.cancel_date = _format_tnpa_date(_tnpa_cancel_raw(match))
        out.status = _tnpa_status(match, today=today)
        out.query = "; ".join(tried[:4])
        log.info("TNPA lookup %s %s -> %s in %.1fs", kind, ref, out.status, time.monotonic() - t0)
        return out
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        log.warning("TNPA lookup failed kind=%s ref=%s: %s", kind, ref, e)
        out.error = str(e)
        if "timed out" in str(e).casefold():
            out.error = "таймаут tnpa.by"
        out.status = "ошибка проверки"
        return out


def refine_and_check_normative_refs_tnpa(
    refs: list[dict[str, str]],
    *,
    client: TnpaClient | None = None,
    today: date | None = None,
    deadline: float | None = None,
) -> tuple[list[dict[str, str]], list[StnCheckResult]]:
    if not stn_lookup_enabled():
        return list(refs or []), []

    checkable_refs = [
        dict(item)
        for item in (refs or [])
        if str(item.get("kind") or "").strip() and str(item.get("ref") or "").strip()
    ]
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
    if len(items) > max_refs:
        items = items[:max_refs]
    if not items:
        return list(refs or []), []

    # Параллельные клиенты + общий кэш на shared client при workers=1
    workers = min(tnpa_parallel_workers(), len(items))
    checks: list[StnCheckResult] = []
    shared_cli = client or TnpaClient()

    def _run_one(item: dict[str, str]) -> StnCheckResult:
        return lookup_one_tnpa(
            str(item.get("kind") or ""),
            str(item.get("ref") or ""),
            client=shared_cli,
            today=today,
            deadline=deadline,
        )

    if workers <= 1:
        for item in items:
            checks.append(_run_one(item))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_run_one, item): item for item in items}
            for fut in as_completed(futs):
                try:
                    checks.append(fut.result())
                except Exception as e:
                    item = futs[fut]
                    checks.append(
                        StnCheckResult(
                            kind=str(item.get("kind") or ""),
                            ref=str(item.get("ref") or ""),
                            query=search_query(str(item.get("kind") or ""), str(item.get("ref") or "")),
                            found=False,
                            status="ошибка проверки",
                            error=str(e),
                        )
                    )

    # Второй проход: таймауты/бюджет на медленном сервере не должны давать «нет в ТНПА»
    retry_idx = [
        i
        for i, c in enumerate(checks)
        if not c.found
        and (
            (c.status or "").startswith("пропущено")
            or c.status == "ошибка проверки"
        )
    ]
    if retry_idx:
        log.warning("TNPA retry %s refs after timeouts/budget", len(retry_idx))
        retry_deadline = time.monotonic() + min(180.0, 25.0 * len(retry_idx))
        for i in retry_idx:
            item = {"kind": checks[i].kind, "ref": checks[i].ref}
            again = lookup_one_tnpa(
                str(item.get("kind") or ""),
                str(item.get("ref") or ""),
                client=shared_cli,
                today=today,
                deadline=retry_deadline,
            )
            checks[i] = again
    return list(refs or []), checks


_client: TnpaClient | None = None
_client_lock = threading.Lock()


def reset_tnpa_client() -> None:
    global _client
    with _client_lock:
        _client = None


def _default_client() -> TnpaClient:
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            _client = TnpaClient()
        return _client


def test_tnpa_connection(base_url: str | None = None) -> tuple[bool, str]:
    cli = TnpaClient(base_url=base_url)
    try:
        rows = cli.search_docs("10704-91", per_page=3)
        if rows:
            return True, "Поиск на tnpa.by выполнен успешно"
        return True, "Сайт отвечает, документы по тестовому запросу не найдены"
    except Exception as e:
        return False, str(e)
