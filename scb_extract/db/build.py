"""Build a local, normalized SQLite database from the committed files in data/.

The database is rebuilt from scratch on every run into <out>.tmp and moved into
place only after it is complete, so a failed build never leaves a partial file.
"""

import os
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from scb_extract.db.common import Documents, Status
from scb_extract.db.links import resolve
from scb_extract.db.load_calendar import load_calendar
from scb_extract.db.load_dokumentation import load_dokumentation
from scb_extract.db.load_extractions import load_extractions

SCHEMA_VERSION = "1"
SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

INDEXED = [
    ("calendar_entry", "product_code, publish_date"),
    ("calendar_entry", "publish_date"),
    ("calendar_entry", "responsible_agency_id"),
    ("calendar_entry_publisher", "agency_id"),
    ("agency_alias", "alias_norm"),
    ("document", "url_sha"),
    ("document", "doc_type"),
    ("document_product_claim", "product_code"),
    ("product_subject_claim", "product_code"),
    ("product_subject_claim", "subject_code"),
    ("product", "agency_id"),
    ("harvest_document", "product_code"),
    ("harvest_document", "canonical_url"),
    ("harvest_document", "agency_id"),
    ("siris_dokument", "canonical_url"),
    ("sam_dokument", "canonical_url"),
    ("product_page", "product_code"),
    ("product_page", "canonical_url"),
    ("product_page", "agency_id"),
    ("product_page_section", "page_key"),
    ("product_page_link", "url"),
    ("subject_product_card", "product_code"),
    ("subject_product_card", "subject_code"),
    ("official_product_row", "product_code"),
    ("official_product_row", "agency_id"),
    ("registry_agency", "agency_id"),
    ("registry_agency", "org_nr"),
    ("hvd_link", "product_code"),
    ("change_notice", "product_code"),
    ("change_notice", "agency_id"),
    ("change_notice_related_product", "product_code"),
]

VIEWS = """
CREATE VIEW v_document_counts AS
SELECT
    dc.product_code,
    count(DISTINCT d.url) AS documents,
    count(DISTINCT CASE WHEN d.doc_type = 'kvalitetsdeklaration' THEN d.url END) AS kd_documents,
    max(CASE WHEN d.doc_type = 'kvalitetsdeklaration' THEN d.year END) AS kd_latest_year,
    count(DISTINCT CASE WHEN d.doc_type = 'beskrivning av statistiken' THEN d.url END) AS bas_documents,
    count(DISTINCT CASE WHEN d.doc_type = 'statistikens framställning' THEN d.url END) AS staf_documents,
    max(CASE WHEN d.doc_type = 'statistikens framställning' THEN d.year END) AS staf_latest_year,
    count(DISTINCT CASE WHEN d.doc_type = 'scbdok' THEN d.url END) AS scbdok_documents,
    count(DISTINCT CASE WHEN d.doc_type = 'metaplus' OR dc.origin = 'metaplus_query' THEN d.url END)
        AS metaplus_documents
FROM document_product_claim dc
JOIN document d USING (url)
WHERE dc.origin != 'harvest_candidate'
GROUP BY dc.product_code;

CREATE VIEW v_product_overview AS
SELECT
    p.product_code,
    p.name,
    a.canonical_name AS agency,
    p.in_workbook,
    p.publication_status,
    p.periodicity,
    p.subject_area,
    p.statistics_area,
    p.in_calendar,
    (SELECT max(c.publish_date) FROM calendar_entry c
      WHERE c.product_code = p.product_code AND c.publish_date <= date('now')) AS last_publish_date,
    (SELECT min(c.publish_date) FROM calendar_entry c
      WHERE c.product_code = p.product_code AND c.publish_date > date('now')) AS next_publish_date,
    pp.next_publication_date AS product_page_next_publication_date,
    p.has_product_page,
    coalesce(dc.documents, 0) AS documents,
    coalesce(dc.kd_documents, 0) AS kd_documents,
    dc.kd_latest_year,
    coalesce(dc.bas_documents, 0) AS bas_documents,
    coalesce(dc.staf_documents, 0) AS staf_documents,
    dc.staf_latest_year,
    coalesce(dc.scbdok_documents, 0) AS scbdok_documents,
    coalesce(dc.metaplus_documents, 0) AS metaplus_documents
FROM product p
LEFT JOIN agency a USING (agency_id)
LEFT JOIN product_page pp USING (product_code)
LEFT JOIN v_document_counts dc USING (product_code);

CREATE VIEW v_agency_products AS
SELECT
    a.agency_id,
    a.canonical_name AS agency,
    a.registry_org_nr,
    p.product_code,
    p.name,
    p.in_workbook,
    p.publication_status,
    (SELECT count(*) FROM calendar_entry c WHERE c.product_code = p.product_code) AS calendar_entries,
    coalesce(dc.documents, 0) AS documents
FROM agency a
JOIN product p USING (agency_id)
LEFT JOIN v_document_counts dc USING (product_code);

CREATE VIEW v_document_latest AS
SELECT product_code, doc_type, url, title, year, claim_origins FROM (
    SELECT
        dc.product_code, d.doc_type, d.url, d.title, d.year,
        group_concat(dc.origin) AS claim_origins,
        row_number() OVER (
            PARTITION BY dc.product_code, d.doc_type
            ORDER BY CAST(d.year AS INTEGER) DESC, d.url
        ) AS n
    FROM document_product_claim dc
    JOIN document d USING (url)
    WHERE dc.origin != 'harvest_candidate' AND d.doc_type IS NOT NULL
    GROUP BY dc.product_code, d.url
)
WHERE n = 1;

CREATE VIEW v_document_code_conflicts AS
SELECT
    dc.url,
    d.title,
    count(DISTINCT dc.product_code) AS distinct_codes,
    group_concat(DISTINCT dc.product_code) AS product_codes,
    group_concat(dc.product_code || ':' || dc.origin, '; ') AS claims
FROM document_product_claim dc
JOIN document d USING (url)
WHERE dc.origin != 'harvest_candidate'
GROUP BY dc.url
HAVING count(DISTINCT dc.product_code) > 1;
"""


