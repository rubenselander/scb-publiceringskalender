"""Resolve soft links between families: agencies, products, subjects.

Nothing here guesses by similarity. Agencies match on a normalized name
(case, whitespace, a trailing "(ABBR)" and the "Statistikansvarig myndighet:"
prefix ignored) or through the workbook's short names, which are mapped to the
calendar's long names via shared product codes. Whatever does not resolve is
left NULL and counted in link_issues.
"""

import json
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

PREFIX = re.compile(r"^\s*statistikansvarig\s+myndighet\s*:\s*", re.IGNORECASE)
TRAILING_ABBREVIATION = re.compile(r"\s*\(([^()]{1,15})\)\s*$")
PRODUCT_WARNING = re.compile(
    r"Requested calendar code ([A-Z]{2}[0-9]{4}); page short address identifies ([A-Z]{2}[0-9]{4})"
)
CODE_GLOB = "[A-Z][A-Z][0-9][0-9][0-9][0-9]"


def norm(name: str | None) -> str:
    if not name:
        return ""
    text = PREFIX.sub("", unicodedata.normalize("NFC", name))
    text = TRAILING_ABBREVIATION.sub("", text)
    return " ".join(text.casefold().split())


def abbreviation(name: str | None) -> str | None:
    match = TRAILING_ABBREVIATION.search(name or "")
    return match.group(1).strip() if match else None


class Agencies:
    """In-memory agency dimension written to the agency/agency_alias tables at the end."""

    def __init__(self):
        self.names: list[tuple[str, str]] = []  # index + 1 = agency_id
        self.by_norm: dict[str, set[int]] = defaultdict(set)
        self.aliases: set[tuple[str, str, int, str]] = set()

    def create(self, name: str, origin: str) -> int:
        self.names.append((name, origin))
        agency_id = len(self.names)
        self.alias(name, agency_id, origin)
        return agency_id

    def alias(self, name: str, agency_id: int, origin: str) -> None:
        key = norm(name)
        if not key:
            return
        self.by_norm[key].add(agency_id)
        self.aliases.add((name, key, agency_id, origin))
        short = abbreviation(name)
        if short:
            self.by_norm[norm(short)].add(agency_id)
            self.aliases.add((short, norm(short), agency_id, origin + ":abbreviation"))

    def resolve(self, name: str | None) -> tuple[int | None, str | None]:
        ids = self.by_norm.get(norm(name), set())
        if len(ids) == 1:
            return next(iter(ids)), None
        return None, ("ambiguous_agency" if ids else "unknown_agency")

    def resolve_or_create(self, name: str | None, origin: str) -> int | None:
        if not norm(name):
            return None
        agency_id, issue = self.resolve(name)
        if agency_id is not None:
            self.alias(name, agency_id, origin)
            return agency_id
        if issue == "ambiguous_agency":
            return None
        return self.create(name, origin)

    def write(self, con: sqlite3.Connection) -> None:
        con.executemany(
            "INSERT INTO agency (agency_id, canonical_name, origin) VALUES (?,?,?)",
            [(n, name, origin) for n, (name, origin) in enumerate(self.names, 1)],
        )
        con.executemany("INSERT OR IGNORE INTO agency_alias VALUES (?,?,?,?)", sorted(self.aliases))


def issue(con, table, column, raw, kind, rows, detail=None) -> None:
    con.execute(
        "INSERT INTO link_issues VALUES (?,?,?,?,?,?)", (table, column, raw, kind, rows, detail)
    )


def distinct(con, sql) -> list:
    return [row[0] for row in con.execute(sql)]


