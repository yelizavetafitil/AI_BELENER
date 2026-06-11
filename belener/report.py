"""Отчёт чертежа: markdown-таблицы, ТТ, штамп и проверка качества PDF."""

from __future__ import annotations

import re
import textwrap
from typing import Any

from belener.config import (
    report_include_body_text,
    report_include_full_text_layer,
    report_include_quality,
    report_markdown_tables,
    report_normative_compact,
    report_stamp_frame_only,
)
from belener.notes_filter import is_technical_requirements_notes
from belener.parse import STAMP_SIGNATURE_ORDER, _is_bad_signature_name, clean_table_title

_SPEC_COLS = ("Поз.", "Обозначение", "Наименование", "Кол.", "Масса ед., кг", "Примечание")


def _esc_md_cell(val: str) -> str:
    return re.sub(r"\s+", " ", str(val or "—").strip() or "—").replace("|", "\\|")


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    if not headers or not rows:
        return []
    h = " | ".join(_esc_md_cell(x) for x in headers)
    sep = " | ".join("---" for _ in headers)
    body = [" | ".join(_esc_md_cell(c) for c in row) for row in rows]
    return [h, sep, *body]


def _render_dict_table(rows: list[dict], *, col_order: tuple[str, ...] | None = None) -> list[str]:
    if not rows:
        return []
    keys: list[str] = list(col_order) if col_order else []
    for r in rows:
        if isinstance(r, dict):
            for k in r:
                if k not in keys:
                    keys.append(k)
    body = [[_esc_md_cell(r.get(k)) for k in keys] for r in rows if isinstance(r, dict)]
    if report_markdown_tables():
        return _md_table(keys, body)
    return _ascii_table(keys, body)


def _ascii_table(headers: list[str], rows: list[list[str]], *, col_width: int = 72) -> list[str]:
    if not headers:
        return []
    widths = [max(3, len(h)) for h in headers]

    def border() -> str:
        return "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def line(cells: list[str]) -> str:
        parts = []
        for i, w in enumerate(widths):
            c = str(cells[i] if i < len(cells) else "").ljust(w)
            parts.append(f" {c} ")
        return "|" + "|".join(parts) + "|"

    out = [border(), line(headers), border()]
    for row in rows:
        out.append(line([str(c) for c in row]))
    out.append(border())
    return out


def _ordered_signatures(sigs: list[dict]) -> list[dict]:
    by_role = {str(s.get("role") or ""): s for s in sigs if s.get("role")}
    out: list[dict] = []
    for role in STAMP_SIGNATURE_ORDER:
        s = by_role.get(role)
        if not s:
            continue
        name = str(s.get("name") or "—").strip()
        if name not in ("—", "") and _is_bad_signature_name(name):
            name = "—"
        out.append({**s, "name": name or "—", "date": str(s.get("date") or "—").strip() or "—"})
    for s in sigs:
        role = str(s.get("role") or "").strip()
        if role and role not in {x["role"] for x in out}:
            out.append(s)
    return out


def _render_tt(sections: list[dict]) -> list[str]:
    lines: list[str] = []
    for sec in sections:
        num = str(sec.get("number") or "").strip()
        txt = str(sec.get("text") or "").strip()
        if not txt:
            continue
        para = textwrap.fill(txt, width=88)
        lines.append(f"{num} {para}" if num else para)
        lines.append("")
    return lines


def _render_text_blocks(blocks: list[dict]) -> list[str]:
    lines: list[str] = []
    for block in blocks or []:
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        title = str(block.get("title") or "Текстовый блок").strip()
        if not lines:
            lines.extend(["**Текстовые обозначения на листе**", ""])
        if title:
            lines.append(title)
            lines.append("")
        lines.append(text)
        lines.append("")
    return lines


