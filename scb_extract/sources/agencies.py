"""Faithful agency lists, ordered regulation text and full registry groups."""

import csv
import io
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag
from pydantic import BaseModel

from scb_extract.core import (
    ExtractionContext,
    FetchRequest,
    FetchSnapshot,
    SourceAdapter,
    SourceResult,
    absolute_url,
)
from scb_extract.models.agencies_law import (
    AgencyRegistryDocument,
    EuropeanAgenciesDocument,
    LawBlock,
    OfficialAgenciesDocument,
    OfficialAgency,
    OfficialStatisticsRegulationDocument,
    RegistryAgency,
    RegistryGroup,
    SourceField,
    SourceLink,
    SourceStatement,
    Subject,
)

OFFICIAL_URL = "https://www.scb.se/om-scb/samordning-av-sveriges-officiella-statistik/statistikansvariga-myndigheter/"
EUROPEAN_URL = "https://www.scb.se/om-scb/samordning-av-europeisk-statistik-i-sverige/myndigheter-som-ansvarar-for-europeisk-statistik/"
REGULATION_URL = "https://data.riksdagen.se/dokument/sfs-2001-100.html"
REGISTRY_URL = "https://myndighetsregistret.scb.se/Myndighet"


def _text(node: Tag) -> str:
    return node.get_text(" ", strip=True)


def _required(soup: Tag, selector: str) -> Tag:
    node = soup.select_one(selector)
    if node is None:
        raise ValueError(f"Missing source element: {selector}")
    return node


def _links(node: Tag, snapshot: FetchSnapshot) -> list[SourceLink]:
    return [
        SourceLink(text=_text(link), url=absolute_url(snapshot.final_url, link["href"]))
        for link in node.select("a[href]")
    ]


def _statements(nodes: list[Tag], snapshot: FetchSnapshot) -> list[SourceStatement]:
    return [
        SourceStatement(text=_text(node), links=_links(node, snapshot))
        for node in nodes
        if _text(node)
    ]


def _stated_count(text: str) -> int | None:
    match = re.search(
        r"\b(\d+)\s+(?:svenska\s+)?(?:statistikansvariga\s+)?myndigheter", text
    )
    return int(match[1]) if match else None


def _flush_area(pending: list[str], subjects: list[Subject]) -> None:
    area = "".join(pending).strip()
    if area and subjects:
        subjects[-1].statistical_areas.append(area)
    pending.clear()


def parse_official_agencies(snapshot: FetchSnapshot) -> OfficialAgenciesDocument:
    """Retain subjects across paragraphs, including br nested inside strong."""
    soup = BeautifulSoup(snapshot.text, "lxml")
    content = _required(soup, "#pageContent .xhtmlText")
    headings = content.find_all("h3", recursive=False)
    if not headings:
        raise ValueError("No official agency sections")
    scope = list(soup.select("#pageContent .ingress"))
    for node in content.children:
        if isinstance(node, Tag) and node.name in ("hr", "h3"):
            break
        if isinstance(node, Tag) and node.name == "p":
            scope.append(node)
    agencies = []
    for heading in headings:
        subjects: list[Subject] = []
        statistics_link = None
        for paragraph in heading.next_siblings:
            if not isinstance(paragraph, Tag):
                continue
            if paragraph.name in ("hr", "h3", "h2"):
                break
            if paragraph.name != "p":
                continue
            for link in paragraph.select("a[href]"):
                if _text(link).startswith("Statistik hos"):
                    statistics_link = link
            pending: list[str] = []

            for child in paragraph.children:
                if isinstance(child, Tag) and child.name == "strong":
                    _flush_area(pending, subjects)
                    subject_name = _text(child)
                    if not subject_name:
                        raise ValueError(f"Blank subject in {_text(heading)}")
                    subjects.append(
                        Subject(subject_name=subject_name, statistical_areas=[])
                    )
                elif isinstance(child, Tag) and child.name == "br":
                    _flush_area(pending, subjects)
                elif subjects:
                    pending.append(
                        child.get_text() if isinstance(child, Tag) else str(child)
                    )
            _flush_area(pending, subjects)
        if statistics_link is None or not subjects:
            raise ValueError(f"Incomplete official agency: {_text(heading)}")
        agencies.append(
            OfficialAgency(
                agency_name=_text(heading),
                statistics_link_text=_text(statistics_link),
                statistics_url=absolute_url(
                    snapshot.final_url, statistics_link["href"]
                ),
                subjects=subjects,
            )
        )
    statements = _statements(scope, snapshot)
    count = _stated_count(" ".join(statement.text for statement in statements))
    return OfficialAgenciesDocument(
        provenance=snapshot.provenance(selector="#pageContent .xhtmlText"),
        page_title=_text(_required(soup, "#pageContent h1")),
        scope_statements=statements,
        stated_agency_count=count,
        coverage="complete" if count is None or count == len(agencies) else "partial",
        agencies=agencies,
    )


