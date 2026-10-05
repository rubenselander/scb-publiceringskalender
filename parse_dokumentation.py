"""Stage 2 of 2: turn data/dokumentation/raw/ into the documentation files in data/dokumentation/.

Standard library only, no network. Run from the repository root:  python parse_dokumentation.py

Outputs (CSV and JSON Lines with the same rows):
  scb_dokument      every document SCB's "Kvalitet och framställning" index lists, all years
  siris_dokument    every file Skolverket's SIRIS API lists (tables, PMs, kvalitetsdeklarationer),
                    with Skolverket's own official-statistics flag (sos)
  sam_dokument      every link on the crawled agency pages that looks like a documentation file
  dokument          the three above reduced to documentation (kvalitetsdeklaration, beskrivning av
                    statistiken, statistikens framställning, SCBDOK, MetaPlus, kvalitetsrapport),
                    one row per document, with the product code where one could be established
  produkter         one row per product code in the calendar (data/calendar.jsonl): its agency,
                    the newest document of each kind and how many documents were found

A product code is taken from the file name or link text when it holds one (xx0000). Documents
without a code (Socialstyrelsen, Trafikanalys, Skolverket, ...) are matched to the responsible
agency's products by name; `product_match` says which ("code", "name" or empty).
"""

from __future__ import annotations

import csv
import html
import json
import re
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

DATA = Path("data")
DOK = DATA / "dokumentation"
RAW = DOK / "raw"

CODE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2}\d{4})(?![0-9])")
YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
DOC_FILE = re.compile(r"\.(pdf|docx?|xlsx?)(\?|$)|/download/|getFile|urn\.kb\.se|metadata\.scb\.se|kvalitetsdeklaration", re.I)
# What kind of documentation a link is, from its file name and text. Order matters.
KINDS = (
    ("metaplus", re.compile(r"metadata\.scb\.se|metaplus", re.I)),
    ("scbdok", re.compile(r"scbdok|(?<![a-z])do[_-]\d{4}|_do[_.]", re.I)),
    ("statistikens framställning", re.compile(r"statistikens[ _-]?framst|(?<![a-z])staf(?![a-z])|framstallning", re.I)),
    ("beskrivning av statistiken", re.compile(r"beskrivning[ _-]av[ _-]statistiken|(?<![a-z])bs[_-]\d{4}|_bs[_.]|_beskr|(?<![a-z])bas(?![a-z])", re.I)),
    ("kvalitetsrapport", re.compile(r"kvalitetsrapport|quality[ _-]report", re.I)),
    ("kvalitetsdeklaration", re.compile(r"kvalitet|(?<![a-z])kd(?![a-z])|quality[ _-]declaration", re.I)),
)
FIELDS = ("source", "agency", "product_code", "product_name", "product_match", "doc_type", "year",
          "title", "url", "filetype", "source_url", "heading")


def clean(s: str) -> str:
    return " ".join(html.unescape(s or "").split())


def filetype(url: str) -> str:
    m = re.search(r"\.(pdf|docx?|xlsx?|html?)(?:\?|$)", url, re.I)
    if m:
        return m.group(1).lower()
    if "getFile" in url or "/download/" in url or "urn.kb.se" in url:
        return "pdf?"
    return "html"


def find_code(url: str, *texts: str) -> str | None:
    """A product code (xx0000) in the file name, else in the texts, else anywhere in the URL."""
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    for t in (name, *texts, unquote(url)):
        m = CODE.search(t or "")
        if m:
            return m.group(1).upper()
    return None


def find_year(url: str, *texts: str) -> str | None:
    """The year a document is about: the last year in its file name, else in its text, else in its path."""
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    for t in (name, *texts, unquote(url)):
        years = YEAR.findall(t or "")
        if years:
            return years[-1] if t is name else years[0]
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


