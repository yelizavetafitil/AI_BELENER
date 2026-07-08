"""Извлечение нормативов (ГОСТ/ОСТ/ТУ/…) из PDF и изображений — OCR по тайлам листа."""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import fitz

from belener.normative_crops import extract_normatives_document_crops
from belener.config import upload_temp_dir
from belener.normative_refs import (
    _phrase_matches_highlight_ref,
    _ref_highlight_target,
    highlight_patterns_for_normative_ref,
    word_looks_like_kind_token,
)

log = logging.getLogger("belener.normative_extract")

_KIND_ALIASES: dict[str, frozenset[str]] = {
    "ГОСТ": frozenset({"гост", "gost"}),
    "ОСТ": frozenset({"ост", "ost", "oct"}),
    "ТУ": frozenset({"ту", "tu"}),
    "СТБ": frozenset({"стб", "stb"}),
    "СТП": frozenset({"стп", "stp"}),
    "ТКП": frozenset({"ткп", "tkp"}),
    "СНиП": frozenset({"снип", "snip"}),
    "СП": frozenset({"сп", "sp"}),
    "РД": frozenset({"рд", "rd"}),
}


def _kind_aliases(kind: str) -> frozenset[str]:
    return _KIND_ALIASES.get(kind, frozenset({kind.casefold()}))


def _kind_regex(kind: str) -> str:
    return {
        "ГОСТ": r"гост|gost",
        "ОСТ": r"ост|ost|oct",
        "ТУ": r"ту|tu",
        "СТБ": r"стб|stb",
        "СТП": r"стп|stp",
        "ТКП": r"ткп|tkp",
        "СНиП": r"снип|snip",
        "СП": r"сп|sp",
        "РД": r"рд|rd",
    }.get(kind, re.escape(kind))


def _word_text(word) -> str:
    raw = str(word[4] or "").strip()
    return re.sub(r"^[\(\[\"']+|[\)\]\}\"'.,;:!?]+$", "", raw)


def _word_rect(word) -> fitz.Rect:
    return fitz.Rect(word[:4])


def _words_on_same_line(words: list, a: int, b: int) -> bool:
    ra, rb = _word_rect(words[a]), _word_rect(words[b])
    tolerance = max(3.5, min(ra.height, rb.height) * 0.55)
    return abs((ra.y0 + ra.y1) * 0.5 - (rb.y0 + rb.y1) * 0.5) <= tolerance


