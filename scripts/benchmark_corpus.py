#!/usr/bin/env python3
"""Бенчмарк на всех PDF корпуса (BNP, GCC, VR, …)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from belener.extract import extract_pdf_path  # noqa: E402
from belener.faithful_audit import audit_drawing_faithful  # noqa: E402
from belener.extract_report import extraction_to_markdown  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=ROOT)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "benchmark")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    pdfs = sorted(args.dir.glob("*.pdf"))
    if args.limit:
        pdfs = pdfs[: args.limit]
    if not pdfs:
        print("Нет PDF", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    summary_path = args.out / "summary.jsonl"
    rows: list[dict] = []

    with summary_path.open("w", encoding="utf-8") as fh:
        for path in pdfs:
            t0 = time.monotonic()
            try:
                facts = extract_pdf_path(str(path), path.name)
                d = facts.get("drawing") or {}
                audit = audit_drawing_faithful(d)
                md = extraction_to_markdown(facts)
                md_path = args.out / (path.stem + ".md")
                md_path.write_text(md, encoding="utf-8")
                row = {
                    "file": path.name,
                    "ok": bool(d.get("ok")),
                    "pipeline": d.get("pipeline"),
                    "seconds": round(time.monotonic() - t0, 1),
                    "tables": len(d.get("tables") or []),
                    "table_rows": sum(len(t.get("rows") or []) for t in (d.get("tables") or [])),
                    "stamp_sigs": len((d.get("stamp") or {}).get("signatures") or []),
                    **audit,
                }
            except Exception as exc:
                row = {"file": path.name, "ok": False, "error": str(exc)}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            rows.append(row)
            print(row)

    ok = sum(1 for r in rows if r.get("ok"))
    faithful = sum(1 for r in rows if r.get("faithful_ok"))
    print(f"\n{ok}/{len(rows)} ok, faithful {faithful}/{len(rows)} -> {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
