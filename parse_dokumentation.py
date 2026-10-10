"""Stage 2 of 2: turn data/dokumentation/raw/ into the documentation files in data/dokumentation/.

Uses the repository’s locked uv environment; no network. Run from the repository root:  python parse_dokumentation.py

Outputs:
  dokument          (CSV + JSON Lines) one row per documentation document (kvalitetsdeklaration,
                    beskrivning av statistiken, statistikens framställning, SCBDOK, MetaPlus,
                    kvalitetsrapport) from SCB's "Kvalitet och framställning" index, SIRIS and the
                    crawled agencies, with the product code where one could be established and,
                    for SCB, the subject area and statistical area it is listed under
  produkter         (CSV + JSON Lines) one row per product code in the calendar
                    (data/calendar.jsonl): its agency, the newest document of each kind and how
                    many documents were found
  siris_dokument    (JSON Lines) the SIRIS files that are documentation, with Skolverket's own
                    official-statistics flag (sos)
  sam_dokument      (JSON Lines) every link on the crawled agency pages that looks like a
                    documentation file

A product code is taken from the file name or link text when it holds one (xx0000). Documents
without a code (Socialstyrelsen, Trafikanalys, Skolverket, ...) are matched to the responsible
agency's products by name; `product_match` says which ("code", "name" or empty).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urljoin, urlparse

from pydantic import BaseModel, ConfigDict, Field

from scb_extract.core import atomic_json

DATA = Path("data")
DOK = DATA / "dokumentation"
RAW = DOK / "raw"

CODE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2}\d{4})(?![A-Za-z0-9])")
YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
DOC_FILE = re.compile(
    r"\.(pdf|docx?|xlsx?)(\?|$)|/download/|getFile|urn\.kb\.se|metadata\.scb\.se|kvalitetsdeklaration",
    re.IGNORECASE,
)
# What kind of documentation a link is, from its file name and text. Order matters.
KINDS = (
    ("metaplus", re.compile(r"metadata\.scb\.se|metaplus", re.IGNORECASE)),
    ("scbdok", re.compile(r"scbdok|(?<![a-z])do[_-]\d{4}|_do[_.]", re.IGNORECASE)),
    (
        "statistikens framställning",
        re.compile(
            r"statistikens[ _-]?framst|(?<![a-z])staf(?![a-z])|framstallning",
            re.IGNORECASE,
        ),
    ),
    (
        "beskrivning av statistiken",
        re.compile(
            r"beskrivning[ _-]av[ _-]statistiken|(?<![a-z])bs[_-]\d{4}|_bs[_.]|_beskr|(?<![a-z])bas(?![a-z])",
            re.IGNORECASE,
        ),
    ),
    (
        "kvalitetsrapport",
        re.compile(r"kvalitetsrapport|quality[ _-]report", re.IGNORECASE),
    ),
    (
        "kvalitetsdeklaration",
        re.compile(
            r"kvalitetsdeklaration|kvalitetsdokumentation|(?<![a-z])kd(?![a-z])|quality[ _-]declaration"
            r"|(?<![a-zåäö])kvalitet(?![a-zåäö])",
            re.IGNORECASE,
        ),
    ),
)
FIELDS = (
    "source",
    "agency",
    "product_code",
    "product_name",
    "product_match",
    "doc_type",
    "year",
    "title",
    "url",
    "filetype",
    "source_url",
    "heading",
    "subject_area",
    "statistics_area",
    "document_id",
    "candidate_product_code",
    "review_required",
    "source_status",
    "provenance",
)


# Recomputed for every document in main(); never carried over from a previous export.
DERIVED = (
    "agency",
    "document_id",
    "product_name",
    "candidate_product_code",
    "review_required",
    "source_status",
)


class Document(BaseModel):
    """Validated document occurrence; inferred joins are review candidates only."""

    model_config = ConfigDict(extra="forbid")
    source: str
    agency: str | None = None
    product_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}[0-9]{4}$")
    product_name: str | None = None
    product_match: str = ""
    doc_type: str | None = None
    year: str | None = None
    title: str
    url: str
    filetype: str
    source_url: str
    heading: str
    subject_area: str | None = None
    statistics_area: str | None = None
    document_id: str
    candidate_product_code: str | None = None
    review_required: bool = False
    source_status: Literal["fresh", "retained", "legacy"] = "legacy"
    provenance: dict = Field(default_factory=dict)


def source_status(source: str, state: dict) -> str:
    family, _, agency = source.partition("/")
    if family in state.get("last_failures", []) or source in state.get(
        "last_failures", []
    ):
        return "retained"
    if not state.get("attempted_sources"):
        return "legacy"
    if family not in state["attempted_sources"]:
        return "retained"
    if (
        family == "sam"
        and state.get("selected_agencies")
        and agency not in state["selected_agencies"]
    ):
        return "retained"
    return "fresh"


def cached(name: str) -> list[dict]:
    path = DOK / f"{name}.jsonl"
    return (
        [
            json.loads(line)
            for line in path.read_text("utf-8").splitlines()
            if line.strip()
        ]
        if path.exists()
        else []
    )


def raw_files(source: str, suffix: str) -> list[Path]:
    directory = RAW / source
    membership = directory / "membership.json"
    if membership.exists():
        names = json.loads(membership.read_text("utf-8"))["files"]
        return [directory / name for name in names if name.endswith(suffix)]
    return sorted(
        p for p in directory.glob(f"*{suffix}") if p.name != "membership.json"
    )


def clean(s: str) -> str:
    return " ".join(html.unescape(s or "").split())


def filetype(url: str) -> str:
    m = re.search(r"\.(pdf|docx?|xlsx?|html?)(?:\?|$)", url, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    if "getFile" in url or "/download/" in url or "urn.kb.se" in url:
        return "pdf?"
    return "html"


def find_code(url: str, *texts: str) -> str | None:
    """A product code (xx0000) in the file name, else in the texts, else anywhere in the URL."""
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    for t in (name, *texts, unquote(url)):
        codes = {m.upper() for m in CODE.findall(t or "")}
        if codes:
            return next(iter(codes)) if len(codes) == 1 else None
    return None


def find_year(url: str, *texts: str) -> str | None:
    """The year a document is about: the last year in its file name, else in its text, else in its path."""
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    for t in (name, *texts, unquote(url)):
        years = YEAR.findall(t or "")
        if years:
            return years[-1] if t == name else years[0]
    return None


def kind(url: str, text: str, heading: str = "") -> str | None:
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    for label, pattern in KINDS:
        if pattern.search(name) or pattern.search(text):
            return label
    # SCB's index puts documents under headings; a file whose name says nothing takes the heading's kind.
    if heading:
        if "framst" in heading.lower():
            return "statistikens framställning"
        if "kvalitet" in heading.lower():
            return "kvalitetsdeklaration"
        if "mikrodata" in heading.lower():
            return "metaplus"
    return None


def write(name: str, rows: list[dict], fields: tuple, *, with_csv: bool = True) -> None:
    """Atomically replace each complete export; never truncate the old output.

    The published files (dokument, produkter) also get a CSV copy; internal
    exports are JSON Lines only, and a stale CSV copy of them is removed.
    """
    DOK.mkdir(parents=True, exist_ok=True)
    lines = "".join(
        json.dumps({k: r.get(k) for k in fields}, ensure_ascii=False) + "\n"
        for r in rows
    )
    outputs = [("jsonl", lines)]
    if with_csv:
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(fields)
        writer.writerows([_csv(r.get(k)) for k in fields] for r in rows)
        outputs.append(("csv", buffer.getvalue()))
    else:
        (DOK / f"{name}.csv").unlink(missing_ok=True)
    for suffix, text in outputs:
        target = DOK / f"{name}.{suffix}"
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(text, "utf-8", newline="\n")
        temporary.replace(target)


def _csv(v):
    if v is None:
        return ""
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return v


# ---------------------------------------------------------------- products (from the calendar)


def products() -> dict[str, dict]:
    """product code -> {name, agency} from the calendar, taking each code's most recent entry."""
    out: dict[str, dict] = {}
    path = DATA / "calendar.jsonl"
    if not path.exists():
        return out
    for line in path.read_text("utf-8").splitlines():
        r = json.loads(line)
        code = r.get("product_code")
        if code and (code not in out or r["publish_date"] >= out[code]["last"]):
            out[code] = {
                "name": r["product_name"],
                "agency": r["responsible_agency"],
                "last": r["publish_date"],
            }
    return out


