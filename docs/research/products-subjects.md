# SCB products, subjects, and documentation: live source study

Research only, observed 2026-10-05. No production extractor or scheduled job was
implemented. Direct HTTPS GETs returned 200 for the seven sampled pages below.
Browser expansion was separately checked on Arbetsmarknad. This is a bounded
sample, not verification of every calendar product or documentation entry.

## Sources and observed structure

| Source | Live observation |
| --- | --- |
| [Official subjects index](https://www.scb.se/om-scb/samordning-av-sveriges-officiella-statistik/officiell-statistik-efter-amne/) | 22 links labelled with two-letter subject codes, including AM and BL. Links target the statistics subject pages. The introduction attributes the list to the annex of the statistics ordinance. |
| [Arbetsmarknad](https://www.scb.se/hitta-statistik/statistik-efter-amne/arbetsmarknad/) | 7 statistical-area accordion headings and 31 product cards. Cards have a product link, description, sometimes an explicit responsible-agency label, and sometimes comma-separated tags. |
| [Befolkning och levnadsförhållanden](https://www.scb.se/hitta-statistik/statistik-efter-amne/befolkning-och-levnadsforhallanden/) | 5 accordion areas and 14 cards. New subject code BL must not be derived from legacy product code BE0101. |
| [AM0201](https://www.scb.se/AM0201) | Full product page, official-statistics marker, summary, discontinued-product notice, no planned next release, result sections, documentation lists, contact, and tags. Its replacement is linked as Anställningar. |
| [PR0701](https://www.scb.se/PR0701) | External-agency product page: next release 2026-10-29, explicit Konjunkturinstitutet (KI) link, summary, contact, and tags. No documentation section exists in the inspected HTML. |
| [BE0101](https://www.scb.se/BE0101) | Full product page with grouped statistical tables and documentation. Documentation has multiple subseries headings and older groups marked as no longer updated; preserve their grouping. |
| [Documentation index](https://www.scb.se/dokumentation/) | 459 A–Ö list entries in #pageContent. Product targets commonly include #_Dokumentation. The PR0701 entry includes that fragment even though the target has no such section. |

The existing local calendar supplies product codes and short product URLs. The
parent investigation verified 569 distinct local product codes/URLs; this study
checked AM0201, PR0701, and BE0101 from that set. Do not mistake this local
calendar population for the official-subject membership population.

## HTTP versus browser

The seven sampled pages expose their relevant content in the initial HTML.
Use direct fetch for scheduled collection. Parse #pageContent rather than the
whole document: cookie banners, menus, footers, and ReadSpeaker links are noise.
The document has another h1 for the cookie banner, so a global first-h1 selector
is incorrect.

On the live Arbetsmarknad page, all seven areas initially appeared collapsed.
Clicking **Öppna alla** made the product links, descriptions, agency labels, and
tags visible. The same material was already present in the fetched HTML inside
`li.expandable-box.expandable-box-content-filled`, with heading
`.expandable-box-heading` and cards `.expandable-box-content-item`. Expansion
is therefore a visibility operation for this sample, not a required extraction
request. A browser fallback is justified only if a future response omits the
content or requires interaction; no universal guarantee was established.

Product-page table groups are also present in the initial HTML, including
collapsed BE0101 table groups. Pagination/archive controls exist on AM0201 and
BE0101; their additional endpoints and archive completeness were not studied.
Capture their links and labels, and keep following them outside this initial
bounded-page extraction unless separately authorized.

## Identifiers, wording, and hierarchy

- Preserve displayed subject code, label, source link, and source order. Subject
  code is sourced from the official index; product-code prefixes do not define
  the current subject taxonomy.
- Area accordion headings have no public statistical-area code or navigable
  URL in the sampled markup. Their toggles use `Javascript:void(0)`. Preserve the
  label under its parent subject, with null source code/URL rather than inventing
  an official identifier from a slug. Product URLs retain the area path.
- Keep product short addresses, requested URLs, response URLs, and canonical
  URLs separate. GET /PR0701 remained /PR0701 as the response URL, while the
  canonical link supplied its full subject/area/product URL.
- Product codes may be observed in the page's short address or calendar links,
  or joined through an exact verified calendar URL/canonical mapping. Record
  the identifier source. Subject cards themselves do not display product codes.
- Preserve source agency text where it exists. An absent agency label on a
  subject card does not prove SCB is responsible.
- Preserve next-release wording independently of its date. AM0201 has no
  planned release and a discontinuation notice; PR0701 has a scheduled date.
- Keep each documentation link under its original heading path. BE0101 uses
  several same-level h4 headings for the documentation type and subseries;
  source containers, not heading-number guesses alone, determine hierarchy.
- The AM0201 link labelled **2017 (pdf)** points to
  `am0201_kd_2016_dh_170524.pdf`. Keep displayed label and original href. Do not
  derive reference year or title from filename.
- Tags have actual categoryId links on product pages. Subject cards expose
  comma-separated tag text; keep that text without inventing equivalent IDs.

## Bounded traversal proposal

1. Deduplicate calendar product URLs/codes and fetch each selected SCB product
   page once; retain failures rather than silently dropping them.
2. Fetch the official subject index, then only its 22 linked subject pages.
   Extract their accordion cards without visiting product pages again.
3. Fetch the documentation index; deduplicate exact target document URLs after
   removing fragments only for network requests, retaining the original href.
   Read documentation sections on the linked SCB product pages once, reusing
   already fetched product responses where possible.
4. Keep document asset/PDF, external agency, MetaPlus, statistics-database,
   archive, and tag-filter links as metadata. Do not recursively crawl them.
5. Bound requests by the discovered target set, same-origin allowlist, timeout,
   retry limit, and per-run response-size/page limits; report unvisited targets
   and failed targets explicitly. These are proposed collection controls, not
   verified production defaults.

Subject pages include discontinued and older products. Membership in a subject
page is not proof that each card is currently an official statistical product.
Keep the official marker and source wording; authoritative exact-product
membership would require a separate source decision.

## Pydantic contracts and validation

[products_subjects.py](models/products_subjects.py) is the source of truth for
three root outputs: `ProductPage`, `OfficialSubjects`, and `DocumentationIndex`.
Use, for example, `ProductPage.model_json_schema()` for LLM structured output. There are no
parallel hand-maintained JSON schemas. All models use `extra='forbid'`.

`SubjectPage` and `DocumentationPage` can also be serialized individually for
the requested per-page files; the collection models retain discovery membership
and traversal status. Product snapshots use the requested product code as the
filename, with identity discrepancies retained explicitly for review.

Required page identity and provenance fields have no defaults. Nullable scalar
fields are required keys with explicit null when absent or not observed. Arrays
default to empty. Empty arrays are only evidence of absence when the containing
page/section was successfully inspected: response failures must stay in fetch
status rather than being reported as empty successful extraction. At run level,
`traversal_complete` and unfetched target arrays distinguish incomplete traversal.
Research samples additionally carry `capture_scope='research_sample'` and must
never be presented as a completed harvest.

Provenance records requested/response/canonical URL, UTC observation time, HTTP
status, SHA-256 of response bytes, scoped selector, capture scope, and warnings.
Every link records displayed text, authored href, and resolved absolute URL.
Heading paths, paragraph arrays, table headers, and ordered cell arrays retain
source structure. Codes use observed patterns; arbitrary source links remain
strings so mailto, tel, and other source schemes survive. No field values should
be supplied from calendar metadata as though they were observed on a page.

Three source-derived snapshots in [examples](examples/) validated with
Pydantic **2.13.5**, then against their generated schema with
`jsonschema.Draft202012Validator` (including `check_schema`):

- `product-page.json`: PR0701 product identity, summary, release, explicit agency,
  contact, and tags.
- `official-subjects.json`: all 22 index references and the full AM accordion
  card list; the other 21 subject pages are explicitly unvisited within this
  example. BL was separately inspected for the observations above.
- `documentation-index.json`: two selected A–Ö entries and their inspected
  AM0201/PR0701 targets. The AM0201 sample includes two principal document lists;
  MetaPlus and all other index entries are outside this sample. An empty
  unfetched list here refers only to the selected two targets, not the full index.

These checks validate the draft contracts and sample data, not an automated
extractor, an LLM call, archive traversal, downloaded document contents, or
coverage of the 569 calendar product URLs and 459 documentation entries.
