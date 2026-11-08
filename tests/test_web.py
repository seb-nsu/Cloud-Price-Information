"""Abrufe mit Freigabe, Web-Oberfläche, Sicherheit, Szenarien, Benachrichtigung, Aufbewahrung."""
from __future__ import annotations

import base64
import json
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

from app import config, jobs, notify, retention, web
from app.models import FetchResult
from app.providers import PROVIDERS, ionos, stackit

from .helpers import FIX, TempDB, rec

NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Fake:
    """Ersetzt fetch() eines Anbieters durch Beispieldaten."""

    def __init__(self, name, func):
        self.name, self.func = name, func

    def __enter__(self):
        self.orig = PROVIDERS[self.name].fetch
        PROVIDERS[self.name].fetch = self.func
        return self

    def __exit__(self, *exc):
        PROVIDERS[self.name].fetch = self.orig


def stackit_data():
    return json.loads((FIX / "stackit_skus.json").read_text())


class JobTests(unittest.TestCase):
    def setUp(self):
        self.t = TempDB('[general]\nfreigabe = "immer"\n').__enter__()
        self.fx = mock.patch("app.jobs.fx.refresh", return_value="Kurse aktuell")
        self.fx.start()

    def tearDown(self):
        self.fx.stop()
        self.t.__exit__()

    def test_review_flow(self):
        data = stackit_data()
        with Fake("stackit", lambda cfg: FetchResult(stackit.parse(data, {})[0], scopes_ok={"alle"})):
            r1 = jobs.run_provider(self.t.db, "stackit")
            self.assertEqual(r1["status"], "ok")                     # Erstimport direkt übernommen
            r2 = jobs.run_provider(self.t.db, "stackit")
            self.assertEqual((r2["status"], r2["n_unchanged"]), ("ok", r1["n_new"]))  # keine Änderungen -> direkt
            data["services"][0]["price"] = "9.99"
            r3 = jobs.run_provider(self.t.db, "stackit")
            self.assertEqual((r3["status"], r3["n_changed"]), ("review", 1))
            key = stackit.parse(data, {})[0][0].key
            cur = self.t.rows("prices_current", "WHERE key=?", (key,))[0]
            self.assertNotEqual(cur["price"], 9.99)                  # Bestand unverändert bis Freigabe
            # neue Vorschau ersetzt alte
            r4 = jobs.run_provider(self.t.db, "stackit")
            self.assertEqual(self.t.rows("fetch_runs", "WHERE id=?", (r3["id"],))[0]["status"], "verworfen")
            msg = jobs.decide(self.t.db, r4["id"], True, by="test")
            self.assertIn("1 geändert", msg)
            self.assertEqual(self.t.rows("prices_current", "WHERE key=?", (key,))[0]["price"], 9.99)
            self.assertEqual(self.t.rows("fetch_runs", "WHERE id=?", (r4["id"],))[0]["status"], "ok")
            self.assertIn("nicht", jobs.decide(self.t.db, r4["id"], True))

    def test_discard(self):
        data = stackit_data()
        with Fake("stackit", lambda cfg: FetchResult(stackit.parse(data, {})[0], scopes_ok={"alle"})):
            jobs.run_provider(self.t.db, "stackit")
            data["services"][0]["price"] = "9.99"
            r = jobs.run_provider(self.t.db, "stackit")
            jobs.decide(self.t.db, r["id"], False)
            self.assertEqual(self.t.rows("fetch_runs", "WHERE id=?", (r["id"],))[0]["status"], "verworfen")
            self.assertEqual(self.t.rows("prices_archive"), [])

    def test_violation_requires_review_even_in_auto_mode(self):
        self.t.cfg.write_text('[general]\nfreigabe = "bei_warnung"\n')
        data = stackit_data()
        with Fake("stackit", lambda cfg: FetchResult(stackit.parse(data, {})[0], scopes_ok={"alle"})):
            jobs.run_provider(self.t.db, "stackit")
            data["services"] = data["services"][:20]
            r = jobs.run_provider(self.t.db, "stackit")
            self.assertEqual(r["status"], "review")
            self.assertIn("Schutzregel", r["message"])
            self.assertTrue(r["violations"])

    def test_config_error_is_logged(self):
        self.t.cfg.write_text("[general\nkaputt")
        r = jobs.run_provider(self.t.db, "stackit")
        self.assertEqual(r["status"], "error")
        self.assertIn("TOML", r["message"])

    def test_lock_and_stale_cleanup(self):
        rid = jobs.acquire(self.t.db, "stackit", "test")
        self.assertIsNone(jobs.acquire(self.t.db, "stackit", "test2"))
        self.assertEqual(jobs.run_provider(self.t.db, "stackit")["status"], "skipped")
        with self.t.db.connect() as con:
            con.execute("UPDATE fetch_runs SET heartbeat='2000-01-01T00:00:00+00:00' WHERE id=?", (rid,))
        self.assertIsNotNone(jobs.acquire(self.t.db, "stackit", "test3"))
        self.assertEqual(self.t.rows("fetch_runs", "WHERE id=?", (rid,))[0]["status"], "error")

    def test_background_process(self):
        md = (FIX / "ionos_pricelist.md").read_text()
        with Fake("ionos", lambda cfg: FetchResult(ionos.parse(md), scopes_ok={"preisliste"})):
            self.assertEqual(jobs.start_background(self.t.db, ["ionos"]), ["ionos"])
            for _ in range(100):
                if not jobs.is_running(self.t.db, "ionos"):
                    break
                time.sleep(0.1)
        run = self.t.rows("fetch_runs", "WHERE provider='ionos'")[-1]
        self.assertEqual(run["status"], "ok", run["message"])
        self.assertGreater(run["n_new"], 10)


