"""Hetzner Cloud – Hetzner Cloud API (`/v1/pricing` und `/v1/server_types`).

Die API verlangt ein API-Token (Umgebungsvariable HCLOUD_TOKEN). Ein Token mit Leserechten aus
einem beliebigen (auch leeren) Projekt genügt; Preise sind öffentliche Listenpreise.
Doku: https://docs.hetzner.cloud/#pricing-get-all-prices

Übernommen werden Netto-Preise (`net`) in EUR:
* Servertypen je Standort: Monatspreis (Hetzner rechnet stündlich ab, höchstens den Monatspreis)
* Volumes, Images/Snapshots (€/GB-Monat), Primary IPs, Load Balancer, Traffic über Inklusivvolumen
"""
from __future__ import annotations

import os

from ..http_util import build_url, get_json
from ..models import FetchResult, PriceRecord, ProviderNotConfigured, ScopeCollector

LABEL = "Hetzner Cloud"
INFO = "Hetzner Cloud API · Netto in EUR · API-Token erforderlich (HCLOUD_TOKEN)"
BASE = "https://api.hetzner.cloud/v1"


def _net(p: dict | None) -> float | None:
    if not isinstance(p, dict):
        return None
    try:
        return float(p.get("net"))
    except (TypeError, ValueError):
        return None


def parse_server_types(types: list[dict], currency: str, include_deprecated: bool = False) -> list[PriceRecord]:
    out = []
    for st in types:
        deprecated = bool(st.get("deprecated") or st.get("deprecation"))
        if deprecated and not include_deprecated:
            continue
        attrs = {k: st[k] for k in ("cores", "memory", "disk", "cpu_type", "architecture", "storage_type",
                                    "description") if st.get(k) not in (None, "")}
        name = st.get("name", "")
        label = f"{name.upper()} · {st.get('cores', '?')} vCPU / {st.get('memory', '?'):g} GB" \
            if isinstance(st.get("memory"), (int, float)) else name.upper()
        if st.get("cpu_type"):
            label += f" ({st['cpu_type']}, {st.get('architecture', 'x86')})"
        for p in st.get("prices") or []:
            loc = p.get("location", "")
            monthly, hourly = _net(p.get("price_monthly")), _net(p.get("price_hourly"))
            if monthly is None:
                continue
            a = dict(attrs, price_hourly=hourly, included_traffic_tb=round((p.get("included_traffic") or 0) / 1024 ** 4, 2))
            out.append(PriceRecord(key=f"server|{name}|{loc}", category="Compute", service="Cloud Server",
                                   product=label, region=loc, unit="pro Monat", price=monthly,
                                   currency=currency, attributes=a))
            tb = _net(p.get("price_per_tb_traffic"))
            if tb is not None:
                out.append(PriceRecord(key=f"traffic|{name}|{loc}", category="Netzwerk", service="Traffic",
                                       product=f"Traffic über Inklusivvolumen ({name.upper()}), je GB",
                                       region=loc, unit="GB", price=tb / 1000, currency=currency,
                                       attributes={"price_per_tb": tb}))
    return out


def parse_pricing(pricing: dict) -> list[PriceRecord]:
    cur = pricing.get("currency", "EUR")
    out = []
    vol = _net((pricing.get("volume") or {}).get("price_per_gb_month"))
    if vol is not None:
        out.append(PriceRecord(key="volume", category="Storage", service="Volumes", product="Volume (Blockspeicher)",
                               region="alle", unit="GB/Monat", price=vol, currency=cur))
    img = _net((pricing.get("image") or {}).get("price_per_gb_month"))
    if img is not None:
        out.append(PriceRecord(key="image", category="Storage", service="Snapshots",
                               product="Snapshot/Image (Speicher)", region="alle", unit="GB/Monat", price=img,
                               currency=cur))
    backup = (pricing.get("server_backup") or {}).get("percentage")
    for ip in pricing.get("primary_ips") or []:
        for p in ip.get("prices") or []:
            m = _net(p.get("price_monthly"))
            if m is not None:
                out.append(PriceRecord(key=f"primary_ip|{ip.get('type')}|{p.get('location')}", category="Netzwerk",
                                       service="Primary IPs", product=f"Primary IP ({ip.get('type')})",
                                       region=p.get("location", ""), unit="pro Monat", price=m, currency=cur))
    for ip in pricing.get("floating_ips") or []:
        for p in ip.get("prices") or []:
            m = _net(p.get("price_monthly"))
            if m is not None:
                out.append(PriceRecord(key=f"floating_ip|{ip.get('type')}|{p.get('location')}", category="Netzwerk",
                                       service="Floating IPs", product=f"Floating IP ({ip.get('type')})",
                                       region=p.get("location", ""), unit="pro Monat", price=m, currency=cur))
    for lb in pricing.get("load_balancer_types") or []:
        for p in lb.get("prices") or []:
            m = _net(p.get("price_monthly"))
            if m is not None:
                out.append(PriceRecord(key=f"lb|{lb.get('name')}|{p.get('location')}", category="Netzwerk",
                                       service="Load Balancer", product=f"Load Balancer {str(lb.get('name')).upper()}",
                                       region=p.get("location", ""), unit="pro Monat", price=m, currency=cur))
    if backup:
        for r in out:
            r.attributes.setdefault("server_backup_prozent", backup)
    return out


def fetch(cfg: dict, get=None) -> FetchResult:
    token = os.environ.get("HCLOUD_TOKEN", "").strip()
    if not token:
        raise ProviderNotConfigured("HCLOUD_TOKEN nicht gesetzt – siehe docs/ANBIETER.md")
    if get is None:
        get = lambda url: get_json(url, headers={"Authorization": f"Bearer {token}"})  # noqa: E731
    col = ScopeCollector()
    pricing = {}

    def prices():
        nonlocal pricing
        pricing = get(f"{BASE}/pricing").get("pricing") or {}
        if not pricing:
            raise RuntimeError("Antwort ohne 'pricing'")
        return parse_pricing(pricing)

    def servers():
        types, page = [], 1
        for _ in range(100):
            data = get(build_url(f"{BASE}/server_types", {"per_page": 50, "page": page}))
            types += data.get("server_types") or []
            nxt = ((data.get("meta") or {}).get("pagination") or {}).get("next_page")
            if not nxt:
                break
            page = nxt
        currency = pricing.get("currency", "EUR") if pricing else "EUR"
        locs = set(cfg.get("locations") or [])
        recs = parse_server_types(types, currency, bool(cfg.get("include_deprecated", False)))
        return [r for r in recs if not locs or r.region in locs]

    col.run("preise", prices)
    col.run("server", servers)
    return col.result(source=BASE)
