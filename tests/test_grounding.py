from belener.grounding import (
    filter_table_rows_by_ocr,
    filter_tables_by_ocr_grounding,
    _looks_like_template_hallucination,
)


def test_template_hallucination_detected():
    rows = [
        {"Поз.": "1", "Обозначение": "QF1", "Наименование": "Распределительный выключатель"},
        {"Поз.": "2", "Обозначение": "TL1", "Наименование": "Трансформатор напряжения"},
        {"Поз.": "3", "Обозначение": "SF30", "Наименование": "Реле напряжения"},
        {"Поз.": "4", "Обозначение": "A1401", "Наименование": "Амперметр"},
    ]
    assert _looks_like_template_hallucination(rows)


def test_vision_table_dropped_without_ocr():
    rows = [
        {"Поз.": "1", "Обозначение": "QF1", "Наименование": "Распределительный выключатель"},
        {"Поз.": "2", "Обозначение": "TL1", "Наименование": "Трансформатор напряжения"},
        {"Поз.": "3", "Обозначение": "SF30", "Наименование": "Реле напряжения"},
        {"Поз.": "4", "Обозначение": "A1401", "Наименование": "Амперметр"},
    ]
    blob = "Блок питания 161 UCT3.1"
    assert filter_table_rows_by_ocr(rows, blob) == []
    tables = [{"kind": "specification", "source": "vision", "rows": rows}]
    assert filter_tables_by_ocr_grounding(tables, blob) == []


def test_real_row_kept_when_in_ocr():
    rows = [{"Поз.": "161", "Обозначение": "UCT3.1", "Наименование": "Блок питания"}]
    blob = "161 UCT3.1 Блок питания 220В"
    assert len(filter_table_rows_by_ocr(rows, blob)) == 1
