"""Shared input data for the database tests: fixture-derived extractions plus synthetic files."""

import json

import pytest

from scb_extract.core import SourceResult, url_key
from scb_extract.output import publish
from scb_extract.sources.agencies import (
    parse_european_agencies,
    parse_official_agencies,
    parse_registry_export,
)
from scb_extract.sources.downloads import (
    parse_changes,
    parse_changes_index,
    parse_hvd,
    parse_workbook,
    product_links_only,
)
from scb_extract.sources.products import (
    parse_product,
    parse_subject,
    parse_subject_index,
)

SCB = "Statistiska centralbyrån"
CONFLICT_URL = "https://www.scb.se/contentassets/x/am0201_kd_2016.pdf"
CODELESS_PAGE = url_key("https://www.scb.se/hitta-statistik/some-page-without-code/")


def calendar_row(code, date, agency=SCB, published_at=SCB, name="Produkt"):
    return {
        "publish_date": date, "product_code": code, "product_name": name,
        "reporting_round": None, "reference_period": "2026", "forms": ["Statistiknyhet"],
        "published_at": published_at, "responsible_agency": agency,
        "product_url": f"https://www.scb.se/{code}",
    }


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def publish_source(output, source, documents):
    publish(SourceResult(source=source, documents=documents, discovered=sorted(documents)), output)


def registry_reference(snapshot, names):
    """The tracked reference file format, built from the fixture register export."""
    group = parse_registry_export(snapshot("registry_export_2"), "2", "Statliga förvaltningsmyndigheter")
    records = []
    for agency in group.agencies:
        fields = {f.label: f.value for f in agency.fields}
        if fields.get("Namn", "").casefold() in names:
            records.append({
                "agency": fields["Namn"], "group_option_value": "2", "group_name": group.group_name,
                "fields": [f.model_dump() for f in agency.fields],
            })
    return {"source_url": "https://myndighetsregistret.scb.se/Myndighet", "records": records}


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    # Module-scoped so the fixture parsing below runs once; `snapshot` is function-scoped.
    return tmp_path_factory.mktemp("data")


@pytest.fixture
def populated(data_dir, snapshot):
    """Write extraction output from real fixtures once, plus synthetic calendar/harvest files."""
    marker = data_dir / ".populated"
    if marker.exists():
        return data_dir
    output = data_dir / "extractions"
    publish_source(output, "official_products", {"index": parse_workbook(snapshot("official_products_0"))})
    publish_source(output, "official_agencies", {"index": parse_official_agencies(snapshot("official_agencies"))})
    publish_source(output, "european_agencies", {"index": parse_european_agencies(snapshot("european_agencies"))})
    publish_source(output, "hvd", {"index": product_links_only(parse_hvd(snapshot("hvd")))})
    changes = {"index": parse_changes_index(snapshot("changes"))}
    for n in range(4):
        source = snapshot(f"changes_{n}")
        changes[url_key(source.request.url)] = parse_changes(source)
    publish_source(output, "changes", changes)
    # The adapter marks the index complete after fetching every subject; simulate that here so
    # the 20 subjects without a fixture page still load from the index.
    index = parse_subject_index(snapshot("subjects"))
    subjects = {"index": index.model_copy(update={"traversal_complete": True, "unfetched_subject_urls": []})}
    for code in ("AM", "BL"):
        subjects[code] = parse_subject(snapshot(f"subject_{code.lower()}"), code)
    publish_source(output, "subjects", subjects)
    products = {code: parse_product(snapshot(f"product_{code.lower()}"), code) for code in ("PR0701", "AM0201")}
    # BE0101 stands in for a page found through the A-Z documentation index (code from the page).
    products["BE0101"] = parse_product(snapshot("product_be0101"))
    products[CODELESS_PAGE] = products["PR0701"].model_copy(
        update={"product_code": None, "product_code_source": "unknown"}
    )
    publish_source(output, "products", products)
    reference = data_dir / "reference" / "agency_registry_linked.json"
    reference.parent.mkdir(parents=True)
    reference.write_text(
        json.dumps(registry_reference(snapshot, {SCB.casefold(), "konjunkturinstitutet"}), ensure_ascii=False),
        encoding="utf-8",
    )

    write_jsonl(data_dir / "calendar.jsonl", [
        calendar_row("PR0701", "2026-01-01", "Konjunkturinstitutet", f"Konjunkturinstitutet, {SCB}"),
        calendar_row("AM0201", "2026-02-01"),
        calendar_row("AM0201", "2026-02-01"),  # exact duplicate is kept
    ])
    (data_dir / "gaps.json").write_text('[{"from": "2006-06-12", "to": "2006-06-13", "expected": 4, "retrieved": 3}]', encoding="utf-8")
    (data_dir / "state.json").write_text('{"last_completed_run": "2026-10-05"}', encoding="utf-8")
    doc = {"source": "scb", "doc_type": "kvalitetsdeklaration", "year": "2016", "title": "KD 2016",
           "url": CONFLICT_URL, "filetype": "pdf", "review_required": False, "provenance": {},
           "subject_area": "Arbetsmarknad", "statistics_area": "Sysselsättning"}
    write_jsonl(data_dir / "dokumentation" / "dokument.jsonl", [
        {**doc, "document_id": "a", "agency": SCB, "product_code": "AM0201", "product_match": "code"},
        {**doc, "document_id": "b", "agency": "Påhittade myndigheten", "product_code": "BE0101",
         "product_match": "block"},
        {**doc, "document_id": "c", "url": "https://example.se/x.pdf", "agency": SCB,
         "product_code": None, "product_match": "name", "candidate_product_code": "AM0201",
         "review_required": True, "subject_area": None, "statistics_area": None},
    ])
    write_jsonl(data_dir / "dokumentation" / "sam_dokument.jsonl", [
        {"name": "x", "agency": SCB, "href": "https://example.se/y.pdf", "product_code": "XX01"},
    ])
    marker.touch()
    return data_dir
