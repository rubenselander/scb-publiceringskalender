from scb_extract.sources.downloads import (
    parse_changes,
    parse_economy,
    parse_hvd,
    parse_workbook,
)


def test_workbook_exact_headers_codes_and_values(snapshot):
    page = parse_workbook(snapshot("official_products_0"))
    assert len(page.rows) == 369
    assert len({row.product_code for row in page.rows}) == 369
    assert "Anvandare" in page.headers
    assert page.rows[0].product_code == "AM0501"


def test_hvd_preserves_all_link_occurrences(snapshot):
    page = parse_hvd(snapshot("hvd"))
    assert len(page.groups) == 22
    links = [link for group in page.groups for link in group.links]
    assert len(links) == 88
    assert sum("/sq/150128" in link.url for link in links) == 2


def test_economy_duplicate_titles_are_distinct(snapshot):
    page = parse_economy(snapshot("economy"))
    assert len(page.entries) == 11
    rates = [entry for entry in page.entries if entry.title == "Kort och lång ränta"]
    assert len(rates) == 2
    assert rates[0].url != rates[1].url


def test_pdf_page_evidence_and_explicit_date(snapshot):
    for index, pages in enumerate([2, 3, 4, 3]):
        report = parse_changes(snapshot(f"changes_{index}"))
        assert report.page_count == pages
        assert report.notices
        assert all(notice.pages and notice.description for notice in report.notices)
    report = parse_changes(snapshot("changes_1"))
    notice = next(n for n in report.notices if n.product_code == "SF0108")
    assert str(notice.effective_date) == "2025-04-10"
    assert notice.timing_claim == "source_claimed_effective"
    spanning = [n for n in report.notices if n.product_code == "SF0206"]
    assert any(1 in n.pages and 2 in n.pages for n in spanning)
