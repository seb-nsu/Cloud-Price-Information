"""Vergleiche über VMs hinaus: Speicher, Traffic, Kubernetes, Datenbanken – sowie
Reservierungen, Commitments und Savings Plans gegenüber On-Demand."""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field

from .db import Database
from .models import PT_ON_DEMAND, PT_RESERVED_1Y, PT_RESERVED_3Y, PT_SAVINGS_1Y, PT_SAVINGS_3Y
from .normalize import U_GB, U_GB_MONTH, U_MONTH
from .sovereignty import Classifier


# ------------------------------------------------------------------ Kategorie-Vergleiche
@dataclass
class Kind:
    slug: str
    title: str
    category: str
    unit_norm: str
    intro: str
    # (Schlüssel, Bezeichnung, Regex auf Dienst + Produkt) – erste passende Art gewinnt
    types: list[tuple[str, str, str]] = field(default_factory=list)
    classes: list[tuple[str, str, str]] = field(default_factory=list)
    engines: list[tuple[str, str, str]] = field(default_factory=list)
    sizing: bool = False


KINDS: dict[str, Kind] = {k.slug: k for k in (
    Kind("speicher", "Speicher", "Storage", U_GB_MONTH,
         "Preise je GB und Monat für gespeicherte Daten (ohne Anfragen, Operationen und Traffic).",
         types=[("backup", "Snapshot/Backup", r"snapshot|backup|\bcbr\b|csbs|sicherung"),
                ("objekt", "Objektspeicher", r"object|objekt|blob|amazons3|\bs3\b|\bobs\b|cloud storage|bucket"),
                ("datei", "Dateispeicher", r"\bfiles?\b|datei|\bsfs\b|\befs\b|\bnfs\b|share"),
                ("block", "Blockspeicher", r"disk|volume|\bevs\b|\bebs\b|ssd|hdd|block|nvme|platte")],
         classes=[("archiv", "Archiv", r"archive|archiv|glacier|deep|coldline|\bcold\b|kalt"),
                  ("selten", "Selten genutzt", r"\bcool\b|infrequent|nearline|standard-ia|one zone|\bia\b"),
                  ("standard", "Standard", r".")]),
    Kind("traffic", "Traffic", "Netzwerk", U_GB,
         "Preise je übertragenem GB. Staffelpreise erscheinen je Stufe; kostenlose Kontingente sind ausgeblendet, "
         "solange „inkl. kostenloser Kontingente“ nicht gesetzt ist.",
         types=[("intern", "Zwischen Regionen/Zonen",
                 r"inter-?region|inter-?zone|between|region to region|availability zone|intra|cross"),
                ("internet", "Ausgehend ins Internet",
                 r"internet|egress|outbound|data transfer out|\bout\b|ausgehend|traffic|outgoing|datentransfer"),
                ("eingehend", "Eingehend", r"inbound|ingress|eingehend|\bin\b")]),
    Kind("kubernetes", "Kubernetes", "Kubernetes", U_MONTH,
         "Verwaltetes Kubernetes: Gebühren für Cluster bzw. Control Plane je Monat (Worker-Knoten sind "
         "normale VMs, siehe VM-Vergleich).",
         types=[("cluster", "Cluster/Control Plane",
                 r"cluster|control plane|management|uptime|\bsla\b|\beks\b|\bgke\b|\baks\b|\bcce\b|\bske\b|kubernetes"),
                ("sonstige", "Sonstiges", r".")]),
    Kind("datenbanken", "Datenbanken", "Datenbank", U_MONTH,
         "Verwaltete Datenbanken je Monat (730 h Dauerbetrieb). vCPU/RAM sind angegeben, wo der Anbieter sie liefert.",
         engines=[("postgres", "PostgreSQL", r"postgre"), ("mysql", "MySQL", r"mysql"),
                  ("mariadb", "MariaDB", r"maria"), ("sqlserver", "SQL Server", r"sql server|sql database|mssql"),
                  ("redis", "Redis/Cache", r"redis|valkey|cache|\bdcs\b"), ("mongodb", "MongoDB", r"mongo|\bdds\b")],
         sizing=True),
)}

