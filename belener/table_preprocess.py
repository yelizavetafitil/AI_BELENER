"""Предобработка растра табличных зон перед OCR (локально, OpenCV)."""

from __future__ import annotations

import logging

from PIL import Image, ImageEnhance, ImageOps

log = logging.getLogger("belener.table_preprocess")

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None
    np = None


def table_preprocess_available() -> bool:
    return cv2 is not None and np is not None


def preprocess_table_image(img: Image.Image) -> Image.Image:
    """
    Усиление контраста и линий сетки на сканах ГОСТ-таблиц.
    Grayscale + CLAHE; без жёсткой бинаризации (Tesseract лучше на полутонах).
    """
    if not table_preprocess_available():
        return ImageOps.autocontrast(img.convert("L"), cutoff=1)

    gray = np.array(img.convert("L"))
    h, w = gray.shape
    if h < 20 or w < 20:
        return img.convert("L")

    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    # Слегка усилить линии сетки (морфология на инвертированном)
    _, bin_inv = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(2, w // 80), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(2, h // 80)))
    lines = cv2.bitwise_or(
        cv2.dilate(cv2.erode(bin_inv, hk, iterations=1), hk, iterations=1),
        cv2.dilate(cv2.erode(bin_inv, vk, iterations=1), vk, iterations=1),
    )
    merged = cv2.addWeighted(enhanced, 0.88, 255 - lines, 0.12, 0)

    out = Image.fromarray(merged)
    out = ImageEnhance.Contrast(out).enhance(1.25)
    out = ImageEnhance.Sharpness(out).enhance(1.2)
    return out
