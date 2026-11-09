"""Erzeugt eine Beispiel-Datenbank und CSV-Exporte aus den Test-Fixtures (ohne Netzwerk).

    python scripts/beispieldaten.py data/beispiel

Ergebnis: <ziel>/preise_beispiel.db sowie CSV-Exporte der aktuellen Preise, des Archivs
und des Abrufprotokolls. Zwei Abrufe werden simuliert (Erstimport + Preisänderungen),
damit Archiv, Vorschau/Freigabe, Verlauf und Vergleich gefüllt sind.
Die Daten stammen aus einem echten STACKIT-PIM-Ausschnitt (Juni 2026), einem nachgebauten
Auszug der IONOS-Preisliste sowie synthetischen Beispielantworten für Azure, AWS, Scaleway,
Hetzner und OVHcloud (tests/fixtures) – sie sind NICHT aktuell.
"""
from __future__ import annotations

import io
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import fx, jobs, pages  # noqa: E402
from app.db import Database  # noqa: E402
from app.models import FetchResult  # noqa: E402
from app.models import FetchResult as FR  # noqa: E402,F811
from app.providers import PROVIDERS, aws, azure, hetzner, ionos, ovh, scaleway, stackit  # noqa: E402
from app.sync import renormalize_all  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def dict_scope(rec, scope: str):
    rec.scope = scope
    return rec