def active(last_publish_date: str | None, as_of: date) -> bool:
    """A product with a publication planned or made in the last year counts as current."""
    return (
        bool(last_publish_date)
        and last_publish_date >= (as_of - timedelta(days=366)).isoformat()
    )


_STOP = {
    "statistik",
    "statistiken",
    "och",
    "för",
    "med",
    "som",
    "inom",
    "efter",
    "per",
    "samt",
    "the",
    "and",
    "kvalitetsdeklaration",
    "statistikens",
    "framställning",
    "pdf",
    "docx",
    "läs",
    "läsår",
    "läsåret",
    "år",
    "hösten",
    "våren",
    "område",
    "områdets",
    "uppgifter",
    "officiell",
    "officiella",
    "sveriges",
}


def tokens(s: str) -> set[str]:
    s = re.sub(r"[^a-zåäö0-9 ]", " ", unquote(s or "").lower())
    out = set()
    for w in s.split():
        if len(w) < 4 or w in _STOP or w.isdigit():
            continue
        out.add(w[:6])  # crude stemming: "gymnasieskolans" ~ "gymnasieskolan"
    return out


class NameMatcher:
    """Finds the product a document title refers to, among one agency's products."""

    def __init__(self, prods: dict[str, dict]):
        self.by_agency: dict[str, list[tuple[str, set[str]]]] = defaultdict(list)
        for code, p in prods.items():
            self.by_agency[p["agency"]].append((code, tokens(p["name"])))

    def match(self, agency: str, *texts: str) -> str | None:
        words = set().union(*(tokens(t) for t in texts))
        if not words:
            return None
        scored = []
        for code, ptoks in self.by_agency.get(agency, []):
            if not ptoks:
                continue
            overlap = len(words & ptoks) / len(ptoks)
            if overlap >= 0.5:
                scored.append((overlap, len(ptoks), code))
        if not scored:
            return None
        scored.sort(reverse=True)
        if len(scored) > 1 and scored[0][:2] == scored[1][:2]:
            return None  # a tie says nothing
        return scored[0][2]


