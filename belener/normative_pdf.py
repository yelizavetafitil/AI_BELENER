from __future__ import annotations

import io
import os
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _register_fonts() -> tuple[str, str]:
    pairs = [
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ),
        (
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        ),
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
        ("C:/Windows/Fonts/calibri.ttf", "C:/Windows/Fonts/calibrib.ttf"),
    ]
    for regular, bold in pairs:
        if not os.path.isfile(regular):
            continue
        try:
            pdfmetrics.registerFont(TTFont("BelenerSans", regular))
            if os.path.isfile(bold):
                pdfmetrics.registerFont(TTFont("BelenerSans-Bold", bold))
                return "BelenerSans", "BelenerSans-Bold"
            return "BelenerSans", "BelenerSans"
        except Exception:
            continue
    return "Helvetica", "Helvetica-Bold"


_TWO_LINE_HEADERS = (
    ("Введен", "Стройдок"),
    ("Отменен", "Стройдок"),
    ("Введен", "ТНПА"),
    ("Отменен", "ТНПА"),
)


def _esc(text: Any) -> str:
    s = str(text or "")
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _format_header_html(header: Any) -> str:
    """Двухстрочные заголовки дат: «Введен» + «Стройдок|ТНПА» без пробела между строками."""
    text = str(header or "—").strip() or "—"
    # Явный перевод строки из клиента (spans / <br>)
    parts = [p.strip() for p in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if p.strip()]
    if len(parts) >= 2:
        return "<br/>".join(_esc(p) for p in parts[:2])
    flat = "".join(text.split())
    for line1, line2 in _TWO_LINE_HEADERS:
        if flat == f"{line1}{line2}":
            return f"{_esc(line1)}<br/>{_esc(line2)}"
    return _esc(text)


def _compute_summary_from_rows(payload: dict[str, Any]) -> str:
    rows = payload.get("rows") or []
    if not rows:
        return ""
    total = len(rows)
    found_ips = 0
    found_tnpa = 0
    active = 0
    for row in rows:
        cells = row.get("cells") or []
        if len(cells) >= 3 and str(cells[2].get("href") or "").strip():
            found_ips += 1
        if len(cells) >= 4 and str(cells[3].get("href") or "").strip():
            found_tnpa += 1
        if len(cells) >= 9:
            status = str(cells[8].get("text") or "").strip().casefold()
            if status == "актуален" or "актуален" in status:
                active += 1
    return f"Всего в документе: {total}; найдено в Стройдок: {found_ips}; найдено в ТНПА: {found_tnpa}; актуально: {active}"


def _parse_summary(summary: str) -> tuple[str, str, str, str]:
    import re

    m = re.search(
        r"Всего в документе:\s*(\d+);\s*найдено в (?:ИПС|Стройдок):\s*(\d+);\s*найдено в ТНПА:\s*(\d+);\s*актуально:\s*(\d+)",
        summary or "",
        re.I,
    )
    if not m:
        return "", "", "", ""
    return m.group(1), m.group(2), m.group(3), m.group(4)


def _sheet_from_meta(meta_lines: list[str]) -> str:
    import re

    for line in meta_lines:
        m = re.match(r"^Листов в файле:\s*(\d+)", line.strip(), re.I)
        if m:
            return m.group(1)
    for line in meta_lines:
        m = re.match(r"^Листов:\s*(\d+)\s*$", line.strip(), re.I)
        if m:
            return m.group(1)
    for line in meta_lines:
        m = re.match(r"^Лист:\s*(\d+)\s*$", line.strip(), re.I)
        if m:
            return m.group(1)
    return ""


