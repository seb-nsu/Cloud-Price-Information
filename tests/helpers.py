from __future__ import annotations

import os
import tempfile
from pathlib import Path

from app.db import Database
from app.models import PriceRecord

FIX = Path(__file__).parent / "fixtures"


def rec(key, price, scope="s", **kw) -> PriceRecord:
    base = dict(key=key, category="Compute", service="S", product=f"Produkt {key}", region="eu",
                unit="h", price=price, currency="EUR", scope=scope)
    base.update(kw)
    return PriceRecord(**base)


class TempDB:
    """Temporäre Datenbank + eigene config.toml je Test."""

    def __init__(self, config_text: str = ""):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = Database(self.dir / "t.db")
        self.cfg = self.dir / "config.toml"
        self.cfg.write_text(config_text or "[general]\n", encoding="utf-8")

    def __enter__(self):
        from app import config
        self._old = config.CONFIG_PATH
        config.CONFIG_PATH = self.cfg
        return self

    def __exit__(self, *exc):
        from app import config
        config.CONFIG_PATH = self._old
        self.tmp.cleanup()

    def rows(self, table: str, where: str = "", args=()):
        with self.db.connect() as con:
            return [dict(r) for r in con.execute(f"SELECT * FROM {table} {where}", args)]


os.environ.setdefault("PV_SECRET", "test-secret")
