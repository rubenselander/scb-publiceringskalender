"""Load the documentation harvest under data/dokumentation/."""

import sqlite3
from pathlib import Path

from scb_extract.db.common import (
    Documents,
    Status,
    canon,
    insert,
    read_json,
    read_jsonl,
)

HARVEST_COLUMNS = [
    "document_id", "source", "agency", "product_code", "product_name", "product_match",
    "doc_type", "year", "title", "url", "canonical_url", "filetype", "source_url",
    "heading", "subject_area", "statistics_area", "candidate_product_code", "review_required", "source_status", "provenance",
]
PRODUCT_COLUMNS = [
    "product_code", "product_name", "responsible_agency", "in_calendar",
    "last_publish_date", "active", "documents", "sources",
] + [
    f"{kind}_{field}"
    for kind in ("kd", "bas", "staf", "scbdok", "metaplus")
    for field in ("count", "latest_year", "latest_url")
]
RAW_TABLES = {
    "siris_dokument": [
        "verkform", "omrade", "lasar", "doc_id", "typ", "filetype", "sos", "tabell_nr",
        "huvudrubrik", "rubrik", "titel", "url", "canonical_url", "doc_type", "year",
        "fetched", "source_url", "provenance",
    ],
    "sam_dokument": [
        "name", "agency", "source_url", "source_title", "href", "canonical_url", "text",
        "product_code", "doc_type", "year", "filetype", "fetched", "provenance",
    ],
}
# product_match values that name where a confirmed product code came from.
MATCH_ORIGINS = {"code": "harvest_filename", "block": "harvest_block"}


def load_dokumentation(
    con: sqlite3.Connection, data_dir: Path, status: Status, documents: Documents
) -> dict:
    directory = data_dir / "dokumentation"
    info: dict = {}
    if not directory.exists():
        status.add("dokumentation", "dokumentation", "directory", "missing")
        return info

    path = directory / "dokument.jsonl"
    if path.exists():
        rows = []
        for row in read_jsonl(path):
            row["canonical_url"] = canon(row.get("url"))
            rows.append(row)
            documents.add(
                row.get("url"), "harvest", title=row.get("title"), doc_type=row.get("doc_type"),
                year=row.get("year"), filetype=row.get("filetype"),
                source_url=row.get("source_url"), heading=row.get("heading"),
            )
            match = row.get("product_match") or "unspecified"
            documents.claim(
                row.get("url"), row.get("product_code"),
                MATCH_ORIGINS.get(match, f"harvest_{match}"), bool(row.get("review_required")),
            )
            documents.claim(row.get("url"), row.get("candidate_product_code"), "harvest_candidate", True)
        insert(con, "harvest_document", HARVEST_COLUMNS, rows)
        status.add("dokumentation", "dokumentation", "dokument.jsonl", "fresh")
    else:
        status.add("dokumentation", "dokumentation", "dokument.jsonl", "missing")

    path = directory / "produkter.jsonl"
    if path.exists():
        insert(con, "dokumentation_product", PRODUCT_COLUMNS, read_jsonl(path))
        status.add("dokumentation", "dokumentation", "produkter.jsonl", "fresh")
    else:
        status.add("dokumentation", "dokumentation", "produkter.jsonl", "missing")

    for table, columns in RAW_TABLES.items():
        path = directory / f"{table}.jsonl"
        if not path.exists():
            status.add("dokumentation", table, f"{table}.jsonl", "missing")
            continue
        url_field = "href" if table == "sam_dokument" else "url"
        rows = ({**row, "canonical_url": canon(row.get(url_field))} for row in read_jsonl(path))
        insert(con, table, columns, rows)
        status.add("dokumentation", table, f"{table}.jsonl", "fresh")

    manifest = directory / "parse-manifest.json"
    if manifest.exists():
        values = read_json(manifest)
        info["dokumentation_as_of"] = values.get("as_of")
        info["dokumentation_fetch_complete"] = values.get("fetch_complete")
    state = directory / "state.json"
    failures = read_json(state).get("last_failures", []) if state.exists() else []
    for failure in failures:
        source, _, key = failure.partition("/")
        status.add("dokumentation", source, key or source, "failed", "listed in state.json last_failures")
    return info
