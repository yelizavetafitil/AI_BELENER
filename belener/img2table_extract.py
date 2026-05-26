"""Извлечение таблиц через img2table (https://github.com/xavctn/img2table) — локально."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

import fitz

log = logging.getLogger("belener.img2table")


def img2table_available() -> bool:
    try:
        import img2table  # noqa: F401

        return True
    except ImportError:
        return False


def _df_to_rows(df) -> list[dict[str, str]]:
    import pandas as pd

    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    df = df.fillna("")
    cols = [str(c).strip() for c in df.columns]
    rows: list[dict[str, str]] = []
    for _, series in df.iterrows():
        row: dict[str, str] = {}
        for i, col in enumerate(cols):
            key = col if col else f"col{i}"
            row[key] = str(series.iloc[i]).strip()
        if any(v for v in row.values()):
            rows.append(row)
    return rows


def _infer_kind_from_columns(rows: list[dict[str, str]], title: str) -> str:
    blob = (title + " " + " ".join(" ".join(r.values()) for r in rows[:3])).casefold()
    if any(k in blob for k in ("поз", "обознач", "наимен", "кол.", "кол ")):
        return "specification"
    if "условн" in blob or "обозначен" in blob and "наимен" in blob:
        return "legend"
    if "экспликац" in blob or "координат" in blob:
        return "explication"
    return "table"


def extract_img2table_pdf(
    pdf_path: str | Path,
    *,
    page_index: int = 0,
) -> dict[str, Any]:
    """Все таблицы страницы PDF через img2table + Tesseract."""
    if not img2table_available():
        return {"ok": False, "tables": [], "table_text": ""}

    from img2table.document import PDF as Img2TablePDF
    from img2table.ocr import TesseractOCR

    from belener.config import ocr_lang

    path = Path(pdf_path)
    if not path.is_file():
        return {"ok": False, "tables": [], "table_text": ""}

    lang = ocr_lang()
    if "rus" not in lang and "+" not in lang:
        lang = f"{lang}+rus"
    ocr = TesseractOCR(n_threads=2, lang=lang)

    try:
        doc = Img2TablePDF(
            str(path),
            pages=[page_index],
            detect_rotation=False,
            pdf_text_extraction=False,
        )
        by_page = doc.extract_tables(
            ocr=ocr,
            implicit_rows=True,
            implicit_columns=True,
            borderless_tables=True,
            min_confidence=30,
            max_workers=1,
        )
        extracted = (by_page or {}).get(page_index) or []
    except Exception as e:
        log.warning("img2table failed: %s", e)
        return {"ok": False, "tables": [], "table_text": ""}

    sections: list[dict[str, Any]] = []
    texts: list[str] = []
    for i, tbl in enumerate(extracted or []):
        try:
            df = tbl.df
            rows = _df_to_rows(df)
            if not rows:
                continue
            title = ""
            if hasattr(tbl, "title") and tbl.title:
                title = str(tbl.title)
            kind = _infer_kind_from_columns(rows, title)
            sec = {
                "title": title,
                "kind": kind,
                "rows": rows,
                "table_number": f"Таблица {len(sections) + 1}",
                "source": "img2table",
            }
            sections.append(sec)
            texts.append(df.to_csv(sep="\t", index=False))
        except Exception:
            log.exception("img2table table %s parse failed", i)

    log.info("img2table tables=%s", len(sections))
    return {
        "ok": bool(sections),
        "tables": sections,
        "table_text": "\n\n".join(texts),
        "pipeline": "belener_img2table",
    }


def extract_img2table_rect(
    doc: fitz.Document,
    rect: fitz.Rect,
    *,
    page_index: int = 0,
    dpi: int = 400,
) -> dict[str, Any]:
    """Таблицы только внутри прямоугольника зоны (локально, img2table + Tesseract)."""
    if not img2table_available() or rect.is_empty:
        return {"ok": False, "tables": [], "table_text": ""}

    import tempfile
    from pathlib import Path

    from img2table.document import Image as Img2TableImage
    from img2table.ocr import TesseractOCR

    from belener.config import ocr_lang

    page = doc[page_index]
    clip = rect & page.rect
    if clip.is_empty or clip.width < 20 or clip.height < 20:
        return {"ok": False, "tables": [], "table_text": ""}

    eff_dpi = min(max(dpi, 300), 520)
    scale = eff_dpi / 72.0
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
            pix.save(tmp_path)
        lang = ocr_lang()
        if "rus" not in lang and "+" not in lang:
            lang = f"{lang}+rus"
        ocr = TesseractOCR(n_threads=2, lang=lang)
        idoc = Img2TableImage(tmp_path, detect_rotation=False)
        extracted = idoc.extract_tables(
            ocr=ocr,
            implicit_rows=True,
            implicit_columns=True,
            borderless_tables=False,
            min_confidence=35,
        )
    except Exception as e:
        log.warning("img2table rect failed: %s", e)
        return {"ok": False, "tables": [], "table_text": ""}
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass

    sections: list[dict[str, Any]] = []
    texts: list[str] = []
    for i, tbl in enumerate(extracted or []):
        try:
            df = tbl.df
            rows = _df_to_rows(df)
            if not rows:
                continue
            title = str(tbl.title) if getattr(tbl, "title", None) else ""
            kind = _infer_kind_from_columns(rows, title)
            sections.append(
                {
                    "title": title,
                    "kind": kind,
                    "rows": rows,
                    "table_number": f"Таблица {len(sections) + 1}",
                    "source": "img2table_zone",
                }
            )
            texts.append(df.to_csv(sep="\t", index=False))
        except Exception:
            log.exception("img2table rect table %s failed", i)

    return {
        "ok": bool(sections),
        "tables": sections,
        "table_text": "\n\n".join(texts),
        "pipeline": "belener_img2table_zone",
    }


def extract_img2table_doc(doc: fitz.Document, pdf_path: str, page_index: int = 0) -> dict[str, Any]:
    if doc.page_count <= 0:
        return {"ok": False, "tables": [], "table_text": ""}
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        path = tmp.name
    try:
        doc.save(path)
        return extract_img2table_pdf(path, page_index=page_index)
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass
