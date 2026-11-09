"""Lesende JSON-API (gleicher Zugangsschutz wie die Oberfläche).

Alle Endpunkte liefern JSON; Preise in Originalwährung (`price`, `currency`) und normiert in EUR
(`price_norm` je `unit_norm`). Übersicht unter GET /api, Beschreibung in docs/API.md.
"""
from __future__ import annotations

import json

from . import __version__, analysis, compare, fx, report, sovereignty
from .db import Database
from .models import CATEGORIES, PRICE_TYPES
from .providers import PROVIDERS

MAX_LIMIT = 5000
PRICE_FIELDS = ("provider", "key", "category", "service", "product", "region", "unit", "price", "currency",
                "price_type", "valid_from", "last_checked", "source_date", "scope", "unit_norm", "price_eur",
                "price_norm", "vcpu", "ram_gb", "os", "gpu")
ARCHIVE_FIELDS = ("provider", "key", "category", "service", "product", "region", "unit", "price", "currency",
                  "price_type", "valid_from", "valid_to", "reason", "new_price", "run_id", "unit_norm", "price_eur",
                  "price_norm")

ENDPOINTS = {
    "/api/anbieter": "Anbieter mit Status, Anzahl Preisen, letztem Abruf und Rechtsraum",
    "/api/preise": "Aktuelle Preise; Filter provider, category, region, price_type, unit_norm, q, key, souv; "
                   "Paging limit (max. 5000), offset; attributes=1 liefert die Rohattribute",
    "/api/archiv": "Archivierte Preise; Filter wie /api/preise sowie run_id",
    "/api/verlauf": "Preisverlauf eines Preises: provider, key",
    "/api/vergleich": "VM-Vergleich: vcpu, ram, os, region, top, gpu, souv",
    "/api/vergleich/<art>": "Kategorie-Vergleich (speicher, traffic, kubernetes, datenbanken): provider, typ, klasse, "
                            "engine, region, q, top, souv, stufe, kostenlos, vcpu, ram",
    "/api/rabatte": "Reservierung/Commitments/Savings Plans vs. On-Demand: provider, category, price_type, souv, min, q",
    "/api/bericht": "Preisänderungs-Bericht: zeitraum (2026-10, 2026-Q4, 2026), provider, category, top",
    "/api/szenarien": "Liste der Szenarien",
    "/api/szenario/<id>": "Szenario mit Positionen, Summen je Variante und Hochrechnung (monate, wachstum)",
    "/api/kurse": "Aktuelle Wechselkurse und Verlauf: waehrung, seit (JJJJ-MM-TT)",
    "/api/status": "Laufende Abrufe je Anbieter",
}


def _rows(con, sql: str, args: list, fields: tuple, attributes: bool = False) -> list[dict]:
    out = []
    for r in con.execute(sql, args):
        d = {f: r[f] for f in fields if f in r.keys()}
        if attributes:
            d["attributes"] = json.loads(r["attributes"] or "{}")
        out.append(d)
    return out


def _limit(params: dict) -> tuple[int, int]:
    try:
        limit = max(1, min(MAX_LIMIT, int(params.get("limit", 500))))
    except ValueError:
        limit = 500
    try:
        offset = max(0, int(params.get("offset", 0)))
    except ValueError:
        offset = 0
    return limit, offset


