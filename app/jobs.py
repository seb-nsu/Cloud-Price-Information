"""Abrufe starten, sperren, protokollieren und freigeben.

* Sperre über die Datenbank (Zeile mit Status `running` + Heartbeat) – gilt damit auch
  zwischen Web-Oberfläche und Kommandozeile (z. B. Cron per `docker exec`).
* Web-Abrufe laufen in einem eigenen Prozess, damit große Downloads/CSV-Verarbeitung
  (AWS) die Oberfläche nicht ausbremsen.
* Ergebnis wird als Vorschau gespeichert; je nach `freigabe`-Einstellung automatisch
  übernommen oder zur manuellen Freigabe vorgelegt.
"""
from __future__ import annotations

import logging
import multiprocessing
import os
import threading
import traceback
from datetime import datetime, timedelta, timezone

from . import fx, notify, retention
from .config import load_config
from .db import Database, now_iso
from .models import ProviderNotConfigured
from .providers import PROVIDERS
from .sync import SyncAborted, apply_staged, discard, renormalize_all, stage

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 30
STALE_AFTER = timedelta(minutes=15)

ST_RUNNING, ST_REVIEW, ST_OK, ST_ERROR, ST_SKIPPED, ST_DISCARDED = (
    "running", "review", "ok", "error", "skipped", "verworfen")


def _stale_cutoff() -> str:
    return (datetime.now(timezone.utc) - STALE_AFTER).replace(microsecond=0).isoformat()


def cleanup_stale(db: Database) -> int:
    """Markiert Abrufe ohne Lebenszeichen als abgebrochen."""
    with db.connect() as con:
        cur = con.execute(
            "UPDATE fetch_runs SET status=?, finished_at=?, message='Abgebrochen (keine Rückmeldung mehr)' "
            "WHERE status=? AND COALESCE(heartbeat, started_at) < ?",
            (ST_ERROR, now_iso(), ST_RUNNING, _stale_cutoff()))
        return cur.rowcount


def is_running(db: Database, name: str) -> bool:
    with db.connect() as con:
        return con.execute("SELECT 1 FROM fetch_runs WHERE provider=? AND status=? AND "
                           "COALESCE(heartbeat, started_at) >= ?",
                           (name, ST_RUNNING, _stale_cutoff())).fetchone() is not None


def acquire(db: Database, name: str, triggered_by: str) -> int | None:
    """Legt einen neuen Abruf an, sofern für den Anbieter keiner läuft. -> run_id oder None."""
    cleanup_stale(db)
    with db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute("SELECT 1 FROM fetch_runs WHERE provider=? AND status=?",
                       (name, ST_RUNNING)).fetchone():
            return None
        ts = now_iso()
        cur = con.execute("INSERT INTO fetch_runs (provider, started_at, status, triggered_by, heartbeat, pid) "
                          "VALUES (?,?,?,?,?,?)", (name, ts, ST_RUNNING, triggered_by, ts, os.getpid()))
        return cur.lastrowid


class _Heartbeat:
    def __init__(self, db: Database, run_id: int):
        self.db, self.run_id, self._stop = db, run_id, threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                with self.db.connect() as con:
                    con.execute("UPDATE fetch_runs SET heartbeat=? WHERE id=?", (now_iso(), self.run_id))
            except Exception:  # noqa: BLE001
                log.warning("Heartbeat konnte nicht geschrieben werden", exc_info=True)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()


