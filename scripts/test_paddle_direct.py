import fitz
import io
import urllib.request
import json
from PIL import Image

def main():
    doc = fitz.open("scan/BNP_1760-228-ЭМ1 л.5.pdf")
    page = doc[4] # Wait, is it page 4 or 0? Let's try page 0.
    
    # We don't know the exact bbox.
    # Let's use the YOLO bounding box detection or just run zone_refine.
    pass

if __name__ == "__main__":
    pass
