"""Run live extraction or deterministic replay of an archived raw bundle."""

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from scb_extract.core import ExtractionContext, atomic_json
from scb_extract.runner import SOURCE_IDS, run_sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    live = commands.add_parser("run")
    live.add_argument("--source", choices=("all", *SOURCE_IDS), default="all")
    live.add_argument("--raw-dir", type=Path)
    live.add_argument("--calendar-path", type=Path, default=Path("data/calendar.json"))
    replay = commands.add_parser("replay")
    replay.add_argument("--raw-dir", type=Path, required=True)
    replay.add_argument("--source", choices=("all", *SOURCE_IDS))
    for command in (live, replay):
        command.add_argument(
            "--output-dir", type=Path, default=Path("data/extractions")
        )
    args = parser.parse_args(argv)
    if args.command == "run":
        raw = args.raw_dir or Path(".extract-raw") / datetime.now(UTC).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
        if (raw / "run.json").exists():
            parser.error(
                "Raw directory already contains a run; choose a new directory or replay it."
            )
        sources = list(SOURCE_IDS) if args.source == "all" else [args.source]
        calendar = raw / "inputs/calendar.json"
        calendar.parent.mkdir(parents=True, exist_ok=True)
        if args.calendar_path.exists():
            shutil.copyfile(args.calendar_path, calendar)
        elif "products" in sources:
            parser.error(f"Calendar input does not exist: {args.calendar_path}")
        metadata = {
            "schema_version": 1,
            "started_at": datetime.now(UTC).isoformat(),
            "sources": sources,
        }
        atomic_json(raw / "run.json", metadata)
    else:
        raw = args.raw_dir
        metadata_path = raw / "run.json"
        if not metadata_path.exists():
            parser.error("Replay requires run.json from a live extraction bundle.")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        sources = metadata["sources"] if args.source in (None, "all") else [args.source]
        if not set(sources).issubset(metadata["sources"]):
            parser.error("Requested source was not included in the archived run.")
        calendar = raw / "inputs/calendar.json"
    with ExtractionContext(
        raw, offline=args.command == "replay", calendar_path=calendar
    ) as context:
        report = run_sources(context, sources, args.output_dir)
    atomic_json(args.output_dir / "run.json", {**metadata, **report})
    if args.command == "run":
        atomic_json(raw / "result.json", report)
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