def _finish(db: Database, run_id: int, status: str, message: str, stats: dict, source: str = "",
            warnings: str = "", violations: str = "", source_date: str = "") -> None:
    values = (now_iso(), status, message[:2000], source, stats.get("n_fetched", 0), stats.get("n_new", 0),
              stats.get("n_changed", 0), stats.get("n_removed", 0), stats.get("n_unchanged", 0),
              stats.get("n_renamed", 0), stats.get("n_dupes", 0), warnings[:4000], violations[:2000],
              source_date, run_id)
    sql = ("UPDATE fetch_runs SET finished_at=?, status=?, message=?, source=?, n_fetched=?, n_new=?, "
           "n_changed=?, n_removed=?, n_unchanged=?, n_renamed=?, n_dupes=?, warnings=?, violations=?, "
           "source_date=? WHERE id=?")
    for attempt in range(3):
        try:
            with db.connect() as con:
                con.execute(sql, values)
            return
        except Exception:  # noqa: BLE001
            log.exception("Abschluss von Lauf %s konnte nicht gespeichert werden (Versuch %d)", run_id, attempt + 1)


def _after_apply(db: Database, name: str, run_id: int) -> list[str]:
    msgs = []
    try:
        msgs += notify.send(db, PROVIDERS[name].LABEL, run_id)
    except Exception as exc:  # noqa: BLE001
        msgs.append(f"Benachrichtigung fehlgeschlagen: {exc}")
    try:
        n = retention.apply_retention(db)
        if n:
            msgs.append(f"{n} alte Archiveinträge exportiert und gelöscht")
    except Exception as exc:  # noqa: BLE001
        msgs.append(f"Archiv-Aufbewahrung fehlgeschlagen: {exc}")
    return msgs


def _rates(db: Database, msgs: list[str]) -> dict:
    try:
        status = fx.refresh(db)
        if status != "Kurse aktuell":
            renormalize_all(db, fx.get_rates(db))
    except Exception as exc:  # noqa: BLE001
        msgs.append(f"EZB-Kurse nicht aktualisiert ({type(exc).__name__}) – verwende gespeicherte/Fallback-Kurse")
    return fx.get_rates(db)


def run_provider(db: Database, name: str, triggered_by: str = "manuell", run_id: int | None = None,
                 auto_apply: bool | None = None) -> dict:
    """Führt einen Abruf synchron aus (Abruf -> Vorschau -> ggf. Übernahme)."""
    if name not in PROVIDERS:
        raise KeyError(name)
    if run_id is None:
        run_id = acquire(db, name, triggered_by)
        if run_id is None:
            return {"provider": name, "status": ST_SKIPPED, "message": "Abruf läuft bereits"}

    status, message, stats, source, source_date = ST_OK, "", {}, "", ""
    warnings, violations, extra = [], [], []
    with _Heartbeat(db, run_id):
        try:
            cfg = load_config()
            pcfg = cfg["providers"].get(name, {})
            guard = cfg.get("general", {})
            if not pcfg.get("enabled", True):
                raise ProviderNotConfigured("In config.toml deaktiviert")
            rates = _rates(db, extra)
            log.info("Abruf %s gestartet (Lauf %s)", name, run_id)
            result = PROVIDERS[name].fetch(pcfg)
            source, source_date, warnings = result.source, result.source_date, list(result.warnings)

            d = stage(db, name, run_id, result, guard)
            stats, violations = d.stats(), d.violations
            has_changes = any((d.new, d.changed, d.removed, d.renamed))
            mode = str(guard.get("freigabe", "immer")).lower()
            if auto_apply is None:
                auto_apply = (mode == "nie" or d.n_existing == 0 or not has_changes)
            if violations:
                status = ST_REVIEW
                message = "Schutzregel verletzt – bitte prüfen und ggf. trotzdem übernehmen: " + " ".join(violations)
            elif auto_apply or mode == "bei_warnung":
                stats = {**apply_staged(db, name, run_id, rates).stats(), "n_dupes": d.n_dupes}
                extra += _after_apply(db, name, run_id)
                message = "Übernommen"
            else:
                status = ST_REVIEW
                message = "Vorschau bereit – wartet auf Freigabe"
            _supersede_reviews(db, name, run_id)
        except ProviderNotConfigured as exc:
            status, message = ST_SKIPPED, str(exc)
        except SyncAborted as exc:
            status, message = ST_ERROR, str(exc)
        except Exception as exc:  # noqa: BLE001 – Fehler je Anbieter isolieren
            status, message = ST_ERROR, f"{type(exc).__name__}: {exc}"
            log.error("Abruf %s fehlgeschlagen:\n%s", name, traceback.format_exc())
        finally:
            if extra:
                message = (message + " · " if message else "") + " · ".join(extra)
            _finish(db, run_id, status, message, stats, source, "\n".join(warnings),
                    "\n".join(violations), source_date)
    with db.connect() as con:
        return dict(con.execute("SELECT * FROM fetch_runs WHERE id=?", (run_id,)).fetchone())


