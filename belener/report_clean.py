"""Очистка фактов чертежа перед отчётом: отделение ТТ от таблиц, фильтр мусора."""

from __future__ import annotations

import re
from typing import Any

from belener.parse import _clean_legend_note, _is_garbage_legend_note, clean_table_title


def _cyrillic_ratio(text: str) -> float:
    letters = re.findall(r"[А-Яа-яЁёA-Za-z]", text or "")
    if not letters:
        return 0.0
    cyr = sum(1 for c in letters if "\u0400" <= c <= "\u04FF")
    return cyr / len(letters)


def _noise_ratio(text: str) -> float:
    s = re.sub(r"\s+", "", text or "")
    if not s:
        return 1.0
    noisy = len(re.findall(r"[|_`$<>=%№©{}\[\]\\]{1}", s))
    return noisy / len(s)


def looks_like_ocr_noise(text: str) -> bool:
    return _looks_like_ocr_noise(text)


def _looks_like_ocr_noise(text: str) -> bool:
    s = re.sub(r"\s+", " ", (text or "").strip())
    if not s:
        return True
    if _noise_ratio(s) > 0.12:
        return True
    words = re.findall(r"[А-Яа-яЁёA-Za-z]{2,}", s)
    if len(s) > 80 and len(words) < 5:
        return True
    short = [w for w in words if len(w) <= 2]
    if len(words) >= 8 and len(short) / len(words) > 0.45:
        return True
    return False


def is_garbage_body_text(text: str) -> bool:
    s = (text or "").strip()
    if len(s) < 40:
        return True
    if _looks_like_ocr_noise(s):
        return True
    if _cyrillic_ratio(s) < 0.45:
        return True
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    if len(lines) > 8 and sum(1 for ln in lines if len(ln) <= 12) / len(lines) > 0.55:
        return True
    return False


def _is_tt_paragraph(note: str) -> bool:
    """Длинный нумерованный текст — вероятно ТТ, не строка легенды."""
    s = _clean_legend_note(note)
    if len(s) < 80:
        return False
    if re.match(r"^\d{1,2}\s*[\.\)]?\s*\S", s):
        return True
    numbered = len(re.findall(r"(?:^|\s)\d{1,2}\s*[\.\)]?\s+", s))
    if numbered >= 2:
        return True
    return len(s) > 220


def _split_tt_items(text: str) -> list[dict[str, str]]:
    s = re.sub(r"\s+", " ", text.strip())
    if not s:
        return []
    parts = re.split(r"(?<=\.)\s+(?=\d{1,2}\s)", s)
    if len(parts) <= 1:
        parts = re.split(r"\s+(?=\d{1,2}\s+[А-ЯA-Z])", s)
    out: list[dict[str, str]] = []
    for chunk in parts:
        chunk = chunk.strip()
        if len(chunk) < 15:
            continue
        m = re.match(r"^(\d{1,2})\s*[\.\)]?\s*(.+)", chunk)
        if m:
            out.append({"number": m.group(1), "text": m.group(2).strip()})
        else:
            out.append({"number": "", "text": chunk})
    return out


def _row_quality(row: dict, kind: str) -> int:
    row_text = " ".join(str(v or "") for v in row.values())
    if _looks_like_ocr_noise(row_text):
        return -20
    if kind == "explication":
        name = str(row.get("name") or "")
        if len(name) < 8:
            return -10
        if _cyrillic_ratio(name) < 0.5:
            return -5
        return len(name)
    if kind == "legend":
        note = _clean_legend_note(str(row.get("note") or ""))
        if _is_garbage_legend_note(note) or _is_tt_paragraph(note):
            return -20
        return len(note)
    return 5 if _cyrillic_ratio(row_text) >= 0.35 else -10


