"""Formatwächter: erkennt, wenn sich das Antwortformat eines Anbieters ändert.

Zu jedem Abruf wird ein „Formatprofil“ gespeichert (Anzahl je Bereich und Kategorie, Anteil der
Datensätze je Attributfeld, Anteil normierbarer Einheiten, Anteil erkannter VM-Größen, Einheiten).
Vor der Übernahme wird das Profil mit dem des letzten übernommenen Abrufs verglichen. Auffällig ist:

* ein Feld, das bisher in fast allen Datensätzen vorkam, fehlt jetzt weitgehend („Spalte fehlt“)
* die Preisanzahl eines Bereichs oder einer Kategorie bricht ein
* deutlich weniger Einheiten lassen sich normieren bzw. VM-Größen erkennen
* viele unbekannte Einheiten tauchen auf
* Pflichtfelder eines Anbieters fehlen (siehe REQUIRED)

Konfiguration in config.toml ([general]):
    formatwaechter = "freigabe"   # Auffälligkeit -> Vorschau mit manueller Freigabe (Standard)
                   = "warnen"     # nur melden, Übernahme gemäß `freigabe`
                   = "aus"
    formatwaechter_einbruch = 0.3 # Rückgang je Bereich/Kategorie, ab dem gewarnt wird (30 %)
"""
from __future__ import annotations

import json
from collections import Counter

from .db import Database, now_iso
from .models import FetchResult
from .normalize import normalize_unit

# Felder, die je Anbieter (bzw. Kategorie) in Datensätzen vorhanden sein sollten
REQUIRED: dict[str, dict[str, tuple[str, ...]]] = {
    "azure": {"Compute": ("armSkuName", "meterId", "skuId")},
    "aws": {"Compute": ("SKU", "Instance Type", "vCPU", "Memory")},
    "otc": {"Compute": ("opiFlavour",)},
    "ionos": {"*": ("Abschnitt", "Originalangabe")},
    "stackit": {"*": ("generalProductGroup",)},
    "gcp": {"*": ("skuId", "usageType")},
    "scaleway": {"*": ("sku",)},
    "hetzner": {"Compute": ("cores", "memory")},
    "ovh": {"*": ("planCode",)},
}
MIN_BASE = 10            # Bereiche/Kategorien mit weniger Preisen werden nicht bewertet
FIELD_WAS = 0.9          # Feld kam bisher in >= 90 % der Datensätze vor …
FIELD_NOW = 0.5          # … und jetzt in < 50 %


def profile(provider: str, result: FetchResult) -> dict:
    recs = result.records
    n = len(recs) or 1
    fields: Counter = Counter()
    fields_by_cat: dict[str, Counter] = {}
    units: Counter = Counter()
    norm_ok = 0
    vm_like = 0
    for r in recs:
        keys = [k for k, v in (r.attributes or {}).items() if v not in (None, "")]
        fields.update(keys)
        fields_by_cat.setdefault(r.category, Counter()).update(keys)
        units[r.unit] += 1
        if normalize_unit(r.unit)[0]:
            norm_ok += 1
        if r.category == "Compute":
            vm_like += 1
    cats = Counter(r.category for r in recs)
    return {
        "n": len(recs),
        "scopes": dict(Counter(r.scope for r in recs)),
        "categories": dict(cats),
        "fields": {k: round(v / n, 4) for k, v in fields.items()},
        "fields_by_cat": {c: {k: round(v / cats[c], 4) for k, v in cnt.items()} for c, cnt in fields_by_cat.items()},
        "units": dict(units.most_common(200)),
        "norm_ratio": round(norm_ok / n, 4),
        "currencies": dict(Counter(r.currency for r in recs)),
        "scopes_failed": sorted(result.scopes_failed),
    }


