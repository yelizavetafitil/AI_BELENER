#!/usr/bin/env python3
"""Диагностика blueprint_extract на PDF."""

import sys
from pathlib import Path

import fitz

from belener.blueprint_extract import blueprint_available, extract_blueprint_page
from belener.zones import build_zones


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/diagnose_blueprint.py <file.pdf>")
        sys.exit(1)
    path = Path(sys.argv[1])
    if not blueprint_available():
        print("opencv not available")
        sys.exit(2)
    doc = fitz.open(path)
    zones = build_zones(doc[0].rect)
    out = extract_blueprint_page(doc, 0, zones=zones)
    print("ok:", out.get("ok"), "blocks:", out.get("blocks"))
    print("tables:", len(out.get("tables") or []))
    stamp = out.get("stamp") or {}
    print("stamp kv:", len(stamp.get("kv") or []), "sigs:", len(stamp.get("signatures") or []))
    for t in out.get("tables") or []:
        print(" -", t.get("kind"), t.get("table_number"), "rows:", len(t.get("rows") or []))
    doc.close()


if __name__ == "__main__":
    main()
