"""Abgleich eines Abrufergebnisses mit dem aktuellen Preisbestand.

Ablauf: Abruf -> `compute_diff` -> `stage` (Vorschau) -> `apply_staged` (Übernahme)
oder `discard` (Verwerfen).

Regeln beim Übernehmen:
* neuer Schlüssel                       -> neu in `prices_current`
* Preis/Einheit/Währung/Preistyp anders -> alter Eintrag ins Archiv (Preisänderung)
* Schlüssel fehlt, Scope erfolgreich    -> Archiv (Nicht mehr gelistet)
* Scope wird gar nicht mehr abgerufen   -> Archiv (Nicht mehr im Abrufumfang)
* Scope-Abruf fehlgeschlagen            -> Bestand dieses Scopes bleibt unangetastet
* gleicher Preis, nur Schlüssel/Name neu -> Umbenennung (kein Archiv, Verlauf wird mitgeführt)

Schutzregeln (Verstoß -> Ergebnis wartet zwingend auf manuelle Freigabe):
* weniger als `min_ratio` der bisherigen Preise geliefert
* mehr als `max_removed_ratio` der bisherigen Preise würden entfallen
* ein Scope, der bisher Preise hatte, liefert plötzlich keine mehr
* Währungswechsel bei bestehenden Preisen
"""
from __future__ import annotations

import difflib
import json
import logging
import math
from dataclasses import asdict, dataclass, field

from .db import Database, now_iso
from .models import FetchResult, PriceRecord
from .normalize import normalize

log = logging.getLogger(__name__)

COMPARE_FIELDS = ("price", "unit", "currency", "price_type")
META_KEY = "__meta__"
R_CHANGED = "Preisänderung"
R_REMOVED = "Nicht mehr gelistet"
R_OUT_OF_SCOPE = "Nicht mehr im Abrufumfang"


class SyncAborted(Exception):
    pass


@dataclass
class Diff:
    new: list[PriceRecord] = field(default_factory=list)
    changed: list[tuple] = field(default_factory=list)      # (old_row, rec)
    unchanged: list[tuple] = field(default_factory=list)    # (old_row, rec)
    removed: list[tuple] = field(default_factory=list)      # (old_row, reason)
    renamed: list[tuple] = field(default_factory=list)      # (old_row, rec)
    violations: list[str] = field(default_factory=list)
    n_fetched: int = 0
    n_dupes: int = 0
    n_existing: int = 0

    def stats(self) -> dict[str, int]:
        return {"n_fetched": self.n_fetched, "n_new": len(self.new), "n_changed": len(self.changed),
                "n_removed": len(self.removed), "n_unchanged": len(self.unchanged),
                "n_renamed": len(self.renamed), "n_dupes": self.n_dupes}


def _same_price(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)


def _changed(old, rec: PriceRecord) -> bool:
    if not _same_price(old["price"], rec.price):
        return True
    return any(old[f] != getattr(rec, f) for f in COMPARE_FIELDS if f != "price")


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _detect_renames(removed: list[tuple], new: list[PriceRecord], min_sim: float = 0.75):
    """Ordnet entfallene und neue Preise einander zu, wenn Preis, Einheit, Währung,
    Preistyp und Region identisch sind und Name oder Schlüssel ähnlich sind (eindeutig 1:1)."""
    renamed, used_new, keep_removed = [], set(), []
    by_sig: dict[tuple, list[int]] = {}
    for i, rec in enumerate(new):
        by_sig.setdefault((round(rec.price, 12), rec.unit, rec.currency, rec.price_type, rec.region), []).append(i)
    for old, reason in removed:
        if reason != R_REMOVED:
            keep_removed.append((old, reason))
            continue
        cands = [i for i in by_sig.get((round(old["price"], 12), old["unit"], old["currency"],
                                         old["price_type"], old["region"]), []) if i not in used_new]
        scored = sorted(((max(_similar(old["product"], new[i].product), _similar(old["key"], new[i].key)), i)
                         for i in cands), reverse=True)
        if scored and scored[0][0] >= min_sim and (len(scored) == 1 or scored[1][0] < scored[0][0] - 0.05):
            i = scored[0][1]
            used_new.add(i)
            renamed.append((old, new[i]))
        else:
            keep_removed.append((old, reason))
    remaining_new = [r for i, r in enumerate(new) if i not in used_new]
    return renamed, keep_removed, remaining_new