def handle(db: Database, parts: list[str], params: dict) -> tuple[int, object]:
    """parts ohne führendes 'api'. -> (HTTP-Status, JSON-Daten)."""
    from .pages import provider_overview, where_clause
    if not parts:
        return 200, {"version": __version__, "endpunkte": ENDPOINTS, "kategorien": CATEGORIES,
                     "preistypen": PRICE_TYPES, "anbieter": list(PROVIDERS)}
    head = parts[0]
    if head == "anbieter" and len(parts) == 1:
        out = []
        for p in provider_overview(db):
            last = p["last"] or {}
            out.append({"name": p["name"], "label": p["label"], "info": p["info"], "enabled": p["enabled"],
                        "count": p["count"], "last_ok": p["last_ok"], "running": p["running"], "review": p["review"],
                        "last_status": last.get("status"), "last_run": last.get("id"),
                        "format_alerts": [a for a in (last.get("format_alerts") or "").splitlines() if a],
                        "jurisdiction": sovereignty.PROVIDER_JURISDICTION.get(p["name"], "")})
        return 200, out
    if head in ("preise", "archiv") and len(parts) == 1:
        table = "prices_current" if head == "preise" else "prices_archive"
        where, args = where_clause(params, ("run_id",) if head == "archiv" else ())
        limit, offset = _limit(params)
        fields = PRICE_FIELDS if head == "preise" else ARCHIVE_FIELDS
        souv = params.get("souv", "")
        with db.connect() as con:
            total = con.execute(f"SELECT COUNT(*) FROM {table}{where}", args).fetchone()[0]
            order = "provider, category, product" if head == "preise" else "valid_to DESC, provider, product"
            rows = _rows(con, f"SELECT * FROM {table}{where} ORDER BY {order} LIMIT ? OFFSET ?",
                         args + [limit, offset], fields, params.get("attributes") in ("1", "true"))
        if souv:
            cls = sovereignty.Classifier()
            rows = [r for r in rows if cls.matches(r["provider"], r["region"], souv)]
        return 200, {"total": total, "limit": limit, "offset": offset, "items": rows}
    if head == "verlauf":
        cur, arch = analysis.history(db, params.get("provider", ""), params.get("key", ""))
        if not cur and not arch:
            return 404, {"error": "nicht gefunden"}
        pts = []
        for a in arch:
            pts.append({k: a[k] for k in ("valid_from", "valid_to", "price", "currency", "unit", "price_eur",
                                          "price_norm", "reason")})
        if cur:
            pts.append({"valid_from": cur["valid_from"], "valid_to": None, "price": cur["price"],
                        "currency": cur["currency"], "unit": cur["unit"], "price_eur": cur["price_eur"],
                        "price_norm": cur["price_norm"], "reason": "aktuell"})
        for p in pts:   # EUR zum damaligen Kurs (Beginn der Gültigkeit)
            rate = fx.rate_at(db, p["currency"], p["valid_from"] or "")
            p["price_eur_damals"] = p["price"] / rate if rate else None
        return 200, {"provider": params.get("provider"), "key": params.get("key"), "points": pts}
    if head == "vergleich" and len(parts) == 1:
        def f(name, default):
            try:
                return float(str(params.get(name, default)).replace(",", "."))
            except ValueError:
                return default
        res = analysis.compare_vms(db, f("vcpu", 2), f("ram", 8), params.get("os", "linux"),
                                   int(f("top", 3)), params.get("region", ""), f("gpu", 0), params.get("souv", ""))
        return 200, res
    if head == "vergleich" and len(parts) == 2:
        kind = compare.KINDS.get(parts[1])
        if not kind:
            return 404, {"error": "unbekannte Vergleichsart", "arten": list(compare.KINDS)}
        return 200, compare.compare_category(db, kind, params)
    if head == "rabatte":
        limit, offset = _limit(params)
        rows = compare.commitments(db, params)
        return 200, {"total": len(rows), "summary": compare.commitment_summary(rows),
                     "items": rows[offset:offset + limit]}
    if head == "bericht":
        try:
            top = int(params.get("top", 20))
        except ValueError:
            top = 20
        rep = report.build(db, params.get("zeitraum"), params.get("provider", ""), params.get("category", ""), top)
        rep.pop("rows")
        return 200, rep
    if head == "szenarien":
        with db.connect() as con:
            return 200, [dict(r) for r in con.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM scenario_items i WHERE i.scenario_id=s.id) AS n_items "
                "FROM scenarios s ORDER BY s.id")]
    if head == "szenario" and len(parts) == 2 and parts[1].isdigit():
        sid = int(parts[1])
        with db.connect() as con:
            s = con.execute("SELECT * FROM scenarios WHERE id=?", (sid,)).fetchone()
        if not s:
            return 404, {"error": "nicht gefunden"}
        lines = analysis.scenario_lines(db, sid)
        try:
            months = int(params.get("monate", s["months"]))
            growth = float(str(params.get("wachstum", s["growth_pct"])).replace(",", "."))
        except ValueError:
            months, growth = s["months"], s["growth_pct"]
        return 200, {"scenario": dict(s), "totals": analysis.scenario_totals(lines),
                     "items": [{"item": ln["item"], "status": ln["status"], "eur_month": ln.get("eur_month"),
                                "price": {k: (ln["price"] or {}).get(k) for k in
                                          ("product", "region", "price", "currency", "unit", "unit_norm",
                                           "price_norm")}} for ln in lines],
                     "projection": analysis.projection(lines, months, growth)}
    if head == "kurse":
        cur = params.get("waehrung", "").upper()
        out = {"rates": fx.get_rates(db), "info": fx.rates_info(db)}
        if cur:
            out["history"] = fx.history_points(db, cur, params.get("seit", ""))
        return 200, out
    return 404, {"error": "unbekannter Endpunkt", "endpunkte": ENDPOINTS}
