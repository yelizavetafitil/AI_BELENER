import fitz

from belener.scanned import is_scanned_document, page_text_layer_usable


def test_pdffactory_watermark_is_not_usable_text_layer():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "PDF создан с пробной версией pdfFactory Pro www.pdffactory.com",
    )
    assert not page_text_layer_usable(doc, 0)
    assert is_scanned_document(doc)
    doc.close()


def test_gost_rich_text_layer_is_usable():
    doc = fitz.open()
    page = doc.new_page()
    for i in range(25):
        page.insert_text((72, 72 + i * 12), f"ГОСТ 10704-91 позиция {i}")
    assert page_text_layer_usable(doc, 0)
    assert not is_scanned_document(doc)
    doc.close()