def write(name: str, rows: list[dict], fields: tuple) -> None:
    DOK.mkdir(parents=True, exist_ok=True)
    with open(DOK / f"{name}.jsonl", "w", encoding="utf-8", newline="\n") as f:
        f.writelines(json.dumps({k: r.get(k) for k in fields}, ensure_ascii=False) + "\n" for r in rows)
    with open(DOK / f"{name}.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(fields)
        w.writerows([_csv(r.get(k)) for k in fields] for r in rows)


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
            out[code] = {"name": r["product_name"], "agency": r["responsible_agency"], "last": r["publish_date"]}
    return out


_STOP = {"statistik", "statistiken", "och", "för", "med", "som", "inom", "efter", "per", "samt", "the", "and",
         "kvalitetsdeklaration", "statistikens", "framställning", "pdf", "docx", "läs", "läsår", "läsåret", "år",
         "hösten", "våren", "område", "områdets", "uppgifter", "officiell", "officiella", "sveriges"}


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
            self._capture = "area" if "h2-small" in (attrs.get("class") or "") else "product"
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
            elif self._capture == "heading":
                self.heading, self.sub = text, ""
            elif "mikrodata" in text.lower():  # a plain <h4> too
                self.heading, self.sub = text, ""
            else:
                self.sub = text
            self._capture = None
        elif tag == "a" and self._capture == "link":
            href = self._href
            url = href if href.startswith("http") else "https://www.scb.se" + href
            self.rows.append({"amne": "", "statistikomrade": self.area, "produkt": self.product,
                              "heading": self.heading, "sub": self.sub, "label": clean(self._text), "url": url})
            self._capture = None


def parse_scb() -> list[dict]:
    rows = []
    for path in sorted((RAW / "scb").glob("*.json")):
        record = json.loads(path.read_text("utf-8"))
        parser = _ScbIndex()
        parser.feed(record["html"])
        parser.close()
        for r in parser.rows:
            r["amne"] = record["amnesomrade"]
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
        meta = [r["product_code"] for r in block if r["doc_type"] == "metaplus" and r["product_code"]]
        code = meta[0] if len(set(meta)) == 1 else (codes.most_common(1)[0][0] if codes else None)
        for r in block:
            if code and not r["product_code"]:
                r["product_code"], r["product_match"] = code, "block"
    return rows


SCB_FIELDS = ("amne", "statistikomrade", "produkt", "product_code", "product_match", "heading", "sub", "doc_type", "year",
              "label", "url", "filetype", "fetched")


# ---------------------------------------------------------------- siris

SIRIS_FIELDS = ("verkform", "omrade", "lasar", "doc_id", "typ", "filetype", "sos", "tabell_nr", "huvudrubrik",
                "rubrik", "titel", "url", "doc_type", "year", "fetched")


def parse_siris() -> list[dict]:
    rows = []
    for path in sorted((RAW / "siris").glob("*.jsonl")):
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
                rows.append({"verkform": p["pVerkform"], "omrade": p["pOmrade"], "lasar": p["pLasar"],
                             "doc_id": d.get("id"), "typ": d.get("typ"), "filetype": d.get("filetype"),
                             "sos": d.get("sos"), "tabell_nr": d.get("tabell_nr"), "huvudrubrik": clean(d.get("huvudrubrik")),
                             "rubrik": clean(d.get("rubrik")), "titel": title, "url": url,
                             "doc_type": kind("", title), "year": find_year("", title, p["pLasar"]),
                             "fetched": call["fetched"]})
    return rows


# ---------------------------------------------------------------- sam

SAM_FIELDS = ("name", "agency", "source_url", "source_title", "href", "text", "product_code", "doc_type", "year",
              "filetype", "fetched")


def parse_sam() -> list[dict]:
    sources = {s["name"]: s for s in json.loads(Path("dokumentation_sources.json").read_text("utf-8"))}
    rows, seen = [], set()
    for path in sorted((RAW / "sam").glob("*.jsonl")):
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
                if (href, text) in seen:
                    continue
                seen.add((href, text))
                rows.append({"name": name, "agency": agency, "source_url": page["url"], "source_title": page.get("title", ""),
                             "href": href, "text": text, "product_code": find_code(href, text), "doc_type": k,
                             "year": find_year(href, text), "filetype": filetype(href), "fetched": page["fetched"]})
    return rows


# ---------------------------------------------------------------- union and products

def main() -> None:
    prods = products()
    matcher = NameMatcher(prods)
    scb, siris, sam = parse_scb(), parse_siris(), parse_sam()
    write("scb_dokument", scb, SCB_FIELDS)
    write("siris_dokument", siris, SIRIS_FIELDS)
    write("sam_dokument", sam, SAM_FIELDS)

    docs: list[dict] = []
    for r in scb:
        code = r["product_code"]
        docs.append({"source": "scb", "product_code": code, "product_match": r["product_match"],
                     "agency": prods.get(code, {}).get("agency") if code else None, "doc_type": r["doc_type"],
                     "year": r["year"], "title": f"{r['produkt']} – {r['sub'] + ' ' if r['sub'] else ''}{r['label']}",
                     "url": r["url"], "filetype": r["filetype"], "source_url": "", "heading": r["heading"]})
    for r in siris:
        if r["doc_type"] not in ("kvalitetsdeklaration", "statistikens framställning", "beskrivning av statistiken"):
            continue
        code = matcher.match("Skolverket", r["titel"], r["verkform"], r["omrade"])
        docs.append({"source": "siris", "product_code": code, "product_match": "name" if code else "", "agency": "Skolverket",
                     "doc_type": r["doc_type"], "year": r["year"], "title": r["titel"], "url": r["url"],
                     "filetype": (r["filetype"] or "").lower(), "source_url": "", "heading": f"{r['verkform']} / {r['omrade']} / {r['lasar']}"})
    for r in sam:
        code, how = r["product_code"], "code"
        if not code:
            code = matcher.match(r["agency"], r["text"], r["source_title"], urlparse(r["href"]).path.rsplit("/", 1)[-1])
            how = "name" if code else ""
        docs.append({"source": f"sam/{r['name']}", "product_code": code, "product_match": how, "agency": r["agency"],
                     "doc_type": r["doc_type"], "year": r["year"], "title": r["text"], "url": r["href"],
                     "filetype": r["filetype"], "source_url": r["source_url"], "heading": r["source_title"]})
    for d in docs:
        d["product_name"] = prods.get(d["product_code"] or "", {}).get("name")
    docs.sort(key=lambda d: (d["product_code"] or "~", d["doc_type"] or "", d["year"] or "", d["url"]))
    write("dokument", docs, FIELDS)

    by_code: dict[str, list[dict]] = defaultdict(list)
    for d in docs:
        if d["product_code"]:
            by_code[d["product_code"]].append(d)
    out = []
    kinds = (("kvalitetsdeklaration", "kd"), ("beskrivning av statistiken", "bas"),
             ("statistikens framställning", "staf"), ("scbdok", "scbdok"), ("metaplus", "metaplus"))
    for code in sorted(set(prods) | set(by_code)):
        p = prods.get(code, {})
        row = {"product_code": code, "product_name": p.get("name"), "responsible_agency": p.get("agency"),
               "in_calendar": code in prods, "documents": len(by_code.get(code, [])),
               "sources": sorted({d["source"].split("/")[0] for d in by_code.get(code, [])})}
        for label, key in kinds:
            ds = [d for d in by_code.get(code, []) if d["doc_type"] == label]
            newest = max(ds, key=lambda d: (d["year"] or "", d["url"]), default=None)
            row[f"{key}_count"] = len(ds)
            row[f"{key}_latest_year"] = newest["year"] if newest else None
            row[f"{key}_latest_url"] = newest["url"] if newest else None
        out.append(row)
    fields = ("product_code", "product_name", "responsible_agency", "in_calendar", "documents", "sources",
              *(f"{k}_{s}" for _, k in kinds for s in ("count", "latest_year", "latest_url")))
    write("produkter", out, fields)

    with_kd = sum(1 for r in out if r["in_calendar"] and r["kd_count"])
    print(f"scb {len(scb)}, siris {len(siris)}, sam {len(sam)} -> {len(docs)} documents; "
          f"{len(prods)} products in the calendar, {with_kd} with a kvalitetsdeklaration")


if __name__ == "__main__":
    main()
