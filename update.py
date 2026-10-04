"""Keep data/calendar.jsonl and data/calendar.csv in sync with SCB's publiceringskalender.

Standard library only. Run from the repository root:  python update.py

First run: finds the earliest and latest entry in the calendar and fetches everything between.
Later runs: refetch from the day before the previous completed run started up to the latest entry
and replace that date range in the files; older rows are kept as they are. Progress is saved
after every calendar year, so a failed run continues where it stopped.

Source: the calendar page GETs an HTML fragment from
    https://www.scb.se/hitta-statistik/publiceringskalendern/UpdateKalenderResults
    ?period=Custom&dateFrom=YYYY-MM-DD&dateTo=YYYY-MM-DD&paging=<0-based page>
There is no JSON API and the page size cannot be changed. Server behaviour this is built around:
  * Response time grows with the number of rows the date range matches (~2 s + ~3 ms/row), for
    every page of that range. So ranges are split until they match only a few pages' worth.
  * A range matching too many rows (somewhere between ~4500 and ~12000) makes the backend time
    out, and the server then answers 200 with an EMPTY result. An empty answer is therefore only
    trusted if it came back quickly, and every split is checked: the parts' totals must add up
    to the whole range's total.
  * Missing/invalid dates silently fall back to "the coming week". So every returned row must
    lie inside the requested range, otherwise the response is rejected.
"""

from __future__ import annotations

import csv
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

URL = "https://www.scb.se/hitta-statistik/publiceringskalendern/UpdateKalenderResults"
USER_AGENT = "scb-publiceringskalender (+https://github.com/rubenselander/scb-publiceringskalender)"
DATA = Path("data")
FIELDS = ("publish_date", "product_code", "product_name", "reporting_round", "reference_period",
          "forms", "published_at", "responsible_agency", "product_url")

CONCURRENCY = 6
LEAF_ROWS = 60          # a range matching at most this many rows is paged through as is
SPLIT_TARGET_ROWS = 40  # otherwise it is split into parts expected to match about this many
SUSPECT_EMPTY_SECONDS = 8.0  # genuine empty answers take 2-4 s, backend-timeout ones 13-31 s
# Upper bounds (years) tried when looking for the earliest entry. Each probe asks for everything
# up to that year, so after these the bound advances one year at a time.
EARLIEST_PROBE_YEARS = (1899, 1949, 1979)


class SourceError(RuntimeError):
    """The calendar answered with something that cannot be trusted."""


# ---------------------------------------------------------------- parsing

class _Fragment(HTMLParser):
    """Collects the table cells and the pagination text of a result fragment."""

    def __init__(self):
        super().__init__()
        self.rows: list[list[dict]] = []
        self.pagination = ""
        self._in_tbody = self._in_pagination = False
        self._cell: dict | None = None
        self._target = "text"

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tbody":
            self._in_tbody = True
        elif tag == "div" and attrs.get("id") == "pagination":
            self._in_pagination = True
        elif self._in_tbody and tag == "tr":
            self.rows.append([])
        elif self._in_tbody and tag == "td":
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
        if tag == "tbody":
            self._in_tbody = False
        elif tag == "td" and self._cell is not None:
            self.rows[-1].append(self._cell)
            self._cell = None
        elif tag in ("a", "span"):
            self._target = "text"

    def handle_data(self, data):
        if self._cell is not None:
            self._cell[self._target] += data
            self._cell["lines"][-1] += data
        elif self._in_pagination:  # the pagination is the last element of the fragment
            self.pagination += data + " "


def _clean(s: str) -> str:
    return " ".join(s.split())


def parse(html: str, d0: date, d1: date) -> tuple[list[dict], int]:
    """Rows of one result page and the total number of rows the query matches."""
    if "publiceringsKalenderTableWrapper" not in html:
        raise SourceError(f"unexpected response for {d0}..{d1}: {html[:200]!r}")
    fragment = _Fragment()
    fragment.feed(html)
    rows = []
    for cells in fragment.rows:
        if len(cells) != 6:
            raise SourceError(f"expected 6 columns, got {len(cells)} in {d0}..{d1}")
        product, reference, forms, published, at, agency = cells
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
        try:
            inside = d0 <= date.fromisoformat(row["publish_date"]) <= d1
        except ValueError:
            raise SourceError(f"unparseable publish date in {row}") from None
        if not inside:
            raise SourceError(f"asked for {d0}..{d1} but got a row dated {row['publish_date']}")
        rows.append(row)
    # The pagination lists row ranges ("1-20", "21-40", ..., "1420-1431"); the last ends at the total.
    his = [int(hi) for _, hi in re.findall(r"(\d+)\s*-\s*(\d+)", fragment.pagination)]
    total = max(his, default=len(rows))
    if total and not rows:
        raise SourceError(f"{d0}..{d1}: pagination says {total} rows but the page has none")
    return rows, total


# ---------------------------------------------------------------- fetching

