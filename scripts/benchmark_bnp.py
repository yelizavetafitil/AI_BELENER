#!/usr/bin/env python3
"""Бенчмарк извлечения на эталонных BNP*.pdf (локально, без сети)."""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from belener.extract import extract_pdf_path  # noqa: E402
from belener.extract_report import extraction_to_markdown  # noqa: E402


def _summary(facts: dict) -> dict:
    d = facts.get("drawing") or {}
    stamp = d.get("stamp") or {}
    tables = d.get("tables") or []
    notes = d.get("sheet_notes") or {}
    return {
        "ok": bool(d.get("ok")),
        "pipeline": d.get("pipeline"),
        "stamp_kv": len(stamp.get("kv") or []),
        "signatures": len(stamp.get("signatures") or []),
        "tables": len(tables),
        "table_rows": sum(len(t.get("rows") or []) for t in tables),
        "tt_sections": len(notes.get("sections") or []),
        "md_chars": 0,
        "warnings": (d.get("warnings") or [])[:3],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(ROOT), help="Каталог с BNP*.pdf")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--md-out", default="", help="Каталог для markdown-отчётов")
    args = ap.parse_args()

    pattern = os.path.join(args.dir, "BNP*.pdf")
    pdfs = sorted(set(glob.glob(pattern) + glob.glob(os.path.join(args.dir, "BNP *.pdf"))))
    if args.limit:
        pdfs = pdfs[: args.limit]
    if not pdfs:
        print("Нет BNP*.pdf в", args.dir, file=sys.stderr)
        return 1

    rows: list[dict] = []
    for path in pdfs:
        name = os.path.basename(path)
        t0 = time.monotonic()
        try:
            facts = extract_pdf_path(path, name)
            md = extraction_to_markdown(facts)
            row = _summary(facts)
            row["file"] = name
            row["seconds"] = round(time.monotonic() - t0, 1)
            row["md_chars"] = len(md)
            row["error"] = facts.get("error") or (facts.get("drawing") or {}).get("error")
            if args.md_out:
                out = Path(args.md_out) / (Path(name).stem + ".md")
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(md, encoding="utf-8")
        except Exception as exc:
            row = {
                "file": name,
                "ok": False,
                "error": str(exc),
                "seconds": round(time.monotonic() - t0, 1),
            }
        rows.append(row)
        if not args.json:
            print(
                f"{row['file']}: ok={row.get('ok')} "
                f"tables={row.get('tables', 0)} rows={row.get('table_rows', 0)} "
                f"stamp={row.get('stamp_kv', 0)} tt={row.get('tt_sections', 0)} "
                f"md={row.get('md_chars', 0)} t={row.get('seconds')}s "
                f"pipe={row.get('pipeline', '-')}"
            )
            if row.get("error"):
                print("  error:", row["error"])

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0 if all(r.get("ok") for r in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
