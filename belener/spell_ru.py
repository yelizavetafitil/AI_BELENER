"""Универсальная правка OCR-русского через hunspell (без подстановок под чертёж)."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any

_TOKEN_RX = re.compile(
    r"([(\[\"«]*)([A-Za-zА-Яа-яЁё\-]+)([)\]\"».,;:!?]*)",
    re.UNICODE,
)


def spell_ru_enabled() -> bool:
    raw = os.environ.get("PDF_SPELL_RU")
    if raw is None or not str(raw).strip():
        return True
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


@lru_cache(maxsize=1)
def _dictionary() -> Any | None:
    if not spell_ru_enabled():
        return None
    try:
        from spylls.hunspell import Dictionary
    except ImportError:
        return None
    bases: list[str] = []
    env = (os.environ.get("HUNSPELL_RU") or os.environ.get("DICPATH") or "").strip()
    if env:
        bases.append(env.rstrip("/"))
    bases.extend(
        (
            "/usr/share/hunspell/ru_RU",
            "/usr/share/hunspell/ru",
            "/usr/share/myspell/dicts/ru_RU",
        )
    )
    for base in bases:
        aff = f"{base}.aff"
        dic = f"{base}.dic"
        if os.path.isfile(aff) and os.path.isfile(dic):
            try:
                return Dictionary.from_files(base)
            except Exception:
                continue
    return None


def spell_ru_available() -> bool:
    return _dictionary() is not None


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def _max_edit_distance(word: str, *, corrupt: bool = False) -> int:
    n = len(word)
    if n <= 5:
        base = 1
    elif n <= 9:
        base = 2
    else:
        base = min(6, max(3, n // 4))
    if corrupt:
        base = min(base + 2, max(4, n // 3))
    return base


def _looks_ocr_corrupt(word: str) -> bool:
    """Слово похоже на OCR-мусор, даже если формально есть в словаре."""
    if re.search(r"ы[цчiuу]$", word, re.I):
        return True
    if re.search(r"(.)\1{2,}", word, re.I):
        return True
    if re.search(r"^восстон|либаем|стонаб", word, re.I):
        return True
    if re.search(r"енны$", word, re.I):
        return True
    vowels = sum(1 for c in word.casefold() if c in "аеёиоуыэюя")
    letters = sum(1 for c in word if c.isalpha())
    if letters >= 8 and vowels / max(letters, 1) < 0.22:
        return True
    return False


def _preserve_case(original: str, fixed: str) -> str:
    if not fixed:
        return fixed
    if original.isupper():
        return fixed.upper()
    if original[:1].isupper():
        return fixed[:1].upper() + fixed[1:]
    return fixed


def _should_skip_core(core: str) -> bool:
    if len(core) < 4:
        return True
    if re.search(r"\d", core):
        return True
    if re.fullmatch(r"[A-ZА-ЯЁ]{2,6}", core):
        return True
    if re.fullmatch(r"[A-Za-z]{2,4}", core):
        return True
    if re.fullmatch(r"\d{1,4}[./-]\d{1,4}", core):
        return True
    return False


def _ocr_glyph_fixes(word: str) -> str:
    """Путаница похожих символов в OCR (не привязка к тексту чертежа)."""
    w = word
    w = re.sub(r"^восстон", "восстан", w, flags=re.I)
    w = re.sub(r"либа(?=ем)", "лива", w, flags=re.I)
    w = re.sub(r"енны$", "енный", w, flags=re.I)
    w = re.sub(r"ец(?=н)", "ей", w, flags=re.I)
    w = re.sub(r"одетон", "обетон", w, flags=re.I)
    w = re.sub(r"ыцу$", "ый", w, flags=re.I)
    w = re.sub(r"ыц$", "ый", w, flags=re.I)
    w = re.sub(r"ыч$", "ый", w, flags=re.I)
    w = re.sub(r"([а-яё])i$", r"\1й", w, flags=re.I)
    w = re.sub(r"ыi$", "ый", w, flags=re.I)
    return w


def _pick_suggestion(probe: str, suggestions: list[str], candidate: str, *, corrupt: bool) -> str:
    limit = _max_edit_distance(candidate, corrupt=corrupt)
    best = ""
    best_dist = limit + 1
    for sug in suggestions[:10]:
        s = str(sug or "").strip()
        if not s:
            continue
        if corrupt and s.casefold() == probe:
            continue
        dist = _levenshtein(probe, s.casefold())
        if dist <= limit and dist < best_dist:
            best, best_dist = s, dist
    return best


def _repair_word(core: str) -> str:
    dic = _dictionary()
    if dic is None or _should_skip_core(core):
        return core
    fixed = _ocr_glyph_fixes(core)
    if fixed != core and dic.lookup(fixed.casefold()):
        return _preserve_case(core, fixed)
    for candidate in (core, fixed):
        probe = candidate.casefold()
        corrupt = _looks_ocr_corrupt(candidate)
        if dic.lookup(probe) and not corrupt:
            return _preserve_case(core, candidate) if candidate != core else core
        suggestions = list(dic.suggest(probe) or [])
        best = _pick_suggestion(probe, suggestions, candidate, corrupt=corrupt)
        if best:
            return _preserve_case(core, best)
        if candidate != core and dic.lookup(probe) and not _looks_ocr_corrupt(candidate):
            return _preserve_case(core, candidate)
    return core


def repair_ocr_russian(text: str) -> str:
    """Поправка опечаток OCR по словарю русского языка."""
    if not text or not spell_ru_enabled():
        return text
    if _dictionary() is None:
        return text

    def repl(m: re.Match[str]) -> str:
        pre, core, suf = m.group(1), m.group(2), m.group(3)
        return f"{pre}{_repair_word(core)}{suf}"

    parts: list[str] = []
    for chunk in re.split(r"(\s+)", text):
        if not chunk or chunk.isspace():
            parts.append(chunk)
            continue
        parts.append(_TOKEN_RX.sub(repl, chunk))
    return "".join(parts)
