from belener.report_llm import dedupe_ocr_text


def test_dedupe_ocr_text_removes_repeated_blocks():
    raw = "Воздух к горелке\n\nВоздух к горелке\n\nУникальный блок текста"
    out = dedupe_ocr_text(raw)
    assert out.count("Воздух к горелке") == 1
    assert "Уникальный блок" in out