# ---------------------------------------------------------------- scb


class _ScbIndex(HTMLParser):
    """The documents of one subject-area fragment of SCB's index."""

    def __init__(self):
        super().__init__()
        self.rows: list[dict] = []
        self.area = self.product = self.heading = self.sub = ""
        self._capture = None  # which field the text now belongs to
        self._text = ""
        self._href = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "h3":
            self._capture = (
                "area" if "h2-small" in (attrs.get("class") or "") else "product"
            )
            self._text = ""
        elif tag == "h4":
            self._capture = "heading" if attrs.get("id") else "sub"
            self._text = ""
        elif tag == "a" and attrs.get("href"):
            self._href, self._text, self._capture = attrs["href"], "", "link"

    def handle_data(self, data):
        if self._capture:
            self._text += data

    def handle_endtag(self, tag):
        if tag in ("h3", "h4") and self._capture:
            text = clean(self._text)
            if self._capture == "area":
                self.area, self.product, self.heading, self.sub = text, "", "", ""
            elif self._capture == "product":
                self.product, self.heading, self.sub = text, "", ""
            elif self._capture == "heading" or "mikrodata" in text.lower():
                self.heading, self.sub = text, ""
            else:
                self.sub = text
            self._capture = None
        elif tag == "a" and self._capture == "link":
            href = self._href
            url = urljoin("https://www.scb.se/", href)
            self.rows.append(
                {
                    "amne": "",
                    "statistikomrade": self.area,
                    "produkt": self.product,
                    "heading": self.heading,
                    "sub": self.sub,
                    "label": clean(self._text),
                    "url": url,
                }
            )
            self._capture = None


