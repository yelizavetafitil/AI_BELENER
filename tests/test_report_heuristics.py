from belener.report_heuristics import extract_tt_lines, is_plausible_tt_line


def test_tt_rejects_scheme_coordinates():
    assert not is_plausible_tt_line("4 4")
    assert not is_plausible_tt_line("3 1 6 6 1 3")


def test_tt_accepts_engineering_sentence():
    assert is_plausible_tt_line(
        "2 Сварные стыковые соединения выполнять в соответствии с требованиями нормативного документа."
    )


def test_tt_from_parse_filtered():
    blob = (
        "4 4\n"
        "1 Монтаж трубопроводов выполнять в соответствии с проектом.\n"
        "2 Контроль качества сварных швов выполнять по нормативам.\n"
    )
    lines = extract_tt_lines(blob)
    assert all(is_plausible_tt_line(x) for x in lines)
    assert not any(x.startswith("4 4") for x in lines)
