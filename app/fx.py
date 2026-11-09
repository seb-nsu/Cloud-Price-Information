"""Wechselkurse: EZB-Referenzkurse (täglich), mit Fallback-Werten aus config.toml."""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from .config import load_config
from .db import Database, now_iso
from .http_util import get_text

log = logging.getLogger(__name__)
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
ECB_HIST_90D_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
ECB_HIST_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml"
SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF", "JPY": "¥"}


def parse_ecb(xml_text: str) -> tuple[str, dict[str, float]]:
    root = ET.fromstring(xml_text)
    date, rates = "", {}
    for el in root.iter():
        if el.tag.endswith("Cube"):
            if "time" in el.attrib:
                date = el.attrib["time"]
            if "currency" in el.attrib and "rate" in el.attrib:
                rates[el.attrib["currency"]] = float(el.attrib["rate"])
    if not rates:
        raise ValueError("Keine Kurse in der EZB-Antwort gefunden")
    return date, rates


def parse_ecb_history(xml_text: str) -> dict[str, dict[str, float]]:
    """EZB-Verlaufsdatei -> {Datum: {Währung: Kurs}}."""
    root = ET.fromstring(xml_text)
    out: dict[str, dict[str, float]] = {}
    for el in root.iter():
        if el.tag.endswith("Cube") and "time" in el.attrib:
            day = {c.attrib["currency"]: float(c.attrib["rate"]) for c in el
                   if "currency" in c.attrib and "rate" in c.attrib}
            if day:
                out[el.attrib["time"]] = day
    if not out:
        raise ValueError("Keine Kurse in der EZB-Verlaufsdatei gefunden")
    return out


def store_history(db: Database, history: dict[str, dict[str, float]]) -> int:
    rows = [(cur, date, rate) for date, day in history.items() for cur, rate in day.items()]
    with db.connect() as con:
        con.executemany("INSERT OR REPLACE INTO fx_history (currency, rate_date, rate) VALUES (?,?,?)", rows)
    return len(rows)


def refresh_history(db: Database, get=get_text, full: bool = False) -> str:
    """Lädt den EZB-Kursverlauf (90 Tage oder seit 1999) in `fx_history`."""
    cfg = load_config().get("waehrung", {})
    url = cfg.get("ezb_verlauf_url") or (ECB_HIST_URL if full else ECB_HIST_90D_URL)
    hist = parse_ecb_history(get(url))
    n = store_history(db, hist)
    days = sorted(hist)
    log.info("EZB-Kursverlauf %s bis %s geladen (%d Werte)", days[0], days[-1], n)
    return f"EZB-Kursverlauf {days[0]} bis {days[-1]} geladen ({len(days)} Tage)"


def rate_at(db: Database, currency: str, date: str) -> float | None:
    """Kurs (Einheiten je 1 EUR) am Tag `date` oder am letzten Handelstag davor."""
    if currency == "EUR":
        return 1.0
    with db.connect() as con:
        r = con.execute("SELECT rate FROM fx_history WHERE currency=? AND rate_date<=? ORDER BY rate_date DESC "
                        "LIMIT 1", (currency, date[:10])).fetchone()
        if r is None:   # vor dem ersten bekannten Kurs: ältesten verwenden
            r = con.execute("SELECT rate FROM fx_history WHERE currency=? ORDER BY rate_date LIMIT 1",
                            (currency,)).fetchone()
    return r["rate"] if r else None


def history_points(db: Database, currency: str, since: str = "") -> list[tuple[str, float]]:
    with db.connect() as con:
        return [(r[0], r[1]) for r in con.execute(
            "SELECT rate_date, rate FROM fx_history WHERE currency=? AND rate_date>=? ORDER BY rate_date",
            (currency, since[:10]))]


def display_currencies() -> list[str]:
    """Zusätzliche Anzeigewährungen neben EUR (config: [waehrung] anzeige = ["USD"])."""
    cur = load_config().get("waehrung", {}).get("anzeige", ["USD"])
    return [c.upper() for c in cur if isinstance(c, str) and c.upper() != "EUR"][:3]


def convert(eur: float | None, currency: str, rates: dict[str, float]) -> float | None:
    if eur is None:
        return None
    if currency == "EUR":
        return eur
    rate = rates.get(currency)
    return eur * rate if rate else None


def refresh(db: Database, get=get_text, force: bool = False, max_age_hours: int = 20) -> str:
    """Lädt die EZB-Kurse, wenn sie älter als `max_age_hours` sind. Gibt eine Statusmeldung zurück."""
    with db.connect() as con:
        row = con.execute("SELECT MAX(fetched_at) FROM fx_rates WHERE source='EZB'").fetchone()
    if not force and row[0]:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(row[0])
        if age < timedelta(hours=max_age_hours):
            return "Kurse aktuell"
    url = load_config().get("waehrung", {}).get("ezb_url", ECB_URL)
    date, rates = parse_ecb(get(url))
    ts = now_iso()
    with db.connect() as con:
        for cur, rate in rates.items():
            con.execute("INSERT OR REPLACE INTO fx_rates (currency, rate, rate_date, fetched_at, source) "
                        "VALUES (?,?,?,?,?)", (cur, rate, date, ts, "EZB"))
            if date:
                con.execute("INSERT OR REPLACE INTO fx_history (currency, rate_date, rate) VALUES (?,?,?)",
                            (cur, date, rate))
        has_hist = con.execute("SELECT COUNT(DISTINCT rate_date) FROM fx_history").fetchone()[0]
    if has_hist < 5 and load_config().get("waehrung", {}).get("verlauf_laden", True):
        try:   # beim ersten Mal die letzten 90 Tage nachladen
            refresh_history(db, get)
        except Exception as exc:  # noqa: BLE001
            log.warning("EZB-Kursverlauf nicht geladen: %s", exc)
    log.info("EZB-Kurse vom %s geladen (%d Währungen)", date, len(rates))
    return f"EZB-Kurse vom {date} geladen"


def get_rates(db: Database) -> dict[str, float]:
    """Einheiten je 1 EUR. EZB-Werte haben Vorrang vor den Fallback-Werten aus der Konfiguration."""
    rates = {k.upper(): float(v) for k, v in
             load_config().get("waehrung", {}).get("fallback_kurse", {}).items()}
    with db.connect() as con:
        for r in con.execute("SELECT currency, rate FROM fx_rates"):
            rates[r["currency"]] = r["rate"]
    rates["EUR"] = 1.0
    return rates


def rates_info(db: Database) -> dict | None:
    with db.connect() as con:
        r = con.execute("SELECT rate, rate_date, source FROM fx_rates WHERE currency='USD'").fetchone()
    return dict(r) if r else None
