from scb_extract.sources.products import (
    parse_documentation,
    parse_documentation_index,
    parse_product,
    parse_subject,
    parse_subject_index,
)


def test_product_identity_and_external_agency(snapshot):
    page = parse_product(snapshot("product_pr0701"), "PR0701")
    assert page.product_code == "PR0701"
    assert "Konjunktur" in page.title
    assert "Konjunkturinstitutet" in page.responsible_agency.label
    assert page.provenance.canonical_url != page.provenance.response_url


def test_discontinued_product_and_documentation_mismatch(snapshot):
    page = parse_product(snapshot("product_am0201"), "AM0201")
    assert page.notices
    documentation = parse_documentation(snapshot("product_am0201"), "AM0201")
    links = [link for group in documentation.groups for link in group.links]
    assert any("2017" in link.text and "am0201_kd_2016" in link.url for link in links)
    assert not parse_documentation(
        snapshot("product_pr0701"), "PR0701"
    ).has_documentation_section


def test_subject_hierarchy_and_index(snapshot):
    assert len(parse_subject_index(snapshot("subjects")).subjects) == 22
    for name, code, areas, products in [
        ("subject_am", "AM", 7, 31),
        ("subject_bl", "BL", 5, 14),
    ]:
        page = parse_subject(snapshot(name), code)
        assert len(page.areas) == areas
        assert sum(len(area.products) for area in page.areas) == products
    assert len(parse_documentation_index(snapshot("documentation")).entries) == 459


def test_full_product_retains_document_groups(snapshot):
    page = parse_product(snapshot("product_be0101"), "BE0101")
    assert page.sections
    assert len(parse_documentation(snapshot("product_be0101"), "BE0101").groups) > 2