def build_normative_pdf_bytes(payload: dict[str, Any]) -> bytes:
    # Палитра чата: белые карточки, серые границы, синие ссылки
    c_surface = colors.HexColor("#ffffff")
    c_border = colors.HexColor("#dde5ef")
    c_text = colors.HexColor("#1a2332")
    c_text_sec = colors.HexColor("#5a6b82")
    c_text_muted = colors.HexColor("#8b9bb0")
    c_accent = colors.HexColor("#0f766e")
    c_row_active = colors.HexColor("#e8f5ec")
    c_row_canceled = colors.HexColor("#fce9ea")
    c_row_replaced = colors.HexColor("#fff8e6")
    pad_h = 10
    pad_v = 8

    font_name, font_bold = _register_fonts()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=9 * mm,
        rightMargin=9 * mm,
        topMargin=10 * mm,
        bottomMargin=9 * mm,
        title=str(payload.get("title") or "Таблица нормативов"),
    )
    avail_w = landscape(A4)[0] - doc.leftMargin - doc.rightMargin

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "BelenerTitle",
        parent=styles["Heading2"],
        fontName=font_name,
        fontSize=14,
        leading=17,
        textColor=c_text,
        spaceAfter=0,
    )
    subtitle_style = ParagraphStyle(
        "BelenerSubtitle",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=8,
        leading=10,
        textColor=c_text_sec,
    )
    card_label_style = ParagraphStyle(
        "BelenerCardLabel",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=7.0,
        leading=8.2,
        textColor=c_text_sec,
        alignment=TA_CENTER,
    )
    card_value_style = ParagraphStyle(
        "BelenerCardValue",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=12,
        leading=14,
        textColor=c_accent,
        alignment=TA_CENTER,
    )
    info_label_style = ParagraphStyle(
        "BelenerInfoLabel",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=7.4,
        leading=8.8,
        textColor=c_text_muted,
    )
    info_value_style = ParagraphStyle(
        "BelenerInfoValue",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=8.4,
        leading=10,
        textColor=c_accent,
    )
    info_file_style = ParagraphStyle(
        "BelenerInfoFile",
        parent=info_value_style,
        textColor=c_accent,
    )
    cell_style = ParagraphStyle(
        "BelenerCell",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=8.2,
        leading=9.6,
        textColor=c_text,
    )
    header_style = ParagraphStyle(
        "BelenerHeader",
        parent=cell_style,
        fontName=font_bold,
        fontSize=8.2,
        leading=9.6,
        alignment=TA_CENTER,
        textColor=colors.white,
    )

    meta_lines = [str(x).strip() for x in (payload.get("meta") or []) if str(x).strip()]
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        summary = _compute_summary_from_rows(payload)

    story = []
    total, found_stn, found_tnpa, active = _parse_summary(summary)
    file_name = next((x.split(":", 1)[1].strip() for x in meta_lines if x.startswith("Файл:")), "")
    sheet = _sheet_from_meta(meta_lines)
    check_date = next((x.split(":", 1)[1].strip() for x in meta_lines if x.startswith("Дата проверки актуальности:")), "")

    header_table = Table(
        [[
            Paragraph(_esc(payload.get("title") or "Таблица нормативов"), title_style),
            Paragraph("Экспорт по результатам проверки нормативных документов", subtitle_style),
        ]],
        colWidths=[avail_w * 0.67, avail_w * 0.33],
    )
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), c_surface),
        ("BOX", (0, 0), (-1, -1), 0.5, c_border),
        ("LEFTPADDING", (0, 0), (-1, -1), pad_h + 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), pad_h + 2),
        ("TOPPADDING", (0, 0), (-1, -1), pad_v + 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), pad_v + 2),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 5))

    info_parts = [16.0, 78.0, 16.0, 18.0, 20.0, 32.0]
    info_scale = avail_w / (sum(info_parts) * mm)
    info_col_widths = [p * mm * info_scale for p in info_parts]
    info_table = Table(
        [[
            Paragraph("<b>Файл</b>", info_label_style),
            Paragraph(_esc(file_name or "—"), info_file_style),
            Paragraph("<b>Листов</b>", info_label_style),
            Paragraph(f"<b>{_esc(sheet or '—')}</b>", info_value_style),
            Paragraph("<b>Проверка</b>", info_label_style),
            Paragraph(f"<b>{_esc(check_date or '—')}</b>", info_value_style),
        ]],
        colWidths=info_col_widths,
    )
    info_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), c_surface),
        ("BOX", (0, 0), (-1, -1), 0.5, c_border),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), pad_h),
        ("RIGHTPADDING", (0, 0), (-1, -1), pad_h),
        ("TOPPADDING", (0, 0), (-1, -1), pad_v),
        ("BOTTOMPADDING", (0, 0), (-1, -1), pad_v),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 4))

    stat_w1 = avail_w / 4.0
    stat_w2 = avail_w / 4.0
    stat_w3 = avail_w / 4.0
    stat_w4 = avail_w - stat_w1 - stat_w2 - stat_w3
    stats_table = Table(
        [
            [
                Paragraph(_esc("Найдено"), card_label_style),
                Paragraph(_esc("В Стройдок"), card_label_style),
                Paragraph(_esc("В ТНПА"), card_label_style),
                Paragraph(_esc("Актуальны"), card_label_style),
            ],
            [
                Paragraph(f"<b>{_esc(total or '—')}</b>", card_value_style),
                Paragraph(f"<b>{_esc(found_stn or '—')}</b>", card_value_style),
                Paragraph(f"<b>{_esc(found_tnpa or '—')}</b>", card_value_style),
                Paragraph(f"<b>{_esc(active or '—')}</b>", card_value_style),
            ],
        ],
        colWidths=[stat_w1, stat_w2, stat_w3, stat_w4],
    )
    stats_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), c_surface),
        ("BOX", (0, 0), (-1, -1), 0.5, c_border),
        ("LINEAFTER", (0, 0), (0, -1), 0.5, c_border),
        ("LINEAFTER", (1, 0), (1, -1), 0.5, c_border),
        ("LINEAFTER", (2, 0), (2, -1), 0.5, c_border),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), pad_h),
        ("RIGHTPADDING", (0, 0), (-1, -1), pad_h),
        ("TOPPADDING", (0, 0), (-1, 0), pad_v),
        ("BOTTOMPADDING", (0, 1), (-1, 1), pad_v + 2),
    ]))
    story.append(stats_table)
    story.append(Spacer(1, 5))

    headers = [str(x or "—") for x in (payload.get("headers") or [])]
    rows = payload.get("rows") or []
    table_data: list[list[Any]] = [
        [Paragraph(_format_header_html(h), header_style) for h in headers]
    ]

    row_fills: list[tuple[int, str]] = []
    for idx, row in enumerate(rows, start=1):
        fill = str(row.get("fill") or "").strip()
        cells = []
        for col_idx, cell in enumerate(row.get("cells") or []):
            text = _esc(cell.get("text") or "—")
            if cell.get("bold"):
                text = f"<b>{text}</b>"
            href = str(cell.get("href") or "").strip()
            if href:
                text = f'<link href="{_esc(href)}" color="#1d4ed8">{text}</link>'
            elif col_idx in (4, 5, 6, 7) and text not in ("—", "&mdash;"):
                text = f"<nobr>{text}</nobr>"
            cells.append(Paragraph(text, cell_style))
        table_data.append(cells)
        if fill:
            row_fills.append((idx, fill))

    # Ширины: даты шире (чтобы dd.mm.yyyy влезала), «Обозначение» уже
    widths_mm = [20.0, 40.0, 24.0, 22.0, 28.0, 28.0, 26.0, 26.0, 36.0]
    extra_mm = max(0.0, (avail_w / mm) - sum(widths_mm))
    widths_mm[1] += extra_mm * 0.35
    widths_mm[4] += extra_mm * 0.12
    widths_mm[5] += extra_mm * 0.12
    widths_mm[6] += extra_mm * 0.12
    widths_mm[7] += extra_mm * 0.12
    widths_mm[8] += extra_mm * 0.17
    col_widths = [w * mm for w in widths_mm]
    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    style_cmds: list[tuple[Any, ...]] = [
        ("BACKGROUND", (0, 0), (-1, 0), c_accent),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, c_accent),
        ("GRID", (0, 1), (-1, -1), 0.25, c_border),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), pad_h),
        ("RIGHTPADDING", (0, 0), (-1, -1), pad_h),
        ("TOPPADDING", (0, 0), (-1, -1), pad_v),
        ("BOTTOMPADDING", (0, 0), (-1, -1), pad_v),
    ]
    fill_map = {
        "active": c_row_active,
        "canceled": c_row_canceled,
        "replaced": c_row_replaced,
    }
    styled: set[int] = set()
    for row_idx, fill in row_fills:
        color = fill_map.get(fill)
        if color:
            style_cmds.append(("BACKGROUND", (0, row_idx), (-1, row_idx), color))
            styled.add(row_idx)
    for row_idx in range(1, len(table_data)):
        if row_idx not in styled:
            style_cmds.append(("BACKGROUND", (0, row_idx), (-1, row_idx), c_surface))
    table.setStyle(TableStyle(style_cmds))
    story.append(table)

    doc.build(story)
    return buf.getvalue()
