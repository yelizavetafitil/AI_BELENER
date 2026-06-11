#!/usr/bin/env python3
"""Один PDF → markdown в data/benchmark (для ответа без браузера)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path("/app/data") if Path("/app/data").is_dir() else ROOT / "data"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from belener.extract import extract_pdf_path  # noqa: E402
from belener.extract_report import extraction_to_markdown  # noqa: E402
from belener.faithful_audit import audit_drawing_faithful  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", help="Имя или путь PDF (в /workspace или абсолютный)")
    ap.add_argument("--dir", type=Path, default=ROOT)
    ap.add_argument("--out", type=Path, default=DATA_ROOT / "benchmark")
    args = ap.parse_args()

    path = Path(args.pdf)
    if not path.is_file():
        path = args.dir / args.pdf
    if not path.is_file():
        print("Файл не найден:", args.pdf, file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"Извлечение {path.name} … (~20–35 мин на CPU, не прерывайте)", flush=True)
    t0 = time.monotonic()
    try:
        facts = extract_pdf_path(str(path), path.name)
    except Exception as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2

    md = extraction_to_markdown(facts)
    stem = path.stem
    md_path = args.out / f"{stem}.md"
    json_path = args.out / f"{stem}.audit.json"
    md_path.write_text(md, encoding="utf-8")

    drawing = facts.get("drawing") or {}
    audit = audit_drawing_faithful(drawing)
    summary = {
        "file": path.name,
        "seconds": round(time.monotonic() - t0, 1),
        "pipeline": drawing.get("pipeline"),
        "ok": bool(drawing.get("ok")),
        **audit,
    }
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    flag = "OK" if audit.get("faithful_ok") else "FAIL"
    print(f"\n[{flag}] {path.name} за {summary['seconds']}s")
    print(f"  spec_rows={audit['spec_rows']} ungrounded={audit['ungrounded_count']} ocr_chars={audit['spec_ocr_chars']}")
    print(f"  Отчёт: {md_path}")
    print(f"  Аудит: {json_path}")
    return 0 if drawing.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