class NotifyRetentionTests(unittest.TestCase):
    def test_notification(self):
        cfg = '[benachrichtigung]\naktiv = true\nmin_aenderung_prozent = 5\narten = ["geändert", "entfallen"]\n'
        with TempDB(cfg) as t, mock.patch.dict("os.environ", {"PV_WEBHOOK_URL": "https://hook.example/x",
                                                              "PV_SMTP_HOST": "smtp.example", "PV_MAIL_TO": "a@b.de",
                                                              "PV_BASE_URL": "https://preise.example.com"}):
            with t.db.connect() as con:
                con.executemany("INSERT INTO run_changes (run_id, provider, key, change, product, region, unit, currency, "
                                "old_price, new_price) VALUES (1,'x',?,?,?,'eu','h','EUR',?,?)",
                                [("a", "geändert", "Server A", 1.0, 1.2), ("b", "geändert", "Server B", 1.0, 1.01),
                                 ("c", "neu", "Server C", None, 2.0), ("d", "entfallen", "Server D", 3.0, None)])
            sent = []
            smtp = mock.MagicMock()
            msgs = notify.send(t.db, "Testanbieter", 1, opener=lambda req, timeout: sent.append(req) or mock.MagicMock(),
                               smtp_factory=smtp)
            self.assertEqual(msgs, ["Webhook gesendet", "E-Mail gesendet"])
            text = json.loads(sent[0].data)["text"]
            self.assertIn("Server A", text)
            self.assertIn("+20.0 %", text)
            self.assertNotIn("Server B", text)           # unter Schwelle
            self.assertNotIn("Server C", text)           # Art „neu“ nicht gewünscht
            self.assertIn("https://preise.example.com/lauf/1", text)

    def test_retention(self):
        with TempDB() as t:
            with t.db.connect() as con:
                for vt in ("2020-01-01T00:00:00+00:00", "2099-01-01T00:00:00+00:00"):
                    con.execute("INSERT INTO prices_archive (provider, key, category, service, product, region, unit, "
                                "price, currency, price_type, valid_from, valid_to, reason) VALUES "
                                "('x','k','C','S','P','eu','h',1,'EUR','On-Demand','2019-01-01',?, 'Preisänderung')", (vt,))
            n = retention.apply_retention(t.db, export_dir=t.dir / "exp", months=12)
            self.assertEqual(n, 1)
            self.assertEqual(len(t.rows("prices_archive")), 1)
            self.assertEqual(len(list((t.dir / "exp").glob("*.csv.gz"))), 1)


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = TempDB('[general]\nfreigabe = "immer"\n').__enter__()
        cls.fxp = mock.patch("app.jobs.fx.refresh", return_value="Kurse aktuell")
        cls.fxp.start()
        data = stackit_data()
        md = (FIX / "ionos_pricelist.md").read_text()
        with Fake("stackit", lambda cfg: FetchResult(stackit.parse(data, {})[0], scopes_ok={"alle"})), \
             Fake("ionos", lambda cfg: FetchResult(ionos.parse(md), scopes_ok={"preisliste"})):
            jobs.run_provider(cls.t.db, "stackit")
            jobs.run_provider(cls.t.db, "ionos")
            data["services"][0]["price"] = "9.99"
            data["services"][0]["title"] = "=HYPERLINK(\"http://evil\")"
            cls.review = jobs.run_provider(cls.t.db, "stackit")
        web.Handler.db = cls.t.db
        cls.httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.fxp.stop()
        cls.t.__exit__()

    def req(self, path, data=None, auth=None, expect=200):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        r = urllib.request.Request(self.base + path, data=body)
        if auth:
            r.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
        try:
            with NO_PROXY.open(r) as resp:
                status, text = resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            status, text = exc.code, exc.read().decode("utf-8", "replace")
        self.assertEqual(status, expect, f"{path}: {text[:300]}")
        return text

    def test_pages(self):
        home = self.req("/")
        self.assertIn("Vorschau prüfen und freigeben", home)
        self.assertIn("Normiert (EUR)", home)
        self.assertNotIn("Gültig bis", home)                     # Archiv nicht auf Startseite
        run = self.req(f"/lauf/{self.review['id']}")
        self.assertIn("Änderungen übernehmen", run)
        self.assertIn("9,99", run)
        for path in ("/archiv", "/protokoll", "/vergleich?vcpu=2&ram=4", "/szenarien", "/api/status",
                     "/health/details", "/?seite=abc", "/archiv?seite=-5", "/vergleich?vcpu=abc", "/?q=%C3%84%C3%96%C3%9C"):
            self.req(path)
        self.req("/gibtsnicht", expect=404)
        self.req("/lauf/999999", expect=404)

    def test_compare(self):
        page = self.req("/vergleich?vcpu=2&ram=4&os=linux")
        self.assertIn("STACKIT", page)
        self.assertIn("IONOS", page)                             # Komponentenpreis vCPU + RAM
        self.assertIn("günstigste", page)

    def test_csrf(self):
        self.req("/aktualisieren", {"provider": "stackit"}, expect=403)
        self.req(f"/lauf/{self.review['id']}/verwerfen", {"csrf": "falsch"}, expect=403)

    def test_csv_export_formula_injection(self):
        csv_text = self.req("/export.csv?provider=stackit")
        self.assertIn("provider;key;category", csv_text)
        self.assertNotIn(';=HYPERLINK', csv_text)

    def test_scenario_flow(self):
        with self.t.db.connect() as con:
            row = con.execute("SELECT provider, key FROM prices_current WHERE unit_norm='Monat' AND provider='stackit' "
                              "LIMIT 1").fetchone()
        form = {"csrf": web.CSRF_TOKEN, "provider": row["provider"], "key": row["key"], "scenario_id": "neu",
                "name": "Test-Szenario", "variant": "A", "quantity": "2"}
        page = self.req("/szenario-hinzufuegen", form)
        self.assertIn("Test-Szenario", page)
        self.assertIn("Variante A", page)
        self.assertIn("2 × €/Monat", page)
        sid = self.t.rows("scenarios")[-1]["id"]
        self.assertIn("variante;anbieter", self.req(f"/szenario/{sid}/export.csv"))

    def test_auth_and_health(self):
        with mock.patch.object(config, "AUTH_USER", "admin"), mock.patch.object(config, "AUTH_PASSWORD", "pw"):
            self.req("/health")                                   # ohne Anmeldung erreichbar
            self.req("/", expect=401)
            self.req("/", auth="admin:pw")
            web._fails.clear()
            for _ in range(web.MAX_FAILS):
                self.req("/", auth="admin:falsch", expect=401)
            self.req("/", auth="admin:pw", expect=429)            # gesperrt nach Fehlversuchen
            web._fails.clear()


if __name__ == "__main__":
    unittest.main()
