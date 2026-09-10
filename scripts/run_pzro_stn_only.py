#!/usr/bin/env python3
"""STN/TNPA only for given refs (no OCR)."""
from __future__ import annotations

import json
import time
from datetime import date

refs = [
    {"kind": "СН", "ref": "СН 2.01.01"},
    {"kind": "СН", "ref": "СН 2.01.07"},
    {"kind": "СН", "ref": "СН 2.02.03"},
    {"kind": "СН", "ref": "СН 3.01.01"},
    {"kind": "СН", "ref": "СН 4.01.01"},
    {"kind": "СН", "ref": "СН 4.01.02"},
    {"kind": "СН", "ref": "СН 4.01.03"},
    {"kind": "СН", "ref": "СН 1.02.01"},
    {"kind": "СП", "ref": "СП 1.02.01"},
]

from belener.stn_lookup import refine_and_check_normative_refs, search_query
from belener.tnpa_lookup import refine_and_check_normative_refs_tnpa

print("queries:", [(r["kind"], search_query(r["kind"], r["ref"])) for r in refs], flush=True)
t0 = time.monotonic()
refs2, stn = refine_and_check_normative_refs(refs, today=date.today(), deadline=time.monotonic() + 300)
print(f"STN done {time.monotonic()-t0:.1f}s checks={len(stn)}", flush=True)
for c in stn:
    d = c.to_dict() if hasattr(c, "to_dict") else dict(c)
    print(
        f"STN\t{d.get('ref')}\tfound={d.get('found')}\tstatus={d.get('status')}\t"
        f"intro={d.get('intro_date')}\tcancel={d.get('cancel_date')}\terr={d.get('error')}",
        flush=True,
    )

t1 = time.monotonic()
refs3, tnpa = refine_and_check_normative_refs_tnpa(refs2, today=date.today(), deadline=time.monotonic() + 180)
print(f"TNPA done {time.monotonic()-t1:.1f}s checks={len(tnpa)}", flush=True)
for c in tnpa:
    d = c.to_dict() if hasattr(c, "to_dict") else dict(c)
    print(
        f"TNPA\t{d.get('ref')}\tfound={d.get('found')}\tstatus={d.get('status')}\t"
        f"intro={d.get('intro_date')}\tcancel={d.get('cancel_date')}\terr={d.get('error')}",
        flush=True,
    )
