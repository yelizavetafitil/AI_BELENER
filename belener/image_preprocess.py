"""Предобработка растра перед OCR: выпрямление (deskew), шум — локально, OpenCV."""

from __future__ import annotations

import logging

from PIL import Image

log = logging.getLogger("belener.image_preprocess")

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None
    np = None


def deskew_available() -> bool:
    return cv2 is not None and np is not None


def _pil_to_gray(img: Image.Image) -> "np.ndarray":
    arr = np.array(img.convert("L"))
    return arr


def _gray_to_pil(gray: "np.ndarray") -> Image.Image:
    return Image.fromarray(gray)


def deskew_image(img: Image.Image, *, max_angle: float = 8.0) -> Image.Image:
    """Выпрямление листа по доминирующим линиям (сканы с телефона / кривой подшив)."""
    if not deskew_available():
        return img
    gray = _pil_to_gray(img)
    h, w = gray.shape
    if h < 80 or w < 80:
        return img
    _, bin_img = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    edges = cv2.Canny(bin_img, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=max(80, int(min(h, w) * 0.08)),
        minLineLength=int(min(h, w) * 0.25),
        maxLineGap=int(min(h, w) * 0.04),
    )
    if lines is None or len(lines) < 4:
        return img
    angles: list[float] = []
    for seg in lines[:120]:
        x1, y1, x2, y2 = [int(v) for v in seg[0]]
        dx, dy = x2 - x1, y2 - y1
        if abs(dx) < 8 and abs(dy) < 8:
            continue
        ang = np.degrees(np.arctan2(dy, dx))
        if abs(ang) < max_angle:
            angles.append(ang)
    if len(angles) < 4:
        return img
    median = float(np.median(angles))
    if abs(median) < 0.15:
        return img
    center = (w // 2, h // 2)
    mat = cv2.getRotationMatrix2D(center, median, 1.0)
    rotated = cv2.warpAffine(
        gray,
        mat,
        (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    log.debug("deskew angle=%.2f", median)
    return _gray_to_pil(rotated)


def denoise_image(img: Image.Image) -> Image.Image:
    if not deskew_available():
        return img
    gray = _pil_to_gray(img)
    cleaned = cv2.fastNlMeansDenoising(gray, None, 8, 7, 21)
    return _gray_to_pil(cleaned)
