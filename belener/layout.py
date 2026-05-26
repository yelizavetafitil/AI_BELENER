"""Layout detection for scanned engineering drawings.

The goal is not to understand the drawing graphics, but to split a sheet into
readable regions: tables, stamp and standalone text/callout blocks.  This keeps
vision prompts small enough to preserve fine table text.
"""

from __future__ import annotations

from dataclasses import dataclass

import fitz
from PIL import Image, ImageOps


@dataclass
class LayoutBlock:
    kind: str
    rect: fitz.Rect
    label: str = ""
    score: int = 0


def _render_page(page: fitz.Page, dpi: int = 120) -> tuple[Image.Image, float]:
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return img, scale


def _threshold(img: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(img)
    return gray.point(lambda p: 0 if p < 185 else 255, mode="1")


def _groups(indices: list[int], gap: int = 2) -> list[tuple[int, int]]:
    if not indices:
        return []
    out: list[tuple[int, int]] = []
    start = prev = indices[0]
    for idx in indices[1:]:
        if idx - prev <= gap:
            prev = idx
            continue
        out.append((start, prev))
        start = prev = idx
    out.append((start, prev))
    return out


def _dark_runs_in_row(bin_img: Image.Image, y: int, *, min_len: int) -> list[tuple[int, int]]:
    w, _h = bin_img.size
    px = bin_img.load()
    runs: list[tuple[int, int]] = []
    x = 0
    while x < w:
        while x < w and px[x, y] != 0:
            x += 1
        x0 = x
        while x < w and px[x, y] == 0:
            x += 1
        if x - x0 >= min_len:
            runs.append((x0, x))
    return runs


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def _cluster_line_segments(
    segments: list[tuple[int, int, int]],
    *,
    min_lines: int,
    min_w: int,
    min_h: int,
) -> list[tuple[int, int, int, int, int]]:
    clusters: list[dict] = []
    for x0, x1, y in sorted(segments, key=lambda s: (s[2], s[0])):
        span = (x0, x1)
        best = None
        best_ov = 0
        for c in clusters:
            cspan = (c["x0"], c["x1"])
            ov = _overlap(span, cspan)
            if ov > best_ov and ov >= min((x1 - x0), (c["x1"] - c["x0"])) * 0.45:
                best = c
                best_ov = ov
        if best is None:
            clusters.append({"x0": x0, "x1": x1, "y0": y, "y1": y, "ys": [y]})
        else:
            best["x0"] = min(best["x0"], x0)
            best["x1"] = max(best["x1"], x1)
            best["y0"] = min(best["y0"], y)
            best["y1"] = max(best["y1"], y)
            best["ys"].append(y)

    out: list[tuple[int, int, int, int, int]] = []
    for c in clusters:
        line_count = len(_groups(sorted(set(c["ys"])), gap=4))
        w = c["x1"] - c["x0"]
        h = c["y1"] - c["y0"]
        if line_count >= min_lines and w >= min_w and h >= min_h:
            out.append((c["x0"], c["y0"], c["x1"], c["y1"], line_count))
    return out


def _merge_rects(rects: list[fitz.Rect], *, tol: float) -> list[fitz.Rect]:
    merged: list[fitz.Rect] = []
    for rect in rects:
        cur = fitz.Rect(rect)
        changed = True
        while changed:
            changed = False
            rest: list[fitz.Rect] = []
            for other in merged:
                grown = fitz.Rect(cur.x0 - tol, cur.y0 - tol, cur.x1 + tol, cur.y1 + tol)
                if grown.intersects(other):
                    cur |= other
                    changed = True
                else:
                    rest.append(other)
            merged = rest
        merged.append(cur)
    return merged


def _to_page_rect(rect_px: tuple[int, int, int, int], scale: float, page_rect: fitz.Rect, *, pad: float = 4.0) -> fitz.Rect:
    x0, y0, x1, y1 = rect_px
    r = fitz.Rect(x0 / scale - pad, y0 / scale - pad, x1 / scale + pad, y1 / scale + pad)
    return r & page_rect


def detect_layout_blocks(doc: fitz.Document, page_index: int = 0, *, dpi: int = 120) -> list[LayoutBlock]:
    page = doc[page_index]
    img, scale = _render_page(page, dpi=dpi)
    bin_img = _threshold(img)
    w, h = bin_img.size
    px = bin_img.load()

    dark_rows: list[int] = []
    row_min = max(80, int(w * 0.16))
    for y in range(h):
        cnt = 0
        for x in range(w):
            if px[x, y] == 0:
                cnt += 1
        if cnt >= row_min:
            dark_rows.append(y)

    segments: list[tuple[int, int, int]] = []
    min_run = max(70, int(w * 0.12))
    for y0, y1 in _groups(dark_rows, gap=2):
        y = (y0 + y1) // 2
        for x0, x1 in _dark_runs_in_row(bin_img, y, min_len=min_run):
            segments.append((x0, x1, y))

    raw = _cluster_line_segments(
        segments,
        min_lines=3,
        min_w=max(100, int(w * 0.14)),
        min_h=max(35, int(h * 0.05)),
    )
    rects = [_to_page_rect((x0, y0, x1, y1), scale, page.rect, pad=5) for x0, y0, x1, y1, _n in raw]
    rects = _merge_rects(rects, tol=max(page.rect.width, page.rect.height) * 0.015)

    blocks: list[LayoutBlock] = []
    for rect in rects:
        if rect.width < page.rect.width * 0.10 or rect.height < page.rect.height * 0.04:
            continue
        center_y = (rect.y0 + rect.y1) / 2
        center_x = (rect.x0 + rect.x1) / 2
        bottom = center_y > page.rect.y0 + page.rect.height * 0.60
        right = center_x > page.rect.x0 + page.rect.width * 0.45
        kind = "stamp" if bottom and right and rect.height >= page.rect.height * 0.12 else "table"
        blocks.append(LayoutBlock(kind=kind, rect=rect, score=int(rect.width * rect.height)))

    # Ensure there is always a stamp candidate and at least one table/body region.
    if not any(b.kind == "stamp" for b in blocks):
        bw = page.rect.width * 0.45
        bh = page.rect.height * 0.22
        blocks.append(LayoutBlock("stamp", fitz.Rect(page.rect.x1 - bw, page.rect.y1 - bh, page.rect.x1, page.rect.y1), "fallback"))

    table_blocks = [b for b in blocks if b.kind == "table"]
    if not table_blocks:
        stamp = next((b.rect for b in blocks if b.kind == "stamp"), None)
        y1 = stamp.y0 if stamp else page.rect.y1
        blocks.append(LayoutBlock("table", fitz.Rect(page.rect.x0, page.rect.y0, page.rect.x1, y1), "fallback"))

    # Add a narrow left callout/text strip if the drawing has one.
    left_strip = fitz.Rect(page.rect.x0, page.rect.y0, page.rect.x0 + page.rect.width * 0.14, page.rect.y1 * 0.92)
    if left_strip.width >= 40 and left_strip.height >= 100:
        blocks.append(LayoutBlock("text", left_strip, "left_strip"))

    order = {"table": 0, "text": 1, "stamp": 2}
    return sorted(blocks, key=lambda b: (order.get(b.kind, 9), b.rect.y0, b.rect.x0))
