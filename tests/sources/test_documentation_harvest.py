import json
from datetime import date
from pathlib import Path

import pytest

import fetch_dokumentation as fetch
import parse_dokumentation as parse

FIXTURES = Path(__file__).parents[1] / "fixtures" / "documentation_harvest"


@pytest.fixture
def harvest(monkeypatch, tmp_path):
    for module in (fetch, parse):
        monkeypatch.setattr(module, "RAW", tmp_path / "raw")
    monkeypatch.setattr(fetch, "BUNDLE", tmp_path / "bundle")
    monkeypatch.setattr(fetch, "DATA", tmp_path / "data")
    monkeypatch.setattr(parse, "DOK", tmp_path / "data")
    monkeypatch.setattr(parse, "DATA", tmp_path)
    monkeypatch.setattr(fetch, "evidence", lambda url: {"response_url": url})
    monkeypatch.setattr(fetch.time, "sleep", lambda delay: None)
    return tmp_path


def test_scb_mid_fetch_failure_keeps_old_files(harvest, monkeypatch):
    out = fetch.RAW / "scb"
    out.mkdir(parents=True)
    old = out / "1.json"
    old.write_text("last good capture")
    index = (
        '<select id="amnesomradenDropDown">'
        + "".join(f'<option value="{i}">Area {i}</option>' for i in range(10))
        + "</select>"
    )

    def response(url, **kwargs):
        if url == fetch.SCB_INDEX:
            return 200, "text/html", index
        if url.endswith("=1"):
            raise fetch.SourceError("HTTP 503")
        return 200, "text/html", "<h3>Product</h3><a href='/AM0101'>Doc</a>"

    monkeypatch.setattr(fetch, "get", response)
    with pytest.raises(fetch.SourceError):
        fetch.fetch_scb()
    assert old.read_text() == "last good capture"
    assert not (out / "0.json").exists()


def test_failed_agency_preserves_last_good_data(harvest, monkeypatch):
    config = harvest / "sources.json"
    config.write_text(
        json.dumps([{"name": "agency", "start": ["https://example.org"]}])
    )
    monkeypatch.setattr(fetch, "SOURCES", config)
    old = fetch.RAW / "sam" / "agency.jsonl"
    old.parent.mkdir(parents=True)
    old.write_text("last good capture")
    monkeypatch.setattr(fetch, "get", lambda *a, **k: (403, "text/html", "blocked"))
    assert fetch.fetch_sam(set()) == (["agency"], 1)
    assert old.read_text() == "last good capture"
    assert (fetch.BUNDLE / "attempts" / "agency.jsonl").exists()


def test_capped_crawl_is_incomplete(harvest, monkeypatch):
    monkeypatch.setattr(
        fetch, "get", lambda *a, **k: (200, "text/html", '<a href="/next">Next</a>')
    )
    with pytest.raises(fetch.SourceError, match="incomplete"):
        fetch.crawl(
            {
                "name": "agency",
                "start": ["https://example.org"],
                "follow": ["example.org"],
                "max_pages": 1,
            }
        )


def test_relative_links_use_redirect_destination(harvest, monkeypatch):
    monkeypatch.setattr(
        fetch,
        "get",
        lambda *a, **k: (200, "text/html", '<a href="report.pdf">Report</a>'),
    )
    monkeypatch.setattr(
        fetch, "evidence", lambda url: {"response_url": "https://example.org/new/"}
    )
    pages = fetch.crawl({"name": "agency", "start": ["https://example.org/old"]})
    assert pages[0]["links"][0]["href"] == "https://example.org/new/report.pdf"


def test_partial_failure_returns_nonzero_without_claiming_completion(
    harvest, monkeypatch
):
    monkeypatch.setattr(fetch, "fetch_scb", lambda: None)
    monkeypatch.setattr(fetch, "fetch_sam", lambda only: (["blocked"], 2))
    fetch.DATA.mkdir()
    (fetch.DATA / "state.json").write_text(
        json.dumps({"last_completed_sam": "2025-01-01"})
    )
    assert fetch.main(["scb", "sam"]) == 1
    state = json.loads((fetch.DATA / "state.json").read_text())
    assert state["complete"] is False
    assert state["last_completed_sam"] == "2025-01-01"
    assert state["last_failures"] == ["sam/blocked"]


def test_selected_refresh_keeps_other_source_failures(harvest, monkeypatch):
    monkeypatch.setattr(fetch, "fetch_scb", lambda: None)
    fetch.DATA.mkdir()
    (fetch.DATA / "state.json").write_text(
        json.dumps({"last_failures": ["sam/blocked"]})
    )
    assert fetch.main(["scb"]) == 1
    assert json.loads((fetch.DATA / "state.json").read_text())["last_failures"] == [
        "sam/blocked"
    ]


