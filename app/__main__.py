"""Kommandozeile der Preisverwaltung.

    python -m app serve                        Web-Oberfläche starten
    python -m app refresh all                  alle Anbieter abrufen (Vorschau gemäß config.toml)
    python -m app refresh azure --uebernehmen  abrufen und ohne Vorschau übernehmen (Schutzregeln gelten)
    python -m app freigeben 42                 Vorschau von Abruf 42 übernehmen
    python -m app verwerfen 42                 Vorschau von Abruf 42 verwerfen
    python -m app kurse                        EZB-Wechselkurse laden und Normierung neu berechnen
    python -m app backup /pfad/preise.db       konsistente Datensicherung (inkl. WAL)
    python -m app aufraeumen                   Archiv-Aufbewahrung jetzt anwenden
"""
from __future__ import annotations

import argparse
import logging
import sys

from . import config, fx, jobs, retention, web
from .db import Database
from .providers import PROVIDERS
from .sync import renormalize_all


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="preisverwaltung", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="Web-Oberfläche starten")
    s.add_argument("--host", default=config.HOST)
    s.add_argument("--port", type=int, default=config.PORT)
    r = sub.add_parser("refresh", help="Preise abrufen")
    r.add_argument("providers", nargs="+", help=f"all oder: {', '.join(PROVIDERS)}")
    r.add_argument("--uebernehmen", action="store_true", help="ohne Vorschau übernehmen (Schutzregeln gelten weiter)")
    for name in ("freigeben", "verwerfen"):
        p = sub.add_parser(name, help=f"Vorschau eines Abrufs {name}")
        p.add_argument("run_id", type=int)
    sub.add_parser("kurse", help="EZB-Kurse laden")
    b = sub.add_parser("backup", help="Datensicherung")
    b.add_argument("ziel")
    sub.add_parser("aufraeumen", help="Archiv-Aufbewahrung anwenden")
    args = ap.parse_args(argv)

    db = Database(config.DB_PATH)
    if args.cmd == "serve":
        web.serve(db, args.host, args.port)
        return 0
    if args.cmd in ("freigeben", "verwerfen"):
        print(jobs.decide(db, args.run_id, args.cmd == "freigeben", by="Kommandozeile"))
        return 0
    if args.cmd == "kurse":
        print(fx.refresh(db, force=True))
        print(f"{renormalize_all(db, fx.get_rates(db))} Preise neu normiert")
        return 0
    if args.cmd == "backup":
        db.backup(args.ziel)
        print(f"Sicherung geschrieben: {args.ziel}")
        return 0
    if args.cmd == "aufraeumen":
        print(f"{retention.apply_retention(db)} Archiveinträge exportiert und gelöscht")
        return 0

    names = list(PROVIDERS) if "all" in args.providers else args.providers
    unknown = [n for n in names if n not in PROVIDERS]
    if unknown:
        ap.error(f"Unbekannte Anbieter: {', '.join(unknown)}")
    failed = 0
    for name in names:
        res = jobs.run_provider(db, name, triggered_by="Kommandozeile",
                                auto_apply=True if args.uebernehmen else None)
        print(f"{name:8} {res['status']:9} abgerufen={res.get('n_fetched', 0)} neu={res.get('n_new', 0)} "
              f"geändert={res.get('n_changed', 0)} entfallen={res.get('n_removed', 0)} "
              f"umbenannt={res.get('n_renamed', 0)} – {res.get('message', '')}")
        if res["status"] == "review":
            print(f"         -> Vorschau: Abruf {res['id']} – freigeben mit: python -m app freigeben {res['id']}")
        failed += res["status"] == "error"
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
