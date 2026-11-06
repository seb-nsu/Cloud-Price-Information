"""Erzeugt eine Beispiel-Datenbank und CSV-Exporte aus den Test-Fixtures (ohne Netzwerk).

    python scripts/beispieldaten.py data/beispiel

Ergebnis: <ziel>/preise_beispiel.db sowie CSV-Exporte der aktuellen Preise, des Archivs
und des Abrufprotokolls. Zwei Abrufe werden simuliert (Erstimport + Preisänderungen),
damit Archiv, Vorschau/Freigabe, Verlauf und Vergleich gefüllt sind.
Die Daten stammen aus einem echten STACKIT-PIM-Ausschnitt (Juni 2026) und einem
nachgebauten Auszug der IONOS-Preisliste – sie sind NICHT aktuell.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import fx, jobs, pages  # noqa: E402
from app.db import Database  # noqa: E402
from app.models import FetchResult  # noqa: E402
from app.providers import PROVIDERS, ionos, stackit  # noqa: E402
from app.sync import renormalize_all  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def main(target: str) -> None:
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    dbfile = out / "preise_beispiel.db"
    for f in out.glob("preise_beispiel.db*"):
        f.unlink()
    db = Database(dbfile)
    with db.connect() as con:   # feste Beispielkurse statt EZB-Abruf
        con.execute("INSERT OR REPLACE INTO fx_rates VALUES ('USD', 1.085, '2026-11-05', '2026-11-06T00:00:00+00:00', 'Beispiel')")

    data = json.loads((FIX / "stackit_skus.json").read_text())
    md = (FIX / "ionos_pricelist.md").read_text()
    PROVIDERS["stackit"].fetch = lambda cfg: FetchResult(stackit.parse(data, {})[0], scopes_ok={"alle"},
                                                         source="Beispiel (STACKIT PIM, Juni 2026)")
    PROVIDERS["ionos"].fetch = lambda cfg: FetchResult(ionos.parse(md), scopes_ok={"preisliste"},
                                                       source="Beispiel (IONOS-Preisliste, Auszug)")
    with mock.patch.object(jobs.fx, "refresh", return_value="Kurse aktuell"):
        jobs.run_provider(db, "stackit", "Beispieldaten")
        jobs.run_provider(db, "ionos", "Beispieldaten")
        # simulierte Preisänderungen
        data["services"][0]["price"] = "0.0289"
        data["services"][5]["price"] = "0.0101"
        md = md.replace("0,012€", "0,013€")
        r = jobs.run_provider(db, "stackit", "Beispieldaten")
        jobs.decide(db, r["id"], True, by="Beispieldaten")
        r = jobs.run_provider(db, "ionos", "Beispieldaten")
        jobs.decide(db, r["id"], True, by="Beispieldaten")
        # eine offene Vorschau zum Ausprobieren der Freigabe
        data["services"][10]["price"] = str(float(data["services"][10]["price"]) * 1.1)
        jobs.run_provider(db, "stackit", "Beispieldaten")
    renormalize_all(db, fx.get_rates(db))

    (out / "preise_aktuell.csv").write_bytes(pages.export_csv(db, "prices_current", {}))
    (out / "preise_archiv.csv").write_bytes(pages.export_csv(db, "prices_archive", {}))
    with db.connect() as con:
        cur = con.execute("SELECT * FROM fetch_runs ORDER BY id")
        cols = [d[0] for d in cur.description]
        (out / "abrufprotokoll.csv").write_bytes(pages._csv(cols, cur, set()))
    db.backup(out / "preise_beispiel.db.tmp")
    for f in out.glob("preise_beispiel.db-*"):
        f.unlink()
    (out / "preise_beispiel.db.tmp").replace(dbfile)
    print(f"Beispieldaten geschrieben nach {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/beispiel")