def full_text_pages_to_markdown(pages: list[dict]) -> str:
    good = [
        p
        for p in pages or []
        if len(str(p.get("text") or "").strip()) >= 20
    ]
    if not good:
        return ""
    lines = ["**Полный текст листа (текстовый слой PDF)**", ""]
    for page in good:
        idx = page.get("index") or "?"
        text = str(page.get("text") or "").strip()
        lines.append(f"Страница {idx}")
        lines.append("")
        lines.append("```text")
        lines.append(text)
        lines.append("```")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _render_quality(quality: dict[str, Any]) -> list[str]:
    if not quality:
        return []
    lines = ["**Проверка чертежа**", ""]
    issue_count = int(quality.get("issue_count") or 0)
    lines.append(f"Выходы за границы листа: {issue_count}")
    lines.append("")

    fonts = quality.get("fonts") or []
    if fonts:
        lines.append("Шрифты в PDF:")
        lines.append("")
        lines.extend(_render_dict_table(fonts, col_order=("Шрифт", "Размер", "Фрагментов")))
        lines.append("")
    else:
        lines.append("Шрифты: текстовый слой не найден или PDF является сканом.")
        lines.append("")

    issue_rows: list[dict[str, str]] = []
    for page in quality.get("pages") or []:
        idx = str(page.get("index") or "?")
        for item in page.get("issues") or []:
            issue_rows.append(
                {
                    "Стр.": idx,
                    "Тип": str(item.get("type") or "—"),
                    "Описание": str(item.get("detail") or "—"),
                    "BBox": ", ".join(str(x) for x in (item.get("bbox") or [])),
                }
            )
            if len(issue_rows) >= 30:
                break
        if len(issue_rows) >= 30:
            break
    if issue_rows:
        lines.append("Проблемные элементы:")
        lines.append("")
        lines.extend(_render_dict_table(issue_rows, col_order=("Стр.", "Тип", "Описание", "BBox")))
        lines.append("")
    return lines


def _table_heading(tbl: dict, index: int) -> str:
    title = clean_table_title(str(tbl.get("title") or "").strip())
    tn = re.sub(r"\s+", " ", str(tbl.get("table_number") or "").strip())
    if tn and title and tn.casefold() not in title.casefold():
        return f"{tn}. {title}"
    return title or tn or f"Таблица {index}"


def _usable_stamp_title(t: str) -> bool:
    """Отсечь мусор из текстового слоя PDF (не заголовки раздела)."""
    from belener.parse import _is_garbage_stamp_title, _looks_like_stamp_section_title, _normalize_stamp_title

    s = _normalize_stamp_title(str(t or ""))
    if not s or _is_garbage_stamp_title(s):
        return False
    return _looks_like_stamp_section_title(s)


def _stamp_has_content(stamp: dict[str, Any]) -> bool:
    if not stamp:
        return False
    if stamp.get("raw_frame") or stamp.get("kv") or stamp.get("signatures"):
        return True
    if stamp.get("revisions") or stamp.get("other_lines"):
        return True
    titles = [str(t).strip() for t in (stamp.get("titles") or []) if len(str(t).strip()) > 25]
    if titles:
        return True
    return False


def _render_universal_stamp(stamp: dict[str, Any]) -> list[str]:
    """Рамка как на чертеже: динамические подписи полей и таблицы без шаблона ГОСТ."""
    lines: list[str] = ["**Основная надпись (рамка листа)**", ""]
    raw = stamp.get("raw_frame") or {}

    rev_rows = stamp.get("revisions") or []
    if rev_rows:
        keys: list[str] = []
        for r in rev_rows:
            if isinstance(r, dict):
                for k in r:
                    if k not in keys:
                        keys.append(k)
        if keys:
            body = [[_esc_md_cell(r.get(k)) for k in keys] for r in rev_rows if isinstance(r, dict)]
            lines.extend(_md_table(keys, body))
            lines.append("")

    sig_table = raw.get("signature_table") if isinstance(raw, dict) else None
    if isinstance(sig_table, dict) and sig_table.get("rows"):
        headers = [str(h).strip() or f"col{i + 1}" for i, h in enumerate(sig_table.get("headers") or [])]
        if not headers:
            ncols = max(len(r) for r in sig_table.get("rows") or [] if isinstance(r, (list, tuple)))
            headers = [f"col{i + 1}" for i in range(ncols)]
        body = []
        for row in sig_table.get("rows") or []:
            if isinstance(row, (list, tuple)):
                cells = [str(c).strip() for c in row]
                while len(cells) < len(headers):
                    cells.append("—")
                body.append([_esc_md_cell(c) for c in cells[: len(headers)]])
        if body:
            lines.append("**Подписи**")
            lines.append("")
            lines.extend(_md_table(headers, body))
            lines.append("")
    elif stamp.get("signatures"):
        sigs = stamp.get("signatures") or []
        keys = sorted({k for s in sigs if isinstance(s, dict) for k in s if k != "sign"})
        if keys:
            body = [[_esc_md_cell(s.get(k)) for k in keys] for s in sigs if isinstance(s, dict)]
            lines.extend(_md_table(keys, body))
            lines.append("")

    for item in stamp.get("kv") or []:
        label = str(item.get("field") or "").strip()
        val = str(item.get("value") or "").strip()
        if label and val:
            lines.append(f"**{label}:** {val}")

    titles = [str(t).strip() for t in (stamp.get("titles") or []) if str(t).strip()]
    if titles:
        lines.append("")
        lines.append("**Наименования в рамке**")
        lines.append("")
        for t in titles:
            lines.append(f"- {t}")
        lines.append("")

    other = [str(ln).strip() for ln in (stamp.get("other_lines") or []) if str(ln).strip()]
    if other:
        lines.append("**Прочий текст рамки**")
        lines.append("")
        lines.append("```text")
        lines.extend(other[:80])
        lines.append("```")
        lines.append("")

    return lines


