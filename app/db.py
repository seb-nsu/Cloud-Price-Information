"""SQLite-Datenbank mit versionierten Migrationen (PRAGMA user_version)."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS prices_current (
    id            INTEGER PRIMARY KEY,
    provider      TEXT NOT NULL,
    key           TEXT NOT NULL,
    category      TEXT NOT NULL,
    service       TEXT NOT NULL,
    product       TEXT NOT NULL,
    region        TEXT NOT NULL,
    unit          TEXT NOT NULL,
    price         REAL NOT NULL,
    currency      TEXT NOT NULL,
    price_type    TEXT NOT NULL,
    attributes    TEXT NOT NULL DEFAULT '{}',
    valid_from    TEXT NOT NULL,
    last_checked  TEXT NOT NULL,
    source_date   TEXT NOT NULL DEFAULT '',
    UNIQUE (provider, key)
);
CREATE INDEX IF NOT EXISTS ix_cur_filter ON prices_current (provider, category, region);

CREATE TABLE IF NOT EXISTS prices_archive (
    id            INTEGER PRIMARY KEY,
    provider      TEXT NOT NULL,
    key           TEXT NOT NULL,
    category      TEXT NOT NULL,
    service       TEXT NOT NULL,
    product       TEXT NOT NULL,
    region        TEXT NOT NULL,
    unit          TEXT NOT NULL,
    price         REAL NOT NULL,
    currency      TEXT NOT NULL,
    price_type    TEXT NOT NULL,
    attributes    TEXT NOT NULL DEFAULT '{}',
    valid_from    TEXT NOT NULL,
    valid_to      TEXT NOT NULL,
    reason        TEXT NOT NULL,
    new_price     REAL,
    run_id        INTEGER
);
CREATE INDEX IF NOT EXISTS ix_arch_key ON prices_archive (provider, key);
CREATE INDEX IF NOT EXISTS ix_arch_time ON prices_archive (valid_to);

CREATE TABLE IF NOT EXISTS fetch_runs (
    id            INTEGER PRIMARY KEY,
    provider      TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL,
    n_fetched     INTEGER DEFAULT 0,
    n_new         INTEGER DEFAULT 0,
    n_changed     INTEGER DEFAULT 0,
    n_removed     INTEGER DEFAULT 0,
    n_unchanged   INTEGER DEFAULT 0,
    message       TEXT DEFAULT '',
    source        TEXT DEFAULT '',
    triggered_by  TEXT DEFAULT ''
);
"""

# Neue Spalten in Version 2: (Tabelle, Spalte, Definition)
V2_COLUMNS = [
    ("prices_current", "scope", "TEXT NOT NULL DEFAULT ''"),
    ("prices_current", "unit_norm", "TEXT NOT NULL DEFAULT ''"),
    ("prices_current", "price_eur", "REAL"),
    ("prices_current", "price_norm", "REAL"),
    ("prices_current", "vcpu", "REAL"),
    ("prices_current", "ram_gb", "REAL"),
    ("prices_current", "os", "TEXT NOT NULL DEFAULT ''"),
    ("prices_archive", "scope", "TEXT NOT NULL DEFAULT ''"),
    ("prices_archive", "unit_norm", "TEXT NOT NULL DEFAULT ''"),
    ("prices_archive", "price_eur", "REAL"),
    ("prices_archive", "price_norm", "REAL"),
    ("fetch_runs", "heartbeat", "TEXT"),
    ("fetch_runs", "pid", "INTEGER"),
    ("fetch_runs", "n_renamed", "INTEGER DEFAULT 0"),
    ("fetch_runs", "n_dupes", "INTEGER DEFAULT 0"),
    ("fetch_runs", "violations", "TEXT DEFAULT ''"),
    ("fetch_runs", "warnings", "TEXT DEFAULT ''"),
    ("fetch_runs", "source_date", "TEXT DEFAULT ''"),
    ("fetch_runs", "decided_at", "TEXT"),
    ("fetch_runs", "decided_by", "TEXT DEFAULT ''"),
]