def parse_european_agencies(snapshot: FetchSnapshot) -> EuropeanAgenciesDocument:
    soup = BeautifulSoup(snapshot.text, "lxml")
    content = _required(soup, "#pageContent .xhtmlText")
    heading = next(
        (h for h in content.select("h2") if _text(h).startswith("Svenska myndigheter")),
        None,
    )
    if heading is None:
        raise ValueError("Missing European agency list heading")
    listing = heading.find_next_sibling("ul")
    if listing is None:
        raise ValueError("Missing European agency list")
    names = [_text(item) for item in listing.find_all("li", recursive=False)]
    if not names or any(not name for name in names):
        raise ValueError("Empty European agency list member")
    scope = list(soup.select("#pageContent .ingress"))
    for node in content.children:
        if node is heading:
            break
        if isinstance(node, Tag) and node.name == "p":
            scope.append(node)
    bank_heading = listing.find_next_sibling("h2")
    if bank_heading is None or not _text(bank_heading).startswith("Centralbank"):
        raise ValueError("Missing separate central bank section")
    bank_paragraphs = []
    for node in bank_heading.next_siblings:
        if isinstance(node, Tag) and node.name.startswith("h"):
            break
        if isinstance(node, Tag) and node.name == "p":
            bank_paragraphs.append(node)
    statements = _statements(scope, snapshot)
    count = _stated_count(" ".join(statement.text for statement in statements))
    return EuropeanAgenciesDocument(
        provenance=snapshot.provenance(selector="#pageContent .xhtmlText"),
        page_title=_text(_required(soup, "#pageContent h1")),
        scope_statements=statements,
        stated_agency_count=count,
        coverage="complete" if count is None or count == len(names) else "partial",
        list_heading=_text(heading),
        agency_names=names,
        central_bank_heading=_text(bank_heading),
        central_bank_statement="\n".join(_text(node) for node in bank_paragraphs),
        central_bank_links=[
            link for node in bank_paragraphs for link in _links(node, snapshot)
        ],
    )


def parse_regulation(snapshot: FetchSnapshot) -> OfficialStatisticsRegulationDocument:
    """Capture direct ordered law children; inline and pre whitespace is retained."""
    soup = BeautifulSoup(snapshot.text, "lxml")
    body = _required(soup, "body")
    title = _text(_required(body, "h2"))
    metadata = []
    for label in body.find_all("b", recursive=False):
        value = []
        for node in label.next_siblings:
            if isinstance(node, Tag) and node.name in ("br", "b", "hr"):
                break
            value.append(node.get_text() if isinstance(node, Tag) else str(node))
        metadata.append(
            SourceField(
                label=_text(label), value="".join(value).strip().lstrip(":").strip()
            )
        )
    # Link labels and URL values are both retained, without an invented API route.
    for link in body.find_all("a", recursive=False):
        metadata.append(
            SourceField(
                label=_text(link), value=absolute_url(snapshot.final_url, link["href"])
            )
        )
    number = next((field.value for field in metadata if field.label == "SFS nr"), None)
    if not number:
        raise ValueError("Missing SFS number")
    law = next(
        (
            node
            for node in body.find_all("div", recursive=False)
            if "sfstoc" not in node.get("class", [])
        ),
        None,
    )
    if law is None:
        raise ValueError("Missing regulation full text")
    blocks = []
    for node in law.children:
        if isinstance(node, Tag):
            if node.name in ("style", "script"):
                continue
            text = node.get_text(separator="\n" if node.name == "br" else "")
            if node.name == "br":
                text = "\n"
            tag = node.name
        elif isinstance(node, NavigableString):
            text, tag = str(node), "text"
        else:
            continue
        # Empty anchor-only elements have no legal prose; retain every nonempty span.
        if text:
            blocks.append(LawBlock(source_tag=tag, text=text))
    if not any(block.source_tag == "pre" for block in blocks):
        raise ValueError("Missing aligned regulation appendix")
    return OfficialStatisticsRegulationDocument(
        provenance=snapshot.provenance(selector="body > div:not(.sfstoc)"),
        page_title=title,
        sfs_number=number,
        metadata=metadata,
        coverage="complete",
        content_blocks=blocks,
    )


