-- Normalized, locally built view of every committed harvest in data/.
-- Links between families are soft: *_id / product_code columns are nullable and
-- anything that could not be resolved is listed in link_issues, never dropped.

-- Metadata ---------------------------------------------------------------

CREATE TABLE build_info (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE source_status (
    family TEXT NOT NULL,            -- calendar | dokumentation | extractions
    source TEXT NOT NULL,
    key TEXT NOT NULL,
    status TEXT NOT NULL,            -- fresh | retained | failed | no_longer_listed | missing | invalid
    error TEXT,
    complete INTEGER,
    discovery_complete INTEGER,
    PRIMARY KEY (family, source, key)
);

CREATE TABLE provenance (
    source TEXT NOT NULL,
    key TEXT NOT NULL,
    part TEXT NOT NULL DEFAULT '',   -- empty for the document itself, else e.g. group:2
    source_file TEXT NOT NULL,
    requested_url TEXT,
    response_url TEXT,
    canonical_url TEXT,
    fetched_at TEXT,
    http_status INTEGER,
    content_type TEXT,
    content_sha256 TEXT,
    PRIMARY KEY (source, key, part)
);

-- Dimensions -------------------------------------------------------------

CREATE TABLE agency (
    agency_id INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    origin TEXT NOT NULL,
    registry_row_id INTEGER REFERENCES registry_agency (registry_row_id),
    registry_group TEXT,
    registry_org_nr TEXT,
    registry_cfar_nr TEXT,
    registry_lop_nr TEXT
);

CREATE TABLE agency_alias (
    alias TEXT NOT NULL,
    alias_norm TEXT NOT NULL,
    agency_id INTEGER NOT NULL REFERENCES agency (agency_id),
    origin TEXT NOT NULL,
    PRIMARY KEY (alias, agency_id, origin)
);

CREATE TABLE product (
    product_code TEXT PRIMARY KEY,
    name TEXT,
    agency_id INTEGER REFERENCES agency (agency_id),
    in_workbook INTEGER NOT NULL,
    in_calendar INTEGER NOT NULL,
    has_product_page INTEGER NOT NULL,
    publication_status TEXT,
    periodicity TEXT,
    subject_area TEXT,
    statistics_area TEXT
);

CREATE TABLE product_name_alias (
    product_code TEXT NOT NULL,
    name TEXT NOT NULL,
    origin TEXT NOT NULL,
    PRIMARY KEY (product_code, name, origin)
);

CREATE TABLE product_alias (
    product_code TEXT NOT NULL,
    alias_of TEXT NOT NULL,
    evidence TEXT NOT NULL,
    PRIMARY KEY (product_code, alias_of)
);

CREATE TABLE subject (
    subject_code TEXT PRIMARY KEY,
    title TEXT,
    index_text TEXT,
    index_url TEXT,
    source_file TEXT
);

CREATE TABLE statistical_area (
    area_id INTEGER PRIMARY KEY,
    subject_code TEXT NOT NULL REFERENCES subject (subject_code),
    ordinal INTEGER NOT NULL,
    label TEXT NOT NULL,
    source_anchor TEXT,
    source_url TEXT
);

CREATE TABLE document (
    url TEXT PRIMARY KEY,            -- canonical_url() of the link
    url_sha TEXT NOT NULL,           -- url_key(), same as extraction filenames
    title TEXT,
    doc_type TEXT,
    year TEXT,
    filetype TEXT,
    source_url TEXT,
    heading TEXT,
    first_origin TEXT NOT NULL
);

CREATE TABLE document_product_claim (
    url TEXT NOT NULL REFERENCES document (url),
    product_code TEXT NOT NULL,
    origin TEXT NOT NULL,            -- harvest_filename | harvest_block | harvest_candidate | product_page | metaplus_query | ...
    review_required INTEGER NOT NULL,
    PRIMARY KEY (url, product_code, origin)
);

CREATE TABLE product_subject_claim (
    product_code TEXT NOT NULL,
    subject_code TEXT,
    subject_label TEXT,
    statistical_area_label TEXT,
    origin TEXT NOT NULL             -- workbook | subject_page | harvest
);

-- Calendar ---------------------------------------------------------------

CREATE TABLE calendar_entry (
    entry_id INTEGER PRIMARY KEY,    -- line number in calendar.jsonl
    publish_date TEXT,
    product_code TEXT,
    product_name TEXT,
    reporting_round TEXT,
    reference_period TEXT,
    published_at TEXT,
    responsible_agency TEXT,
    responsible_agency_id INTEGER REFERENCES agency (agency_id),
    product_url TEXT,
    row_hash TEXT NOT NULL,
    occurrence INTEGER NOT NULL      -- 1 for the first identical row, 2 for its duplicate, ...
);

CREATE TABLE calendar_entry_form (
    entry_id INTEGER NOT NULL REFERENCES calendar_entry (entry_id),
    ordinal INTEGER NOT NULL,
    form TEXT NOT NULL,
    PRIMARY KEY (entry_id, ordinal)
);

CREATE TABLE calendar_entry_publisher (
    entry_id INTEGER NOT NULL REFERENCES calendar_entry (entry_id),
    ordinal INTEGER NOT NULL,
    name TEXT NOT NULL,
    agency_id INTEGER REFERENCES agency (agency_id),
    PRIMARY KEY (entry_id, ordinal)
);

CREATE TABLE calendar_gap (
    from_date TEXT,
    to_date TEXT,
    expected INTEGER,
    retrieved INTEGER
);

-- Dokumentation harvest (verbatim rows) ----------------------------------

CREATE TABLE harvest_document (
    document_id TEXT PRIMARY KEY,
    source TEXT,
    agency TEXT,
    agency_id INTEGER REFERENCES agency (agency_id),
    product_code TEXT,
    product_name TEXT,
    product_match TEXT,
    doc_type TEXT,
    year TEXT,
    title TEXT,
    url TEXT,
    canonical_url TEXT,
    filetype TEXT,
    source_url TEXT,
    heading TEXT,
    subject_area TEXT,
    statistics_area TEXT,
    candidate_product_code TEXT,
    review_required INTEGER,
    source_status TEXT,
    provenance TEXT
);

CREATE TABLE dokumentation_product (
    product_code TEXT PRIMARY KEY,
    product_name TEXT,
    responsible_agency TEXT,
    in_calendar INTEGER,
    last_publish_date TEXT,
    active INTEGER,
    documents INTEGER,
    sources TEXT,
    kd_count INTEGER, kd_latest_year TEXT, kd_latest_url TEXT,
    bas_count INTEGER, bas_latest_year TEXT, bas_latest_url TEXT,
    staf_count INTEGER, staf_latest_year TEXT, staf_latest_url TEXT,
    scbdok_count INTEGER, scbdok_latest_year TEXT, scbdok_latest_url TEXT,
    metaplus_count INTEGER, metaplus_latest_year TEXT, metaplus_latest_url TEXT
);

CREATE TABLE siris_dokument (
    row_id INTEGER PRIMARY KEY,
    verkform TEXT, omrade TEXT, lasar TEXT, doc_id TEXT, typ TEXT,
    filetype TEXT, sos TEXT, tabell_nr TEXT, huvudrubrik TEXT, rubrik TEXT,
    titel TEXT, url TEXT, canonical_url TEXT, doc_type TEXT, year TEXT,
    fetched TEXT, source_url TEXT, provenance TEXT
);

CREATE TABLE sam_dokument (
    row_id INTEGER PRIMARY KEY,
    name TEXT, agency TEXT, agency_id INTEGER REFERENCES agency (agency_id),
    source_url TEXT, source_title TEXT, href TEXT, canonical_url TEXT, text TEXT,
    product_code TEXT, doc_type TEXT, year TEXT, filetype TEXT, fetched TEXT,
    provenance TEXT
);

-- Extractions: products, subjects, documentation pages -------------------

CREATE TABLE product_page (
    page_key TEXT PRIMARY KEY,       -- extraction file key: the product code, else url_key()
    product_code TEXT,
    product_code_source TEXT,        -- calendar | page_short_address | unknown
    title TEXT,
    short_address TEXT,
    summary TEXT,
    official_statistics_marker TEXT,
    next_publication_text TEXT,
    next_publication_date TEXT,
    responsible_agency_label TEXT,
    responsible_agency_url TEXT,
    agency_id INTEGER REFERENCES agency (agency_id),
    requested_url TEXT,
    canonical_url TEXT,
    fetched_at TEXT
);

CREATE TABLE product_page_notice (
    page_key TEXT NOT NULL REFERENCES product_page (page_key),
    ordinal INTEGER NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (page_key, ordinal)
);

CREATE TABLE product_page_tag (
    page_key TEXT NOT NULL REFERENCES product_page (page_key),
    ordinal INTEGER NOT NULL,
    text TEXT, href TEXT, url TEXT,
    PRIMARY KEY (page_key, ordinal)
);

CREATE TABLE product_page_section (
    section_id INTEGER PRIMARY KEY,
    page_key TEXT NOT NULL REFERENCES product_page (page_key),
    ordinal INTEGER NOT NULL,
    heading_path TEXT,               -- JSON list of {text, level, anchor}
    heading_text TEXT,               -- heading texts joined with ' > '
    paragraphs TEXT,                 -- JSON list
    table_headers TEXT,              -- JSON list
    table_rows TEXT                  -- JSON list of lists
);

CREATE TABLE product_page_link (
    section_id INTEGER NOT NULL REFERENCES product_page_section (section_id),
    ordinal INTEGER NOT NULL,
    text TEXT, href TEXT, url TEXT,
    PRIMARY KEY (section_id, ordinal)
);

CREATE TABLE subject_product_card (
    card_id INTEGER PRIMARY KEY,
    subject_code TEXT NOT NULL,
    area_id INTEGER REFERENCES statistical_area (area_id),
    ordinal INTEGER NOT NULL,
    link_text TEXT,
    link_url TEXT,
    link_canonical_url TEXT,
    product_code TEXT,
    product_code_origin TEXT,        -- source | url_match | unresolved | ambiguous
    summary TEXT,
    responsible_agency_text TEXT,
    agency_id INTEGER REFERENCES agency (agency_id),
    tags_text TEXT
);

-- Extractions: agencies; reference registry rows -------------------------

CREATE TABLE scope_statement (
    source TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    text TEXT,
    links TEXT,
    PRIMARY KEY (source, ordinal)
);

CREATE TABLE official_agency (
    ordinal INTEGER PRIMARY KEY,
    agency_name TEXT NOT NULL,
    agency_id INTEGER REFERENCES agency (agency_id),
    statistics_link_text TEXT,
    statistics_url TEXT
);

CREATE TABLE official_agency_subject (
    agency_ordinal INTEGER NOT NULL REFERENCES official_agency (ordinal),
    subject_ordinal INTEGER NOT NULL,
    area_ordinal INTEGER NOT NULL,
    subject_name TEXT,
    statistical_area TEXT,
    PRIMARY KEY (agency_ordinal, subject_ordinal, area_ordinal)
);

CREATE TABLE european_agency (
    ordinal INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    agency_id INTEGER REFERENCES agency (agency_id)
);

CREATE TABLE european_central_bank (
    heading TEXT, statement TEXT, links TEXT
);

-- From data/reference/agency_registry_linked.json (the register extraction is disabled).
CREATE TABLE registry_agency (
    registry_row_id INTEGER PRIMARY KEY,
    group_option_value TEXT NOT NULL,
    group_name TEXT,
    listed_agency TEXT,              -- the agency the reference file links the row to
    ordinal INTEGER NOT NULL,
    org_nr TEXT, cfar_nr TEXT, lop_nr TEXT,
    name TEXT,
    agency_id INTEGER REFERENCES agency (agency_id)
);

CREATE TABLE registry_agency_field (
    registry_row_id INTEGER NOT NULL REFERENCES registry_agency (registry_row_id),
    ordinal INTEGER NOT NULL,
    label TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (registry_row_id, ordinal)
);

-- Extractions: workbook, HVD, change reports -----------------------------

CREATE TABLE official_product_row (
    ordinal INTEGER PRIMARY KEY,
    product_code TEXT NOT NULL,
    responsible_authority TEXT,
    agency_id INTEGER REFERENCES agency (agency_id),
    product_name TEXT,
    purpose TEXT,
    users TEXT,
    publication_status TEXT,
    subject_area TEXT,
    statistics_area TEXT,
    periodicity TEXT
);

CREATE TABLE hvd_group (
    group_id INTEGER PRIMARY KEY,
    dataset_label TEXT,
    row_text TEXT
);

CREATE TABLE hvd_link (
    group_id INTEGER NOT NULL REFERENCES hvd_group (group_id),
    ordinal INTEGER NOT NULL,
    anchor_text TEXT, url TEXT, target_kind TEXT, context_text TEXT,
    product_code TEXT,               -- from a START__<SUBJ>__<CODE> PxWeb path; the output keeps only such links
    subject_code TEXT,
    PRIMARY KEY (group_id, ordinal)
);

CREATE TABLE changes_report (
    url_sha TEXT PRIMARY KEY,
    url TEXT, link_label TEXT, title TEXT, document_date_text TEXT,
    coverage_period_text TEXT, page_count INTEGER, partial_sample INTEGER
);

CREATE TABLE changes_report_note (
    url_sha TEXT NOT NULL, ordinal INTEGER NOT NULL, text TEXT,
    PRIMARY KEY (url_sha, ordinal)
);

CREATE TABLE changes_report_page (
    url_sha TEXT NOT NULL, page INTEGER NOT NULL, text TEXT,
    PRIMARY KEY (url_sha, page)
);

CREATE TABLE change_notice (
    notice_id INTEGER PRIMARY KEY,
    report_sha TEXT NOT NULL REFERENCES changes_report (url_sha),
    row_ordinal INTEGER NOT NULL,
    authority TEXT,
    agency_id INTEGER REFERENCES agency (agency_id),
    product_code TEXT,
    product_name TEXT,
    description TEXT,
    timing_claim TEXT,
    effective_date TEXT,
    effective_date_text TEXT,
    interpretation_evidence TEXT,
    review_required INTEGER
);

CREATE TABLE change_notice_kind (
    notice_id INTEGER NOT NULL REFERENCES change_notice (notice_id), kind TEXT NOT NULL,
    PRIMARY KEY (notice_id, kind)
);

CREATE TABLE change_notice_page (
    notice_id INTEGER NOT NULL REFERENCES change_notice (notice_id), page INTEGER NOT NULL,
    PRIMARY KEY (notice_id, page)
);

CREATE TABLE change_notice_related_product (
    notice_id INTEGER NOT NULL REFERENCES change_notice (notice_id), product_code TEXT NOT NULL,
    PRIMARY KEY (notice_id, product_code)
);

-- Diagnostics ------------------------------------------------------------

CREATE TABLE link_issues (
    table_name TEXT NOT NULL,
    column_name TEXT NOT NULL,
    raw_value TEXT,
    issue TEXT NOT NULL,             -- unknown_product | unknown_agency | ambiguous_agency | inferred | malformed_code | conflicting_claim | no_registry_match
    row_count INTEGER NOT NULL,
    detail TEXT
);