def main(target: str) -> None:
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    dbfile = out / "preise_beispiel.db"
    for f in out.glob("preise_beispiel.db*"):
        f.unlink()
    db = Database(dbfile)
    with db.connect() as con:   # feste Beispielkurse statt EZB-Abruf, dazu ein synthetischer Kursverlauf
        con.execute("INSERT OR REPLACE INTO fx_rates VALUES ('USD', 1.085, '2026-11-05', '2026-11-06T00:00:00+00:00', 'Beispiel')")
        start = date(2026, 11, 5).toordinal()
        con.executemany("INSERT OR REPLACE INTO fx_history (currency, rate_date, rate) VALUES ('USD', ?, ?)",
                        [(date.fromordinal(start - i).isoformat(), round(1.085 + 0.03 * math.sin(i / 45), 4))
                         for i in range(0, 420) if date.fromordinal(start - i).weekday() < 5])

    data = json.loads((FIX / "stackit_skus.json").read_text())
    md = (FIX / "ionos_pricelist.md").read_text()
    az_items = json.loads((FIX / "azure_items.json").read_text())["Items"]
    aws_csv = (FIX / "aws_ec2.csv").read_text()
    scw = json.loads((FIX / "scaleway_products.json").read_text())
    hz_types = json.loads((FIX / "hetzner_server_types.json").read_text())
    hz_pricing = json.loads((FIX / "hetzner_pricing.json").read_text())
    ovh_cat = json.loads((FIX / "ovh_catalog.json").read_text())

    def aws_fetch(cfg):
        _, recs = aws.parse_offer("AmazonEC2", "eu-central-1", io.StringIO(aws_csv),
                                  {"term_types": ["OnDemand", "Reserved"]})
        for r in recs:
            r.scope = "AmazonEC2/eu-central-1"
        return FR(recs, scopes_ok={"AmazonEC2/eu-central-1"}, source="Beispiel (synthetisch)")

    def hz_get(url):
        return hz_pricing if url.endswith("/pricing") else hz_types

    PROVIDERS["stackit"].fetch = lambda cfg: FetchResult(stackit.parse(data, {})[0], scopes_ok={"alle"},
                                                         source="Beispiel (STACKIT PIM, Juni 2026)")
    PROVIDERS["ionos"].fetch = lambda cfg: FetchResult(ionos.parse(md), scopes_ok={"preisliste"},
                                                       source="Beispiel (IONOS-Preisliste, Auszug)")
    PROVIDERS["azure"].fetch = lambda cfg: FR([dict_scope(r, "Beispiel") for r in
                                               azure.parse_items(az_items, "EUR", include_savings_plan=True)],
                                              scopes_ok={"Beispiel"}, source="Beispiel (synthetisch)")
    PROVIDERS["aws"].fetch = aws_fetch
    scw_fetch, hz_fetch, ovh_fetch = scaleway.fetch, hetzner.fetch, ovh.fetch
    PROVIDERS["scaleway"].fetch = lambda cfg: scw_fetch({}, get=lambda url: scw)
    PROVIDERS["hetzner"].fetch = lambda cfg: hz_fetch({}, get=hz_get)
    PROVIDERS["ovh"].fetch = lambda cfg: ovh_fetch({}, get=lambda url: ovh_cat)
    names = ["stackit", "ionos", "azure", "aws", "scaleway", "hetzner", "ovh"]
    with mock.patch.object(jobs.fx, "refresh", return_value="Kurse aktuell"), \
            mock.patch.object(jobs, "load_config", return_value={"general": {"freigabe": "immer"}, "providers": {}}):
        for n in names:
            jobs.run_provider(db, n, "Beispieldaten")
        # simulierte Preisänderungen im Vormonat
        data["services"][0]["price"] = "0.0289"
        data["services"][5]["price"] = "0.0101"
        md = md.replace("0,012€", "0,013€")
        az_items[0]["retailPrice"] = 0.2290
        az_items[6]["retailPrice"] = 0.5490
        aws_csv = aws_csv.replace('"0.1150"', '"0.1190"')
        scw["products"][0]["price"]["retail_price"]["nanos"] = 112000000
        earlier = []
        for n in ("stackit", "ionos", "azure", "aws", "scaleway"):
            r = jobs.run_provider(db, n, "Beispieldaten")
            jobs.decide(db, r["id"], True, by="Beispieldaten")
            earlier.append(r["id"])
        with db.connect() as con:   # in den Vormonat verschieben (für Bericht und Verlauf)
            shift = (datetime.now(timezone.utc) - timedelta(days=31)).replace(microsecond=0).isoformat()
            first = (datetime.now(timezone.utc) - timedelta(days=75)).replace(microsecond=0).isoformat()
            con.execute("UPDATE fetch_runs SET started_at=?, finished_at=?, decided_at=? WHERE id < ?",
                        (first, first, first, min(earlier)))
            con.execute("UPDATE prices_current SET valid_from=?", (first,))
            for rid in earlier:
                con.execute("UPDATE fetch_runs SET started_at=?, finished_at=?, decided_at=? WHERE id=?",
                            (shift, shift, shift, rid))
                con.execute("UPDATE prices_archive SET valid_from=?, valid_to=? WHERE run_id=?", (first, shift, rid))
                con.execute("UPDATE prices_current SET valid_from=? WHERE (provider, key) IN "
                            "(SELECT provider, key FROM prices_archive WHERE run_id=?)", (shift, rid))
        # Änderungen im laufenden Monat
        az_items[4]["retailPrice"] = 0.1010
        hz_types["server_types"][0]["prices"][0]["price_monthly"]["net"] = "3.9900000000"
        for n in ("azure", "hetzner"):
            r = jobs.run_provider(db, n, "Beispieldaten")
            if r["status"] == "review":
                jobs.decide(db, r["id"], True, by="Beispieldaten")
        # eine offene Vorschau zum Ausprobieren der Freigabe
        data["services"][10]["price"] = str(float(data["services"][10]["price"]) * 1.1)
        jobs.run_provider(db, "stackit", "Beispieldaten")
    renormalize_all(db, fx.get_rates(db))

    # Beispiel-Szenario mit Wachstum
    with db.connect() as con:
        sid = con.execute("INSERT INTO scenarios (name, note, created_at, months, growth_pct) VALUES "
                          "('Plattform 2027', 'Beispiel: 4 VMs + 2 TB Objektspeicher, Speicher wächst 5 %/Monat', ?, 24, 0)",
                          (datetime.now(timezone.utc).replace(microsecond=0).isoformat(),)).lastrowid
        picks = [("A", "azure", "Standard_D4s_v5", 4, None), ("B", "scaleway", "POP2-4C-16G", 4, None),
                 ("C", "hetzner", "CCX23", 4, None)]
        for variant, prov, needle, qty, growth in picks:
            row = con.execute("SELECT key, product FROM prices_current WHERE provider=? AND price_type='On-Demand' "
                              "AND (attributes LIKE ? OR product LIKE ?) AND category='Compute' ORDER BY price LIMIT 1",
                              (prov, f"%{needle}%", f"%{needle}%")).fetchone()
            if row:
                con.execute("INSERT INTO scenario_items (scenario_id, variant, provider, key, quantity, label, growth_pct) "
                            "VALUES (?,?,?,?,?,?,?)", (sid, variant, prov, row["key"], qty, row["product"], growth))
        for variant, prov, needle in (("A", "azure", "Hot LRS"), ("B", "scaleway", "Object Storage"),
                                      ("C", "hetzner", "Volume (Block")):
            row = con.execute("SELECT key, product FROM prices_current WHERE provider=? AND product LIKE ? LIMIT 1",
                              (prov, f"%{needle}%")).fetchone()
            if row:
                con.execute("INSERT INTO scenario_items (scenario_id, variant, provider, key, quantity, label, growth_pct) "
                            "VALUES (?,?,?,?,?,?,5)", (sid, variant, prov, row["key"], 2000, row["product"]))

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
