"""Coordinate source adapters and publish independently validated results."""

from collections.abc import Mapping
from pathlib import Path

from scb_extract.core import ExtractionContext, SourceAdapter, SourceResult
from scb_extract.output import publish

SOURCE_IDS = (
    "products",
    "subjects",
    "official_agencies",
    "european_agencies",
    "official_products",
    "hvd",
    "changes",
)
# Kept runnable on explicit request (--source agency_registry) but excluded from
# "all" and the scheduled workflow. Rows that link to a statistics agency are kept
# in data/reference/agency_registry_linked.json.
DISABLED_SOURCE_IDS = ("agency_registry",)


def adapters() -> dict[str, SourceAdapter]:
    # Lazy imports permit shared infrastructure tests before source implementations.
    from scb_extract.sources.agencies import ADAPTERS as agencies
    from scb_extract.sources.downloads import ADAPTERS as downloads
    from scb_extract.sources.products import ADAPTERS as products

    return {**products, **agencies, **downloads}


def run_sources(
    context: ExtractionContext,
    sources: list[str],
    output_dir: Path,
    *,
    registry: Mapping[str, SourceAdapter] | None = None,
) -> dict:
    registry = registry if registry is not None else adapters()
    results = {}
    for source in sources:
        print(f"Extracting {source}", flush=True)
        try:
            result = registry[source].collect(context)
            if result.source != source:
                raise ValueError(
                    f"Adapter returned source {result.source!r} instead of {source!r}"
                )
        except Exception as exc:  # noqa: BLE001 - isolate source failures at the run boundary
            result = SourceResult(
                source=source,
                discovery_complete=False,
                failures={"index": f"{type(exc).__name__}: {exc}"},
            )
        results[source] = publish(result, output_dir)
        fresh = sum(r["status"] == "fresh" for r in results[source]["records"].values())
        print(
            f"{source}: {fresh} fresh, complete={results[source]['complete']}",
            flush=True,
        )
    return {
        "complete": all(result["complete"] for result in results.values()),
        "sources": results,
    }
