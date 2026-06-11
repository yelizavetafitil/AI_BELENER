"""Многоракурсный OCR (как в промышленных пайплайнах для ГОСТ-чертежей).

Текст на чертеже часто под углами 0°/90°/270°; несколько проходов повышают recall
без облачных API. Совпадающие строки получают бонус; 0° — небольшой приоритет.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Callable

from PIL import Image

# Углы как в типовых инженерных пайплайнах (−90…270°)
DEFAULT_ANGLES: tuple[int, ...] = (-90, -45, 0, 45, 90, 180, 270)
FAST_ANGLES: tuple[int, ...] = (0, 90, 270)


def ocr_multiview_angles(*, fast: bool = False) -> tuple[int, ...]:
    from belener.config import ocr_multiview_fast

    return FAST_ANGLES if (fast or ocr_multiview_fast()) else DEFAULT_ANGLES


def _line_key(ln: str) -> str:
    return re.sub(r"\s+", " ", (ln or "").strip()).casefold()


def upscale_small_crop(img: Image.Image, *, min_height: int = 64) -> Image.Image:
    """Индексы допусков / мелкий Ra — upsample перед OCR (как в Habr)."""
    w, h = img.size
    if h >= min_height:
        return img
    scale = min_height / float(max(h, 1))
    return img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)


def _rotate_image(img: Image.Image, angle: int) -> Image.Image:
    if angle == 0:
        return img
    # PIL: положительный угол — против часовой
    return img.rotate(-angle, expand=True, resample=Image.Resampling.BICUBIC, fillcolor=255)


def merge_multiview_texts(angle_texts: list[tuple[int, str]]) -> str:
    """Слияние OCR с разных углов: голосование по нормализованным строкам."""
    if not angle_texts:
        return ""
    if len(angle_texts) == 1:
        return (angle_texts[0][1] or "").strip()

    votes: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for angle, text in angle_texts:
        for raw in (text or "").replace("\r\n", "\n").split("\n"):
            key = _line_key(raw)
            if len(key) < 2:
                continue
            votes[key].append((angle, raw.strip()))

    scored: list[tuple[float, str, int]] = []
    for key, items in votes.items():
        angles = {a for a, _ in items}
        best_raw = max(items, key=lambda x: len(x[1]))[1]
        score = float(len(angles))
        if 0 in angles:
            score += 0.35
        scored.append((score, best_raw, len(best_raw)))

    scored.sort(key=lambda x: (-x[0], -x[2], x[1]))
    seen: set[str] = set()
    out: list[str] = []
    for _, line, _ in scored:
        k = _line_key(line)
        if k in seen:
            continue
        seen.add(k)
        out.append(line)
    return "\n".join(out).strip()


def ocr_pil_multiview(
    img: Image.Image,
    ocr_fn: Callable[[Image.Image], str],
    *,
    angles: tuple[int, ...] | None = None,
    upscale: bool = True,
) -> str:
    """OCR изображения под несколькими углами; ocr_fn — один проход (Tesseract/Surya)."""
    if img is None:
        return ""
    base = upscale_small_crop(img.convert("RGB")) if upscale else img.convert("RGB")
    use_angles = angles if angles is not None else ocr_multiview_angles()
    parts: list[tuple[int, str]] = []
    for angle in use_angles:
        rotated = _rotate_image(base, angle)
        text = (ocr_fn(rotated) or "").strip()
        if text:
            parts.append((angle, text))
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0][1]
    return merge_multiview_texts(parts)
