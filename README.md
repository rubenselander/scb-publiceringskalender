# scb-publiceringskalender

The complete contents of SCB's [publiceringskalender](https://www.scb.se/hitta-statistik/publiceringskalendern/)
(publishing calendar for Sweden's official statistics), refreshed automatically once a month.

| File | |
|---|---|
| [`data/calendar.csv`](data/calendar.csv) | one row per calendar entry; `forms` joined with ` \| ` |
| [`data/calendar.jsonl`](data/calendar.jsonl) | same rows, one JSON object per line; `forms` is a list |
| [`data/raw/`](data/raw) | one line per request made to the calendar: the request (`from`, `to`, `page`, `newest_first`, `form`, `url`, `fetched`), the `total` the calendar reported and `rows`, the table rows exactly as served; one file per year |
| [`data/unparsed.jsonl`](data/unparsed.jsonl) | rows that could not be turned into columns (normally empty) |
| [`data/gaps.json`](data/gaps.json) | date ranges where the calendar has more rows than it would show |
| [`data/state.json`](data/state.json) | when the data was last refreshed |

Raw URLs:
`https://raw.githubusercontent.com/rubenselander/scb-publiceringskalender/main/data/calendar.csv`
(or `.jsonl`).

## Columns

| Column | Example |
|---|---|
| `publish_date` | `2026-10-07` |
| `product_code` | `PR0101` (taken from `product_url`; empty if the link has no product code) |
| `product_name` | `Konsumentprisindex` |
| `reporting_round` | `Snabb-KPI september 2026` (often empty) |
| `reference_period` | `September 2026` |
| `forms` | `Databas`, `Publikation`, `Statistiknyhet`, `Tabell och diagram` |
| `published_at` | `Statistiska centralbyrån` |
| `responsible_agency` | `Statistiska centralbyrån` |
| `product_url` | `https://www.scb.se/PR0101` |

Rows are exactly what the calendar shows, including a few entries with placeholder dates such as
`1900-01-01`. The calendar has no entry ids.

## How it updates

A [scheduled GitHub Action](.github/workflows/update.yml) runs two scripts (standard library
only) on the 2nd of every month. [`fetch.py`](fetch.py) copies the calendar's rows into `data/raw/`
without interpreting them, and [`parse.py`](parse.py) rebuilds the CSV and JSONL from those, so
the output format can be changed without fetching anything again. Each run
refetches everything from 1 January of the previous year up to the last planned publication and
replaces those years in the files; older years are left as they are. Earlier versions of the
files are in the git history.

Running the workflow by hand with `full` ticked (or `python fetch.py --full`) refetches the whole
calendar. If such a run fails, what it fetched so far is committed and the next run continues
from there.

## Known holes

A few calendar entries cannot be displayed by SCB's own site: any result page that would contain
one comes back empty. The rows around such an entry are still collected, and the entry itself is
recorded in `data/gaps.json` as `from`/`to` (dates), `expected` (rows the calendar counts) and
`retrieved` (rows it would show).
