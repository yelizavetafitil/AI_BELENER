#!/usr/bin/env python3
"""Проверка парсеров по эталонам data/training/golden/*.json (ваши правильные выводы)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from belener.parse import parse_explication, parse_legend, parse_numbered_notes, parse_specification  # noqa: E402


def _load_golden(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _norm(s: str) -> str:
    return " ".join((s or "").casefold().split())


def _check_spec(rows: list[dict], expected: list[dict]) -> list[str]:
    errs: list[str] = []
    if len(rows) < len(expected):
        errs.append(f"spec rows: got {len(rows)}, want>={len(expected)}")
    for i, exp in enumerate(expected):
        if i >= len(rows):
            break
        got = rows[i]
        for key in ("Поз.", "Обозначение", "Наименование"):
            if key not in exp:
                continue
            if _norm(str(got.get(key, ""))) != _norm(str(exp[key])):
                errs.append(f"spec[{i}].{key}: {got.get(key)!r} != {exp[key]!r}")
        if "Кол." in exp and exp["Кол."] not in ("—", ""):
            gq, eq = _norm(str(got.get("Кол.", ""))), _norm(str(exp["Кол."]))
            if gq != eq and eq.replace("м", "") not in gq and gq not in eq:
                errs.append(f"spec[{i}].Кол.: {got.get('Кол.')!r} != {exp['Кол.']!r}")
    return errs


def _check_legend(rows: list[dict], expected: list[dict]) -> list[str]:
    errs: list[str] = []
    got_notes = {_norm(r.get("note", "")) for r in rows}
    for exp in expected:
        note = _norm(exp.get("note", ""))
        if note and not any(note in g or g in note for g in got_notes):
            errs.append(f"legend missing: {exp.get('note')!r}")
    return errs


def _check_explication(rows: list[dict], expected: list[dict]) -> list[str]:
    errs: list[str] = []
    if len(rows) < len(expected):
        errs.append(f"explication rows: got {len(rows)}, want>={len(expected)}")
    for exp in expected:
        name = _norm(exp.get("name", ""))
        if not any(name in _norm(r.get("name", "")) for r in rows):
            errs.append(f"explication missing: {exp.get('name')!r}")
    return errs


def validate_one(golden: dict, training: Path) -> list[str]:
    stem = golden["stem"]
    spec_label = training / "labels" / f"{stem}_spec_right.txt"
    if not spec_label.is_file():
        return [f"нет label {spec_label}"]
    text = spec_label.read_text(encoding="utf-8")
    errs: list[str] = []

    if golden.get("specification"):
        rows = parse_specification(text)
        errs.extend(_check_spec(rows, golden["specification"]))
    if golden.get("explication"):
        rows = parse_explication(text)
        errs.extend(_check_explication(rows, golden["explication"]))
    if golden.get("legend"):
        rows = parse_legend(text)
        errs.extend(_check_legend(rows, golden["legend"]))
    notes_min = int(golden.get("notes_min") or 0)
    if notes_min:
        notes = parse_numbered_notes(text)
        if len(notes) < notes_min:
            errs.append(f"notes: got {len(notes)}, want>={notes_min}")

    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", type=Path, default=ROOT / "data" / "training" / "golden")
    ap.add_argument("--training", type=Path, default=ROOT / "data" / "training")
    args = ap.parse_args()

    files = sorted(args.golden.glob("*.json"))
    if not files:
        print("Нет golden/*.json", file=sys.stderr)
        return 1

    failed = 0
    for gf in files:
        g = _load_golden(gf)
        errs = validate_one(g, args.training)
        if errs:
            failed += 1
            print(f"FAIL {gf.name}")
            for e in errs:
                print(f"  - {e}")
        else:
            print(f"OK   {gf.name}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
