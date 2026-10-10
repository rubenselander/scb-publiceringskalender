"""Output contracts for agency lists and the (disabled) agency register.

Use ``model_json_schema()`` on a document model when a JSON Schema is needed.
Source strings retain spelling, punctuation, casing, blank cells, and identifiers.
Optional fields distinguish omission (not extracted) from explicit null (unknown).
Use ``model_dump(exclude_unset=True)`` to retain that distinction.
"""

from typing import Literal

from pydantic import Field

from scb_extract.models.common import Provenance, SourceModel


class Subject(SourceModel):
    """A bold subject label and its following ordinary-text statistical areas."""

    subject_name: str
    statistical_areas: list[str]


class SourceLink(SourceModel):
    """Visible link text and its resolved href in a source statement."""

    text: str
    url: str


class SourceStatement(SourceModel):
    """Scope or responsibility prose, without inferred per-authority roles."""

    text: str
    links: list[SourceLink]


class OfficialAgency(SourceModel):
    """One h3 authority section in SCB's official-statistics responsibility page."""

    agency_name: str
    statistics_link_text: str
    statistics_url: str = Field(
        description="Absolute resolved href, without redirect normalization."
    )
    subjects: list[Subject]


class OfficialAgenciesDocument(SourceModel):
    """Ordered authority sections, retaining the source's bold hierarchy."""

    provenance: Provenance
    page_title: str
    scope_statements: list[SourceStatement]
    stated_agency_count: int | None = None
    coverage: Literal["complete", "partial"]
    agencies: list[OfficialAgency]


class EuropeanAgenciesDocument(SourceModel):
    """The enumerated authorities and separate central-bank statement."""

    provenance: Provenance
    page_title: str
    scope_statements: list[SourceStatement]
    stated_agency_count: int | None = None
    coverage: Literal["complete", "partial"]
    list_heading: str
    agency_names: list[str]
    central_bank_heading: str | None = None
    central_bank_statement: str | None = Field(
        default=None,
        description="Separate source prose: Riksbanken is not a twenty-second list member.",
    )
    central_bank_links: list[SourceLink]


class SourceField(SourceModel):
    """A source-labelled cell or metadata field, including explicit empty text."""

    label: str
    value: str | None = Field(
        description="Empty source text is ''; null means unavailable."
    )


class RegistryAgency(SourceModel):
    """One row of a group response or export, retaining source column labels.

    Identifier columns vary by group: Organisationsnr, CfarNr or LopNr.
    The source has blank-name rows; do not drop them or invent replacement IDs.
    """

    fields: list[SourceField]
    detail_fields: list[SourceField] | None = None
    detail_provenance: Provenance | None = None


class RegistryGroup(SourceModel):
    """One source group, preserving its actual option value and source rows."""

    group_option_value: str
    group_name: str
    provenance: Provenance
    retrieval_surface: Literal["html_table", "tsv_download", "xlsx_download"]
    table_provenance: Provenance | None = None
    source_columns: list[str]
    source_row_count: int | None = None
    coverage: Literal["complete", "partial"]
    agencies: list[RegistryAgency]


class AgencyRegistryDocument(SourceModel):
    """Group-preserving registry snapshot, never a deduplicated agency universe."""

    provenance: Provenance
    page_title: str
    coverage: Literal["complete", "partial"]
    groups: list[RegistryGroup]