def facts_to_markdown(facts: dict[str, Any], *, mode: str = "full") -> str:
    """mode: full | analysis | text — text без нормативов, analysis с нормативами."""
    if not facts.get("ok"):
        return f"**Ошибка:** {facts.get('error', 'Не удалось разобрать PDF')}\n"

    include_normatives = mode in ("full", "analysis")

    stamp = facts.get("stamp") or {}
    if report_stamp_frame_only():
        lines = _render_universal_stamp(stamp) if stamp.get("source") == "stamp_universal" else []
        if not lines and stamp:
            lines = ["**Основная надпись (рамка листа)**", ""]
            for item in stamp.get("kv") or []:
                f, v = str(item.get("field") or ""), str(item.get("value") or "")
                if f and v:
                    lines.append(f"**{f}:** {v}")
        if lines:
            return "\n".join(lines).strip() + "\n"
        return "**Основная надпись:** не удалось прочитать рамку.\n"

    lines: list[str] = []
    tables = list(facts.get("tables") or [])

    kind_order = ("specification", "explication", "legend", "table")
    by_kind: dict[str, list[dict]] = {k: [] for k in kind_order}
    for tbl in tables:
        k = str(tbl.get("kind") or "table")
        by_kind.setdefault(k, []).append(tbl)
        if k not in kind_order:
            by_kind.setdefault("table", []).append(tbl)

    idx = 0
    for kind in kind_order:
        for tbl in by_kind.get(kind) or []:
            rows = tbl.get("rows") or []
            if not rows:
                continue
            idx += 1
            heading = _table_heading(tbl, idx)
            if kind == "legend":
                lines.append("**Условные обозначения**")
            else:
                lines.append(f"**{heading}**")
            lines.append("")
            if kind == "explication":
                lines.extend(
                    _render_dict_table(
                        [
                            {
                                "№": r.get("plan_number"),
                                "Наименование": r.get("name"),
                                "Координаты": r.get("grid"),
                                "Примечание": r.get("note"),
                            }
                            for r in rows
                        ],
                        col_order=("№", "Наименование", "Координаты", "Примечание"),
                    )
                )
            elif kind == "specification":
                lines.extend(_render_dict_table(rows, col_order=_SPEC_COLS))
            elif kind == "legend":
                leg_rows = []
                for r in rows:
                    sym = str(r.get("symbol") or "").strip()
                    note = str(r.get("note") or "—")
                    if sym in ("—", "") or "графическ" in sym.casefold():
                        sym = "(графический символ)"
                    leg_rows.append({"Обозначение": sym, "Наименование": note})
                lines.extend(
                    _render_dict_table(leg_rows, col_order=("Обозначение", "Наименование"))
                )
            else:
                lines.extend(_render_dict_table(rows))
            lines.append("")

    notes = facts.get("sheet_notes") or {}
    tt_sections = (notes.get("sections") or []) if is_technical_requirements_notes(notes) else []
    notes_title = str(notes.get("title") or "").strip()
    if tt_sections:
        lines.append(f"**{notes_title or 'Технические требования'}**")
        lines.append("")
        lines.extend(_render_tt(tt_sections))
    notes_full = str(notes.get("full_text") or "").strip()
    if notes_full:
        from belener.body_filter import body_text_usable, filter_body_text

        notes_full = filter_body_text(notes_full)
    if (
        report_include_body_text()
        and notes_full
        and body_text_usable(notes_full)
        and (
            not tt_sections
            or len(notes_full) > len("\n".join(str(s.get("text") or "") for s in tt_sections)) + 80
        )
    ):
        lines.append(f"**{notes_title or 'Подписи и текст на схеме'}**")
        lines.append("")
        lines.append("```text")
        lines.append(notes_full[:50000])
        lines.append("```")
        lines.append("")

    if report_include_body_text():
        text_block_lines = _render_text_blocks(facts.get("text_blocks") or [])
        if text_block_lines:
            lines.extend(text_block_lines)

    stamp = facts.get("stamp") or {}
    lines.append("")
    lines.append("**Основная надпись (рамка)**")
    lines.append("")
    if stamp.get("source") == "stamp_universal":
        uni = _render_universal_stamp(stamp)
        lines.extend(uni[2:] if len(uni) > 2 else uni)
    elif _stamp_has_content(stamp):
        sig = _ordered_signatures(stamp.get("signatures") or [])
        org = next(
            (x.get("value") for x in stamp.get("kv") or [] if "организ" in str(x.get("field", "")).casefold()),
            "",
        )
        from belener.parse import _is_garbage_kv

        if org and str(org).strip() not in ("", "—") and not _is_garbage_kv("Организация", str(org)):
            lines.append(f"**{org}**")
            lines.append("")

        titles = [
            str(t).strip()
            for t in (stamp.get("titles") or [])
            if _usable_stamp_title(str(t))
        ]
        if titles:
            lines.append("**Наименование документа**")
            lines.append("")
            for t in titles[:4]:
                lines.append(f"- {t}")
            lines.append("")

        for item in stamp.get("kv") or []:
            field = str(item.get("field") or "").strip()
            val = str(item.get("value") or "").strip()
            if field and val and val != "—":
                if "организ" in field.casefold():
                    continue
                lines.append(f"**{field}:** {val}")

        if sig:
            lines.append("")
            if report_markdown_tables():
                doc_cipher = next(
                    (
                        x.get("value", "")
                        for x in stamp.get("kv") or []
                        if "обознач" in str(x.get("field", "")).casefold()
                    ),
                    "",
                )
                lines.extend(
                    _md_table(
                        ["Роль", "Фамилия", "Дата", "Обозначение документа"],
                        [
                            [
                                str(s.get("role") or "—"),
                                str(s.get("name") or "—"),
                                str(s.get("date") or "—"),
                                doc_cipher or "—",
                            ]
                            for s in sig
                        ],
                    )
                )
            else:
                lines.extend(
                    _ascii_table(
                        ["Роль", "Фамилия", "Дата"],
                        [[s.get("role"), s.get("name"), s.get("date")] for s in sig],
                    )
                )
    else:
        lines.append("*(рамка не распознана — сверьте с PDF.)*")

    normative = facts.get("normative_refs") or []
    if include_normatives and normative:
        lines.append("")
        lines.append("**Нормативные документы (ГОСТ, ОСТ, СТП, ТУ и др.)**")
        lines.append("")
        compact = report_normative_compact()
        if report_markdown_tables():
            if compact:
                lines.extend(
                    _md_table(
                        ["Тип", "Обозначение"],
                        [[str(n.get("kind") or "—"), str(n.get("ref") or "—")] for n in normative],
                    )
                )
            else:
                lines.extend(
                    _md_table(
                        ["Тип", "Обозначение", "Контекст на листе"],
                        [
                            [
                                str(n.get("kind") or "—"),
                                str(n.get("ref") or "—"),
                                str(n.get("context") or n.get("ref") or "—"),
                            ]
                            for n in normative
                        ],
                    )
                )
        else:
            if compact:
                lines.extend(
                    _ascii_table(
                        ["Тип", "Обозначение"],
                        [[n.get("kind"), n.get("ref")] for n in normative],
                    )
                )
            else:
                lines.extend(
                    _ascii_table(
                        ["Тип", "Обозначение", "Контекст"],
                        [
                            [n.get("kind"), n.get("ref"), n.get("context")]
                            for n in normative
                        ],
                    )
                )
        lines.append("")

    if report_include_quality():
        quality_lines = _render_quality(facts.get("quality") or {})
        if quality_lines:
            lines.append("")
            lines.extend(quality_lines)

    if report_include_full_text_layer():
        full_text = full_text_pages_to_markdown(facts.get("full_text_pages") or [])
        if full_text:
            lines.append("")
            lines.append(full_text.rstrip())

    return "\n".join(lines).strip() + "\n"
