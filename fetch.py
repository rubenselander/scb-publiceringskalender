"""Stage 1 of 2: copy the rows of SCB's publiceringskalender into data/raw/, unparsed.

Standard library only. Run from the repository root:  python fetch.py [--full]   (then: python parse.py)

For every response that makes up the calendar, the table rows are cut out of the page and stored
exactly as sent in data/raw/<year>.jsonl, together with the request that produced them. What is
in a row is not interpreted here; parse.py does that. This script reads a response only to find
its way: how many rows the page shows, the total in the pagination, and whether the rows' dates
fit the request.

First run, or with --full: finds the earliest and latest entry in the calendar and fetches
everything between. Later runs: refetch from 1 January of last year up to the latest entry and
replace the stored years from there on; older years are kept as they are. Progress is saved after
every calendar year, so a full run that failed continues where it stopped.

Source: the calendar page GETs an HTML fragment from
    https://www.scb.se/hitta-statistik/publiceringskalendern/UpdateKalenderResults
    ?period=Custom&dateFrom=...&dateTo=YYYY-MM-DD&paging=<0-based page>
There is no JSON API and the page size cannot be changed. Server behaviour this is built around:
  * Response time grows with the number of rows the date range matches (~2 s + ~3 ms/row), for
    every page of that range. So ranges are split until they match only a few pages' worth.
  * A range matching too many rows (somewhere between ~4500 and ~12000) makes the backend time
    out, and the server then answers 200 with an EMPTY result. A slow empty answer is retried.
  * Entries have a time of day, normally 09:30. dateTo=D reaches the 09:30 entries of D but not
    one later that day, while dateFrom takes a time. An entry later in the day is therefore
    missed by a range ending on its day. So the range for the days d0..d1 is asked for as
    (d0 - 1 day) 09:30:01 .. d1, and such ranges are expected to fit together without holes or
    overlap; the check at every split fails if they do not. Rows can carry the date d0 - 1.
  * Some entries cannot be rendered by the server at all, and every page that would contain one
    comes back EMPTY (quickly), neighbours included. The total shown in the pagination of the
    other pages still counts them. So an empty answer never proves there are no rows: the number
    of rows in a range is established from a page that does render, every split is checked
    against it, and a range that comes up short is narrowed down to single days. There the
    neighbours are read through other sort orders and the form filter, which move or hide the
    broken entry. Rows that still cannot be read are listed in data/gaps.json.
  * Missing/invalid dates silently fall back to "the coming week". So a response whose rows lie
    outside the requested range is rejected.
"""

from __future__ import annotations

import http.client
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

URL = "https://www.scb.se/hitta-statistik/publiceringskalendern/UpdateKalenderResults"
USER_AGENT = "scb-publiceringskalender (+https://github.com/rubenselander/scb-publiceringskalender)"
DATA = Path("data")
RAW = DATA / "raw"

CONCURRENCY = 6
LEAF_ROWS = 60          # a range matching at most this many rows is paged through as is
SPLIT_TARGET_ROWS = 40  # otherwise it is split into parts expected to match about this many
SUSPECT_EMPTY_SECONDS = 8.0  # genuine empty answers take 2-4 s, backend-timeout ones 13-31 s
DAY_STARTS = "T09:30:01"  # just after the time of day that dateTo reaches
FORMS = (1, 2, 3, 4)    # values of the form filter: Databas, Publikation, Statistiknyhet, Tabell och diagram
# Upper bounds (years) tried when looking for the earliest entry. Each probe asks for everything
# up to that year, so after these the bound advances one year at a time.
EARLIEST_PROBE_YEARS = (1899, 1949, 1979)
ONE_DAY = timedelta(days=1)

_page_size = 0  # rows per page, learned from the first pagination seen


class SourceError(RuntimeError):
    """The calendar answered with something that cannot be trusted."""