def compute_diff(con, provider: str, result: FetchResult, guard: dict | None = None) -> Diff:
    guard = guard or {}
    min_ratio = float(guard.get("min_ratio", 0.5))
    max_removed = float(guard.get("max_removed_ratio", 0.2))
    min_scope = int(guard.get("min_scope_size", 3))

    incoming: dict[str, PriceRecord] = {}
    d = Diff()
    for r in result.records:
        if r.key in incoming:
            d.n_dupes += 1
        incoming[r.key] = r
    d.n_fetched = len(incoming)

    existing = {row["key"]: row for row in
                con.execute("SELECT * FROM prices_current WHERE provider=?", (provider,))}
    d.n_existing = len(existing)
    if not incoming:
        raise SyncAborted("Abruf lieferte keine Preise – Bestand bleibt unverändert.")

    scopes_ok = result.effective_scopes_ok()
    failed = set(result.scopes_failed)
    legacy = result.scopes_ok is None

    for key, rec in incoming.items():
        old = existing.get(key)
        if old is None:
            d.new.append(rec)
        elif _changed(old, rec):
            d.changed.append((old, rec))
        else:
            d.unchanged.append((old, rec))

    removed = []
    for key, old in existing.items():
        if key in incoming:
            continue
        scope = old["scope"]
        if scope in failed:
            continue
        if legacy or scope in scopes_ok:
            removed.append((old, R_REMOVED))
        else:
            removed.append((old, R_OUT_OF_SCOPE))
    d.renamed, d.removed, d.new = _detect_renames(removed, d.new)

    # --- Schutzregeln
    if existing:
        if len(incoming) < min_ratio * len(existing):
            d.violations.append(f"Nur {len(incoming)} Preise geliefert, bisher {len(existing)} "
                                f"(< {int(min_ratio * 100)} %).")
        if len(existing) >= 10 and len(d.removed) > max_removed * len(existing):
            d.violations.append(f"{len(d.removed)} von {len(existing)} Preisen würden entfallen "
                                f"(> {int(max_removed * 100)} %).")
        per_scope_old: dict[str, int] = {}
        for old in existing.values():
            per_scope_old[old["scope"]] = per_scope_old.get(old["scope"], 0) + 1
        per_scope_new: dict[str, int] = {}
        for rec in incoming.values():
            per_scope_new[rec.scope] = per_scope_new.get(rec.scope, 0) + 1
        for scope in sorted(scopes_ok):
            if per_scope_old.get(scope, 0) >= min_scope and per_scope_new.get(scope, 0) == 0:
                d.violations.append(f"Bereich „{scope or 'gesamt'}“ lieferte keine Preise mehr "
                                    f"(bisher {per_scope_old[scope]}).")
        cur_switch = sum(1 for old, rec in d.changed if old["currency"] != rec.currency)
        if cur_switch:
            d.violations.append(f"Währungswechsel bei {cur_switch} Preisen.")
    return d


# ------------------------------------------------------------------ Vorschau
def _rec_to_json(rec: PriceRecord) -> str:
    return json.dumps(asdict(rec), ensure_ascii=False, sort_keys=True)


