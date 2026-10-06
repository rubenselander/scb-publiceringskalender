# Agency lists, regulation and registry: extraction research

Verified against live sources on 2026-10-05. This is research and draft data modelling; no scheduled fetcher or production changes were made.

## Draft contract and examples

The source of truth is [models/agencies_law.py](models/agencies_law.py), using Pydantic v2 and `ConfigDict(extra="forbid")`. Generate JSON Schema with the relevant document class's `model_json_schema()` when needed; there are no parallel hand-maintained schema files. The four document types are `OfficialAgenciesDocument`, `EuropeanAgenciesDocument`, `OfficialStatisticsRegulationDocument` and `AgencyRegistryDocument`.

Source-derived examples:

- [official-agencies.json](examples/official-agencies.json): all 29 authority sections, their statistics links and bold-subject hierarchy, plus introductory scope statements.
- [european-agencies.json](examples/european-agencies.json): all 21 list members, introductory responsibility definition and SCB coordination role, and the separate central-bank statement and links.
- [official-statistics-regulation.json](examples/official-statistics-regulation.json): **partial sample**, metadata and selected ordered appendix blocks/temporal markers; not a full legal extraction or an interpreted current responsibility map.
- [agency-registry.json](examples/agency-registry.json): **partial sample**, one table row from each of the six groups, with the actual total rows fetched per group; not a full registry export.

Every example carries URL, retrieval timestamp and SHA-256 of the fetched bytes. Registry group provenance also records the POST parameters. These hashes identify research retrievals; the raw files were temporary, not committed. A production run should keep its raw source snapshot alongside its output. Ordinal position in arrays is source ordering, not an agency identifier.

Required fields must be extracted or validation fails. Optional `None` defaults mean omitted fields remain distinguishable from explicit JSON null through `model_fields_set` and `model_dump(exclude_unset=True)`. Source blank cells remain `""`; absent/unextracted optional fields stay absent; explicit null means unavailable, never zero. Preserve spelling, punctuation, casing, repeated text and actual identifiers. Do not infer IDs by matching names across these four sources.

## SCB official-statistics responsibilities

