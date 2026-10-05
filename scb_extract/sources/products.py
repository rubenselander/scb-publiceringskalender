"""SCB landing pages and their bounded subject/documentation discovery."""

import json
import re
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from scb_extract.core import (
    ExtractionContext,
    FetchRequest,
    FetchSnapshot,
    SourceResult,
    absolute_url,
    canonical_url,
    url_key,
)
from scb_extract.models.products_subjects import (
    ContentGroup,
    DocumentationEntry,
    DocumentationIndex,
    DocumentationPage,
    Heading,
    OfficialSubjects,
    ProductCard,
    ProductPage,
    ResponsibleAgency,
    SourceLink,
    StatisticalArea,
    SubjectPage,
)

SUBJECTS_URL = "https://www.scb.se/om-scb/samordning-av-sveriges-officiella-statistik/officiell-statistik-efter-amne/"
DOCUMENTATION_URL = "https://www.scb.se/dokumentation/"
PRODUCT_CODE = re.compile(r"\b([A-Z]{2}\d{4})\b", re.IGNORECASE)
_ALIASES: WeakKeyDictionary = WeakKeyDictionary()


def _text(node: Tag | None) -> str | None:
    return " ".join(node.stripped_strings) if node else None


def _page(snapshot: FetchSnapshot) -> tuple[Tag, str | None]:
    soup = BeautifulSoup(snapshot.text, "lxml")
    page = soup.select_one("#pageContent")
    if page is None or not _text(page.select_one("h1")):
        raise ValueError(f"Missing pageContent/title: {snapshot.final_url}")
    canonical = soup.select_one('link[rel="canonical"]')
    return page, (
        absolute_url(snapshot.final_url, canonical["href"])
        if canonical and canonical.get("href")
        else None
    )


def _link(node: Tag, base: str) -> SourceLink:
    href = str(node.get("href", ""))
    return SourceLink(text=_text(node) or "", href=href, url=absolute_url(base, href))


def _heading(node: Tag) -> Heading:
    return Heading(
        text=_text(node) or "",
        level=int(node.name[1]) if re.fullmatch(r"h[1-6]", node.name) else None,
        anchor=node.get("id") or node.get("name"),
    )


