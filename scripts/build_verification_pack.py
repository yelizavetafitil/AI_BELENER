#!/usr/bin/env python3
"""HTML-обзор для проверки: кроп + OCR-текст + метрики quick_scan."""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path("/app/data") if Path("/app/data").is_dir() else ROOT / "data"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--training", type=Path, default=DATA_ROOT / "training")
    ap.add_argument("--quick", type=Path, default=DATA_ROOT / "benchmark" / "quick_scan.json")
    ap.add_argument("--out", type=Path, default=DATA_ROOT / "benchmark" / "verify.html")
    args = ap.parse_args()

    quick: dict[str, dict] = {}
    if args.quick.is_file():
        for row in json.loads(args.quick.read_text(encoding="utf-8")):
            quick[row.get("file", "")] = row

    manifest_path = args.training / "manifest.jsonl"
    entries: list[dict] = []
    if manifest_path.is_file():
        for ln in manifest_path.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                entries.append(json.loads(ln))

    by_file: dict[str, list[dict]] = {}
    for e in entries:
        if e.get("zone") in ("spec_right", "stamp_frame"):
            by_file.setdefault(e.get("file", ""), []).append(e)

    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        "<title>Belener — проверка кропов</title>",
        "<style>body{font-family:Segoe UI,sans-serif;margin:1rem}",
        "h2{border-top:1px solid #ccc;padding-top:1rem}",
        ".grid{display:flex;gap:1rem;flex-wrap:wrap}",
        ".card{border:1px solid #ddd;padding:.5rem;max-width:48%}",
        "img{max-width:100%;height:auto;border:1px solid #eee}",
        "pre{background:#f6f6f6;padding:.5rem;white-space:pre-wrap;font-size:12px}",
        ".ok{color:green}.fail{color:#c00}</style></head><body>",
        "<h1>Проверка OCR по кропам</h1>",
        "<p>Откройте в браузере. Сравните картинку и текст. Исправления → Label Studio → обучение.</p>",
    ]

    files = sorted(set(quick) | set(by_file))
    for fname in files:
        q = quick.get(fname, {})
        st = "ok" if q.get("faithful_ok") else "fail"
        parts.append(f"<h2>{html.escape(fname)} <span class='{st}'>")
        parts.append(
            f"rows={q.get('spec_rows','?')} ungrounded={q.get('ungrounded_count','?')} "
            f"ocr_chars={q.get('spec_ocr_chars','?')}</span></h2>"
        )
        if q.get("spec_preview"):
            parts.append("<h3>Быстрый OCR spec (preview)</h3>")
            parts.append(f"<pre>{html.escape(q['spec_preview'])}</pre>")
        for e in by_file.get(fname, []):
            img_rel = e.get("image", "")
            img_path = args.training / img_rel.replace("/", "\\").replace("\\", "/")
            label = args.training / (e.get("label_file") or "")
            text = ""
            if label.is_file():
                text = label.read_text(encoding="utf-8", errors="replace")
            elif e.get("ocr_baseline"):
                text = e["ocr_baseline"]
            parts.append("<div class='card'>")
            parts.append(f"<h3>{html.escape(e.get('zone',''))}</h3>")
            if img_path.is_file():
                rel = Path("..") / "training" / img_rel
                parts.append(f"<img src='{html.escape(str(rel).replace(chr(92),'/'))}' alt='crop'>")
            parts.append(f"<pre>{html.escape(text[:3000] or '(пусто — запустите ocr_training_crops.py)')}</pre>")
            parts.append("</div>")

    parts.append("</body></html>")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(parts), encoding="utf-8")
    print("Wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