def _write_changes(con, run_id: int, provider: str, d: Diff) -> None:
    con.execute("DELETE FROM run_changes WHERE run_id=?", (run_id,))
    rows = []
    for rec in d.new:
        rows.append((run_id, provider, rec.key, "neu", rec.category, rec.product, rec.region, rec.unit,
                     rec.currency, None, rec.price, None, ""))
    for old, rec in d.changed:
        rows.append((run_id, provider, rec.key, "geändert", rec.category, rec.product, rec.region,
                     rec.unit, rec.currency, old["price"], rec.price, None, R_CHANGED))
    for old, reason in d.removed:
        rows.append((run_id, provider, old["key"], "entfallen", old["category"], old["product"],
                     old["region"], old["unit"], old["currency"], old["price"], None, None, reason))
    for old, rec in d.renamed:
        rows.append((run_id, provider, rec.key, "umbenannt", rec.category, rec.product, rec.region,
                     rec.unit, rec.currency, old["price"], rec.price, old["key"], "Schlüssel/Name geändert"))
    con.executemany("""INSERT INTO run_changes (run_id, provider, key, change, category, product, region,
                       unit, currency, old_price, new_price, old_key, reason)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)


def stage(db: Database, provider: str, run_id: int, result: FetchResult, guard: dict | None = None) -> Diff:
    """Speichert das Abrufergebnis als Vorschau und berechnet die geplanten Änderungen."""
    with db.connect() as con:
        d = compute_diff(con, provider, result, guard)
        con.execute("DELETE FROM staging_records WHERE run_id=?", (run_id,))
        seen = {}
        for r in result.records:
            seen[r.key] = r
        con.executemany("INSERT INTO staging_records (run_id, provider, key, data) VALUES (?,?,?,?)",
                        [(run_id, provider, k, _rec_to_json(r)) for k, r in seen.items()])
        meta = {"scopes_ok": None if result.scopes_ok is None else sorted(result.scopes_ok),
                "scopes_failed": result.scopes_failed, "source_date": result.source_date,
                "source": result.source}
        con.execute("INSERT INTO staging_records (run_id, provider, key, data) VALUES (?,?,?,?)",
                    (run_id, provider, META_KEY, json.dumps(meta, ensure_ascii=False)))
        _write_changes(con, run_id, provider, d)
    return d


def load_staged(con, run_id: int) -> FetchResult:
    recs, meta = [], {}
    for row in con.execute("SELECT key, data FROM staging_records WHERE run_id=?", (run_id,)):
        if row["key"] == META_KEY:
            meta = json.loads(row["data"])
        else:
            recs.append(PriceRecord(**json.loads(row["data"])))
    if not recs:
        raise SyncAborted("Keine Vorschau-Daten zu diesem Abruf vorhanden.")
    scopes_ok = meta.get("scopes_ok")
    return FetchResult(records=recs, source=meta.get("source", ""), source_date=meta.get("source_date", ""),
                       scopes_ok=None if scopes_ok is None else set(scopes_ok),
                       scopes_failed=meta.get("scopes_failed", {}))


# ------------------------------------------------------------------ Übernahme
def _archive(con, old, reason: str, ts: str, run_id: int, new_price: float | None):
    con.execute(
        """INSERT INTO prices_archive
           (provider, key, category, service, product, region, unit, price, currency, price_type,
            attributes, valid_from, valid_to, reason, new_price, run_id, scope, unit_norm, price_eur, price_norm)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (old["provider"], old["key"], old["category"], old["service"], old["product"], old["region"],
         old["unit"], old["price"], old["currency"], old["price_type"], old["attributes"],
         old["valid_from"], ts, reason, new_price, run_id, old["scope"], old["unit_norm"],
         old["price_eur"], old["price_norm"]),
    )


def _norm(provider: str, rec: PriceRecord, rates: dict) -> dict:
    return normalize(provider, rec.category, rec.product, rec.unit, rec.price, rec.currency,
                     rec.attributes, rates)


