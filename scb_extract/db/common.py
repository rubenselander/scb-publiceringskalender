"""Small helpers shared by the SQLite loaders."""

import json
import re
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from scb_extract.core import canonical_url, url_key

PRODUCT_CODE = re.compile(r"^[A-Z]{2}[0-9]{4}$")
DOCUMENT_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".zip", ".txt", ".ppt", ".pptx",
)


def is_product_code(value: str | None) -> bool:
    return bool(value) and bool(PRODUCT_CODE.fullmatch(value))


def cell(value):
    """Convert a JSON value to something sqlite3 stores without loss."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def insert(
    con: sqlite3.Connection, table: str, columns: list[str], rows: Iterable[Mapping]
) -> int:
    """Insert mappings by column name; missing keys become NULL, extra keys are ignored."""
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' * len(columns))})"
    )
    values = [[cell(row.get(column)) for column in columns] for row in rows]
    con.executemany(sql, values)
    return len(values)


def read_jsonl(path: Path) -> Iterable[dict]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def canon(url: str | None) -> str | None:
    return canonical_url(url) if url else None


def is_document_url(url: str | None) -> bool:
    """A link to a documentation asset (file or MetaPlus), not to another web page."""
    if not url:
        return False
    parts = urlsplit(url)
    path = parts.path.lower()
    return (
        path.endswith(DOCUMENT_EXTENSIONS)
        or "/contentassets/" in path
        or parts.netloc.lower() == "metadata.scb.se"
    )


def metaplus_code(url: str | None) -> str | None:
    """MetaPlus links carry the product code in the query (?produkt=AM0101)."""
    if not url or urlsplit(url).netloc.lower() != "metadata.scb.se":
        return None
    values = parse_qs(urlsplit(url).query).get("produkt", [])
    code = values[0].strip().upper() if values else None
    return code if is_product_code(code) else None


class Documents:
    """Accumulates the document dimension and product-code claims.

    The first origin to see a URL supplies its descriptive columns; claims from
    every origin are kept so disagreements stay queryable.
    """

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def add(self, url: str | None, origin: str, **fields) -> str | None:
        key = canon(url)
        if not key:
            return None
        self.con.execute(
            "INSERT OR IGNORE INTO document (url, url_sha, title, doc_type, year, "
            "filetype, source_url, heading, first_origin) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                key,
                url_key(key),
                fields.get("title"),
                fields.get("doc_type"),
                fields.get("year"),
                fields.get("filetype"),
                fields.get("source_url"),
                fields.get("heading"),
                origin,
            ),
        )
        return key

    def claim(
        self, url: str | None, product_code: str | None, origin: str, review: bool = False
    ) -> None:
        key = canon(url)
        if key and product_code:
            self.con.execute(
                "INSERT OR IGNORE INTO document_product_claim VALUES (?,?,?,?)",
                (key, product_code, origin, int(review)),
            )


class Status:
    """Collects source_status rows; later rows for the same key replace earlier ones."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self.warnings: list[str] = []

    def add(
        self,
        family: str,
        source: str,
        key: str,
        status: str,
        error: str | None = None,
        complete: bool | None = None,
        discovery_complete: bool | None = None,
    ) -> None:
        self.con.execute(
            "INSERT OR REPLACE INTO source_status VALUES (?,?,?,?,?,?,?)",
            (family, source, key, status, error, cell(complete), cell(discovery_complete)),
        )
        if status in ("missing", "invalid"):
            self.warnings.append(f"{family}/{source}/{key}: {status} {error or ''}".strip())
