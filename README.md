# scb-publiceringskalender

The complete contents of SCB's [publiceringskalender](https://www.scb.se/hitta-statistik/publiceringskalendern/)
(publishing calendar for Sweden's official statistics), refreshed automatically once a month.

| File | |
|---|---|
| [`data/calendar.csv`](data/calendar.csv) | one row per calendar entry; `forms` joined with ` \| ` |
| [`data/calendar.jsonl`](data/calendar.jsonl) | same rows, one JSON object per line; `forms` is a list |
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

[`update.py`](update.py) (standard library only) is run by a
[scheduled GitHub Action](.github/workflows/update.yml) on the 2nd of every month. Each run
refetches everything dated from the day before the previous run up to the last planned
publication and replaces that date range in the files; rows for already published dates are
left as they are. Earlier versions of the files are in the git history.

If a run fails, what it fetched so far is committed and the next run continues from there.