def apply_staged(db: Database, provider: str, run_id: int, rates: dict[str, float]) -> Diff:
    """Übernimmt die Vorschau eines Abrufs in den aktuellen Bestand (eine Transaktion)."""
    ts = now_iso()
    with db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        result = load_staged(con, run_id)
        d = compute_diff(con, provider, result)   # gegen den jetzt aktuellen Stand neu berechnen

        def upsert_values(rec: PriceRecord):
            n = _norm(provider, rec, rates)
            return (rec.category, rec.service, rec.product, rec.region, rec.unit, rec.price, rec.currency,
                    rec.price_type, json.dumps(rec.attributes, ensure_ascii=False, sort_keys=True),
                    rec.scope, n["unit_norm"], n["price_eur"], n["price_norm"], n["vcpu"], n["ram_gb"], n["os"])

        cols = ("category, service, product, region, unit, price, currency, price_type, attributes, scope, "
                "unit_norm, price_eur, price_norm, vcpu, ram_gb, os")
        set_clause = ", ".join(f"{c.strip()}=?" for c in cols.split(","))

        for rec in d.new:
            con.execute(f"""INSERT INTO prices_current (provider, key, {cols}, valid_from, last_checked, source_date)
                            VALUES (?,?,{','.join('?' * 16)},?,?,?)""",
                        (provider, rec.key, *upsert_values(rec), ts, ts, result.source_date))
        for old, rec in d.changed:
            _archive(con, old, R_CHANGED, ts, run_id, rec.price)
            con.execute(f"UPDATE prices_current SET {set_clause}, valid_from=?, last_checked=?, source_date=? WHERE id=?",
                        (*upsert_values(rec), ts, ts, result.source_date, old["id"]))
        for old, rec in d.unchanged:
            con.execute(f"UPDATE prices_current SET {set_clause}, last_checked=?, source_date=? WHERE id=?",
                        (*upsert_values(rec), ts, result.source_date, old["id"]))
        for old, rec in d.renamed:
            con.execute(f"UPDATE prices_current SET key=?, {set_clause}, last_checked=?, source_date=? WHERE id=?",
                        (rec.key, *upsert_values(rec), ts, result.source_date, old["id"]))
            con.execute("UPDATE prices_archive SET key=? WHERE provider=? AND key=?", (rec.key, provider, old["key"]))
            con.execute("UPDATE scenario_items SET key=? WHERE provider=? AND key=?", (rec.key, provider, old["key"]))
        for old, reason in d.removed:
            _archive(con, old, reason, ts, run_id, None)
            con.execute("DELETE FROM prices_current WHERE id=?", (old["id"],))

        _write_changes(con, run_id, provider, d)
        con.execute("DELETE FROM staging_records WHERE run_id=?", (run_id,))
    return d


def discard(db: Database, run_id: int) -> None:
    with db.connect() as con:
        con.execute("DELETE FROM staging_records WHERE run_id=?", (run_id,))


def renormalize_all(db: Database, rates: dict[str, float]) -> int:
    """Berechnet die Normalisierung aller Preise neu (z. B. nach neuen Wechselkursen)."""
    n = 0
    with db.connect() as con:
        for table in ("prices_current", "prices_archive"):
            rows = con.execute(f"SELECT id, provider, category, product, unit, price, currency, attributes "
                               f"FROM {table}").fetchall()
            for r in rows:
                v = normalize(r["provider"], r["category"], r["product"], r["unit"], r["price"],
                              r["currency"], json.loads(r["attributes"] or "{}"), rates)
                if table == "prices_current":
                    con.execute("UPDATE prices_current SET unit_norm=?, price_eur=?, price_norm=?, vcpu=?, "
                                "ram_gb=?, os=? WHERE id=?", (v["unit_norm"], v["price_eur"], v["price_norm"],
                                                              v["vcpu"], v["ram_gb"], v["os"], r["id"]))
                else:
                    con.execute("UPDATE prices_archive SET unit_norm=?, price_eur=?, price_norm=? WHERE id=?",
                                (v["unit_norm"], v["price_eur"], v["price_norm"], r["id"]))
                n += 1
    return n


def apply_result(db: Database, provider: str, result: FetchResult, run_id: int,
                 rates: dict[str, float] | None = None, guard: dict | None = None,
                 force: bool = False) -> dict[str, int]:
    """Kurzform: stagen und – sofern keine Schutzregel verletzt ist oder `force` – übernehmen."""
    d = stage(db, provider, run_id, result, guard)
    if d.violations and not force:
        raise SyncAborted(" ".join(d.violations))
    stats = apply_staged(db, provider, run_id, rates or {"EUR": 1.0}).stats()
    stats["n_dupes"] = d.n_dupes
    return stats
