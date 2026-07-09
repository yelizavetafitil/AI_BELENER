"""Нормативы из PDF: тот же tile OCR, что и для полного текста листа."""

from __future__ import annotations

from typing import Any

import fitz

from belener.normative_refs import merge_normative_refs_from_sources
from belener.tile_ocr import (
    PIPELINE,
    TILE_COLS,
    TILE_ROWS,
    extract_document_tiles,
    page_tile_jobs,
)

__all__ = [
    "PIPELINE",
    "TILE_COLS",
    "TILE_ROWS",
    "extract_normatives_document_crops",
    "extract_normatives_page_tiles",
    "page_tile_jobs",
    "_finalize_refs",
]


def _finalize_refs(all_sources: list[str]) -> list[dict[str, str]]:
    uniq = [s for s in all_sources if str(s or "").strip()]
    if not uniq:
        return []
    return merge_normative_refs_from_sources(*uniq)


def extract_normatives_page_tiles(doc, page_index, *, dpi, deadline, tile_max_sec, overlap_frac):
    from belener.tile_ocr import extract_page_tiles

    chunks, _, _ = extract_page_tiles(
        doc, page_index, dpi=dpi, deadline=deadline, tile_max_sec=tile_max_sec, overlap_frac=overlap_frac
    )
    return chunks


def extract_normatives_document_crops(
    doc: fitz.Document,
    filename: str = "document.pdf",
    *,
    pipeline_deadline: float | None = None,
) -> dict[str, Any]:
    tiles = extract_document_tiles(doc, filename, pipeline_deadline=pipeline_deadline)
    page_texts = tiles.get("page_texts") or []
    page_normative_refs = [
        _finalize_refs([text]) if str(text or "").strip() else []
        for text in page_texts
    ]
    refs = _finalize_refs(tiles["all_sources"])
    return {
        "ok": True,
        "filename": filename,
        "page_count": doc.page_count,
        "pages_processed": tiles.get("pages_processed", 0),
        "tiles_done": tiles.get("tiles_done"),
        "tiles_expected": tiles.get("tiles_expected"),
        "budget_exhausted": bool(tiles.get("budget_exhausted")),
        "elapsed_sec": tiles.get("elapsed_sec", 0.0),
        "pipeline": f"{PIPELINE}+normative",
        "normative_refs": refs,
        "page_normative_refs": page_normative_refs,
        "page_preview_words": tiles.get("page_preview_words") or [],
        "page_tile_zones": tiles.get("page_tile_zones") or [],
        "vision_model": None,
        "source_text_chars": sum(len(s) for s in page_texts),
        "page_texts": tiles["all_sources"],
        "drawing": None,
    }
