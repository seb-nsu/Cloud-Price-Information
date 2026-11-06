"""Abgleich, Schutzregeln, Scopes, Umbenennungen, Vorschau/Freigabe."""
from __future__ import annotations

import sqlite3
import unittest

from app.db import Database
from app.models import FetchResult
from app.sync import SyncAborted, apply_result, apply_staged, compute_diff, stage

from .helpers import TempDB, rec


def fr(records, scopes=None, failed=None):
    return FetchResult(records, scopes_ok=set(scopes) if scopes is not None else {r.scope for r in records},
                       scopes_failed=failed or {})


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.t = TempDB().__enter__()
        self.db = self.t.db

    def tearDown(self):
        self.t.__exit__()

    def cur(self):
        return {r["key"]: r for r in self.t.rows("prices_current")}

    def arch(self):
        return {r["key"]: r for r in self.t.rows("prices_archive")}

    def test_new_change_remove(self):
        s1 = apply_result(self.db, "x", fr([rec(k, 1.0) for k in "abcdefghij"]), 1)
        self.assertEqual(s1["n_new"], 10)
        recs = [rec(k, 1.0) for k in "abcdefghi"]
        recs[1] = rec("b", 2.5)
        s2 = apply_result(self.db, "x", fr(recs), 2)
        self.assertEqual((s2["n_unchanged"], s2["n_changed"], s2["n_removed"], s2["n_new"]), (8, 1, 1, 0))
        self.assertEqual(self.cur()["b"]["price"], 2.5)
        a = self.arch()
        self.assertEqual((a["b"]["reason"], a["b"]["price"], a["b"]["new_price"]), ("Preisänderung", 1.0, 2.5))
        self.assertEqual(a["j"]["reason"], "Nicht mehr gelistet")

    def test_description_change_not_archived(self):
        apply_result(self.db, "x", fr([rec("a", 1.0)]), 1)
        apply_result(self.db, "x", fr([rec("a", 1.0, product="Neuer Name")]), 2)
        self.assertEqual(self.arch(), {})
        self.assertEqual(self.cur()["a"]["product"], "Neuer Name")

    def test_unit_change_archived(self):
        apply_result(self.db, "x", fr([rec("a", 1.0)]), 1)
        self.assertEqual(apply_result(self.db, "x", fr([rec("a", 1.0, unit="Monat")]), 2)["n_changed"], 1)

    def test_guard_total_ratio_and_empty(self):
        apply_result(self.db, "x", fr([rec(str(i), 1.0) for i in range(10)]), 1)
        with self.assertRaises(SyncAborted):
            apply_result(self.db, "x", fr([rec("1", 9.0)]), 2)
        with self.assertRaises(SyncAborted):
            apply_result(self.db, "x", fr([]), 3)
        self.assertEqual(len(self.cur()), 10)
        self.assertEqual(self.arch(), {})

    def test_guard_removed_ratio(self):
        apply_result(self.db, "x", fr([rec(str(i), 1.0) for i in range(100)]), 1)
        with self.db.connect() as con:
            d = compute_diff(con, "x", fr([rec(str(i), 1.0) for i in range(70)]))
        self.assertTrue(any("entfallen" in v for v in d.violations))

    def test_partial_failure_keeps_failed_scope(self):
        """AWS-Szenario: S3-Abruf scheitert -> S3-Preise bleiben, EC2 wird normal abgeglichen."""
        recs = [rec(f"ec2-{i}", 1.0, scope="AmazonEC2/eu") for i in range(90)] + \
               [rec(f"s3-{i}", 0.02, scope="AmazonS3/eu") for i in range(10)]
        apply_result(self.db, "aws", fr(recs), 1)
        res = fr([rec(f"ec2-{i}", 1.0, scope="AmazonEC2/eu") for i in range(90)], scopes={"AmazonEC2/eu"},
                 failed={"AmazonS3/eu": "HTTP 500"})
        s = apply_result(self.db, "aws", res, 2)
        self.assertEqual(s["n_removed"], 0)
        self.assertEqual(len(self.cur()), 100)

    def test_empty_scope_is_violation(self):
        """S3 liefert 0 Zeilen (Formatänderung) -> nicht archivieren, sondern Freigabe verlangen."""
        recs = [rec(f"ec2-{i}", 1.0, scope="AmazonEC2/eu") for i in range(90)] + \
               [rec(f"s3-{i}", 0.02, scope="AmazonS3/eu") for i in range(10)]
        apply_result(self.db, "aws", fr(recs), 1)
        res = fr([rec(f"ec2-{i}", 1.0, scope="AmazonEC2/eu") for i in range(90)], scopes={"AmazonEC2/eu", "AmazonS3/eu"})
        with self.assertRaises(SyncAborted) as cm:
            apply_result(self.db, "aws", res, 2)
        self.assertIn("AmazonS3/eu", str(cm.exception))
        self.assertEqual(self.arch(), {})

    def test_out_of_scope(self):
        apply_result(self.db, "x", fr([rec("a", 1.0, scope="A")] + [rec(f"b{i}", 1.0, scope="B") for i in range(20)]), 1)
        s = apply_result(self.db, "x", fr([rec(f"b{i}", 1.0, scope="B") for i in range(20)], scopes={"B"}), 2)
        self.assertEqual(s["n_removed"], 1)
        self.assertEqual(self.arch()["a"]["reason"], "Nicht mehr im Abrufumfang")

    def test_currency_switch_is_violation(self):
        apply_result(self.db, "x", fr([rec("a", 1.0)]), 1)
        with self.assertRaises(SyncAborted):
            apply_result(self.db, "x", fr([rec("a", 1.1, currency="USD")]), 2)

    def test_rename_detection(self):
        old = [rec(f"k{i}", 1.0 + i, product=f"Server Typ {i}") for i in range(20)]
        apply_result(self.db, "x", fr(old), 1)
        new = list(old)
        new[3] = rec("kNEU3", 4.0, product="Server Typ 3 (neu)")
        s = apply_result(self.db, "x", fr(new), 2)
        self.assertEqual((s["n_renamed"], s["n_removed"], s["n_new"]), (1, 0, 0))
        self.assertIn("kNEU3", self.cur())
        self.assertNotIn("k3", self.cur())

    def test_rename_keeps_history(self):
        apply_result(self.db, "x", fr([rec("a", 1.0, product="Alpha Server")] + [rec(f"z{i}", 1.0 + i) for i in range(15)]), 1)
        apply_result(self.db, "x", fr([rec("a", 2.0, product="Alpha Server")] + [rec(f"z{i}", 1.0 + i) for i in range(15)]), 2)
        apply_result(self.db, "x", fr([rec("a2", 2.0, product="Alpha Server")] + [rec(f"z{i}", 1.0 + i) for i in range(15)]), 3)
        self.assertEqual(self.t.rows("prices_archive")[0]["key"], "a2")

    def test_duplicates_counted(self):
        s = apply_result(self.db, "x", fr([rec("a", 1.0), rec("a", 2.0)]), 1)
        self.assertEqual(s["n_dupes"], 1)

    def test_stage_then_apply_recomputes(self):
        apply_result(self.db, "x", fr([rec(k, 1.0) for k in "abcdefghij"]), 1)
        d = stage(self.db, "x", 2, fr([rec(k, 2.0 if k == "a" else 1.0) for k in "abcdefghij"]))
        self.assertEqual(len(d.changed), 1)
        self.assertEqual(self.cur()["a"]["price"], 1.0)                  # Vorschau ändert nichts
        self.assertEqual(len(self.t.rows("run_changes", "WHERE run_id=2")), 1)
        apply_staged(self.db, "x", 2, {"EUR": 1.0})
        self.assertEqual(self.cur()["a"]["price"], 2.0)
        self.assertEqual(self.t.rows("staging_records", "WHERE run_id=2"), [])

    def test_normalization_stored(self):
        apply_result(self.db, "x", fr([rec("a", 0.1, unit="1 Hour", currency="USD")]), 1,
                     rates={"EUR": 1.0, "USD": 1.25})
        c = self.cur()["a"]
        self.assertAlmostEqual(c["price_eur"], 0.08)
        self.assertAlmostEqual(c["price_norm"], 0.08 * 730)
        self.assertEqual(c["unit_norm"], "Monat")


