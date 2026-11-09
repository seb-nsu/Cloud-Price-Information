"""Auswertungen: VM-Vergleich, Szenarien (Warenkorb) und Preisverlauf."""
from __future__ import annotations

import json
import re

from .db import Database
from .models import PT_ON_DEMAND
from .normalize import U_GB, U_GB_MONTH, U_MONTH, U_REQ, U_TOKENS
from .sovereignty import Classifier

GCP_EXCLUDE = re.compile(r"sole tenancy|custom|premium|commitment|extended|preemptible|spot|reserved", re.I)


# ------------------------------------------------------------------ VM-Vergleich
def compare_vms(db: Database, vcpu: float, ram: float, os_: str = "linux", top_n: int = 3,
                region_like: str = "", gpu: float = 0, souv: str = "") -> dict[str, list[dict]]:
    """Günstigste On-Demand-Optionen je Anbieter für mind. `vcpu` vCPU und `ram` GB RAM (€/Monat).

    `gpu` = 0: nur Größen ohne GPU; > 0: mindestens so viele GPUs.
    `souv`: Filter Datenhoheit ("", "eu", "eu_souveraen"), siehe sovereignty.py.
    """
    out: dict[str, list[dict]] = {}
    cls = Classifier()
    args: list = [vcpu, ram, PT_ON_DEMAND]
    sql = ("SELECT provider, key, product, region, price_norm, vcpu, ram_gb, os, gpu FROM prices_current "
           "WHERE category='Compute' AND unit_norm='Monat' AND price_norm IS NOT NULL "
           "AND vcpu >= ? AND ram_gb >= ? AND price_type = ?")
    if gpu > 0:
        sql += " AND gpu >= ?"
        args.append(gpu)
    else:
        sql += " AND (gpu IS NULL OR gpu = 0)"
    if os_:
        sql += " AND os = ?"
        args.append(os_)
    if region_like:
        sql += " AND pv_lower(region) LIKE ?"
        args.append(f"%{region_like.casefold()}%")
    sql += " ORDER BY price_norm"
    with db.connect() as con:
        for r in con.execute(sql, args):
            lst = out.setdefault(r["provider"], [])
            if len(lst) < top_n and cls.matches(r["provider"], r["region"], souv):
                lst.append({"kind": "Flavor", "label": r["product"], "region": r["region"],
                            "eur_month": r["price_norm"], "vcpu": r["vcpu"], "ram": r["ram_gb"],
                            "gpu": r["gpu"], "provider": r["provider"], "key": r["key"],
                            "souv": cls.classify(r["provider"], r["region"])})
        if os_ in ("", "linux") and not gpu:
            for opt in _component_options(con, vcpu, ram, region_like):
                if cls.matches(opt["provider"], opt["region"], souv):
                    opt["souv"] = cls.classify(opt["provider"], opt["region"])
                    out.setdefault(opt["provider"], []).append(opt)
    out = {p: v for p, v in out.items() if v}
    for p in out:
        out[p] = sorted(out[p], key=lambda o: o["eur_month"])[:top_n]
    return dict(sorted(out.items(), key=lambda kv: kv[1][0]["eur_month"] if kv[1] else 1e18))