def seed_agencies(con: sqlite3.Connection, sources_file: Path | None) -> Agencies:
    agencies = Agencies()
    for name in distinct(con, "SELECT DISTINCT responsible_agency FROM calendar_entry WHERE responsible_agency IS NOT NULL ORDER BY 1"):
        agencies.resolve_or_create(name, "calendar")
    for name in distinct(con, "SELECT DISTINCT name FROM calendar_entry_publisher ORDER BY 1"):
        agencies.resolve_or_create(name, "calendar_publisher")

    # Workbook short names ("SCB", "UKÄ") -> calendar long names, by shared product codes.
    pairs = con.execute(
        "SELECT w.responsible_authority, c.responsible_agency, count(*) FROM official_product_row w "
        "JOIN (SELECT DISTINCT product_code, responsible_agency FROM calendar_entry) c USING (product_code) "
        "WHERE w.responsible_authority IS NOT NULL AND c.responsible_agency IS NOT NULL "
        "GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2"
    ).fetchall()
    mapped: set[str] = set()
    for short, long_name, _ in pairs:
        if short in mapped:
            continue
        mapped.add(short)
        target, _ = agencies.resolve(long_name)
        if target is not None and agencies.resolve(short)[0] in (None, target):
            agencies.alias(short, target, "workbook")

    # Named lists next: "Statistiska centralbyrån (SCB)" registers the abbreviation, so a
    # workbook short name the calendar could not map still finds its agency below.
    lists = [
        ("official_agencies", "SELECT agency_name FROM official_agency ORDER BY ordinal"),
        ("product_page", "SELECT DISTINCT responsible_agency_label FROM product_page WHERE responsible_agency_label IS NOT NULL ORDER BY 1"),
        ("european_agencies", "SELECT name FROM european_agency ORDER BY ordinal"),
    ]
    for origin, sql in lists:
        for name in distinct(con, sql):
            agencies.resolve_or_create(name, origin)
    if sources_file and sources_file.exists():
        configs = json.loads(sources_file.read_text(encoding="utf-8"))
        for config in configs:
            agency_id = agencies.resolve_or_create(config.get("agency"), "dokumentation_sources")
            if agency_id is not None and config.get("name"):
                agencies.aliases.add((config["name"], config["name"], agency_id, "dokumentation_sources:slug"))
    for name in distinct(con, "SELECT DISTINCT responsible_authority FROM official_product_row WHERE responsible_authority IS NOT NULL ORDER BY 1"):
        agencies.resolve_or_create(name, "workbook")
    return agencies


# (table, name column, id column) resolved against the agency dimension.
AGENCY_COLUMNS = [
    ("calendar_entry", "responsible_agency", "responsible_agency_id"),
    ("calendar_entry_publisher", "name", "agency_id"),
    ("official_product_row", "responsible_authority", "agency_id"),
    ("product_page", "responsible_agency_label", "agency_id"),
    ("official_agency", "agency_name", "agency_id"),
    ("european_agency", "name", "agency_id"),
    ("subject_product_card", "responsible_agency_text", "agency_id"),
    ("harvest_document", "agency", "agency_id"),
    ("sam_dokument", "agency", "agency_id"),
    ("change_notice", "authority", "agency_id"),
]


def resolve_agency_columns(con: sqlite3.Connection, agencies: Agencies) -> None:
    for table, name_column, id_column in AGENCY_COLUMNS:
        rows = con.execute(
            f"SELECT {name_column}, count(*) FROM {table} WHERE {name_column} IS NOT NULL "
            f"AND trim({name_column}) != '' GROUP BY 1 ORDER BY 1"
        ).fetchall()
        for name, count in rows:
            agency_id, problem = agencies.resolve(name)
            if agency_id is None:
                issue(con, table, name_column, name, problem, count)
                continue
            con.execute(
                f"UPDATE {table} SET {id_column} = ? WHERE {name_column} = ?", (agency_id, name)
            )


def link_registry(con: sqlite3.Connection, agencies: Agencies) -> None:
    """Exact normalized-name match of agencies to registry rows; never picks among several."""
    candidates: dict[int, list[tuple]] = defaultdict(list)
    for row in con.execute(
        "SELECT registry_row_id, group_option_value, org_nr, cfar_nr, lop_nr, name "
        "FROM registry_agency WHERE name IS NOT NULL ORDER BY registry_row_id"
    ):
        ids = agencies.by_norm.get(norm(row[5]), set())
        if len(ids) == 1:
            candidates[next(iter(ids))].append(row)
    for agency_id, (name, _) in enumerate(agencies.names, 1):
        rows = candidates.get(agency_id, [])
        if len(rows) == 1:
            row_id, group, org_nr, cfar_nr, lop_nr, _ = rows[0]
            con.execute(
                "UPDATE agency SET registry_row_id=?, registry_group=?, registry_org_nr=?, "
                "registry_cfar_nr=?, registry_lop_nr=? WHERE agency_id=?",
                (row_id, group, org_nr, cfar_nr, lop_nr, agency_id),
            )
            con.execute("UPDATE registry_agency SET agency_id=? WHERE registry_row_id=?", (agency_id, row_id))
        elif rows:
            issue(con, "agency", "registry_row_id", name, "ambiguous_agency", len(rows),
                  "several registry rows share this name")
        else:
            issue(con, "agency", "registry_row_id", name, "no_registry_match", 1)