def parse_registry_group(
    snapshot: FetchSnapshot, option: str, name: str
) -> RegistryGroup:
    """Read the full tbody, including hidden cells and blank-name rows."""
    soup = BeautifulSoup(snapshot.text, "lxml")
    table = _required(soup, "table#ResultTable")
    columns = [_text(cell) for cell in _required(table, "thead tr").select("th")]
    if not columns or any(not column for column in columns):
        raise ValueError(f"Missing registry columns for group {option}")
    agencies = []
    for row in table.select("tbody tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) != len(columns):
            raise ValueError(f"Malformed registry row in group {option}")
        agencies.append(
            RegistryAgency(
                fields=[
                    SourceField(label=label, value=cell.get_text())
                    for label, cell in zip(columns, cells, strict=True)
                ]
            )
        )
    if not agencies:
        raise ValueError(f"No registry rows for group {option}")
    return RegistryGroup(
        group_option_value=option,
        group_name=name,
        provenance=snapshot.provenance(selector="#ResultTable tbody tr"),
        retrieval_surface="html_table",
        source_columns=columns,
        source_row_count=len(agencies),
        coverage="complete",
        agencies=agencies,
    )


def parse_registry_export(
    snapshot: FetchSnapshot, option: str, name: str
) -> RegistryGroup:
    rows = list(
        csv.reader(
            io.StringIO(snapshot.text.lstrip("\ufeff"), newline=""), delimiter="\t"
        )
    )
    if len(rows) < 2 or "Namn" not in rows[0] or len(set(rows[0])) != len(rows[0]):
        raise ValueError(f"Missing registry export data for group {option}")
    columns = rows[0]
    agencies = []
    for row in rows[1:]:
        if len(row) != len(columns):
            raise ValueError(f"Malformed registry export row in group {option}")
        agencies.append(
            RegistryAgency(
                fields=[
                    SourceField(label=label, value=value)
                    for label, value in zip(columns, row, strict=True)
                ]
            )
        )
    return RegistryGroup(
        group_option_value=option,
        group_name=name,
        provenance=snapshot.provenance(selector="TSV rows"),
        retrieval_surface="tsv_download",
        source_columns=columns,
        source_row_count=len(agencies),
        coverage="complete",
        agencies=agencies,
    )


def parse_registry_detail(snapshot: FetchSnapshot) -> list[SourceField]:
    soup = BeautifulSoup(snapshot.text, "lxml")
    detail = _required(soup, "#_MyndDetalj .panel-body")
    fields = []
    for paragraph in detail.select("p"):
        label = paragraph.find("strong")
        if label is None:
            continue
        values = []
        for node in label.next_siblings:
            if isinstance(node, Tag) and node.name == "br":
                values.append("\n")
            else:
                values.append(node.get_text() if isinstance(node, Tag) else str(node))
        # HTML indentation is formatting; the published address line breaks remain.
        lines = [line.strip() for line in "".join(values).splitlines() if line.strip()]
        fields.append(
            SourceField(label=_text(label).rstrip(":"), value="\n".join(lines))
        )
    if not fields:
        raise ValueError("Missing registry detail fields")
    return fields


@dataclass(frozen=True)
class PageAdapter:
    source: str
    url: str
    parser: Callable[[FetchSnapshot], BaseModel]

    def collect(self, context: ExtractionContext) -> SourceResult:
        result = SourceResult(source=self.source, discovered=["index"])
        try:
            document = self.parser(context.fetch(FetchRequest(url=self.url)))
            if document.coverage != "complete":
                raise ValueError("Source stated count disagrees with parsed count")
            result.documents["index"] = document
        except (OSError, ValueError) as exc:
            result.failures["index"] = str(exc)
            result.discovery_complete = False
        return result


class RegistryAdapter:
    def collect(self, context: ExtractionContext) -> SourceResult:
        result = SourceResult(source="agency_registry", discovered=["index"])
        try:
            index = context.fetch(FetchRequest(url=REGISTRY_URL))
            soup = BeautifulSoup(index.text, "lxml")
            options = [
                (option["value"], _text(option))
                for option in _required(soup, "select#MyId").select("option[value]")
                if option["value"] != "1"
            ]
            if len(options) != 6 or len({value for value, _ in options}) != 6:
                raise ValueError("Registry index does not expose all six groups")
        except (OSError, ValueError) as exc:
            result.failures["index"] = str(exc)
            result.discovery_complete = False
            return result
        groups = []
        errors = []
        for option, name in options:
            try:
                table_snapshot = context.fetch(
                    FetchRequest(
                        url=REGISTRY_URL + "/HamtaMynd",
                        method="POST",
                        json_body={"mynd": name},
                    )
                )
                table = parse_registry_group(table_snapshot, option, name)
                try:
                    prepare = context.fetch(
                        FetchRequest(
                            url=REGISTRY_URL + "/PrepareDownload",
                            params={"format": "false", "myndgrupp": name},
                        )
                    )
                    path = json.loads(prepare.text)
                    if not isinstance(path, str):
                        raise TypeError(
                            "Registry download preparation did not return a path"
                        )
                    download_url = absolute_url(
                        prepare.final_url, path.replace("\\u0026", "&")
                    )
                    if (
                        urlsplit(download_url).netloc != urlsplit(REGISTRY_URL).netloc
                        or urlsplit(download_url).path.lower() != "/myndighet/download"
                    ):
                        raise ValueError("Unexpected registry download path")
                    export = parse_registry_export(
                        context.fetch(FetchRequest(url=download_url)), option, name
                    )
                    if len(export.agencies) != len(table.agencies):
                        raise ValueError("Registry export and grid row counts disagree")
                    names = [
                        next(
                            field.value
                            for field in agency.fields
                            if field.label == "Namn"
                        )
                        for agency in table.agencies
                    ]
                    if len(names) != len(set(names)):
                        raise ValueError(
                            "Ambiguous duplicate registry names prevent export/grid alignment"
                        )
                    for exported, grid in zip(
                        export.agencies, table.agencies, strict=True
                    ):
                        exported_fields = {
                            field.label: field.value for field in exported.fields
                        }
                        grid_fields = {
                            field.label: field.value for field in grid.fields
                        }
                        if exported_fields["Namn"] != grid_fields["Namn"]:
                            raise ValueError(
                                "Registry export and grid ordering disagree"
                            )
                        if (
                            "Organisationsnr" in grid_fields
                            and exported_fields.get("Organisationsnr")
                            != grid_fields["Organisationsnr"]
                        ):
                            raise ValueError(
                                "Registry export and grid organisation identifiers disagree"
                            )
                        exported.fields.extend(
                            field
                            for field in grid.fields
                            if field.label not in exported_fields
                        )
                    export.table_provenance = table.provenance
                    groups.append(export)
                except (OSError, ValueError, TypeError) as exc:
                    result.warnings.append(
                        f"Group {option} export unavailable; trying every detail: {exc}"
                    )
                    for agency in table.agencies:
                        fields = {field.label: field.value for field in agency.fields}
                        if option == "6":
                            body = {
                                "peorgnr": None,
                                "cfarnr": fields["CfarNr"],
                                "lopnr": None,
                            }
                        elif option == "7":
                            body = {
                                "peorgnr": None,
                                "cfarnr": None,
                                "lopnr": fields["LopNr"],
                            }
                        else:
                            body = {
                                "peorgnr": "16"
                                + fields["Organisationsnr"].replace("-", ""),
                                "cfarnr": None,
                                "lopnr": None,
                            }
                        detail = context.fetch(
                            FetchRequest(
                                url=REGISTRY_URL + "/HamtaEnMynd",
                                method="POST",
                                json_body=body,
                            )
                        )
                        agency.detail_fields = parse_registry_detail(detail)
                        detail_name = next(
                            (
                                field.value
                                for field in agency.detail_fields
                                if field.label == "Namn"
                            ),
                            None,
                        )
                        if detail_name != fields.get("Namn"):
                            raise ValueError(
                                "Registry detail name disagrees with its grid row"
                            )
                        agency.detail_provenance = detail.provenance(
                            selector="#_MyndDetalj .panel-body p"
                        )
                    groups.append(table)
            except (OSError, ValueError, KeyError) as exc:
                errors.append(f"Group {option} ({name}): {exc}")
        if errors:
            result.failures["index"] = "; ".join(errors)
            result.discovery_complete = False
        else:
            result.documents["index"] = AgencyRegistryDocument(
                provenance=index.provenance(selector="select#MyId option[value]"),
                page_title="Myndighetsregistret",
                coverage="complete",
                groups=groups,
            )
        return result


ADAPTERS: dict[str, SourceAdapter] = {
    "official_agencies": PageAdapter(
        "official_agencies", OFFICIAL_URL, parse_official_agencies
    ),
    "european_agencies": PageAdapter(
        "european_agencies", EUROPEAN_URL, parse_european_agencies
    ),
    "regulation": PageAdapter("regulation", REGULATION_URL, parse_regulation),
    "agency_registry": RegistryAdapter(),
}