_TIER = re.compile(r"\s*(\[[^\]]*\]|\(ab [^)]*\))\s*$")


def classify(patterns: list[tuple[str, str, str]], text: str) -> tuple[str, str]:
    for key, label, rx in patterns:
        if re.search(rx, text, re.I):
            return key, label
    return "", ""


def compare_category(db: Database, kind: Kind, params: dict) -> dict:
    """-> {"providers": {anbieter: [zeilen]}, "n": Treffer gesamt}. Je Anbieter die günstigsten `top` Zeilen."""
    top = max(1, min(50, int(params.get("top") or 5)))
    typ, klasse, engine = params.get("typ", ""), params.get("klasse", ""), params.get("engine", "")
    souv, region = params.get("souv", ""), (params.get("region") or "").casefold()
    words = [w.casefold() for w in (params.get("q") or "").split()]
    with_free = params.get("kostenlos") in ("1", "ja", "true")
    tier_mode = params.get("stufe", "guenstigste")
    min_vcpu = _f(params.get("vcpu"))
    min_ram = _f(params.get("ram"))
    cls = Classifier()

    sql = ("SELECT provider, key, service, product, region, price, currency, unit, price_norm, vcpu, ram_gb "
           "FROM prices_current WHERE category=? AND unit_norm=? AND price_norm IS NOT NULL AND price_type=?")
    args: list = [kind.category, kind.unit_norm, PT_ON_DEMAND]
    if params.get("provider"):
        sql += " AND provider=?"
        args.append(params["provider"])
    if not with_free:
        sql += " AND price_norm > 0"
    if min_vcpu:
        sql += " AND vcpu >= ?"
        args.append(min_vcpu)
    if min_ram:
        sql += " AND ram_gb >= ?"
        args.append(min_ram)
    groups: dict[tuple, dict] = {}
    with db.connect() as con:
        for r in con.execute(sql, args):
            text = f"{r['service']} {r['product']}"
            low = text.casefold()
            if words and not all(w in low for w in words):
                continue
            if region and region not in r["region"].casefold():
                continue
            if not cls.matches(r["provider"], r["region"], souv):
                continue
            t_key, t_label = (classify(kind.types, r["product"]) or ("", "")) if kind.types else ("", "")
            if kind.types and not t_key:
                t_key, t_label = classify(kind.types, text)
            if typ and t_key != typ:
                continue
            c_key, c_label = classify(kind.classes, text) if kind.classes else ("", "")
            if klasse and c_key != klasse:
                continue
            e_key, e_label = classify(kind.engines, text) if kind.engines else ("", "")
            if engine and e_key != engine:
                continue
            row = {"provider": r["provider"], "key": r["key"], "service": r["service"], "product": r["product"],
                   "region": r["region"], "price": r["price"], "currency": r["currency"], "unit": r["unit"],
                   "eur": r["price_norm"], "vcpu": r["vcpu"], "ram": r["ram_gb"],
                   "type": t_label, "class": c_label, "engine": e_label,
                   "souv": cls.classify(r["provider"], r["region"])}
            # Staffelpreise: je Produkt nur eine Stufe zeigen
            g = (r["provider"], _TIER.sub("", r["product"]), r["region"])
            cur = groups.get(g)
            if cur is None or (row["eur"] < cur["eur"] if tier_mode != "einstieg" else row["eur"] > cur["eur"]):
                groups[g] = row
    by_provider: dict[str, list[dict]] = {}
    for row in sorted(groups.values(), key=lambda x: x["eur"]):
        lst = by_provider.setdefault(row["provider"], [])
        if len(lst) < top:
            lst.append(row)
    best = min((v[0]["eur"] for v in by_provider.values()), default=None)
    return {"providers": dict(sorted(by_provider.items(), key=lambda kv: kv[1][0]["eur"])),
            "n": len(groups), "best": best}


def _f(v) -> float:
    try:
        x = float(str(v).replace(",", "."))
        return x if x > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


