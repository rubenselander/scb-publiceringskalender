# SCB downloads and collections: research draft

Live inspected 2026-10-05. This is research and contract design; no scheduled extractor has been implemented. Source language remains Swedish. Draft Pydantic v2 models live in [models/downloads_collections.py](models/downloads_collections.py); generate schemas with `model_json_schema(by_alias=True)` rather than maintaining parallel JSON Schema files. Each `.partial.json` file under [examples](examples) is explicitly a partial, source-derived sample, not a complete extraction.

## Official product workbook

Discover the anchor labelled **Statistikprodukter inom den officiella statistiken** on the [official statistics landing page](https://www.scb.se/sam-forum/hem/officiell-statistik/) each run. Its live target is [the 2026-02-24 workbook](https://www.scb.se/globalassets/sam-forum/officiell-statistik/produktdatabasen/uttag-fran-pdb-till-sam-forum-2026-02-24_publicerad-version.xlsx). Do not pin the dated filename.

Actual workbook: one sheet, `Blad1`, 370 physical rows including header, nine columns, 369 data rows and 369 unique product codes. Exact headers, in order:

`Statistikansvarig myndighet`, `Produktkod`, `Produktnamn`, `Syfte`, `Anvandare`, `Publiceringsstatus`, `Ämnesområde`, `Statistikområde`, `Periodicitet`.

`Anvandare` is the literal source spelling. Preserve cells, including trailing whitespace, codes, prose and missing cells. Observed publication statuses are `Aldrig publicering`, `Ej publicering`, `Publicering`; these are publication statuses, not a substitute for change-report active/inactive/closed semantics. The example preserves the first row (`AM0501`) with all nine original values. Exact product-code membership is the join boundary; do not fuzzy-match titles into official membership.

Use deterministic openpyxl extraction: validate sheet/header structure and code uniqueness, save provenance and content hash, then diff rows by exact `Produktkod`. An LLM adds no value to structural conversion. Changed/missing headers should fail visibly rather than silently shifting columns. Nullable cells are supported; unknown status values remain source strings. The observed code pattern is two uppercase letters plus four digits, enforced in this draft but requiring review if the workbook evolves.

## HVD collection

The [HVD statistics page](https://www.scb.se/vara-tjanster/oppna-data/vardefulla-datamangder-hvd/vardefulla-datamangder--statistik/) contains one HTML table with headers `Datamängd` and `Tabell/sparad fråga`: 22 data rows and 88 anchor occurrences. Targets use both `www.statistikdatabasen.scb.se` and `hvduppdrag.scb.se`; URLs distinguish `/pxweb/` tables and `/sq/` saved queries. Capture dataset grouping, anchor text, URL, and surrounding context deterministically. Retain repeated anchors: `/sq/150128` appears twice with different descriptions. The partial example includes ordinary table and saved-query groups.

Bound traversal to the landing table. Optional target verification is one hop to allowlisted hosts with request/page limits; following all table data is outside this metadata contract. No target availability or API/table identity has been verified in this research. Store saved-query URLs directly; do not guess an API path or claim all tables are ordinary SSD endpoints. Keep row prose as evidence where subheadings, classifications, or annotations group several links.

## Sveriges ekonomi

The [Tabeller och diagram collection](https://www.scb.se/hitta-statistik/statistik-efter-amne/ovrigt/allmant/sveriges-ekonomi/#_Tabellerochdiagram) currently has 11 rows, all `Diagram`, with `Namn`, `Typ`, `Datum`. These links are diagram detail pages, not separate discovered statistical product pages. Preserve the root collection membership and visit only its listed child URLs (one hop), with no site-wide traversal.

Four children were inspected: [service-sector confidence](https://www.scb.se/hitta-statistik/statistik-efter-amne/ovrigt/allmant/sveriges-ekonomi/pong/tabell-och-diagram/konjunkturbarometer-tjanstesektorn/), [monthly exchange rates](https://www.scb.se/hitta-statistik/statistik-efter-amne/ovrigt/allmant/sveriges-ekonomi/pong/tabell-och-diagram/valutakurser-manadsgenomsnitt/), and the [1989 interest-rate view](https://www.scb.se/hitta-statistik/statistik-efter-amne/ovrigt/allmant/sveriges-ekonomi/pong/tabell-och-diagram/kort-och-lang-ranta-1989-/) and [2008 interest-rate view](https://www.scb.se/hitta-statistik/statistik-efter-amne/ovrigt/allmant/sveriges-ekonomi/pong/tabell-och-diagram/kort-och-lang-ranta-2008-/). All expose an Excel `.xls` source asset and update date 2026-10-02. Confidence cites KI; the others cite Sveriges riksbank. The two interest pages have identical titles but different URLs and asset paths: key by URL, never title alone.

Deterministic detail parsing can capture subtitle, comments, source label, update date, image, Excel links and presence of the official-statistics mark. Each visited child needs its own provenance. The sample captures four children and download URLs; omitted subtitle/comments/images are explicit null/empty values, so it is not a complete detail extraction. No `.xls` contents or chart values were extracted. Remaining seven children and image extraction are unverified. Diagram source provider must not be replaced by SCB merely because SCB hosts the page.

## Changes in official statistics

The [inventory](https://www.scb.se/sam-forum/hem/officiell-statistik/andringar-i-den-officiella-statistiken/) currently links four PDFs. Discover links each run and fetch only listed PDFs; use content hashes to avoid reprocessing unchanged documents. Inventory labels are reporting periods, not exact effective dates.

| PDF reporting period | Pages | Printed document date |
| --- | ---: | --- |
| [Nov 2025–Feb 2026](https://www.scb.se/globalassets/sam-forum/officiell-statistik/andringar-i-officiell-statistik/forandringar-i-den-officiella-statistiken---nov-2025---feb-2026.pdf) | 2 | mars 2026 |
| [Apr–Oct 2025](https://www.scb.se/globalassets/sam-forum/officiell-statistik/andringar-i-officiell-statistik/forandringar-i-den-officiella-statistiken---april---okt-2025.pdf) | 3 | Okt 2025 |
| [Nov 2024–Mar 2025](https://www.scb.se/globalassets/sam-forum/officiell-statistik/andringar-i-officiell-statistik/forandringar-i-den-officiella-statistiken---1-nov-2024--31-mars-2025.pdf) | 4 | April 2025 |
| [Apr–Oct 2024](https://www.scb.se/globalassets/sam-forum/officiell-statistik/andringar-i-officiell-statistik/forandringar-i-den-officiella-statistiken---1-apr---31-okt.-2024.pdf) | 3 | Okt 2024 |

All four downloaded successfully and text extracted with bundled pypdf. Page 1 of the latest and Nov 2024–Mar 2025 reports was rendered with Poppler and visually inspected; layout changes between templates. Their common table fields are `Myndighet`, `Produktkod`, `Produktnamn`, `Beskrivning av ändring`. Repeated headers, wrapped cells, footers and rows split across pages need deterministic cleanup; retain all contributing one-based PDF pages and row ordinals. Product codes can repeat within a report, so `(PDF hash, row ordinal)` identifies notices.

Latest report p.2 marks AM0206 inactive and lists new products and renames. Apr–Oct 2025 p.1 explicitly dates SF0108 to 2025-04-10; its SF0206 narrative continues on p.2 and describes an intention. Apr–Oct 2024 NV0124 spans pp.2–3 and combines a proposal, reference period and related products NV0801/NV0123. Preserve these separate roles: never convert a reference quarter, report period, document date or publication date into an effective date. Nov 2024–Mar 2025 p.1 says additional subject/statistics-area changes due to the amended ordinance are omitted from its listing: the PDF inventory is not a complete change ledger.

Use deterministic download, page/text extraction, codes and source preservation; optionally use an LLM only to reconstruct difficult multi-page rows and interpret prose into change kinds, date claims and related codes. Keep descriptions, page references and interpretation evidence. `timing_claim` distinguishes source-claimed effective, planned, mixed and unclear; it is not independent verification of implementation. Uncertain/mixed cases require review. Preserve original Swedish and English names in descriptions rather than silently translating or correcting source misspellings. Never infer inactive = closed: reports distinguish retaining old results from removing them.

## Validation and remaining gaps

All four partial examples validated with Pydantic 2.13.5; all four generated schemas passed `Draft202012Validator.check_schema` and sample validation with a format checker. An unexpected field was rejected. These checks prove draft contract/sample compatibility, not extractor completeness, legal status, target availability, LLM reliability or scheduling behavior. Schemas are generated ephemerally; no hand-maintained schema duplicates are saved.

No Regina inspection, endpoint implementation, scheduling, observation collection or exhaustive PDF row extraction was performed. Downloaded research scratch files are removed after inspection; hashes and source URLs in examples support later reproducibility. Production ingestion should archive raw responses under its agreed data retention policy.