SCHEMA_V2_TABLES = """
CREATE INDEX IF NOT EXISTS ix_arch_run ON prices_archive (run_id);
CREATE INDEX IF NOT EXISTS ix_cur_norm ON prices_current (unit_norm, price_norm);
CREATE INDEX IF NOT EXISTS ix_cur_vm ON prices_current (category, vcpu, ram_gb);
CREATE INDEX IF NOT EXISTS ix_runs_provider ON fetch_runs (provider, status);

-- Abrufergebnis, das auf Freigabe wartet (Vorschau)
CREATE TABLE IF NOT EXISTS staging_records (
    run_id    INTEGER NOT NULL,
    provider  TEXT NOT NULL,
    key       TEXT NOT NULL,
    data      TEXT NOT NULL,
    PRIMARY KEY (run_id, key)
);

-- Änderungen je Abruf (geplant bei Vorschau, endgültig nach Übernahme)
CREATE TABLE IF NOT EXISTS run_changes (
    id         INTEGER PRIMARY KEY,
    run_id     INTEGER NOT NULL,
    provider   TEXT NOT NULL,
    key        TEXT NOT NULL,
    change     TEXT NOT NULL,      -- neu | geändert | entfallen | umbenannt
    category   TEXT NOT NULL DEFAULT '',
    product    TEXT NOT NULL DEFAULT '',
    region     TEXT NOT NULL DEFAULT '',
    unit       TEXT NOT NULL DEFAULT '',
    currency   TEXT NOT NULL DEFAULT '',
    old_price  REAL,
    new_price  REAL,
    old_key    TEXT,
    reason     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_changes_run ON run_changes (run_id, change);

-- Wechselkurse (Einheiten je 1 EUR, EZB-Referenzkurse)
CREATE TABLE IF NOT EXISTS fx_rates (
    currency   TEXT PRIMARY KEY,
    rate       REAL NOT NULL,
    rate_date  TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    source     TEXT NOT NULL
);

-- Szenarien / Warenkorb
CREATE TABLE IF NOT EXISTS scenarios (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scenario_items (
    id           INTEGER PRIMARY KEY,
    scenario_id  INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    variant      TEXT NOT NULL DEFAULT 'A',
    provider     TEXT NOT NULL,
    key          TEXT NOT NULL,
    quantity     REAL NOT NULL DEFAULT 1,
    note         TEXT NOT NULL DEFAULT '',
    label        TEXT NOT NULL DEFAULT ''   -- Produktname zum Zeitpunkt des Hinzufügens
);
"""

# Version 3: Formatwächter, GPU-Merkmal, Szenario-Hochrechnung, Kursverlauf
V3_COLUMNS = [
    ("prices_current", "gpu", "REAL"),
    ("fetch_runs", "format_alerts", "TEXT DEFAULT ''"),
    ("scenarios", "months", "INTEGER NOT NULL DEFAULT 12"),
    ("scenarios", "growth_pct", "REAL NOT NULL DEFAULT 0"),
    ("scenario_items", "growth_pct", "REAL"),
]

SCHEMA_V3_TABLES = """
-- Formatprofil je Abruf (Grundlage für den Formatwächter)
CREATE TABLE IF NOT EXISTS format_profiles (
    run_id      INTEGER PRIMARY KEY,
    provider    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    data        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_profiles_provider ON format_profiles (provider, run_id);

-- historische EZB-Referenzkurse (Einheiten je 1 EUR)
CREATE TABLE IF NOT EXISTS fx_history (
    currency   TEXT NOT NULL,
    rate_date  TEXT NOT NULL,
    rate       REAL NOT NULL,
    PRIMARY KEY (currency, rate_date)
);
CREATE INDEX IF NOT EXISTS ix_runs_finished ON fetch_runs (status, finished_at);
"""

SCHEMA_VERSION = 3


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _migrate(con: sqlite3.Connection) -> None:
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        con.executescript(SCHEMA_V1)
        con.execute("PRAGMA user_version = 1")
        version = 1
    if version < 2:
        _add_columns(con, V2_COLUMNS)
        con.executescript(SCHEMA_V2_TABLES)
        con.execute("PRAGMA user_version = 2")
        version = 2
    if version < 3:
        _add_columns(con, V3_COLUMNS)
        con.executescript(SCHEMA_V3_TABLES)
        # bisherige Tageskurse als ersten Verlaufspunkt übernehmen
        con.execute("INSERT OR IGNORE INTO fx_history (currency, rate_date, rate) "
                    "SELECT currency, rate_date, rate FROM fx_rates")
        con.execute("PRAGMA user_version = 3")


def _add_columns(con: sqlite3.Connection, columns) -> None:
    for table, column, definition in columns:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            _migrate(con)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=60)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=60000")
        # Unicode-fähige Kleinschreibung für die Suche (SQLite-LOWER kann nur ASCII)
        con.create_function("pv_lower", 1, lambda s: s.casefold() if isinstance(s, str) else s,
                            deterministic=True)
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def backup(self, target: str | Path) -> None:
        """Konsistente Sicherung (inkl. WAL-Inhalt) über die SQLite-Backup-API."""
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(self.path)
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
