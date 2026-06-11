from belener.report_structure import structured_report_from_drawing


def test_structured_report_no_raw_fence():
    drawing = {
        "ok": True,
        "full_text_pages": [
            {
                "index": 1,
                "text": (
                    "Спецификация\n"
                    "Поз. Обозначение Наименование Кол.\n"
                    "1 Труба 14х2-20 160 м\n"
                    "1 Монтаж выполнять по проекту организации-заказчика.\n"
                    "ГОСТ 28191-89\n"
                ),
            }
        ],
        "normative_refs": [{"kind": "ГОСТ", "ref": "28191-89"}],
    }
    md = structured_report_from_drawing(drawing, mode="full")
    assert "```text" not in md
    assert "28191-89" in md
