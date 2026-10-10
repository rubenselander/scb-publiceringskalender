"""Load data/calendar.jsonl, gaps.json and state.json."""

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from scb_extract.db.common import Status, insert, read_json, read_jsonl

CALENDAR_COLUMNS = [
    "entry_id", "publish_date", "product_code", "product_name", "reporting_round",
    "reference_period", "published_at", "responsible_agency", "product_url",
    "row_hash", "occurrence",
]


def row_hash(row: dict) -> str:
    text = json.dumps(row, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


def load_calendar(con: sqlite3.Connection, data_dir: Path, status: Status) -> dict:
    path = data_dir / "calendar.jsonl"
    info: dict = {}
    if not path.exists():
        status.add("calendar", "calendar", "calendar.jsonl", "missing")
        return info
    seen: Counter = Counter()
    entries, forms, publishers = [], [], []
    for entry_id, row in enumerate(read_jsonl(path), start=1):
        digest = row_hash(row)
        seen[digest] += 1
        entries.append({**row, "entry_id": entry_id, "row_hash": digest, "occurrence": seen[digest]})
        for ordinal, form in enumerate(row.get("forms") or [], start=1):
            forms.append({"entry_id": entry_id, "ordinal": ordinal, "form": form})
        # published_at joins several agencies with ", " (e.g. "Trafikanalys, Statistiska centralbyrån").
        names = [part.strip() for part in (row.get("published_at") or "").split(", ")]
        for ordinal, name in enumerate(filter(None, names), start=1):
            publishers.append({"entry_id": entry_id, "ordinal": ordinal, "name": name})
    insert(con, "calendar_entry", CALENDAR_COLUMNS, entries)
    insert(con, "calendar_entry_form", ["entry_id", "ordinal", "form"], forms)
    insert(con, "calendar_entry_publisher", ["entry_id", "ordinal", "name"], publishers)
    status.add("calendar", "calendar", "calendar.jsonl", "fresh")

    gaps = data_dir / "gaps.json"
    if gaps.exists():
        rows = [
            {"from_date": gap.get("from"), "to_date": gap.get("to"),
             "expected": gap.get("expected"), "retrieved": gap.get("retrieved")}
            for gap in read_json(gaps)
        ]
        insert(con, "calendar_gap", ["from_date", "to_date", "expected", "retrieved"], rows)
    state = data_dir / "state.json"
    if state.exists():
        info["calendar_last_completed_run"] = read_json(state).get("last_completed_run")
    unparsed = data_dir / "unparsed.jsonl"
    if unparsed.exists():
        info["calendar_unparsed_rows"] = sum(1 for _ in read_jsonl(unparsed))
    return info
