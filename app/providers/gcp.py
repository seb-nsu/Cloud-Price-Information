"""Google Cloud – Cloud Billing Catalog API.

Benötigt einen API-Key (Umgebungsvariable GCP_API_KEY) aus einem Projekt mit aktivierter
"Cloud Billing API". Der Key wird im Header `x-goog-api-key` übertragen, nicht in der URL.
Doku: https://cloud.google.com/billing/docs/how-to/get-pricing-information-api
"""
from __future__ import annotations

import json
import os

from ..http_util import build_url, open_stream
from ..models import (PT_ON_DEMAND, PT_RESERVED_1Y, PT_RESERVED_3Y, PT_SPOT, FetchResult, PriceRecord,
                      ProviderNotConfigured, ScopeCollector)

LABEL = "Google Cloud"
INFO = "Cloud Billing Catalog API · API-Key erforderlich (GCP_API_KEY)"
BASE = "https://cloudbilling.googleapis.com/v1"

FAMILY_CATEGORY = {"Compute": "Compute", "Storage": "Storage", "Network": "Netzwerk",
                   "License": "Sonstiges", "ApplicationServices": "Sonstiges"}
SERVICE_CATEGORY = {"Kubernetes Engine": "Kubernetes", "Cloud SQL": "Datenbank",
                    "Cloud Storage": "Storage", "Networking": "Netzwerk"}
USAGE_TYPE = {"OnDemand": PT_ON_DEMAND, "Preemptible": PT_SPOT,
              "Commit1Yr": PT_RESERVED_1Y, "Commit3Yr": PT_RESERVED_3Y}


def _money(m: dict) -> float:
    return int(m.get("units", 0) or 0) + int(m.get("nanos", 0) or 0) / 1e9


def default_get(url: str, key: str):
    with open_stream(url, accept="application/json", headers={"x-goog-api-key": key}) as fh:
        return json.loads(fh.read().decode("utf-8"))


def parse_skus(service_name: str, skus: list[dict], regions: set[str],
               include_global: bool, usage_types: set[str]) -> list[PriceRecord]:
    out = []
    for sku in skus:
        cat = sku.get("category", {})
        usage = cat.get("usageType", "")
        if usage_types and usage not in usage_types:
            continue
        sku_regions = sku.get("serviceRegions") or ["global"]
        wanted = [r for r in sku_regions if r in regions or (include_global and r == "global")]
        if not wanted or not sku.get("pricingInfo"):
            continue
        expr = sku["pricingInfo"][0].get("pricingExpression", {})
        unit = expr.get("usageUnitDescription") or expr.get("usageUnit", "")
        rates = expr.get("tieredRates", [])
        for rate in rates:
            price = _money(rate.get("unitPrice", {}))
            start = rate.get("startUsageAmount", 0) or 0
            product = sku.get("description", "")
            if len(rates) > 1:
                product += f" (ab {start:g})"
            for region in wanted:
                out.append(PriceRecord(
                    key=f"{sku.get('skuId')}|{region}|{start:g}",
                    category=SERVICE_CATEGORY.get(service_name)
                    or FAMILY_CATEGORY.get(cat.get("resourceFamily", ""), "Sonstiges"),
                    service=f"{service_name} · {cat.get('resourceGroup', '')}".rstrip(" ·"),
                    product=product,
                    region=region,
                    unit=unit,
                    price=price,
                    currency=rate.get("unitPrice", {}).get("currencyCode", ""),
                    price_type=USAGE_TYPE.get(usage, usage or PT_ON_DEMAND),
                    attributes={"skuId": sku.get("skuId"), "resourceFamily": cat.get("resourceFamily"),
                                "resourceGroup": cat.get("resourceGroup"), "usageType": usage,
                                "effectiveTime": sku["pricingInfo"][0].get("effectiveTime")},
                ))
    return out


def _paged(url: str, params: dict, item_key: str, get) -> list[dict]:
    items, token = [], None
    for _ in range(1000):
        p = dict(params)
        if token:
            p["pageToken"] = token
        data = get(build_url(url, p))
        items.extend(data.get(item_key, []))
        token = data.get("nextPageToken")
        if not token:
            return items
    raise RuntimeError("Seitenlimit erreicht – Ergebnis unvollständig")


def fetch(cfg: dict, get=None) -> FetchResult:
    key = os.environ.get("GCP_API_KEY", "").strip()
    if not key:
        raise ProviderNotConfigured("GCP_API_KEY nicht gesetzt – siehe README")
    if get is None:
        get = lambda url: default_get(url, key)  # noqa: E731
    currency = cfg.get("currency", "EUR")
    wanted_services = cfg.get("services", ["Compute Engine", *SERVICE_CATEGORY])
    regions = set(cfg.get("regions", ["europe-west3"]))
    global_services = set(cfg.get("global_services", ["Networking"]))
    usage_types = set(cfg.get("usage_types", ["OnDemand"]))

    # pageSize: max. 5000 laut API
    services = _paged(f"{BASE}/services", {"pageSize": 5000}, "services", get)
    by_name = {s.get("displayName"): s for s in services}
    col = ScopeCollector()
    for name in wanted_services:
        svc = by_name.get(name)
        if not svc:
            col.warnings.append(f"Dienst '{name}' nicht im Katalog gefunden")
            continue

        def one(name=name, svc=svc):
            skus = _paged(f"{BASE}/{svc['name']}/skus", {"currencyCode": currency, "pageSize": 5000}, "skus", get)
            recs = parse_skus(name, skus, regions, name in global_services, usage_types)
            bad = {r.currency for r in recs if r.currency != currency}
            if bad:
                raise RuntimeError(f"Unerwartete Währung(en) {bad} statt {currency}")
            return recs
        col.run(name, one)
    return col.result(source=BASE)
