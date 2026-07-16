from __future__ import annotations

import io
import os
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _register_font() -> str:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                pdfmetrics.registerFont(TTFont("BelenerSans", path))
                return "BelenerSans"
            except Exception:
                continue
    return "Helvetica"


def _esc(text: Any) -> str:
    s = str(text or "")
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _compute_summary_from_rows(payload: dict[str, Any]) -> str:
    rows = payload.get("rows") or []
    if not rows:
        return ""
    total = len(rows)
    found_ips = 0
    active = 0
    for row in rows:
        cells = row.get("cells") or []
        if len(cells) >= 3 and str(cells[2].get("href") or "").strip():
            found_ips += 1
        if len(cells) >= 6:
            status = str(cells[5].get("text") or "").strip().casefold()
            if status == "актуален" or "актуален" in status:
                active += 1
    return f"Всего в документе: {total}; найдено в ИПС: {found_ips}; актуально: {active}"


def build_normative_pdf_bytes(payload: dict[str, Any]) -> bytes:
    font_name = _register_font()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title=str(payload.get("title") or "Таблица нормативов"),
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "BelenerTitle",
        parent=styles["Heading2"],
        fontName=font_name,
        fontSize=13,
        leading=16,
        spaceAfter=6,
    )
    meta_style = ParagraphStyle(
        "BelenerMeta",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#4b5563"),
        spaceAfter=8,
    )
    summary_style = ParagraphStyle(
        "BelenerSummary",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#4b5563"),
        spaceBefore=8,
        spaceAfter=4,
    )
    cell_style = ParagraphStyle(
        "BelenerCell",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=8.5,
        leading=10,
    )
    header_style = ParagraphStyle(
        "BelenerHeader",
        parent=cell_style,
        fontName=font_name,
        fontSize=8.5,
        leading=10,
    )

    meta_lines = [str(x).strip() for x in (payload.get("meta") or []) if str(x).strip()]
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        summary = _compute_summary_from_rows(payload)

    story = [
        Paragraph(_esc(payload.get("title") or "Таблица нормативов"), title_style),
    ]
    if meta_lines:
        story.append(Paragraph("<br/>".join(_esc(x) for x in meta_lines), meta_style))
    story.append(Spacer(1, 2))

    headers = [str(x or "—") for x in (payload.get("headers") or [])]
    rows = payload.get("rows") or []
    table_data: list[list[Any]] = [
        [Paragraph(f"<b>{_esc(h)}</b>", header_style) for h in headers]
    ]

    row_styles: list[tuple[int, str]] = []
    for idx, row in enumerate(rows, start=1):
        fill = str(row.get("fill") or "").strip()
        cells = []
        for cell in row.get("cells") or []:
            text = _esc(cell.get("text") or "—")
            if cell.get("bold"):
                text = f"<b>{text}</b>"
            href = str(cell.get("href") or "").strip()
            if href:
                text = f'<link href="{_esc(href)}" color="blue">{text}</link>'
            cells.append(Paragraph(text, cell_style))
        table_data.append(cells)
        if fill:
            row_styles.append((idx, fill))

    widths = payload.get("widths") or [14, 66, 18, 20, 20, 28]
    col_widths = [float(w) * mm for w in widths]
    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    style_cmds: list[tuple[Any, ...]] = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#111827")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d1d5db")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    fill_map = {
        "active": colors.HexColor("#dcfce7"),
        "canceled": colors.HexColor("#fee2e2"),
        "replaced": colors.HexColor("#fef3c7"),
    }
    for row_idx, fill in row_styles:
        color = fill_map.get(fill)
        if color:
            style_cmds.append(("BACKGROUND", (0, row_idx), (-1, row_idx), color))
    table.setStyle(TableStyle(style_cmds))
    story.append(table)

    if summary:
        story.append(
            KeepTogether(
                [
                    Spacer(1, 6),
                    Paragraph(_esc(summary), summary_style),
                ]
            )
        )

    doc.build(story)
    return buf.getvalue()
