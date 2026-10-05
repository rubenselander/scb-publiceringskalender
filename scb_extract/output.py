"""Validated per-document publication with explicit retained/failed coverage."""

import json
import re
from pathlib import Path

from scb_extract.core import SourceResult, atomic_json


def safe_key(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError(f"Unsafe output key: {value!r}")
    return value


def publish(result: SourceResult, output_dir: Path | str) -> dict:
    directory = Path(output_dir) / safe_key(result.source)
    manifest_path = directory / "manifest.json"
    previous = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    records = {}
    discovered = set(result.discovered) | set(result.documents) | set(result.failures)
    keys = discovered | set(previous.get("records", {}))
    for key in keys:
        safe_key(key)
        if key == "manifest":
            raise ValueError("Document key 'manifest' is reserved")
    for key in sorted(keys):
        target = directory / f"{key}.json"
        failure = result.failures.get(key)
        if key in result.documents and not failure:
            document = result.documents[key]
            try:
                values = document.model_dump(mode="json", by_alias=True)
                type(document).model_validate(values)
                if (
                    values.get("partial_sample")
                    or values.get("coverage") == "partial"
                    or values.get("traversal_complete") is False
                    or values.get("complete") is False
                ):
                    raise ValueError("Document explicitly reports incomplete coverage")
            except (ValueError, TypeError) as exc:
                failure = f"Validation failed: {exc}"
        if key in result.documents and not failure:
            atomic_json(target, values)
            status = "fresh"
        elif key not in discovered and result.discovery_complete:
            status = "no_longer_listed"
        elif target.exists():
            status = "retained"
        else:
            status = "failed"
        records[key] = {"status": status, "error": failure}
    complete = (
        bool(discovered)
        and result.discovery_complete
        and not result.failures
        and all(records[key]["status"] == "fresh" for key in discovered)
    )
    manifest = {
        "source": result.source,
        "complete": complete,
        "discovery_complete": result.discovery_complete,
        "discovered_count": len(discovered),
        "records": dict(sorted(records.items())),
        "warnings": result.warnings,
    }
    atomic_json(manifest_path, manifest)
    return manifest
