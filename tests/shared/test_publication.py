import json

from pydantic import BaseModel

from scb_extract.core import SourceResult
from scb_extract.output import publish


class Document(BaseModel):
    title: str
    partial_sample: bool = False


def test_invalid_model_retains_last_good_and_publishes_siblings(tmp_path):
    publish(
        SourceResult(source="sample", documents={"one": Document(title="old")}),
        tmp_path,
    )
    result = SourceResult(
        source="sample",
        documents={
            "one": Document.model_construct(title=None),
            "two": Document(title="new"),
        },
    )
    manifest = publish(result, tmp_path)
    assert not manifest["complete"]
    assert manifest["records"]["one"]["status"] == "retained"
    assert json.loads((tmp_path / "sample/one.json").read_text())["title"] == "old"
    assert manifest["records"]["two"]["status"] == "fresh"


def test_partial_document_is_not_published_as_complete(tmp_path):
    result = SourceResult(
        source="sample",
        documents={"one": Document(title="partial", partial_sample=True)},
    )
    manifest = publish(result, tmp_path)
    assert not manifest["complete"]
    assert not (tmp_path / "sample/one.json").exists()


def test_empty_adapter_result_is_not_success(tmp_path):
    assert not publish(SourceResult(source="sample"), tmp_path)["complete"]