def parse_scb() -> list[dict] | None:
    """Rows of SCB's index, or None without a capture (the previous dokument rows are reused)."""
    rows = []
    files = raw_files("scb", ".json")
    if not files:
        # Exports written before scb_dokument was retired still carry the full SCB rows.
        return cached("scb_dokument") or None
    for path in files:
        record = json.loads(path.read_text("utf-8"))
        parser = _ScbIndex()
        parser.feed(record["html"])
        parser.close()
        for r in parser.rows:
            r["amne"] = record["amnesomrade"]
            r["source_url"] = record["url"]
            r["provenance"] = record.get("provenance", {})
            r["product_code"] = find_code(r["url"], r["label"])
            r["product_match"] = "code" if r["product_code"] else ""
            r["year"] = find_year(r["url"], r["label"])
            r["doc_type"] = kind(r["url"], r["label"], r["heading"])
            r["filetype"] = filetype(r["url"])
            r["fetched"] = record["fetched"]
            rows.append(r)
    # A file named without a code (kvalitetsdeklaration-fordon_2025.pdf) belongs to the product whose
    # block it is listed in: the code of its MetaPlus link, else the code its sibling files carry.
    blocks: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        blocks[(r["amne"], r["statistikomrade"], r["produkt"])].append(r)
    for block in blocks.values():
        codes = Counter(r["product_code"] for r in block if r["product_code"])
        code = next(iter(codes)) if len(codes) == 1 else None
        for r in block:
            if code and not r["product_code"]:
                r["product_code"], r["product_match"] = code, "block"
    return rows


# ---------------------------------------------------------------- siris

# SIRIS lists every file Skolverket publishes; only these kinds are documentation.
SIRIS_DOC_TYPES = (
    "kvalitetsdeklaration",
    "statistikens framställning",
    "beskrivning av statistiken",
)

SIRIS_FIELDS = (
    "verkform",
    "omrade",
    "lasar",
    "doc_id",
    "typ",
    "filetype",
    "sos",
    "tabell_nr",
    "huvudrubrik",
    "rubrik",
    "titel",
    "url",
    "doc_type",
    "year",
    "fetched",
    "source_url",
    "provenance",
)


def parse_siris() -> list[dict]:
    rows = []
    files = raw_files("siris", ".jsonl")
    if not files:
        return cached("siris_dokument")
    for path in files:
        for line in path.read_text("utf-8").splitlines():
            call = json.loads(line)
            if call["endpoint"] != "dokument" or not isinstance(call["body"], list):
                continue
            p = call["params"]
            for d in call["body"]:
                url = d.get("url") or ""
                if url.startswith("//"):
                    url = "https:" + url
                title = clean(d.get("titel"))
                rows.append(
                    {
                        "verkform": p["pVerkform"],
                        "omrade": p["pOmrade"],
                        "lasar": p["pLasar"],
                        "doc_id": d.get("id"),
                        "typ": d.get("typ"),
                        "filetype": d.get("filetype"),
                        "sos": d.get("sos"),
                        "tabell_nr": d.get("tabell_nr"),
                        "huvudrubrik": clean(d.get("huvudrubrik")),
                        "rubrik": clean(d.get("rubrik")),
                        "titel": title,
                        "url": url,
                        "doc_type": kind("", title),
                        "year": find_year("", title, p["pLasar"]),
                        "fetched": call["fetched"],
                        "source_url": call["url"],
                        "provenance": call.get("provenance", {}),
                    }
                )
    return rows


# ---------------------------------------------------------------- sam

SAM_FIELDS = (
    "name",
    "agency",
    "source_url",
    "source_title",
    "href",
    "text",
    "product_code",
    "doc_type",
    "year",
    "filetype",
    "fetched",
    "provenance",
)


def parse_sam() -> list[dict]:
    sources = {
        s["name"]: s
        for s in json.loads(Path("dokumentation_sources.json").read_text("utf-8"))
    }
    files = raw_files("sam", ".jsonl")
    refreshed = {path.stem for path in files}
    rows = [row for row in cached("sam_dokument") if row["name"] not in refreshed]
    seen = set()
    for path in files:
        name = path.stem
        agency = sources.get(name, {}).get("agency", name)
        for line in path.read_text("utf-8").splitlines():
            page = json.loads(line)
            for link in page.get("links", []):
                href, text = link["href"], clean(link["text"])
                if not DOC_FILE.search(href):
                    continue
                k = kind(href, text)
                if not k:
                    continue
                if (name, page["url"], href, text) in seen:
                    continue
                seen.add((name, page["url"], href, text))
                rows.append(
                    {
                        "name": name,
                        "agency": agency,
                        "source_url": page["url"],
                        "source_title": page.get("title", ""),
                        "href": href,
                        "text": text,
                        "product_code": find_code(href, text),
                        "doc_type": k,
                        "year": find_year(href, text),
                        "filetype": filetype(href),
                        "fetched": page["fetched"],
                        "provenance": page.get("provenance", {}),
                    }
                )
    return rows


