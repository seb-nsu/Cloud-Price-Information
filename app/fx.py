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
