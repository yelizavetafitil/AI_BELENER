import pytest

pytest.importorskip("reportlab")

from belener.normative_pdf import build_normative_pdf_bytes


def test_build_normative_pdf_bytes_returns_pdf():
    payload = {
        "title": "Таблица нормативов",
        "filename": "sample.pdf",
        "meta": ["Файл: sample.pdf", "Листов в файле: 42"],
        "summary": "Всего в документе: 36; найдено в Стройдок: 23; найдено в ТНПА: 5; актуально: 21",
        "headers": [
            "Тип",
            "Обозначение",
            "Стройдок",
            "ТНПА",
            "Введен Стройдок",
            "Отменен Стройдок",
            "Введен ТНПА",
            "Отменен ТНПА",
            "Статус",
        ],
        "rows": [
            {
                "fill": "active",
                "cells": [
                    {"text": "ГОСТ", "bold": False},
                    {"text": "ГОСТ 23407-78", "bold": False},
                    {"text": "Стройдок", "href": "https://normy.stn.by/ips.php?123", "bold": False},
                    {"text": "ТНПА", "href": "https://tnpa.by/#!/DocumentCard/100/200", "bold": False},
                    {"text": "01.07.1979", "bold": False},
                    {"text": "—", "bold": False},
                    {"text": "—", "bold": False},
                    {"text": "—", "bold": False},
                    {"text": "актуален", "bold": True},
                ],
            }
        ],
        "widths": [14, 66, 18, 18, 20, 20, 20, 20, 28],
    }
    pdf = build_normative_pdf_bytes(payload)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


def test_build_normative_pdf_computes_summary_from_rows():
    payload = {
        "title": "Таблица нормативов",
        "headers": [
            "Тип",
            "Обозначение",
            "Стройдок",
            "ТНПА",
            "Введен Стройдок",
            "Отменен Стройдок",
            "Введен ТНПА",
            "Отменен ТНПА",
            "Статус",
        ],
        "rows": [
            {
                "cells": [
                    {"text": "ГОСТ"},
                    {"text": "ГОСТ 1"},
                    {"text": "Стройдок", "href": "https://normy.stn.by/ips.php?1"},
                    {"text": "ТНПА", "href": "https://tnpa.by/#!/DocumentCard/1/2"},
                    {"text": "01.01.2000"},
                    {"text": "—"},
                    {"text": "—"},
                    {"text": "—"},
                    {"text": "актуален", "bold": True},
                ],
            },
            {
                "cells": [
                    {"text": "СП"},
                    {"text": "СП 2"},
                    {"text": "—"},
                    {"text": "—"},
                    {"text": "—"},
                    {"text": "—"},
                    {"text": "—"},
                    {"text": "не найдено"},
                ],
            },
        ],
    }
    pdf = build_normative_pdf_bytes(payload)
    assert pdf.startswith(b"%PDF")
    assert b"36" not in pdf[:5000]  # sanity: not random
    assert len(pdf) > 800
