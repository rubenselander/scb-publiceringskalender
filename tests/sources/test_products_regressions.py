"""Identity, source grouping, and failure coverage beyond inventory fixtures."""

import json

import httpx

from scb_extract.core import ExtractionContext, url_key
from scb_extract.sources.products import (
    ADAPTERS,
    DOCUMENTATION_URL,
    SUBJECTS_URL,
    parse_documentation_index,
    parse_product,
    parse_subject,
)


def test_requested_code_is_not_overwritten_by_redirect_identity(snapshot):
    raw = snapshot("product_be0101")
    original = raw.content
    page = parse_product(raw, "BE9999")
    assert page.product_code == "BE9999"
    assert "be0101" in page.short_address
    assert "BE0101" in page.provenance.warnings[0]
    assert raw.content == original
    assert raw.request.url.endswith("BE0101")


def html(content):
    return httpx.Response(
        200, content=content, headers={"content-type": "text/html; charset=utf-8"}
    )


def listing(*urls):
    items = "".join(f'<li><a href="{url}">Entry</a></li>' for url in urls)
    return f'<div id="pageContent"><h1>Dokumentation</h1><h2>A</h2><ul>{items}</ul></div>'.encode()


def test_documentation_subseries_are_under_their_source_document_type(snapshot):
    page = parse_product(snapshot("product_be0101"))
    archive = next(
        group
        for group in page.sections
        if group.links
        and any("Uppdateras ej" in heading.text for heading in group.heading_path)
    )
    assert "Statistikens kvalitet" in archive.heading_path[-2].text
    assert "Uppdateras ej" in archive.heading_path[-1].text


def test_subject_agency_and_tags_are_observed_without_code_guessing(snapshot):
    page = parse_subject(snapshot("subject_am"), "AM")
    cards = [card for area in page.areas for card in area.products]
    assert any(
        "Arbetsmiljöverket" in (card.responsible_agency_text or "") for card in cards
    )
    assert any(card.tags_text for card in cards)
    assert all(card.product_code is None for card in cards)
    assert all(area.source_url is None for area in page.areas)


def test_calendar_duplicates_fetch_once_and_failures_keep_requested_keys(
    tmp_path, snapshot
):
    raw = snapshot("product_pr0701")
    calendar = tmp_path / "calendar.json"
    calendar.write_text(
        json.dumps(
            [
                {"product_code": "PR0701", "product_url": raw.request.url},
                {"product_code": "PR0701", "product_url": raw.request.url},
                {"product_code": "AM9999", "product_url": "https://www.scb.se/AM9999"},
            ]
        )
    )
    requests = []
    canonical = parse_product(raw).provenance.canonical_url

    def respond(request):
        requests.append(str(request.url))
        if str(request.url) == DOCUMENTATION_URL:
            return html(listing(canonical + "#_Dokumentation"))
        if request.url.path == "/PR0701":
            return html(raw.content)
        return httpx.Response(404, content=raw.content)

    with ExtractionContext(
        tmp_path / "raw",
        calendar_path=calendar,
        transport=httpx.MockTransport(respond),
        retry_delay=0,
    ) as context:
        result = ADAPTERS["products"].collect(context)
    assert result.discovered == ["AM9999", "PR0701"]
    assert set(result.documents) == {"PR0701"}
    assert set(result.failures) == {"AM9999"}
    assert result.discovery_complete

    assert requests.count(raw.request.url) == 1
    # The index entry is the calendar page under its canonical URL: no second request.
    assert canonical not in requests


def test_subject_index_failure_marks_discovery_incomplete(tmp_path):
    with ExtractionContext(
        tmp_path / "raw",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(404, text="missing")
        ),
        retry_delay=0,
    ) as context:
        result = ADAPTERS["subjects"].collect(context)
    assert not result.discovery_complete
    assert set(result.failures) == {"index"}
    assert not result.documents


def test_documentation_index_failure_keeps_calendar_pages(tmp_path, snapshot):
    raw = snapshot("product_pr0701")
    calendar = tmp_path / "calendar.json"
    calendar.write_text(json.dumps([{"product_code": "PR0701", "product_url": raw.request.url}]))

    def respond(request):
        if request.url.path == "/PR0701":
            return html(raw.content)
        return httpx.Response(404, text="missing")

    with ExtractionContext(
        tmp_path / "raw", calendar_path=calendar, transport=httpx.MockTransport(respond), retry_delay=0
    ) as context:
        result = ADAPTERS["products"].collect(context)
    assert set(result.documents) == {"PR0701"}
    assert set(result.failures) == {"documentation_index"}
    assert not result.discovery_complete


