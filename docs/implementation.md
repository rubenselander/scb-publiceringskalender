# SCB extraction implementation record

Approved scope: eleven deterministic source families; Python 3.13, uv, Pydantic,
HTTPX, Beautiful Soup/lxml, openpyxl, pdfplumber, pytest and Ruff. No LLM calls.
Regina deferred. Production raw bundles become 90-day GitHub artifacts; fixed
fixtures stay in Git. Existing calendar scripts and files remain unchanged.

## Frozen worker interfaces

Read `scb_extract/core.py` and production models under `scb_extract/models`.
Pure `parse_*` functions accept FetchSnapshot and return typed models without IO.
Use `snapshot.provenance(canonical=..., selector=...)` for standardized evidence.
Every source group exports `ADAPTERS: dict[str, SourceAdapter]`; each adapter has
`collect(context) -> SourceResult`. Use context.fetch for every request and
context.calendar_path for calendar discovery. No source-specific file writes.

Document keys are product/subject codes, `index`, or `url_key(url)` for other pages. Documentation always uses its discovered URL key, keeping its product code as metadata, so failed refreshes retain the same identity. Main
publishes to `data/extractions/<source>/<key>.json`. Record all discovered keys,
keyed failures and warnings. Set discovery_complete=False on incomplete index
retrieval. Incomplete document keys must have failures; last-good data is retained.
Ambiguous but fully reconstructed PDF prose is flagged for review; unresolved
row reconstruction is an incomplete report. Preserve evidence in either case.

Main owns shared models, core, CLI, fixtures, dependencies and workflows. Workers
own their source module (or same-name package) and source tests only. Request
shared changes centrally. No nested delegation. All workers explicitly use
gpt-6.1-sol with medium reasoning.

## Fixture gate

Live responses captured 2026-10-05 are in tests/fixtures/raw; aliases.json maps
human-readable fixture names to archived requests. pytest's snapshot(name) fixture
loads them without network. Expectations predate parser implementations and come
from reviewed research plus raw-response inspection. Never regenerate expectations
from the implementation under test. Research JSON examples are explicitly partial.

Expected inventories: 22 subjects; AM 7 areas/31 cards; BL 5/14; 459 documentation
entries; 29 official agencies; 21 EU agencies; registry groups 244/5/3/6/83/108;
369 workbook products; 22 HVD groups/88 links; 11 economy diagrams; four PDFs
with 2/3/4/3 pages. These counts apply only to committed snapshots.
All six registry TSV exports and three representative details were also captured.

Worker tests: `uv run pytest tests/sources/test_<group>.py -q`, where group is
products, agencies or downloads; run Ruff on owned files. Add regressions and
bounded live checks, labelled separately. Main runs combined suite and full live
collection after integration. Source tests initially fail on missing modules.

## Checkpoints

- Foundation dispatch gate: six shared behavior tests and Ruff passed before workers started.
- All fixture families captured before dispatch; source assertions frozen.
- Three gpt-6.1-sol/medium workers implemented the source groups and verified fixtures and bounded live smoke tests.
- Integration review caught documentation key instability; success-then-failed-refresh regression now proves retention under the same URL key.
- Final suite: 51 tests passed; Ruff passed; lockfile consistent.
- Full live capture: 643 requests. Two offline full replays generated 973 byte-identical JSON files including manifests.
- Ten source families complete. Products: 455 successful pages from 569 calendar codes; the remaining 114 requests all returned HTTP 404.
- First live run imported a pre-fix workbook parser; its raw response was complete. Corrected parser passed a separate live smoke and both final full replays.
- Registry fixtures were corrected to use actual JSON nulls for unused detail identifiers, preserving the source's court/foreign-mission response variants.
- Weekly workflow configured with locked installation, tests, source selection, 90-day raw artifacts and one Git publication step; not executed on GitHub in this task.
- Existing calendar source/data files unchanged; production outputs regenerated from archived live evidence. No LLM calls, Regina or linked observation-data harvest.

Working branch: codex/scb-extractions. No remote publication is implied by local implementation.
