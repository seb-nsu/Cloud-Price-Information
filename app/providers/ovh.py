"""OVHcloud – öffentlicher Bestellkatalog Public Cloud (ohne Anmeldung).

GET https://eu.api.ovh.com/v1/order/catalog/public/cloud?ovhSubsidiary=DE
Antwort: locale (currencyCode), plans[] und addons[] mit planCode, invoiceName, product, pricings[]
(price in 1/100.000.000 der Währung, capacities, intervalUnit) sowie blobs.technical (CPU, RAM, GPU).
Instanzen: planCode "<flavor>.consumption" (stündlich) bzw. "<flavor>.monthly.postpaid" (monatlich).
Suffixe ".3AZ" (Region Paris mit drei Zonen) und ".LZ.<Gebiet>" (Local Zones) werden als Region geführt.

**Experimentell:** Das Format ist nur teilweise dokumentiert und wurde nicht live geprüft. Der Anbieter
ist deshalb standardmäßig deaktiviert. Beim ersten Abruf Hinweise und Formatwächter im Abrufprotokoll prüfen.
"""
from __future__ import annotations

import re

from ..http_util import build_url, get_json
from ..models import FetchResult, PriceRecord, ScopeCollector

LABEL = "OVHcloud"
INFO = "OVHcloud Bestellkatalog Public Cloud · Netto · experimentell"
BASE = "https://eu.api.ovh.com/v1/order/catalog/public/cloud"
PRICE_SCALE = 1e8

CATEGORY_RULES = [
    (r"^databases?\.", "Datenbank"), (r"kube|mks|kubernetes", "Kubernetes"),
    (r"volume|storage|snapshot|backup|archive|object|swift|s3|image", "Storage"),
    (r"bandwidth|traffic|ip\b|publicip|floatingip|loadbalancer|octavia|gateway|network", "Netzwerk"),
    (r"^ai|notebook|training|deploy|inference|gpu-", "KI"),
]


def _category(plan_code: str, family: str) -> str:
    if family == "instance":
        return "Compute"
    for rx, cat in CATEGORY_RULES:
        if re.search(rx, plan_code, re.I):
            return cat
    return "Sonstiges"


def _region(plan_code: str) -> str:
    m = re.search(r"\.LZ\.([A-Za-z]+)", plan_code)
    if m:
        return f"lz-{m.group(1).lower()}"
    if plan_code.upper().endswith(".3AZ") or ".3az" in plan_code.lower():
        return "eu-west-par"
    return "ovh-eu"


def _pick_pricing(pricings: list[dict]) -> dict | None:
    def caps(p):
        return set(p.get("capacities") or [])
    for want in ("consumption", "renew"):
        for p in pricings:
            if want in caps(p) and isinstance(p.get("price"), (int, float)) and p.get("intervalUnit") != "none":
                return p
    return None


def _unit(p: dict, category: str, plan_code: str) -> str:
    iu = (p.get("intervalUnit") or "").lower()
    interval = p.get("interval") or 1
    base = {"hour": "Stunde", "month": "Monat", "day": "Tag", "year": "Jahr"}.get(iu, iu or "Einheit")
    if category == "Storage" and re.search(r"volume|storage|snapshot|backup|archive|object", plan_code, re.I):
        return f"GB/{base}"
    if category == "Netzwerk" and re.search(r"bandwidth|traffic", plan_code, re.I):
        return "GB"
    return f"pro {interval} {base}" if interval not in (1, "1") else f"pro {base}"


def _technical(item: dict, products: dict[str, dict]) -> dict:
    tech = ((item.get("blobs") or {}).get("technical")) or \
        (((products.get(item.get("product", "")) or {}).get("blobs") or {}).get("technical")) or {}
    out = {}
    cpu, mem, gpu = tech.get("cpu") or {}, tech.get("memory") or {}, tech.get("gpu") or {}
    if cpu.get("cores"):
        out["vcpu"] = cpu["cores"]
    if mem.get("size"):
        out["ram_gb"] = mem["size"]
    if gpu.get("number"):
        out["gpu"] = gpu["number"]
        out["gpu_model"] = gpu.get("model", "")
    return out


def parse_catalog(data: dict, warnings: list[str] | None = None) -> list[PriceRecord]:
    warnings = warnings if warnings is not None else []
    currency = (data.get("locale") or {}).get("currencyCode", "EUR")
    family_of: dict[str, str] = {}
    for plan in data.get("plans") or []:
        for fam in plan.get("addonFamilies") or []:
            for code in fam.get("addons") or []:
                family_of.setdefault(code, fam.get("name", ""))
    products = {p.get("name", ""): p for p in data.get("products") or []}
    out, seen, dropped = [], set(), 0
    for item in (data.get("addons") or []) + (data.get("plans") or []):
        code = item.get("planCode", "")
        if not code or code in seen or code in ("project", "credit"):
            continue
        seen.add(code)
        p = _pick_pricing(item.get("pricings") or [])
        if not p:
            dropped += 1
            continue
        fam = family_of.get(code, "")
        cat = _category(code, fam)
        tech = _technical(item, products)
        name = item.get("invoiceName") or code
        flavor = code.split(".")[0]
        product = name if flavor.lower() in name.lower() else f"{name} ({flavor})"
        if tech.get("vcpu") and tech.get("ram_gb"):
            product += f" · {tech['vcpu']} vCPU / {tech['ram_gb']:g} GB"
        if ".monthly" in code:
            product += " · Monatsabrechnung"
        attrs = {"planCode": code, "family": fam, "description": p.get("description", ""), **tech}
        out.append(PriceRecord(key=code, category=cat, service=fam or item.get("family") or "Public Cloud",
                               product=product, region=_region(code), unit=_unit(p, cat, code),
                               price=p["price"] / PRICE_SCALE, currency=currency,
                               attributes={k: v for k, v in attrs.items() if v not in (None, "")}))
    if dropped:
        warnings.append(f"{dropped} Katalogeinträge ohne laufenden Preis übersprungen")
    return out


def fetch(cfg: dict, get=get_json) -> FetchResult:
    url = build_url(cfg.get("url", BASE), {"ovhSubsidiary": cfg.get("subsidiary", "DE")})
    col = ScopeCollector()

    def catalog():
        data = get(url)
        if "plans" not in data:
            raise RuntimeError("Unerwartetes Katalogformat: 'plans' fehlt")
        recs = parse_catalog(data, col.warnings)
        wanted = set(cfg.get("kategorien") or [])
        return [r for r in recs if not wanted or r.category in wanted]

    col.run("katalog", catalog)
    return col.result(source=url)