def _component_options(con, vcpu: float, ram: float, region_like: str) -> list[dict]:
    """Anbieter mit Preisen je vCPU und je GB RAM (Google, IONOS) -> zusammengesetzter Preis."""
    opts = []
    # Google: "<Familie> Instance Core running in <Ort>" / "<Familie> Instance Ram running in <Ort>"
    parts: dict[tuple, dict] = {}
    for r in con.execute("SELECT product, region, price_norm, unit_norm FROM prices_current WHERE provider='gcp' "
                         "AND category='Compute' AND price_type=? AND price_norm IS NOT NULL", (PT_ON_DEMAND,)):
        m = re.match(r"^(.*?)\s+Instance (Core|Ram)\b", r["product"])
        if not m or GCP_EXCLUDE.search(r["product"]):
            continue
        if region_like and region_like.casefold() not in r["region"].casefold():
            continue
        parts.setdefault((m.group(1), r["region"]), {})[m.group(2)] = r["price_norm"]
    for (family, region), p in parts.items():
        if "Core" in p and "Ram" in p:
            opts.append({"kind": "Komponenten", "label": f"{family} (Custom: {vcpu:g} vCPU / {ram:g} GB)",
                         "region": region, "eur_month": vcpu * p["Core"] + ram * p["Ram"],
                         "vcpu": vcpu, "ram": ram, "gpu": None, "provider": "gcp", "key": ""})
    # IONOS: vCPU-Server / Dedicated Core-Server mit Preis je vCPU/Core und je GB RAM
    ion: dict[str, dict] = {}
    for r in con.execute("SELECT product, attributes, price_norm FROM prices_current WHERE provider='ionos' "
                         "AND category='Compute' AND price_type=? AND price_norm IS NOT NULL", (PT_ON_DEMAND,)):
        section = json.loads(r["attributes"] or "{}").get("Abschnitt", "")
        last = section.split(" > ")[-1] if section else ""
        if not re.search(r"vCPU-Server|Dedicated Core-Server", last) or "Opteron" in r["product"]:
            continue   # AMD Opteron ist für Neubestellungen nicht mehr verfügbar
        if re.search(r"\bRAM\b", r["product"]) and "1 GB" in r["product"]:
            ion.setdefault(last, {})["ram"] = r["price_norm"]
        elif re.search(r"vCPU\b|Dedicated Core\b|EPYC|Intel|AMD", r["product"]):
            ion.setdefault(last, {}).setdefault("cores", []).append((r["price_norm"], r["product"]))
    for family, p in ion.items():
        if "ram" in p and p.get("cores"):
            core_price, core_name = min(p["cores"])
            opts.append({"kind": "Komponenten", "label": f"{family}: {core_name.split(' · ')[-1]} "
                                                         f"({vcpu:g} vCPU / {ram:g} GB)",
                         "region": "de", "eur_month": vcpu * core_price + ram * p["ram"],
                         "vcpu": vcpu, "ram": ram, "gpu": None, "provider": "ionos", "key": ""})
    return opts


# ------------------------------------------------------------------ Szenarien
QTY_HINT = {U_MONTH: "Anzahl (z. B. VMs) – Dauerbetrieb 730 h/Monat",
            U_GB_MONTH: "GB gespeichert", U_GB: "GB pro Monat (z. B. Traffic)",
            U_TOKENS: "Mio. Token pro Monat", U_REQ: "Tausend Anfragen pro Monat"}


def scenario_lines(db: Database, scenario_id: int) -> list[dict]:
    with db.connect() as con:
        items = con.execute("SELECT * FROM scenario_items WHERE scenario_id=? ORDER BY variant, id",
                            (scenario_id,)).fetchall()
        lines = []
        for it in items:
            cur = con.execute("SELECT * FROM prices_current WHERE provider=? AND key=?",
                              (it["provider"], it["key"])).fetchone()
            line = {"item": dict(it), "status": "aktuell", "price": None}
            if cur is None:
                arch = con.execute("SELECT * FROM prices_archive WHERE provider=? AND key=? "
                                   "ORDER BY valid_to DESC LIMIT 1", (it["provider"], it["key"])).fetchone()
                cur, line["status"] = arch, "nicht mehr gelistet"
            if cur is not None:
                line["price"] = dict(cur)
                if cur["price_norm"] is not None and cur["unit_norm"]:
                    line["eur_month"] = cur["price_norm"] * it["quantity"]
                    line["calc"] = f"{it['quantity']:g} × €/{cur['unit_norm']}"
                elif cur["price_eur"] is not None:
                    line["eur_month"] = cur["price_eur"] * it["quantity"]
                    line["calc"] = f"{it['quantity']:g} × Preis (Einheit „{cur['unit']}“ nicht normiert)"
                else:
                    line["eur_month"] = None
                    line["calc"] = "kein EUR-Kurs"
            else:
                line["eur_month"], line["status"], line["calc"] = None, "unbekannt", ""
            lines.append(line)
    return lines