# ------------------------------------------------------------------ Commitments
COMMIT_TYPES = (PT_RESERVED_1Y, PT_RESERVED_3Y, PT_SAVINGS_1Y, PT_SAVINGS_3Y)
_GCP_OD = re.compile(r"^(.*?)\s+Instance (Core|Ram) running in (.+)$", re.I)
_GCP_CUD = re.compile(r"^Commitment(?: v\d+)?:\s*(.*?)\s+(Cpu|Core|Ram|Memory)\s+in\s+(.+?)\s+for\s+(\d+)\s+Years?$",
                      re.I)
_TERM_WORDS = re.compile(r"\b(1|3|one|three|ein|drei)\s*(jahr|jahre|year|years|yr)\b|savings?\s*plan|laufzeit|"
                         r"reserv\w*|commit\w*|spar\w*", re.I)


def _months(ptype: str) -> int:
    return 36 if "3 J." in ptype else 12


def _gcp_od_key(product: str):
    m = _GCP_OD.match(product)
    if not m:
        return None
    return m.group(1).strip().lower(), "cpu" if m.group(2).lower() == "core" else "ram", m.group(3).strip().lower()


def _gcp_cud_key(product: str):
    m = _GCP_CUD.match(product)
    if not m:
        return None
    res = "cpu" if m.group(2).lower() in ("cpu", "core") else "ram"
    return m.group(1).strip().lower(), res, m.group(3).strip().lower()


def commitments(db: Database, params: dict) -> list[dict]:
    """Stellt Reservierungen, Commitments und Savings Plans dem On-Demand-Preis desselben Produkts gegenüber.

    Ergebnis je Zeile: On-Demand €/Monat, effektiver Commitment-Preis €/Monat (inkl. anteiliger Vorauszahlung),
    Ersparnis in %, Break-even-Auslastung (ab diesem Anteil der Laufzeit lohnt sich die Bindung) und – bei
    Vorauszahlung – die Amortisationsdauer bei Dauerbetrieb.
    """
    cls = Classifier()
    souv = params.get("souv", "")
    wanted_type = params.get("price_type", "")
    words = [w.casefold() for w in (params.get("q") or "").split()]
    where, args = ["price_norm IS NOT NULL OR unit='Quantity'"], []
    for f in ("provider", "category"):
        if params.get(f):
            where.append(f"{f}=?")
            args.append(params[f])
    with db.connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT provider, key, category, product, region, unit, price, currency, price_type, attributes, "
            "price_eur, price_norm, unit_norm FROM prices_current WHERE (" + ") AND (".join(where) + ")", args)]
    od_by: dict[str, dict] = {}
    commits: list[dict] = []
    for r in rows:
        r["attrs"] = json.loads(r.pop("attributes") or "{}")
        if r["price_type"] == PT_ON_DEMAND:
            od_by[r["provider"]] = od_by.get(r["provider"]) or {}
            for k in _od_keys(r):
                od_by[r["provider"]].setdefault(k, r)
        elif r["price_type"] in COMMIT_TYPES:
            if wanted_type and r["price_type"] != wanted_type:
                continue
            commits.append(r)

    # AWS: Stundenanteil und Vorauszahlung je Angebot zusammenführen
    merged: dict[tuple, dict] = {}
    for c in commits:
        if c["provider"] == "aws":
            a = c["attrs"]
            k = ("aws", a.get("SKU"), c["price_type"], a.get("PurchaseOption", ""), a.get("OfferingClass", ""),
                 c["region"])
            m = merged.setdefault(k, {**c, "hourly_eur": 0.0, "upfront_eur": 0.0, "parts": 0})
            m["parts"] += 1
            if c["unit"] == "Quantity":
                m["upfront_eur"] += c["price_eur"] or 0.0
            else:
                m["hourly_eur"] += c["price_norm"] or 0.0   # €/Monat bei 730 h
                m["product"] = c["product"]
                m["key"] = c["key"]
        else:
            merged[(c["provider"], c["key"])] = c

    out = []
    for c in merged.values():
        od = _find_od(c, od_by.get(c["provider"], {}))
        if not od or not od["price_norm"]:
            continue
        months = _months(c["price_type"])
        if c["provider"] == "aws":
            recurring = c["hourly_eur"]               # bereits €/Monat (price_norm)
            upfront = c["upfront_eur"]
        else:
            recurring = c["price_norm"]
            upfront = 0.0
        if recurring is None:
            continue
        eff = recurring + upfront / months
        od_m = od["price_norm"]
        if eff <= 0 or od_m <= 0:
            continue
        saving = (1 - eff / od_m) * 100
        if params.get("min") and saving < _f(params["min"]):
            continue
        text = f"{c['product']} {od['product']}".casefold()
        if words and not all(w in text for w in words):
            continue
        if not cls.matches(c["provider"], c["region"], souv):
            continue
        amort = upfront / (od_m - recurring) if upfront and od_m > recurring else None
        out.append({"provider": c["provider"], "key": c["key"], "od_key": od["key"], "category": c["category"],
                    "product": od["product"], "commit_product": c["product"], "region": c["region"],
                    "price_type": c["price_type"], "months": months, "od_month": od_m, "eff_month": eff,
                    "recurring_month": recurring, "upfront": upfront, "total": eff * months,
                    "saving_pct": saving, "break_even_pct": eff / od_m * 100, "amort_months": amort,
                    "souv": cls.classify(c["provider"], c["region"])})
    sort = params.get("sort", "-ersparnis")
    keyf = {"ersparnis": lambda x: x["saving_pct"], "od": lambda x: x["od_month"],
            "commit": lambda x: x["eff_month"], "anbieter": lambda x: (x["provider"], x["product"])}
    out.sort(key=keyf.get(sort.lstrip("-"), keyf["ersparnis"]), reverse=sort.startswith("-"))
    return out


