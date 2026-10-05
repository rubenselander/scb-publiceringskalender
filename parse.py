"""Stage 2 of 2: turn the stored calendar rows in data/raw/ into data/calendar.jsonl and .csv.

Standard library only, no network. Run from the repository root:  python parse.py

Reads every row fetch.py stored and rewrites the output files from scratch, so a change to the
output format only needs this script to be run again. A row that does not look as expected is
not dropped: it goes to data/unparsed.jsonl with its HTML and the reason.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

DATA = Path("data")
FIELDS = ("publish_date", "product_code", "product_name", "reporting_round", "reference_period",
          "forms", "published_at", "responsible_agency", "product_url")


class _Cells(HTMLParser):
    """Collects the cells of one table row."""

    def __init__(self):
        super().__init__()
        self.cells: list[dict] = []
        self._cell: dict | None = None
        self._target = "text"

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "td":
            self._cell = {"text": "", "link": "", "round": "", "href": None, "lines": [""]}
            self._target = "text"
        elif self._cell is not None and tag == "a":
            self._cell["href"] = attrs.get("href")
            self._target = "link"
        elif self._cell is not None and tag == "span" and "reportingRoundName" in (attrs.get("class") or ""):
            self._target = "round"
        elif self._cell is not None and tag == "br":
            self._cell["lines"].append("")

    def handle_endtag(self, tag):
        if tag == "td" and self._cell is not None:
            self.cells.append(self._cell)
            self._cell = None
        elif tag in ("a", "span"):
            self._target = "text"

    def handle_data(self, data):
        if self._cell is not None:
            self._cell[self._target] += data
            self._cell["lines"][-1] += data


def _clean(s: str) -> str:
    return " ".join(s.split())


def parse(html: str) -> dict:
    """One calendar entry from the HTML of its table row; ValueError if it is not understood."""
    parser = _Cells()
    parser.feed(html)
    parser.close()
    if len(parser.cells) != 6:
        raise ValueError(f"expected 6 columns, got {len(parser.cells)}")
    product, reference, forms, published, at, agency = parser.cells
    href = product["href"]
    code = re.search(r"/([A-Za-z]{2}\d{4}[A-Za-z]?)/?$", href or "")
    row = {
        "publish_date": _clean(published["text"]),
        "product_code": code.group(1).upper() if code else None,
        "product_name": _clean(product["link"]) or _clean(product["text"]),
        "reporting_round": _clean(product["round"]) or None,
        "reference_period": _clean(reference["text"]),
        "forms": [f for f in map(_clean, forms["lines"]) if f],
        "published_at": _clean(at["text"]),
        "responsible_agency": _clean(agency["text"]),
        "product_url": href,
    }
    date.fromisoformat(row["publish_date"])  # ValueError if it is not a date
    return row


def stored_rows() -> list[str]:
    """Every stored table row once.

    A date range is normally stored as the pages of one listing. Where fetch.py had to work
    around a broken entry, a day is stored as several listings (other sort order, form filters)
    that each show part of its rows; a row is then taken as often as the listing showing it most.
    """
    listings: dict[tuple, dict[tuple, Counter]] = {}
    for path in sorted((DATA / "raw").glob("*.jsonl")):
        for line in path.read_text("utf-8").splitlines():
            page = json.loads(line)
            listing = listings.setdefault((page["from"], page["to"]), {}).setdefault(
                (page["newest_first"], page["form"]), Counter())
            # tr attributes and whitespace differ with the row's position on a page
            listing.update(" ".join(re.sub(r"^<tr\b[^>]*>", "<tr>", row).split()) for row in page["rows"])
    rows = []
    for variants in listings.values():
        union = Counter()
        for listing in variants.values():
            union |= listing
        rows += union.elements()
    return rows


def main() -> None:
    rows, unparsed = [], []
    for html in stored_rows():
        try:
            rows.append(parse(html))
        except Exception as e:  # whatever goes wrong with a row, it is kept
            unparsed.append({"html": html, "error": f"{type(e).__name__}: {e}"})
    rows.sort(key=lambda r: json.dumps([r[f] for f in FIELDS], ensure_ascii=False))
    with open(DATA / "calendar.jsonl", "w", encoding="utf-8", newline="\n") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    with open(DATA / "calendar.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(FIELDS)
        w.writerows([" | ".join(r[k]) if k == "forms" else r[k] for k in FIELDS] for r in rows)
    with open(DATA / "unparsed.jsonl", "w", encoding="utf-8", newline="\n") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in unparsed)
    print(f"{len(rows)} rows, {len(unparsed)} unparsed")


if __name__ == "__main__":
    main()
