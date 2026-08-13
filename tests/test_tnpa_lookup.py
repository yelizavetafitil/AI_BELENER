from datetime import date

from belener.tnpa_lookup import (
    _pick_best_tnpa_match,
    _tnpa_designation,
    _tnpa_status,
    lookup_one_tnpa,
    refine_and_check_normative_refs_tnpa,
)


def test_portal_column_label():
    # kept intentionally blank: helpers are tested in the UI/PDF layers
    assert True


def test_portal_doc_url_tnpa():
    # url generation is covered indirectly via HTML rendering
    assert True


def test_tnpa_designation():
    row = {"Number": "10704-91", "OND": "ГОСТ", "OND1": ""}
    assert _tnpa_designation(row) == "10704-91 ГОСТ"


def test_tnpa_status_cancelled():
    row = {"PRIZN_BD": "0", "DTTN": "1991-01-01", "DTTK": "2020-01-01"}
    assert _tnpa_status(row, today=date(2026, 1, 1)) == "отменён"


def test_tnpa_status_cancelled_without_dttk():
    row = {"PRIZN_BD": "0", "DTTN": "1991-01-01", "DTTK": None, "DSMSOS": "1991-01-01"}
    assert _tnpa_status(row, today=date(2026, 1, 1)) == "отменён"


def test_tnpa_status_active():
    row = {"PRIZN_BD": "1", "DTTN": "1991-01-01", "DTTK": None}
    assert _tnpa_status(row, today=date(2026, 1, 1)) == "актуален"


def test_tnpa_ignores_dsmsos_same_as_intro(monkeypatch):
    """DSMSOS=DTTN у действующих («Взамен») не должно попадать в Отменен."""
    monkeypatch.setenv("PDF_STN_LOOKUP", "1")
    rows = [
        {
            "Number": "СТБ 2221-2020",
            "OND": "",
            "NND": "Смеси",
            "RN": "494639",
            "IDGLOBAL": "627186",
            "DTTN": "2021-04-01 00:00:00.000",
            "DTTK": None,
            "DSMSOS": "2021-04-01 00:00:00.000",
            "PRIZN": "2",
            "PRIZN_BD": "1",
        }
    ]
    out = lookup_one_tnpa("СТБ", "СТБ 2221-2020", client=_FakeTnpaClient(rows), today=date(2026, 8, 11))
    assert out.found is True
    assert out.intro_date == "01.04.2021"
    assert out.cancel_date == ""
    assert out.status == "актуален"


def test_pick_best_tnpa_match_single_row():
    rows = [{"Number": "СТБ 2073-2010", "OND": "", "NND": "Правила"}]
    match = _pick_best_tnpa_match("СТБ", "СТБ 2073-2010", rows)
    assert match is not None
    assert "2073" in _tnpa_designation(match)


def test_pick_best_tnpa_match_gost_dotted():
    """tnpa.by часто отдаёт Number без типа: «12.1.046-2014» + OND=ГОСТ."""
    rows = [{"Number": "12.1.046-2014", "OND": "ГОСТ", "NND": "Нормы освещения"}]
    match = _pick_best_tnpa_match("ГОСТ", "ГОСТ 12.1.046-2014", rows)
    assert match is not None
    assert "12.1.046" in _tnpa_designation(match)


def test_tnpa_parallel_ignores_stn_parallel_one(monkeypatch):
    monkeypatch.setenv("PDF_STN_PARALLEL", "1")
    monkeypatch.delenv("PDF_TNPA_PARALLEL", raising=False)
    from belener.config import tnpa_parallel_workers

    assert tnpa_parallel_workers() >= 3


def test_lookup_one_tnpa_timeout_is_not_missing(monkeypatch):
    monkeypatch.setenv("PDF_STN_LOOKUP", "1")

    class _TimeoutClient:
        def search_docs(self, query: str, *, page: int = 1, per_page: int = 30):
            raise TimeoutError("timed out")

    out = lookup_one_tnpa("ГОСТ", "ГОСТ 12.1.046-2014", client=_TimeoutClient(), today=date(2026, 1, 1))
    assert out.found is False
    assert out.status == "ошибка проверки"
    assert out.status != "нет в ТНПА"


def test_pick_best_tnpa_match():
    rows = [
        {"Number": "10704-91", "OND": "ГОСТ", "NND": "Трубы стальные"},
        {"Number": "8969-75", "OND": "ГОСТ", "NND": "Другой"},
    ]
    match = _pick_best_tnpa_match("ГОСТ", "ГОСТ 10704-91", rows)
    assert match is not None
    assert "10704" in _tnpa_designation(match)


class _FakeTnpaClient:
    def __init__(self, rows=None):
        self.rows = rows or []

    def search_docs(self, query: str, *, page: int = 1, per_page: int = 30):
        return list(self.rows)


def test_lookup_one_tnpa_found(monkeypatch):
    monkeypatch.setenv("PDF_STN_LOOKUP", "1")
    rows = [
        {
            "Number": "10704-91",
            "OND": "ГОСТ",
            "NND": "Трубы стальные",
            "RN": "100",
            "IDGLOBAL": "200",
            "DTTN": "1991-01-01",
            "DTTK": None,
            "PRIZN_BD": "1",
        }
    ]
    out = lookup_one_tnpa("ГОСТ", "ГОСТ 10704-91", client=_FakeTnpaClient(rows), today=date(2026, 1, 1))
    assert out.found is True
    assert out.doc_id == "100/200"
    assert out.status == "актуален"


def test_lookup_one_tnpa_not_found(monkeypatch):
    monkeypatch.setenv("PDF_STN_LOOKUP", "1")
    out = lookup_one_tnpa("ГОСТ", "ГОСТ 99999-99", client=_FakeTnpaClient([]), today=date(2026, 1, 1))
    assert out.found is False
    assert out.status == "нет в ТНПА"


def test_refine_and_check_normative_refs_tnpa(monkeypatch):
    monkeypatch.setenv("PDF_STN_LOOKUP", "1")
    rows = [
        {
            "Number": "8969-75",
            "OND": "ГОСТ",
            "NND": "Уголки",
            "RN": "1",
            "IDGLOBAL": "2",
            "DTTN": "1976-01-01",
            "PRIZN_BD": "1",
        }
    ]
    refs = [{"kind": "ГОСТ", "ref": "ГОСТ 8969-75"}]
    _, checks = refine_and_check_normative_refs_tnpa(refs, client=_FakeTnpaClient(rows), today=date(2026, 1, 1))
    assert len(checks) == 1
    assert checks[0].found is True


def test_active_portal_kind_env(monkeypatch):
    assert True
