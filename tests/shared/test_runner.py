import json

import httpx
from pydantic import BaseModel

from scb_extract.core import ExtractionContext, FetchRequest, SourceResult
from scb_extract.runner import run_sources


class Document(BaseModel):
    text: str


class Adapter:
    def collect(self, context):
        snapshot = context.fetch(FetchRequest(url="https://example.org/"))
        return SourceResult(
            source="working", documents={"index": Document(text=snapshot.text)}
        )


class BrokenAdapter:
    def collect(self, context):
        raise ValueError("missing source selector")


def test_sources_fail_independently(tmp_path):
    with ExtractionContext(
        tmp_path / "raw",
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text="good")),
    ) as context:
        report = run_sources(
            context,
            ["broken", "working"],
            tmp_path / "out",
            registry={"broken": BrokenAdapter(), "working": Adapter()},
        )
    assert not report["complete"]
    assert report["sources"]["working"]["complete"]
    assert (tmp_path / "out/working/index.json").exists()


def test_corrupt_raw_snapshot_fails_replay(tmp_path):
    request = FetchRequest(url="https://example.org/")
    with ExtractionContext(
        tmp_path,
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text="good")),
    ) as context:
        snapshot = context.fetch(request)
    (tmp_path / "bodies" / snapshot.sha256).write_bytes(b"changed")
    with ExtractionContext(tmp_path, offline=True) as context:
        report = run_sources(
            context, ["working"], tmp_path / "out", registry={"working": Adapter()}
        )
    assert not report["complete"]
    assert "Corrupt snapshot" in json.dumps(report)