class MigrationTests(unittest.TestCase):
    def test_v1_database_is_migrated(self):
        from app.db import SCHEMA_V1
        with TempDB() as t:
            path = t.dir / "alt.db"
            con = sqlite3.connect(path)
            con.executescript(SCHEMA_V1)
            con.execute("INSERT INTO prices_current (provider, key, category, service, product, region, unit, price, "
                        "currency, price_type, valid_from, last_checked) VALUES ('x','a','Compute','S','P','eu','h',1,"
                        "'EUR','On-Demand','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')")
            con.commit()
            con.close()
            db = Database(path)
            with db.connect() as con:
                self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 2)
                row = con.execute("SELECT scope, unit_norm FROM prices_current").fetchone()
                self.assertEqual((row[0], row[1]), ("", ""))
            s = apply_result(db, "x", fr([rec("a", 1.0, scope="neu")]), 1)
            self.assertEqual(s["n_unchanged"], 1)

    def test_backup(self):
        with TempDB() as t:
            apply_result(t.db, "x", fr([rec("a", 1.0)]), 1)
            t.db.backup(t.dir / "bak" / "b.db")
            con = sqlite3.connect(t.dir / "bak" / "b.db")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM prices_current").fetchone()[0], 1)
            con.close()


if __name__ == "__main__":
    unittest.main()
