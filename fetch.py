"""Stage 1 of 2: copy SCB's publiceringskalender into data/raw/ exactly as the calendar serves it.

Standard library only. Run from the repository root:  python fetch.py   (then: python parse.py)

Nothing is interpreted here beyond what is needed to walk the calendar: each table row is stored
as its HTML, in data/raw/<year>.jsonl, and parse.py turns those rows into the published files.

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
    out, and the server then answers 200 with an EMPTY result. A slow empty answer is retried.
  * Some entries cannot be rendered by the server at all, and every page that would contain one
    comes back EMPTY (quickly), neighbours included. The total shown in the pagination of the
    other pages still counts them. So an empty answer never proves there are no rows: the number
    of rows in a range is established from a page that does render, every split is checked
    against it, and a range that comes up short is narrowed down to single days. There the
    neighbours are read through other sort orders and the form filter, which move or hide the
    broken entry. Rows that still cannot be read are listed in data/gaps.json.
  * Missing/invalid dates silently fall back to "the coming week". So every returned row must
    lie inside the requested range, otherwise the response is rejected.
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
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

URL = "https://www.scb.se/hitta-statistik/publiceringskalendern/UpdateKalenderResults"
USER_AGENT = "scb-publiceringskalender (+https://github.com/rubenselander/scb-publiceringskalender)"
DATA = Path("data")
RAW = DATA / "raw"

CONCURRENCY = 6
LEAF_ROWS = 60          # a range matching at most this many rows is paged through as is
SPLIT_TARGET_ROWS = 40  # otherwise it is split into parts expected to match about this many
SUSPECT_EMPTY_SECONDS = 8.0  # genuine empty answers take 2-4 s, backend-timeout ones 13-31 s
FORMS = (1, 2, 3, 4)    # values of the form filter: Databas, Publikation, Statistiknyhet, Tabell och diagram
# Upper bounds (years) tried when looking for the earliest entry. Each probe asks for everything
# up to that year, so after these the bound advances one year at a time.
EARLIEST_PROBE_YEARS = (1899, 1949, 1979)

Row = tuple[str, str]  # (publish date, HTML of the table row's cells)

_page_size = 0  # rows per page, learned from the first pagination seen


class SourceError(RuntimeError):
    """The calendar answered with something that cannot be trusted."""


# ---------------------------------------------------------------- one request

def read_fragment(html: str, d0: date, d1: date) -> tuple[list[Row], int | None]:
    """Rows of one result page and the total number of rows the query matches (None: no rows shown)."""
    global _page_size
    if "publiceringsKalenderTableWrapper" not in html:
        raise SourceError(f"unexpected response: {html[:200]!r}")
    body = re.search(r"<tbody\b[^>]*>(.*?)</tbody>", html, re.S)
    rows = []
    for cells in re.findall(r"<tr\b[^>]*>(.*?)</tr>", body.group(1), re.S) if body else ():
        day = d0  # a row without a recognisable date is kept under the first day asked for
        for found in re.findall(r"<td\b[^>]*>\s*(\d{4}-\d{2}-\d{2})\s*</td>", cells):
            try:
                day = date.fromisoformat(found)
            except ValueError:
                continue
            if not d0 <= day <= d1:
                raise SourceError(f"got a row dated {day}")
            break
        rows.append((day.isoformat(), " ".join(cells.split())))
    # The pagination lists row ranges ("1-20", "21-40", ..., "1420-1431"); the last ends at the total.
    pagination = re.sub(r"<[^>]*>", " ", html.partition('id="pagination"')[2])
    ranges = [(int(lo), int(hi)) for lo, hi in re.findall(r"(\d+)\s*-\s*(\d+)", pagination)]
    if ranges and ranges[0][0] == 1:
        _page_size = ranges[0][1]
    return rows, max((hi for _, hi in ranges), default=len(rows) or None)


def get_page(d0: date, d1: date, page: int = 0, newest_first: bool = False,
             form: int | None = None) -> tuple[list[Row], int | None]:
    params = {"period": "Custom", "dateFrom": d0.isoformat(), "dateTo": d1.isoformat(), "paging": page,
              "sortField": 2, "sortOrder": int(newest_first)}  # sortField 2 = publish date
    if form:
        params["form"] = form
    request = urllib.request.Request(f"{URL}?{urllib.parse.urlencode(params)}", headers={"User-Agent": USER_AGENT})
    problem = ""
    for attempt in range(6):
        if attempt:
            time.sleep(2 ** attempt)
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                html = response.read().decode("utf-8", "replace")
            rows, total = read_fragment(html, d0, d1)
        except (OSError, http.client.HTTPException, SourceError) as e:  # OSError: network trouble, HTTP errors
            problem = repr(e)
            continue
        if not rows and time.monotonic() - t0 > SUSPECT_EMPTY_SECONDS:
            problem = "slow empty answer (looks like a backend timeout)"
            continue
        return rows, total
    raise SourceError(f"{d0}..{d1} page {page}: {problem}")


def pages(total: int) -> int:
    return math.ceil(total / _page_size) if _page_size else 1


def count(d0: date, d1: date) -> int | None:
    """Number of rows dated d0..d1; None if no page says (no rows, or broken ones at both ends)."""
    return get_page(d0, d1)[1] or get_page(d0, d1, newest_first=True)[1]


# ---------------------------------------------------------------- one date range

def split(pool: ThreadPoolExecutor, a: date, b: date, total: int, n: int) -> list[tuple] | None:
    """Cut a..b (holding `total` rows) into n parts -> [(from, to, rows in it, its first page)].

    None if the rows cannot be attributed: more than one part shows nothing yet rows are missing.
    """
    days = (b - a).days + 1
    n = min(days, n)
    cuts = [a + timedelta(days=days * i // n) for i in range(n)] + [b + timedelta(days=1)]
    parts = [(lo, hi - timedelta(days=1)) for lo, hi in zip(cuts, cuts[1:])]
    firsts, totals = map(list, zip(*pool.map(lambda p: get_page(*p), parts)))
    if sum(t or 0 for t in totals) != total:  # a part shows nothing although it has rows
        blank = [i for i, t in enumerate(totals) if t is None]
        for i, (_, t) in zip(blank, pool.map(lambda i: get_page(*parts[i], newest_first=True), blank)):
            totals[i] = t
    blank = [i for i, t in enumerate(totals) if t is None]
    missing = total - sum(t or 0 for t in totals)
    if missing and len(blank) == 1:
        totals[blank[0]], missing = missing, 0
    if missing < 0 or missing and not blank:
        raise SourceError(f"{a}..{b}: parts add up to {total - missing} rows, whole range says {total}")
    if missing:
        return None
    return [(*p, t, first) for p, t, first in zip(parts, totals, firsts) if t]


def read_day(day: date) -> list[Row]:
    """Whatever can be read of one day, also when its plain listing shows nothing."""
    rows, total = get_page(day, day)
    if rows and total == len(rows):
        return rows
    # Other sort orders move a broken entry to another page, the form filter hides it if it lacks
    # that form. Each listing shows a part of the day's rows, so their union is taken.
    best: Counter[Row] = Counter()
    for variant in ({}, {"newest_first": True}, *({"form": f} for f in FORMS)):
        rows, total = get_page(day, day, **variant)
        if total is None:  # the first page may be the one that is broken
            total = get_page(day, day, 1, **variant)[1]
        for page in range(1, pages(total or 0)):
            rows = rows + get_page(day, day, page, **variant)[0]
        best |= Counter(rows)
    return list(best.elements())


def fetch_range(pool: ThreadPoolExecutor, a: date, b: date, total: int) -> tuple[list[Row], list[dict]]:
    """Every readable row dated a..b, where the calendar has `total`, and the gaps left."""
    out, gaps = [], []

    def day_by_day(a, b, total):
        days = [a + timedelta(days=i) for i in range((b - a).days + 1)]
        rows = [r for day_rows in pool.map(read_day, days) for r in day_rows]
        out.extend(rows)
        if len(rows) != total:
            gaps.append({"from": a.isoformat(), "to": b.isoformat(), "expected": total, "retrieved": len(rows)})

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
                pending += parts
            else:
                day_by_day(a, b, total)
        # Page through them. A leaf that comes up short holds a broken entry: halve it until the
        # entry is confined to a single day.
        jobs = [(a, b, page) for a, b, total, first in leaves
                for page in range(0 if first is None else 1, pages(total))]
        got = dict(zip(jobs, pool.map(lambda j: get_page(*j)[0], jobs)))
        for a, b, total, first in leaves:
            rows = (first or []) + [r for page in range(pages(total)) for r in got.get((a, b, page), [])]
            if len(rows) == total:
                out += rows
            elif a < b and (parts := split(pool, a, b, total, 2)):
                pending += parts
            else:
                day_by_day(a, b, total)
    return out, gaps


# ---------------------------------------------------------------- the whole calendar

def earliest(today: date) -> date:
    for year in (*EARLIEST_PROBE_YEARS, *range(EARLIEST_PROBE_YEARS[-1] + 1, today.year + 1)):
        rows, _ = get_page(date.min, date(year, 12, 31))
        if rows:
            return date.fromisoformat(rows[0][0])
    raise SourceError("found no entries at all")


def latest(today: date) -> date:
    rows, _ = get_page(today, date.max, newest_first=True)
    if rows:
        return date.fromisoformat(rows[0][0])
    # Nothing ahead, or that page is broken: see which coming years have rows.
    year = today.year
    while year < date.max.year and count(date(year + 1, 1, 1), date.max) is not None:
        year += 1
    return date(year, 12, 31) if year > today.year or count(today, date.max) is not None else today


def chunks(pool: ThreadPoolExecutor, start: date, end: date) -> list[tuple[date, date, int]]:
    """start..end cut at year ends -> [(from, to, rows in it)].

    A year that shows no rows is joined to the following ones until a page does render, so that
    its emptiness is confirmed by a total instead of taken on trust.
    """
    years = []
    while start <= end:
        years.append((start, min(date(start.year, 12, 31), end)))
        start = years[-1][1] + timedelta(days=1)
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


def store(a: date, b: date, rows: list[Row], gaps: list[dict]) -> None:
    """Replace everything dated a..b in data/raw/ and data/gaps.json."""
    inside = lambda day: a.isoformat() <= day <= b.isoformat()
    RAW.mkdir(parents=True, exist_ok=True)
    stored = {int(p.stem) for p in RAW.glob("*.jsonl")}
    for year in {y for y in stored if a.year <= y <= b.year} | {int(day[:4]) for day, _ in rows}:
        path = RAW / f"{year:04}.jsonl"
        kept = [json.loads(line) for line in path.read_text("utf-8").splitlines()] if path.exists() else []
        lines = sorted([(r["date"], r["html"]) for r in kept if not inside(r["date"])]
                       + [r for r in rows if int(r[0][:4]) == year])
        if lines:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.writelines(json.dumps({"date": day, "html": html}, ensure_ascii=False) + "\n" for day, html in lines)
        else:
            path.unlink()
    path = DATA / "gaps.json"
    kept = json.loads(path.read_text("utf-8")) if path.exists() else []
    gaps = sorted([g for g in kept if not inside(g["from"])] + gaps, key=lambda g: g["from"])
    path.write_text(json.dumps(gaps, indent=1) + "\n", "utf-8")


def main() -> None:
    path = DATA / "state.json"
    state = json.loads(path.read_text("utf-8")) if path.exists() else {}
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

    def save():
        DATA.mkdir(exist_ok=True)
        path.write_text(json.dumps(state, indent=1) + "\n", "utf-8")

    with ThreadPoolExecutor(CONCURRENCY) as pool:
        for a, b, total in chunks(pool, start, end):
            rows, gaps = fetch_range(pool, a, b, total)
            store(a, date.max if b == end else b, rows, gaps)  # nothing is published after the latest entry
            state["resume_from"] = (b + timedelta(days=1)).isoformat()
            save()
            missing = sum(g["expected"] - g["retrieved"] for g in gaps)
            print(f"  {a} .. {b}: {len(rows)} rows" + (f", {missing} unreadable" if missing else ""), flush=True)

    state.pop("resume_from", None)
    state["last_completed_run"] = state.pop("run_started")
    save()
    print("done")


if __name__ == "__main__":
    try:
        main()
    except SourceError as e:
        sys.exit(f"error: {e}")
