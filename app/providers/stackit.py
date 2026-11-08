"""STACKIT – Price Information Model (PIM) API (öffentlich, ohne Anmeldung).

https://pim.api.stackit.cloud/v1/skus liefert alle SKUs inkl. Stundenpreis
und Monatspreis (720 h). Die Datenmenge ist klein, daher wird alles übernommen.
"""
from __future__ import annotations

from ..http_util import get_json
from ..models import FetchResult, PriceRecord

LABEL = "STACKIT"
INFO = "STACKIT PIM API · Netto-Listenpreise in EUR"
DEFAULT_URL = "https://pim.api.stackit.cloud/v1/skus"

CATEGORY = {
    "Compute Engine": "Compute", "Compute Engine GPU": "Compute",
    "Confidential Computing": "Compute", "Database": "Datenbank", "Storage": "Storage",
    "Archiving": "Storage", "Network": "Netzwerk", "Data & AI": "KI",
}
CURRENCY = {"€": "EUR", "$": "USD"}


def _yes(v) -> bool:
    return str(v or "").strip().lower().startswith("y")


def parse(data: dict, cfg: dict) -> tuple[list[PriceRecord], list[str]]:
    regions = {r.lower() for r in cfg.get("regions") or []}
    include_deprecated = bool(cfg.get("include_deprecated", False))
    include_hidden = bool(cfg.get("include_hidden", False))
    out, skipped = [], 0
    for s in data.get("services", []):
        region = (s.get("region") or "global").lower()
        if regions and region not in regions and region != "global":
            continue
        if _yes(s.get("deprecated")) and not include_deprecated:
            continue
        if not include_hidden and s.get("priceListVisibility") and not _yes(s.get("priceListVisibility")):
            continue
        raw = s.get("price")
        try:
            price = float(raw)
        except (TypeError, ValueError):
            skipped += 1      # leerer/ungültiger Preis wird nicht als 0 € übernommen
            continue
        group = s.get("generalProductGroup") or ""
        category = "Kubernetes" if "Kubernetes" in group else CATEGORY.get(s.get("category", ""), "Sonstiges")
        attrs = {k: s.get(k) for k in ("id", "sku", "monthlyPrice", "maturityModelState",
                                       "generalProductGroup", "unit") if s.get(k)}
        for k in ("flavor", "vCPU", "ram", "hardware", "metro"):
            if (s.get("attributes") or {}).get(k) not in (None, ""):
                attrs[k] = s["attributes"][k]
        out.append(PriceRecord(
            key=s.get("sku") or s.get("id"),
            category=category,
            service=f"{s.get('category', '')} · {group}".strip(" ·"),
            product=s.get("title") or s.get("name") or "",
            region=region,
            unit=s.get("unitBilling") or s.get("unit") or "",
            price=price,
            currency=CURRENCY.get(s.get("currency", "€"), s.get("currency", "EUR")),
            attributes=attrs,
            scope="alle",
        ))
    warnings = [f"{skipped} SKUs ohne gültigen Preis übersprungen"] if skipped else []
    return out, warnings


def fetch(cfg: dict, get=get_json) -> FetchResult:
    url = cfg.get("url", DEFAULT_URL)
    data = get(url)
    recs, warnings = parse(data, cfg)
    return FetchResult(records=recs, source=url, source_date=data.get("lastUpdatedAt", ""),
                       warnings=warnings, scopes_ok={"alle"})
