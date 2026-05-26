"""OCR: PyMuPDF render → препроцессинг → Tesseract CLI (fallback: get_textpage_ocr)."""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

import fitz
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from belener.config import (
    ocr_fitz_fallback,
    ocr_lang,
    ocr_max_pixels,
    ocr_min_rect_pt,
    ocr_psm_for_zone,
    ocr_timeout_sec,
)

_FITZ_LOCK = threading.Lock()

# Latin → Cyrillic в словах, где уже есть кириллица (типовой rus OCR на eng-модели)
_LATIN_IN_CYRILLIC = str.maketrans(
    {
        "A": "А",
        "a": "а",
        "B": "В",
        "b": "в",
        "C": "С",
        "c": "с",
        "E": "Е",
        "e": "е",
        "H": "Н",
        "h": "н",
        "K": "К",
        "k": "к",
        "M": "М",
        "m": "м",
        "O": "О",
        "o": "о",
        "P": "Р",
        "p": "р",
        "T": "Т",
        "t": "т",
        "X": "Х",
        "x": "х",
        "Y": "У",
        "y": "у",
        "I": "И",
        "i": "и",
        "N": "Н",
        "n": "н",
        "R": "Р",
        "r": "р",
        "S": "С",
        "s": "с",
    }
)


def tessdata_dir() -> str | None:
    for p in (
        os.environ.get("TESSDATA_PREFIX", "").strip(),
        "/usr/share/tesseract-ocr/5/tessdata",
        "/usr/share/tesseract-ocr/4.00/tessdata",
    ):
        if p and Path(p).is_dir():
            return p
    return None


def tesseract_available() -> bool:
    return tessdata_dir() is not None


def normalize_ocr_text(text: str) -> str:
    """Универсальная пост-обработка OCR русского текста."""
    if not text:
        return ""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        ln = raw.strip()
        if not ln:
            continue
        cyr = len(re.findall(r"[А-Яа-яЁё]", ln))
        lat = len(re.findall(r"[A-Za-z]", ln))
        if cyr and lat:
            parts = re.split(r"(\s+)", ln)
            fixed: list[str] = []
            for part in parts:
                if part.isspace() or not part:
                    fixed.append(part)
                    continue
                if re.search(r"[А-Яа-яЁё]", part):
                    fixed.append(part.translate(_LATIN_IN_CYRILLIC))
                elif cyr >= lat and re.fullmatch(r"[A-Za-z]{4,}", part):
                    fixed.append(part.translate(_LATIN_IN_CYRILLIC))
                else:
                    fixed.append(part)
            ln = "".join(fixed)
        lines.append(ln)
    return "\n".join(lines).strip()


def finalize_ocr_text(text: str, *, spell: bool | None = None) -> str:
    """Homoglyphs + hunspell — единая точка нормализации OCR."""
    base = normalize_ocr_text(text)
    if not base:
        return ""
    from belener.spell_ru import spell_ru_enabled

    use_spell = spell_ru_enabled() if spell is None else spell
    if not use_spell or len(base) > 4000:
        return base
    from belener.spell_ru import repair_ocr_russian

    return repair_ocr_russian(base)


def _rect_too_small(clip: fitz.Rect) -> bool:
    try:
        m = ocr_min_rect_pt()
        return clip.width < m or clip.height < m
    except Exception:
        return False


def _is_table_zone(zone: str) -> bool:
    z = (zone or "").casefold()
    return z.startswith(("spec_", "legend", "tables_block", "explication", "table"))


def _preprocess_image(img: Image.Image, *, zone: str = "") -> Image.Image:
    from belener.config import ocr_deskew_enabled, table_clahe_enabled
    from belener.image_preprocess import deskew_image

    if ocr_deskew_enabled():
        img = deskew_image(img)
    if table_clahe_enabled() and _is_table_zone(zone):
        from belener.table_preprocess import preprocess_table_image

        return preprocess_table_image(img)
    img = ImageOps.grayscale(img)
    img = ImageOps.autocontrast(img, cutoff=1)
    img = ImageEnhance.Contrast(img).enhance(1.6)
    img = ImageEnhance.Sharpness(img).enhance(1.5)
    img = img.filter(ImageFilter.MedianFilter(size=3))
    threshold = _otsu_threshold(img)
    img = img.point(lambda p: 255 if p > threshold else 0)
    return img


