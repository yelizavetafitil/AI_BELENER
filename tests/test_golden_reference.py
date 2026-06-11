"""Регрессия по эталонным выводам (2 эталонных листа)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from belener.parse import parse_explication, parse_legend, parse_numbered_notes, parse_specification

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data" / "training" / "golden"
LABELS = ROOT / "data" / "training" / "labels"


def _label(stem: str) -> str:
    return (LABELS / f"{stem}_spec_right.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "golden_file,min_spec",
    [
        ("BNP_1760-228-ЭМ1_л.5.json", 4),
        ("_10-16-25_23.01.2026_1118-0-ГП9_л.4.json", 0),
    ],
)
def test_golden_parse(golden_file: str, min_spec: int) -> None:
    g = json.loads((GOLDEN / golden_file).read_text(encoding="utf-8"))
    text = _label(g["stem"])
    if min_spec:
        rows = parse_specification(text)
        assert len(rows) >= min_spec
        assert any("гост" in str(r.get("Обозначение", "")).casefold() for r in rows)
    if g.get("explication"):
        expl = parse_explication(text)
        assert len(expl) >= len(g["explication"])
    if g.get("legend"):
        leg = parse_legend(text)
        assert len(leg) >= len(g["legend"]) // 2
    if g.get("notes_min"):
        assert len(parse_numbered_notes(text)) >= g["notes_min"]
