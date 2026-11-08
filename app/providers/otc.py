"""Open Telekom Cloud – Price Calculator API (öffentlich, ohne Anmeldung).

Doku: https://docs.otc.t-systems.com/price-calculator/api-ref
Endpunkt (englisch, Dezimalpunkt): https://calculator.otc-service.com/en/open-telekom-price-api/
Antwort: response.result.services.<dienst> -> Liste von Preisdatensätzen
(id, productName, opiFlavour, priceAmount, currency, unit, region, …).

Die API ist eine Schnittstelle des Preisrechners und nur knapp dokumentiert. Das Modul ist
daher tolerant gebaut: Dezimalkomma wird akzeptiert, die Seitengröße wird nicht vorausgesetzt
(Abbruch erst bei leerer bzw. sich wiederholender Seite oder erreichter Gesamtzahl), und
verworfene Datensätze werden als Warnung gemeldet.
"""
from __future__ import annotations

import re

from ..http_util import build_url, get_json
from ..models import FetchResult, PriceRecord, ScopeCollector

LABEL = "Open Telekom Cloud"
INFO = "OTC Price Calculator API · Netto in EUR"
DEFAULT_URL = "https://calculator.otc-service.com/en/open-telekom-price-api/"
PAGE = 500          # angefragte Seitengröße (API-Obergrenze laut Doku)
MAX_PAGES = 200

CATEGORY = {
    "ecs": "Compute", "ecsnoc": "Compute", "memo": "Compute", "uhio": "Compute", "gpu": "Compute",
    "bms": "Compute", "deh": "Compute", "evs": "Storage", "obs": "Storage", "sfs": "Storage",
    "cbr": "Storage", "csbs": "Storage", "vbs": "Storage", "cce": "Kubernetes", "rds": "Datenbank",
    "rdbs": "Datenbank", "dds": "Datenbank", "dcs": "Datenbank", "gaussdb": "Datenbank",
    "eip": "Netzwerk", "elb": "Netzwerk", "nat": "Netzwerk", "vpn": "Netzwerk", "dc": "Netzwerk",
    "vpcep": "Netzwerk", "modelarts": "KI",
}
KEEP_ATTRS = ("id", "productId", "productid", "productName", "opiFlavour", "osUnit",
              "vCpu", "ram", "description", "productFamily", "productType")
TOTAL_KEYS = ("recordsTotal", "recordscount", "recordsCount", "total", "totalRecords", "count")


def parse_amount(v) -> float | None:
    """Akzeptiert 0.046, "0.046", "0,046", "1.234,56", "1,234.56"."""
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str) or not v.strip():
        return None
    s = re.sub(r"[^\d,.\-]", "", v)
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _records(container) -> list[dict]:
    """Extrahiert Datensätze robust aus verschiedenen Antwortformen."""
    if isinstance(container, list):
        return [r for r in container if isinstance(r, dict)]
    if isinstance(container, dict):
        for k in ("records", "data", "items"):
            if k in container:
                return _records(container[k])
        vals = list(container.values())
        if vals and all(isinstance(v, dict) for v in vals):
            return vals
    return []


def parse_page(data: dict, service: str) -> tuple[list[dict], int | None]:
    resp = data.get("response", data)
    http = resp.get("responseCode", resp.get("httpCode", 200))
    code = str(resp.get("code", "")).lower()
    if str(http) not in ("200", "") and code not in ("success",):
        raise RuntimeError(f"OTC-API Fehler {http}: {resp.get('message') or code}")
    services = (resp.get("result") or {}).get("services") or {}
    raw = services.get(service, []) if isinstance(services, dict) else services
    total = None
    stats = resp.get("stats") or {}
    if isinstance(stats, dict):
        for k in TOTAL_KEYS:
            if isinstance(stats.get(k), (int, str)) and str(stats[k]).isdigit():
                total = int(stats[k])
                break
    return _records(raw), total


def to_record(svc: str, region: str, r: dict) -> PriceRecord | None:
    price = parse_amount(r.get("priceAmount"))
    if price is None:
        return None
    rid = r.get("id") or f"{r.get('productName')}|{r.get('opiFlavour')}"
    reg = r.get("region") or region
    product = r.get("productName") or rid
    if r.get("opiFlavour") and r["opiFlavour"] not in product:
        product += f" ({r['opiFlavour']})"
    attrs = {k: r[k] for k in KEEP_ATTRS if r.get(k) not in (None, "")}
    for k, v in r.items():   # weitere Preisfelder (z. B. Reserved-Preise) mitnehmen
        if k != "priceAmount" and "price" in k.lower() and isinstance(v, (int, float, str)) and v != "":
            attrs[k] = v
    return PriceRecord(
        key=f"{svc}|{reg}|{rid}|{r.get('unit', '')}",
        category=CATEGORY.get(svc, "Sonstiges"),
        service=svc.upper(),
        product=product,
        region=reg,
        unit=r.get("unit") or r.get("priceUnit") or "h",
        price=price,
        currency=r.get("currency") or "EUR",
        attributes=attrs,
    )


def fetch_scope(url: str, svc: str, region: str, get, warnings: list[str]) -> list[PriceRecord]:
    out, offset, seen_ids, dropped = [], 0, set(), 0
    for _ in range(MAX_PAGES):
        page, total = parse_page(get(build_url(url, [
            ("serviceName[]", svc), ("region[]", region), ("limitMax", PAGE), ("limitFrom", offset)])), svc)
        if not page:
            break
        ids = tuple(str(r.get("id")) for r in page)
        if ids in seen_ids:     # API ignoriert limitFrom -> gleiche Seite erneut
            break
        seen_ids.add(ids)
        for r in page:
            rec = to_record(svc, region, r)
            if rec:
                out.append(rec)
            else:
                dropped += 1
        offset += len(page)
        if total is not None and offset >= total:
            break
    else:
        raise RuntimeError(f"Seitenlimit erreicht für {svc}/{region}")
    if dropped:
        warnings.append(f"{svc}/{region}: {dropped} Datensätze ohne lesbaren Preis verworfen")
    return out


def fetch(cfg: dict, get=get_json) -> FetchResult:
    url = cfg.get("url", DEFAULT_URL)
    col = ScopeCollector()
    for svc in cfg.get("services", ["ecs", "evs", "obs", "cce", "rds", "eip", "elb"]):
        for region in cfg.get("regions", ["eu-de"]):
            col.run(f"{svc}/{region}", lambda s=svc, r=region: fetch_scope(url, s, r, get, col.warnings))
    return col.result(source=url)
