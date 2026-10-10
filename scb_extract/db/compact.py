"""Build a compact SQLite database: four tables, agency slugs, JSON for nested facts.

The full database (`build-db`) is built into a temporary file first, so matching
and linking are identical; this module only projects it. Everything that hangs
off a product (calendar entries, documents, change notices, HVD links) is stored
as JSON on the product row. Agencies are referenced everywhere by a slug of their
name; there are no ids, aliases or URL keys. Left out as redundant or derivable:
canonical/requested URLs, provenance, publication status, the product page's own
next-publication date (the calendar has it), exact duplicate calendar rows, and
anything that only records how a link was made.
"""

import json
import os
import re
import sqlite3
import tempfile
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from scb_extract.db.build import build
from scb_extract.db.links import TRAILING_ABBREVIATION

SCHEMA = """
CREATE TABLE product (
    code TEXT PRIMARY KEY,           -- e.g. PR0101
    name TEXT,
    agency TEXT,                     -- agency slug
    subject TEXT,                    -- subject code, e.g. PR
    area TEXT,                       -- statistical area
    official INTEGER NOT NULL,       -- listed in SCB's official-product workbook
    periodicity TEXT,
    purpose TEXT,
    summary TEXT,                    -- product page introduction
    alias_of TEXT,                   -- code whose page this code redirects to
    other_names JSON CHECK (other_names IS NULL OR json_valid(other_names)),
    publications JSON CHECK (publications IS NULL OR json_valid(publications)),
    documents JSON CHECK (documents IS NULL OR json_valid(documents)),
    changes JSON CHECK (changes IS NULL OR json_valid(changes)),
    hvd JSON CHECK (hvd IS NULL OR json_valid(hvd))
);

CREATE TABLE agency (
    slug TEXT PRIMARY KEY,           -- lowercase name, å/ä -> a, ö -> o, other runs -> _
    name TEXT NOT NULL,
    org_nr TEXT,
    website TEXT,
    statistics_url TEXT,
    official INTEGER NOT NULL,       -- listed as statistikansvarig myndighet
    european INTEGER NOT NULL,       -- listed as responsible for European statistics
    subjects JSON CHECK (subjects IS NULL OR json_valid(subjects)),
    documents JSON CHECK (documents IS NULL OR json_valid(documents))
);

CREATE TABLE subject (
    code TEXT PRIMARY KEY,
    name TEXT,
    areas JSON CHECK (areas IS NULL OR json_valid(areas))
);

CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value JSON CHECK (json_valid(value))
);
"""

TRANSLITERATION = str.maketrans({"å": "a", "ä": "a", "ö": "o", "é": "e", "è": "e", "ü": "u"})


def slug(name: str) -> str:
    """'Statistiska centralbyrån (SCB)' -> 'statistiska_centralbyran'."""
    text = TRAILING_ABBREVIATION.sub("", unicodedata.normalize("NFC", name))
    text = text.casefold().translate(TRANSLITERATION)
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def dumps(value):
    """JSON text, or NULL for an empty value; nulls are dropped from objects."""
    if not value:
        return None
    if isinstance(value, list):
        value = [{k: v for k, v in item.items() if v not in (None, [], "")} if isinstance(item, dict) else item
                 for item in value]
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def agency_slugs(rich: sqlite3.Connection) -> dict[int, str]:
    slugs: dict[int, str] = {}
    taken: set[str] = set()
    for agency_id, name in rich.execute("SELECT agency_id, canonical_name FROM agency ORDER BY agency_id"):
        base = value = slug(name)
        n = 2
        while value in taken:  # never merge two distinct agencies under one slug
            value, n = f"{base}_{n}", n + 1
        taken.add(value)
        slugs[agency_id] = value
    return slugs


def grouped(rows, key_index=0):
    out = defaultdict(list)
    for row in rows:
        out[row[key_index]].append(row)
    return out