[Official page](https://www.scb.se/om-scb/samordning-av-sveriges-officiella-statistik/statistikansvariga-myndigheter/).

The live HTML contains all 29 authority sections in one response; there is no pagination or demonstrated API for this page. Each authority is a `main h3`, followed by a link whose visible text begins `Statistik hos`, then responsibility paragraphs. Resolve relative hrefs against the page URL, preserving the published href meaning and link text. Stop a section at the next `hr`, `h3` or `h2`.

Within responsibility paragraphs, `<strong>` supplies the subject label; following ordinary text separated by `<br>` supplies its statistical areas. Retain the current subject across paragraphs until another bold label appears. Some `<br>` are inside `<strong>`, and the SCB authority heading itself is bold: only interpret bold labels inside responsibility paragraphs. Store ordered `{subject_name, statistical_areas}` objects rather than flattening all text. Live examples include Finansinspektionen with two subjects and SCB with twelve.

Preserve the source typo `Kultur, biblotek och fritid` for Kungliga biblioteket and the colon in `Socialförsäkring:` for Pensionsmyndigheten. The latter is source punctuation, not a separate normalized subject identifier. All 29 parsed sections matched the page's count. Keep the introductory government/appendix definition and its regulation link as source statements; do not conflate this list with the European list.

## SCB European-statistics responsibilities

[Official page](https://www.scb.se/om-scb/samordning-av-europeisk-statistik-i-sverige/myndigheter-som-ansvarar-for-europeisk-statistik/).

All 21 authority names occur in a single `ul` following the heading `Svenska myndigheter med ansvar för europeisk statistik`. No pagination, per-member ID, per-member responsibility domain or per-member statistics URL is published in this list. Extract names and order directly; preserve the introductory scope definition as prose, with links if present. It defines membership by statistics included in Eurostat's annual work programme and the European-statistics product list, and separately describes SCB's coordination responsibility.

The ingress mentions Riksbanken, while the later central-bank section says central banks report to Eurostat but are not classified as an authority responsible for European statistics. Preserve both source statements and that section's ESCB/ESS links. Do not add Riksbanken as a twenty-second member or resolve the prose difference through invented classification. The names include Ekonomistyrningsverket; a refresh should capture what this page actually states even if another source has newer agency wording.

## Regulation 2001:100

[Riksdagen page](https://www.riksdagen.se/sv/dokument-och-lagar/dokument/svensk-forfattningssamling/forordning-2001100-om-den-officiella_sfs-2001-100/); [direct first-party HTML](https://data.riksdagen.se/dokument/sfs-2001-100.html); [Riksdagen open data](https://www.riksdagen.se/sv/dokument-och-lagar/riksdagens-oppna-data/).

The direct HTML was fetched successfully with Windows `curl.exe`, after Python's certificate trust check failed and PowerShell's transport closed. This was a client access limitation; the source was available. A guessed `.json` document URL was not verified and must not be treated as a working API.

Metadata includes SFS number, department, issue date, amended-through wording, amendment-register link and full-text source link. The live amended-through value is `t.o.m. SFS 2026:1406`. The document has numbered provisions, section headings, transition provisions and appendices. The appendix includes authority lists, subject lists, responsibility mappings and sensitive-personal-data provisions; extracting only authority names would omit substantial requested law content.

The live text displays **parallel future versions**, with cessation/effective markers referring to 2027-01-01 and 2029-01-01. Some future text uses `Myndigheten för tillväxt- och transportanalys`, while earlier text uses `Myndigheten för tillväxtpolitiska utvärderingar och analyser`. Preserve markers and blocks in source order. Do not select a version by the highest amendment number, concatenate all responsibility mappings as simultaneously effective, or compute current applicability without separately verified temporal interpretation.

Responsibility mappings are laid out as text and `<pre>` blocks, not an HTML table. Agency names and statistical-area labels can wrap onto separate lines/blocks; tabs and spaces carry column alignment. Preserve raw ordered blocks including their markup type before attempting a structured mapping. An LLM may propose subject/area/authority groupings from these blocks, but acceptance must check source spans, wrapped continuations and temporal boundaries. The current draft exposes faithful source blocks rather than speculative interpreted mappings. The full document is available in one response; no pagination is needed.

## SCB agency registry

[Registry](https://myndighetsregistret.scb.se/Myndighet); [first-party client script](https://myndighetsregistret.scb.se/Scripts/Modules/myndighet-2.js).

The initial page has an empty grid and a six-group select; its option value `1` is only a placeholder. Its text says agency information updates weekly. The client uses these live, verified routes:

- `POST /Myndighet/HamtaMynd`, JSON body `{"mynd": "<exact selected group label>"}`, returns HTML containing all group rows.
- `POST /Myndighet/HamtaEnMynd`, JSON body with `peorgnr`, `cfarnr`, `lopnr`, returns detail HTML. The client prefixes `16` to the de-hyphenated organisation number for `peorgnr`; courts use their published CfarNr, foreign missions their published LopNr. These request transformations are not new export identifiers.
- `GET /Myndighet/PrepareDownload?format=false&myndgrupp=...` returns a JSON-string download path. The returned path contained literal `\u0026`, requiring decoding before the subsequent GET. `format=true` selects Excel in the client; Excel was not downloaded during this research.
- The returned `/myndighet/download?...&format=False` path yields a UTF-8 TSV attachment containing the complete selected group's detailed fields, exceeding the visible grid columns.

| Option | Source group | HTML rows | TSV verified |
|---|---|---:|---|
| 2 | Statliga förvaltningsmyndigheter | 244 | 244 rows |
| 3 | Myndigheter under riksdagen | 5 | 5 rows |
| 4 | Statliga affärsverk | 3 | 3 rows |
| 5 | AP-fonder | 6 | 6 rows |
| 6 | Sveriges domstolar samt Domstolsverket | 83 | 83 rows |
| 7 | Svenska utlandsmyndigheter | 108 | download timed out |

These are fetched group row counts, not a deduplicated count of distinct agencies. The HTML/client agreement establishes browser-side pagination: the script initializes DataTables on the returned table, without a server-side page request. The help text says five initial rows, while the live script sets six; do not rely on either visible page size for completeness. Parse all `tbody tr` rows or use the group download.

Groups 2–5 grid columns are `Namn`, `Organisationsnr`, `SFS`, `WebbAdress`. Group 6 has `Namn`, hidden `CfarNr`, `Postadress`, `Postnr`, `Postort`, `WebbAdress`. Group 7 has `Land`, hidden `LopNr`, `Namn`, `Ambassadör/Generalkonsul`, `WebbAdress`. Hidden cells remain essential source identifiers.

Verified TSV columns for groups 2–6 are `Organisationsnr`, `Namn`, `PostAdress`, `PostNr`, `PostOrt`, `BesöksAdress`, `BesöksPostNr`, `BesöksPostOrt`, `Tfn`, `Fax`, `Epost`, `Webbadress`, `SFS`; groups 5 and 6 also have `Sort`. Preserve phone and postal numbers as strings, including leading zeros and spaces. Do not convert `000 00` to null or turn bare website strings into assumed HTTPS URLs. Group 7 download fields remain unverified; the flexible source-labelled field model does not invent them.

Three detail responses were checked: an administrative authority, a court and a foreign mission. The first two include contact/address/SFS fields. The first foreign-mission row has actual LopNr `201` but blank other cells, and its detail response also has blank values. Keep this anomaly. A scheduled extractor should retry failed group downloads, distinguish unavailable from empty, and never publish a partial retrieval as complete. Prefer the deterministic TSV path where it works; HTML tables/details remain a source-backed fallback.

## Validation performed

With installed Pydantic 2.13.5 and `jsonschema`, all four draft examples validated against their document models and their ephemeral `model_json_schema()` results; generated schemas passed `Draft202012Validator.check_schema`. The two SCB lists matched their stated counts (29 and 21). All six registry HTML groups were fetched, five TSV group counts matched, and three detail responses were checked. The law and registry saved examples remain explicitly partial. Schema conformance does not prove an interpreted legal mapping or complete foreign-mission download.