def link_product_aliases(con: sqlite3.Connection, product_warnings: list[str]) -> None:
    for warning in product_warnings:
        match = PRODUCT_WARNING.search(warning)
        if match:
            con.execute(
                "INSERT OR IGNORE INTO product_alias VALUES (?,?,?)",
                (match.group(1), match.group(2), "products manifest warning"),
            )
    con.execute(
        "INSERT OR IGNORE INTO product_alias "
        "SELECT b.product_code, a.product_code, 'shared canonical_url' FROM product_page a "
        "JOIN product_page b ON a.canonical_url = b.canonical_url AND a.product_code < b.product_code "
        "WHERE NOT EXISTS (SELECT 1 FROM product_alias x WHERE "
        "(x.product_code = b.product_code AND x.alias_of = a.product_code) OR "
        "(x.product_code = a.product_code AND x.alias_of = b.product_code))"
    )


def link_subject_cards(con: sqlite3.Connection) -> None:
    """Subject cards carry no code; match their link to a product page's canonical URL."""
    pages: dict[str, list[str]] = defaultdict(list)
    for code, url in con.execute(
        "SELECT product_code, canonical_url FROM product_page "
        "WHERE canonical_url IS NOT NULL AND product_code IS NOT NULL ORDER BY 1"
    ):
        pages[url].append(code)
    aliases = set(distinct(con, "SELECT product_code FROM product_alias"))
    cards = con.execute(
        "SELECT card_id, link_canonical_url FROM subject_product_card WHERE product_code IS NULL"
    ).fetchall()
    for card_id, url in cards:
        codes = pages.get(url, [])
        if len(codes) > 1:
            codes = [code for code in codes if code not in aliases]
        if len(codes) == 1:
            con.execute(
                "UPDATE subject_product_card SET product_code=?, product_code_origin='url_match' WHERE card_id=?",
                (codes[0], card_id),
            )
        else:
            origin = "ambiguous" if codes else "unresolved"
            con.execute(
                "UPDATE subject_product_card SET product_code_origin=? WHERE card_id=?", (origin, card_id)
            )
    for origin, count in con.execute(
        "SELECT product_code_origin, count(*) FROM subject_product_card "
        "WHERE product_code_origin IN ('ambiguous', 'unresolved') GROUP BY 1"
    ):
        issue(con, "subject_product_card", "product_code", None, "unknown_product", count,
              f"link URL {origin} against product page canonical URLs")


