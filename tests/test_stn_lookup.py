from datetime import date
from pathlib import Path

from belener.stn_lookup import (
    StnCheckResult,
    StnClient,
    _digits_compatible,
    _iter_ocr_digit_variants,
    check_normative_refs_stn,
    is_stn_checkable,
    lookup_one,
    parse_card_html,
    parse_ru_date,
    refine_and_check_normative_refs,
    search_queries,
    search_query,
    stn_checks_to_markdown,
    validity_status,
)

CARD_HTML = """
<table class="doc-card-table">
<tr><td class="doc-card-header">Обозначение</td><td>СНиП 3.05.02-88</td></tr>
<tr><td class="doc-card-header">Дата введения</td><td>01.07.1988</td></tr>
<tr><td class="doc-card-header">Дата отмены</td><td>25.04.2026</td></tr>
</table>
"""


def test_is_stn_checkable():
    assert is_stn_checkable("ГОСТ")
    assert is_stn_checkable("СНиП")
    assert is_stn_checkable("ОСТ")
    assert not is_stn_checkable("СТП")


def test_digit_compatible_blocks_1070_vs_10704():
    assert _digits_compatible("107091", "1070491") is False
    assert _digits_compatible("896275", "896975") is True


def test_ocr_digit_variants():
    variants = _iter_ocr_digit_variants("ГОСТ", "ГОСТ 8962-75")
    assert any("8969" in v for v in variants)


def test_search_query_strips_part_spec():
    assert search_query("ГОСТ", "В-20 ГОСТ 10705-80") == "ГОСТ 10705-80"
    assert search_query("ГОСТ", "В-Ст3пс ГОСТ 10705-80") == "ГОСТ 10705-80"


def test_search_query_strips_table_pos():
    assert search_query("ГОСТ", "13 ГОСТ 8969-75") == "ГОСТ 8969-75"


def test_tkp_normalizes_ocr_spaces():
    assert search_query("ТКП", "ТКП 45 - 4.03 - 267-2012") == "ТКП 45-4.03-267-2012"
    assert search_query("ТКП", "T K P 45-4.03-267-2012") == "ТКП 45-4.03-267-2012"
    assert search_query("ТКП", "ТКП45-4.03-267-2012 (02250)") == "ТКП 45-4.03-267-2012"
    qs = search_queries("ТКП", "Т К П 45 - 4.03 - 267 - 2012")
    assert qs[0] == "ТКП 45-4.03-267-2012"
    assert "45-4.03-267-2012" in qs


def test_ost_normalizes_space_before_dot():
    assert search_query("ОСТ", "ОСТ 34 10.761-97") == "ОСТ 34.10.761-97"


def test_parse_card_html_snip():
    fields = parse_card_html(CARD_HTML)
    assert fields["Обозначение"] == "СНиП 3.05.02-88"
    assert fields["Дата введения"] == "01.07.1988"
    assert fields["Дата отмены"] == "25.04.2026"


def test_validity_status_cancelled():
    assert (
        validity_status(
            parse_ru_date("01.07.1988"),
            parse_ru_date("25.04.2026"),
            today=date(2026, 6, 11),
        )
        == "отменён"
    )


def test_validity_status_active_before_cancel():
    assert (
        validity_status(
            parse_ru_date("01.07.1988"),
            parse_ru_date("25.04.2026"),
            today=date(2026, 1, 1),
        )
        == "актуален"
    )


class _FakeClient(StnClient):
    def __init__(self) -> None:
        self.base = "https://example.test/"
        self.timeout = 5

    def search_all(self, queries: list[str]):
        for q in queries:
            if "3.05.02-88" in q:
                return [
                    {
                        "docid": "160",
                        "code": "СНиП 3.05.02-88",
                        "name": "Газоснабжение",
                        "activitydate": "1988-07-01",
                        "status": "1",
                    },
                    {
                        "docid": "117",
                        "code": "Изменение №1 СНиП 3.05.02-88",
                        "name": "Изменение №1 СНиП 3.05.02-88",
                        "activitydate": "1994-06-01",
                        "status": "1",
                    },
                ]
        return []

    def search_escalated(self, kind, ref, queries, *, max_queries=None, deadline=None):
        rows = self.search_all(queries[: max_queries or len(queries)])
        from belener.stn_lookup import _pick_best_match

        match = _pick_best_match(kind, ref, rows)
        return match, "; ".join(queries[:4])

    def fetch_card(self, doc_id: str) -> str:
        assert doc_id == "160"
        return CARD_HTML