def test_documentation_index_adds_pages_missing_from_the_calendar(tmp_path, snapshot):
    product = snapshot("product_am0201")
    extra = snapshot("product_be0101")
    canonical = parse_product(product).provenance.canonical_url
    extra_url = parse_product(extra).provenance.canonical_url
    missing = "https://www.scb.se/missing-product/"
    calendar = tmp_path / "calendar.json"
    calendar.write_text(json.dumps([{"product_code": "AM0201", "product_url": product.request.url}]))
    requests = []

    def respond(request):
        url = str(request.url)
        requests.append(url)
        if url == product.request.url:
            return html(product.content)
        if url == DOCUMENTATION_URL:
            return html(listing(canonical + "#_Dokumentation", missing, extra_url))
        if url == extra_url:
            return html(extra.content)
        return httpx.Response(404, text="missing")

    with ExtractionContext(
        tmp_path / "raw", calendar_path=calendar, transport=httpx.MockTransport(respond), retry_delay=0
    ) as context:
        result = ADAPTERS["products"].collect(context)
    assert requests == [product.request.url, DOCUMENTATION_URL, missing, extra_url]
    assert set(result.documents) == {"AM0201", "BE0101"}
    assert result.documents["BE0101"].product_code_source == "page_short_address"
    assert set(result.failures) == {url_key(missing)}
    assert result.discovered == ["AM0201", url_key(missing), "BE0101"]
    assert result.discovery_complete

    # Replay offline with an empty memory cache: the canonical AM0201 URL was never
    # requested, so it must resolve to the archived short-address response.
    with ExtractionContext(tmp_path / "raw", calendar_path=calendar, offline=True) as context:
        replayed = ADAPTERS["products"].collect(context)
    assert replayed.discovered == result.discovered
    assert replayed.failures.keys() == result.failures.keys()
    assert {
        key: page.model_dump(mode="json") for key, page in replayed.documents.items()
    } == {key: page.model_dump(mode="json") for key, page in result.documents.items()}


def test_subject_collection_does_not_infer_codes_from_product_cache(tmp_path, snapshot):
    product = snapshot("product_am0201")
    subject = snapshot("subject_am")
    listing = f'<div id="pageContent"><h1>Subjects</h1><div class="xhtmlText"><ul><li><a href="{subject.request.url}">AM Arbetsmarknad</a></li></ul></div></div>'
    calendar = tmp_path / "calendar.json"
    calendar.write_text(
        json.dumps([{"product_code": "AM0201", "product_url": product.request.url}])
    )

    def respond(request):
        content = (
            listing.encode() if str(request.url) == SUBJECTS_URL else subject.content
        )
        return httpx.Response(
            200, content=content, headers={"content-type": "text/html; charset=utf-8"}
        )

    with ExtractionContext(
        tmp_path / "raw", calendar_path=calendar, transport=httpx.MockTransport(respond)
    ) as context:
        context.cache[product.request.key] = product
        result = ADAPTERS["subjects"].collect(context)
    assert not result.failures
    cards = [card for area in result.documents["AM"].areas for card in area.products]
    ks = next(card for card in cards if "Kortperiodisk" in card.link.text)
    assert ks.product_code is None
    assert ks.product_code_source == "unknown"


def test_calendar_code_failing_at_short_url_is_recovered_from_index(tmp_path, snapshot):
    product = snapshot("product_am0201")
    canonical = parse_product(product).provenance.canonical_url
    calendar = tmp_path / "calendar.json"
    calendar.write_text(json.dumps([{"product_code": "AM0201", "product_url": product.request.url}]))

    def respond(request):
        url = str(request.url)
        if url == DOCUMENTATION_URL:
            return html(listing(canonical))
        if url == canonical:
            return html(product.content)
        return httpx.Response(404, text="missing")

    with ExtractionContext(
        tmp_path / "raw", calendar_path=calendar, transport=httpx.MockTransport(respond), retry_delay=0
    ) as context:
        result = ADAPTERS["products"].collect(context)
    assert not result.failures
    assert result.documents["AM0201"].product_code_source == "page_short_address"
    assert any("recovered from the documentation index" in w for w in result.warnings)
    assert result.discovered == ["AM0201"]


def test_content_list_keeps_direct_text_and_inline_words_without_navigation(snapshot):
    raw = snapshot("product_am0201")
    content = '<div id="pageContent"><h1>Source</h1><section aria-labelledby="_Dokumentation"><h2 id="_Dokumentation">Dokumentation</h2><ul><li>Plain source item</li><li>Text with <strong>emphasis</strong> and <a href="/source.pdf">a source link</a>.</li><li>Outer text<ul><li>Nested text</li></ul></li></ul><nav><ul><li>Navigation label</li></ul></nav></section></div>'
    document = parse_product(raw.model_copy(update={"content": content.encode()}))
    paragraphs = [
        paragraph for group in document.sections for paragraph in group.paragraphs
    ]
    assert "Plain source item" in paragraphs
    assert "Text with emphasis and a source link ." in paragraphs
    assert "Outer text" in paragraphs
    assert "Nested text" in paragraphs
    assert "Navigation label" not in paragraphs
    assert [link.href for group in document.sections for link in group.links] == [
        "/source.pdf"
    ]


def test_documentation_index_reads_the_web_component_layout(snapshot):
    raw = snapshot("documentation")
    content = (
        '<div id="pageContent"><h1>Dokumentation</h1><scb-scrollspy><div slot="content">'
        '<section id="A"><scb-link-card><span slot="heading">A</span>'
        '<scb-link href="https://www.scb.se/hitta-statistik/a/aborter/#_Dokumentation" slot="links">Aborter</scb-link>'
        '<scb-link href="/hitta-statistik/a/aku/" slot="links">AKU</scb-link>'
        "</scb-link-card></section>"
        '<section id="Ö"><scb-link-card><scb-link href="/hitta-statistik/o/x/" slot="links">Ö</scb-link>'
        "</scb-link-card></section></div></scb-scrollspy></div>"
    )
    index = parse_documentation_index(raw.model_copy(update={"content": content.encode()}))
    assert [(e.letter, e.link.text) for e in index.entries] == [("A", "Aborter"), ("A", "AKU"), ("Ö", "Ö")]
    assert index.unfetched_page_urls == [
        "https://www.scb.se/hitta-statistik/a/aborter/",
        "https://www.scb.se/hitta-statistik/a/aku/",
        "https://www.scb.se/hitta-statistik/o/x/",
    ]