def _supersede_reviews(db: Database, name: str, keep_run: int) -> None:
    with db.connect() as con:
        old = [r[0] for r in con.execute("SELECT id FROM fetch_runs WHERE provider=? AND status=? AND id<>?",
                                         (name, ST_REVIEW, keep_run))]
        for rid in old:
            con.execute("DELETE FROM staging_records WHERE run_id=?", (rid,))
            con.execute("UPDATE fetch_runs SET status=?, decided_at=?, decided_by=? WHERE id=?",
                        (ST_DISCARDED, now_iso(), f"ersetzt durch Lauf {keep_run}", rid))


def decide(db: Database, run_id: int, approve: bool, by: str = "manuell") -> str:
    """Freigabe oder Verwerfen einer Vorschau."""
    with db.connect() as con:
        run = con.execute("SELECT * FROM fetch_runs WHERE id=?", (run_id,)).fetchone()
    if not run or run["status"] != ST_REVIEW:
        return "Dieser Abruf wartet nicht (mehr) auf eine Freigabe."
    name = run["provider"]
    if not approve:
        discard(db, run_id)
        with db.connect() as con:
            con.execute("UPDATE fetch_runs SET status=?, decided_at=?, decided_by=? WHERE id=?",
                        (ST_DISCARDED, now_iso(), by, run_id))
        return "Vorschau verworfen – Bestand unverändert."
    try:
        d = apply_staged(db, name, run_id, fx.get_rates(db))
    except SyncAborted as exc:
        return f"Übernahme nicht möglich: {exc}"
    msgs = _after_apply(db, name, run_id)
    s = d.stats()
    with db.connect() as con:
        con.execute("""UPDATE fetch_runs SET status=?, decided_at=?, decided_by=?, n_new=?, n_changed=?,
                       n_removed=?, n_unchanged=?, n_renamed=?, message=? WHERE id=?""",
                    (ST_OK, now_iso(), by, s["n_new"], s["n_changed"], s["n_removed"], s["n_unchanged"],
                     s["n_renamed"], " · ".join(["Übernommen"] + msgs), run_id))
    return (f"Übernommen: {s['n_new']} neu, {s['n_changed']} geändert, {s['n_removed']} entfallen, "
            f"{s['n_renamed']} umbenannt.")


# ------------------------------------------------------------------ Hintergrund
def _worker(db_path: str, name: str, run_id: int, triggered_by: str) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_provider(Database(db_path), name, triggered_by, run_id=run_id)


def start_background(db: Database, names: list[str], triggered_by: str = "manuell") -> list[str]:
    """Startet Abrufe in eigenen Prozessen; gibt die tatsächlich gestarteten Anbieter zurück."""
    started = []
    ctx = multiprocessing.get_context("spawn" if os.name == "nt" else "fork")
    for name in names:
        if name not in PROVIDERS:
            continue
        run_id = acquire(db, name, triggered_by)
        if run_id is None:
            continue
        p = ctx.Process(target=_worker, args=(db.path, name, run_id, triggered_by), name=f"abruf-{name}")
        p.start()
        threading.Thread(target=p.join, daemon=True).start()   # Zombie-Prozesse vermeiden
        started.append(name)
    return started
