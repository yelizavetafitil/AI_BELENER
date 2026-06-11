#!/usr/bin/env python3
"""
Правка YOLO-разметки: убрать legend на поле схемы, переназначить верхние spec → legend.

  python scripts/refine_yolo_labels.py
  python scripts/refine_yolo_labels.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_DS = ROOT / "data" / "training" / "yolo_zones"

from belener.yolo_labels import format_yolo_box, parse_yolo_line, refine_yolo_boxes


def refine_label_file(path: Path, *, dry_run: bool) -> int:
    raw = path.read_text(encoding="utf-8").splitlines()
    boxes: list[tuple[int, float, float, float, float]] = []
    for line in raw:
        p = parse_yolo_line(line)
        if p:
            boxes.append(p)
    if not boxes:
        return 0
    if len(boxes) <= 2:
        return 0
    refined = refine_yolo_boxes(boxes)
    if refined == boxes:
        return 0
    new_text = "\n".join(format_yolo_box(*b) for b in refined) + "\n"
    if dry_run:
        print(f"would fix {path.name}: {len(boxes)} -> {len(refined)} boxes")
    else:
        path.write_text(new_text, encoding="utf-8")
        print(f"fixed {path.name}: {len(boxes)} -> {len(refined)} boxes")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=DEFAULT_DS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    n = 0
    for sub in ("labels/train", "labels/val"):
        d = args.data / sub
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.txt")):
            n += refine_label_file(path, dry_run=args.dry_run)
    print(f"{'dry-run: ' if args.dry_run else ''}{n} label files updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
