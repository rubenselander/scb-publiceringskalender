"""The compact database: four tables, agency slugs only, nested facts as JSON."""

import json
import sqlite3

import pytest

from scb_extract import __main__ as cli
from scb_extract.db.compact import build_compact, slug
from tests.db.conftest import CONFLICT_URL


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Statistiska centralbyrån", "statistiska_centralbyran"),
        ("Statistiska centralbyrån (SCB)", "statistiska_centralbyran"),
        ("Havs- och vattenmyndigheten", "havs_och_vattenmyndigheten"),
        ("Myndigheten för familjerätt och föräldraskapsstöd", "myndigheten_for_familjeratt_och_foraldraskapsstod"),
        ("Universitetskanslersämbetet", "universitetskanslersambetet"),
    ],
)
def test_slug(name, expected):
    assert slug(name) == expected


@pytest.fixture
def compact(populated, tmp_path):
    out = tmp_path / "compact.db"
    build_compact(populated, out)
    con = sqlite3.connect(out)
    yield con
    con.close()


def rows(con, sql, *args):
    return con.execute(sql, args).fetchall()


def test_only_four_tables_and_no_reference_columns(compact):
    tables = {name for (name,) in rows(compact, "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {"product", "agency", "subject", "meta"}
    columns = {
        column[1] for table in tables for column in rows(compact, f"PRAGMA table_info({table})")
    }
    assert not columns & {"agency_id", "canonical_url", "publication_status", "url_sha", "page_key"}


def test_agencies_are_slugs_everywhere(compact):
    slugs = {slug_ for (slug_,) in rows(compact, "SELECT slug FROM agency")}
    used = {agency for (agency,) in rows(compact, "SELECT DISTINCT agency FROM product WHERE agency IS NOT NULL")}
    assert used <= slugs
    assert all(value == slug(value) for value in slugs)
    scb = rows(compact, "SELECT org_nr, official FROM agency WHERE slug = 'statistiska_centralbyran'")
    assert scb == [("202100-0837", 1)]
    assert rows(compact, "SELECT agency FROM product WHERE code = 'PR0701'") == [("konjunkturinstitutet",)]


def test_publications_are_deduplicated_json_with_other_publishers(compact):
    (am,) = rows(compact, "SELECT publications FROM product WHERE code = 'AM0201'")[0]
    assert json.loads(am) == [{"date": "2026-02-01", "period": "2026", "forms": ["Statistiknyhet"]}]
    (pr,) = rows(compact, "SELECT publications FROM product WHERE code = 'PR0701'")[0]
    assert json.loads(pr)[0]["by"] == ["konjunkturinstitutet", "statistiska_centralbyran"]


def test_documents_changes_and_hvd_are_baked_into_products(compact):
    (documents,) = rows(compact, "SELECT documents FROM product WHERE code = 'AM0201'")[0]
    documents = json.loads(documents)
    assert {"type": "kvalitetsdeklaration", "year": "2016", "url": CONFLICT_URL} in documents
    assert any(d.get("candidate") and d["url"] == "https://example.se/x.pdf" for d in documents)
    (be_documents,) = rows(compact, "SELECT documents FROM product WHERE code = 'BE0101'")[0]
    assert any(d["url"] == CONFLICT_URL for d in json.loads(be_documents))
    changed = rows(compact, "SELECT changes FROM product WHERE changes IS NOT NULL")
    assert changed and all(json.loads(c)[0]["text"] for (c,) in changed)
    hvd = rows(compact, "SELECT hvd FROM product WHERE hvd IS NOT NULL")
    assert sum(len(json.loads(h)) for (h,) in hvd) == 22


def test_subject_and_meta(compact):
    assert rows(compact, "SELECT count(*) FROM subject") == [(22,)]
    (areas,) = rows(compact, "SELECT areas FROM subject WHERE code = 'AM'")[0]
    assert len(json.loads(areas)) == 7
    assert rows(compact, "SELECT subject FROM product WHERE code = 'AM0201'") == [("AM",)]
    keys = {key for (key,) in rows(compact, "SELECT key FROM meta")}
    assert {"built_at", "not_fresh"} <= keys


def test_cli_and_size(populated, tmp_path):
    out = tmp_path / "cli.db"
    assert cli.main(["build-compact-db", "--data-dir", str(populated), "--out", str(out)]) == 0
    full = tmp_path / "full.db"
    assert cli.main(["build-db", "--data-dir", str(populated), "--out", str(full)]) == 0
    assert out.stat().st_size < full.stat().st_size
    assert not (tmp_path / "cli.db.tmp").exists()
