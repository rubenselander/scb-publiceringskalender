"""Load the scb_extract source families under data/extractions/<source>/
and the tracked agency-register rows under data/reference/."""

import sqlite3
from collections.abc import Callable, Iterator
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from scb_extract.db.common import (
    Documents,
    Status,
    canon,
    cell,
    insert,
    is_document_url,
    metaplus_code,
    read_json,
)
from scb_extract.models.agencies_law import (
    EuropeanAgenciesDocument,
    OfficialAgenciesDocument,
)
from scb_extract.models.common import PageIndex, Provenance
from scb_extract.models.downloads_collections import (
    ChangesReport,
    HvdCollection,
    OfficialProductWorkbook,
)
from scb_extract.models.products_subjects import (
    OfficialSubjects,
    ProductPage,
    SubjectPage,
)

REGISTRY_FILE = "reference/agency_registry_linked.json"
REGISTRY_IDS = {"Organisationsnr": "org_nr", "CfarNr": "cfar_nr", "LopNr": "lop_nr", "Namn": "name"}


def iso(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


class Loader:
    def __init__(self, con: sqlite3.Connection, data_dir: Path, status: Status, documents: Documents):
        self.con = con
        self.data_dir = data_dir
        self.status = status
        self.documents = documents

    # -- shared ------------------------------------------------------------

    def manifest(self, source: str) -> dict | None:
        path = self.data_dir / "extractions" / source / "manifest.json"
        return read_json(path) if path.exists() else None

    def documents_of(
        self, source: str, model_for: Callable[[str], type[BaseModel]]
    ) -> Iterator[tuple[str, str, BaseModel]]:
        """Yield (key, source_file, validated model) for every manifest record with a file."""
        manifest = self.manifest(source)
        if manifest is None:
            self.status.add("extractions", source, "manifest", "missing")
            return
        records = manifest.get("records", {})
        for key in sorted(records):
            record = records[key]
            self.status.add(
                "extractions", source, key, record.get("status", "unknown"), record.get("error"),
                manifest.get("complete"), manifest.get("discovery_complete"),
            )
            path = self.data_dir / "extractions" / source / f"{key}.json"
            if not path.exists():
                continue
            source_file = f"extractions/{source}/{key}.json"
            try:
                document = model_for(key).model_validate_json(path.read_bytes())
            except ValidationError as exc:
                self.status.add(
                    "extractions", source, key, "invalid", str(exc).splitlines()[0],
                    manifest.get("complete"), manifest.get("discovery_complete"),
                )
                continue
            yield key, source_file, document

    def provenance(
        self, source: str, key: str, source_file: str, value: Provenance | None, part: str = ""
    ) -> None:
        if value is None:
            return
        self.con.execute(
            "INSERT OR REPLACE INTO provenance VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                source, key, part, source_file, value.requested_url, value.response_url,
                value.canonical_url, iso(value.fetched_at), value.http_status,
                value.content_type, value.content_sha256,
            ),
        )

    def document_link(self, url: str, origin: str, product_code: str | None, **fields) -> None:
        self.documents.add(url, origin, **fields)
        self.documents.claim(url, product_code, origin)
        self.documents.claim(url, metaplus_code(url), "metaplus_query")

    def load_all(self) -> None:
        self.official_products()
        self.official_agencies()
        self.european_agencies()
        self.subjects()
        self.products()
        self.hvd()
        self.changes()

    # -- products, subjects, documentation ---------------------------------

    def products(self) -> None:
        for key, source_file, page in self.documents_of("products", lambda _: ProductPage):
            code = page.product_code
            self.provenance("products", key, source_file, page.provenance)
            agency = page.responsible_agency
            self.con.execute(
                "INSERT INTO product_page VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?)",
                (
                    key, code, page.product_code_source, page.title, page.short_address, page.summary,
                    page.official_statistics_marker, page.next_publication_text,
                    page.next_publication_date, agency.label if agency else None,
                    agency.link.url if agency and agency.link else None,
                    page.provenance.requested_url, canon(page.provenance.canonical_url),
                    iso(page.provenance.fetched_at),
                ),
            )
            insert(
                self.con, "product_page_notice", ["page_key", "ordinal", "text"],
                ({"page_key": key, "ordinal": n, "text": text} for n, text in enumerate(page.notices, 1)),
            )
            insert(
                self.con, "product_page_tag", ["page_key", "ordinal", "text", "href", "url"],
                ({"page_key": key, "ordinal": n, **tag.model_dump()} for n, tag in enumerate(page.tags, 1)),
            )
            for ordinal, section in enumerate(page.sections, 1):
                heading = " > ".join(h.text for h in section.heading_path)
                section_id = self.con.execute(
                    "INSERT INTO product_page_section (page_key, ordinal, heading_path, "
                    "heading_text, paragraphs, table_headers, table_rows) VALUES (?,?,?,?,?,?,?)",
                    (
                        key, ordinal, cell([h.model_dump() for h in section.heading_path]), heading,
                        cell(section.paragraphs), cell(section.table_headers), cell(section.table_rows),
                    ),
                ).lastrowid
                insert(
                    self.con, "product_page_link", ["section_id", "ordinal", "text", "href", "url"],
                    ({"section_id": section_id, "ordinal": n, **link.model_dump()}
                     for n, link in enumerate(section.links, 1)),
                )
                if "dokumentation" not in heading.casefold():
                    continue
                for link in section.links:
                    if is_document_url(link.url):
                        self.document_link(
                            link.url, "product_page", code, title=link.text, heading=heading,
                            source_url=page.provenance.requested_url,
                        )

    def subjects(self) -> None:
        index = None
        pages: dict[str, tuple[str, SubjectPage]] = {}
        model_for = lambda key: OfficialSubjects if key == "index" else SubjectPage
        for key, source_file, document in self.documents_of("subjects", model_for):
            if key == "index":
                index = (source_file, document)
                self.provenance("subjects", key, source_file, document.provenance)
            else:
                pages[document.code] = (source_file, document)
        if index:
            for subject in index[1].subjects:
                pages.setdefault(subject.code, (index[0], subject))
        for code in sorted(pages):
            source_file, subject = pages[code]
            self.provenance("subjects", code, source_file, subject.provenance)
            self.con.execute(
                "INSERT INTO subject VALUES (?,?,?,?,?)",
                (code, subject.title, subject.index_link.text, subject.index_link.url, source_file),
            )
            for area_ordinal, area in enumerate(subject.areas, 1):
                area_id = self.con.execute(
                    "INSERT INTO statistical_area (subject_code, ordinal, label, source_anchor, "
                    "source_url) VALUES (?,?,?,?,?)",
                    (code, area_ordinal, area.label, area.source_anchor, area.source_url),
                ).lastrowid
                for ordinal, card in enumerate(area.products, 1):
                    self.con.execute(
                        "INSERT INTO subject_product_card (subject_code, area_id, ordinal, link_text, "
                        "link_url, link_canonical_url, product_code, product_code_origin, summary, "
                        "responsible_agency_text, tags_text) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            code, area_id, ordinal, card.link.text, card.link.url, canon(card.link.url),
                            card.product_code, "source" if card.product_code else None, card.summary,
                            card.responsible_agency_text, card.tags_text,
                        ),
                    )

    # -- agencies and the reference registry rows ---------------------------

    def scope_statements(self, source: str, statements) -> None:
        insert(
            self.con, "scope_statement", ["source", "ordinal", "text", "links"],
            (
                {"source": source, "ordinal": n, "text": s.text, "links": [link.model_dump() for link in s.links]}
                for n, s in enumerate(statements, 1)
            ),
        )

    def official_agencies(self) -> None:
        for key, source_file, document in self.documents_of(
            "official_agencies", lambda _: OfficialAgenciesDocument
        ):
            self.provenance("official_agencies", key, source_file, document.provenance)
            self.scope_statements("official_agencies", document.scope_statements)
            for ordinal, agency in enumerate(document.agencies, 1):
                self.con.execute(
                    "INSERT INTO official_agency VALUES (?,?,NULL,?,?)",
                    (ordinal, agency.agency_name, agency.statistics_link_text, agency.statistics_url),
                )
                for subject_ordinal, subject in enumerate(agency.subjects, 1):
                    areas = subject.statistical_areas or [None]
                    for area_ordinal, area in enumerate(areas, 1):
                        self.con.execute(
                            "INSERT INTO official_agency_subject VALUES (?,?,?,?,?)",
                            (ordinal, subject_ordinal, area_ordinal, subject.subject_name, area),
                        )

    def european_agencies(self) -> None:
        for key, source_file, document in self.documents_of(
            "european_agencies", lambda _: EuropeanAgenciesDocument
        ):
            self.provenance("european_agencies", key, source_file, document.provenance)
            self.scope_statements("european_agencies", document.scope_statements)
            insert(
                self.con, "european_agency", ["ordinal", "name"],
                ({"ordinal": n, "name": name} for n, name in enumerate(document.agency_names, 1)),
            )
            self.con.execute(
                "INSERT INTO european_central_bank VALUES (?,?,?)",
                (
                    document.central_bank_heading, document.central_bank_statement,
                    cell([link.model_dump() for link in document.central_bank_links]),
                ),
            )

    def agency_registry(self) -> None:
        """Register rows that link to a statistics agency, kept by hand while the extraction is disabled."""
        path = self.data_dir / REGISTRY_FILE
        if not path.exists():
            self.status.add("reference", "agency_registry", REGISTRY_FILE, "missing")
            return
        values = read_json(path)
        self.status.add("reference", "agency_registry", REGISTRY_FILE, "fresh")
        for ordinal, record in enumerate(values.get("records", []), 1):
            fields = record.get("fields", [])
            lifted: dict[str, str | None] = {}
            for field in fields:
                column = REGISTRY_IDS.get(field.get("label"))
                if column and column not in lifted:
                    lifted[column] = field.get("value") or None
            row_id = self.con.execute(
                "INSERT INTO registry_agency (group_option_value, group_name, listed_agency, ordinal, "
                "org_nr, cfar_nr, lop_nr, name) VALUES (?,?,?,?,?,?,?,?)",
                (
                    record.get("group_option_value"), record.get("group_name"), record.get("agency"),
                    ordinal, lifted.get("org_nr"), lifted.get("cfar_nr"), lifted.get("lop_nr"),
                    lifted.get("name"),
                ),
            ).lastrowid
            self.con.executemany(
                "INSERT INTO registry_agency_field VALUES (?,?,?,?)",
                [(row_id, n, f.get("label"), f.get("value")) for n, f in enumerate(fields, 1)],
            )

    # -- workbook, HVD, changes ---------------------------------------------

    def official_products(self) -> None:
        for key, source_file, document in self.documents_of(
            "official_products", lambda _: OfficialProductWorkbook
        ):
            self.provenance("official_products", key, source_file, document.provenance)
            for ordinal, row in enumerate(document.rows, 1):
                strip = lambda value: value.strip() if value else value
                self.con.execute(
                    "INSERT INTO official_product_row VALUES (?,?,?,NULL,?,?,?,?,?,?,?)",
                    (
                        ordinal, row.product_code, strip(row.responsible_authority),
                        strip(row.product_name), row.purpose, row.users, strip(row.publication_status),
                        strip(row.subject_area), strip(row.statistics_area), strip(row.periodicity),
                    ),
                )
                if row.product_name:
                    self.con.execute(
                        "INSERT OR IGNORE INTO product_name_alias VALUES (?,?,?)",
                        (row.product_code, row.product_name, "workbook"),
                    )

    def hvd(self) -> None:
        for key, source_file, document in self.documents_of("hvd", lambda _: HvdCollection):
            self.provenance("hvd", key, source_file, document.provenance)
            for group_id, group in enumerate(document.groups, 1):
                self.con.execute(
                    "INSERT INTO hvd_group VALUES (?,?,?)", (group_id, group.dataset_label, group.row_text)
                )
                for ordinal, link in enumerate(group.links, 1):
                    self.con.execute(
                        "INSERT INTO hvd_link VALUES (?,?,?,?,?,?,?,?)",
                        (
                            group_id, ordinal, link.anchor_text, link.url, link.target_kind,
                            link.context_text, link.product_code, link.subject_code,
                        ),
                    )

    def changes(self) -> None:
        urls: dict[str, str] = {}
        model_for = lambda key: PageIndex if key == "index" else ChangesReport
        reports = []
        for key, source_file, document in self.documents_of("changes", model_for):
            self.provenance("changes", key, source_file, document.provenance)
            if key == "index":
                urls = {entry.key: entry.url for entry in document.entries}
            else:
                reports.append((key, document))
        for key, report in reports:
            self.con.execute(
                "INSERT INTO changes_report VALUES (?,?,?,?,?,?,?,?)",
                (
                    key, urls.get(key, report.provenance.requested_url), report.link_label, report.title,
                    report.document_date_text, report.coverage_period_text, report.page_count,
                    int(report.partial_sample),
                ),
            )
            self.con.executemany(
                "INSERT INTO changes_report_note VALUES (?,?,?)",
                [(key, n, text) for n, text in enumerate(report.document_notes, 1)],
            )
            self.con.executemany(
                "INSERT INTO changes_report_page VALUES (?,?,?)",
                [(key, page, text) for page, text in sorted(report.page_texts.items())],
            )
            for notice in report.notices:
                notice_id = self.con.execute(
                    "INSERT INTO change_notice (report_sha, row_ordinal, authority, product_code, "
                    "product_name, description, timing_claim, effective_date, effective_date_text, "
                    "interpretation_evidence, review_required) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        key, notice.row_ordinal, notice.authority, notice.product_code,
                        notice.product_name, notice.description, notice.timing_claim,
                        iso(notice.effective_date), notice.effective_date_text,
                        notice.interpretation_evidence, int(notice.review_required),
                    ),
                ).lastrowid
                self.con.executemany(
                    "INSERT OR IGNORE INTO change_notice_kind VALUES (?,?)",
                    [(notice_id, kind) for kind in notice.change_kinds],
                )
                self.con.executemany(
                    "INSERT OR IGNORE INTO change_notice_page VALUES (?,?)",
                    [(notice_id, page) for page in notice.pages],
                )
                self.con.executemany(
                    "INSERT OR IGNORE INTO change_notice_related_product VALUES (?,?)",
                    [(notice_id, code) for code in notice.related_product_codes],
                )


def load_extractions(con: sqlite3.Connection, data_dir: Path, status: Status, documents: Documents) -> Loader:
    loader = Loader(con, data_dir, status, documents)
    loader.agency_registry()
    if not (data_dir / "extractions").exists():
        status.add("extractions", "extractions", "directory", "missing")
        return loader
    loader.load_all()
    return loader
