# SCB extraction expansion: source study and draft contracts

Research date: 2026-10-05. These are draft output contracts and source observations, not deployed scrapers. The calendar scripts, existing data, and scheduled workflow are unchanged.

## Purpose and boundaries

Extend the calendar repository with scheduled, source-faithful JSON extracts. Keep one file per product code, one object for each requested whole-page source, one object per subject/documentation page, and one common structure for change-notice PDFs. Preserve Swedish wording, links, codes, ordering, and hierarchy. Regina regional divisions are explicitly deferred until all other sources are implemented and verified.

The local baseline is commit `2e9e4ca`: 27,618 calendar rows, 569 distinct product codes and 569 distinct product URLs, with no uncoded rows. These counts describe the local file, not a new live calendar harvest. At inspection, `main` was clean and one commit behind its locally known remote tracking branch; that commit changes raw calendar files. No pull or calendar rerun was performed.

## Research deliverables

- [Product pages, subjects, and documentation](products-subjects.md).
- [Responsible agencies, European responsibilities, legislation, and the agency register](agencies-law.md).
- [Product workbook, HVD, Sveriges ekonomi, and change PDFs](downloads-collections.md).
- [Draft Pydantic models](models/). Source-specific reports explain the fields and limitations.

Pydantic v2 models are the single source of truth, as requested. Validate parser/model responses with `model_validate()` or `model_validate_json()`, serialize with `model_dump_json()`, and generate LLM-facing JSON Schema with `model_json_schema()`. Do not maintain separate handwritten schemas. See [Pydantic's schema documentation](https://docs.pydantic.dev/latest/concepts/json_schema/).

## Source-to-output map

| Requested source | Output unit and important content | Initial parsing choice |
|---|---|---|
| Calendar product pages | One file per discovered product code; requested/final/canonical URLs, description, agency labels, sections, releases and documentation links | HTML structure; optional LLM for prose |
| Statistical responsible agencies | One page object; agencies and nested subject/area responsibilities, preserving bold spans | HTML headings/lists/formatting |
| Official statistics by subject | Index plus one object per subject; subject code, area hierarchy and product cards | HTML includes sampled collapsed content |
| European statistical responsibilities | One page object; scope/definitions and agency responsibilities | HTML structure and source wording |
| Current official product workbook | One workbook object with exact sheet/column/row provenance and product codes | Discover current download link, parse XLSX |
| Official statistics regulation | One document object; metadata, ordered provisions/annex content, amendment and effective-date markers | HTML structure; preserve temporal text |
| Documentation | Index and linked product-page objects; documentation categories, periods and file links | HTML index plus shared product model |
| HVD statistics | One page object; table rows and linked products/tables | HTML table; no observation-data traversal requested |
| Sveriges ekonomi | Collection object and linked diagram-page objects, with assets and related links | HTML plus bounded child-page traversal |
| Government agencies register | One collection object; all source groups and records, including identifiers and source blanks | Observed backend data/downloads; verify all groups |
| Changes to official statistics | Index plus one common object per PDF; document/page evidence, affected products and change status | PDF text/layout, selective LLM interpretation |
| Regina regional divisions | Deferred; no guessed model or crawler | Only after the preceding sources are complete |

These boundaries describe the requested extracts. Downloading every linked data table, historical release, or documentation PDF would expand the scope; only the change-notice PDFs and current product workbook are explicitly requested as file-content extracts.

## Recommended extraction approach

Three approaches are possible:

1. **Deterministic first, selective LLM extraction (recommended).** Parse existing HTML structure, workbook cells, and register records directly. Use a schema-constrained model for prose interpretation and irregular PDF content where it adds value. This retains exact identifiers and provides repeatable structural checks.
2. **LLM first for every page.** Fast to prototype across heterogeneous pages, but repeated whole-page processing costs more and can omit valid source content while still returning valid JSON. It still needs discovery, fetching, hierarchy preservation, provenance, and coverage checks.
3. **Deterministic only.** Best reproducibility for regular source structures, but more custom work for irregular PDF layouts and semantic change descriptions.

Fetching a page without browser interaction does not itself imply that an LLM is the best parser. Decide from the actual markup and variation between examples. Benchmark model extraction on held-out examples before choosing a production model; no paid model calls or quality/cost benchmark were made in this research.

### Preserve the existing fetch/parse separation

Keep the calendar flow intact. Add source-specific fetch and parse steps alongside it, sharing only small HTTP, provenance, and validation helpers where useful. Avoid turning this small repository into a general scraping framework.

Each fetch should record the requested URL, final redirected URL, fetch time in UTC, content type, response status, and raw-content SHA-256. Preserve source bytes separately from normalized JSON so a schema/parser change can reprocess the same evidence. A fetch timestamp is not a source publication or effective date.

The draft models are independent research contracts, not yet a single production API. Standardize their envelope/naming and schema version policy when implementing, without discarding source-specific fields. Provider-facing LLM schemas may need an adapter for the endpoint's supported subset of generated JSON Schema.

### Identity and relationships

- Calendar product codes are discovery seeds; keep one requested-code output even if its URL redirects. Do not silently assign a redirect target's identity to the old code.
- Calendar membership, listing under a subject, HVD membership, and membership in the current official product workbook are separate claims with separate sources.
- Use exact `Produktkod` matches for joining workbook records. Never infer official status merely from a calendar entry or use a fuzzy product-name match as a confirmed join.
- Keep source subject/area hierarchies independently. Different SCB pages and the legal annex can describe different classifications; apparent label similarity is not proof of equivalence.
- Match organizations by source identifiers where present. Preserve source names and leave ambiguous aliases unresolved.
- Change notices may mention no product code, multiple products, proposals, or future changes. Preserve that uncertainty and attach evidence rather than forcing every PDF onto exactly one product.
- Preserve actual links as absolute URLs. Keep section anchors when they identify a section, but deduplicate network fetches independently of fragments.

### LLM boundary

Supply cleaned main-content HTML or a structured text representation that retains headings, bold spans, list nesting, tables, links, and PDF page boundaries. Plain text with all markup removed would destroy the hierarchy requested by the user.

Use a fixed extraction instruction and treat supplied page/PDF text as untrusted source data, never as instructions. Ask only for source-supported values; allow nulls or explicit unresolved fields. Record model/provider, prompt version, schema version, input hash, and token/cost usage. Cache by input hash plus extraction configuration; re-extract when the schema or prompt changes even if the source has not.

[OpenRouter's structured-output documentation](https://openrouter.ai/docs/guides/features/structured-outputs) supports `response_format.type = json_schema` on compatible endpoints, recommends `require_parameters: true`, and notes that strict enforcement and supported schema features vary by provider. Validate every completed response locally as well. A schema-conforming answer is not evidence of factual accuracy or complete coverage.

Start with a modest, explicit per-run spending cap, bounded retries, and no automatic switch to a more expensive model. Model choice and budget remain implementation settings to agree; no credentials are needed to review these drafts.

## Scheduling proposal

Use the current weekly Monday refresh as the initial default for the added sources, with manual source-specific reruns. This is a proposal; no new schedules have been enabled.

Discover child URLs and download URLs afresh from their parent pages on each run. Honor conditional HTTP responses where supported, and skip parsing/model calls when the relevant content and extraction configuration are unchanged. Do not pin the dated XLSX filename supplied in the request.

Give sources independent run status so a failed PDF or register request cannot mark every source successful. Stage results and replace each source's published snapshot only after its coverage and schema checks pass. Keep the last successful output after failures, alongside a failure record; an HTTP error, empty response, or missing selector must not become an empty successful collection.

For collections, reconcile discovered, attempted, parsed, failed, and retained items. Do not delete previously captured products merely because an index temporarily omits them. Explicitly record disappearance and distinguish removal evidence from a failed fetch. Bound concurrency and retry with backoff to avoid unnecessary load.

## Acceptance checks before scheduling

1. Validate full output examples against each schema and verify representative fields against saved raw sources.
2. Compare discovered source items with emitted items, including collapsed sections, pagination, workbook rows, and PDFs.
3. Prove exact code/link preservation, hierarchy retention, redirect handling, and unresolved relationships.
4. Exercise changed markup, incomplete downloads, empty responses, invalid model output, and retry exhaustion without overwriting last-good data.
5. Repeat parsing from the same raw snapshot; compare semantic output and separately record run metadata.
6. Complete all non-Regina sources before investigating or implementing the regional-division harvest.

Schema meta-validation only proves that a schema is well formed. Small research examples do not prove production coverage or extraction reliability. The source reports distinguish observed facts, draft choices, and unverified cases.

## Checks performed on these drafts

All 11 saved examples passed Pydantic validation, JSON serialization/validation round trips, and validation of the serialized object against its generated JSON Schema. Generated schemas passed Draft 2020-12 meta-validation. These were local checks using Pydantic 2.13.5, not live tests of a scheduled extraction pipeline. Source inspection findings and sampling limits are in the three reports above.
