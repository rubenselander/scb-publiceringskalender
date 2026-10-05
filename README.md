# scb-publiceringskalender

The complete contents of SCB's [publiceringskalender](https://www.scb.se/hitta-statistik/publiceringskalendern/)
(publishing calendar for Sweden's official statistics), from the first entry to the last planned
publication, refreshed automatically every week.

## Get the data

The whole calendar in one file, in three formats with the same rows:

| Format | URL |
|---|---|
| CSV | https://raw.githubusercontent.com/rubenselander/scb-publiceringskalender/main/data/calendar.csv |
| JSON (one array) | https://raw.githubusercontent.com/rubenselander/scb-publiceringskalender/main/data/calendar.json |
| JSON Lines (one object per line) | https://raw.githubusercontent.com/rubenselander/scb-publiceringskalender/main/data/calendar.jsonl |

The URLs always serve the latest version; no account or key is needed. All files are UTF-8.

```bash
curl -O https://raw.githubusercontent.com/rubenselander/scb-publiceringskalender/main/data/calendar.csv
```

```python
import pandas as pd
df = pd.read_csv("https://raw.githubusercontent.com/rubenselander/scb-publiceringskalender/main/data/calendar.csv")
```

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

`forms` is a list in the JSON files and joined with ` | ` in the CSV. Empty values are `null` in
the JSON files and empty in the CSV.

Rows are exactly what the calendar shows: a few entries have placeholder dates such as
`1900-01-01`, and an entry the calendar lists twice appears twice. The calendar has no entry ids.

## All files

| File | |
|---|---|
| [`data/calendar.csv`](data/calendar.csv), [`.json`](data/calendar.json), [`.jsonl`](data/calendar.jsonl) | the calendar, one row per entry |
| [`data/raw/`](data/raw) | one line per request made to the calendar: the request (`from`, `to`, `page`, `newest_first`, `form`, `url`, `fetched`), the `total` the calendar reported and `rows`, the table rows exactly as served; one file per year |
| [`data/unparsed.jsonl`](data/unparsed.jsonl) | rows that could not be turned into columns (normally empty) |
| [`data/gaps.json`](data/gaps.json) | date ranges where the calendar has more rows than it would show |
| [`data/state.json`](data/state.json) | `last_completed_run`: the date the data was last refreshed |

## How it updates

A [scheduled GitHub Action](.github/workflows/update.yml) runs two scripts (standard library
only) every Monday at 03:17 UTC. [`fetch.py`](fetch.py) copies the calendar's rows into
`data/raw/` without interpreting them, and [`parse.py`](parse.py) rebuilds the calendar files from
those, so the output format can be changed without fetching anything again. Each run refetches
everything from 1 January of the previous year up to the last planned publication and replaces
those years in the files; older years are left as they are. Earlier versions of the files are in
the git history.

### Change the schedule

Edit the `cron` line in [`.github/workflows/update.yml`](.github/workflows/update.yml). The five
fields are minute, hour, day of month, month and day of week, in UTC:

| Schedule | `cron` |
|---|---|
| every Monday 03:17 (current) | `"17 3 * * 1"` |
| every day 03:17 | `"17 3 * * *"` |
| the 2nd of every month 03:17 | `"17 3 2 * *"` |

GitHub switches off schedules in a repository that has had no activity for 60 days. Every run
commits, so that should not happen; if it does, switch the workflow on again under the Actions tab.

### Run it by hand

On GitHub: Actions → Update calendar data → Run workflow. Tick `full` to refetch the whole
calendar instead of last year onwards (about 40 minutes instead of a few). If a full run
fails, what it fetched so far is committed and the next run continues from there.

Locally, from the repository root, with Python 3.9 or later and nothing to install:

```bash
python fetch.py          # or: python fetch.py --full
python parse.py
```

## Dokumentation: kvalitetsdeklarationer and other documentation per product

Every product of Sweden's official statistics has a *kvalitetsdeklaration* (quality declaration),
most have a *statistikens framställning* (production documentation), older ones a *beskrivning av
statistiken* or an SCBDOK, and SCB's products a MetaPlus entry. No one lists them in one place:
SCB's index covers the products SCB produces, the other agencies publish theirs on their own
sites, each in its own way. This repository collects them all, all years, every week.

| Format | URL |
|---|---|
| per document, CSV | https://raw.githubusercontent.com/rubenselander/scb-publiceringskalender/main/data/dokumentation/dokument.csv |
| per document, JSON Lines | https://raw.githubusercontent.com/rubenselander/scb-publiceringskalender/main/data/dokumentation/dokument.jsonl |
| per product, CSV | https://raw.githubusercontent.com/rubenselander/scb-publiceringskalender/main/data/dokumentation/produkter.csv |
| per product, JSON Lines | https://raw.githubusercontent.com/rubenselander/scb-publiceringskalender/main/data/dokumentation/produkter.jsonl |

`dokument` has one row per document: `source` (`scb`, `siris` or `sam/<agency>`), `agency`,
`product_code`, `product_name`, `product_match` (how the product was established: `code` from the
file name or link text, `block` or `page` from the other files listed with it, `name` by matching
the title to the agency's product names, empty if it could not be), `doc_type`
(`kvalitetsdeklaration`, `beskrivning av statistiken`, `statistikens framställning`, `scbdok`,
`metaplus`, `kvalitetsrapport`), `year`, `title`, `url`, `filetype`, `source_url` (the page the
link was found on) and `heading`.

`produkter` has one row per product code in the calendar (and codes seen only in documents):
`product_name`, `responsible_agency`, `in_calendar`, `last_publish_date`, `active` (a publication
in the last year), `documents`, `sources`, and for each kind of document its count and the newest
one's year and URL (`kd_*`, `bas_*`, `staf_*`, `scbdok_*`, `metaplus_*`).

### Sources

| Source | What | Raw data |
|---|---|---|
| `scb` | SCB's [Kvalitet och framställning](https://www.scb.se/dokumentation/kvalitet-och-framtagning/) index, which the page loads one subject area at a time from `/DokumentationSammanstallning/UpdateAmnesomrade?amnesomrade=<id>`. Every document of every product SCB documents, including the ones SCB produces for other agencies. Parsed to `scb_dokument.*` | `data/dokumentation/raw/scb/<id>.json`, the HTML fragment per subject area |
| `siris` | Skolverket's "Sök statistik" form, backed by a JSON API on `siris.skolverket.se/siris/reports/sossok_api/` (`verksamhetsformer` → `omrade` → `lasar` → `dokument`). Every file Skolverket publishes per school form, area and year — tables, PMs, kvalitetsdeklarationer — with Skolverket's own official-statistics flag (`sos`). All of it is in `siris_dokument.*`; the documentation part goes into `dokument.*` | `data/dokumentation/raw/siris/<verkform>.jsonl`, one line per API response |
| `sam` | The other statistikansvariga myndigheter, crawled from the start pages in [`dokumentation_sources.json`](dokumentation_sources.json) following the links that file allows. Every link on every page is stored; which ones are documents is decided when parsing (`sam_dokument.*`) | `data/dokumentation/raw/sam/<agency>.jsonl`, one line per page with its links |

Agencies whose pages cannot be read by a plain HTTP client (bot checks, JavaScript-only pages) come
up short or empty; `data/dokumentation/state.json` lists the sources that failed in the last run.
Försäkringskassan's current kvalitetsdeklarationer are PDFs that no page links to and are not found.

### How it updates

[`update-dokumentation.yml`](.github/workflows/update-dokumentation.yml) runs
[`fetch_dokumentation.py`](fetch_dokumentation.py) and
[`parse_dokumentation.py`](parse_dokumentation.py) every Tuesday at 04:41 UTC, in the same way as
the calendar: fetch stores what the sources send, parse rebuilds the output files from that.
Locally:

```bash
python fetch_dokumentation.py            # or: python fetch_dokumentation.py scb siris
python fetch_dokumentation.py sam --only trafikanalys socialstyrelsen
python parse_dokumentation.py
```

## Known holes

A few calendar entries cannot be displayed by SCB's own site: any result page that would contain
one comes back empty. The rows around such an entry are still collected, and the entry itself is
recorded in `data/gaps.json` as `from`/`to` (dates), `expected` (rows the calendar counts) and
`retrieved` (rows it would show).