def products(rich: sqlite3.Connection, slugs: dict[int, str]) -> list[tuple]:
    first_workbook = {
        row[0]: row for row in rich.execute(
            "SELECT product_code, purpose, periodicity, statistics_area FROM official_product_row ORDER BY ordinal DESC"
        )
    }
    pages = dict(rich.execute(
        "SELECT product_code, summary FROM product_page WHERE product_code IS NOT NULL"
    ))
    subjects: dict[str, tuple] = {}
    for code, subject, area in rich.execute(
        "SELECT product_code, subject_code, statistical_area_label FROM product_subject_claim "
        "WHERE subject_code IS NOT NULL ORDER BY CASE origin WHEN 'workbook' THEN 0 "
        "WHEN 'subject_page' THEN 1 ELSE 2 END DESC"
    ):
        subjects[code] = (subject, area.strip() if area else None)
    alias_of = dict(rich.execute("SELECT product_code, alias_of FROM product_alias"))

    names = grouped(rich.execute(
        "SELECT DISTINCT product_code, trim(name) FROM product_name_alias ORDER BY 1, 2"
    ))
    forms = grouped(rich.execute("SELECT entry_id, form FROM calendar_entry_form ORDER BY entry_id, ordinal"))
    publishers = grouped(rich.execute(
        "SELECT entry_id, agency_id, name FROM calendar_entry_publisher ORDER BY entry_id, ordinal"
    ))
    calendar = grouped(rich.execute(
        "SELECT product_code, entry_id, publish_date, reference_period, reporting_round, responsible_agency_id "
        "FROM calendar_entry WHERE product_code IS NOT NULL AND occurrence = 1 ORDER BY publish_date, entry_id"
    ))
    documents = grouped(rich.execute(
        "SELECT c.product_code, d.url, d.doc_type, d.year, min(c.origin = 'harvest_candidate') "
        "FROM document_product_claim c JOIN document d USING (url) "
        "GROUP BY c.product_code, d.url "
        "ORDER BY c.product_code, d.doc_type, CAST(d.year AS INTEGER) DESC, d.url"
    ))
    kinds = grouped(rich.execute("SELECT notice_id, kind FROM change_notice_kind ORDER BY 1, 2"))
    related = grouped(rich.execute(
        "SELECT notice_id, product_code FROM change_notice_related_product ORDER BY 1, 2"
    ))
    changes = grouped(rich.execute(
        "SELECT n.product_code, n.notice_id, n.description, n.effective_date, n.effective_date_text, "
        "n.timing_claim, r.document_date_text FROM change_notice n JOIN changes_report r ON r.url_sha = n.report_sha "
        "WHERE n.product_code IS NOT NULL ORDER BY n.product_code, r.document_date_text, n.row_ordinal"
    ))
    hvd = grouped(rich.execute(
        "SELECT l.product_code, g.dataset_label, l.anchor_text, l.url FROM hvd_link l JOIN hvd_group g USING (group_id) "
        "WHERE l.product_code IS NOT NULL ORDER BY l.product_code, l.group_id, l.ordinal"
    ))

    rows = []
    for code, name, agency_id, official, product_periodicity in rich.execute(
        "SELECT product_code, name, agency_id, in_workbook, periodicity FROM product ORDER BY product_code"
    ):
        agency = slugs.get(agency_id)
        workbook = first_workbook.get(code)
        subject, area = subjects.get(code, (None, None))
        publications = []
        for _, entry_id, date, period, round_, responsible in calendar.get(code, []):
            by = [slugs.get(p[1]) or p[2] for p in publishers.get(entry_id, [])]
            publications.append({
                "date": date,
                "period": period,
                "round": round_,
                "forms": [f[1] for f in forms.get(entry_id, [])],
                # Only when the calendar names publishers other than the product's agency.
                "by": by if set(by) != {slugs.get(responsible) or agency} else None,
            })
        docs = [
            {"type": doc_type, "year": year, "url": url, "candidate": True if candidate else None}
            for _, url, doc_type, year, candidate in documents.get(code, [])
        ]
        notices = [
            {
                "report": report, "kinds": [k[1] for k in kinds.get(notice_id, [])],
                "effective": effective or effective_text, "timing": timing if timing != "unclear" else None,
                "text": text, "related": [r[1] for r in related.get(notice_id, [])],
            }
            for _, notice_id, text, effective, effective_text, timing, report in changes.get(code, [])
        ]
        rows.append((
            code, name, agency, subject, area or (workbook[3] if workbook else None), int(official),
            product_periodicity, workbook[1] if workbook else None, pages.get(code), alias_of.get(code),
            dumps([n[1] for n in names.get(code, []) if n[1] and n[1] != name]),
            dumps(publications), dumps(docs), dumps(notices),
            dumps([{"dataset": h[1], "table": h[2], "url": h[3]} for h in hvd.get(code, [])]),
        ))
    return rows


