#!/usr/bin/env python3
"""Прогон PDF: OCR → STN → TNPA, сводка по найденным/пропущенным."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date
from pathlib import Path

path = sys.argv[1] if len(sys.argv) > 1 else "/app/data/tmp/pzro.pdf"
filename = Path(path).name

print(f"=== file: {path}", flush=True)
t0 = time.monotonic()

from belener.config import gost_check_total_budget_sec, stn_lookup_enabled
from belener.normative_extract import extract_normatives_pdf_path
from belener.stn_lookup import refine_and_check_normative_refs
from belener.tnpa_lookup import refine_and_check_normative_refs_tnpa

import fitz

with fitz.open(path) as doc:
    page_count = doc.page_count
print(f"pages={page_count} engine={os.environ.get('PDF_OCR_ENGINE','?')} surya={os.environ.get('SURYA_OCR_URL','')}", flush=True)

budget = gost_check_total_budget_sec(page_count)
deadline = time.monotonic() + budget
result = extract_normatives_pdf_path(path, filename, pipeline_deadline=deadline)
refs = result.get("normative_refs") or []
print(
    f"OCR done {time.monotonic()-t0:.1f}s refs={len(refs)} "
    f"pages_processed={result.get('pages_processed')} exhausted={result.get('budget_exhausted')}",
    flush=True,
)
for r in refs:
    print(f"  REF {r.get('kind')} | {r.get('ref')}", flush=True)

stn_checks = []
tnpa_checks = []
if stn_lookup_enabled() and refs:
    print("=== STN lookup...", flush=True)
    refs, stn_checks = refine_and_check_normative_refs(
        refs, today=date.today(), deadline=time.monotonic() + 900
    )
    print("=== TNPA lookup...", flush=True)
    refs, tnpa_checks = refine_and_check_normative_refs_tnpa(
        refs, today=date.today(), deadline=time.monotonic() + 900
    )

print("=== SUMMARY ===", flush=True)
print(f"total_refs={len(refs)} stn_checks={len(stn_checks)} tnpa_checks={len(tnpa_checks)}", flush=True)
stn_map = {getattr(c, "ref", None) or c.get("ref"): c for c in stn_checks}
tnpa_map = {getattr(c, "ref", None) or c.get("ref"): c for c in tnpa_checks}

found_stn = found_tnpa = active = 0
rows = []
for n in refs:
    ref = n.get("ref") or "—"
    c = stn_map.get(ref)
    ct = tnpa_map.get(ref)
    stn_found = bool(getattr(c, "found", False) if c and hasattr(c, "found") else (c or {}).get("found") in (True, 1, "1"))
    tnpa_found = bool(getattr(ct, "found", False) if ct and hasattr(ct, "found") else (ct or {}).get("found") in (True, 1, "1"))
    stn_st = (getattr(c, "status", None) if c and hasattr(c, "status") else (c or {}).get("status")) or "—"
    tnpa_st = (getattr(ct, "status", None) if ct and hasattr(ct, "status") else (ct or {}).get("status")) or "—"
    if stn_found:
        found_stn += 1
    if tnpa_found:
        found_tnpa += 1
    if (stn_found and stn_st == "актуален") or (tnpa_found and tnpa_st == "актуален"):
        active += 1
        color = "GREEN"
    else:
        color = "RED"
    line = f"{color}\t{ref}\tSTN={stn_found}/{stn_st}\tTNPA={tnpa_found}/{tnpa_st}"
    print(line, flush=True)
    rows.append({"ref": ref, "stn_found": stn_found, "stn_status": stn_st, "tnpa_found": tnpa_found, "tnpa_status": tnpa_st, "color": color})

print(f"found_stn={found_stn} found_tnpa={found_tnpa} active={active} elapsed={time.monotonic()-t0:.1f}s", flush=True)
out = Path("/app/data/tmp/pzro_check.json")
out.write_text(json.dumps({"refs": refs, "rows": rows, "result_meta": {k: result.get(k) for k in ("pages_processed","budget_exhausted","pipeline","elapsed_sec")}}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"wrote {out}", flush=True)
