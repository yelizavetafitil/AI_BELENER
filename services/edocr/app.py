#!/usr/bin/env python3
"""eDOCr (javvi51/eDOCr) — OCR основной надписи механических/САПР-чертежей."""

from __future__ import annotations

import logging
import os
import string
import tempfile
from pathlib import Path

import cv2
import fitz
import numpy as np
from flask import Flask, jsonify, request

app = Flask(__name__)
log = logging.getLogger("edocr")
logging.basicConfig(level=logging.INFO)


def _pix_to_bgr(pix) -> np.ndarray:
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n >= 3:
        return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def _infoblock_to_lines(infoblock_dict: dict) -> list[dict]:
    lines: list[dict] = []
    for _key, cells in (infoblock_dict or {}).items():
        if not isinstance(cells, dict):
            continue
        for cell_text in cells.values():
            t = str(cell_text or "").strip()
            if t:
                lines.append({"text": t})
    return lines


def _run_edocr(pdf_path: Path) -> dict:
    try:
        from eDOCr import tools  # type: ignore
    except ImportError:
        log.error("eDOCr not installed")
        return {"ok": False, "error": "eDOCr not installed", "tables": [], "lines": []}

    try:
        doc = fitz.open(pdf_path)
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
        doc.close()
        img = _pix_to_bgr(pix)

        class_list, _img_boxes = tools.box_tree.findrect(img)
        boxes_infoblock, _gdt, _cl_frame, _process_img = tools.img_process.process_rect(
            class_list, img
        )

        alphabet_infoblock = string.digits + string.ascii_letters + ",.:-/«»"
        import eDOCr  # type: ignore

        pkg = Path(eDOCr.__file__).resolve().parent
        model_infoblock = str(
            pkg / "keras_ocr_models" / "models" / "recognizer_infoblock.h5"
        )
        if not Path(model_infoblock).is_file():
            log.warning("eDOCr infoblock model missing at %s", model_infoblock)
            return {"ok": False, "error": "infoblock model missing", "tables": [], "lines": []}

        infoblock_dict = tools.pipeline_infoblock.read_infoblocks(
            boxes_infoblock, img, alphabet_infoblock, model_infoblock
        )
        lines = _infoblock_to_lines(infoblock_dict)
        full_text = "\n".join(x["text"] for x in lines)
        log.info("eDOCr infoblock cells=%s lines=%s", len(infoblock_dict or {}), len(lines))
        return {
            "ok": bool(lines),
            "lines": lines,
            "table_text": full_text,
            "tables": [],
            "infoblock": infoblock_dict,
            "pipeline": "edocr_infoblock",
        }
    except Exception as e:
        log.exception("eDOCr infoblock failed")
        return {"ok": False, "error": str(e), "tables": [], "lines": []}


@app.get("/health")
def health():
    try:
        import eDOCr  # noqa: F401

        return jsonify({"ok": True, "service": "edocr", "pipeline": "infoblock"})
    except ImportError:
        return jsonify({"ok": False, "error": "eDOCr missing"}), 503


@app.post("/parse")
def parse():
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "no file"}), 400
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        f.save(tmp.name)
        path = Path(tmp.name)
    try:
        return jsonify(_run_edocr(path))
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    port = int(os.environ.get("EDOCR_PORT", "5001"))
    app.run(host="0.0.0.0", port=port, threaded=False)
