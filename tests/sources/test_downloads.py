from scb_extract.sources.downloads import (
    parse_changes,
    parse_hvd,
    parse_workbook,
    product_links_only,
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
    assert page.total_source_links == 88
    coded = [link for link in links if link.product_code]
    assert len(coded) == 22
    assert {(link.subject_code, link.product_code) for link in coded} >= {("NV", "NV0119")}
    assert all(link.target_kind == "pxweb_table" for link in coded)


def test_hvd_output_keeps_only_rows_that_yield_a_product(snapshot):
    page = product_links_only(parse_hvd(snapshot("hvd")))
    links = [link for group in page.groups for link in group.links]
    assert page.product_links_only
    assert len(links) == 22
    assert all(link.product_code for link in links)
    assert all(group.links for group in page.groups)
    assert page.total_source_groups == 22 and page.total_source_links == 88


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