def _otsu_threshold(img: Image.Image) -> int:
    hist = img.histogram()
    total = sum(hist)
    if total <= 0:
        return 168
    sum_total = sum(i * hist[i] for i in range(256))
    sum_b = 0.0
    w_b = 0.0
    max_var = -1.0
    threshold = 168
    for i in range(256):
        w_b += hist[i]
        if w_b <= 0:
            continue
        w_f = total - w_b
        if w_f <= 0:
            break
        sum_b += i * hist[i]
        m_b = sum_b / w_b
        m_f = (sum_total - sum_b) / w_f
        var_between = w_b * w_f * (m_b - m_f) ** 2
        if var_between > max_var:
            max_var = var_between
            threshold = i
    return max(120, min(int(threshold), 200))


def _cap_dpi_for_clip(clip: fitz.Rect, dpi: int, max_pixels: int | None = None) -> int:
    limit = max_pixels if max_pixels is not None else ocr_max_pixels()
    try:
        w_pt, h_pt = float(clip.width), float(clip.height)
    except Exception:
        return dpi
    if w_pt <= 0 or h_pt <= 0:
        return dpi
    scale = dpi / 72.0
    pixels = (w_pt * scale) * (h_pt * scale)
    if pixels <= limit:
        return dpi
    factor = (limit / pixels) ** 0.5
    return max(200, min(int(dpi * factor), dpi))


def _render_clip(doc: fitz.Document, page_index: int, clip: fitz.Rect, dpi: int) -> Image.Image | None:
    try:
        if clip.width < 2 or clip.height < 2:
            return None
    except Exception:
        return None

    eff_dpi = _cap_dpi_for_clip(clip, dpi)
    scale = eff_dpi / 72.0
    mat = fitz.Matrix(scale, scale)
    with _FITZ_LOCK:
        pix = doc[page_index].get_pixmap(matrix=mat, clip=clip, alpha=False)
    try:
        return Image.open(io.BytesIO(pix.tobytes("png")))
    except Exception:
        return None