def git_revision(directory: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=directory, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def create_indexes(con: sqlite3.Connection) -> None:
    for table, columns in INDEXED:
        name = f"ix_{table}_{columns.replace(', ', '_')}"
        con.execute(f"CREATE INDEX {name} ON {table} ({columns})")


def build(data_dir: Path, out: Path, sources_file: Path | None = None) -> dict:
    data_dir, out = Path(data_dir), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name(out.name + ".tmp")
    temporary.unlink(missing_ok=True)
    con = sqlite3.connect(temporary)
    try:
        con.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA foreign_keys=OFF;")
        con.executescript(SCHEMA)
        status = Status(con)
        documents = Documents(con)
        info: dict = {}
        info.update(load_calendar(con, data_dir, status))
        info.update(load_dokumentation(con, data_dir, status, documents))
        loader = load_extractions(con, data_dir, status, documents)
        warnings = (loader.manifest("products") or {}).get("warnings", [])
        issues = resolve(con, sources_file, warnings)
        create_indexes(con)
        con.executescript(VIEWS)
        info.update(
            schema_version=SCHEMA_VERSION,
            built_at=datetime.now(UTC).isoformat(timespec="seconds"),
            git_revision=git_revision(data_dir),
        )
        con.executemany(
            "INSERT INTO build_info VALUES (?, ?)",
            [(key, None if value is None else str(value)) for key, value in sorted(info.items())],
        )
        con.commit()
        con.execute("ANALYZE")
        con.commit()
        con.execute("VACUUM")
        tables = [row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        counts = {table: con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}
        con.close()
        os.replace(temporary, out)
    except BaseException:
        con.close()
        temporary.unlink(missing_ok=True)
        raise
    return {"counts": counts, "issues": dict(issues), "warnings": status.warnings}


def run(data_dir: Path, out: Path, sources_file: Path | None) -> int:
    report = build(data_dir, out, sources_file)
    print(f"Wrote {out}")
    for table, count in report["counts"].items():
        print(f"  {table}: {count}")
    print("Link issues (rows in link_issues by kind):")
    for kind, count in sorted(report["issues"].items()):
        print(f"  {kind}: {count}")
    for warning in report["warnings"]:
        print(f"Warning: {warning}")
    return 0