def log(message: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {message}", flush=True)


# ---------------------------------------------------------------- one request

def row_days(row: str) -> list[date]:
    """The dates that fill a whole cell of a table row; the publish date is the last one."""
    days = []
    for found in re.findall(r"<td\b[^>]*>\s*(\d{4}-\d{2}-\d{2})\s*</td>", row):
        try:
            days.append(date.fromisoformat(found))
        except ValueError:
            pass
    return days


def read_fragment(html: str, first: date, last: date) -> tuple[list[str], int | None]:
    """The table rows of one result page, as sent, and the total number of rows the query matches."""
    global _page_size
    if "publiceringsKalenderTableWrapper" not in html:
        raise SourceError(f"unexpected response: {html[:200]!r}")
    body = re.search(r"<tbody\b[^>]*>(.*?)</tbody>", html, re.S)
    rows = re.findall(r"<tr\b[^>]*>.*?</tr>", body.group(1), re.S) if body else []
    for row in rows:
        days = row_days(row)
        if days and not any(first <= day <= last for day in days):
            raise SourceError(f"got a row dated {days[-1]}")
    # The pagination lists row ranges ("1-20", "21-40", ..., "1420-1431"); the last ends at the total.
    pagination = re.sub(r"<[^>]*>", " ", html.partition('id="pagination"')[2])
    ranges = [(int(lo), int(hi)) for lo, hi in re.findall(r"(\d+)\s*-\s*(\d+)", pagination)]
    if ranges and ranges[0][0] == 1:
        _page_size = ranges[0][1]
    return rows, max((hi for _, hi in ranges), default=len(rows) or None)


def get_page(d0: date, d1: date, page: int = 0, newest_first: bool = False, form: int | None = None,
             expected: bool = False) -> dict:
    """One result page as it is stored: the request, its rows as sent and `total`, the number of
    rows the whole query matches (None if the page shows no rows). `expected`: the page is known
    to hold rows, so an empty answer is asked for again before it is believed."""
    first = d0 - ONE_DAY if d0 > date.min else d0
    params = {"period": "Custom", "dateFrom": first.isoformat() + DAY_STARTS if d0 > date.min else d0.isoformat(),
              "dateTo": d1.isoformat(), "paging": page,
              "sortField": 2, "sortOrder": int(newest_first)}  # sortField 2 = publish date
    if form:
        params["form"] = form
    url = f"{URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    what = f"GET {d0}..{d1} page {page}" + " newest first" * newest_first + (f" form {form}" if form else "")
    problem = ""
    for attempt in range(6):
        if attempt:
            log(f"{what}: {problem}; retry {attempt} in {2 ** attempt} s")
            time.sleep(2 ** attempt)
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                html = response.read().decode("utf-8", "replace")
            rows, total = read_fragment(html, first, d1)
        except (OSError, http.client.HTTPException, SourceError) as e:  # OSError: network trouble, HTTP errors
            problem = repr(e)
            continue
        if not rows and time.monotonic() - t0 > SUSPECT_EMPTY_SECONDS:
            problem = "slow empty answer (looks like a backend timeout)"
            continue
        if not rows and expected and attempt < 2:  # the server also has quick empty hiccups
            problem = "empty answer where rows are expected"
            continue
        log(f"{what}: {len(rows)} rows, total {total}, {time.monotonic() - t0:.1f} s")
        return {"from": d0.isoformat(), "to": d1.isoformat(), "page": page, "newest_first": newest_first,
                "form": form, "url": url, "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "total": total, "rows": rows}
    raise SourceError(f"{what}: {problem}")


def pages(total: int) -> int:
    return math.ceil(total / _page_size) if _page_size else 1


def count(d0: date, d1: date) -> int | None:
    """Number of rows in d0..d1; None if no page says (no rows, or broken ones at both ends)."""
    return get_page(d0, d1)["total"] or get_page(d0, d1, newest_first=True)["total"]


# ---------------------------------------------------------------- one date range

def split(pool: ThreadPoolExecutor, a: date, b: date, total: int, n: int) -> list[tuple] | None:
    """Cut a..b (holding `total` rows) into n parts -> [(from, to, rows in it, its first page)].

    None if the rows cannot be attributed: more than one part shows nothing yet rows are missing.
    """
    days = (b - a).days + 1
    n = min(days, n)
    cuts = [a + timedelta(days=days * i // n) for i in range(n)] + [b + ONE_DAY]
    parts = [(lo, hi - ONE_DAY) for lo, hi in zip(cuts, cuts[1:])]
    firsts = list(pool.map(lambda p: get_page(*p), parts))
    totals = [first["total"] for first in firsts]
    if sum(t or 0 for t in totals) != total:  # a part shows nothing although it has rows
        blank = [i for i, t in enumerate(totals) if t is None]
        for i, last in zip(blank, pool.map(lambda i: get_page(*parts[i], newest_first=True), blank)):
            totals[i] = last["total"]
    blank = [i for i, t in enumerate(totals) if t is None]
    missing = total - sum(t or 0 for t in totals)
    if missing and len(blank) == 1:
        totals[blank[0]], missing = missing, 0
    if missing < 0 or missing and not blank:
        raise SourceError(f"{a}..{b}: parts add up to {total - missing} rows, whole range says {total}")
    if missing:
        return None
    return [(*p, t, first) for p, t, first in zip(parts, totals, firsts) if t]


def read_day(day: date) -> tuple[list[dict], int]:
    """Whatever can be read of one day, also when its plain listing shows nothing -> (pages, rows)."""
    plain = get_page(day, day)
    if plain["rows"] and plain["total"] == len(plain["rows"]):
        return [plain], len(plain["rows"])
    # Other sort orders move a broken entry to another page, the form filter hides it if it lacks
    # that form. Each listing shows a part of the day's rows; parse.py takes their union.
    found, best = [], Counter()
    for variant in ({}, {"newest_first": True}, *({"form": f} for f in FORMS)):
        first = get_page(day, day, **variant) if variant else plain
        total = first["total"]
        if total is None:  # the first page may be the one that is broken
            total = get_page(day, day, 1, **variant)["total"]
        listing = [first] + [get_page(day, day, page, **variant) for page in range(1, pages(total or 0))]
        found += [p for p in listing if p["rows"]]
        # compared without the tr attributes, which differ with the row's position on a page
        best |= Counter(" ".join(re.sub(r"^<tr\b[^>]*>", "", row).split()) for p in listing for row in p["rows"])
    return found, sum(best.values())


def fetch_range(pool: ThreadPoolExecutor, a: date, b: date, total: int) -> tuple[list[dict], int, list[dict]]:
    """The pages holding every readable row of a..b, where the calendar has `total` -> (pages, rows, gaps)."""
    out, gaps, read = [], [], 0

    def day_by_day(a, b, total):
        nonlocal read
        days = [a + timedelta(days=i) for i in range((b - a).days + 1)]
        found = 0
        for day_pages, n in pool.map(read_day, days):
            out.extend(day_pages)
            found += n
        read += found
        if found != total:
            log(f"GAP {a}..{b}: calendar has {total} rows, {found} readable")
            gaps.append({"from": a.isoformat(), "to": b.isoformat(), "expected": total, "retrieved": found})

    pending = [(a, b, total, None)]
    while pending:
        # Split until each range matches few rows, checking at every split that nothing went missing.
        leaves = []
        while pending:
            a, b, total, first = pending.pop()
            if total <= LEAF_ROWS or a == b:
                leaves.append((a, b, total, first))
                continue
            parts = split(pool, a, b, total, math.ceil(total / SPLIT_TARGET_ROWS)) or split(pool, a, b, total, 2)
            if parts:
                log(f"split {a}..{b} ({total} rows) into {len(parts)} parts with rows")
                pending += parts
            else:
                log(f"{a}..{b}: cannot tell where its {total} rows are, reading it day by day")
                day_by_day(a, b, total)
        # Page through them. A leaf that comes up short holds a broken entry: halve it until the
        # entry is confined to a single day.
        jobs = [(a, b, page) for a, b, total, first in leaves
                for page in range(0 if first is None else 1, pages(total))]
        got = dict(zip(jobs, pool.map(lambda j: get_page(*j, expected=True), jobs)))
        for a, b, total, first in leaves:
            listing = [p for p in [first, *(got.get((a, b, page)) for page in range(pages(total)))] if p and p["rows"]]
            found = sum(len(p["rows"]) for p in listing)
            if found == total:
                out += listing
                read += found
            elif a < b and (parts := split(pool, a, b, total, 2)):
                log(f"{a}..{b}: read {found} of {total} rows, a page is blank; halving")
                pending += parts
            else:
                log(f"{a}..{b}: read {found} of {total} rows, a page is blank; reading it day by day")
                day_by_day(a, b, total)
    return out, read, gaps


# ---------------------------------------------------------------- the whole calendar

def earliest(today: date) -> date:
    for year in (*EARLIEST_PROBE_YEARS, *range(EARLIEST_PROBE_YEARS[-1] + 1, today.year + 1)):
        rows = get_page(date.min, date(year, 12, 31))["rows"]
        if rows and row_days(rows[0]):
            return row_days(rows[0])[-1]
    raise SourceError("found no entries at all")


def latest(today: date) -> date:
    """The last day to ask for: the day after the latest entry, so that an entry late on that day is reached."""
    rows = get_page(today, date.max, newest_first=True)["rows"]
    if rows and row_days(rows[0]):
        return row_days(rows[0])[-1] + ONE_DAY
    # Nothing ahead, or that page is broken: see which coming years have rows.
    year = today.year
    while year < date.max.year and count(date(year + 1, 1, 1), date.max) is not None:
        year += 1
    return date(year, 12, 31) if year > today.year or count(today, date.max) is not None else today + ONE_DAY


def chunks(pool: ThreadPoolExecutor, start: date, end: date) -> list[tuple[date, date, int]]:
    """start..end cut at year ends -> [(from, to, rows in it)].

    A year that shows no rows is joined to the following ones until a page does render, so that
    its emptiness is confirmed by a total instead of taken on trust.
    """
    years = []
    while start <= end:
        years.append((start, min(date(start.year, 12, 31), end)))
        start = years[-1][1] + ONE_DAY
    out, a = [], None
    for (lo, hi), total in zip(years, pool.map(lambda y: count(*y), years)):
        if total is None and hi < end:
            a = a or lo
            continue
        if a:
            lo, total = a, count(a, hi)
        out.append((lo, hi, total or 0))
        a = None
    return out


def store(a: date, b: date, fetched: list[dict], gaps: list[dict]) -> None:
    """Replace what is stored for the years of a..b in data/raw/ and data/gaps.json."""
    RAW.mkdir(parents=True, exist_ok=True)
    by_year: dict[int, list[dict]] = {}
    for p in fetched:
        by_year.setdefault(int(p["from"][:4]), []).append(p)
    for path in RAW.glob("*.jsonl"):
        if a.year <= int(path.stem) <= b.year and int(path.stem) not in by_year:
            path.unlink()
    for year, records in by_year.items():
        records.sort(key=lambda p: (p["from"], p["to"], p["newest_first"], p["form"] or 0, p["page"]))
        with open(RAW / f"{year:04}.jsonl", "w", encoding="utf-8", newline="\n") as f:
            f.writelines(json.dumps(p, ensure_ascii=False) + "\n" for p in records)
    path = DATA / "gaps.json"
    kept = [g for g in (json.loads(path.read_text("utf-8")) if path.exists() else [])
            if not a.year <= int(g["from"][:4]) <= b.year]
    path.write_text(json.dumps(sorted(kept + gaps, key=lambda g: g["from"]), indent=1) + "\n", "utf-8")


def main() -> None:
    path = DATA / "state.json"
    state = json.loads(path.read_text("utf-8")) if path.exists() else {}
    today = datetime.now(timezone.utc).date()
    if "--full" in sys.argv[1:] or not state:
        start = earliest(today)
    else:
        start = date(today.year - 1, 1, 1)  # what lies before last year is final
        if "resume_from" in state:  # a full run did not finish
            start = min(start, date.fromisoformat(state["resume_from"]))
    end = latest(today)
    log(f"fetching {start} .. {end}; counting the rows of each year")

    def save():
        DATA.mkdir(exist_ok=True)
        path.write_text(json.dumps(state, indent=1) + "\n", "utf-8")

    state["resume_from"] = start.isoformat()
    save()
    with ThreadPoolExecutor(CONCURRENCY) as pool:
        for a, b, total in chunks(pool, start, end):
            log(f"YEAR {a}..{b}: {total} rows expected")
            fetched, read, gaps = fetch_range(pool, a, b, total)
            store(a, date.max if b == end else b, fetched, gaps)  # nothing is published after the latest entry
            state["resume_from"] = (b + ONE_DAY).isoformat()
            save()
            log(f"SAVED {a}..{b}: {read} rows in {len(fetched)} pages"
                + (f", {total - read} unreadable" if total != read else ""))

    del state["resume_from"]
    state["last_completed_run"] = today.isoformat()
    save()
    log("done")


if __name__ == "__main__":
    try:
        main()
    except SourceError as e:
        sys.exit(f"error: {e}")
