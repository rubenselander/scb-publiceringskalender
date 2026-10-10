"""Capture missing source fixtures; existing aliases stay frozen for review."""

import json
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scb_extract.core import ExtractionContext, FetchRequest, atomic_json

ROOT = Path("tests/fixtures")
SCB = "https://www.scb.se"
SOURCES = {
    "product_am0201": SCB + "/AM0201",
    "product_pr0701": SCB + "/PR0701",
    "product_be0101": SCB + "/BE0101",
    "subjects": SCB
    + "/om-scb/samordning-av-sveriges-officiella-statistik/officiell-statistik-efter-amne/",
    "subject_am": SCB + "/hitta-statistik/statistik-efter-amne/arbetsmarknad/",
    "subject_bl": SCB
    + "/hitta-statistik/statistik-efter-amne/befolkning-och-levnadsforhallanden/",
    "documentation": SCB + "/dokumentation/",
    "official_agencies": SCB
    + "/om-scb/samordning-av-sveriges-officiella-statistik/statistikansvariga-myndigheter/",
    "european_agencies": SCB
    + "/om-scb/samordning-av-europeisk-statistik-i-sverige/myndigheter-som-ansvarar-for-europeisk-statistik/",
    "agency_registry": "https://myndighetsregistret.scb.se/Myndighet",
    "official_products": SCB + "/sam-forum/hem/officiell-statistik/",
    "hvd": SCB
    + "/vara-tjanster/oppna-data/vardefulla-datamangder-hvd/vardefulla-datamangder--statistik/",
    "changes": SCB
    + "/sam-forum/hem/officiell-statistik/andringar-i-den-officiella-statistiken/",
}


def main():
    aliases = (
        json.loads((ROOT / "aliases.json").read_text(encoding="utf-8"))
        if (ROOT / "aliases.json").exists()
        else {}
    )
    with ExtractionContext(ROOT / "raw", timeout=35) as context:

        def capture(name, request):
            try:
                if name in aliases:
                    with ExtractionContext(ROOT / "raw", offline=True) as replay:
                        return replay.fetch(FetchRequest(**aliases[name]))
                snapshot = context.fetch(request)
                aliases[name] = request.model_dump()
                atomic_json(ROOT / "aliases.json", aliases)
                print(name, len(snapshot.content), flush=True)
                return snapshot
            except OSError as exc:
                print("FAILED", name, exc, flush=True)
                return None

        snapshots = {
            name: capture(name, FetchRequest(url=url)) for name, url in SOURCES.items()
        }
        for name in ["official_products", "changes"]:
            snapshot = snapshots[name]
            if snapshot is None:
                continue
            soup = BeautifulSoup(snapshot.text, "lxml")
            links = []
            for anchor in soup.select("#pageContent a[href]"):
                href = urljoin(snapshot.final_url, anchor["href"])
                if (
                    (
                        name == "official_products"
                        and ".xlsx" in href
                        and "Statistikprodukter" in anchor.get_text()
                    )
                    or (name == "changes" and ".pdf" in href)
                ) and href not in links:
                    links.append(href)
            for number, href in enumerate(links):
                capture(f"{name}_{number}", FetchRequest(url=href))
        registry = snapshots["agency_registry"]
        if registry:
            soup = BeautifulSoup(registry.text, "lxml")
            for option in soup.select("select option"):
                if option.get("value") not in ["2", "3", "4", "5", "6", "7"]:
                    continue
                value = option["value"]
                label = option.get_text(strip=True)
                group = capture(
                    f"registry_group_{value}",
                    FetchRequest(
                        url=registry.final_url + "/HamtaMynd",
                        method="POST",
                        json_body={"mynd": label},
                    ),
                )
                prepared = capture(
                    f"registry_prepare_{value}",
                    FetchRequest(
                        url=registry.final_url + "/PrepareDownload",
                        params={"format": "false", "myndgrupp": label},
                    ),
                )
                if prepared:
                    href = json.loads(prepared.text).replace("\\u0026", "&")
                    capture(
                        f"registry_export_{value}",
                        FetchRequest(url=urljoin(registry.final_url, href)),
                    )
                if group and value in ("2", "6", "7"):
                    row = BeautifulSoup(group.text, "lxml").select_one("tbody tr")
                    print("DETAIL ROW", value, str(row)[:1700], flush=True)


if __name__ == "__main__":
    main()
