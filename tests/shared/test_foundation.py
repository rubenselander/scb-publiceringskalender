import json

import httpx
import pytest
from pydantic import BaseModel

from scb_extract.core import ExtractionContext, FetchRequest, SourceResult
from scb_extract.output import publish


class Example(BaseModel):
    title: str


def test_fetch_replay_deduplicates_post_and_preserves_bytes(tmp_path):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200, text="<h1>Statistik</h1>", headers={"content-type": "text/html"}
        )

    raw = tmp_path / "raw"
    request = FetchRequest(
        url="https://example.org/list#section",
        method="POST",
        json_body={"group": "one"},
    )
    with ExtractionContext(raw, transport=httpx.MockTransport(respond)) as context:
        first = context.fetch(request)
        assert context.fetch(request) is first
    assert len(requests) == 1
    with ExtractionContext(raw, offline=True) as context:
        replay = context.fetch(request)
        assert replay.content == first.content
        assert replay.provenance() == first.provenance()
        with pytest.raises(OSError, match="snapshot"):
            context.fetch(FetchRequest(url="https://example.org/missing"))


def test_retries_and_redirect_provenance(tmp_path):
    calls = []

    def respond(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(503)
        if request.url.path == "/old":
            return httpx.Response(302, headers={"location": "/new"})
        return httpx.Response(200, text="content")

    with ExtractionContext(
        tmp_path, transport=httpx.MockTransport(respond), retry_delay=0
    ) as context:
        snapshot = context.fetch(FetchRequest(url="https://example.org/old"))
        assert snapshot.final_url == "https://example.org/new"
        assert snapshot.request.url == "https://example.org/old"
    assert len(calls) == 3


def test_empty_and_http_error_never_succeed(tmp_path):
    for status in [200, 404]:
        with (
            ExtractionContext(
                tmp_path / str(status),
                transport=httpx.MockTransport(
                    lambda request, status=status: httpx.Response(status)
                ),
                retry_delay=0,
            ) as context,
            pytest.raises(OSError),
        ):
            context.fetch(FetchRequest(url="https://example.org/empty"))


def test_publish_preserves_failed_and_disappeared_documents(tmp_path):
    publish(
        SourceResult(
            source="sample",
            documents={"a": Example(title="old"), "b": Example(title="keep")},
            discovered=["a", "b"],
        ),
        tmp_path,
    )
    result = SourceResult(source="sample", discovered=["a"], failures={"a": "timeout"})
    manifest = publish(result, tmp_path)
    assert json.loads((tmp_path / "sample/a.json").read_text())["title"] == "old"
    assert manifest["records"]["a"]["status"] == "retained"
    assert manifest["records"]["b"]["status"] == "no_longer_listed"
    assert manifest["complete"] is False


def test_adapter_end_to_end_and_replay(tmp_path):
    def collect(context):
        snapshot = context.fetch(FetchRequest(url="https://example.org/"))
        return SourceResult(
            source="sample",
            documents={"index": Example(title=snapshot.text)},
            discovered=["index"],
        )

    raw = tmp_path / "raw"
    with ExtractionContext(
        raw,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="Title")
        ),
    ) as context:
        assert publish(collect(context), tmp_path / "first")["complete"]
    with ExtractionContext(raw, offline=True) as context:
        publish(collect(context), tmp_path / "second")
    assert (tmp_path / "first/sample/index.json").read_bytes() == (
        tmp_path / "second/sample/index.json"
    ).read_bytes()


def test_unsafe_output_key_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        publish(
            SourceResult(
                source="sample", documents={"../escape": Example(title="bad")}
            ),
            tmp_path,
        )