def scenario_totals(lines: list[dict]) -> dict[str, dict]:
    totals: dict[str, dict] = {}
    for ln in lines:
        v = ln["item"]["variant"]
        t = totals.setdefault(v, {"sum": 0.0, "incomplete": False, "n": 0})
        t["n"] += 1
        if ln.get("eur_month") is None:
            t["incomplete"] = True
        else:
            t["sum"] += ln["eur_month"]
        if ln["status"] != "aktuell":
            t["incomplete"] = True
    return dict(sorted(totals.items()))


# ------------------------------------------------------------------ Verlauf
def history(db: Database, provider: str, key: str) -> tuple[dict | None, list[dict]]:
    with db.connect() as con:
        cur = con.execute("SELECT * FROM prices_current WHERE provider=? AND key=?", (provider, key)).fetchone()
        arch = [dict(r) for r in con.execute("SELECT * FROM prices_archive WHERE provider=? AND key=? "
                                             "ORDER BY valid_from", (provider, key))]
    return (dict(cur) if cur else None), arch


def sparkline_svg(points: list[tuple[str, float]], width: int = 520, height: int = 120) -> str:
    """Treppen-Diagramm des Preisverlaufs als Inline-SVG."""
    if not points:
        return ""
    vals = [p for _, p in points]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or (abs(hi) or 1.0)
    pad = 8
    n = len(points)
    xs = [pad + i * (width - 2 * pad) / max(1, n - 1) for i in range(n)] if n > 1 else [width / 2]
    ys = [height - pad - (v - lo) / span * (height - 2 * pad) if hi != lo else height / 2 for v in vals]
    path = f"M{xs[0]:.1f},{ys[0]:.1f}"
    for i in range(1, n):
        path += f" H{xs[i]:.1f} V{ys[i]:.1f}"
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3"><title>{d[:10]}: {v:g}</title></circle>'
                   for x, y, (d, v) in zip(xs, ys, points))
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
            f'role="img" aria-label="Preisverlauf"><path d="{path}" fill="none" stroke="currentColor" '
            f'stroke-width="2"/>{dots}</svg>')


# ------------------------------------------------------------------ Szenario-Hochrechnung
def projection(lines: list[dict], months: int, default_growth_pct: float) -> dict:
    """Hochrechnung je Variante über `months` Monate mit monatlichem Wachstum (Zinseszins).

    Wachstum je Position: eigener Wert (`growth_pct` der Position) oder Standard des Szenarios.
    Mengen mit Einheit „Monat“ (Anzahl VMs, Cluster …) werden auf ganze Stück aufgerundet, Mengen in GB,
    Token usw. bleiben anteilig. Positionen ohne Preis fließen nicht ein (Variante = unvollständig).
    -> {"months": [1..n], "variants": {V: {"monthly": [...], "total": x, "first": a, "last": b}}}
    """
    import math as _m
    months = max(1, min(120, int(months or 12)))
    out: dict[str, dict] = {}
    for ln in lines:
        it = ln["item"]
        v = out.setdefault(it["variant"], {"monthly": [0.0] * months, "incomplete": False})
        if ln.get("eur_month") is None or not it["quantity"]:
            v["incomplete"] = True
            continue
        unit_price = ln["eur_month"] / it["quantity"]
        g = it.get("growth_pct")
        g = default_growth_pct if g is None else g
        whole = (ln.get("price") or {}).get("unit_norm") == U_MONTH
        for m in range(months):
            qty = it["quantity"] * (1 + g / 100) ** m
            if whole:
                qty = _m.ceil(qty - 1e-9)
            v["monthly"][m] += qty * unit_price
    for v in out.values():
        v["total"] = sum(v["monthly"])
        v["first"], v["last"] = v["monthly"][0], v["monthly"][-1]
    return {"months": list(range(1, months + 1)), "variants": dict(sorted(out.items()))}