# ---------------------------------------------------------------- union and products


def _group(rows: list[dict], key: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[r[key]].append(r)
    return out


def main(argv: list[str] | None = None) -> None:
    global DATA, DOK, RAW
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--output-dir", type=Path, default=DOK)
    parser.add_argument("--raw-dir", type=Path, default=RAW)
    parser.add_argument("--as-of", type=date.fromisoformat)
    options = parser.parse_args(argv)
    DATA, DOK, RAW = options.data_dir, options.output_dir, options.raw_dir
    state_path = DOK / "state.json"
    state = json.loads(state_path.read_text("utf-8")) if state_path.exists() else {}
    reference = options.as_of or date.fromisoformat(
        state.get(
            "last_attempted_at",
            state.get("last_completed_scb", datetime.now(UTC).date().isoformat()),
        )[:10]
    )
    prods = products()
    matcher = NameMatcher(prods)
    scb, siris, sam = parse_scb(), parse_siris(), parse_sam()

    docs: list[dict] = []
    if scb is None:
        # No SCB capture: keep the previous SCB documents as they were published.
        scb_docs = [
            {k: r.get(k) for k in FIELDS if k not in DERIVED}
            for r in cached("dokument")
            if r.get("source") == "scb"
        ]
    else:
        scb_docs = [
            {
                "source": "scb",
                "product_code": r["product_code"],
                "product_match": r["product_match"],
                "doc_type": r["doc_type"],
                "year": r["year"],
                "title": f"{r['produkt']} – {r['sub'] + ' ' if r['sub'] else ''}{r['label']}",
                "url": r["url"],
                "filetype": r["filetype"],
                "source_url": r.get("source_url", ""),
                "heading": r["heading"],
                "subject_area": r.get("amne") or None,
                "statistics_area": r.get("statistikomrade") or None,
                "provenance": r.get("provenance", {}),
            }
            for r in scb
        ]
    for d in scb_docs:
        code = d["product_code"]
        d["agency"] = prods.get(code, {}).get("agency") if code else None
        docs.append(d)
    siris = [r for r in siris if r["doc_type"] in SIRIS_DOC_TYPES]
    for r in siris:
        code = matcher.match("Statens skolverk", r["titel"], r["verkform"], r["omrade"])
        docs.append(
            {
                "source": "siris",
                "product_code": code,
                "product_match": "name" if code else "",
                "agency": "Statens skolverk",
                "doc_type": r["doc_type"],
                "year": r["year"],
                "title": r["titel"],
                "url": r["url"],
                "filetype": (r["filetype"] or "").lower(),
                "source_url": r.get("source_url", ""),
                "heading": f"{r['verkform']} / {r['omrade']} / {r['lasar']}",
                "provenance": r.get("provenance", {}),
            }
        )
    # A page that is about one product (its other files carry a code, or its title names the
    # product) lends that code to the files on it that carry none.
    page_code: dict[str, str] = {}
    for url, on_page in _group(sam, "source_url").items():
        codes = Counter(r["product_code"] for r in on_page if r["product_code"])
        if len(codes) == 1:
            page_code[url] = next(iter(codes))
        elif not codes and on_page:
            code = matcher.match(on_page[0]["agency"], on_page[0]["source_title"])
            if code:
                page_code[url] = code
    for r in sam:
        code, how = r["product_code"], "code"
        if not code:
            code = matcher.match(
                r["agency"],
                r["text"],
                r["source_title"],
                urlparse(r["href"]).path.rsplit("/", 1)[-1],
            )
            how = "name" if code else ""
        if not code and r["source_url"] in page_code:
            code, how = page_code[r["source_url"]], "page"
        docs.append(
            {
                "source": f"sam/{r['name']}",
                "product_code": code,
                "product_match": how,
                "agency": r["agency"],
                "doc_type": r["doc_type"],
                "year": r["year"],
                "title": r["text"],
                "url": r["href"],
                "filetype": r["filetype"],
                "source_url": r["source_url"],
                "heading": r["source_title"],
                "provenance": r.get("provenance", {}),
            }
        )
    occurrences: dict[str, int] = defaultdict(int)
    for d in docs:
        d["source_status"] = source_status(d["source"], state)
        d["candidate_product_code"] = None
        if d["product_match"] in ("name", "page"):
            d["candidate_product_code"] = d["product_code"]
            d["product_code"] = None
        d["review_required"] = bool(d["candidate_product_code"]) or not d["doc_type"]
        identity = [d["source"], d["source_url"], d["url"], d["title"], d["heading"]]
        base_identity = json.dumps(identity, ensure_ascii=False)
        occurrence = occurrences[base_identity]
        occurrences[base_identity] += 1
        d["document_id"] = hashlib.sha256(
            json.dumps([identity, occurrence], ensure_ascii=False).encode()
        ).hexdigest()
        d["product_name"] = prods.get(d["product_code"] or "", {}).get("name")
    docs.sort(
        key=lambda d: (
            d["product_code"] or "~",
            d["doc_type"] or "",
            d["year"] or "",
            d["url"],
        )
    )
    docs = [Document.model_validate(d).model_dump(mode="json") for d in docs]
    if not docs:
        raise ValueError("No documentation could be parsed; previous exports preserved")
    write("siris_dokument", siris, SIRIS_FIELDS, with_csv=False)
    write("sam_dokument", sam, SAM_FIELDS, with_csv=False)
    write("dokument", docs, FIELDS)
    # scb_dokument is retired: its subject and statistical-area labels now live in dokument.
    for suffix in ("jsonl", "csv"):
        (DOK / f"scb_dokument.{suffix}").unlink(missing_ok=True)

    by_code: dict[str, list[dict]] = defaultdict(list)
    for d in docs:
        if d["product_code"]:
            by_code[d["product_code"]].append(d)
    out = []
    kinds = (
        ("kvalitetsdeklaration", "kd"),
        ("beskrivning av statistiken", "bas"),
        ("statistikens framställning", "staf"),
        ("scbdok", "scbdok"),
        ("metaplus", "metaplus"),
    )
    for code in sorted(set(prods) | set(by_code)):
        p = prods.get(code, {})
        row = {
            "product_code": code,
            "product_name": p.get("name"),
            "responsible_agency": p.get("agency"),
            "in_calendar": code in prods,
            "last_publish_date": p.get("last"),
            "active": active(p.get("last"), reference),
            "documents": len(by_code.get(code, [])),
            "sources": sorted(
                {d["source"].split("/")[0] for d in by_code.get(code, [])}
            ),
        }
        for label, key in kinds:
            ds = [d for d in by_code.get(code, []) if d["doc_type"] == label]
            newest = max(ds, key=lambda d: (d["year"] or "", d["url"]), default=None)
            row[f"{key}_count"] = len(ds)
            row[f"{key}_latest_year"] = newest["year"] if newest else None
            row[f"{key}_latest_url"] = newest["url"] if newest else None
        out.append(row)
    fields = (
        "product_code",
        "product_name",
        "responsible_agency",
        "in_calendar",
        "last_publish_date",
        "active",
        "documents",
        "sources",
        *(f"{k}_{s}" for _, k in kinds for s in ("count", "latest_year", "latest_url")),
    )
    write("produkter", out, fields)
    atomic_json(
        DOK / "parse-manifest.json",
        {
            "as_of": reference.isoformat(),
            "documents": len(docs),
            "products": len(out),
            "review_candidates": sum(d["review_required"] for d in docs),
            "source_counts": {"scb": len(scb_docs), "siris": len(siris), "sam": len(sam)},
            "fetch_complete": state.get("complete", not state.get("last_failures")),
            "failures": state.get("last_failures", []),
        },
    )

    current = [r for r in out if r["active"]]
    with_kd = sum(1 for r in current if r["kd_count"])
    print(
        f"scb {len(scb_docs)}, siris {len(siris)}, sam {len(sam)} -> {len(docs)} documents; "
        f"{len(prods)} products in the calendar, {len(current)} current, {with_kd} of those with a kvalitetsdeklaration"
    )


if __name__ == "__main__":
    main()
