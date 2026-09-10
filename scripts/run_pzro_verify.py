#!/usr/bin/env python3
import time
from datetime import date

import fitz

from belener.normative_crops import extract_normatives_document_crops
from belener.stn_lookup import refine_and_check_normative_refs

path = "/app/data/tmp/pzro.pdf"
doc = fitz.open(path)
r = extract_normatives_document_crops(doc, "pzro.pdf", pipeline_deadline=time.monotonic() + 30)
doc.close()
refs = r.get("normative_refs") or []
print("refs", len(refs), [x.get("ref") for x in refs], flush=True)
print("exhausted", r.get("budget_exhausted"), "tiles", r.get("tiles_done"), r.get("tiles_expected"), flush=True)
refs2, stn = refine_and_check_normative_refs(refs, today=date.today(), deadline=time.monotonic() + 180)
for c in stn:
    d = c.to_dict()
    print(f"STN {d['ref']}: found={d['found']} status={d['status']}", flush=True)
print("stn_found", sum(1 for c in stn if c.found), flush=True)