def _base_product(product: str) -> str:
    return re.sub(r"\s+", " ", _TERM_WORDS.sub("", product)).strip(" ·-:()").casefold()


def _od_keys(r: dict) -> list:
    """Schlüssel, unter denen ein On-Demand-Preis für die Zuordnung gefunden wird."""
    keys = [("key", r["key"])]
    a = r["attrs"]
    if r["provider"] == "azure" and a.get("armSkuName"):
        keys.append(("az", a.get("armSkuName"), r["region"], a.get("productName", "")))
    if r["provider"] == "aws" and a.get("SKU") and r["unit"] in ("Hrs", "Hours", "hrs"):
        keys.append(("aws", a["SKU"]))
    if r["provider"] == "gcp":
        k = _gcp_od_key(r["product"])
        if k:
            keys.append(("gcp",) + k)
    keys.append(("base", r["category"], r["region"], r["unit_norm"], _base_product(r["product"])))
    return keys


def _find_od(c: dict, od: dict) -> dict | None:
    a = c["attrs"]
    if c["provider"] == "azure":
        if "|SP" in c["key"]:
            return od.get(("key", c["key"].rsplit("|SP", 1)[0]))
        return od.get(("az", a.get("armSkuName"), c["region"], a.get("productName", "")))
    if c["provider"] == "aws":
        return od.get(("aws", a.get("SKU")))
    if c["provider"] == "gcp":
        k = _gcp_cud_key(c["product"])
        return od.get(("gcp",) + k) if k else None
    hit = od.get(("base", c["category"], c["region"], c["unit_norm"], _base_product(c["product"])))
    if hit:
        return hit
    # Fallback: ähnlichster On-Demand-Preis gleicher Kategorie, Region und Einheit
    base = _base_product(c["product"])
    best, score = None, 0.0
    for k, row in od.items():
        if k[0] != "base" or k[1:4] != (c["category"], c["region"], c["unit_norm"]):
            continue
        s = difflib.SequenceMatcher(None, base, k[4]).ratio()
        if s > score:
            best, score = row, s
    return best if score >= 0.85 else None


def commitment_summary(rows: list[dict]) -> list[dict]:
    """Durchschnittliche Ersparnis je Anbieter und Preistyp."""
    agg: dict[tuple, list[float]] = {}
    for r in rows:
        agg.setdefault((r["provider"], r["price_type"]), []).append(r["saving_pct"])
    return [{"provider": p, "price_type": t, "n": len(v), "avg": sum(v) / len(v), "max": max(v)}
            for (p, t), v in sorted(agg.items())]