def _groups(container: Tag, base: str) -> list[ContentGroup]:
    """Walk containers independently, preserving headings and authored links.

    SCB document lists identify a heading that applies to sibling subseries via
    bas-list-h/dok-list-h; those same-level h4s have structural parentage.
    """
    groups: list[ContentGroup] = []

    def walk(parent: Tag, inherited: list[Heading]) -> None:
        path = list(inherited)
        document_type: Heading | None = None
        current: ContentGroup | None = None

        def group() -> ContentGroup:
            nonlocal current
            if current is None:
                current = ContentGroup(heading_path=list(path))
                groups.append(current)
            return current

        for child in parent.children:
            if not isinstance(child, Tag):
                if (
                    parent.name == "li"
                    and isinstance(child, NavigableString)
                    and not isinstance(child, Comment)
                ):
                    text = " ".join(str(child).split())
                    if text:
                        group().paragraphs.append(text)
                continue
            if (
                child.name in {"script", "style", "nav", "button", "select"}
                or child.get("role") == "navigation"
                or "rs_skip" in child.get("class", [])
            ):
                continue
            if re.fullmatch(r"h[1-6]", child.name):
                heading = _heading(child)
                if child.get("id") in {"bas-list-h", "dok-list-h"}:
                    document_type = heading
                    path = [*inherited, heading]
                elif document_type and child.name == "h4":
                    path = [*inherited, document_type, heading]
                else:
                    path = [*inherited, heading]
                    document_type = None
                current = None
            elif child.name == "table":
                item = group()
                item.table_headers.extend(_text(t) or "" for t in child.select("th"))
                item.table_rows.extend(
                    [
                        _text(cell) or ""
                        for cell in row.find_all(["td", "th"], recursive=False)
                    ]
                    for row in child.select("tr")
                )
                item.links.extend(_link(a, base) for a in child.select("a[href]"))
            elif child.name == "li" and not child.find(
                ["p", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6"]
            ):
                item = group()
                if _text(child):
                    item.paragraphs.append(_text(child))
                item.links.extend(_link(a, base) for a in child.select("a[href]"))
            elif child.name in {"p", "dt", "dd", "a"}:
                item = group()
                if child.name == "a":
                    if child.has_attr("href"):
                        item.links.append(_link(child, base))
                else:
                    if _text(child):
                        item.paragraphs.append(_text(child))
                    item.links.extend(_link(a, base) for a in child.select("a[href]"))
            else:
                walk(child, path)

    walk(container, [])
    return [g for g in groups if g.paragraphs or g.links or g.table_rows]


def _identity(page: Tag) -> tuple[str | None, str | None]:
    for paragraph in page.select("p"):
        text = _text(paragraph) or ""
        if "Kortadress till denna sida:" in text:
            short = text.split("Kortadress till denna sida:", 1)[1].strip()
            match = PRODUCT_CODE.search(short)
            return short, match[1].upper() if match else None
    return None, None


def parse_product(
    snapshot: FetchSnapshot, product_code: str | None = None
) -> ProductPage:
    """Extract the inspected page while keeping its requested identity separate."""
    page, canonical = _page(snapshot)
    short, observed_code = _identity(page)
    release = page.select_one(".topIntro .link-icon-calendar")
    release_text = _text(release)
    date = re.search(r"\b\d{4}-\d{2}-\d{2}\b", release_text or "")
    marker = page.select_one('img[alt="Sveriges officiella statistik"]')
    agency = None
    for paragraph in page.select(".topIntro .box p"):
        if "ansvarar" in (_text(paragraph) or ""):
            anchor = paragraph.select_one("a[href]")
            agency = ResponsibleAgency(
                label=_text(anchor) or _text(paragraph) or "",
                link=_link(anchor, snapshot.final_url) if anchor else None,
            )
            break
    provenance = snapshot.provenance(canonical=canonical, selector="#pageContent")
    if product_code and observed_code and product_code != observed_code:
        provenance.warnings.append(
            f"Requested calendar code {product_code}; page short address identifies {observed_code}"
        )
    notices = []
    for block in page.select(":scope > .xhtmlText, :scope > .alert, :scope > .notice"):
        notices.extend(_text(p) for p in block.select("p") if _text(p))
    return ProductPage(
        provenance=provenance,
        product_code=product_code or observed_code,
        product_code_source="calendar"
        if product_code
        else "page_short_address"
        if observed_code
        else "unknown",
        title=_text(page.select_one("h1")),
        short_address=short,
        summary=_text(page.select_one(".ingress")),
        official_statistics_marker=marker.get("alt") if marker else None,
        next_publication_text=release_text,
        next_publication_date=date[0] if date else None,
        responsible_agency=agency,
        notices=notices,
        tags=[
            _link(a, snapshot.final_url) for a in page.select("a.category-item[href]")
        ],
        sections=_groups(page, snapshot.final_url),
    )


def parse_documentation(
    snapshot: FetchSnapshot, product_code: str | None = None
) -> DocumentationPage:
    page, canonical = _page(snapshot)
    heading = page.select_one("#_Dokumentation")
    section = heading.find_parent("section") if heading else None
    if heading and section is None:
        raise ValueError("Documentation heading has no section container")
    return DocumentationPage(
        provenance=snapshot.provenance(
            canonical=canonical,
            selector="#pageContent section[aria-labelledby='_Dokumentation']",
        ),
        title=_text(page.select_one("h1")),
        product_code=product_code or _identity(page)[1],
        has_documentation_section=section is not None,
        groups=_groups(section, snapshot.final_url) if section else [],
    )


def parse_subject_index(snapshot: FetchSnapshot) -> OfficialSubjects:
    page, canonical = _page(snapshot)
    subjects = []
    for anchor in page.select(".xhtmlText li a[href]"):
        match = re.match(r"^([A-Z]{2})\s+", _text(anchor) or "")
        if match:
            subjects.append(
                SubjectPage(
                    code=match[1],
                    index_link=_link(anchor, snapshot.final_url),
                    provenance=None,
                    title=None,
                )
            )
    if not subjects:
        raise ValueError("Subject index contains no coded subjects")
    return OfficialSubjects(
        provenance=snapshot.provenance(
            canonical=canonical, selector="#pageContent .xhtmlText li a"
        ),
        title=_text(page.select_one("h1")),
        introduction=_text(page.select_one(".ingress")),
        subjects=subjects,
        traversal_complete=False,
        unfetched_subject_urls=[s.index_link.url for s in subjects],
    )


def parse_subject(
    snapshot: FetchSnapshot,
    code: str,
    *,
    index_link: SourceLink | None = None,
    calendar_urls: dict[str, str] | None = None,
) -> SubjectPage:
    page, canonical = _page(snapshot)
    areas = []
    for box in page.select("li.expandable-box.expandable-box-content-filled"):
        cards = []
        for card in box.select(".expandable-box-content-item"):
            anchor = card.select_one("p.h2 a[href]")
            if anchor is None:
                raise ValueError(f"Subject {code} card missing product link")
            link = _link(anchor, snapshot.final_url)
            mapped_code = (calendar_urls or {}).get(canonical_url(link.url))
            observed_code = re.fullmatch(
                r"/([A-Z]{2}\d{4})/?", urlsplit(link.url).path, re.IGNORECASE
            )
            paragraphs = [
                p
                for p in card.select("p")
                if "h2" not in p.get("class", [])
                and "category-simple-list" not in p.get("class", [])
            ]
            agency = next(
                (p for p in paragraphs if "ansvar" in (_text(p) or "").lower()), None
            )
            summary = (
                " ".join(_text(p) or "" for p in paragraphs if p is not agency) or None
            )
            cards.append(
                ProductCard(
                    link=link,
                    product_code=mapped_code
                    or (observed_code[1].upper() if observed_code else None),
                    product_code_source="calendar_url_match"
                    if mapped_code
                    else "page"
                    if observed_code
                    else "unknown",
                    summary=summary,
                    responsible_agency_text=_text(agency),
                    tags_text=_text(card.select_one(".category-simple-list")),
                )
            )
        areas.append(
            StatisticalArea(
                label=_text(box.select_one(".expandable-box-heading")) or "",
                source_anchor=box.get("id"),
                source_url=None,
                products=cards,
            )
        )
    if not areas:
        raise ValueError(f"Subject {code} contains no statistical areas")
    return SubjectPage(
        code=code,
        index_link=index_link
        or SourceLink(
            text=_text(page.select_one("h1")),
            href=snapshot.request.url,
            url=snapshot.request.url,
        ),
        provenance=snapshot.provenance(
            canonical=canonical, selector="#pageContent .expandable-box"
        ),
        title=_text(page.select_one("h1")),
        areas=areas,
    )


def parse_documentation_index(snapshot: FetchSnapshot) -> DocumentationIndex:
    page, canonical = _page(snapshot)
    entries = []
    for heading in page.select("h2"):
        letter = _text(heading) or ""
        listing = heading.find_next_sibling("ul")
        if len(letter) == 1 and listing:
            entries.extend(
                DocumentationEntry(letter=letter, link=_link(a, snapshot.final_url))
                for a in listing.select("li a[href]")
            )
    if not entries:
        raise ValueError("Documentation index contains no entries")
    return DocumentationIndex(
        provenance=snapshot.provenance(
            canonical=canonical, selector="#pageContent h2 + ul li a"
        ),
        title=_text(page.select_one("h1")),
        introduction=_text(page.select_one(".xhtmlText")),
        entries=entries,
        traversal_complete=False,
        unfetched_page_urls=list(
            dict.fromkeys(canonical_url(e.link.url) for e in entries)
        ),
    )


def _fetch(context: ExtractionContext, url: str) -> FetchSnapshot:
    if urlsplit(url).hostname not in {"www.scb.se", "scb.se"}:
        raise ValueError(f"Target outside SCB allowlist: {url}")
    # Reuse short-address requests for documentation's canonical product links.
    target = canonical_url(url)
    processed, aliases = _ALIASES.setdefault(context, (set(), {}))
    for snapshot in context.available_snapshots():
        request_key = snapshot.request.key
        if request_key in processed:
            continue
        processed.add(request_key)
        aliases.setdefault(canonical_url(snapshot.final_url), snapshot)
        aliases.setdefault(canonical_url(snapshot.request.url), snapshot)
        if "html" in snapshot.content_type:
            soup = BeautifulSoup(snapshot.text, "lxml")
            canonical = soup.select_one('link[rel="canonical"][href]')
            if canonical:
                aliases.setdefault(
                    canonical_url(absolute_url(snapshot.final_url, canonical["href"])),
                    snapshot,
                )
    if target in aliases:
        return aliases[target]
    return context.fetch(FetchRequest(url=target))


def _calendar(context: ExtractionContext) -> dict[str, str]:
    records = json.loads(context.calendar_path.read_text(encoding="utf-8"))
    products = {}
    for record in records:
        code = record.get("product_code")
        if code and re.fullmatch(r"[A-Z]{2}\d{4}", code):
            url = record.get("product_url") or f"https://www.scb.se/{code}"
            if code in products and canonical_url(products[code]) != canonical_url(url):
                raise ValueError(f"Conflicting calendar URLs for {code}")
            products[code] = url
    if not products:
        raise ValueError("Calendar has no valid product codes")
    return products


class ProductsAdapter:
    def collect(self, context: ExtractionContext) -> SourceResult:
        result = SourceResult(source="products")
        try:
            products = _calendar(context)
        except (OSError, ValueError, TypeError) as exc:
            result.discovery_complete = False
            result.failures["index"] = str(exc)
            return result
        result.discovered = sorted(products)
        for code in result.discovered:
            try:
                page = parse_product(_fetch(context, products[code]), code)
                result.documents[code] = page
                result.warnings.extend(page.provenance.warnings)
            except (OSError, ValueError) as exc:
                result.failures[code] = str(exc)
        return result


class SubjectsAdapter:
    def collect(self, context: ExtractionContext) -> SourceResult:
        result = SourceResult(source="subjects", discovered=["index"])
        try:
            index = parse_subject_index(_fetch(context, SUBJECTS_URL))
        except (OSError, ValueError) as exc:
            result.discovery_complete = False
            result.failures["index"] = str(exc)
            return result
        result.discovered.extend(subject.code for subject in index.subjects)
        mapping = {}
        try:
            mapping = {
                canonical_url(url): code for code, url in _calendar(context).items()
            }
        except (OSError, ValueError, TypeError) as exc:
            result.warnings.append(f"Calendar URL linkage unavailable: {exc}")
        pending = []
        pages = []
        for subject in index.subjects:
            try:
                parsed = parse_subject(
                    _fetch(context, subject.index_link.url),
                    subject.code,
                    index_link=subject.index_link,
                    calendar_urls=mapping,
                )
                result.documents[subject.code] = parsed
                pages.append(parsed)
            except (OSError, ValueError) as exc:
                result.failures[subject.code] = str(exc)
                pending.append(subject.index_link.url)
                pages.append(subject)
        index.subjects = pages
        index.unfetched_subject_urls = pending
        index.traversal_complete = not pending
        result.documents["index"] = index
        return result


class DocumentationAdapter:
    def collect(self, context: ExtractionContext) -> SourceResult:
        result = SourceResult(source="documentation", discovered=["index"])
        try:
            index = parse_documentation_index(_fetch(context, DOCUMENTATION_URL))
        except (OSError, ValueError) as exc:
            result.discovery_complete = False
            result.failures["index"] = str(exc)
            return result
        pending = []
        for url in index.unfetched_page_urls:
            key = url_key(url)
            result.discovered.append(key)
            try:
                page = parse_documentation(_fetch(context, url))
                result.documents[key] = page
            except (OSError, ValueError) as exc:
                result.failures[key] = str(exc)
                pending.append(url)
        index.unfetched_page_urls = pending
        index.traversal_complete = not pending
        result.documents["index"] = index
        return result


ADAPTERS = {
    "products": ProductsAdapter(),
    "subjects": SubjectsAdapter(),
    "documentation": DocumentationAdapter(),
}