def get_page(d0: date, d1: date, page: int = 0, newest_first: bool = False) -> tuple[list[dict], int]:
    query = urllib.parse.urlencode({
        "period": "Custom", "dateFrom": d0.isoformat(), "dateTo": d1.isoformat(), "paging": page,
        "sortField": 2, "sortOrder": int(newest_first)})  # sortField 2 = publish date
    request = urllib.request.Request(f"{URL}?{query}", headers={"User-Agent": USER_AGENT})
    problem = ""
    for attempt in range(6):
        if attempt:
            time.sleep(2 ** attempt)
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                html = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            problem = repr(e)
            continue
        rows, total = parse(html, d0, d1)
        if page == 0 and not rows and time.monotonic() - t0 > SUSPECT_EMPTY_SECONDS:
            problem = "slow empty answer (looks like a backend timeout)"
            continue
        return rows, total
    raise SourceError(f"{d0}..{d1} page {page}: {problem}")


def fetch_range(pool: ThreadPoolExecutor, d0: date, d1: date) -> list[dict]:
    """Every row dated d0..d1."""
    # Split until each range matches few rows, checking at every split that nothing went missing.
    pending = [(d0, d1, *get_page(d0, d1))]
    leaves = []
    while pending:
        a, b, rows, total = pending.pop()
        days = (b - a).days + 1
        if total <= LEAF_ROWS or days == 1:
            leaves.append((a, b, rows, total))
            continue
        n = min(days, math.ceil(total / SPLIT_TARGET_ROWS))
        cuts = [a + timedelta(days=days * i // n) for i in range(n)] + [b + timedelta(days=1)]
        parts = [(lo, hi - timedelta(days=1)) for lo, hi in zip(cuts, cuts[1:])]
        answers = list(pool.map(lambda p: get_page(*p), parts))
        if sum(t for _, t in answers) != total:
            raise SourceError(f"{a}..{b}: parts add up to {sum(t for _, t in answers)} rows, whole range says {total}")
        pending += [(*p, *ans) for p, ans in zip(parts, answers)]

    # First pages are in hand; fetch the rest. The page size is whatever a full first page held.
    rest = [(a, b, page) for a, b, rows, total in leaves if len(rows) < total
            for page in range(1, math.ceil(total / len(rows)))]
    more: dict[tuple[date, date], list[dict]] = {}
    for (a, b, _), (rows, _) in zip(rest, pool.map(lambda j: get_page(*j), rest)):
        more.setdefault((a, b), []).extend(rows)
    out = []
    for a, b, rows, total in leaves:
        rows = rows + more.get((a, b), [])
        if len(rows) != total:
            raise SourceError(f"{a}..{b}: read {len(rows)} rows, calendar says {total}")
        out += rows
    return out


def earliest(today: date) -> date:
    for year in (*EARLIEST_PROBE_YEARS, *range(EARLIEST_PROBE_YEARS[-1] + 1, today.year + 1)):
        rows, _ = get_page(date.min, date(year, 12, 31))
        if rows:
            return date.fromisoformat(rows[0]["publish_date"])
    raise SourceError("found no entries at all")


def latest(today: date) -> date:
    rows, _ = get_page(today, date.max, newest_first=True)
    return date.fromisoformat(rows[0]["publish_date"]) if rows else today


# ---------------------------------------------------------------- files

def load() -> tuple[list[dict], dict]:
    jsonl, state = DATA / "calendar.jsonl", DATA / "state.json"
    rows = [json.loads(line) for line in jsonl.read_text("utf-8").splitlines()] if jsonl.exists() else []
    return rows, json.loads(state.read_text("utf-8")) if state.exists() else {}


def save(rows: list[dict], state: dict) -> None:
    DATA.mkdir(exist_ok=True)
    rows.sort(key=lambda r: json.dumps([r[f] for f in FIELDS], ensure_ascii=False))
    with open(DATA / "calendar.jsonl", "w", encoding="utf-8", newline="\n") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    with open(DATA / "calendar.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(FIELDS)
        w.writerows([" | ".join(r[k]) if k == "forms" else r[k] for k in FIELDS] for r in rows)
    (DATA / "state.json").write_text(json.dumps(state, indent=1) + "\n", "utf-8")


def year_chunks(d0: date, d1: date):
    while d0 <= d1:
        end = min(date(d0.year, 12, 31), d1)
        yield d0, end
        d0 = end + timedelta(days=1)


def main() -> None:
    rows, state = load()
    today = datetime.now(ZoneInfo("Europe/Stockholm")).date()
    if "resume_from" in state:  # the previous run did not finish
        start = date.fromisoformat(state["resume_from"])
    else:
        if "last_completed_run" in state:  # rows dated before the day preceding that run are final
            start = date.fromisoformat(state["last_completed_run"]) - timedelta(days=1)
        else:
            start = earliest(today)
        state["run_started"] = today.isoformat()
    end = latest(today)
    print(f"fetching {start} .. {end}", flush=True)

    with ThreadPoolExecutor(CONCURRENCY) as pool:
        for a, b in year_chunks(start, end):
            fresh = fetch_range(pool, a, b)
            upto = date.max if b == end else b  # nothing is published after the latest entry
            rows = [r for r in rows if not a.isoformat() <= r["publish_date"] <= upto.isoformat()] + fresh
            state["resume_from"] = (b + timedelta(days=1)).isoformat()
            save(rows, state)
            print(f"  {a} .. {b}: {len(fresh)} rows", flush=True)

    state.pop("resume_from", None)
    state["last_completed_run"] = state.pop("run_started")
    save(rows, state)
    print(f"done: {len(rows)} rows, {rows[0]['publish_date']} .. {rows[-1]['publish_date']}")


if __name__ == "__main__":
    try:
        main()
    except SourceError as e:
        sys.exit(f"error: {e}")