def agencies(rich: sqlite3.Connection, slugs: dict[int, str]) -> list[tuple]:
    websites = dict(rich.execute(
        "SELECT registry_row_id, value FROM registry_agency_field "
        "WHERE lower(label) = 'webbadress' AND value != '' ORDER BY ordinal DESC"
    ))
    official = {
        row[0]: row for row in rich.execute(
            "SELECT agency_id, ordinal, statistics_url FROM official_agency WHERE agency_id IS NOT NULL"
        )
    }
    subject_rows = grouped(rich.execute(
        "SELECT agency_ordinal, trim(subject_name, ' :'), statistical_area FROM official_agency_subject "
        "ORDER BY agency_ordinal, subject_ordinal, area_ordinal"
    ))
    european = {row[0] for row in rich.execute("SELECT agency_id FROM european_agency WHERE agency_id IS NOT NULL")}
    unlinked = grouped(rich.execute(
        "SELECT DISTINCT agency_id, canonical_url, doc_type, year, title FROM harvest_document "
        "WHERE agency_id IS NOT NULL AND product_code IS NULL AND candidate_product_code IS NULL "
        "ORDER BY agency_id, doc_type, CAST(year AS INTEGER) DESC, canonical_url"
    ))
    rows = []
    for agency_id, name, registry_row, org_nr in rich.execute(
        "SELECT agency_id, canonical_name, registry_row_id, registry_org_nr FROM agency ORDER BY agency_id"
    ):
        listed = official.get(agency_id)
        subjects: dict[str, list[str]] = {}
        for _, subject, area in subject_rows.get(listed[1], []) if listed else []:
            subjects.setdefault(subject, [])
            if area:
                subjects[subject].append(area)
        rows.append((
            slugs[agency_id], name, org_nr, websites.get(registry_row), listed[2] if listed else None,
            int(listed is not None), int(agency_id in european), dumps(subjects),
            dumps([
                {"type": doc_type, "year": year, "url": url, "title": title}
                for _, url, doc_type, year, title in unlinked.get(agency_id, [])
            ]),
        ))
    return sorted(rows)


def subjects(rich: sqlite3.Connection) -> list[tuple]:
    areas = grouped(rich.execute("SELECT subject_code, label FROM statistical_area ORDER BY subject_code, ordinal"))
    return [
        (code, title, dumps([a[1] for a in areas.get(code, [])]))
        for code, title in rich.execute("SELECT subject_code, title FROM subject ORDER BY subject_code")
    ]


def meta(rich: sqlite3.Connection) -> list[tuple]:
    info = dict(rich.execute("SELECT key, value FROM build_info"))
    incomplete = [
        f"{family}/{source}/{key}: {status}"
        for family, source, key, status in rich.execute(
            "SELECT family, source, key, status FROM source_status WHERE status NOT IN ('fresh') "
            "ORDER BY 1, 2, 3"
        )
    ]
    values = {
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_revision": info.get("git_revision"),
        "calendar_last_completed_run": info.get("calendar_last_completed_run"),
        "dokumentation_as_of": info.get("dokumentation_as_of"),
        "not_fresh": incomplete,
    }
    return [(key, json.dumps(value, ensure_ascii=False)) for key, value in sorted(values.items())]


def project(rich_path: Path, out: Path) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name(out.name + ".tmp")
    temporary.unlink(missing_ok=True)
    rich = sqlite3.connect(rich_path)
    con = sqlite3.connect(temporary)
    try:
        con.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;")
        con.executescript(SCHEMA)
        slugs = agency_slugs(rich)
        con.executemany("INSERT INTO product VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", products(rich, slugs))
        con.executemany("INSERT INTO agency VALUES (?,?,?,?,?,?,?,?,?)", agencies(rich, slugs))
        con.executemany("INSERT INTO subject VALUES (?,?,?)", subjects(rich))
        con.executemany("INSERT INTO meta VALUES (?,?)", meta(rich))
        con.execute("CREATE INDEX ix_product_agency ON product (agency)")
        con.execute("CREATE INDEX ix_product_subject ON product (subject)")
        con.commit()
        con.execute("VACUUM")
        counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in ("product", "agency", "subject", "meta")}
        con.close()
        rich.close()
        os.replace(temporary, out)
    except BaseException:
        con.close()
        rich.close()
        temporary.unlink(missing_ok=True)
        raise
    return counts


def build_compact(data_dir: Path, out: Path, sources_file: Path | None = None) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        rich = Path(directory) / "full.db"
        build(data_dir, rich, sources_file)
        return project(rich, Path(out))


def run(data_dir: Path, out: Path, sources_file: Path | None) -> int:
    counts = build_compact(data_dir, out, sources_file)
    print(f"Wrote {out} ({Path(out).stat().st_size / 1e6:.1f} MB)")
    for table, count in counts.items():
        print(f"  {table}: {count}")
    return 0
