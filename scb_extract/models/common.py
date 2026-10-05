"""Shared source evidence and index contracts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Provenance(SourceModel):
    requested_url: str
    response_url: str
    canonical_url: str | None = None
    fetched_at: datetime
    http_status: int = Field(ge=100, le=599)
    content_type: str
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_method: Literal["GET", "POST"] = "GET"
    request_parameters: dict[str, str] = Field(default_factory=dict)
    request_json_body: dict[str, str | None] | None = None
    source_selector: str = ""
    capture_scope: Literal["complete_page", "research_sample"] = "complete_page"
    warnings: list[str] = Field(default_factory=list)


class IndexEntry(SourceModel):
    key: str
    title: str
    url: str


class PageIndex(SourceModel):
    provenance: Provenance
    title: str
    entries: list[IndexEntry]
    complete: bool
