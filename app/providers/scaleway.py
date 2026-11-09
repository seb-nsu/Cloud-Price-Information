"""Scaleway – Product Catalog API (öffentlicher Katalog, ohne Anmeldung).

GET https://api.scaleway.com/product-catalog/v2alpha1/public-catalog/products?page=N&page_size=M
Antwort: {"products": [...], "total_count": N}; je Produkt sku, service_category, product, variant,
description, locality (zone/region/datacenter/global), price.retail_price (currency_code, units,
nanos), unit_of_measure (unit, size), properties.hardware (cpu.virtual.count, ram.size in Byte,
gpu.count) und status.
Doku: https://www.scaleway.com/en/developers/api/product-catalog/public-catalog-api/
"""
from __future__ import annotations

import os

from ..http_util import build_url, get_json
from ..models import FetchResult, PriceRecord, ScopeCollector

LABEL = "Scaleway"
INFO = "Scaleway Product Catalog API · Netto in EUR"
BASE = "https://api.scaleway.com/product-catalog/v2alpha1/public-catalog/products"
PAGE_SIZE = 100
MAX_PAGES = 500

CATEGORY = {"compute": "Compute", "storage": "Storage", "network": "Netzwerk", "containers": "Kubernetes",
            "kubernetes": "Kubernetes", "managed databases": "Datenbank", "databases": "Datenbank",
            "ai": "KI", "generative apis": "KI", "managed inference": "KI"}
TYPE_CATEGORY = {"instance": "Compute", "elastic_metal": "Compute", "apple_silicon": "Compute",
                 "block_storage": "Storage", "object_storage": "Storage", "load_balancer": "Netzwerk",
                 "kubernetes": "Kubernetes", "managed_relational_database": "Datenbank",
                 "managed_redis_database": "Datenbank", "managed_mongodb": "Datenbank",
                 "managed_inference": "KI", "generative_apis": "KI"}
UNITS = {"hour": "Stunde", "month": "Monat", "year": "Jahr", "day": "Tag", "minute": "Minute",
         "gigabyte": "GB", "gigabyte_day": "GB-Tag", "request": "Anfragen", "token": "Token"}
SKIP_STATUS = {"end_of_sale", "end_of_life", "retired"}


def _money(m: dict | None) -> float | None:
    if not isinstance(m, dict):
        return None
    try:
        return int(m.get("units", 0) or 0) + int(m.get("nanos", 0) or 0) / 1e9
    except (TypeError, ValueError):
        return None


def _category(p: dict) -> str:
    props = p.get("properties") or {}
    for t, cat in TYPE_CATEGORY.items():
        if props.get(t) is not None:
            return cat
    return CATEGORY.get(str(p.get("service_category", "")).strip().lower(), "Sonstiges")


def _unit(p: dict) -> str:
    uom = p.get("unit_of_measure") or {}
    unit, size = uom.get("unit", ""), uom.get("size", 1) or 1
    desc = (p.get("description") or "").lower()
    if unit == "gigabyte" and ("month" in desc or "monat" in desc):
        label = "GB/Monat"
    elif unit == "gigabyte_day":
        return f"{size} GB-Tag" if size != 1 else "GB-Tag"
    else:
        label = UNITS.get(unit, unit or "Einheit")
    if unit in ("token", "request") and size and size != 1:
        return f"pro {int(size):,} {label}".replace(",", ".")
    return f"{size:g} {label}" if size not in (1, "1") else label


def _region(loc: dict | None) -> str:
    loc = loc or {}
    return loc.get("zone") or loc.get("region") or loc.get("datacenter") or "global"


def to_record(p: dict) -> PriceRecord | None:
    price = _money((p.get("price") or {}).get("retail_price"))
    if price is None or not p.get("sku"):
        return None
    if p.get("status") in SKIP_STATUS:
        return None
    hw = ((p.get("properties") or {}).get("hardware") or {})
    cpu, ram, gpu = hw.get("cpu") or {}, hw.get("ram") or {}, hw.get("gpu") or {}
    attrs = {"sku": p.get("sku"), "variant": p.get("variant"), "status": p.get("status"),
             "service_category": p.get("service_category")}
    vcpu = (cpu.get("virtual") or {}).get("count") or cpu.get("threads")
    if vcpu:
        attrs["vcpu"] = vcpu
    if ram.get("size"):
        attrs["ram_gb"] = round(int(ram["size"]) / 1024 ** 3, 2)
    if gpu.get("count"):
        attrs["gpu"] = gpu.get("count")
        attrs["gpu_type"] = gpu.get("type") or gpu.get("description")
    inst = (p.get("properties") or {}).get("instance") or {}
    if inst.get("range"):
        attrs["range"] = inst["range"]
    product = p.get("product") or p.get("variant") or p["sku"]
    if attrs.get("vcpu") and attrs.get("ram_gb"):
        product += f" · {attrs['vcpu']} vCPU / {attrs['ram_gb']:g} GB"
    return PriceRecord(key=p["sku"], category=_category(p), service=p.get("service_category") or "Scaleway",
                       product=product, region=_region(p.get("locality")), unit=_unit(p), price=price,
                       currency=((p.get("price") or {}).get("retail_price") or {}).get("currency_code", "EUR"),
                       attributes={k: v for k, v in attrs.items() if v not in (None, "")})


def fetch(cfg: dict, get=None) -> FetchResult:
    token = os.environ.get("SCW_SECRET_KEY", "").strip()
    if get is None:
        get = (lambda url: get_json(url, headers={"X-Auth-Token": token})) if token else get_json
    url = cfg.get("url", BASE)
    zones = {z.lower() for z in cfg.get("zones") or []}
    col = ScopeCollector()

    def all_products():
        out, page, total, dropped = [], 1, None, 0
        for _ in range(MAX_PAGES):
            data = get(build_url(url, {"page": page, "page_size": PAGE_SIZE}))
            items = data.get("products") or []
            total = data.get("total_count", total)
            for p in items:
                rec = to_record(p)
                if rec is None:
                    dropped += 1
                elif not zones or rec.region.lower() in zones or rec.region == "global" \
                        or any(rec.region.lower().startswith(z) for z in zones):
                    out.append(rec)
            if not items or (total is not None and page * PAGE_SIZE >= int(total)):
                break
            page += 1
        else:
            raise RuntimeError("Seitenlimit erreicht – Ergebnis unvollständig")
        if dropped:
            col.warnings.append(f"{dropped} Produkte ohne Preis oder abgekündigt übersprungen")
        return out

    col.run("katalog", all_products)
    return col.result(source=url)
