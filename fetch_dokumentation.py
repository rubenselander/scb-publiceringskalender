"""Stage 1 of 2: collect the documentation of Sweden's official statistics into data/dokumentation/raw/.

Standard library only. Run from the repository root:
    python fetch_dokumentation.py [scb] [siris] [sam] [--only NAME ...]   (then: python parse_dokumentation.py)

Three sources, each stored as it was served so that parse_dokumentation.py can be changed and run
again without fetching anything:

  scb    SCB's "Kvalitet och framställning" index (https://www.scb.se/dokumentation/kvalitet-och-framtagning/)
         lists, for every product SCB documents, every kvalitetsdeklaration / beskrivning av
         statistiken, every "statistikens framställning" / SCBDOK and the MetaPlus link, all years.
         The page loads one subject area at a time from
             /DokumentationSammanstallning/UpdateAmnesomrade?amnesomrade=<id>
         The ids come from the page's <select>. Each HTML fragment is stored whole.
         -> data/dokumentation/raw/scb/<id>.json

  siris  Skolverket's "Sök statistik" form is backed by a JSON API on siris.skolverket.se:
             sossok_api/verksamhetsformer/
             sossok_api/omrade/?pVerkform=..&pHmantyp=00
             sossok_api/lasar/?pVerkform=..&pOmrade=..&pStatomr=..
             sossok_api/dokument/?pVerkform=..&pOmrade=..&pLasar=..
         The last one lists every file published for that school form, area and year: tables,
         PMs and kvalitetsdeklarationer, each with a direct URL and a flag (sos) for tables that
         are official statistics. Every combination is walked, all years.
         -> data/dokumentation/raw/siris/<verkform>.jsonl, one line per API response

  sam    The other statistikansvariga myndigheter publish their documentation on their own sites,
         each in its own way. dokumentation_sources.json says per agency where to start and which
         links to follow; the pages are crawled and every link on them (href + text) is stored.
         Which links are documents is decided in parse_dokumentation.py.
         -> data/dokumentation/raw/sam/<name>.jsonl, one line per page

A source that fails is logged and does not stop the others; the exit status is then 1. Nothing is
deleted here: a source's files are replaced only when it completed.
"""

from __future__ import annotations

import html
import http.client
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

USER_AGENT = "scb-publiceringskalender (+https://github.com/rubenselander/scb-publiceringskalender)"
DATA = Path("data") / "dokumentation"
RAW = DATA / "raw"
SOURCES = Path("dokumentation_sources.json")

SCB_INDEX = "https://www.scb.se/dokumentation/kvalitet-och-framtagning/"
SCB_PARTIAL = "https://www.scb.se/DokumentationSammanstallning/UpdateAmnesomrade"
SIRIS_API = "https://siris.skolverket.se/siris/reports/sossok_api/"

TIMEOUT = 90
RETRIES = 4
# Some sites answer a first request with a redirect that sets a cookie and leads back to the same
# page; without the cookie that is an endless loop. One cookie jar for the whole run.
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


class SourceError(RuntimeError):
    pass


