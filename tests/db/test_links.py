import sqlite3

import pytest

from scb_extract.db.build import SCHEMA
from scb_extract.db.links import (
    Agencies,
    link_product_aliases,
    link_subject_cards,
    norm,
)
from scb_extract.sources.downloads import PXWEB_CODE


def test_norm_ignores_case_prefix_and_trailing_abbreviation():
    assert norm("Statistiska centralbyrån (SCB)") == "statistiska centralbyrån"
    assert norm("STATISTISKA  CENTRALBYRÅN") == "statistiska centralbyrån"
    assert norm("Statistikansvarig myndighet: Arbetsmiljöverket") == "arbetsmiljöverket"
    assert norm(None) == ""


def test_agencies_register_abbreviations_and_refuse_ambiguity():
    agencies = Agencies()
    sos = agencies.create("Socialstyrelsen", "calendar")
    agencies.resolve_or_create("Socialstyrelsen (SoS)", "product_page")
    assert agencies.resolve("SOS") == (sos, None)
    other = agencies.create("Sjöfartsverket", "test")
    agencies.alias("SV", sos, "test")
    agencies.alias("SV", other, "test")
    assert agencies.resolve("SV") == (None, "ambiguous_agency")
    assert agencies.resolve("Okänd") == (None, "unknown_agency")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.statistikdatabasen.scb.se/pxweb/sv/ssd/START__NV__NV0119/IVPKNAr/", ("NV", "NV0119")),
        ("https://www.statistikdatabasen.scb.se/pxweb/sv/ssd/START__PR__PR0301__PR0301G/X/", ("PR", "PR0301")),
        ("https://www.statistikdatabasen.scb.se/sq/150128", (None, None)),
        ("https://example.se/START__PI__PI2020X/", (None, None)),
    ],
)
def test_pxweb_codes_are_parsed_only_from_start_segments(url, expected):
    match = PXWEB_CODE.search(url)
    assert ((match[1], match[2]) if match else (None, None)) == expected


def test_subject_cards_prefer_the_alias_target_for_a_shared_page():
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    page = "https://www.scb.se/hitta-statistik/nationalrakenskaper/"
    con.executemany(
        "INSERT INTO product_page (page_key, product_code, canonical_url) VALUES (?, ?, ?)",
        [
            ("AA0101", "AA0101", page),
            ("NR0001", "NR0001", page),
            ("AM0401", "AM0401", "https://www.scb.se/aku/"),
            ("f" * 64, None, "https://www.scb.se/elsewhere/"),
        ],
    )
    con.executemany(
        "INSERT INTO subject_product_card (subject_code, ordinal, link_canonical_url) VALUES ('NR', ?, ?)",
        [(1, page), (2, "https://www.scb.se/aku/"), (3, "https://www.scb.se/elsewhere/")],
    )
    link_product_aliases(
        con, ["Requested calendar code AA0101; page short address identifies NR0001"]
    )
    link_subject_cards(con)
    assert con.execute("SELECT * FROM product_alias").fetchall() == [
        ("AA0101", "NR0001", "products manifest warning")
    ]
    cards = con.execute(
        "SELECT ordinal, product_code, product_code_origin FROM subject_product_card ORDER BY ordinal"
    ).fetchall()
    assert cards == [(1, "NR0001", "url_match"), (2, "AM0401", "url_match"), (3, None, "unresolved")]
