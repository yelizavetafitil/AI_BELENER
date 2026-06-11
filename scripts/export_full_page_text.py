#!/usr/bin/env python3
"""Один PDF → markdown «как в чате»: спецификация, легенда, указания, штамп (быстрый OCR по зонам)."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path("/app/data") if Path("/app/data").is_dir() else ROOT / "data"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402

from belener.config import stamp_block_dpi, table_dpi  # noqa: E402
from belener.discover import discover_sheet_zones  # noqa: E402
from belener.ocr import ocr_region  # noqa: E402
from belener.parse import (  # noqa: E402
    parse_legend,
    parse_numbered_notes,
    parse_revision_table,
    parse_specification,
    parse_stamp,
)
from belener.report import _esc_md_cell, _md_table  # noqa: E402
from belener.stamp_read import read_stamp_frame  # noqa: E402
from belener.zone_refine import refine_sheet_zones  # noqa: E402

_SPEC_COLS = ("Поз.", "Обозначение", "Наименование", "Кол.", "Масса ед., кг", "Примечание")


def _resolve_pdf(arg: str, search_dirs: list[Path]) -> Path | None:
    p = Path(arg)
    if p.is_file():
        return p
    key = arg.casefold().replace(" ", "")
    for d in search_dirs:
        if not d.is_dir():
            continue
        for f in d.glob("*.pdf"):
            if key in f.stem.casefold().replace(" ", ""):
                return f
    return None


def _spec_table_md(rows: list[dict]) -> list[str]:
    if not rows:
        return ["*(строки спецификации не распознаны — сверьте с PNG spec_right)*", ""]
    body = []
    for r in rows:
        body.append([_esc_md_cell(r.get(c, "—")) for c in _SPEC_COLS])
    return _md_table(list(_SPEC_COLS), body) + [""]


def _legend_md(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    lines = ["**Условные обозначения**", "", "Таблица 2", ""]
    headers = ("Обозначение", "Наименование")
    body = [[_esc_md_cell(r.get("symbol", "—")), _esc_md_cell(r.get("note", "—"))] for r in rows]
    lines.extend(_md_table(list(headers), body))
    lines.append("")
    return lines


def _notes_md(notes: list[str]) -> list[str]:
    if not notes:
        return []
    lines = [""]
    for n in notes:
        lines.append(n.strip())
        lines.append("")
    return lines


def _stamp_md(stamp: dict, stamp_ocr: str) -> list[str]:
    lines: list[str] = []
    rev = stamp.get("revisions") or parse_revision_table(stamp_ocr)
    if rev:
        keys = sorted({k for r in rev if isinstance(r, dict) for k in r})
        if keys:
            body = [[_esc_md_cell(r.get(k, "—")) for k in keys] for r in rev if isinstance(r, dict)]
            lines.extend(_md_table(keys, body))
            lines.append("")

    for item in stamp.get("kv") or []:
        f, v = str(item.get("field") or "").strip(), str(item.get("value") or "").strip()
        if f and v:
            lines.append(f"**{f}:** {v}")
    if stamp.get("titles"):
        lines.append("")
        for t in stamp.get("titles") or []:
            if str(t).strip():
                lines.append(f"**{str(t).strip()}**")
    sigs = stamp.get("signatures") or []
    if sigs:
        lines.append("")
        keys = sorted({k for s in sigs if isinstance(s, dict) for k in s if k != "sign"})
        if keys:
            body = [[_esc_md_cell(s.get(k, "—")) for k in keys] for s in sigs if isinstance(s, dict)]
            lines.extend(_md_table(keys, body))
    if not lines and stamp_ocr.strip():
        lines.append("```text")
        lines.append(stamp_ocr.strip()[:8000])
        lines.append("```")
    lines.append("")
    return lines


def _label_key(s: str) -> str:
    import re

    return re.sub(r"[\s_\-]+", "", (s or "").casefold())


def _labels_for_pdf(training: Path, stem: str) -> tuple[str, str]:
    labels = training / "labels"
    if not labels.is_dir():
        return "", ""
    spec, stamp = "", ""
    key = _label_key(stem)
    for p in labels.glob("*.txt"):
        base = p.stem
        name = _label_key(base)
        if key not in name and name not in key:
            continue
        if base.endswith("_spec_right"):
            spec = p.read_text(encoding="utf-8", errors="replace")
        elif base.endswith("_stamp_frame"):
            stamp = p.read_text(encoding="utf-8", errors="replace")
    return spec, stamp


def export_pdf(path: Path, *, use_labels: bool = False, training: Path | None = None) -> str:
    spec_text = ""
    stamp_text = ""
    stamp: dict = {}
    if use_labels and training:
        spec_text, stamp_text = _labels_for_pdf(training, path.stem)
    if not (use_labels and spec_text and stamp_text):
        doc = fitz.open(path)
        try:
            page = doc[0]
            zones = refine_sheet_zones(
                doc, discover_sheet_zones(doc, 0, page.rect, fast=True), 0, classify_with_ocr=False
            )
            spec_rect = zones.rects.get("spec_right") or zones.rects.get("tables_block")
            stamp_rect = zones.rects.get("stamp_frame") or zones.rects.get("stamp_block")
            if not spec_text and spec_rect is not None:
                spec_text = ocr_region(doc, 0, spec_rect, dpi=min(table_dpi(), 420), zone="spec_right")
            if not stamp_text and stamp_rect is not None:
                stamp_text = ocr_region(
                    doc, 0, stamp_rect, dpi=min(stamp_block_dpi(), 480), zone="stamp_frame"
                )
            if stamp_rect is not None and not use_labels:
                stamp = read_stamp_frame(
                    doc, stamp_rect, dpi=min(stamp_block_dpi(), 480), grid_rect=stamp_rect
                )
        finally:
            doc.close()
    if stamp_text and not stamp:
        stamp = parse_stamp(stamp_text)

    spec_rows = parse_specification(spec_text or "")
    legend_rows = parse_legend(spec_text or "")
    notes = parse_numbered_notes(spec_text or "")

    out: list[str] = [
        f"# Извлечённый текст — {path.name}",
        "",
        "## Страница 1",
        "",
        "```text",
        "1-1",
        "```",
        "",
        "Поз. | Обозначение | Наименование | Кол. | Масса ед., кг | Примечание",
        "--- | --- | --- | --- | --- | ---",
    ]
    for r in spec_rows:
        out.append(
            " | ".join(_esc_md_cell(r.get(c, "—")) for c in _SPEC_COLS)
        )
    out.append("")
    out.extend(_legend_md(legend_rows))
    out.extend(_notes_md(notes))
    out.append("Изм. | Кол. уч. | Описание | Лист | № док. | Подпись | Дата")
    out.append("--- | --- | --- | --- | --- | --- | ---")
    out.append("")
    out.extend(_stamp_md(stamp, stamp_text))
    out.append("")
    out.append("---")
    out.append(f"*OCR spec: {len(spec_text)} симв., строк спецификации: {len(spec_rows)}, указаний: {len(notes)}*")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Полный текст листа (зоны + OCR + парсеры)")
    ap.add_argument("pdf", help="PDF или фрагмент имени (1760, BNP_1760)")
    ap.add_argument("--dir", type=Path, default=ROOT, help="Корень поиска PDF")
    ap.add_argument("--scan", type=Path, default=Path("/workspace/scan"))
    ap.add_argument("--out", type=Path, default=DATA_ROOT / "benchmark")
    ap.add_argument(
        "--use-labels",
        action="store_true",
        help="Взять OCR из data/training/labels (быстро, без повторного OCR PDF)",
    )
    ap.add_argument("--training", type=Path, default=DATA_ROOT / "training")
    args = ap.parse_args()

    path = _resolve_pdf(args.pdf, [args.scan, args.dir, ROOT / "scan"])
    if path is None:
        print("PDF не найден:", args.pdf, file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"OCR + разбор {path.name} …", flush=True)
    t0 = time.monotonic()
    md = export_pdf(path, use_labels=args.use_labels, training=args.training)
    out_path = args.out / f"{path.stem}_full_text.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"Готово за {time.monotonic() - t0:.1f}s → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