def log(message: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {message}", flush=True)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- HTTP

def get(url: str, accept: str = "*/*", retries: int = RETRIES) -> tuple[int, str, str]:
    """GET url -> (status, content type, body as text). 404 and similar are returned, not raised."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept,
                                                   "Accept-Language": "sv,en;q=0.5"})
    problem = ""
    for attempt in range(retries):
        if attempt:
            log(f"GET {url}: {problem}; retry {attempt} in {3 ** attempt} s")
            time.sleep(3 ** attempt)
        try:
            with _opener.open(request, timeout=TIMEOUT) as response:
                ctype = response.headers.get("Content-Type", "")
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return response.status, ctype, raw.decode(charset, "replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 410, 403, 401):
                return e.code, e.headers.get("Content-Type", ""), ""
            problem = f"HTTP {e.code}"
        except (OSError, http.client.HTTPException) as e:
            problem = repr(e)
    raise SourceError(f"GET {url}: {problem}")


def get_json(url: str):
    status, _, body = get(url, accept="application/json")
    if status != 200:
        raise SourceError(f"GET {url}: HTTP {status}")
    try:
        return json.loads(body)
    except ValueError as e:
        raise SourceError(f"GET {url}: not JSON ({e}): {body[:200]!r}")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in records)


def replace_dir(path: Path, keep: set[str]) -> None:
    """Remove files in path whose names are not in keep (a completed source replaces its files)."""
    if path.exists():
        for p in path.iterdir():
            if p.is_file() and p.name not in keep:
                p.unlink()


# ---------------------------------------------------------------- source: scb

def fetch_scb() -> None:
    status, _, page = get(SCB_INDEX, accept="text/html")
    if status != 200:
        raise SourceError(f"index page: HTTP {status}")
    select = re.search(r"<select[^>]*amnesomradenDropDown[^>]*>(.*?)</select>", page, re.S)
    if not select:
        raise SourceError("index page: no subject-area <select> found")
    areas = [(v, html.unescape(t).strip()) for v, t in re.findall(r'<option value="(\d+)"[^>]*>([^<]*)<', select.group(1))]
    if len(areas) < 10:
        raise SourceError(f"index page: only {len(areas)} subject areas found")
    log(f"scb: {len(areas)} subject areas")
    out = RAW / "scb"
    out.mkdir(parents=True, exist_ok=True)
    written = set()
    for area_id, name in areas:
        url = f"{SCB_PARTIAL}?amnesomrade={area_id}"
        status, _, fragment = get(url, accept="text/html")
        if status != 200:
            raise SourceError(f"{name}: HTTP {status}")
        n_links = len(re.findall(r"<a\b", fragment))
        log(f"scb: {name}: {n_links} links")
        record = {"amnesomrade_id": area_id, "amnesomrade": name, "url": url, "fetched": now(), "html": fragment}
        (out / f"{area_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", "utf-8")
        written.add(f"{area_id}.json")
    replace_dir(out, written)


# ---------------------------------------------------------------- source: siris

def _codes(items) -> list[str]:
    if not isinstance(items, list):
        raise SourceError(f"siris: expected a list, got {str(items)[:200]!r}")
    return [str(i.get("kod") if isinstance(i, dict) else i) for i in items]


def fetch_siris() -> None:
    q = urllib.parse.quote
    forms = _codes(get_json(SIRIS_API + "verksamhetsformer/"))
    if not forms:
        raise SourceError("siris: no school forms")
    log(f"siris: {len(forms)} school forms")
    out = RAW / "siris"
    out.mkdir(parents=True, exist_ok=True)
    written = set()
    total_docs = 0
    for form in forms:
        records = []

        def call(endpoint: str, **params):
            url = f"{SIRIS_API}{endpoint}/?" + urllib.parse.urlencode(params, quote_via=q)
            body = get_json(url)
            records.append({"endpoint": endpoint, "params": params, "url": url, "fetched": now(), "body": body})
            return body

        areas = _codes(call("omrade", pVerkform=form, pHmantyp="00"))
        n = 0
        for area in areas:
            years = _codes(call("lasar", pVerkform=form, pOmrade=area, pStatomr=area))
            for year in years:
                docs = call("dokument", pVerkform=form, pOmrade=area, pLasar=year)
                n += len(docs) if isinstance(docs, list) else 0
        name = re.sub(r"[^a-z0-9]+", "-", form.lower().translate(str.maketrans("åäö", "aao"))).strip("-")
        write_jsonl(out / f"{name}.jsonl", records)
        written.add(f"{name}.jsonl")
        total_docs += n
        log(f"siris: {form}: {len(areas)} areas, {len(records)} calls, {n} documents")
    replace_dir(out, written)
    log(f"siris: {total_docs} documents in all")


# ---------------------------------------------------------------- source: sam (crawl)

class _Links(HTMLParser):
    """All <a href> of a page with their text, plus the <title>."""

    def __init__(self):
        super().__init__()
        self.links: list[dict] = []
        self.title = ""
        self._href = None
        self._text = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._href, self._text = href, []
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append({"href": self._href, "text": " ".join("".join(self._text).split())})
            self._href = None
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)
        if self._in_title:
            self.title += data


def _absolute(base: str, href: str) -> str | None:
    href = href.strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    url = urllib.parse.urljoin(base, href)
    url, _ = urllib.parse.urldefrag(url)
    return url if url.startswith("http") else None


def crawl(source: dict) -> list[dict]:
    """Breadth-first crawl of one agency's documentation pages -> one record per page fetched."""
    follow = [re.compile(p) for p in source.get("follow", [])]
    skip = [re.compile(p) for p in source.get("skip", [])]
    variants = source.get("variants", [""])
    max_pages = source.get("max_pages", 200)
    max_depth = source.get("max_depth", 1)
    delay = source.get("delay", 0.5)
    queue = [(url, 0) for url in source["start"]]
    seen, pages = set(), []
    while queue and len(pages) < max_pages:
        url, depth = queue.pop(0)
        for variant in variants:  # e.g. "?currentTab=3" where documents sit on a tab of the page
            target = url + variant if variant and variant not in url else url
            if target in seen:
                continue
            seen.add(target)
            try:
                status, ctype, body = get(target, accept="text/html", retries=2)
            except SourceError as e:
                pages.append({"url": target, "depth": depth, "status": None, "error": str(e), "fetched": now(),
                              "title": "", "links": []})
                continue
            record = {"url": target, "depth": depth, "status": status, "fetched": now(), "title": "", "links": []}
            if status == 200 and "html" in ctype.lower():
                parser = _Links()
                try:
                    parser.feed(body)
                    parser.close()
                except Exception as e:  # a page that will not parse is kept with its error
                    record["error"] = f"parse: {e!r}"
                record["title"] = " ".join(parser.title.split())
                for link in parser.links:
                    absolute = _absolute(target, link["href"])
                    if not absolute:
                        continue
                    record["links"].append({"href": absolute, "text": link["text"]})
                    if (depth < max_depth and absolute not in seen
                            and any(p.search(absolute) for p in follow) and not any(p.search(absolute) for p in skip)):
                        queue.append((absolute, depth + 1))
            pages.append(record)
            time.sleep(delay)
    if queue:
        log(f"sam/{source['name']}: stopped at max_pages={max_pages}, {len(queue)} pages left in queue")
    return pages


def fetch_sam(only: set[str] | None) -> tuple[list[str], int]:
    """Crawl every agency in dokumentation_sources.json -> (names of the ones that failed, number tried)."""
    sources = json.loads(SOURCES.read_text("utf-8"))
    if only:
        sources = [s for s in sources if s["name"] in only]
    out = RAW / "sam"
    out.mkdir(parents=True, exist_ok=True)
    failed = []

    def one(source: dict) -> None:
        pages = crawl(source)
        ok = sum(1 for p in pages if p["status"] == 200)
        links = sum(len(p["links"]) for p in pages)
        write_jsonl(out / f"{source['name']}.jsonl", pages)
        log(f"sam/{source['name']}: {len(pages)} pages ({ok} ok), {links} links")
        if not ok:
            raise SourceError(f"sam/{source['name']}: no page could be read")

    with ThreadPoolExecutor(6) as pool:  # agencies in parallel, each agency's pages one after another
        futures = {pool.submit(one, s): s["name"] for s in sources}
        for future, name in futures.items():
            try:
                future.result()
            except Exception as e:
                log(f"sam/{name}: FAILED: {e}")
                failed.append(name)
    return failed, len(sources)


# ---------------------------------------------------------------- main

def main() -> int:
    args = sys.argv[1:]
    only = set()
    if "--only" in args:
        i = args.index("--only")
        only = set(args[i + 1:])
        args = args[:i]
    wanted = set(args) or {"scb", "siris", "sam"}
    failures = []
    for name, fn in (("scb", fetch_scb), ("siris", fetch_siris)):
        if name in wanted:
            try:
                fn()
            except Exception as e:
                log(f"{name}: FAILED: {e}")
                failures.append(name)
    sam_all_failed = False
    if "sam" in wanted:
        failed, n_sources = fetch_sam(only)
        failures += [f"sam/{n}" for n in failed]
        sam_all_failed = len(failed) == n_sources
    DATA.mkdir(parents=True, exist_ok=True)
    state_path = DATA / "state.json"
    state = json.loads(state_path.read_text("utf-8")) if state_path.exists() else {}
    for name in wanted:
        if name not in failures:
            state[f"last_completed_{name}"] = now()[:10]
    state["last_failures"] = failures
    state_path.write_text(json.dumps(state, indent=1) + "\n", "utf-8")
    log("done" + (f"; could not read: {', '.join(failures)}" if failures else ""))
    everything_failed = all(n in failures for n in wanted - {"sam"}) and ("sam" not in wanted or sam_all_failed)
    return 1 if everything_failed else 0


if __name__ == "__main__":
    sys.exit(main())