def _tesseract_cli(img: Image.Image, *, lang: str, psm: int, dpi: int, zone: str = "") -> str:
    img = _preprocess_image(img, zone=zone)
    with subprocess.Popen(
        [
            "tesseract",
            "stdin",
            "stdout",
            "-l",
            lang,
            "--oem",
            "1",
            "--psm",
            str(psm),
            "-c",
            "preserve_interword_spaces=1",
            "-c",
            f"user_defined_dpi={dpi}",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=False,
    ) as proc:
        assert proc.stdin is not None
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        try:
            out, _ = proc.communicate(buf.getvalue(), timeout=ocr_timeout_sec())
        except subprocess.TimeoutExpired:
            proc.kill()
            return ""
    if proc.returncode not in (0, None):
        return ""
    try:
        return out.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _tesseract_fitz(doc: fitz.Document, page_index: int, clip: fitz.Rect, *, dpi: int, lang: str) -> str:
    tess = tessdata_dir()
    if tess is None:
        return ""
    tmp = fitz.open()
    try:
        with _FITZ_LOCK:
            page = tmp.new_page(width=clip.width, height=clip.height)
            page.show_pdf_page(page.rect, doc, page_index, clip=clip)
        tp = None
        try:
            tp = page.get_textpage_ocr(
                flags=0,
                language=lang,
                dpi=dpi,
                full=True,
                tessdata=tess,
            )
            return page.get_text(sort=True, textpage=tp).strip()
        finally:
            if tp is not None:
                del tp
    except Exception:
        return ""
    finally:
        tmp.close()


def zone_to_base64_png(
    doc: fitz.Document,
    page_index: int,
    clip: fitz.Rect,
    dpi: int,
    *,
    max_side: int | None = None,
) -> str:
    """JPEG зоны для vision (ограниченный размер — иначе qwen2.5vl падает по памяти)."""
    import base64

    from belener.config import vision_max_side, vision_zone_dpi

    eff_dpi = dpi or vision_zone_dpi()
    img = _render_clip(doc, page_index, clip, eff_dpi)
    if img is None:
        return ""
    max_side = max_side if max_side is not None else vision_max_side()
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _ocr_with_tesseract(
    doc: fitz.Document,
    page_index: int,
    clip: fitz.Rect,
    *,
    dpi: int,
    lang: str,
    zone: str,
    psm_val: int,
    img: Image.Image | None,
) -> str:
    eff_dpi = _cap_dpi_for_clip(clip, dpi)
    if img is None:
        img = _render_clip(doc, page_index, clip, dpi)
    text = ""
    if img is not None and shutil.which("tesseract"):
        text = _tesseract_cli(img, lang=lang, psm=psm_val, dpi=eff_dpi, zone=zone)
    if not text and ocr_fitz_fallback():
        text = _tesseract_fitz(doc, page_index, clip, dpi=eff_dpi, lang=lang)
    return text


def ocr_region(
    doc: fitz.Document,
    page_index: int,
    clip: fitz.Rect,
    *,
    dpi: int,
    lang: str | None = None,
    zone: str = "",
    psm: int | None = None,
    img: Image.Image | None = None,
) -> str:
    if _rect_too_small(clip):
        return ""

    from belener.config import ocr_engine, ocr_fallback_tesseract

    lang = lang or ocr_lang()
    psm_val = psm if psm is not None else ocr_psm_for_zone(zone)
    use_spell = _is_table_zone(zone) or (zone or "").casefold().startswith("stamp")

    engine = ocr_engine()
    use_deepseek = engine in ("deepseek", "auto")

    if use_deepseek:
        from belener.deepseek_ocr import (
            deepseek_ocr_enabled,
            normalize_deepseek_table_text,
            ocr_pil_image,
        )

        if deepseek_ocr_enabled():
            if img is None:
                img = _render_clip(doc, page_index, clip, dpi)
            if img is not None:
                raw = ocr_pil_image(img, zone=zone, filename=f"{zone or 'zone'}.png")
                if raw:
                    if _is_table_zone(zone):
                        raw = normalize_deepseek_table_text(raw)
                    return finalize_ocr_text(raw, spell=use_spell)
        if engine == "deepseek" and not ocr_fallback_tesseract():
            return ""
        if not tesseract_available():
            return ""

    elif not tesseract_available():
        return ""

    text = _ocr_with_tesseract(
        doc, page_index, clip, dpi=dpi, lang=lang, zone=zone, psm_val=psm_val, img=img
    )
    return finalize_ocr_text(text, spell=use_spell)


def ocr_stamp_frame(
    doc: fitz.Document,
    page_index: int,
    clip: fitz.Rect,
    *,
    dpi: int,
    lang: str | None = None,
) -> str:
    """OCR рамки: DeepSeek (если включён) или два прохода Tesseract."""
    from belener.config import ocr_engine, ocr_fallback_tesseract

    if ocr_engine() in ("deepseek", "auto"):
        from belener.deepseek_ocr import deepseek_ocr_enabled, ocr_pil_image

        if deepseek_ocr_enabled():
            img = _render_clip(doc, page_index, clip, dpi)
            if img is not None:
                text = ocr_pil_image(img, zone="stamp_frame", filename="stamp.png")
                if text:
                    return finalize_ocr_text(text, spell=True)
        if ocr_engine() == "deepseek" and not ocr_fallback_tesseract():
            return ""

    lang = lang or ocr_lang()
    eff_dpi = _cap_dpi_for_clip(clip, dpi)
    img = _render_clip(doc, page_index, clip, dpi)
    if img is None:
        return ""

    block = ""
    sig_grid = ""
    if shutil.which("tesseract"):
        block = _tesseract_cli(img, lang=lang, psm=6, dpi=eff_dpi, zone="stamp_frame")
        w, h = img.size
        sig_w = max(1, int(w * 0.58))
        sig_img = img.crop((0, 0, sig_w, h))
        sig_grid = _tesseract_cli(sig_img, lang=lang, psm=4, dpi=eff_dpi, zone="stamp_sig")

    if not block and not sig_grid:
        return ocr_region(doc, page_index, clip, dpi=dpi, lang=lang, zone="stamp_frame")

    parts = [t for t in (block, sig_grid) if t]
    combined = parts[0] if len(parts) == 1 else parts[0] + "\n\n--- stamp_sig ---\n\n" + parts[1]
    return finalize_ocr_text(combined, spell=True)


def _merge_ocr_passes(parts: list[str]) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for block in parts:
        for ln in block.splitlines():
            key = ln.strip()
            if key and key not in seen:
                seen.add(key)
                out.append(ln)
    return "\n".join(out)


def ocr_image_multipass(img: Image.Image, *, dpi: int, lang: str | None = None) -> str:
    """Несколько PSM для чертежей: авто, разреженный текст, блоки."""
    lang = lang or ocr_lang()
    parts: list[str] = []
    for psm in (3, 11, 6):
        t = _tesseract_cli(img, lang=lang, psm=psm, dpi=dpi)
        if t:
            parts.append(t)
    return finalize_ocr_text(_merge_ocr_passes(parts))


def _iter_image_tiles(img: Image.Image, tile_px: int, overlap: int):
    w, h = img.size
    step = max(tile_px - overlap, tile_px // 2)
    y = 0
    while y < h:
        y2 = min(y + tile_px, h)
        x = 0
        while x < w:
            x2 = min(x + tile_px, w)
            yield img.crop((x, y, x2, y2))
            if x2 >= w:
                break
            x += step
        if y2 >= h:
            break
        y += step


def ocr_clip_tiled(
    doc: fitz.Document,
    page_index: int,
    clip: fitz.Rect,
    *,
    dpi: int,
    tile_px: int = 2400,
    overlap: int = 240,
    lang: str | None = None,
    zone: str = "",
    psm: int | None = None,
) -> str:
    """OCR фрагмента листа; крупные зоны — по плиткам."""
    if not tesseract_available():
        return ""

    lang = lang or ocr_lang()
    psm_val = psm if psm is not None else ocr_psm_for_zone(zone)
    eff_dpi = _cap_dpi_for_clip(clip, dpi)
    img = _render_clip(doc, page_index, clip, dpi)
    if img is None:
        return ""

    w, h = img.size
    if w <= tile_px and h <= tile_px:
        if shutil.which("tesseract"):
            text = _tesseract_cli(img, lang=lang, psm=psm_val, dpi=eff_dpi)
            if text:
                return finalize_ocr_text(text)
        return finalize_ocr_text(_tesseract_fitz(doc, page_index, clip, dpi=eff_dpi, lang=lang))

    parts: list[str] = []
    for tile in _iter_image_tiles(img, tile_px, overlap):
        t = ocr_image_multipass(tile, dpi=eff_dpi, lang=lang)
        if t:
            parts.append(t)
    return finalize_ocr_text(_merge_ocr_passes(parts))


def ocr_page_full(
    doc: fitz.Document,
    page_index: int,
    *,
    dpi: int,
    tile_px: int = 2400,
    overlap: int = 240,
    lang: str | None = None,
) -> str:
    """OCR всей страницы; крупные листы — по плиткам."""
    return ocr_clip_tiled(
        doc,
        page_index,
        doc[page_index].rect,
        dpi=dpi,
        tile_px=tile_px,
        overlap=overlap,
        lang=lang,
        zone="full_page",
        psm=3,
    )
