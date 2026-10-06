# scb-publiceringskalender

The complete contents of SCB's [publiceringskalender](https://www.scb.se/hitta-statistik/publiceringskalendern/)
(publishing calendar for Sweden's official statistics), from the first entry to the last planned
publication, refreshed automatically every week.

The repository also contains deterministic extractors for SCB product pages,
subject/documentation indexes, agency responsibilities, the agency register,
official product workbook, legislation, HVD links, economy diagrams and change
reports. See [Metadata extracts](#metadata-extracts) below.

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

On GitHub: Actions → Update SCB data → Run workflow. Select `all` to include the calendar; tick `full` to refetch the whole
calendar instead of last year onwards (about 40 minutes instead of a few). If a full run
fails, what it fetched so far is committed and the next run continues from there.

Locally, from the repository root, with Python 3.9 or later and nothing to install:

```bash
python fetch.py          # or: python fetch.py --full
python parse.py
```

## Known holes

A few calendar entries cannot be displayed by SCB's own site: any result page that would contain
one comes back empty. The rows around such an entry are still collected, and the entry itself is
recorded in `data/gaps.json` as `from`/`to` (dates), `expected` (rows the calendar counts) and
`retrieved` (rows it would show).

## Metadata extracts

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/). The existing calendar-only
commands above remain standard-library-only. Install the metadata dependencies
and run all sources, or select one source:

```bash
uv sync --locked
uv run python -m scb_extract run --source all
uv run python -m scb_extract run --source official_products
```

Source IDs: `products`, `subjects`, `documentation`, `official_agencies`,
`european_agencies`, `regulation`, `agency_registry`, `official_products`, `hvd`,
`economy`, `changes`. No LLM service or credential is used. Regina is deferred.

Each source writes to `data/extractions/<source>/`. Products use product codes;
subjects use subject codes; documentation pages, diagrams and PDFs use a stable
SHA-256 URL identifier. Documentation product codes remain metadata, so a failed
refresh cannot change the page's filename. Collection files are `index.json`.
Swedish source text, source ordering and link contexts are retained. The workbook
uses original column names, including `Anvandare`.

Every source has a `manifest.json`. Check `complete` and each record's `status`:
`fresh`, `retained` after a failed refresh, `failed` without a usable current
document, or `no_longer_listed` when discovery no longer lists an older document.
Previously saved documents are retained; their presence alone does not mean they
were successfully refreshed. The overall `run.json` describes the selected sources
for that invocation. Exit code 1 means at least one source was incomplete; successful
sibling outputs are still published. An absent section is distinguished from a
failed request.

Product discovery includes all distinct codes in the calendar, including old
codes whose current URLs may return 404. These failures remain visible. Membership
in a subject or calendar is not proof of current official-product status: join
against the current workbook using exact `Produktkod`, never fuzzy titles.

The regulation retains source text and future-effective versions without deciding
which provisions apply today. PDF notices retain page text and evidence; proposals
and uncertain dates are flagged. The reports themselves omit some changes, so
they are not a complete historical change ledger. Linked HVD observations,
documentation PDFs and economy Excel assets are recorded as links, not downloaded.

### Raw archives and replay

Live runs archive responses to a unique directory under `.extract-raw/`, or an
explicit new `--raw-dir`. The bundle includes `run.json`, the calendar input,
request metadata, SHA-256-addressed response bytes, and final diagnostics. These
production raw files are ignored by Git. Reproduce the same extraction without
network access, using the same code revision and locked dependencies:

```bash
uv run python -m scb_extract replay --raw-dir .extract-raw/my-run --output-dir .extract-stage/replay
```

Replay can also select a source originally included in the bundle with `--source`.
Missing, unsuccessful or corrupt snapshots remain failures; replay never silently
fetches new data. Fetch timestamps belong to archived evidence and remain unchanged
in replay. A source-specific run does not rerun the calendar.

The weekly workflow retains Monday 03:17 UTC. Manual runs can select one source.
Raw response bundles are uploaded even on failure to the run's `scb-raw-*` GitHub
artifact and retained for 90 days. Download and unzip that artifact, then point
`--raw-dir` at its directory containing `run.json`. After artifact expiry, older
raw inputs cannot be recovered from Git; normalized JSON remains in Git history.
The workflow commits validated data and status files once, and remains failed if
any extraction step failed. Workflow changes take effect after integration into
the repository's scheduled branch.

### Tests and parser contracts

```bash
uv run pytest -q
uv run ruff check .
```

Default tests are network-free and include real source fixtures under
`tests/fixtures`, with request metadata and hashes. Fixtures cover hierarchy,
missing sections, duplicate titles, workbook columns, registry identifiers,
future law versions, cross-page PDF rows, failed refreshes and replay. Fixture
counts are capture-specific assertions, not hardcoded expectations for live data.
The fixture capture utility is a maintainer tool and does not run during tests.

Pydantic models under `scb_extract/models` define production contracts. Use
`model_json_schema()` to inspect a contract; handwritten duplicate schemas are
not maintained. Parsers consume `FetchSnapshot` and return models; adapters use
the shared `ExtractionContext` and return `SourceResult`. Research notes under
`docs/research` describe the original investigation and partial examples, not
current production verification.
