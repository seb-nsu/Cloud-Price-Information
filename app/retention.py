"""Aufbewahrung des Archivs: ältere Einträge werden vor dem Löschen als CSV.gz exportiert."""
from __future__ import annotations

import csv
import gzip
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import DB_PATH, load_config
from .db import Database

log = logging.getLogger(__name__)


def apply_retention(db: Database, export_dir: Path | None = None, months: int | None = None) -> int:
    if months is None:
        months = int(load_config().get("general", {}).get("archiv_aufbewahrung_monate", 0) or 0)
    if months <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=round(months * 30.44))).replace(microsecond=0).isoformat()
    export_dir = export_dir or Path(DB_PATH).parent / "archiv-export"
    with db.connect() as con:
        cur = con.execute("SELECT * FROM prices_archive WHERE valid_to < ? ORDER BY valid_to", (cutoff,))
        rows = cur.fetchall()
        if not rows:
            return 0
        cols = [d[0] for d in cur.description]
        export_dir.mkdir(parents=True, exist_ok=True)
        target = export_dir / f"archiv-bis-{cutoff[:10]}-{datetime.now().strftime('%H%M%S')}.csv.gz"
        with gzip.open(target, "wt", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(cols)
            w.writerows([tuple(r) for r in rows])
        con.execute("DELETE FROM prices_archive WHERE valid_to < ?", (cutoff,))
    log.info("Archiv-Aufbewahrung: %d Einträge nach %s exportiert und gelöscht", len(rows), target)
    return len(rows)
