"""Identity, source grouping, and failure coverage beyond inventory fixtures."""

import json

import httpx
import pytest

from scb_extract.core import ExtractionContext, FetchRequest, url_key
from scb_extract.output import publish
from scb_extract.sources.products import (
    ADAPTERS,
    DOCUMENTATION_URL,
    SUBJECTS_URL,
    parse_documentation,
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


def test_documentation_subseries_are_under_their_source_document_type(snapshot):
    page = parse_documentation(snapshot("product_be0101"))
    archive = next(
        group
        for group in page.groups
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

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(
            200 if request.url.path == "/PR0701" else 404,
            content=raw.content,
            headers={"content-type": "text/html; charset=utf-8"},
        )

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


@pytest.mark.parametrize("source", ["subjects", "documentation"])
def test_index_failure_marks_discovery_incomplete(source, tmp_path):
    with ExtractionContext(
        tmp_path / "raw",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(404, text="missing")
        ),
        retry_delay=0,
    ) as context:
        result = ADAPTERS[source].collect(context)
    assert not result.discovery_complete
    assert set(result.failures) == {"index"}
    assert not result.documents


def test_documentation_reuses_cached_canonical_page_and_records_failed_target(
    tmp_path, snapshot
):
    product = snapshot("product_am0201")
    canonical = parse_product(product).provenance.canonical_url
    missing = "https://www.scb.se/missing-product/"
    listing = f'<div id="pageContent"><h1>Dokumentation</h1><h2>A</h2><ul><li><a href="{canonical}#_Dokumentation">KS</a></li><li><a href="{missing}">Missing</a></li></ul></div>'
    requests = []

    def respond(request):
        url = str(request.url)
        requests.append(url)
        if url == product.request.url:
            return httpx.Response(
                200,
                content=product.content,
                headers={"content-type": "text/html; charset=utf-8"},
            )
        if url == DOCUMENTATION_URL:
            return httpx.Response(
                200, text=listing, headers={"content-type": "text/html; charset=utf-8"}
            )
        return httpx.Response(404, text="missing")

    with ExtractionContext(
        tmp_path / "raw", transport=httpx.MockTransport(respond), retry_delay=0
    ) as context:
        context.fetch(FetchRequest(url=product.request.url))
        result = ADAPTERS["documentation"].collect(context)
    assert requests == [product.request.url, DOCUMENTATION_URL, missing]
    assert result.documents[url_key(canonical)].product_code == "AM0201"
    assert result.failures.keys() == {url_key(missing)}
    assert not result.documents["index"].traversal_complete
    assert result.documents["index"].unfetched_page_urls == [missing]
    assert result.discovery_complete

    # Replay just documentation with an empty memory cache: only the short
    # product request was archived, never its canonical long URL.
    with ExtractionContext(tmp_path / "raw", offline=True) as context:
        replayed = ADAPTERS["documentation"].collect(context)
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


def test_documentation_failed_refresh_retains_same_url_identity(tmp_path, snapshot):
    product = snapshot("product_am0201")
    canonical = parse_product(product).provenance.canonical_url
    listing = f'<div id="pageContent"><h1>Dokumentation</h1><h2>A</h2><ul><li><a href="{canonical}#_Dokumentation">KS</a></li></ul></div>'
    successful = True

    def respond(request):
        if str(request.url) == DOCUMENTATION_URL:
            return httpx.Response(
                200, text=listing, headers={"content-type": "text/html"}
            )
        return httpx.Response(
            200 if successful else 404,
            content=product.content,
            headers={"content-type": "text/html; charset=utf-8"},
        )

    with ExtractionContext(
        tmp_path / "raw-first", transport=httpx.MockTransport(respond), retry_delay=0
    ) as context:
        first = ADAPTERS["documentation"].collect(context)
    key = url_key(canonical)
    output = tmp_path / "output"
    assert publish(first, output)["records"][key]["status"] == "fresh"
    last_good = (output / "documentation" / f"{key}.json").read_bytes()
    successful = False
    with ExtractionContext(
        tmp_path / "raw-refresh", transport=httpx.MockTransport(respond), retry_delay=0
    ) as context:
        refresh = ADAPTERS["documentation"].collect(context)
    assert refresh.discovered == first.discovered
    assert set(refresh.failures) == {key}
    assert publish(refresh, output)["records"][key]["status"] == "retained"
    assert (output / "documentation" / f"{key}.json").read_bytes() == last_good


def test_content_list_keeps_direct_text_and_inline_words_without_navigation(snapshot):
    raw = snapshot("product_am0201")
    content = '<div id="pageContent"><h1>Source</h1><section aria-labelledby="_Dokumentation"><h2 id="_Dokumentation">Dokumentation</h2><ul><li>Plain source item</li><li>Text with <strong>emphasis</strong> and <a href="/source.pdf">a source link</a>.</li><li>Outer text<ul><li>Nested text</li></ul></li></ul><nav><ul><li>Navigation label</li></ul></nav></section></div>'
    document = parse_documentation(raw.model_copy(update={"content": content.encode()}))
    paragraphs = [
        paragraph for group in document.groups for paragraph in group.paragraphs
    ]
    assert "Plain source item" in paragraphs
    assert "Text with emphasis and a source link ." in paragraphs
    assert "Outer text" in paragraphs
    assert "Nested text" in paragraphs
    assert "Navigation label" not in paragraphs
    assert [link.href for group in document.groups for link in group.links] == [
        "/source.pdf"
    ]
