"""Правка YOLO-разметки зон (legend не на схеме, spec снизу)."""

from __future__ import annotations


def parse_yolo_line(line: str) -> tuple[int, float, float, float, float] | None:
    parts = line.strip().split()
    if len(parts) != 5:
        return None
    try:
        return int(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
    except ValueError:
        return None


def format_yolo_box(cls: int, cx: float, cy: float, bw: float, bh: float) -> str:
    return f"{cls} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def refine_yolo_boxes(boxes: list[tuple[int, float, float, float, float]]) -> list[tuple[int, float, float, float, float]]:
    specs: list[tuple[int, float, float, float, float]] = []
    legends: list[tuple[int, float, float, float, float]] = []
    stamps: list[tuple[int, float, float, float, float]] = []

    for cls, cx, cy, bw, bh in boxes:
        if cls == 1:
            stamps.append((cls, cx, cy, bw, bh))
        elif cls == 2:
            legends.append((cls, cx, cy, bw, bh))
        else:
            specs.append((cls, cx, cy, bw, bh))

    legends = [b for b in legends if not (b[2] > 0.34 and b[4] > 0.22 and b[2] < 0.72)]

    promoted: list[tuple[int, float, float, float, float]] = []
    kept_specs: list[tuple[int, float, float, float, float]] = []
    for b in specs:
        _, cx, cy, bw, bh = b
        # верхний правый узкий блок — легенда, не перечень
        if cy < 0.32 and bh < 0.28 and cx > 0.52:
            promoted.append((2, cx, cy, bw, bh))
        else:
            kept_specs.append(b)
    specs = kept_specs
    legends.extend(promoted)

    if len(specs) >= 2:
        left = [b for b in specs if b[1] < 0.45]
        right = [b for b in specs if b[1] >= 0.45]
        if left and right:
            specs = [
                max(left, key=lambda b: b[4]),
                max(right, key=lambda b: b[2] * b[4]),
            ]
        elif len(specs) > 2:
            specs = sorted(specs, key=lambda b: b[2] * b[4], reverse=True)[:2]

    # большой spec справа в верхней трети — поле схемы, не перечень
    specs = [b for b in specs if not (b[2] < 0.28 and b[4] > 0.30 and b[1] > 0.52)]

    if len(legends) > 1:
        legends = [min(legends, key=lambda b: b[2])]

    out: list[tuple[int, float, float, float, float]] = []
    out.extend(specs)
    out.extend(stamps)
    out.extend(legends)
    return out
