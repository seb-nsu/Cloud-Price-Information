"""Microsoft Azure – Azure Retail Prices API (öffentlich, ohne Anmeldung).

Doku: https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices
Laut Microsoft ist USD die Abrechnungsbasis; Preise in anderen Währungen dienen der Orientierung.
"""
from __future__ import annotations

import urllib.parse

from ..http_util import build_url, get_json
from ..models import (PT_DEVTEST, PT_ON_DEMAND, PT_OTHER, PT_RESERVED_1Y, PT_RESERVED_3Y,
                      PT_SAVINGS_1Y, PT_SAVINGS_3Y, PT_SPOT, FetchResult, PriceRecord, ScopeCollector)

LABEL = "Microsoft Azure"
INFO = "Azure Retail Prices API · USD ist Abrechnungsbasis, EUR laut Microsoft Orientierungswert"
BASE = "https://prices.azure.com/api/retail/prices"
API_VERSION = "2023-01-01-preview"
PAGE_SIZE = 1000
MAX_PAGES = 1000  # Schutz vor Endlosschleifen (= 1 Mio. Einträge je Bereich)

CATEGORY = {
    "Virtual Machines": "Compute",
    "Storage": "Storage",
    "Azure Kubernetes Service": "Kubernetes",
    "Bandwidth": "Netzwerk",
    "SQL Database": "Datenbank",
}


def _category(service: str, family: str) -> str:
    if service in CATEGORY:
        return CATEGORY[service]
    if service.startswith("Azure Database") or family == "Databases":
        return "Datenbank"
    return {"Compute": "Compute", "Storage": "Storage", "Networking": "Netzwerk",
            "AI + Machine Learning": "KI"}.get(family, "Sonstiges")


def _odata_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_filter(service: str, region: str | None, price_type: str) -> str:
    parts = [f"serviceName eq {_odata_str(service)}", f"priceType eq {_odata_str(price_type)}"]
    if region:
        parts.append(f"armRegionName eq {_odata_str(region)}")
    return " and ".join(parts)


def price_type_of(item: dict) -> str:
    t = item.get("type") or item.get("priceType") or "Consumption"
    names = f"{item.get('skuName', '')} {item.get('meterName', '')}".lower()
    if t == "Reservation":
        term = (item.get("reservationTerm") or "").lower()
        return PT_RESERVED_3Y if term.startswith("3") else PT_RESERVED_1Y
    if t == "DevTestConsumption":
        return PT_DEVTEST
    if t == "Consumption":
        if "spot" in names or "low priority" in names:
            return PT_SPOT
        return PT_ON_DEMAND
    return PT_OTHER


def parse_items(items: list[dict], expected_currency: str | None = None,
                include_savings_plan: bool = False) -> list[PriceRecord]:
    out = []
    for it in items:
        if expected_currency and it.get("currencyCode") and it["currencyCode"] != expected_currency:
            raise RuntimeError(f"Unerwartete Währung {it['currencyCode']} statt {expected_currency} "
                               f"(Paging-Parameter verloren?) – Abruf abgebrochen")
        ptype = price_type_of(it)
        term = it.get("reservationTerm") or ""
        tier = it.get("tierMinimumUnits", 0) or 0
        key = "|".join(str(x) for x in (
            it.get("meterId"), it.get("skuId"), it.get("armRegionName"),
            it.get("type") or it.get("priceType"), term, tier))
        product = " · ".join(x for x in (it.get("productName"), it.get("skuName"), it.get("meterName")) if x)
        if tier:
            product += f" (ab {tier:g} Einh.)"
        attrs = {k: it[k] for k in ("armSkuName", "skuName", "meterName", "productName", "serviceFamily",
                                    "effectiveStartDate", "skuId", "meterId", "reservationTerm") if it.get(k)}
        common = dict(category=_category(it.get("serviceName", ""), it.get("serviceFamily", "")),
                      service=it.get("serviceName", ""),
                      region=it.get("armRegionName") or it.get("location") or "global",
                      unit=it.get("unitOfMeasure", ""), currency=it.get("currencyCode", ""))
        out.append(PriceRecord(key=key, product=product, price=float(it.get("retailPrice", it.get("unitPrice", 0)) or 0),
                               price_type=ptype, attributes=attrs, **common))
        if include_savings_plan and ptype == PT_ON_DEMAND:
            for sp in it.get("savingsPlan") or []:
                years = str(sp.get("term", "")).split()[0]
                out.append(PriceRecord(key=f"{key}|SP{years}", product=product,
                                       price=float(sp.get("retailPrice", sp.get("unitPrice", 0)) or 0),
                                       price_type=PT_SAVINGS_3Y if years == "3" else PT_SAVINGS_1Y,
                                       attributes=attrs, **common))
    return out


def _skip_from(next_link: str) -> int | None:
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(next_link).query)
    for k in ("$skip", "%24skip"):
        if k in q:
            return int(q[k][0])
    return None


def fetch_scope(service: str, region: str | None, ptype: str, currency: str, get,
                include_savings_plan: bool) -> list[PriceRecord]:
    params = {"api-version": API_VERSION, "currencyCode": f"'{currency}'", "meterRegion": "'primary'",
              "$filter": build_filter(service, region, ptype)}
    records, skip = [], 0
    for _ in range(MAX_PAGES):
        url = build_url(BASE, {**params, "$skip": skip} if skip else params)
        data = get(url)
        items = data.get("Items", [])
        records.extend(parse_items(items, currency, include_savings_plan))
        nxt = data.get("NextPageLink")
        if not nxt or not items:
            return records
        # Folge-URL selbst bauen, damit Währung/Filter/API-Version sicher erhalten bleiben
        new_skip = _skip_from(nxt)
        skip = new_skip if new_skip is not None and new_skip > skip else skip + len(items)
    raise RuntimeError(f"Seitenlimit ({MAX_PAGES}) erreicht – Ergebnis unvollständig")


def fetch(cfg: dict, get=get_json) -> FetchResult:
    currency = cfg.get("currency", "EUR")
    regions = cfg.get("regions") or [None]
    sp = bool(cfg.get("savings_plan", False))
    col = ScopeCollector()
    for service in cfg.get("services", list(CATEGORY)):
        # Datenübertragung ist zonen- statt regionsbasiert -> ohne Regionsfilter
        for region in ([None] if service == "Bandwidth" else regions):
            for ptype in cfg.get("price_types", ["Consumption"]):
                scope = f"{service}/{region or 'alle'}/{ptype}"
                col.run(scope, lambda s=service, r=region, p=ptype: fetch_scope(s, r, p, currency, get, sp))
    return col.result(source=BASE)