def test_pick_base_not_amendment(monkeypatch):
    monkeypatch.setattr("belener.stn_lookup.stn_lookup_enabled", lambda: True)
    res = lookup_one("СНиП", "СНиП 3.05.02-88", client=_FakeClient(), today=date(2026, 6, 11))
    assert res.found
    assert res.stn_code == "СНиП 3.05.02-88"
    assert res.intro_date == "01.07.1988"
    assert res.cancel_date == "25.04.2026"
    assert res.status == "отменён"
    assert "Изменение" not in res.stn_code


def test_check_fund_kinds(monkeypatch):
    monkeypatch.setattr("belener.stn_lookup.stn_lookup_enabled", lambda: True)
    refs = [
        {"kind": "ОСТ", "ref": "ОСТ 34 10.761-97"},
        {"kind": "ГОСТ", "ref": "ГОСТ 10705-80"},
        {"kind": "СНиП", "ref": "СНиП 3.05.02-88"},
        {"kind": "СТП", "ref": "СТП 34 17.101"},
    ]
    out = check_normative_refs_stn(refs, client=_FakeClient(), today=date(2026, 6, 11))
    kinds = {c.kind for c in out}
    assert kinds == {"ГОСТ", "ОСТ", "СНиП"}
    assert len(out) == 3


def test_parse_card_html_ips():
    ips_html = Path(__file__).resolve().parents[1].joinpath(
        "scripts", "_ips_card_sample.html"
    )
    if not ips_html.is_file():
        return
    fields = parse_card_html(ips_html.read_text(encoding="utf-8"))
    assert "ТКП 45-4.03-267-2012" in fields["Обозначение"]
    assert fields["Дата введения"] == "01.12.2012"
    assert fields["Дата отмены"] == "21.09.2020"


def test_tkp_not_found_without_ips(monkeypatch):
    monkeypatch.setattr("belener.stn_lookup.stn_lookup_enabled", lambda: True)

    class _EmptyClient(_FakeClient):
        _logged_in = False

        def search_all(self, queries):
            return []

        def search_escalated(self, kind, ref, queries, *, max_queries=None, deadline=None):
            return None, "; ".join(queries[:4])

    res = lookup_one("ТКП", "ТКП 45-4.03-267-2012", client=_EmptyClient(), today=date(2026, 6, 11))
    assert not res.found
    assert "IPS" in res.status


def test_ref_keeps_sheet_ref_on_stn(monkeypatch):
    monkeypatch.setattr("belener.stn_lookup.stn_lookup_enabled", lambda: True)

    class _VariantClient(_FakeClient):
        def search_all(self, queries):
            for q in queries:
                if "8969" in q:
                    return [
                        {
                            "docid": "999",
                            "code": "ГОСТ 8969-75",
                            "name": "Электроды",
                            "activitydate": "1975-01-01",
                            "status": "1",
                        }
                    ]
            return super().search_all(queries)

        def search_escalated(self, kind, ref, queries, *, max_queries=None, deadline=None):
            rows = self.search_all(queries[: max_queries or len(queries)])
            from belener.stn_lookup import _pick_best_match

            match = _pick_best_match(kind, ref, rows)
            return match, "; ".join(queries[:4])

        def fetch_card(self, doc_id: str) -> str:
            if doc_id == "999":
                return (
                    '<table class="doc-card-table">'
                    "<tr><td class=\"doc-card-header\">Обозначение</td><td>ГОСТ 8969-75</td></tr>"
                    "<tr><td class=\"doc-card-header\">Дата введения</td><td>01.01.1975</td></tr>"
                    "</table>"
                )
            return super().fetch_card(doc_id)

    refs = [{"kind": "ГОСТ", "ref": "ГОСТ 8962-75"}]
    refined, checks = refine_and_check_normative_refs(refs, client=_VariantClient(), today=date(2026, 6, 11))
    assert refined[0]["ref"] == "ГОСТ 8962-75"
    assert not checks[0].found

    md = "\n".join(
        stn_checks_to_markdown(
            [
                StnCheckResult(
                    kind="СНиП",
                    ref="СНиП 3.05.02-88",
                    query="СНиП 3.05.02-88",
                    found=True,
                    stn_code="СНиП 3.05.02-88",
                    intro_date="01.07.1988",
                    cancel_date="25.04.2026",
                    status="отменён",
                )
            ]
        )
    )
    assert "normy.stn.by" in md
    assert "01.07.1988" in md
    assert "отменён" in md
