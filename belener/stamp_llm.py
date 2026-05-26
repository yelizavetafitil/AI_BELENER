"""Локальное уточнение штампа: LLM только по OCR-тексту (без vision)."""

from __future__ import annotations

import json
import re
from typing import Any

import ollama

from belener.config import ollama_host, stamp_llm_enabled, stamp_llm_model
from belener.parse import STAMP_SIGNATURE_ORDER, _dedupe_titles, _norm_signature_role, normalize_signatures

_STAMP_KV_FIELDS: tuple[str, ...] = (
    "Обозначение / шифр",
    "Организация",
    "Город / адрес",
    "Масштаб",
    "Формат",
    "Стадия (обозначение)",
    "Стадия",
    "Очередь строительства",
    "Лист",
    "Копировал",
)

_FIELD_ALIASES: dict[str, str] = {
    "шифр": "Обозначение / шифр",
    "обозначение": "Обозначение / шифр",
    "обозначение / шифр": "Обозначение / шифр",
    "организация": "Организация",
    "город": "Город / адрес",
    "город / адрес": "Город / адрес",
    "адрес": "Город / адрес",
    "масштаб": "Масштаб",
    "формат": "Формат",
    "стадия": "Стадия",
    "стадия (обозначение)": "Стадия (обозначение)",
    "очередь": "Очередь строительства",
    "очередь строительства": "Очередь строительства",
    "лист": "Лист",
    "копировал": "Копировал",
}

_SYSTEM = """Ты редактор текста основной надписи ГОСТ инженерного чертежа.
Вход — сырой OCR-текст рамки штампа. Ответ — только JSON.

Правила:
1) Только факты из OCR. Не выдумывай фамилии, даты, шифры.
2) Исправь OCR-опечатки, не меняя смысл.
3) Нет данных → "—"
4) kv.field — ТОЛЬКО из списка:
   "Обозначение / шифр", "Организация", "Город / адрес", "Масштаб", "Формат",
   "Стадия (обозначение)", "Стадия", "Очередь строительства", "Лист", "Копировал"
5) Масштаб = 1:500, 1:1000 и т.п. Формат = A4x4, A3 и т.п. НЕ ПУТАЙ их.
6) signatures — все видимые строки подписей (роль, фамилия, дата); пустые не добавляй.
7) Дата подписи: формат 11.25, 01.12 и т.п. если есть в OCR.
8) titles — наименования разделов (Генеральный план, План благоустройства, …), без | и ролей.
9) summary — 2–4 предложения только из фактов OCR: шифр, организация, очередь, разделы. Не указывай масштаб/лист, если их нет в OCR.

Формат:
{
  "kv": [{"field": "...", "value": "..."}],
  "signatures": [{"role": "...", "name": "...", "date": "..."}],
  "titles": ["..."],
  "summary": "..."
}"""


def _installed_models(client: ollama.Client) -> list[str]:
    try:
        resp = client.list()
        models = getattr(resp, "models", None) or []
        out: list[str] = []
        for m in models:
            name = getattr(m, "model", None) or (m.get("name") if isinstance(m, dict) else None)
            if name:
                out.append(str(name))
        return out
    except Exception:
        return []


def _pick_model(client: ollama.Client) -> str:
    preferred = stamp_llm_model()
    installed = _installed_models(client)
    if not installed:
        return preferred
    if preferred in installed:
        return preferred
    for m in installed:
        if preferred.split(":")[0] in m:
            return m
    for m in installed:
        low = m.lower()
        if "vl" in low or "vision" in low:
            continue
        if any(x in low for x in ("gemma", "llama", "qwen", "mistral", "phi")):
            return m
    return installed[0]


def _norm_field(raw: str) -> str:
    s = re.sub(r"\s+", " ", (raw or "").strip())
    if not s:
        return ""
    if s in _STAMP_KV_FIELDS:
        return s
    key = s.casefold()
    return _FIELD_ALIASES.get(key, "")


def _clean_value(raw: str) -> str:
    v = re.sub(r"\s+", " ", (raw or "").strip())
    if not v or v in ("—", "-", "–"):
        return ""
    if re.fullmatch(r"[\d.\-©|_\[\]()]+", v):
        return ""
    return v


def _validate_kv_field(field: str, value: str) -> str:
    v = re.sub(r"\s+", " ", (value or "").strip())
    if not v or v == "—":
        return ""
    if field == "Стадия (обозначение)":
        m = re.search(r"\b([СРПТ])\b", v, re.I)
        if m:
            return m.group(1).upper()
        if len(v) <= 2 and re.fullmatch(r"[СРПТ]", v, re.I):
            return v.upper()
        return ""
    if field == "Масштаб":
        m = re.search(r"1\s*:\s*(\d+)", v)
        if not m:
            return ""
        return f"1:{m.group(1)}"
    if field == "Формат":
        if re.search(r"1\s*:", v):
            return ""
    if field == "Очередь строительства":
        if re.search(r"^(генерал|подготов|план\b|реконструк|благоустр)", v, re.I):
            return ""
    if field == "Стадия":
        if re.search(r"^очеред", v, re.I):
            return ""
    if field in ("Организация", "Город / адрес") and re.search(r"разраб|пров\.|гип\b", v, re.I):
        return ""
    return v