def _filter_rows(rows: list[dict], kind: str) -> list[dict]:
    from belener.parse import _filter_specification_rows, _is_spec_header_data_row

    kept: list[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if kind == "specification" and _is_spec_header_data_row(row):
            continue
        if _row_quality(row, kind) >= 0:
            kept.append(row)
    if kind == "specification":
        return _filter_specification_rows(kept)
    return kept


_STAMP_COL = re.compile(
    r"\b(изм\.?|кол\.?\s*уч|кол\.|лист|№\s*док|подп\.|дата)\b",
    re.I,
)

_GARBAGE_COL_RX = re.compile(
    r"^(напряжения|обозна-?|науменобаниуе|прумечание|symbol|0|1|2|3|4)$",
    re.I,
)

_SCHEMATIC_BLOB_RX = re.compile(
    r"цеп[ией]\s+напряжен|радиомодем|блок\s+питан|выключател|рпв\d|х\d+:\d+|"
    r"аскмитт|трансформатор\s+раздел|ценце\s+ченце|разделительн.*поз",
    re.I,
)

_BOM_BLOB_RX = re.compile(
    r"перечень\s+аппаратур|поз\.?\s*обознач",
    re.I,
)


def _is_stamp_revision_table(tbl: dict[str, Any]) -> bool:
    """Таблица изменений штампа, ошибочно попавшая в раздел таблиц."""
    rows = tbl.get("rows") or []
    if not rows:
        return False
    blob_parts: list[str] = []
    for r in rows[:8]:
        if not isinstance(r, dict):
            continue
        blob_parts.extend(str(k) for k in r.keys())
        blob_parts.extend(str(v) for v in r.values())
    blob = " ".join(blob_parts)
    if len(_STAMP_COL.findall(blob)) >= 3:
        return True
    if re.search(r"копиров|молодечн|общестанцион|руп\s*[\"«]|филиал", blob, re.I):
        if len(_STAMP_COL.findall(blob)) >= 1 or "лист" in blob.casefold():
            return True
    keys_blob = " ".join(str(k) for r in rows[:3] if isinstance(r, dict) for k in r.keys())
    return len(_STAMP_COL.findall(keys_blob)) >= 2


def _has_garbage_column_headers(tbl: dict[str, Any]) -> bool:
    rows = tbl.get("rows") or []
    if not rows or not isinstance(rows[0], dict):
        return False
    keys = [str(k).casefold().strip() for k in rows[0].keys()]
    bad = sum(1 for k in keys if _GARBAGE_COL_RX.search(k))
    return bad >= 2


def _is_bad_table_title(title: str) -> bool:
    t = re.sub(r"\s+", " ", (title or "").strip()).casefold()
    if not t:
        return False
    if len(title) > 75:
        return True
    if re.search(r"разделительн.*поз|ченце|^\d+\s*$", t):
        return True
    if _looks_like_ocr_noise(title) and len(title) < 70:
        return True
    from belener.parse import _SPEC_COL_MARKERS

    if len(_SPEC_COL_MARKERS.findall(title)) >= 2:
        return True
    return False


def _is_schematic_caption_table(tbl: dict[str, Any]) -> bool:
    """Подписи к элементам схемы, ошибочно собранные в таблицу."""
    kind = str(tbl.get("kind") or "")
    rows = tbl.get("rows") or []
    if not rows:
        return False
    blob = " ".join(str(v) for r in rows if isinstance(r, dict) for v in r.values())
    blob += " " + str(tbl.get("title") or "")
    if kind == "specification":
        from belener.spec_table import is_schematic_caption_row
        from belener.table_quality import spec_table_header_present

        sch = sum(1 for r in rows if isinstance(r, dict) and is_schematic_caption_row(r))
        if sch >= max(2, int(len(rows) * 0.5)):
            return True
        if len(rows) <= 2 and sch >= 1 and not spec_table_header_present(blob):
            return True
        return False
    if kind in ("explication",):
        return False
    sch = len(_SCHEMATIC_BLOB_RX.findall(blob))
    bom = len(_BOM_BLOB_RX.findall(blob))
    if sch >= 3 and bom < 2:
        return True
    kind = str(tbl.get("kind") or "")
    if kind == "legend":
        for r in rows:
            note = str(r.get("note") or "")
            if len(note) > 100 and _SCHEMATIC_BLOB_RX.search(note):
                return True
    if kind == "table":
        for r in rows[:6]:
            if not isinstance(r, dict):
                continue
            vals = " ".join(str(v) for v in r.values())
            if len(vals) > 90 and _SCHEMATIC_BLOB_RX.search(vals) and bom == 0:
                return True
    return False


def _is_mixed_garbage_table(tbl: dict[str, Any]) -> bool:
    """Широкая «таблица» с шумным OCR — часто штамп+чертёж в одном блоке."""
    if _is_stamp_revision_table(tbl):
        return True
    if _has_garbage_column_headers(tbl):
        return True
    if _is_schematic_caption_table(tbl):
        return True
    title = str(tbl.get("title") or "")
    if _is_bad_table_title(title):
        return True
    rows = tbl.get("rows") or []
    if not rows:
        return False
    blob = " ".join(str(v) for r in rows if isinstance(r, dict) for v in r.values())
    if len(blob) < 80:
        return False
    ncol = max((len(r) for r in rows if isinstance(r, dict)), default=0)
    if ncol >= 6 and _looks_like_ocr_noise(blob):
        return True
    if rows and isinstance(rows[0], dict) and len(rows[0]) >= 5:
        vals = [str(v).strip() for v in rows[0].values()]
        short = sum(1 for v in vals if 0 < len(v) <= 14)
        dates = sum(1 for v in vals if re.search(r"\d{1,2}[\./]\d{2}", v))
        if short >= 4 and dates >= 1 and _noise_ratio(blob) > 0.06:
            return True
    return False


def _merge_legend_tables(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    legends = [t for t in tables if str(t.get("kind") or "") == "legend"]
    if len(legends) <= 1:
        return tables
    others = [t for t in tables if str(t.get("kind") or "") != "legend"]
    from belener.parse import merge_legend_rows

    merged_rows: list[dict] = []
    best_title = ""
    for leg in sorted(legends, key=_table_score, reverse=True):
        if _table_score(leg) < 0:
            continue
        t = clean_table_title(str(leg.get("title") or ""))
        if t and not best_title:
            best_title = t
        merged_rows = merge_legend_rows(merged_rows, leg.get("rows") or [])
    if not merged_rows:
        return tables
    return others + [
        {
            "kind": "legend",
            "title": best_title or "Условные обозначения",
            "rows": merged_rows,
            "table_number": str(legends[0].get("table_number") or ""),
        }
    ]


def _keep_best_explication(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expls = [t for t in tables if str(t.get("kind") or "") == "explication"]
    if len(expls) <= 1:
        return tables
    best = max(expls, key=_table_score)
    return [t for t in tables if str(t.get("kind") or "") != "explication"] + [best]


def _keep_best_specification(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs = [t for t in tables if str(t.get("kind") or "") == "specification"]
    if len(specs) <= 1:
        return tables
    best = max(specs, key=_table_score)
    return [t for t in tables if str(t.get("kind") or "") != "specification"] + [best]


def _fix_table_kind_and_rows(tbl: dict[str, Any]) -> dict[str, Any]:
    from belener.parse import (
        _explication_rows_to_specification,
        _infer_table_kind,
        _looks_like_bom_rows,
    )

    kind = _infer_table_kind(tbl)
    rows = list(tbl.get("rows") or [])
    if kind == "specification" and rows and all(isinstance(r, dict) and r.get("name") for r in rows):
        rows = _explication_rows_to_specification(rows)
    elif kind == "explication" and _looks_like_bom_rows(rows):
        kind = "specification"
        rows = _explication_rows_to_specification(rows)
    title = clean_table_title(str(tbl.get("title") or "").strip())
    if kind == "specification" and not title:
        if re.search(r"продолжен", str(tbl.get("table_number") or ""), re.I):
            title = "Продолжение таблицы 1"
        else:
            title = "Перечень аппаратуры"
    return {**tbl, "kind": kind, "rows": rows, "title": title}


def _prune_legend_rows(
    tbl: dict[str, Any],
    spec_rows: list[dict] | None = None,
) -> dict[str, Any]:
    from belener.parse import _clean_legend_note, _is_garbage_legend_note, _is_section_title_only
    from belener.spec_table import is_bom_like_legend_note, legend_note_matches_spec

    rows = tbl.get("rows") or []
    spec_rows = spec_rows or []
    kept: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        note = _clean_legend_note(str(r.get("note") or ""))
        if not note or _is_garbage_legend_note(note) or _is_section_title_only(note):
            continue
        if is_bom_like_legend_note(note):
            continue
        if spec_rows and legend_note_matches_spec(note, spec_rows):
            continue
        if len(note) > 100:
            continue
        kept.append({"symbol": r.get("symbol") or "—", "note": note})
    return {**tbl, "rows": kept}


def _legend_note_to_spec_row(note: str) -> dict[str, str] | None:
    from belener.spec_extract import extract_spec_rows_from_messy_ocr

    rows = extract_spec_rows_from_messy_ocr(note, max_rows=3)
    data = [r for r in rows if not r.get("_group")]
    return data[0] if data else None


def _salvage_spec_from_legend_tables(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Перечень аппаратуры, ошибочно попавший в условные обозначения (склейка OCR)."""
    from belener.parse import _parse_spec_row_loose
    from belener.parse import _is_section_title_only
    from belener.spec_table import (
        is_bom_like_legend_note,
        is_schematic_caption_row,
        salvage_spec_rows_from_texts,
    )

    salvaged: list[dict[str, str]] = []
    out: list[dict[str, Any]] = []
    for tbl in tables:
        if str(tbl.get("kind") or "") != "legend":
            out.append(tbl)
            continue
        notes = [str(r.get("note") or "") for r in (tbl.get("rows") or []) if isinstance(r, dict)]
        rows = salvage_spec_rows_from_texts(
            notes,
            parse_row=_parse_spec_row_loose,
            is_caption=is_schematic_caption_row,
        )
        salvaged.extend(rows)
        kept_leg: list[dict] = []
        for r in tbl.get("rows") or []:
            if not isinstance(r, dict):
                continue
            note = str(r.get("note") or "").strip()
            if is_bom_like_legend_note(note) or _is_section_title_only(note):
                continue
            if rows and any(
                re.sub(r"\W+", " ", note.casefold())
                in re.sub(r"\W+", " ", str(x.get("Наименование") or "").casefold())
                for x in rows
            ):
                continue
            kept_leg.append(r)
        out.append({**tbl, "rows": kept_leg})
    if salvaged:
        from belener.spec_table import dedupe_spec_rows

        salvaged = dedupe_spec_rows(salvaged)
        merged = False
        for tbl in out:
            if str(tbl.get("kind") or "") == "specification":
                tbl["rows"] = dedupe_spec_rows(list(tbl.get("rows") or []) + salvaged)
                merged = True
                break
        if not merged:
            out.insert(
                0,
                {
                    "kind": "specification",
                    "title": "Перечень аппаратуры",
                    "rows": salvaged,
                    "table_number": "Таблица 1",
                },
            )
    return out


def prune_garbage_tables(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tables = _salvage_spec_from_legend_tables(tables)
    spec_rows: list[dict] = []
    for t in tables:
        if str(t.get("kind") or "") == "specification":
            spec_rows.extend(t.get("rows") or [])

    kept: list[dict[str, Any]] = []
    for tbl in tables:
        kind = str(tbl.get("kind") or "table")
        if kind == "legend":
            from belener.table_quality import legend_ocr_plausible

            if not legend_ocr_plausible("", tbl.get("rows") or []):
                continue
        if kind == "specification":
            from belener.table_quality import spec_table_plausible
            from belener.normative_spec import normative_bom_plausible

            if not spec_table_plausible("", tbl.get("rows") or []) and not normative_bom_plausible(
                tbl.get("rows") or []
            ):
                continue
        if kind == "explication":
            from belener.table_quality import explication_table_plausible

            if not explication_table_plausible("", tbl.get("rows") or []):
                continue
        if _is_mixed_garbage_table(tbl):
            continue
        tbl = _fix_table_kind_and_rows(tbl)
        kind = str(tbl.get("kind") or "table")
        if kind == "legend":
            tbl = _prune_legend_rows(tbl, spec_rows)
        rows = _filter_rows(tbl.get("rows") or [], kind)
        if not rows:
            continue
        title = clean_table_title(str(tbl.get("title") or "").strip())
        if _is_bad_table_title(title):
            title = ""
        score = _table_score({**tbl, "rows": rows, "title": title})
        min_score = 3 if kind in ("explication", "legend", "specification") else 6
        if score < min_score:
            continue
        kept.append({**tbl, "rows": rows, "title": title})
    kept = _keep_best_explication(kept)
    kept = _keep_best_specification(kept)
    return _merge_legend_tables(kept)


def extract_quality_poor(
    stamp: dict[str, Any],
    tables: list[dict[str, Any]],
    table_text: str = "",
) -> bool:
    from belener.parse import _is_bad_signature_name

    sig_ok = sum(
        1
        for s in stamp.get("signatures") or []
        if not _is_bad_signature_name(str(s.get("name") or ""))
    )
    if sig_ok < 2:
        return True
    good_sections = 0
    for sec in tables:
        kind = str(sec.get("kind") or "table")
        rows = sec.get("rows") or []
        if not rows:
            continue
        good = sum(1 for r in rows if _row_quality(r, kind) >= 8)
        if good >= max(2, int(len(rows) * 0.45)):
            good_sections += 1
    if good_sections == 0 and tables:
        return True
    if table_text and len(table_text) > 200 and _looks_like_ocr_noise(table_text[:2500]):
        return True
    return False


def _clean_signatures(stamp: dict[str, Any]) -> dict[str, Any]:
    from belener.parse import normalize_signatures

    out = dict(stamp)
    out["signatures"] = normalize_signatures(list(stamp.get("signatures") or []))
    return out


def _table_score(tbl: dict[str, Any]) -> int:
    kind = str(tbl.get("kind") or "")
    rows = _filter_rows(tbl.get("rows") or [], kind)
    title = clean_table_title(str(tbl.get("title") or ""))
    score = len(rows) * 10
    if title and title.casefold() not in ("таблица", "table"):
        score += 20
    if title and _looks_like_ocr_noise(title):
        score -= 30
    return score


def _dedupe_tables(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for tbl in tables:
        kind = str(tbl.get("kind") or "table")
        title = clean_table_title(str(tbl.get("title") or "")).casefold()
        rows = tbl.get("rows") or []
        row_sig = "|".join(
            re.sub(r"\s+", " ", " ".join(str(v) for v in row.values())).casefold()[:120]
            for row in rows[:4]
            if isinstance(row, dict)
        )
        key = (kind, title, row_sig)
        if key in seen:
            continue
        seen.add(key)
        kept.append(tbl)
    order = {"specification": 0, "explication": 1, "legend": 2, "table": 3}
    return sorted(kept, key=lambda t: (order.get(str(t.get("kind") or "table"), 10), tables.index(t)))


def clean_drawing_facts(facts: dict[str, Any]) -> dict[str, Any]:
    if not facts.get("ok"):
        return facts

    tables = list(facts.get("tables") or [])
    tt_chunks: list[str] = []
    cleaned_tables: list[dict[str, Any]] = []

    for tbl in tables:
        kind = str(tbl.get("kind") or "table")
        rows = list(tbl.get("rows") or [])
        if kind == "legend":
            from belener.table_quality import legend_ocr_plausible

            kept_leg: list[dict] = []
            for row in rows:
                note = _clean_legend_note(str(row.get("note") or ""))
                if _is_tt_paragraph(note):
                    tt_chunks.append(note)
                    continue
                if not _is_garbage_legend_note(note):
                    kept_leg.append({"symbol": row.get("symbol") or "—", "note": note})
            if not legend_ocr_plausible("", kept_leg):
                continue
            rows = kept_leg
        rows = _filter_rows(rows, kind)
        if not rows:
            continue
        title = clean_table_title(str(tbl.get("title") or "").strip())
        if title and _looks_like_ocr_noise(title):
            title = ""
        cleaned_tables.append({**tbl, "title": title, "rows": rows})

    tables = prune_garbage_tables(_dedupe_tables(cleaned_tables))

    from belener.notes_filter import filter_notes_to_tt

    notes = filter_notes_to_tt(facts.get("sheet_notes")) or {}
    full = ""
    sections = list(notes.get("sections") or [])
    for chunk in tt_chunks:
        sections.extend(_split_tt_items(chunk))
    if full and not sections:
        sections = _split_tt_items(full)

    seen: set[str] = set()
    uniq_sections: list[dict[str, str]] = []
    for sec in sections:
        key = (sec.get("number"), (sec.get("text") or "")[:80])
        if key in seen:
            continue
        seen.add(key)
        sec_text = str(sec.get("text") or "")
        if len(sec_text) >= 12 and not _looks_like_ocr_noise(sec_text):
            uniq_sections.append(sec)

    if uniq_sections:
        notes = {
            "title": str(notes.get("title") or "Технические требования").strip()
            or "Технические требования",
            "sections": uniq_sections,
            "full_text": "",
        }
    else:
        notes = {}

    warnings = [
        w
        for w in (facts.get("warnings") or [])
        if "текстовый слой" not in w.casefold() and "вне таблиц" not in w.casefold()
    ]

    out = dict(facts)
    out["tables"] = tables
    out["sheet_notes"] = notes or None
    stamp = _clean_signatures(dict(out.get("stamp") or {}))
    from belener.parse import _is_garbage_stamp_title, _looks_like_stamp_section_title, _normalize_stamp_title

    titles = [
        _normalize_stamp_title(str(t))
        for t in (stamp.get("titles") or [])
        if _looks_like_stamp_section_title(_normalize_stamp_title(str(t)))
        and not _is_garbage_stamp_title(str(t))
    ]
    stamp["titles"] = titles
    revisions = [
        r
        for r in (stamp.get("revisions") or [])
        if isinstance(r, dict) and not _looks_like_ocr_noise(" ".join(str(v or "") for v in r.values()))
    ]
    stamp["revisions"] = revisions
    out["stamp"] = stamp
    out["warnings"] = warnings
    expl_rows: list[dict] = []
    leg_rows: list[dict] = []
    for t in tables:
        if t.get("kind") == "explication":
            expl_rows = t.get("rows") or []
        if t.get("kind") == "legend":
            leg_rows = t.get("rows") or []
    out["explication"] = {"title": "", "rows": expl_rows}
    out["legend"] = {"title": "", "rows": leg_rows, "row_count": len(leg_rows)}
    return out