def test_real_fixtures_preserve_source_context(harvest):
    for source in ("scb", "siris"):
        directory = parse.RAW / source
        directory.mkdir(parents=True)
        content = (FIXTURES / f"{source}.json").read_text("utf-8")
        (directory / ("1.json" if source == "scb" else "form.jsonl")).write_text(
            content if source == "scb" else json.dumps(json.loads(content)) + "\n",
            "utf-8",
        )
    scb, siris = parse.parse_scb(), parse.parse_siris()
    assert len(scb) == 1080 and len(siris) == 3
    assert scb[0]["product_code"] == "AM0501"
    assert scb[0]["produkt"] == "Arbetsmiljöundersökningen"
    assert siris[0]["doc_id"] == "554117"
    assert siris[0]["sos"] == ""
    assert all(row["source_url"] for row in scb + siris)
    assert all(row["url"].startswith("https://") for row in scb + siris)


def test_ambiguous_codes_and_blocks_remain_unmatched(harvest):
    assert parse.find_code("https://example.org/AM0101_BE0101.pdf") is None
    assert parse.find_code("https://example.org/AM0101extra.pdf") is None
    directory = parse.RAW / "scb"
    directory.mkdir(parents=True)
    (directory / "1.json").write_text(
        json.dumps(
            {
                "url": "https://example.org",
                "fetched": "2026-10-05",
                "amnesomrade": "Area",
                "html": '<h3>Product</h3><a href="/AM0101_kd.pdf">KD</a><a href="/BE0101_kd.pdf">KD</a><a href="/unknown.pdf">Unknown</a>',
            }
        )
    )
    assert parse.parse_scb()[-1]["product_code"] is None


def test_inferred_join_is_candidate_excluded_from_product_counts(harvest, monkeypatch):
    monkeypatch.setattr(
        parse,
        "products",
        lambda: {
            "AM0101": {
                "name": "Employment survey",
                "agency": "Agency",
                "last": "2026-10-01",
            }
        },
    )
    monkeypatch.setattr(parse, "parse_scb", list)
    monkeypatch.setattr(parse, "parse_siris", list)
    monkeypatch.setattr(
        parse,
        "parse_sam",
        lambda: [
            {
                "name": "agency",
                "agency": "Agency",
                "source_url": "https://example.org",
                "source_title": "Employment survey",
                "href": "https://example.org/kd.pdf",
                "text": "Employment survey",
                "product_code": None,
                "doc_type": "kvalitetsdeklaration",
                "year": "2026",
                "filetype": "pdf",
            }
        ],
    )
    parse.main(["--as-of", "2026-10-05"])
    doc = json.loads((parse.DOK / "dokument.jsonl").read_text())
    product = json.loads((parse.DOK / "produkter.jsonl").read_text())
    assert doc["product_code"] is None
    assert doc["candidate_product_code"] == "AM0101"
    assert doc["review_required"] is True
    assert product["documents"] == 0


def test_cached_agencies_survive_subset_refresh(harvest, monkeypatch):
    parse.DOK.mkdir()
    (parse.DOK / "sam_dokument.jsonl").write_text(
        json.dumps({"name": "old", "agency": "Old", "href": "https://old.org/kd.pdf"})
        + "\n"
    )
    monkeypatch.setattr(parse, "raw_files", lambda *args: [])
    assert parse.parse_sam()[0]["name"] == "old"


def test_activity_reference_is_explicit():
    assert parse.active("2025-10-05", date(2026, 10, 5))
    assert not parse.active("2025-10-05", date(2027, 10, 5))


def test_unknown_agency_is_rejected(harvest, monkeypatch):
    config = harvest / "sources.json"
    config.write_text(json.dumps([{"name": "known", "start": ["https://example.org"]}]))
    monkeypatch.setattr(fetch, "SOURCES", config)
    with pytest.raises(fetch.SourceError, match="Unknown agencies"):
        fetch.fetch_sam({"typo"})


def test_invalid_siris_response_keeps_previous_capture(harvest, monkeypatch):
    directory = fetch.RAW / "siris"
    directory.mkdir(parents=True)
    old = directory / "old.jsonl"
    old.write_text("last good capture")

    def response(url):
        if "verksamhetsformer" in url:
            return [{"kod": "School"}]
        if "omrade" in url:
            return [{"kod": "Area"}]
        if "lasar" in url:
            return [{"kod": "2026"}]
        return {"error": "invalid document response"}

    monkeypatch.setattr(fetch, "get_json", response)
    with pytest.raises(fetch.SourceError, match="expected document list"):
        fetch.fetch_siris()
    assert old.read_text() == "last good capture"


def test_empty_parse_preserves_existing_output(harvest, monkeypatch):
    parse.DOK.mkdir()
    old = parse.DOK / "dokument.jsonl"
    old.write_text("last good export")
    for function in ("parse_scb", "parse_siris", "parse_sam"):
        monkeypatch.setattr(parse, function, list)
    with pytest.raises(ValueError, match="previous exports preserved"):
        parse.main(["--as-of", "2026-10-05"])
    assert old.read_text() == "last good export"


