"""Build the SQLite database from fixture-derived extraction output plus small synthetic files."""

import sqlite3

import pytest

from scb_extract import __main__ as cli
from scb_extract.db import build as build_module
from scb_extract.db.build import build
from tests.db.conftest import CODELESS_PAGE, CONFLICT_URL, SCB


@pytest.fixture
def db(populated, tmp_path):
    out = tmp_path / "scb.db"
    build(populated, out)
    con = sqlite3.connect(out)
    yield con
    con.close()


def scalar(con, sql, *args):
    return con.execute(sql, args).fetchone()[0]


def test_fixture_inventories_are_loaded(db):
    assert scalar(db, "SELECT count(*) FROM subject") == 22
    assert scalar(db, "SELECT count(*) FROM official_agency") == 29
    assert scalar(db, "SELECT count(*) FROM european_agency") == 21
    assert scalar(db, "SELECT count(*) FROM official_product_row") == 369
    assert scalar(db, "SELECT count(*) FROM hvd_link") == 22
    assert scalar(db, "SELECT count(*) FROM hvd_link WHERE product_code IS NULL") == 0
    assert scalar(db, "SELECT count(*) FROM changes_report") == 4
    assert scalar(db, "SELECT count(*) FROM product_page") == 4
    assert scalar(db, "SELECT count(*) FROM calendar_entry") == 3
    assert scalar(db, "SELECT max(occurrence) FROM calendar_entry WHERE product_code = 'AM0201'") == 2
    assert scalar(db, "SELECT count(*) FROM source_status WHERE status = 'fresh'") > 0
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not tables & {"documentation_page", "economy_diagram", "regulation", "scb_dokument", "registry_group"}


def test_agencies_link_short_long_and_registry_names(db):
    scb = scalar(db, "SELECT agency_id FROM agency WHERE canonical_name = ?", SCB)
    # Workbook "SCB" and official-agency "Statistiska centralbyrån (SCB)" resolve to the calendar agency.
    assert scalar(db, "SELECT count(*) FROM agency_alias WHERE alias = 'SCB' AND agency_id = ?", scb) >= 1
    assert scalar(
        db, "SELECT agency_id FROM official_agency WHERE agency_name = 'Statistiska centralbyrån (SCB)'"
    ) == scb
    assert scalar(db, "SELECT registry_org_nr FROM agency WHERE agency_id = ?", scb) == "202100-0837"
    assert scalar(db, "SELECT count(*) FROM registry_agency WHERE agency_id IS NOT NULL") == 2
    publishers = db.execute(
        "SELECT name, agency_id IS NOT NULL FROM calendar_entry_publisher WHERE entry_id = 1 ORDER BY ordinal"
    ).fetchall()
    assert publishers == [("Konjunkturinstitutet", 1), (SCB, 1)]
    assert scalar(db, "SELECT count(*) FROM official_product_row WHERE agency_id IS NULL "
                      "AND responsible_authority = 'SCB'") == 0


def test_unresolved_links_are_reported_not_dropped(db):
    assert scalar(db, "SELECT count(*) FROM harvest_document") == 3
    assert scalar(
        db, "SELECT issue FROM link_issues WHERE raw_value = 'Påhittade myndigheten'"
    ) == "unknown_agency"
    assert scalar(db, "SELECT issue FROM link_issues WHERE raw_value = 'XX01'") == "malformed_code"
    assert scalar(db, "SELECT count(*) FROM product WHERE product_code = 'XX01'") == 0


def test_document_code_claims_are_kept_side_by_side(db):
    claims = db.execute(
        "SELECT product_code, origin FROM document_product_claim WHERE url = ? ORDER BY 1", (CONFLICT_URL,)
    ).fetchall()
    assert claims == [("AM0201", "harvest_filename"), ("BE0101", "harvest_block")]
    assert scalar(db, "SELECT product_codes FROM v_document_code_conflicts WHERE url = ?", CONFLICT_URL) in (
        "AM0201,BE0101", "BE0101,AM0201",
    )
    # Candidate joins are stored but never counted as confirmed documents.
    assert scalar(db, "SELECT review_required FROM document_product_claim WHERE origin = 'harvest_candidate'") == 1
    assert scalar(db, "SELECT count(*) FROM v_document_code_conflicts WHERE url = 'https://example.se/x.pdf'") == 0
    # Product pages contribute claims for documents in their own Dokumentation section.
    assert scalar(
        db, "SELECT count(*) FROM document_product_claim WHERE product_code = 'AM0201' AND origin = 'product_page'"
    ) > 0


def test_subject_labels_come_from_dokument(db):
    claim = db.execute(
        "SELECT subject_code, subject_label, statistical_area_label FROM product_subject_claim "
        "WHERE product_code = 'AM0201' AND origin = 'harvest'"
    ).fetchone()
    assert claim == ("AM", "Arbetsmarknad", "Sysselsättning")


def test_product_pages_without_calendar_or_code(db):
    row = db.execute(
        "SELECT product_code, product_code_source FROM product_page WHERE page_key = 'BE0101'"
    ).fetchone()
    assert row == ("BE0101", "page_short_address")
    assert scalar(db, "SELECT has_product_page FROM product WHERE product_code = 'BE0101'") == 1
    assert scalar(db, "SELECT in_calendar FROM product WHERE product_code = 'BE0101'") == 0
    assert scalar(db, "SELECT product_code FROM product_page WHERE page_key = ?", CODELESS_PAGE) is None
    assert scalar(db, "SELECT count(*) FROM product_page_section WHERE page_key = ?", CODELESS_PAGE) > 0


def test_products_and_views(db):
    row = db.execute(
        "SELECT in_workbook, in_calendar, has_product_page, agency FROM v_product_overview "
        "WHERE product_code = 'PR0701'"
    ).fetchone()
    assert row[1:3] == (1, 1)
    assert row[3] == "Konjunkturinstitutet"
    assert scalar(db, "SELECT count(*) FROM product WHERE in_workbook") == 369
    assert scalar(db, "SELECT count(*) FROM subject_product_card WHERE product_code IS NOT NULL") > 0
    assert scalar(db, "SELECT count(*) FROM v_agency_products") > 0
    assert scalar(db, "SELECT count(*) FROM v_document_latest WHERE product_code = 'AM0201'") > 0
    assert scalar(db, "PRAGMA integrity_check") == "ok"


def test_build_is_deterministic_apart_from_build_time(populated, tmp_path):
    dumps = []
    for name in ("one.db", "two.db"):
        build(populated, tmp_path / name)
        con = sqlite3.connect(tmp_path / name)
        dumps.append([line for line in con.iterdump() if "build_info" not in line])
        con.close()
    assert dumps[0] == dumps[1]


def test_cli_builds_database_and_failed_build_keeps_previous(populated, tmp_path, monkeypatch):
    out = tmp_path / "cli.db"
    assert cli.main(["build-db", "--data-dir", str(populated), "--out", str(out)]) == 0
    before = out.read_bytes()

    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(build_module, "resolve", explode)
    with pytest.raises(RuntimeError):
        build(populated, out)
    assert out.read_bytes() == before
    assert not (tmp_path / "cli.db.tmp").exists()


def test_missing_inputs_build_empty_tables(tmp_path):
    report = build(tmp_path / "nothing", tmp_path / "empty.db")
    assert report["counts"]["calendar_entry"] == 0
    assert any("missing" in warning for warning in report["warnings"])
