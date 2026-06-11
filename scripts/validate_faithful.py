#!/usr/bin/env python3
"""
Этап 1: проверка «честного» извлечения — строки BOM должны быть в OCR зон spec_*.

  python scripts/validate_faithful.py --dir /workspace
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from belener.extract import extract_pdf_path  # noqa: E402
from belener.faithful_audit import audit_drawing_faithful  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(ROOT))
    ap.add_argument("--pattern", default="*.pdf")
    ap.add_argument("--file", default="", help="Точный PDF в --dir (обход glob в PowerShell)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list-only", action="store_true", help="Только список найденных PDF")
    ap.add_argument("--fail-on-ungrounded", action="store_true")
    args = ap.parse_args()

    base = Path(args.dir)
    if args.file:
        one = base / args.file if not Path(args.file).is_absolute() else Path(args.file)
        pdfs = [one] if one.is_file() else []
    else:
        pdfs = sorted(base.glob(args.pattern))

    if not pdfs:
        print(f"Нет PDF в {base} по шаблону {args.pattern!r}", file=sys.stderr)
        print("Подсказка (PowerShell): --file BNP-1828-0-ЭМ1Л7.pdf  или  --pattern '*.pdf'", file=sys.stderr)
        try:
            sample = sorted(base.glob("*.pdf"))[:5]
            if sample:
                print("Есть в каталоге:", ", ".join(p.name for p in sample), file=sys.stderr)
        except OSError:
            pass
        return 1

    if args.list_only:
        for p in pdfs:
            print(p.name)
        return 0

    print(f"Найдено PDF: {len(pdfs)} — обработка займёт несколько минут на CPU…", flush=True)
    print(
        "Подсказка: для одного файла и готового отчёта используйте scripts/extract_one.py "
        "(результат в data/benchmark/).",
        flush=True,
    )
    rows: list[dict] = []
    bad = 0
    for path in pdfs:
        t0 = time.monotonic()
        print(f"→ {path.name} …", flush=True)
        try:
            facts = extract_pdf_path(str(path), path.name)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"[ERROR] {path.name}: {exc}", file=sys.stderr, flush=True)
            rows.append({"file": path.name, "ok": False, "error": str(exc)})
            bad += 1
            continue
        drawing = facts.get("drawing") or {}
        audit = audit_drawing_faithful(drawing)
        row = {
            "file": path.name,
            "ok": bool(drawing.get("ok")),
            "pipeline": drawing.get("pipeline"),
            "seconds": round(time.monotonic() - t0, 1),
            **audit,
        }
        if not audit.get("faithful_ok"):
            bad += 1
        rows.append(row)
        if not args.json:
            flag = "OK" if audit.get("faithful_ok") else "FAIL"
            print(
                f"[{flag}] {path.name} spec_rows={audit['spec_rows']} "
                f"ungrounded={audit['ungrounded_count']} ocr_chars={audit['spec_ocr_chars']} "
                f"({row['seconds']}s)"
            )
            for sample in audit.get("ungrounded_sample") or []:
                print("   ?", sample)

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(f"\nИтого: {len(rows)} PDF, проблемных: {bad}")

    if args.fail_on_ungrounded and bad:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
