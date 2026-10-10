from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from scb_extract.core import ExtractionContext
from scb_extract.sources.agencies import (
    ADAPTERS,
    parse_european_agencies,
    parse_official_agencies,
    parse_registry_detail,
    parse_registry_export,
    parse_registry_group,
)


def test_official_bold_hierarchy(snapshot):
    page = parse_official_agencies(snapshot("official_agencies"))
    assert len(page.agencies) == 29
    agency = next(a for a in page.agencies if "Finansinspektionen" in a.agency_name)
    assert len(agency.subjects) == 2
    assert any(s.statistical_areas for s in agency.subjects)
    assert page.scope_statements


def test_european_definition_and_central_bank(snapshot):
    page = parse_european_agencies(snapshot("european_agencies"))
    assert len(page.agency_names) == 21
    assert "Riksbanken" not in page.agency_names
    assert "Riksbanken" in page.central_bank_statement
    assert any("Eurostat" in s.text for s in page.scope_statements)


def test_all_registry_groups_and_blank_identified_rows(snapshot):
    for option, count in [
        ("2", 244),
        ("3", 5),
        ("4", 3),
        ("5", 6),
        ("6", 83),
        ("7", 108),
    ]:
        source = snapshot("registry_group_" + option)
        page = parse_registry_group(source, option, source.request.json_body["mynd"])
        assert len(page.agencies) == count
        if option == "7":
            fields = {f.label: f.value for f in page.agencies[0].fields}
            assert fields["LopNr"] == "201"
            assert fields["Namn"] == ""


def test_official_source_punctuation_and_scb_hierarchy(snapshot):
    page = parse_official_agencies(snapshot("official_agencies"))
    library = next(a for a in page.agencies if a.agency_name == "Kungliga biblioteket")
    pension = next(a for a in page.agencies if a.agency_name == "Pensionsmyndigheten")
    scb = next(
        a for a in page.agencies if a.agency_name == "Statistiska centralbyrån (SCB)"
    )
    assert library.subjects[0].subject_name == "Kultur, biblotek och fritid"
    assert pension.subjects[1].subject_name == "Socialförsäkring:"
    assert len(scb.subjects) == 12
    assert scb.statistics_url == "https://www.scb.se/"
    assert any(
        link.text == "(2001:100)"
        for statement in page.scope_statements
        for link in statement.links
    )


def test_registry_exports_keep_strings_and_blank_foreign_row(snapshot):
    for option, count in [
        ("2", 244),
        ("3", 5),
        ("4", 3),
        ("5", 6),
        ("6", 83),
        ("7", 108),
    ]:
        group = parse_registry_export(
            snapshot("registry_export_" + option), option, "fixture"
        )
        assert len(group.agencies) == count
        assert all(
            isinstance(field.value, str)
            for agency in group.agencies
            for field in agency.fields
        )
        if option == "2":
            fields = {field.label: field.value for field in group.agencies[0].fields}
            assert fields["Tfn"] == "087000800"
            assert fields["BesöksPostNr"] == "000 00"
            assert (
                fields["Webbadress"]
                == "www.kammarkollegiet.se/alkoholsortimentsnamnden"
            )
        if option == "7":
            assert all(field.value == "" for field in group.agencies[0].fields)


def test_registry_detail_correct_nullable_surfaces(snapshot):
    court = snapshot("registry_detail_6")
    assert court.request.json_body == {
        "peorgnr": None,
        "cfarnr": "19075852",
        "lopnr": None,
    }
    fields = {field.label: field.value for field in parse_registry_detail(court)}
    assert fields["Namn"] == "ARBETSDOMSTOLEN"
    assert fields["Tfn"] == "086176600"
    assert fields["PostAdress"] == "BOX 2018\n103 11 STOCKHOLM"
    foreign = {
        field.label: field.value
        for field in parse_registry_detail(snapshot("registry_detail_7"))
    }
    assert foreign["Namn"] == ""
    assert "Land" in foreign and "Ambassadör" in foreign


def test_all_agency_adapters_fixture_replay():
    with ExtractionContext(
        Path(__file__).parents[1] / "fixtures/raw", offline=True
    ) as context:
        for name, adapter in ADAPTERS.items():
            result = adapter.collect(context)
            assert result.source == name
            assert result.discovered == ["index"]
            assert not result.failures, result.failures
            assert result.discovery_complete
            assert result.documents["index"].coverage == "complete"
            if name == "agency_registry":
                groups = result.documents["index"].groups
                assert [group.source_row_count for group in groups] == [
                    244,
                    5,
                    3,
                    6,
                    83,
                    108,
                ]
                assert all(
                    group.retrieval_surface == "tsv_download" for group in groups
                )
                assert all(group.table_provenance for group in groups)
                assert (
                    next(
                        field.value
                        for field in groups[-1].agencies[0].fields
                        if field.label == "LopNr"
                    )
                    == "201"
                )


def test_registry_failed_download_and_detail_is_incomplete():
    class FailedDownloadContext(ExtractionContext):
        def fetch(self, request):
            if "/PrepareDownload" in request.url or "/HamtaEnMynd" in request.url:
                raise OSError("simulated unavailable detail/export")
            return super().fetch(request)

    with FailedDownloadContext(
        Path(__file__).parents[1] / "fixtures/raw", offline=True
    ) as context:
        result = ADAPTERS["agency_registry"].collect(context)
    assert not result.documents
    assert (
        "Group 2" in result.failures["index"] and "Group 7" in result.failures["index"]
    )
    assert not result.discovery_complete


def test_registry_rejects_truncated_export(snapshot):
    source = snapshot("registry_export_2")
    source = source.model_copy(update={"content": b"Namn\tPostNr\nAuthority\n"})
    with pytest.raises(ValueError, match="Malformed registry export row"):
        parse_registry_export(source, "2", "fixture")


def test_registry_detail_fallback_keeps_blank_identified_row(snapshot):
    """Injected one-row grid exercises fallback without pretending to be live."""
    detail = snapshot("registry_detail_7")

    class DetailFallbackContext(ExtractionContext):
        def fetch(self, request):
            if (
                "/PrepareDownload" in request.url
                and request.params.get("myndgrupp") == "Svenska utlandsmyndigheter"
            ):
                raise OSError("injected export failure")
            if "/HamtaEnMynd" in request.url:
                assert request.json_body == {
                    "peorgnr": None,
                    "cfarnr": None,
                    "lopnr": "201",
                }
                return detail
            source = super().fetch(request)
            if request.json_body == {"mynd": "Svenska utlandsmyndigheter"}:
                soup = BeautifulSoup(source.text, "lxml")
                for row in soup.select("tbody tr")[1:]:
                    row.decompose()
                return source.model_copy(update={"content": str(soup).encode("utf-8")})
            return source

    with DetailFallbackContext(
        Path(__file__).parents[1] / "fixtures/raw", offline=True
    ) as context:
        result = ADAPTERS["agency_registry"].collect(context)
    assert not result.failures
    group = result.documents["index"].groups[-1]
    assert group.retrieval_surface == "html_table"
    assert group.source_row_count == 1
    agency = group.agencies[0]
    assert (
        next(field.value for field in agency.fields if field.label == "LopNr") == "201"
    )
    assert agency.detail_provenance.content_sha256 == detail.sha256
    assert all(field.value == "" for field in agency.detail_fields)
