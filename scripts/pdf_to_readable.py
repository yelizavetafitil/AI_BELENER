#!/usr/bin/env python3
"""PDF-чертёж → .facts.json + .readable.md"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from belener.extract import extract_pdf_path
from belener.extract_report import extraction_to_markdown


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("pdf", type=Path)
    p.add_argument("-o", "--out-dir", type=Path, default=None, help="Каталог для .facts.json / .readable.md (по умолчанию — рядом с PDF)")
    args = p.parse_args()
    if not args.pdf.is_file():
        print(f"Не найден: {args.pdf}", file=sys.stderr)
        return 1

    facts = extract_pdf_path(str(args.pdf))
    md = extraction_to_markdown(facts)
    out_dir = args.out_dir or args.pdf.parent
    stem = args.pdf.stem
    facts_path = out_dir / f"{stem}.facts.json"
    md_path = out_dir / f"{stem}.readable.md"
    facts_path.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(md, encoding="utf-8")
    print(facts_path)
    print(md_path)
    if not facts.get("ok"):
        print(facts.get("error"), file=sys.stderr)
        return 2
    expl = len((facts.get("explication") or {}).get("rows") or [])
    leg = (facts.get("legend") or {}).get("row_count", 0)
    print(f"OK: экспликация {expl}, легенда {leg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