def compare(provider: str, old: dict | None, new: dict, drop: float = 0.3) -> list[str]:
    """Liefert Auffälligkeiten (leer = Format unverändert)."""
    alerts: list[str] = []
    # Pflichtfelder (auch ohne Vergleichsprofil)
    for cat, req in REQUIRED.get(provider, {}).items():
        share = new["fields"] if cat == "*" else new["fields_by_cat"].get(cat)
        n_cat = new["n"] if cat == "*" else new["categories"].get(cat, 0)
        if not share or n_cat < MIN_BASE:
            continue
        for f in req:
            if share.get(f, 0) < FIELD_NOW:
                where = "allen Datensätzen" if cat == "*" else f"Kategorie {cat}"
                alerts.append(f"Pflichtfeld „{f}“ fehlt in {int((1 - share.get(f, 0)) * 100)} % der {where}.")
    if not old or not old.get("n"):
        return alerts
    for f, was in old.get("fields", {}).items():
        now = new["fields"].get(f, 0)
        if was >= FIELD_WAS and now < FIELD_NOW and new["n"] >= MIN_BASE:
            alerts.append(f"Feld „{f}“ kam bisher in {was:.0%} der Datensätze vor, jetzt in {now:.0%} "
                          f"(Antwortformat geändert?).".replace("%", " %"))
    failed = set(new.get("scopes_failed", []))
    for scope, cnt in old.get("scopes", {}).items():
        if scope in failed or cnt < MIN_BASE:
            continue
        now = new["scopes"].get(scope, 0)
        if now < cnt * (1 - drop):
            alerts.append(f"Bereich „{scope or 'gesamt'}“: {now} statt bisher {cnt} Preise "
                          f"({(now / cnt - 1) * 100:+.0f} %).")
    for cat, cnt in old.get("categories", {}).items():
        now = new["categories"].get(cat, 0)
        if cnt >= MIN_BASE and now < cnt * (1 - drop):
            alerts.append(f"Kategorie {cat}: {now} statt bisher {cnt} Preise ({(now / cnt - 1) * 100:+.0f} %).")
    if old.get("norm_ratio", 0) - new["norm_ratio"] > 0.2:
        alerts.append(f"Nur noch {new['norm_ratio']:.0%} der Einheiten normierbar (bisher "
                      f"{old['norm_ratio']:.0%}) – Einheitenangaben geändert?".replace("%", " %"))
    old_units = set(old.get("units", {}))
    unknown = {u: c for u, c in new.get("units", {}).items() if u not in old_units}
    n_unknown = sum(unknown.values())
    if old_units and n_unknown > max(MIN_BASE, 0.2 * new["n"]):
        sample = ", ".join(f"„{u}“" for u in list(unknown)[:5])
        alerts.append(f"{n_unknown} Preise mit bisher unbekannten Einheiten, z. B. {sample}.")
    if set(new.get("currencies", {})) - set(old.get("currencies", {})):
        alerts.append("Neue Währung(en): " + ", ".join(sorted(set(new["currencies"]) - set(old["currencies"]))))
    return alerts


def baseline(db: Database, provider: str, exclude_run: int) -> dict | None:
    """Profil des letzten übernommenen Abrufs."""
    with db.connect() as con:
        row = con.execute("SELECT p.data FROM format_profiles p JOIN fetch_runs r ON r.id = p.run_id "
                          "WHERE p.provider=? AND r.status='ok' AND p.run_id<>? ORDER BY p.run_id DESC LIMIT 1",
                          (provider, exclude_run)).fetchone()
    return json.loads(row[0]) if row else None


def check(db: Database, provider: str, run_id: int, result: FetchResult, cfg: dict) -> list[str]:
    """Speichert das Profil dieses Abrufs und liefert die Auffälligkeiten gegenüber dem letzten übernommenen."""
    mode = str(cfg.get("formatwaechter", "freigabe")).lower()
    if mode == "aus":
        return []
    prof = profile(provider, result)
    with db.connect() as con:
        con.execute("INSERT OR REPLACE INTO format_profiles (run_id, provider, created_at, data) VALUES (?,?,?,?)",
                    (run_id, provider, now_iso(), json.dumps(prof, ensure_ascii=False)))
        # nur die letzten 20 Profile je Anbieter aufbewahren
        con.execute("DELETE FROM format_profiles WHERE provider=? AND run_id NOT IN (SELECT run_id FROM "
                    "format_profiles WHERE provider=? ORDER BY run_id DESC LIMIT 20) AND run_id NOT IN ("
                    "SELECT MAX(p.run_id) FROM format_profiles p JOIN fetch_runs r ON r.id=p.run_id "
                    "WHERE p.provider=? AND r.status='ok')", (provider, provider, provider))
    return compare(provider, baseline(db, provider, run_id), prof, float(cfg.get("formatwaechter_einbruch", 0.3)))


def blocks_apply(cfg: dict) -> bool:
    return str(cfg.get("formatwaechter", "freigabe")).lower() == "freigabe"
