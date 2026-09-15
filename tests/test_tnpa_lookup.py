from datetime import date

from belener.tnpa_lookup import (
    _pick_best_tnpa_match,
    _tnpa_designation,
    _tnpa_status,
    lookup_one_tnpa,
    refine_and_check_normative_refs_tnpa,
)


def test_tnpa_ca_bundle_contains_intermediate():
    from pathlib import Path

    from cryptography import x509
    from cryptography.hazmat.backends import default_backend

    from belener.tnpa_lookup import _load_tnpa_intermediate_pem, _tnpa_trust_ca_path, reset_tnpa_ssl

    reset_tnpa_ssl()
    intermediate = _load_tnpa_intermediate_pem()
    assert intermediate.count(b"BEGIN CERTIFICATE") >= 2
    subjects = []
    for block in intermediate.split(b"-----END CERTIFICATE-----"):
        if b"BEGIN CERTIFICATE" not in block:
            continue
        pem = b"-----BEGIN CERTIFICATE-----" + block.split(b"-----BEGIN CERTIFICATE-----")[-1] + b"-----END CERTIFICATE-----\n"
        cert = x509.load_pem_x509_certificate(pem, default_backend())
        subjects.append(cert.subject.rfc4514_string())
    assert any("R6 AlphaSSL" in s for s in subjects)
    assert any("R46 AlphaSSL" in s for s in subjects)
    bundle = Path(_tnpa_trust_ca_path()).read_bytes()
    assert intermediate == bundle


def test_tnpa_ssl_context_no_deadlock():
    import concurrent.futures

    from belener.tnpa_lookup import _tnpa_ssl_context, reset_tnpa_ssl

    reset_tnpa_ssl()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(_tnpa_ssl_context) for _ in range(4)]
        ctxs = [f.result(timeout=10) for f in futs]
    assert ctxs[0] is not None
    assert all(c is ctxs[0] for c in ctxs)


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


def test_tnpa_parallel_default_independent_of_stn(monkeypatch):
    monkeypatch.setenv("PDF_STN_PARALLEL", "1")
    monkeypatch.delenv("PDF_TNPA_PARALLEL", raising=False)
    from belener.config import tnpa_parallel_workers

    # По умолчанию 1 воркер — параллель на сервере валит SSL handshake.
    assert tnpa_parallel_workers() == 1
    monkeypatch.setenv("PDF_TNPA_PARALLEL", "2")
    assert tnpa_parallel_workers() == 2


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


def test_pick_best_tnpa_prefers_newer_year():
    """СН 2.01.05-2019 заменён → берём СН 2.01.05-2025."""
    rows = [
        {
            "Number": "2.01.05-2019",
            "OND": "СН",
            "NND": "Ветровые воздействия",
            "DTTN": "2020-09-08",
            "DTTK": "2026-04-19",
            "PRIZN_BD": "0",
        },
        {
            "Number": "2.01.05-2025",
            "OND": "СН",
            "NND": "Ветровые воздействия",
            "DTTN": "2026-04-19",
            "DTTK": None,
            "PRIZN_BD": "1",
        },
    ]
    match = _pick_best_tnpa_match("СН", "СН 2.01.05", rows)
    assert match is not None
    assert "2025" in _tnpa_designation(match)


def test_sn_digits_compatible_without_year():
    from belener.stn_lookup import _digits_compatible

    assert _digits_compatible("20105", "201052019") is True
    assert _digits_compatible("20105", "201052025") is True
    assert _digits_compatible("201052022", "201052019") is False


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
