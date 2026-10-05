"""Fetch, archive and replay source responses without parser coupling."""

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from scb_extract.models.common import Provenance


def canonical_url(url: str) -> str:
    """Normalize network identity without changing path/query semantics."""
    parts = urlsplit(urldefrag(url)[0])
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, "")
    )


def url_key(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()


def absolute_url(base: str, href: str) -> str:
    return urljoin(base, href)


class FetchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    url: str
    method: Literal["GET", "POST"] = "GET"
    params: dict[str, str] = Field(default_factory=dict)
    json_body: dict[str, str | None] | None = None

    @property
    def key(self) -> str:
        values = self.model_dump()
        values["url"] = canonical_url(self.url)
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


class FetchSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: FetchRequest
    final_url: str
    status: int
    content_type: str
    fetched_at: datetime
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    encoding: str = "utf-8"
    content: bytes = Field(exclude=True)

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding, errors="replace")

    def provenance(
        self, *, canonical: str | None = None, selector: str = ""
    ) -> Provenance:
        return Provenance(
            requested_url=self.request.url,
            response_url=self.final_url,
            canonical_url=canonical,
            fetched_at=self.fetched_at,
            http_status=self.status,
            content_type=self.content_type,
            content_sha256=self.sha256,
            request_method=self.request.method,
            request_parameters=self.request.params,
            request_json_body=self.request.json_body,
            source_selector=selector,
        )


class SourceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    documents: dict[str, BaseModel] = Field(default_factory=dict)
    discovered: list[str] = Field(default_factory=list)
    failures: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    discovery_complete: bool = True


class SourceAdapter(Protocol):
    def collect(self, context: "ExtractionContext") -> SourceResult: ...


def atomic_json(path: Path, value: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


class ExtractionContext:
    """A sequential, rate-limited client or a strict network-free replay context.

    Every request has independent metadata and a content-addressed response body.
    Offline mode never falls back to a network request. Existing raw bundles can
    be reused by replay; live runs always refresh each distinct request once.
    """

    def __init__(
        self,
        raw_dir: Path | str,
        *,
        offline: bool = False,
        transport: httpx.BaseTransport | None = None,
        retry_delay: float = 1,
        timeout: float = 45,
        interval: float = 0.15,
        calendar_path: Path | str = "data/calendar.json",
    ):
        self.raw_dir = Path(raw_dir)
        self.offline = offline
        self.calendar_path = Path(calendar_path)
        self.retry_delay = retry_delay
        self.interval = 0 if transport or offline else interval
        self.cache: dict[str, FetchSnapshot] = {}
        self._archive_loaded = False
        self.client = httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "scb-publiceringskalender/0.1 (+https://github.com/rubenselander/scb-publiceringskalender)"
            },
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def fetch(self, request: FetchRequest) -> FetchSnapshot:
        if request.key in self.cache:
            return self.cache[request.key]
        metadata = self.raw_dir / "requests" / f"{request.key}.json"
        if self.offline:
            if not metadata.exists():
                raise OSError(f"No raw snapshot for {request.method} {request.url}")
            values = json.loads(metadata.read_text(encoding="utf-8"))
            snapshot = FetchSnapshot(**values, content=b"")
            if snapshot.request.key != request.key:
                raise OSError(f"Snapshot request identity mismatch: {metadata}")
            content = (self.raw_dir / "bodies" / snapshot.sha256).read_bytes()
            if hashlib.sha256(content).hexdigest() != snapshot.sha256:
                raise OSError(f"Corrupt snapshot: {metadata}")
            snapshot.content = content
            if snapshot.status >= 400 or not snapshot.content.strip():
                raise OSError(
                    f"Recorded unsuccessful response {snapshot.status}: {request.url}"
                )
        else:
            snapshot = self._download(request, metadata)
        self.cache[request.key] = snapshot
        return snapshot

    def available_snapshots(self):
        """Expose validated archived responses for canonical-URL alias discovery.

        This makes subset replay independent of earlier source execution. Failed
        HTTP snapshots are excluded; corrupt successful snapshots remain errors.
        """
        if self.offline and not self._archive_loaded:
            for path in sorted((self.raw_dir / "requests").glob("*.json")):
                values = json.loads(path.read_text(encoding="utf-8"))
                if 200 <= values["status"] < 300:
                    request = FetchRequest.model_validate(values["request"])
                    if request.key != path.stem:
                        raise OSError(f"Snapshot request identity mismatch: {path}")
                    self.fetch(request)
            self._archive_loaded = True
        return tuple(self.cache.values())

    def _download(self, request: FetchRequest, metadata: Path) -> FetchSnapshot:
        error = ""
        for attempt in range(3):
            time.sleep(
                self.retry_delay * 2 ** (attempt - 1) if attempt else self.interval
            )
            try:
                response = self.client.request(
                    request.method,
                    canonical_url(request.url),
                    params=request.params or None,
                    json=request.json_body,
                )
                snapshot = FetchSnapshot(
                    request=request,
                    final_url=str(response.url),
                    status=response.status_code,
                    content_type=response.headers.get("content-type", ""),
                    fetched_at=datetime.now(UTC),
                    sha256=hashlib.sha256(response.content).hexdigest(),
                    encoding=response.encoding or "utf-8",
                    content=response.content,
                )
                body_path = self.raw_dir / "bodies" / snapshot.sha256
                body_path.parent.mkdir(parents=True, exist_ok=True)
                if not body_path.exists():
                    body_path.write_bytes(snapshot.content)
                atomic_json(metadata, snapshot.model_dump(mode="json"))
                if response.is_success and response.content.strip():
                    return snapshot
                error = f"HTTP {response.status_code}, {len(response.content)} bytes"
                if 400 <= response.status_code < 500 and response.status_code not in (
                    408,
                    429,
                ):
                    break
            except httpx.HTTPError as exc:
                error = str(exc)
        raise OSError(f"{request.method} {request.url}: {error}")