def _is_garbage_kv(field: str, value: str) -> bool:
    if not field or not value:
        return True
    if field.casefold() == value.casefold():
        return True
    if re.match(r"^формат\b", value, re.I) and field != "Формат":
        return True
    if field == "Масштаб" and not re.search(r"1\s*:\s*\d", value):
        return True
    if field == "Формат" and re.search(r"1\s*:\s*\d", value):
        return True
    return False


def _clean_title(raw: str) -> str:
    t = re.sub(r"\s+", " ", (raw or "").strip())
    if len(t) < 8 or "|" in t:
        return ""
    if re.search(r"^(?:разро|пров|гип|н\.?\s*контр|нач\.|копиров|утв)", t, re.I):
        return ""
    if re.search(r"\.[_]|разраб\.|_\s", t, re.I):
        return ""
    if re.search(r"ру\s*[\"«]|ооо\s*[\"«]", t, re.I):
        return ""
    if sum(1 for w in t.split() if len(w) <= 2) >= 3:
        return ""
    return t


def _clean_name(raw: str) -> str:
    s = re.sub(r"[\[\]_`©|]+", " ", (raw or "").strip()).strip(" .-")
    if len(s) < 4 or re.search(r"\d{3,}", s):
        return ""
    if re.search(r"копир|копор|формат\b", s, re.I):
        return ""
    m = re.search(r"([А-ЯЁ][а-яё]{3,})", s)
    return m.group(1) if m else ""


def _norm_date(raw: str) -> str:
    s = (raw or "").strip()
    if not s or s == "—":
        return ""
    m = re.search(r"(\d{1,2})[./](\d{1,2})", s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12 and b > 12:
            return ""
        if b > 31:
            return ""
        return f"{m.group(1)}.{m.group(2)}"
    m = re.search(r"(\d{2})(\d{2})", s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12 and b > 12:
            return ""
        return f"{m.group(1)}.{m.group(2)}"
    return ""


def is_bad_signature_date(raw: str) -> bool:
    s = (raw or "").strip()
    if not s or s == "—":
        return False
    nd = _norm_date(s)
    return not nd


def sanitize_llm_stamp(raw: dict[str, Any]) -> dict[str, Any]:
    kv_out: dict[str, str] = {}
    for item in raw.get("kv") or []:
        if not isinstance(item, dict):
            continue
        field = _norm_field(str(item.get("field") or ""))
        val = _validate_kv_field(field, _clean_value(str(item.get("value") or "")))
        if not field or not val or _is_garbage_kv(field, val):
            continue
        prev = kv_out.get(field, "")
        if not prev or len(val) > len(prev):
            kv_out[field] = val

    sig_out: list[dict[str, str]] = []
    seen_roles: set[str] = set()
    for item in raw.get("signatures") or []:
        if not isinstance(item, dict):
            continue
        role = _norm_signature_role(str(item.get("role") or ""))
        if not role or role in seen_roles:
            continue
        seen_roles.add(role)
        name = _clean_name(str(item.get("name") or "")) or "—"
        date = _norm_date(str(item.get("date") or "")) or "—"
        if is_bad_signature_date(date):
            date = "—"
        if name == "—" and date == "—":
            continue
        sig_out.append({"role": role, "name": name, "sign": "—", "date": date})

    titles: list[str] = []
    seen_t: set[str] = set()
    for raw_t in raw.get("titles") or []:
        if not isinstance(raw_t, str):
            continue
        t = _clean_title(raw_t)
        if not t or t.casefold() in seen_t:
            continue
        seen_t.add(t.casefold())
        titles.append(t)

    summary = re.sub(r"\s+", " ", str(raw.get("summary") or "").strip())
    if len(summary) < 20:
        summary = ""
    elif re.search(r"масштаб\s+\d", summary, re.I) and "Масштаб" not in kv_out:
        summary = re.sub(r"\s*масштаб\s+[^.]+\.?", "", summary, flags=re.I).strip()

    return {
        "kv": kv_out,
        "signatures": normalize_signatures(sig_out),
        "titles": _dedupe_titles(titles),
        "summary": summary,
    }


def refine_stamp_llm(ocr_text: str) -> dict[str, Any] | None:
    if not stamp_llm_enabled():
        return None
    text = (ocr_text or "").strip()
    if not text:
        return None
    try:
        client = ollama.Client(host=ollama_host())
        model = _pick_model(client)
        resp = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": f"OCR рамки штампа:\n\n{text[:10000]}"},
            ],
            format="json",
            options={"temperature": 0, "num_predict": 1600},
        )
        raw = (resp.get("message") or {}).get("content") or ""
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        clean = sanitize_llm_stamp(data)
        if not clean.get("kv") and not clean.get("signatures") and not clean.get("titles") and not clean.get("summary"):
            return None
        return clean
    except Exception:
        return None