def fill_subject_claims(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        INSERT INTO product_subject_claim
        SELECT DISTINCT w.product_code, s.subject_code, w.subject_area, w.statistics_area, 'workbook'
        FROM official_product_row w
        LEFT JOIN subject s ON lower(trim(s.title)) = lower(trim(w.subject_area));

        INSERT INTO product_subject_claim
        SELECT DISTINCT c.product_code, c.subject_code, s.title, a.label, 'subject_page'
        FROM subject_product_card c
        JOIN subject s USING (subject_code)
        LEFT JOIN statistical_area a USING (area_id)
        WHERE c.product_code IS NOT NULL;

        INSERT INTO product_subject_claim
        SELECT DISTINCT d.product_code, s.subject_code, d.subject_area, d.statistics_area, 'harvest'
        FROM harvest_document d
        LEFT JOIN subject s ON lower(trim(s.title)) = lower(trim(d.subject_area))
        WHERE d.product_code IS NOT NULL AND d.subject_area IS NOT NULL;
        """
    )


# Every column that holds a product code, for the product dimension and diagnostics.
PRODUCT_COLUMNS = [
    ("official_product_row", "product_code"),
    ("calendar_entry", "product_code"),
    ("product_page", "product_code"),
    ("subject_product_card", "product_code"),
    ("document_product_claim", "product_code"),
    ("product_subject_claim", "product_code"),
    ("dokumentation_product", "product_code"),
    ("harvest_document", "product_code"),
    ("harvest_document", "candidate_product_code"),
    ("sam_dokument", "product_code"),
    ("hvd_link", "product_code"),
    ("change_notice", "product_code"),
    ("change_notice_related_product", "product_code"),
    ("product_alias", "product_code"),
    ("product_alias", "alias_of"),
]


def build_products(con: sqlite3.Connection) -> None:
    union = " UNION ".join(
        f"SELECT {column} AS code FROM {table} WHERE {column} GLOB '{CODE_GLOB}'"
        for table, column in PRODUCT_COLUMNS
    )
    con.executescript(
        f"""
        CREATE TEMP TABLE all_codes AS {union};

        INSERT INTO product
        SELECT
            a.code,
            COALESCE(w.product_name, pp.title, c.product_name, dp.product_name),
            COALESCE(w.agency_id, c.responsible_agency_id, pp.agency_id),
            w.product_code IS NOT NULL,
            c.product_code IS NOT NULL,
            pp.product_code IS NOT NULL,
            w.publication_status, w.periodicity, w.subject_area, w.statistics_area
        FROM all_codes a
        LEFT JOIN (SELECT *, row_number() OVER (PARTITION BY product_code ORDER BY ordinal) AS n
                   FROM official_product_row) w ON w.product_code = a.code AND w.n = 1
        LEFT JOIN (SELECT product_code, product_name, responsible_agency_id,
                          row_number() OVER (PARTITION BY product_code ORDER BY entry_id DESC) AS n
                   FROM calendar_entry) c ON c.product_code = a.code AND c.n = 1
        LEFT JOIN product_page pp ON pp.product_code = a.code
        LEFT JOIN dokumentation_product dp ON dp.product_code = a.code
        ORDER BY a.code;

        DROP TABLE all_codes;

        INSERT OR IGNORE INTO product_name_alias
            SELECT DISTINCT product_code, product_name, 'calendar' FROM calendar_entry
            WHERE product_code IS NOT NULL AND product_name IS NOT NULL;
        INSERT OR IGNORE INTO product_name_alias
            SELECT product_code, title, 'product_page' FROM product_page
            WHERE product_code IS NOT NULL AND title IS NOT NULL;
        INSERT OR IGNORE INTO product_name_alias
            SELECT product_code, product_name, 'dokumentation' FROM dokumentation_product
            WHERE product_name IS NOT NULL;
        INSERT OR IGNORE INTO product_name_alias
            SELECT DISTINCT product_code, product_name, 'change_notice' FROM change_notice
            WHERE product_code IS NOT NULL AND product_name IS NOT NULL;
        """
    )


def product_issues(con: sqlite3.Connection) -> None:
    known = (
        "SELECT product_code FROM official_product_row WHERE product_code IS NOT NULL "
        "UNION SELECT product_code FROM calendar_entry WHERE product_code IS NOT NULL "
        "UNION SELECT product_code FROM product_page WHERE product_code IS NOT NULL"
    )
    for table, column in PRODUCT_COLUMNS:
        for raw, count in con.execute(
            f"SELECT {column}, count(*) FROM {table} WHERE {column} IS NOT NULL AND {column} != '' "
            f"AND {column} NOT GLOB '{CODE_GLOB}' GROUP BY 1 ORDER BY 1"
        ).fetchall():
            issue(con, table, column, raw, "malformed_code", count)
        if table in ("official_product_row", "calendar_entry", "product_page"):
            continue
        for raw, count in con.execute(
            f"SELECT {column}, count(*) FROM {table} WHERE {column} GLOB '{CODE_GLOB}' "
            f"AND {column} NOT IN ({known}) GROUP BY 1 ORDER BY 1"
        ).fetchall():
            issue(con, table, column, raw, "unknown_product", count,
                  "not in workbook, calendar or product pages")
    for raw, count in con.execute(
        "SELECT product_code, count(*) FROM hvd_link WHERE product_code IS NOT NULL GROUP BY 1 ORDER BY 1"
    ).fetchall():
        issue(con, "hvd_link", "product_code", raw, "inferred", count, "parsed from PxWeb START__ path")
    con.execute(
        "INSERT INTO link_issues "
        "SELECT 'document_product_claim', 'product_code', url, 'conflicting_claim', "
        "count(DISTINCT product_code), group_concat(DISTINCT product_code) "
        "FROM (SELECT url, product_code FROM document_product_claim "
        "      WHERE origin != 'harvest_candidate' ORDER BY url, product_code) "
        "GROUP BY url HAVING count(DISTINCT product_code) > 1 ORDER BY url"
    )


def resolve(con: sqlite3.Connection, sources_file: Path | None, product_warnings: list[str]) -> Counter:
    agencies = seed_agencies(con, sources_file)
    agencies.write(con)
    resolve_agency_columns(con, agencies)
    link_registry(con, agencies)
    link_product_aliases(con, product_warnings)
    link_subject_cards(con)
    fill_subject_claims(con)
    build_products(con)
    product_issues(con)
    return Counter(dict(con.execute("SELECT issue, count(*) FROM link_issues GROUP BY 1 ORDER BY 1")))
