"""Output contracts for SCB workbook, HVD and change reports.

Use ``model_json_schema(by_alias=True)`` for generated JSON Schema. Nullable
fields are required so missing evidence is explicit in structured extraction.
"""

from datetime import date
from typing import Literal

from pydantic import Field

from scb_extract.models.common import Provenance, SourceModel


class OfficialProductRow(SourceModel):
    """Aliases match the actual workbook headers, including 'Anvandare'."""

    responsible_authority: str | None = Field(alias="Statistikansvarig myndighet")
    product_code: str = Field(alias="Produktkod", pattern=r"^[A-Z]{2}[0-9]{4}$")
    product_name: str | None = Field(alias="Produktnamn")
    purpose: str | None = Field(alias="Syfte")
    users: str | None = Field(alias="Anvandare")
    publication_status: str | None = Field(alias="Publiceringsstatus")
    subject_area: str | None = Field(alias="Ämnesområde")
    statistics_area: str | None = Field(alias="Statistikområde")
    periodicity: str | None = Field(alias="Periodicitet")


class OfficialProductWorkbook(SourceModel):
    provenance: Provenance
    sheet: str
    headers: list[str]
    total_source_rows: int = Field(ge=0)
    partial_sample: bool
    rows: list[OfficialProductRow]


class HvdLink(SourceModel):
    anchor_text: str
    url: str
    target_kind: Literal["pxweb_table", "saved_query", "other"]
    context_text: str | None
    subject_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    product_code: str | None = Field(
        default=None,
        pattern=r"^[A-Z]{2}[0-9]{4}$",
        description="From a PxWeb START__<subject>__<code> path segment; saved queries have none.",
    )


class HvdGroup(SourceModel):
    """Do not deduplicate links: repeated URLs may have different contexts."""

    dataset_label: str
    row_text: str
    links: list[HvdLink]


class HvdCollection(SourceModel):
    """Published output keeps only links that yield a product code (see product_links_only)."""

    provenance: Provenance
    title: str
    headers: list[str]
    total_source_groups: int = Field(ge=0)
    total_source_links: int = Field(default=0, ge=0)
    product_links_only: bool = False
    partial_sample: bool
    groups: list[HvdGroup]


class ChangeNotice(SourceModel):
    """A statement in a report is evidence of a claim, not legal enactment."""

    authority: str
    product_code: str | None
    product_name: str | None
    description: str
    pages: list[int] = Field(min_length=1)
    row_ordinal: int = Field(ge=1)
    change_kinds: list[
        Literal[
            "new_product",
            "name",
            "content",
            "periodicity",
            "inactive",
            "closed",
            "status",
            "merge",
            "other",
        ]
    ]
    timing_claim: Literal["source_claimed_effective", "planned", "mixed", "unclear"]
    effective_date: date | None
    effective_date_text: str | None
    related_product_codes: list[str]
    interpretation_evidence: str | None
    review_required: bool


class ChangesReport(SourceModel):
    provenance: Provenance
    link_label: str
    title: str
    document_date_text: str | None
    coverage_period_text: str
    page_count: int = Field(ge=1)
    document_notes: list[str]
    partial_sample: bool
    notices: list[ChangeNotice]
    page_texts: dict[int, str] = Field(default_factory=dict)