def _median_word_height(words: list, start: int, end: int) -> float:
    hs = [_word_rect(words[k]).height for k in range(start, end + 1) if _word_text(words[k])]
    if not hs:
        return 12.0
    hs.sort()
    return hs[len(hs) // 2]


def _tighten_word_rect(word, *, line_h: float) -> fitz.Rect:
    """OCR часто раздувает bbox; поджимаем к высоте строки таблицы."""
    r = _word_rect(word)
    txt = _word_text(word)
    if not txt:
        return r
    h = max(r.height, 0.1)
    if h > line_h * 1.45:
        cy = (r.y0 + r.y1) * 0.5
        nh = min(h, line_h * 1.12)
        r = fitz.Rect(r.x0, cy - nh * 0.5, r.x1, cy + nh * 0.5)
    if r.width > 14:
        inset = min(1.2, r.width * 0.03)
        r = fitz.Rect(r.x0 + inset, r.y0, r.x1 - inset, r.y1)
    return r


def _rect_overlap_ratio(a: fitz.Rect, b: fitz.Rect) -> float:
    inter = a & b
    if inter.is_empty:
        return 0.0
    area = inter.get_area()
    denom = min(a.get_area(), b.get_area())
    return area / denom if denom > 0 else 0.0


def _word_has_kind_marker(word, kind: str) -> bool:
    text = _word_text(word)
    if not text:
        return False
    if text.casefold() in _kind_aliases(kind):
        return True
    if word_looks_like_kind_token(text, kind):
        return True
    return bool(re.search(rf"(?<![a-zа-яё]){_kind_regex(kind)}(?![a-zа-яё])", text, re.I))


def _rect_fingerprint(rect: fitz.Rect) -> tuple[int, int, int, int]:
    """Округление до пункта: грубое //2 сливало соседние строки таблицы."""
    return (
        round(rect.x0),
        round(rect.y0),
        round(rect.x1),
        round(rect.y1),
    )


def _dedupe_rects(rects: list[fitz.Rect]) -> list[fitz.Rect]:
    out: list[fitz.Rect] = []
    seen: set[tuple[int, int, int, int]] = set()
    for r in rects:
        if r is None or r.is_empty or r.width < 2 or r.height < 2:
            continue
        fp = _rect_fingerprint(r)
        if fp in seen:
            continue
        seen.add(fp)
        out.append(r)
    return out


def _is_number_token(text: str) -> bool:
    t = (text or "").strip()
    if not t or not re.search(r"\d", t):
        return False
    if re.match(r"^[A-Za-zА-Яа-яЁё]-\d", t):
        return False
    if re.fullmatch(r"\d{1,2}", t):
        return False
    letters = re.sub(r"[^a-zа-яё]", "", t, flags=re.I)
    return len(letters) < 3


def _phrase_matches_ref(phrase: str, ref_str: str) -> bool:
    kind, canon, dedupe = _ref_highlight_target(ref_str)
    if not kind or not canon:
        return False
    from belener.normative_refs import _normalize_highlight_phrase

    phrase = _normalize_highlight_phrase(phrase, kind)
    return _phrase_matches_highlight_ref(
        phrase, kind=kind, canon=canon, dedupe=dedupe, ref_str=ref_str
    )


def _trim_span_to_ref_tokens(words: list, start: int, end: int, kind: str) -> tuple[int, int]:
    kind_i = start
    for k in range(start, end + 1):
        if _word_has_kind_marker(words[k], kind):
            kind_i = k
            break
    last = kind_i
    for k in range(kind_i + 1, end + 1):
        if not _words_on_same_line(words, kind_i, k):
            break
        if _is_number_token(_word_text(words[k])):
            last = k
        else:
            break
    return kind_i, last


def _span_phrase(words: list, start: int, end: int) -> str:
    return " ".join(_word_text(words[k]) for k in range(start, end + 1) if _word_text(words[k]))


def _span_matches_ref(words: list, start: int, end: int, ref_str: str) -> bool:
    kind, _, _ = _ref_highlight_target(ref_str)
    if kind:
        a, b = _trim_span_to_ref_tokens(words, start, end, kind)
        if a <= b and _word_has_kind_marker(words[a], kind):
            phrase = _span_phrase(words, a, b)
            if phrase and _phrase_matches_ref(phrase, ref_str):
                return True
    phrase = _span_phrase(words, start, end)
    return bool(phrase) and _phrase_matches_ref(phrase, ref_str)


def _pinpoint_rects_for_span(words: list, start: int, end: int) -> list[fitz.Rect]:
    line_h = _median_word_height(words, start, end)
    rects: list[fitz.Rect] = []
    for k in range(start, end + 1):
        txt = _word_text(words[k])
        if txt:
            rects.append(_tighten_word_rect(words[k], line_h=line_h))
    return _dedupe_rects(rects)


def _all_word_spans_for_ref(words: list, ref_str: str) -> list[tuple[int, int]]:
    """Все вхождения норматива: повтор одного ГОСТ на листе → несколько spans."""
    kind, canon, _ = _ref_highlight_target(ref_str)
    if not kind or not canon or not words:
        return []

    n = len(words)
    seen: set[tuple[int, int]] = set()
    spans: list[tuple[int, int]] = []

    for k in range(n):
        token = _word_text(words[k])
        if not token or not _word_has_kind_marker(words[k], kind):
            continue
        if _phrase_matches_ref(token, ref_str):
            pair = (k, k)
            if pair not in seen:
                seen.add(pair)
                spans.append(pair)

    for i in range(n):
        for j in range(i, min(i + 14, n)):
            if j > i and not _words_on_same_line(words, i, j):
                continue
            if not _span_matches_ref(words, i, j, ref_str):
                continue
            a, b = _trim_span_to_ref_tokens(words, i, j, kind)
            if a > b or not _span_matches_ref(words, a, b, ref_str):
                continue
            if not _word_has_kind_marker(words[a], kind):
                continue
            if not all(_words_on_same_line(words, a, k) for k in range(a, b + 1)):
                continue
            pair = (a, b)
            if pair in seen:
                continue
            seen.add(pair)
            spans.append(pair)

    from belener.normative_refs import _body_year_digits

    body, _year = _body_year_digits(kind, ref_str)
    if body and len(body) >= 4:
        for k in range(n):
            token = _word_text(words[k])
            if not token or not _is_number_token(token):
                continue
            if not _phrase_matches_ref(f"{kind} {token}", ref_str):
                continue
            a = k
            for j in range(k - 1, max(-1, k - 6), -1):
                if not _words_on_same_line(words, j, k):
                    break
                if _word_has_kind_marker(words[j], kind):
                    a = j
                    break
            if not _word_has_kind_marker(words[a], kind):
                continue
            pair = (a, k)
            if pair in seen:
                continue
            seen.add(pair)
            spans.append(pair)

    return spans


def _quad_to_word_rects(words: list, quad_rect: fitz.Rect, ref_str: str) -> list[fitz.Rect]:
    kind, _, _ = _ref_highlight_target(ref_str)
    idxs = [k for k, w in enumerate(words) if _word_rect(w).intersects(quad_rect)]
    if not idxs:
        return []

    rects: list[fitz.Rect] = []
    seen: set[tuple[int, int]] = set()
    idx_set = set(idxs)
    for i in idxs:
        if not _word_has_kind_marker(words[i], kind):
            continue
        for j in idxs:
            if j < i or j - i > 11:
                continue
            if not _span_matches_ref(words, i, j, ref_str):
                continue
            a, b = _trim_span_to_ref_tokens(words, i, j, kind)
            if a > b or not _span_matches_ref(words, a, b, ref_str):
                continue
            if not all(k in idx_set for k in range(a, b + 1)):
                continue
            if not all(_words_on_same_line(words, a, k) for k in range(a, b + 1)):
                continue
            pair = (a, b)
            if pair in seen:
                continue
            seen.add(pair)
            rects.extend(_pinpoint_rects_for_span(words, a, b))
    return _dedupe_rects(rects)


def _search_quad_rects(page: fitz.Page, words: list, ref_str: str) -> list[fitz.Rect]:
    rects: list[fitz.Rect] = []
    for pattern in highlight_patterns_for_normative_ref(ref_str):
        try:
            hits = page.search_for(pattern, quads=True)
        except TypeError:
            hits = page.search_for(pattern)
        for hit in hits or []:
            try:
                rect = hit.rect
            except Exception:
                rect = fitz.Rect(hit)
            part = _quad_to_word_rects(words, rect, ref_str)
            if part:
                rects.extend(part)
    return _dedupe_rects(rects)


def _word_span_rects(words: list, ref_str: str) -> list[fitz.Rect]:
    rects: list[fitz.Rect] = []
    for start, end in _all_word_spans_for_ref(words, ref_str):
        rects.extend(_pinpoint_rects_for_span(words, start, end))
    return _dedupe_rects(rects)


def _expand_hit_rect(rect: fitz.Rect, *, pad: float = 5.0) -> fitz.Rect:
    return fitz.Rect(rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad)


def _merge_word_sources(sources: list[list]) -> list:
    """Текстовый слой + OCR: объединить без дублей по позиции."""
    out: list = []
    for src in sources:
        if not src:
            continue
        for w in src:
            txt = _word_text(w)
            if not txt:
                continue
            rw = _word_rect(w)
            if rw.is_empty:
                continue
            duplicate = False
            for kept in out:
                if _word_text(kept) != txt:
                    continue
                if _rect_overlap_ratio(rw, _word_rect(kept)) > 0.62:
                    duplicate = True
                    break
            if not duplicate:
                out.append(w)
    out.sort(key=lambda w: (round(_word_rect(w).y0), _word_rect(w).x0))
    return out


def _highlight_rects_for_ref(
    page: fitz.Page | None,
    words: list,
    ref_str: str,
) -> list[fitz.Rect]:
    """Все вхождения норматива: search_for по PDF + spans по словам."""
    rects: list[fitz.Rect] = []
    seen_fp: set[tuple[int, int, int, int]] = set()

    def collect(new_rects: list[fitz.Rect]) -> None:
        for r in _dedupe_rects(new_rects):
            fp = _rect_fingerprint(r)
            if fp in seen_fp:
                continue
            seen_fp.add(fp)
            rects.append(r)

    if page is not None:
        for pattern in highlight_patterns_for_normative_ref(ref_str):
            try:
                hits = page.search_for(pattern, quads=True)
            except TypeError:
                hits = page.search_for(pattern)
            for hit in hits or []:
                try:
                    hit_rect = hit.rect
                except Exception:
                    hit_rect = fitz.Rect(hit)
                clip_words = page.get_text("words", clip=_expand_hit_rect(hit_rect)) or []
                if clip_words:
                    collect(_word_span_rects(clip_words, ref_str))
                if words:
                    collect(_quad_to_word_rects(words, hit_rect, ref_str))

    if words:
        collect(_word_span_rects(words, ref_str))

    return rects


def _merge_source_rects(sources: list[list], ref_str: str, page: fitz.Page | None) -> list[fitz.Rect]:
    """Совместимость: объединённые слова + search_for."""
    merged_words = _merge_word_sources(sources)
    return _highlight_rects_for_ref(page, merged_words, ref_str)


def _find_pinpoint_rects(
    words: list,
    ref_str: str,
    *,
    page: fitz.Page | None = None,
) -> list[fitz.Rect]:
    rects: list[fitz.Rect] = []
    if page is not None:
        rects.extend(_search_quad_rects(page, words, ref_str))
    rects.extend(_word_span_rects(words, ref_str))
    return _dedupe_rects(rects)


def _mark_pinpoint_rect(page: fitz.Page, rect: fitz.Rect, used: set[tuple[int, int, int, int]]) -> None:
    if rect is None or rect.is_empty:
        return
    fp = _rect_fingerprint(rect)
    if fp in used:
        return
    used.add(fp)
    pad = 0.25
    box = fitz.Rect(rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad) & page.rect
    if box.is_empty:
        return
    annot = page.add_rect_annot(box)
    annot.set_colors(stroke=(0.95, 0.78, 0.0), fill=(1.0, 1.0, 0.0))
    annot.set_opacity(0.38)
    annot.set_border(width=0.25)
    annot.update()


def _preview_word_sources(
    pdf_path: str,
    page_index: int,
    *,
    page_count: int = 1,
) -> list[list]:
    """Слова листа для подсветки: текстовый слой + Tesseract по той же сетке, что и извлечение."""
    import gc

    from belener.config import tile_grid_for_page_count, tile_ocr_dpi_for_pages
    from belener.ocr import tesseract_words_from_rect
    from belener.tile_ocr import (
        page_is_wide,
        page_tile_jobs,
        page_tile_jobs_normative,
        supplements_for_page_scan,
    )

    sources: list[list] = []
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        text_words = page.get_text("words") or []
        if text_words:
            sources.append(text_words)

        cols, rows = tile_grid_for_page_count(page_count)
        dpi = tile_ocr_dpi_for_pages(page_count)
        tile_timeout = 18.0 if page_count <= 1 else 10.0
        if page_is_wide(page.rect):
            jobs = page_tile_jobs_normative(page.rect, cols=cols, rows=rows)
        else:
            jobs = page_tile_jobs(page.rect, cols=cols, rows=rows)
        jobs.extend(supplements_for_page_scan(page.rect, page_count))
        ocr_words: list = []
        for _zone, rect in jobs:
            chunk = tesseract_words_from_rect(
                doc,
                page_index,
                rect,
                dpi=dpi,
                timeout=tile_timeout,
            )
            if chunk:
                ocr_words.extend(chunk)
        if ocr_words:
            sources.append(ocr_words)
        log.debug(
            "preview words page=%s text=%s ocr=%s tiles=%s",
            page_index + 1,
            len(text_words),
            len(ocr_words),
            len(jobs),
        )
    finally:
        doc.close()
        gc.collect()
    return sources


def _highlight_on_page(
    page: fitz.Page,
    refs: list[dict],
    *,
    words: list | None = None,
    extra_word_sources: list[list] | None = None,
) -> tuple[int, int]:
    """Точечная подсветка всех вхождений нормативов из таблицы ответа."""
    sources: list[list] = []
    if words is not None:
        sources.append(words)
    elif page is not None:
        sources.append(page.get_text("words") or [])
    for extra in extra_word_sources or []:
        if extra and extra not in sources:
            sources.append(extra)

    merged_words = _merge_word_sources(sources)

    used: set[tuple[int, int, int, int]] = set()
    highlighted_refs = 0
    total_marks = 0

    for r in refs:
        ref_str = (r.get("ref") or "").strip()
        if not ref_str:
            continue
        rects = _highlight_rects_for_ref(page, merged_words, ref_str)
        rects = _dedupe_rects(rects)
        if not rects:
            log.debug("preview highlight miss: %s", ref_str)
            continue
        highlighted_refs += 1
        for rect in rects:
            before = len(used)
            _mark_pinpoint_rect(page, rect, used)
            if len(used) > before:
                total_marks += 1

    return highlighted_refs, total_marks


def generate_pdf_preview_pages_with_highlights(
    pdf_path: str,
    refs: list[dict],
    *,
    page_normative_refs: list[list[dict]] | None = None,
) -> list[dict[str, Any]]:
    """Превью каждого листа PDF с жёлтой подсветкой нормативов из ответа."""
    pages_out: list[dict[str, Any]] = []
    try:
        probe = fitz.open(pdf_path)
        page_count = probe.page_count
        probe.close()
        if page_count == 0:
            return pages_out

        tmp_dir = upload_temp_dir()
        for page_index in range(page_count):
            word_sources = _preview_word_sources(
                pdf_path,
                page_index,
                page_count=page_count,
            )
            doc = fitz.open(pdf_path)
            try:
                page = doc[page_index]
                highlighted_refs, total_marks = _highlight_on_page(
                    page,
                    refs,
                    words=word_sources[0] if word_sources else [],
                    extra_word_sources=word_sources[1:] or None,
                )
                pix = page.get_pixmap(dpi=144, annots=True, alpha=False)
            finally:
                doc.close()

            fname = f"preview_{uuid.uuid4().hex}.jpg"
            out_path = os.path.join(tmp_dir, fname)
            pix.save(out_path)
            pages_out.append(
                {
                    "page": page_index + 1,
                    "url": f"/api/preview/{fname}",
                    "refs": highlighted_refs,
                    "marks": total_marks,
                }
            )
            log.debug(
                "preview page=%s words=%s marks=%s",
                page_index + 1,
                sum(len(s) for s in word_sources),
                total_marks,
            )

        log.info(
            "preview highlights: %s pages, %s marks total on %s",
            len(pages_out),
            sum(int(p.get("marks") or 0) for p in pages_out),
            pdf_path,
        )
    except Exception as e:
        log.warning("Preview generation failed: %s", e)
    return pages_out


def generate_pdf_preview_with_highlights(pdf_path: str, refs: list[dict]) -> str | None:
    """Превью первого листа (совместимость)."""
    pages = generate_pdf_preview_pages_with_highlights(pdf_path, refs)
    return pages[0]["url"] if pages else None


def extract_normatives_from_document(
    doc: fitz.Document,
    filename: str = "document.pdf",
    *,
    source_path: str | None = None,
    allow_drawing_fallback: bool | None = None,
    pipeline_deadline: float | None = None,
) -> dict[str, Any]:
    """Нормативы: сетка тайлов по листу → OCR (основной путь)."""
    return extract_normatives_document_crops(doc, filename, pipeline_deadline=pipeline_deadline)


def extract_normatives_from_image_path(
    path: str,
    filename: str | None = None,
    *,
    pipeline_deadline: float | None = None,
) -> dict[str, Any]:
    """Изображение как одностраничный PDF → те же тайлы и OCR."""
    p = Path(path)
    doc = fitz.open(str(p))
    try:
        return extract_normatives_from_document(
            doc,
            filename or p.name,
            pipeline_deadline=pipeline_deadline,
        )
    finally:
        doc.close()


def extract_normatives_pdf_path(
    path: str,
    filename: str | None = None,
    *,
    pipeline_deadline: float | None = None,
) -> dict[str, Any]:
    p = Path(path)
    path_str = str(p.resolve())
    doc = fitz.open(path_str)
    try:
        return extract_normatives_from_document(
            doc,
            filename or p.name,
            source_path=path_str,
            pipeline_deadline=pipeline_deadline,
        )
    finally:
        doc.close()


def normative_refs_to_markdown(
    refs: list[dict[str, str]],
    *,
    filename: str = "",
    pipeline: str = "",
    include_context: bool = False,
    stn_checks: list | None = None,
    check_date: date | None = None,
    stn_error: str = "",
    page_count: int = 0,
    pages_processed: int = 0,
    budget_exhausted: bool = False,
    source_path: str = "",
    page_normative_refs: list[list[dict]] | None = None,
) -> str:
    lines = ["## Нормативные документы (ГОСТ, ОСТ, СТП, ТУ и др.)", ""]
    if filename:
        lines.append(f"**Файл:** {filename}")
    if page_count > 1:
        if filename:
            lines.append("")
        proc = pages_processed or page_count
        note = f"**Листов в файле:** {page_count}"
        if proc < page_count:
            note += f" · **обработано:** {proc}"
        if budget_exhausted:
            note += " · *не все листы успели прочитаться*"
        lines.append(note)
    elif budget_exhausted:
        if filename:
            lines.append("")
        lines.append("*Не все участки листа успели прочитаться — список может быть неполным.*")
    if check_date:
        if filename or page_count > 1 or budget_exhausted:
            lines.append("")
        lines.append(f"**Дата проверки актуальности:** {check_date.strftime('%d.%m.%Y')}")
    lines.append("")

    if not refs:
        lines.append(
            "*Нормативные ссылки не найдены. Проверьте качество скана или "
            "используйте полный разбор чертежа.*"
        )
        lines.append("")
    else:
        # Build check dictionary
        checks_map = {}
        if stn_checks:
            for c in stn_checks:
                # Handle both dicts and StnCheckResult objects
                try:
                    ref_val = c.ref if hasattr(c, "ref") else c.get("ref")
                    checks_map[ref_val] = c
                except Exception:
                    pass

        lines.append('<div class="normative-table-container">')
        lines.append("<table>")
        lines.append("<thead><tr>")
        lines.append("<th>Тип</th><th>Обозначение</th><th>ИПС</th><th>Введен</th><th>Отменен</th><th>Статус</th>")
        lines.append("</tr></thead>")
        lines.append("<tbody>")

        for n in refs:
            kind = n.get('kind') or '—'
            ref = n.get('ref') or '—'
            c = checks_map.get(ref)

            ips_link = "—"
            intro = "—"
            cancel = "—"
            status = "—"
            row_class = ""

            if c:
                found = c.found if hasattr(c, "found") else str(c.get("found")) == "1"
                doc_id = c.doc_id if hasattr(c, "doc_id") else c.get("doc_id")
                intro = c.intro_date if hasattr(c, "intro_date") else c.get("intro_date") or "—"
                cancel = c.cancel_date if hasattr(c, "cancel_date") else c.get("cancel_date") or "—"
                status_val = c.status if hasattr(c, "status") else c.get("status") or "—"
                error_val = c.error if hasattr(c, "error") else c.get("error")
                
                if error_val and status_val == "ошибка проверки":
                    status = f"{status_val} ({error_val[:60]})"
                elif not found and str(status_val).startswith("пропущено"):
                    status = "не проверено (время)"
                elif not found and (
                    status_val in ("нет в ИПС", "не в фонде STN")
                ):
                    status = "не найдено"
                else:
                    status = status_val

                if found and doc_id:
                    ips_link = (
                        f'<a class="stn-link" href="https://normy.stn.by/ips.php?{doc_id}" '
                        f'target="_blank">Открыть</a>'
                    )

                if status == "актуален":
                    row_class = ' class="row-active"'
                    status = f"<strong>{status}</strong>"
                elif status == "отменён":
                    row_class = ' class="row-canceled"'
                    status = f"<strong>{status}</strong>"
                elif status == "заменён":
                    row_class = ' class="row-replaced"'

            lines.append(f"<tr{row_class}>")
            lines.append(f"<td>{kind}</td><td>{ref}</td><td>{ips_link}</td><td>{intro}</td><td>{cancel}</td><td>{status}</td>")
            lines.append("</tr>")

        lines.append("</tbody></table>")
        lines.append("</div>")
        lines.append("")
        
        found_ips = sum(1 for c in checks_map.values() if (c.found if hasattr(c, "found") else str(c.get("found")) == "1"))
        active = sum(1 for c in checks_map.values() if (c.status if hasattr(c, "status") else c.get("status")) == "актуален")
        lines.append(f"*Всего в документе: {len(refs)}; найдено в ИПС: {found_ips}; актуально: {active}*")
        lines.append("")

    stn_error = (stn_error or "").strip()
    if stn_checks and not stn_error:
        skipped = sum(
            1
            for c in stn_checks
            if str(c.status if hasattr(c, "status") else c.get("status") or "").startswith("пропущено")
        )
        if skipped == len(stn_checks) and skipped > 0:
            stn_error = "Проверка ИПС не выполнена — не хватило времени после OCR."
    if stn_error:
        lines.extend(["", f"*⚠ {stn_error}*", ""])

    if source_path and os.path.isfile(source_path):
        preview_pages = generate_pdf_preview_pages_with_highlights(
            source_path,
            refs,
            page_normative_refs=page_normative_refs,
        )
        if preview_pages:
            if len(preview_pages) == 1:
                lines.append("### Предпросмотр листа")
            else:
                lines.append(f"### Предпросмотр листов ({len(preview_pages)})")
            for entry in preview_pages:
                page_no = int(entry.get("page") or 0)
                preview_url = str(entry.get("url") or "")
                if not preview_url:
                    continue
                preview_id = f"preview-{uuid.uuid4().hex[:8]}"
                lines.append(f'<h4 class="pdf-preview-sheet-title">Лист {page_no}</h4>')
                lines.append(
                    f'<div class="pdf-preview-tools">'
                    f'<a class="stn-link" href="{preview_url}" target="_blank">Открыть лист {page_no}</a>'
                    f'<div class="preview-zoom-buttons">'
                    f'<button class="preview-zoom-btn" data-target="{preview_id}" data-action="out">-</button>'
                    f'<button class="preview-zoom-btn" data-target="{preview_id}" data-action="reset">100%</button>'
                    f'<button class="preview-zoom-btn" data-target="{preview_id}" data-action="in">+</button>'
                    f'</div></div>'
                )
                lines.append(
                    f'<div class="pdf-preview-container"><img id="{preview_id}" src="{preview_url}" '
                    f'alt="Предпросмотр листа {page_no}" class="pdf-preview-img" data-scale="1"></div>'
                )
            lines.append("")

    return "\n".join(lines)


def normative_result_to_markdown(
    result: dict[str, Any],
    *,
    include_context: bool = False,
    stn_checks: list | None = None,
    check_date: date | None = None,
    source_path: str = "",
) -> str:
    checks = stn_checks
    if checks is None:
        checks = result.get("stn_checks")
    return normative_refs_to_markdown(
        list(result.get("normative_refs") or []),
        filename=str(result.get("filename") or ""),
        pipeline=str(result.get("pipeline") or ""),
        include_context=include_context,
        stn_checks=checks,
        check_date=check_date,
        stn_error=str(result.get("stn_error") or ""),
        page_count=int(result.get("page_count") or 0),
        pages_processed=int(result.get("pages_processed") or 0),
        budget_exhausted=bool(result.get("budget_exhausted")),
        source_path=source_path,
        page_normative_refs=list(result.get("page_normative_refs") or []),
    )
