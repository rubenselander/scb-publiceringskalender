import json
from pathlib import Path

import httpx
import pytest

from scb_extract.core import ExtractionContext, FetchRequest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_accidental_network(monkeypatch, request):
    """Ordinary tests must use snapshots or explicit MockTransport responses."""
    if request.node.get_closest_marker("live"):
        return

    def reject(*args, **kwargs):
        raise AssertionError("Network disabled in offline tests")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject)


@pytest.fixture
def snapshot():
    aliases = json.loads((FIXTURES / "aliases.json").read_text(encoding="utf-8"))
    with ExtractionContext(FIXTURES / "raw", offline=True) as context:
        yield lambda name: context.fetch(FetchRequest(**aliases[name]))