def test_interrupted_generation_keeps_previous_membership(harvest, monkeypatch):
    first = harvest / "first"
    first.mkdir()
    (first / "1.json").write_text("old capture")
    fetch.publish_raw("scb", first)
    old_files = parse.raw_files("scb", ".json")
    second = harvest / "second"
    second.mkdir()
    (second / "1.json").write_text("new capture")
    (second / "2.json").write_text("another capture")
    copy = fetch.shutil.copyfile

    def interrupted(source, target):
        if source.name == "2.json":
            raise OSError("interrupted write")
        return copy(source, target)

    monkeypatch.setattr(fetch.shutil, "copyfile", interrupted)
    with pytest.raises(OSError, match="interrupted write"):
        fetch.publish_raw("scb", second)
    assert parse.raw_files("scb", ".json") == old_files
    assert old_files[0].read_text() == "old capture"


def test_javascript_only_agency_does_not_replace_previous_capture(harvest, monkeypatch):
    config = harvest / "sources.json"
    config.write_text(
        json.dumps([{"name": "agency", "start": ["https://example.org"]}])
    )
    monkeypatch.setattr(fetch, "SOURCES", config)
    monkeypatch.setattr(
        fetch,
        "get",
        lambda *args, **kwargs: (
            200,
            "text/html",
            '<div id="app"></div><script src="app.js"></script>',
        ),
    )
    assert fetch.fetch_sam(set()) == (["agency"], 1)
    assert not (fetch.RAW / "sam" / "agency.jsonl").exists()


def test_selected_agency_cannot_clear_family_failure(harvest, monkeypatch):
    monkeypatch.setattr(fetch, "fetch_sam", lambda only: ([], 1))
    fetch.DATA.mkdir()
    (fetch.DATA / "state.json").write_text(
        json.dumps({"last_failures": ["sam"], "last_completed_sam": "2025-01-01"})
    )
    assert fetch.main(["sam", "--only", "known"]) == 1
    state = json.loads((fetch.DATA / "state.json").read_text())
    assert state["last_failures"] == ["sam"]
    assert state["last_completed_sam"] == "2025-01-01"


def test_exports_carry_scb_subject_labels_and_drop_retired_files(harvest):
    for source in ("scb", "siris"):
        directory = parse.RAW / source
        directory.mkdir(parents=True)
        content = (FIXTURES / f"{source}.json").read_text("utf-8")
        (directory / ("1.json" if source == "scb" else "form.jsonl")).write_text(
            content if source == "scb" else json.dumps(json.loads(content)) + "\n", "utf-8"
        )
    parse.DOK.mkdir()
    for name in ("scb_dokument.jsonl", "scb_dokument.csv", "siris_dokument.csv", "sam_dokument.csv"):
        (parse.DOK / name).write_text("stale")
    parse.main(["--as-of", "2026-10-05"])
    docs = [json.loads(line) for line in (parse.DOK / "dokument.jsonl").read_text("utf-8").splitlines()]
    scb = [d for d in docs if d["source"] == "scb"]
    assert scb and all(d["subject_area"] and d["statistics_area"] for d in scb)
    assert all(d["subject_area"] is None for d in docs if d["source"] != "scb")
    siris = [json.loads(line) for line in (parse.DOK / "siris_dokument.jsonl").read_text("utf-8").splitlines() if line]
    assert all(row["doc_type"] in parse.SIRIS_DOC_TYPES for row in siris)
    names = {path.name for path in parse.DOK.iterdir()}
    assert {"dokument.csv", "produkter.csv", "siris_dokument.jsonl"} <= names
    assert not names & {"scb_dokument.jsonl", "scb_dokument.csv", "siris_dokument.csv", "sam_dokument.csv"}


def test_missing_scb_capture_reuses_previous_scb_documents(harvest, monkeypatch):
    parse.DOK.mkdir()
    previous = {
        "source": "scb", "agency": None, "product_code": "AM0101", "product_name": None,
        "product_match": "code", "doc_type": "kvalitetsdeklaration", "year": "2025",
        "title": "Product – KD 2025", "url": "https://www.scb.se/am0101_kd_2025.pdf",
        "filetype": "pdf", "source_url": "https://www.scb.se/index", "heading": "Kvalitet",
        "subject_area": "Arbetsmarknad", "statistics_area": "Sysselsättning",
        "document_id": "old", "candidate_product_code": None, "review_required": False,
        "source_status": "legacy", "provenance": {},
    }
    (parse.DOK / "dokument.jsonl").write_text(json.dumps(previous, ensure_ascii=False) + "\n", "utf-8")
    monkeypatch.setattr(parse, "parse_siris", list)
    monkeypatch.setattr(parse, "parse_sam", list)
    parse.main(["--as-of", "2026-10-05"])
    (doc,) = [json.loads(line) for line in (parse.DOK / "dokument.jsonl").read_text("utf-8").splitlines()]
    assert doc["url"] == previous["url"]
    assert (doc["subject_area"], doc["statistics_area"]) == ("Arbetsmarknad", "Sysselsättning")
    assert doc["product_code"] == "AM0101"
