import json
from belener.extract import extract_pdf_path
import logging

logging.basicConfig(level=logging.INFO)
res = extract_pdf_path("/workspace/scan/BNP_1760-228-ЭМ1 л.5.pdf", "BNP_1760-228-ЭМ1 л.5.pdf")
print("===== RAW TEXT ZONE TEXTS =====")
for k, v in res.get("zone_texts", {}).items():
    print(f"[{k}]")
    print(v)
    print("=" * 40)
