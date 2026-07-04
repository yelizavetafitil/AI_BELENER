"""Кэш отрендеренных зон PDF на быстром диске (SSD) — те же пиксели, тот же OCR."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from pathlib import Path

import fitz

log = logging.getLogger("belener.zone_cache")


def _ssd_root() -> Path:
    raw = (os.environ.get("BELENER_SSD_ROOT") or "").strip()
    if raw:
        return Path(raw)
    # В Docker: том смонтирован в /ssd (см. docker-compose.ssd.yml)
    if Path("/ssd/zone_render").exists() or Path("/ssd").is_dir():
        return Path("/ssd")
    return Path("G:/BelenerCache")


def zone_cache_enabled() -> bool:
    return (os.environ.get("PDF_ZONE_CACHE") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def zone_cache_dir() -> Path:
    p = _ssd_root() / "zone_render"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _doc_id(doc: fitz.Document) -> str:
    name = getattr(doc, "name", None) or ""
    if name:
        try:
            st = os.stat(name)
            return f"{name}|{int(st.st_mtime)}|{st.st_size}"
        except OSError:
            return str(name)
    return str(id(doc))


def cache_key(
    doc: fitz.Document,
    page_index: int,
    clip: fitz.Rect,
    dpi: int,
) -> str:
    payload = (
        f"{_doc_id(doc)}|p{page_index}|"
        f"{clip.x0:.3f},{clip.y0:.3f},{clip.x1:.3f},{clip.y1:.3f}|dpi{dpi}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


def read_png(key: str) -> bytes | None:
    if not zone_cache_enabled():
        return None
    path = zone_cache_dir() / f"{key}.png"
    if not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def write_png(key: str, data: bytes) -> None:
    if not zone_cache_enabled() or not data:
        return
    path = zone_cache_dir() / f"{key}.png"
    try:
        path.write_bytes(data)
    except OSError as exc:
        if getattr(exc, "errno", None) == 28:
            freed = prune_zone_cache(keep_newest=40)
            if freed:
                log.warning("zone cache full — pruned %s files, retry write", freed)
                try:
                    path.write_bytes(data)
                    return
                except OSError:
                    pass
        log.debug("zone cache write failed %s", path, exc_info=True)


def prune_zone_cache(*, keep_newest: int = 80) -> int:
    """Удалить старые PNG из кэша зон (при переполнении /ssd)."""
    if not zone_cache_enabled():
        return 0
    cache = zone_cache_dir()
    try:
        files = sorted(cache.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return 0
    removed = 0
    for path in files[keep_newest:]:
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return removed


def zone_cache_free_mb() -> float | None:
    try:
        usage = shutil.disk_usage(zone_cache_dir())
        return usage.free / (1024 * 1024)
    except OSError:
        return None
