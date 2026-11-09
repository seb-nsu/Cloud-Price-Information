"""Preisänderungs-Bericht je Monat oder Quartal (Top-Erhöhungen, Top-Senkungen, neu, entfallen).

Grundlage sind die endgültigen Änderungen übernommener Abrufe (`run_changes` von Läufen mit
Status „ok“), zeitlich eingeordnet nach dem Zeitpunkt der Übernahme.
"""
from __future__ import annotations

import re
import statistics
from datetime import date, datetime, timezone

from .db import Database

_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_QUARTER = re.compile(r"^(\d{4})-Q([1-4])$", re.I)
_YEAR = re.compile(r"^(\d{4})$")


def parse_period(value: str | None) -> tuple[str, str, str]:
    """'2026-10' | '2026-Q4' | '2026' -> (Start, Ende exklusiv, Bezeichnung). Standard: aktueller Monat."""
    v = (value or "").strip()
    m = _MONTH.match(v)
    if m and 1 <= int(m.group(2)) <= 12:
        y, mo = int(m.group(1)), int(m.group(2))
        start = date(y, mo, 1)
        end = date(y + (mo == 12), mo % 12 + 1, 1)
        return start.isoformat(), end.isoformat(), f"{mo:02d}/{y}"
    m = _QUARTER.match(v)
    if m:
        y, q = int(m.group(1)), int(m.group(2))
        start = date(y, 3 * q - 2, 1)
        end = date(y + (q == 4), (3 * q) % 12 + 1, 1)
        return start.isoformat(), end.isoformat(), f"Q{q}/{y}"
    m = _YEAR.match(v)
    if m:
        y = int(m.group(1))
        return date(y, 1, 1).isoformat(), date(y + 1, 1, 1).isoformat(), str(y)
    today = datetime.now(timezone.utc).date()
    return parse_period(f"{today.year}-{today.month:02d}")


def period_options(db: Database, n_months: int = 18) -> list[tuple[str, str]]:
    """Auswahlliste: Monate und Quartale mit übernommenen Abrufen (neueste zuerst)."""
    with db.connect() as con:
        months = [r[0] for r in con.execute(
            "SELECT DISTINCT substr(COALESCE(decided_at, finished_at), 1, 7) FROM fetch_runs "
            "WHERE status='ok' AND COALESCE(decided_at, finished_at) IS NOT NULL ORDER BY 1 DESC LIMIT ?",
            (n_months,))]
    today = datetime.now(timezone.utc).date()
    cur = f"{today.year}-{today.month:02d}"
    if cur not in months:
        months.insert(0, cur)
    opts, quarters = [], []
    for mo in months:
        y, m = mo.split("-")
        q = f"{y}-Q{(int(m) - 1) // 3 + 1}"
        if q not in quarters:
            quarters.append(q)
        opts.append((mo, f"{m}/{y}"))
    opts += [(q, f"{q[-2:]}/{q[:4]}") for q in quarters]
    return opts


def changes(db: Database, period: str | None, provider: str = "", category: str = "") -> list[dict]:
    start, end, _ = parse_period(period)
    sql = ("SELECT c.*, COALESCE(r.decided_at, r.finished_at) AS changed_at FROM run_changes c "
           "JOIN fetch_runs r ON r.id = c.run_id WHERE r.status='ok' "
           "AND COALESCE(r.decided_at, r.finished_at) >= ? AND COALESCE(r.decided_at, r.finished_at) < ? "
           "AND c.change != 'umbenannt'")
    args: list = [start, end]
    if provider:
        sql += " AND c.provider=?"
        args.append(provider)
    if category:
        sql += " AND c.category=?"
        args.append(category)
    out = []
    with db.connect() as con:
        # Erstimporte (Lauf ohne vorherigen Bestand) zählen nicht als „neu“ im Bericht
        first_runs = {r[0] for r in con.execute(
            "SELECT MIN(id) FROM fetch_runs WHERE status='ok' GROUP BY provider")}
        for r in con.execute(sql + " ORDER BY changed_at", args):
            d = dict(r)
            if d["change"] == "neu" and d["run_id"] in first_runs:
                continue
            if d["change"] == "geändert" and d["old_price"]:
                d["pct"] = (d["new_price"] - d["old_price"]) / d["old_price"] * 100
            else:
                d["pct"] = None
            out.append(d)
    return out


def build(db: Database, period: str | None, provider: str = "", category: str = "", top: int = 20) -> dict:
    start, end, label = parse_period(period)
    rows = changes(db, period, provider, category)
    changed = [r for r in rows if r["change"] == "geändert" and r["pct"] is not None]
    per_provider: dict[str, dict] = {}
    for r in rows:
        p = per_provider.setdefault(r["provider"], {"up": 0, "down": 0, "neu": 0, "entfallen": 0, "pcts": []})
        if r["change"] == "geändert" and r["pct"] is not None:
            p["up" if r["pct"] > 0 else "down"] += 1
            p["pcts"].append(r["pct"])
        elif r["change"] in ("neu", "entfallen"):
            p[r["change"]] += 1
    for p in per_provider.values():
        pcts = p.pop("pcts")
        p["median"] = statistics.median(pcts) if pcts else None
        p["max_up"] = max(pcts) if pcts else None
    return {
        "start": start, "end": end, "label": label,
        "summary": dict(sorted(per_provider.items())),
        "increases": sorted((r for r in changed if r["pct"] > 0), key=lambda r: -r["pct"])[:top],
        "decreases": sorted((r for r in changed if r["pct"] < 0), key=lambda r: r["pct"])[:top],
        "removed": [r for r in rows if r["change"] == "entfallen"][:top],
        "new": [r for r in rows if r["change"] == "neu"][:top],
        "counts": {"geändert": len(changed), "neu": sum(r["change"] == "neu" for r in rows),
                   "entfallen": sum(r["change"] == "entfallen" for r in rows)},
        "rows": rows,
    }
