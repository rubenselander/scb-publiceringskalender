"""Research drafts for SCB products, subject hierarchy, and documentation.

These models describe page snapshots, not a production extractor. Generate the
LLM contract with the root model's model_json_schema(). Displayed source text
is preserved; inferred identifiers must be explicitly attributed.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SourceModel(BaseModel):
    """Reject unexpected keys without discarding source wording."""

    model_config = ConfigDict(extra="forbid")


class Provenance(SourceModel):
    """Identify the actual response, independently of its canonical URL."""

    requested_url: str
    response_url: str
    canonical_url: str | None
    fetched_at: datetime
    http_status: int = Field(ge=100, le=599)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_selector: str
    capture_scope: Literal["complete_page", "research_sample"]
    warnings: list[str] = Field(default_factory=list)


class SourceLink(SourceModel):
    """Preserve href as authored and the resolved absolute URL separately."""

    text: str
    href: str
    url: str


class Heading(SourceModel):
    """Ordered source heading, including headings styled without h1-h6."""

    text: str
    level: int | None = Field(ge=1, le=6)
    anchor: str | None


class ContentGroup(SourceModel):
    """Keep section/list grouping, text, and table cell order without guessing types.

    heading_path records the actual source container/heading hierarchy. Adjacent
    same-level headings must not be nested solely from their numeric level.
    """

    heading_path: list[Heading]
    paragraphs: list[str] = Field(default_factory=list)
    links: list[SourceLink] = Field(default_factory=list)
    table_headers: list[str] = Field(default_factory=list)
    table_rows: list[list[str]] = Field(default_factory=list)


class ResponsibleAgency(SourceModel):
    """Only source-observed agency information; absence does not imply SCB."""

    label: str
    link: SourceLink | None


class ProductPage(SourceModel):
    """SCB full or externally maintained product landing page."""

    record_type: Literal["product_page"] = "product_page"
    provenance: Provenance
    product_code: str | None = Field(pattern=r"^[A-Z]{2}[0-9]{4}$")
    product_code_source: Literal[
        "calendar", "page_short_address", "page_link", "unknown"
    ]
    title: str
    short_address: str | None
    summary: str | None
    official_statistics_marker: str | None
    next_publication_text: str | None
    next_publication_date: str | None = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    responsible_agency: ResponsibleAgency | None
    notices: list[str] = Field(default_factory=list)
    tags: list[SourceLink] = Field(default_factory=list)
    sections: list[ContentGroup] = Field(default_factory=list)


class ProductCard(SourceModel):
    """Product mention within a subject accordion; retain its local description."""

    link: SourceLink
    product_code: str | None = Field(pattern=r"^[A-Z]{2}[0-9]{4}$")
    product_code_source: Literal["calendar_url_match", "page", "unknown"]
    summary: str | None
    responsible_agency_text: str | None
    tags_text: str | None


class StatisticalArea(SourceModel):
    """Source accordion group, whose label need not have a public code or URL."""

    label: str
    source_anchor: str | None
    source_url: str | None
    products: list[ProductCard] = Field(default_factory=list)


class SubjectPage(SourceModel):
    """A subject discovered from SCB's official-statistics subject index."""

    code: str = Field(pattern=r"^[A-Z]{2}$")
    index_link: SourceLink
    provenance: Provenance | None
    title: str | None
    areas: list[StatisticalArea] = Field(default_factory=list)


class OfficialSubjects(SourceModel):
    """Index plus subject pages; completeness is explicit and bounded."""

    record_type: Literal["official_subjects"] = "official_subjects"
    provenance: Provenance
    title: str
    introduction: str | None
    subjects: list[SubjectPage]
    traversal_complete: bool
    unfetched_subject_urls: list[str] = Field(default_factory=list)


class DocumentationEntry(SourceModel):
    """A-Z entry links often point to a product's #_Dokumentation fragment."""

    letter: str
    link: SourceLink


class DocumentationPage(SourceModel):
    """Observed documentation section of a statistical product page.

    has_documentation_section=False is a successful inspected-page observation,
    not a fetch failure. An externally maintained product can have no section.
    """

    provenance: Provenance
    title: str
    product_code: str | None = Field(pattern=r"^[A-Z]{2}[0-9]{4}$")
    has_documentation_section: bool
    groups: list[ContentGroup] = Field(default_factory=list)


class DocumentationIndex(SourceModel):
    """A-Z index and bounded section captures; document contents are out of scope."""

    record_type: Literal["documentation_index"] = "documentation_index"
    provenance: Provenance
    title: str
    introduction: str | None
    entries: list[DocumentationEntry]
    pages: list[DocumentationPage] = Field(default_factory=list)
    traversal_complete: bool
    unfetched_page_urls: list[str] = Field(default_factory=list)
