"""Fast PDF drawing quality checks: fonts and objects outside page bounds."""

from __future__ import annotations

from collections import Counter
from typing import Any

import fitz


_EDGE_TOL_PT = 2.0
_MAX_ISSUES = 80


def _rect_tuple(rect: fitz.Rect) -> list[float]:
    return [round(float(rect.x0), 2), round(float(rect.y0), 2), round(float(rect.x1), 2), round(float(rect.y1), 2)]


def _outside(inner: fitz.Rect, outer: fitz.Rect, *, tol: float = _EDGE_TOL_PT) -> bool:
    return (
        inner.x0 < outer.x0 - tol
        or inner.y0 < outer.y0 - tol
        or inner.x1 > outer.x1 + tol
        or inner.y1 > outer.y1 + tol
    )


def _font_rows(font_counter: Counter[tuple[str, float]]) -> list[dict[str, str]]:
    rows = []
    for (font, size), count in font_counter.most_common(30):
        rows.append({"Шрифт": font or "—", "Размер": f"{size:g}", "Фрагментов": str(count)})
    return rows


def analyze_pdf_quality(doc: fitz.Document) -> dict[str, Any]:
    """Return lightweight checks for CAD PDF exports.

    Scanned PDFs usually contain one page image and no text spans. In that case
    font lists will be empty, but image bounds are still checked.
    """

    pages: list[dict[str, Any]] = []
    all_fonts: Counter[tuple[str, float]] = Counter()
    issues_total = 0

    for page_index in range(doc.page_count):
        page = doc[page_index]
        page_rect = page.rect
        page_fonts: Counter[tuple[str, float]] = Counter()
        issues: list[dict[str, Any]] = []

        def add_issue(kind: str, rect: fitz.Rect, detail: str) -> None:
            nonlocal issues_total
            if len(issues) >= _MAX_ISSUES:
                return
            issues.append({"type": kind, "detail": detail, "bbox": _rect_tuple(rect)})
            issues_total += 1

        try:
            text_dict = page.get_text("dict") or {}
            for block in text_dict.get("blocks") or []:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines") or []:
                    for span in line.get("spans") or []:
                        text = str(span.get("text") or "").strip()
                        if not text:
                            continue
                        font = str(span.get("font") or "").strip()
                        size = round(float(span.get("size") or 0.0), 1)
                        page_fonts[(font, size)] += 1
                        all_fonts[(font, size)] += 1
                        rect = fitz.Rect(span.get("bbox") or (0, 0, 0, 0))
                        if _outside(rect, page_rect):
                            add_issue("text_outside_page", rect, text[:120])
        except Exception:
            add_issue("text_check_failed", page_rect, "Не удалось проверить текстовый слой")

        try:
            for item in page.get_drawings() or []:
                rect = item.get("rect")
                if rect is None:
                    continue
                r = fitz.Rect(rect)
                if _outside(r, page_rect):
                    add_issue("vector_outside_page", r, "Векторный объект выходит за границы листа")
        except Exception:
            add_issue("vector_check_failed", page_rect, "Не удалось проверить векторные объекты")

        try:
            for img in page.get_images(full=True) or []:
                xref = img[0]
                for rect in page.get_image_rects(xref) or []:
                    r = fitz.Rect(rect)
                    if _outside(r, page_rect):
                        add_issue("image_outside_page", r, "Изображение выходит за границы листа")
        except Exception:
            add_issue("image_check_failed", page_rect, "Не удалось проверить изображения")

        pages.append(
            {
                "index": page_index + 1,
                "page_size_pt": [round(float(page_rect.width), 2), round(float(page_rect.height), 2)],
                "fonts": _font_rows(page_fonts),
                "issues": issues,
            }
        )

    return {
        "ok": issues_total == 0,
        "issue_count": issues_total,
        "fonts": _font_rows(all_fonts),
        "pages": pages,
    }
