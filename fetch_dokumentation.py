"""Stage 1 of 2: collect the documentation of Sweden's official statistics into data/dokumentation/raw/.

Uses the repository’s locked uv environment. Run from the repository root:
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

import argparse
import hashlib
import html
import json
import re
import shutil
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

from bs4 import BeautifulSoup

from scb_extract.core import ExtractionContext, FetchRequest, atomic_json

DATA = Path("data") / "dokumentation"
RAW = DATA / "raw"
SOURCES = Path("dokumentation_sources.json")

SCB_INDEX = "https://www.scb.se/dokumentation/kvalitet-och-framtagning/"
SCB_PARTIAL = "https://www.scb.se/DokumentationSammanstallning/UpdateAmnesomrade"
SIRIS_API = "https://siris.skolverket.se/siris/reports/sossok_api/"

BUNDLE = (
    Path(".extract-raw")
    / "documentation"
    / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
)
_local = threading.local()
_contexts: list[ExtractionContext] = []
_context_lock = threading.Lock()


def context() -> ExtractionContext:
    """Give each worker its own cookie-preserving shared HTTP client and archive."""
    if not hasattr(_local, "client"):
        _local.client = ExtractionContext(
            BUNDLE / "http" / threading.current_thread().name
        )
        with _context_lock:
            _contexts.append(_local.client)
    return _local.client


def evidence(url: str) -> dict:
    snapshot = context().cache.get(FetchRequest(url=url).key)
    return snapshot.provenance().model_dump(mode="json") if snapshot else {}


class SourceError(RuntimeError):
    pass


def log(message: str) -> None:
    print(f"{datetime.now(UTC):%H:%M:%S} {message}", flush=True)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------- HTTP


def get(url: str) -> tuple[int, str, str]:
    """Fetch through the shared bounded client; archive bytes and request provenance."""
    try:
        snapshot = context().fetch(FetchRequest(url=url))
    except OSError as exc:
        raise SourceError(str(exc)) from exc
    return snapshot.status, snapshot.content_type, snapshot.text


def get_json(url: str):
    status, _, body = get(url)
    if status != 200:
        raise SourceError(f"GET {url}: HTTP {status}")
    try:
        return json.loads(body)
    except ValueError as e:
        raise SourceError(f"GET {url}: not JSON ({e}): {body[:200]!r}")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), "utf-8"
    )
    temporary.replace(path)


def publish_raw(source: str, staging: Path) -> None:
    """Publish a complete immutable source generation through one atomic pointer.

    A crash during staging leaves the previous generation available. Failed or
    capped captures stay in the diagnostic artifact and never become current.
    """
    files = sorted(staging.iterdir())
    digest = hashlib.sha256()
    for file in files:
        digest.update(file.name.encode())
        digest.update(file.read_bytes())
    generation = Path("generations") / digest.hexdigest()
    target = RAW / source / generation
    target.mkdir(parents=True, exist_ok=True)
    for file in files:
        temporary = target / (file.name + ".tmp")
        shutil.copyfile(file, temporary)
        temporary.replace(target / file.name)
    atomic_json(
        RAW / source / "membership.json",
        {"files": [(generation / file.name).as_posix() for file in files]},
    )


# ---------------------------------------------------------------- source: scb


def fetch_scb() -> None:
    status, _, page = get(SCB_INDEX)
    if status != 200:
        raise SourceError(f"index page: HTTP {status}")
    select = re.search(
        r"<select[^>]*amnesomradenDropDown[^>]*>(.*?)</select>", page, re.DOTALL
    )
    if not select:
        raise SourceError("index page: no subject-area <select> found")
    areas = [
        (v, html.unescape(t).strip())
        for v, t in re.findall(r'<option value="(\d+)"[^>]*>([^<]*)<', select.group(1))
    ]
    if len(areas) < 10:
        raise SourceError(f"index page: only {len(areas)} subject areas found")
    log(f"scb: {len(areas)} subject areas")
    out = BUNDLE / "parsed-raw" / "scb"
    out.mkdir(parents=True, exist_ok=True)
    for area_id, name in areas:
        url = f"{SCB_PARTIAL}?amnesomrade={area_id}"
        status, _, fragment = get(url)
        if status != 200:
            raise SourceError(f"{name}: HTTP {status}")
        n_links = len(re.findall(r"<a\b", fragment))
        log(f"scb: {name}: {n_links} links")
        record = {
            "amnesomrade_id": area_id,
            "amnesomrade": name,
            "url": url,
            "fetched": now(),
            "html": fragment,
            "provenance": evidence(url),
        }
        (out / f"{area_id}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=1) + "\n", "utf-8"
        )
    publish_raw("scb", out)


# ---------------------------------------------------------------- source: siris


def _codes(items) -> list[str]:
    if not isinstance(items, list):
        raise SourceError(f"siris: expected a list, got {str(items)[:200]!r}")
    codes = [i.get("kod") if isinstance(i, dict) else i for i in items]
    if any(code is None or str(code).strip() == "" for code in codes):
        raise SourceError("siris: missing code in discovery response")
    return list(dict.fromkeys(str(code) for code in codes))


def fetch_siris() -> None:
    q = urllib.parse.quote
    forms = _codes(get_json(SIRIS_API + "verksamhetsformer/"))
    if not forms:
        raise SourceError("siris: no school forms")
    log(f"siris: {len(forms)} school forms")
    out = BUNDLE / "parsed-raw" / "siris"
    out.mkdir(parents=True, exist_ok=True)
    total_docs = 0
    for form in forms:
        records = []

        def call(endpoint: str, records=records, **params):
            url = f"{SIRIS_API}{endpoint}/?" + urllib.parse.urlencode(
                params, quote_via=q
            )
            body = get_json(url)
            records.append(
                {
                    "endpoint": endpoint,
                    "params": params,
                    "url": url,
                    "fetched": now(),
                    "body": body,
                    "provenance": evidence(url),
                }
            )
            return body

        areas = _codes(call("omrade", pVerkform=form, pHmantyp="00"))
        n = 0
        for area in areas:
            years = _codes(call("lasar", pVerkform=form, pOmrade=area, pStatomr=area))
            for year in years:
                docs = call("dokument", pVerkform=form, pOmrade=area, pLasar=year)
                if not isinstance(docs, list):
                    raise SourceError(
                        f"siris: {form}/{area}/{year}: expected document list"
                    )
                n += len(docs)
        name = re.sub(
            r"[^a-z0-9]+", "-", form.lower().translate(str.maketrans("åäö", "aao"))
        ).strip("-")
        write_jsonl(out / f"{name}.jsonl", records)
        total_docs += n
        log(f"siris: {form}: {len(areas)} areas, {len(records)} calls, {n} documents")
    publish_raw("siris", out)
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
            self.links.append(
                {"href": self._href, "text": " ".join("".join(self._text).split())}
            )
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
    started = time.monotonic()
    max_seconds = source.get("max_seconds", 600)
    while queue and len(pages) < max_pages:
        url, depth = queue.pop(0)
        for (
            variant
        ) in variants:  # e.g. "?currentTab=3" where documents sit on a tab of the page
            target = url + variant if variant and variant not in url else url
            if target in seen:
                continue
            if time.monotonic() - started >= max_seconds:
                write_jsonl(BUNDLE / "attempts" / f"{source['name']}.jsonl", pages)
                raise SourceError(
                    f"sam/{source['name']}: time budget {max_seconds}s reached"
                )
            if len(pages) >= max_pages:
                write_jsonl(BUNDLE / "attempts" / f"{source['name']}.jsonl", pages)
                raise SourceError(
                    f"sam/{source['name']}: max_pages={max_pages} reached"
                )
            seen.add(target)
            try:
                status, ctype, body = get(target)
            except SourceError as e:
                pages.append(
                    {
                        "url": target,
                        "depth": depth,
                        "status": None,
                        "error": str(e),
                        "fetched": now(),
                        "title": "",
                        "links": [],
                    }
                )
                continue
            record = {
                "url": target,
                "depth": depth,
                "status": status,
                "fetched": now(),
                "title": "",
                "links": [],
                "provenance": evidence(target),
            }
            if status == 200 and "html" in ctype.lower():
                parser = _Links()
                try:
                    parser.feed(body)
                    parser.close()
                except Exception as e:  # noqa: BLE001 - capture page diagnostics
                    record["error"] = f"parse: {e!r}"
                record["title"] = " ".join(parser.title.split())
                soup = BeautifulSoup(body, "lxml")
                main = soup.find("main") or soup.find(attrs={"role": "main"}) or soup
                base = record["provenance"].get("response_url", target)
                follow_links = {
                    _absolute(base, anchor["href"])
                    for anchor in main.find_all("a", href=True)
                }
                for link in parser.links:
                    absolute = _absolute(
                        record["provenance"].get("response_url", target), link["href"]
                    )
                    if not absolute:
                        continue
                    record["links"].append({"href": absolute, "text": link["text"]})
                    if (
                        depth < max_depth
                        and absolute in follow_links
                        and absolute not in seen
                        and any(p.search(absolute) for p in follow)
                        and not any(p.search(absolute) for p in skip)
                    ):
                        queue.append((absolute, depth + 1))
            if status != 200 or "html" not in ctype.lower():
                record["error"] = f"Expected HTML, received {status} {ctype}"
            pages.append(record)
            time.sleep(delay)
    diagnostic = BUNDLE / "attempts" / f"{source['name']}.jsonl"
    write_jsonl(diagnostic, pages)
    pending = [
        url
        for url, _ in queue
        if any(
            (url + variant if variant and variant not in url else url) not in seen
            for variant in variants
        )
    ]
    errors = [p for p in pages if p.get("error") or p["status"] != 200]
    if pending or errors or not pages:
        raise SourceError(
            f"sam/{source['name']}: incomplete crawl ({len(errors)} failed pages, {len(pending)} pending)"
        )
    return pages


def fetch_sam(only: set[str] | None) -> tuple[list[str], int]:
    """Crawl every agency in dokumentation_sources.json -> (names of the ones that failed, number tried)."""
    sources = json.loads(SOURCES.read_text("utf-8"))
    if only:
        unknown = only - {s["name"] for s in sources}
        if unknown:
            raise SourceError(f"Unknown agencies: {', '.join(sorted(unknown))}")
        sources = [s for s in sources if s["name"] in only]
    out = RAW / "sam"
    out.mkdir(parents=True, exist_ok=True)
    failed = []

    def one(source: dict) -> None:
        pages = crawl(source)
        ok = sum(1 for p in pages if p["status"] == 200)
        links = sum(len(p["links"]) for p in pages)
        if not ok or not links:
            raise SourceError(
                f"sam/{source['name']}: no usable page links; page may require JavaScript"
            )
        write_jsonl(out / f"{source['name']}.jsonl", pages)
        write_jsonl(BUNDLE / "parsed-raw" / "sam" / f"{source['name']}.jsonl", pages)
        log(f"sam/{source['name']}: {len(pages)} pages ({ok} ok), {links} links")

    with ThreadPoolExecutor(
        6
    ) as pool:  # agencies in parallel, each agency's pages one after another
        futures = {pool.submit(one, s): s["name"] for s in sources}
        for future in as_completed(futures):
            name = futures[future]
            try:
                future.result()
            except Exception as e:  # noqa: BLE001 - isolate source/page failures
                log(f"sam/{name}: FAILED: {e}")
                failed.append(name)
                atomic_json(
                    BUNDLE / "attempts" / f"{name}-error.json",
                    {"source": name, "error": str(e)},
                )
    return failed, len(sources)


# ---------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    global RAW, DATA, BUNDLE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="*", choices=("scb", "siris", "sam"))
    parser.add_argument("--only", nargs="+", default=[])
    parser.add_argument("--raw-dir", type=Path, default=RAW)
    parser.add_argument("--bundle-dir", type=Path, default=BUNDLE)
    parser.add_argument("--output-dir", type=Path, default=DATA)
    options = parser.parse_args(argv)
    RAW, DATA, BUNDLE = options.raw_dir, options.output_dir, options.bundle_dir
    if (BUNDLE / "result.json").exists():
        parser.error("Bundle already contains a run; choose a fresh --bundle-dir")
    inputs = BUNDLE / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    for path in [
        SOURCES,
        Path("data/calendar.jsonl"),
        *DATA.glob("*.jsonl"),
        DATA / "state.json",
    ]:
        if path.exists():
            shutil.copyfile(path, inputs / path.name)
    wanted = set(options.sources) or {"scb", "siris", "sam"}
    if options.only and "sam" not in wanted:
        parser.error("--only requires sam")
    failures: list[str] = []
    errors: dict[str, str] = {}
    successful = set()
    for name, fn in (
        ("scb", fetch_scb),
        ("siris", fetch_siris),
        ("sam", lambda: fetch_sam(set(options.only))),
    ):
        if name not in wanted:
            continue
        try:
            outcome = fn()
            if name == "sam" and outcome[0]:
                failures.extend(f"sam/{agency}" for agency in outcome[0])
            else:
                successful.add(name)
        except Exception as exc:  # noqa: BLE001 - preserve successful sibling sources
            failures.append(name)
            errors[name] = str(exc)
            log(f"{name}: FAILED: {exc}")
    DATA.mkdir(parents=True, exist_ok=True)
    state_path = DATA / "state.json"
    state = json.loads(state_path.read_text("utf-8")) if state_path.exists() else {}
    for name in successful:
        if name != "sam" or not options.only:
            state[f"last_completed_{name}"] = now()[:10]
    previous_failures = state.get("last_failures", [])

    def was_attempted(failure: str) -> bool:
        family, _, agency = failure.partition("/")
        return family in wanted and (
            family != "sam"
            or not options.only
            or bool(agency)
            and agency in options.only
        )

    failures = sorted(
        set(failures)
        | {failure for failure in previous_failures if not was_attempted(failure)}
    )
    state.update(
        last_attempted_at=now(),
        attempted_sources=sorted(wanted),
        selected_agencies=sorted(options.only),
        last_failures=sorted(failures),
        errors=errors,
        complete=not failures,
    )
    atomic_json(state_path, state)
    atomic_json(BUNDLE / "result.json", state)
    atomic_json(BUNDLE / "state.json", state)
    for client in _contexts:
        client.client.close()
    _contexts.clear()
    if hasattr(_local, "client"):
        del _local.client
    log("done" + (f"; incomplete: {', '.join(failures)}" if failures else ""))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
