"""Erweiterungen 2.1: Azure-Größen, Datenhoheit, Formatwächter, Vergleiche, Rabatte, Bericht,
Hochrechnung, Excel/PDF, JSON-API, neue Anbieter, Kursverlauf, Zweisprachigkeit."""
from __future__ import annotations

import io
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from unittest import mock

from app import analysis, compare, fx, i18n, jobs, report, sovereignty, watch, web, xlsx
from app.azure_sizes import lookup
from app.models import (PT_ON_DEMAND, PT_RESERVED_1Y, PT_RESERVED_3Y, PT_SAVINGS_1Y, FetchResult, PriceRecord)
from app.normalize import extract_vm, normalize
from app.pdf import Report
from app.providers import PROVIDERS, aws, azure, hetzner, ovh, scaleway
from app.sync import apply_result

from .helpers import FIX, TempDB, rec

NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def load(name):
    return json.loads((FIX / name).read_text())


def azure_records():
    return azure.parse_items(load("azure_items.json")["Items"], "EUR", include_savings_plan=True)


def aws_records():
    _, recs = aws.parse_offer("AmazonEC2", "eu-central-1", io.StringIO((FIX / "aws_ec2.csv").read_text()),
                              {"term_types": ["OnDemand", "Reserved"]})
    return recs


def fill(t: TempDB, provider: str, recs: list[PriceRecord], rates=None, run_id: int = 1):
    for r in recs:
        r.scope = r.scope or "s"
    return apply_result(t.db, provider, FetchResult(recs, scopes_ok={r.scope for r in recs}), run_id,
                        rates or {"EUR": 1.0, "USD": 1.0}, force=True)


# ------------------------------------------------------------------ Azure-Größen und GPU
class AzureSizeTests(unittest.TestCase):
    def test_series(self):
        cases = {"Standard_B2ms": (2, 8, 0), "Standard_B1ls": (1, 0.5, 0), "Standard_B2ts_v2": (2, 1, 0),
                 "Standard_B4als_v2": (4, 8, 0), "Standard_B8s_v2": (8, 32, 0), "Standard_M64s": (64, 1024, 0),
                 "Standard_M416ms_v2": (416, 11400, 0), "Standard_L8s_v3": (8, 64, 0), "Standard_L16as_v3": (16, 128, 0),
                 "Standard_NC4as_T4_v3": (4, 28, 1), "Standard_ND96isr_H100_v5": (96, 1900, 8),
                 "Standard_F4s_v2": (4, 8, 0), "Standard_F8als_v6": (8, 16, 0), "Standard_F8ams_v6": (8, 64, 0),
                 "Standard_D4s_v5": (4, 16, 0), "Standard_D4ls_v5": (4, 8, 0), "Standard_E8s_v3": (8, 64, 0)}
        for sku, (v, r, g) in cases.items():
            got = lookup(sku)
            self.assertIsNotNone(got, sku)
            self.assertEqual(got[:3], (v, r, g), sku)
        for sku in ("Standard_D8-4s_v5", "Standard_DS2_v2", "Standard_D2_v2", "Standard_NC999_v9", ""):
            self.assertIsNone(lookup(sku), sku)

    def test_gpu_and_extract(self):
        self.assertEqual(extract_vm("azure", "", {"armSkuName": "Standard_NC24ads_A100_v4"}), (24.0, 220.0, "linux"))
        n = normalize("azure", "Compute", "x", "1 Hour", 1.0, "EUR", {"armSkuName": "Standard_NC24ads_A100_v4"}, {})
        self.assertEqual(n["gpu"], 1.0)
        n = normalize("aws", "Compute", "x", "Hrs", 1.0, "USD",
                      {"Instance Type": "g4dn.xlarge", "vCPU": "4", "Memory": "16 GiB", "GPU": "1",
                       "Operating System": "Linux"}, {"USD": 1.0})
        self.assertEqual((n["vcpu"], n["gpu"]), (4.0, 1.0))

    def test_azure_reservation_is_spread_over_term(self):
        n = normalize("azure", "Compute", "x", "1 Hour", 1200.0, "EUR",
                      {"armSkuName": "Standard_D4s_v5", "reservationTerm": "1 Year"}, {}, PT_RESERVED_1Y)
        self.assertEqual(n["unit_norm"], "Monat")
        self.assertAlmostEqual(n["price_norm"], 100.0)

    def test_compare_vms_gpu_filter(self):
        with TempDB() as t:
            fill(t, "azure", azure_records())
            plain = analysis.compare_vms(t.db, 4, 16, "linux", 10)
            self.assertFalse(any(o["gpu"] for o in plain["azure"]))
            labels = [o["label"] for o in plain["azure"]]
            self.assertTrue(any("L8s v3" in x for x in labels) or any("D4s v5" in x for x in labels))
            gpu = analysis.compare_vms(t.db, 1, 1, "linux", 10, gpu=1)
            self.assertTrue(gpu["azure"] and all(o["gpu"] >= 1 for o in gpu["azure"]))
            small = analysis.compare_vms(t.db, 2, 8, "linux", 10)
            self.assertTrue(any("B2ms" in o["label"] for o in small["azure"]))


# ------------------------------------------------------------------ Datenhoheit
class SovereigntyTests(unittest.TestCase):
    def test_levels(self):
        c = sovereignty.Classifier()
        self.assertEqual(c.classify("stackit", "eu01")["level"], "eu_souveraen")
        self.assertEqual(c.classify("aws", "eu-central-1")["level"], "eu_region")
        self.assertEqual(c.classify("aws", "eu-west-2")["level"], "nicht_eu")
        self.assertEqual(c.classify("azure", "")["level"], "unbekannt")
        self.assertEqual(c.classify("scaleway", "fr-par-2")["country"], "FR")
        self.assertEqual(c.classify("otc", "eu-de-01")["country"], "DE")
        self.assertTrue(c.matches("aws", "eu-central-1", "eu"))
        self.assertFalse(c.matches("aws", "eu-central-1", "eu_souveraen"))

    def test_config_override(self):
        with TempDB('[datenhoheit]\nregionen = { "xy-1" = "DE" }\nanbieter = { aws = "DE" }\n'):
            self.assertEqual(sovereignty.classify("aws", "xy-1")["level"], "eu_souveraen")

    def test_filter_in_compare(self):
        with TempDB() as t:
            fill(t, "azure", azure_records())
            fill(t, "hetzner", hetzner.parse_server_types(load("hetzner_server_types.json")["server_types"], "EUR"),
                 run_id=2)
            res = analysis.compare_vms(t.db, 2, 4, "linux", 10, souv="eu_souveraen")
            self.assertEqual(set(res), {"hetzner"})
            self.assertTrue(all(o["region"] != "ash" for o in res["hetzner"]))


# ------------------------------------------------------------------ Formatwächter
class FormatWatchTests(unittest.TestCase):
    def recs(self, n=30, with_field=True, scope="a"):
        return [rec(f"k{i}", 1.0 + i, scope=scope, attributes={"armSkuName": "Standard_D2s_v5", "meterId": "m",
                                                               "skuId": "s"} if with_field else {"meterId": "m"})
                for i in range(n)]

    def test_compare_detects_missing_field_and_drop(self):
        old = watch.profile("azure", FetchResult(self.recs(30)))
        new = watch.profile("azure", FetchResult(self.recs(12, with_field=False)))
        alerts = watch.compare("azure", old, new)
        self.assertTrue(any("armSkuName" in a for a in alerts))
        self.assertTrue(any("Bereich" in a for a in alerts))
        self.assertEqual(watch.compare("azure", old, watch.profile("azure", FetchResult(self.recs(30)))), [])

    def test_run_goes_to_review_and_is_logged(self):
        with TempDB('[general]\nfreigabe = "nie"\n') as t, \
                mock.patch("app.jobs.fx.refresh", return_value="Kurse aktuell"):
            data = {"recs": self.recs(30)}
            orig = PROVIDERS["azure"].fetch
            PROVIDERS["azure"].fetch = lambda cfg: FetchResult(data["recs"], scopes_ok={"a"})
            try:
                self.assertEqual(jobs.run_provider(t.db, "azure")["status"], "ok")
                data["recs"] = self.recs(30, with_field=False)
                r = jobs.run_provider(t.db, "azure")
                self.assertEqual(r["status"], "review")
                self.assertIn("armSkuName", r["format_alerts"])
            finally:
                PROVIDERS["azure"].fetch = orig
        with TempDB('[general]\nfreigabe = "nie"\nformatwaechter = "warnen"\n') as t, \
                mock.patch("app.jobs.fx.refresh", return_value="Kurse aktuell"):
            data = {"recs": self.recs(30)}
            PROVIDERS["azure"].fetch = lambda cfg: FetchResult(data["recs"], scopes_ok={"a"})
            try:
                jobs.run_provider(t.db, "azure")
                data["recs"] = self.recs(30, with_field=False)
                r = jobs.run_provider(t.db, "azure")
                self.assertEqual(r["status"], "ok")
                self.assertIn("Formatwächter", r["message"])
            finally:
                PROVIDERS["azure"].fetch = orig


# ------------------------------------------------------------------ Kategorie-Vergleiche
class CategoryCompareTests(unittest.TestCase):
    def test_storage_traffic_kubernetes(self):
        with TempDB() as t:
            fill(t, "azure", azure_records())
            fill(t, "aws", aws_records(), run_id=2)
            fill(t, "scaleway", scaleway.fetch({}, get=lambda url: load("scaleway_products.json")).records, run_id=3)
            st = compare.compare_category(t.db, compare.KINDS["speicher"], {})
            self.assertIn("azure", st["providers"])
            arch = compare.compare_category(t.db, compare.KINDS["speicher"], {"klasse": "archiv"})
            self.assertTrue(all("Archiv" in r["class"] for rows in arch["providers"].values() for r in rows))
            obj = compare.compare_category(t.db, compare.KINDS["speicher"], {"typ": "objekt"})
            self.assertTrue({"azure", "scaleway"} <= set(obj["providers"]))
            tr = compare.compare_category(t.db, compare.KINDS["traffic"], {"provider": "aws"})
            self.assertEqual(len(tr["providers"]["aws"]), 1)          # Staffel zusammengefasst
            self.assertAlmostEqual(tr["providers"]["aws"][0]["price"], 0.085)
            entry = compare.compare_category(t.db, compare.KINDS["traffic"], {"provider": "aws", "stufe": "einstieg"})
            self.assertAlmostEqual(entry["providers"]["aws"][0]["price"], 0.09)
            k8s = compare.compare_category(t.db, compare.KINDS["kubernetes"], {})
            self.assertNotIn("scaleway", {p for p, rows in k8s["providers"].items()
                                          if any(r["eur"] == 0 for r in rows)})
            k8s_free = compare.compare_category(t.db, compare.KINDS["kubernetes"], {"kostenlos": "1"})
            self.assertTrue(any(r["eur"] == 0 for r in k8s_free["providers"]["scaleway"]))
            db = compare.compare_category(t.db, compare.KINDS["datenbanken"], {"engine": "postgres"})
            self.assertTrue(db["providers"])


# ------------------------------------------------------------------ Rabatte
class CommitmentTests(unittest.TestCase):
    def test_azure_and_aws(self):
        with TempDB() as t:
            fill(t, "azure", azure_records())
            fill(t, "aws", aws_records(), rates={"EUR": 1.0, "USD": 1.0}, run_id=2)
            rows = compare.commitments(t.db, {})
            az = {r["price_type"]: r for r in rows if r["provider"] == "azure"}
            self.assertEqual(set(az), {"Reserviert 1 J.", "Reserviert 3 J.", "Savings Plan 1 J.", "Savings Plan 3 J."})
            od = 0.2160 * 730
            self.assertAlmostEqual(az["Reserviert 1 J."]["od_month"], od)
            self.assertAlmostEqual(az["Reserviert 1 J."]["eff_month"], 1180 / 12)
            self.assertAlmostEqual(az["Reserviert 1 J."]["saving_pct"], (1 - 1180 / 12 / od) * 100)
            self.assertAlmostEqual(az["Savings Plan 1 J."]["eff_month"], 0.154 * 730)
            aws_rows = {r["price_type"]: r for r in rows if r["provider"] == "aws"}
            ri3 = aws_rows[PT_RESERVED_3Y]
            self.assertAlmostEqual(ri3["upfront"], 1150)
            self.assertAlmostEqual(ri3["eff_month"], 1150 / 36)
            self.assertAlmostEqual(ri3["amort_months"], 1150 / (0.115 * 730))
            self.assertAlmostEqual(aws_rows[PT_RESERVED_1Y]["break_even_pct"], 0.073 / 0.115 * 100)
            self.assertIsNone(aws_rows[PT_RESERVED_1Y]["amort_months"])
            only = compare.commitments(t.db, {"price_type": PT_SAVINGS_1Y})
            self.assertEqual({r["price_type"] for r in only}, {PT_SAVINGS_1Y})
            summ = compare.commitment_summary(rows)
            self.assertEqual(len(summ), 6)

    def test_gcp_commitment_pairing(self):
        recs = [PriceRecord("c1", "Compute", "Compute Engine", "N2 Instance Core running in Frankfurt", "europe-west3",
                            "hour", 0.04, "EUR", PT_ON_DEMAND),
                PriceRecord("c2", "Compute", "Compute Engine", "Commitment v1: N2 Cpu in Frankfurt for 1 Year",
                            "europe-west3", "hour", 0.025, "EUR", PT_RESERVED_1Y)]
        with TempDB() as t:
            fill(t, "gcp", recs)
            rows = compare.commitments(t.db, {})
            self.assertEqual(len(rows), 1)
            self.assertAlmostEqual(rows[0]["saving_pct"], 37.5)


# ------------------------------------------------------------------ Bericht und Hochrechnung
class ReportTests(unittest.TestCase):
    def test_periods(self):
        self.assertEqual(report.parse_period("2026-10")[:2], ("2026-10-01", "2026-11-01"))
        self.assertEqual(report.parse_period("2026-Q4")[:2], ("2026-10-01", "2027-01-01"))
        self.assertEqual(report.parse_period("2026-12")[:2], ("2026-12-01", "2027-01-01"))
        self.assertEqual(report.parse_period("2026")[:2], ("2026-01-01", "2027-01-01"))
        self.assertEqual(len(report.parse_period("unsinn")[0]), 10)

    def test_build(self):
        with TempDB('[general]\nfreigabe = "nie"\n') as t, mock.patch("app.jobs.fx.refresh", return_value="x"):
            data = {"recs": [rec("a", 1.0), rec("b", 2.0), rec("c", 3.0)]}
            orig = PROVIDERS["stackit"].fetch
            PROVIDERS["stackit"].fetch = lambda cfg: FetchResult([PriceRecord(**{**r.__dict__}) for r in data["recs"]],
                                                                 scopes_ok={"s"})
            try:
                jobs.run_provider(t.db, "stackit")
                data["recs"] = [rec("a", 1.5), rec("b", 1.0), rec("c", 3.0), rec("d", 4.0)]
                jobs.run_provider(t.db, "stackit")
            finally:
                PROVIDERS["stackit"].fetch = orig
            rep = report.build(t.db, None)
            self.assertEqual(rep["counts"], {"geändert": 2, "neu": 1, "entfallen": 0})
            self.assertAlmostEqual(rep["increases"][0]["pct"], 50.0)
            self.assertAlmostEqual(rep["decreases"][0]["pct"], -50.0)
            self.assertEqual(rep["summary"]["stackit"]["up"], 1)
            pdf = __import__("app.pages_extra", fromlist=["x"]).export_report_pdf(t.db, {})
            self.assertTrue(pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF"))
            book = __import__("app.pages_extra", fromlist=["x"]).export_report_xlsx(t.db, {})
            self.assertEqual(len([n for n in zipfile.ZipFile(io.BytesIO(book)).namelist()
                                  if n.startswith("xl/worksheets/")]), 4)

    def test_projection(self):
        lines = [{"item": {"variant": "A", "quantity": 2, "growth_pct": None}, "eur_month": 20.0,
                  "price": {"unit_norm": "Monat"}},
                 {"item": {"variant": "A", "quantity": 100, "growth_pct": 10}, "eur_month": 10.0,
                  "price": {"unit_norm": "GB-Monat"}},
                 {"item": {"variant": "B", "quantity": 1, "growth_pct": None}, "eur_month": None, "price": None}]
        p = analysis.projection(lines, 3, 50)
        a = p["variants"]["A"]["monthly"]
        # VMs: 2 -> 3 -> 4.5 aufgerundet 5 (je 10 €); GB: 100 -> 110 -> 121 (je 0,10 €)
        self.assertEqual([round(x, 6) for x in a], [30.0, 41.0, 62.1])
        self.assertTrue(p["variants"]["B"]["incomplete"])
        self.assertAlmostEqual(p["variants"]["A"]["total"], 133.1)


# ------------------------------------------------------------------ Excel und PDF
class FileFormatTests(unittest.TestCase):
    def test_xlsx_structure(self):
        data = xlsx.single_sheet("Preise/Test", ["a", "b", "c"], [[1, "x<&>", None], [2.5, "=1+1", True]],
                                 number_formats={0: "eur"})
        z = zipfile.ZipFile(io.BytesIO(data))
        self.assertIn("xl/worksheets/sheet1.xml", z.namelist())
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("x&lt;&amp;&gt;", sheet)
        self.assertIn('t="inlineStr"', sheet)      # Formeln werden nie als Formel geschrieben
        self.assertNotIn("<f>", sheet)
        self.assertIn('name="PreiseTest"', z.read("xl/workbook.xml").decode())

    def test_pdf_umlauts_and_pages(self):
        r = Report("Bericht Ä€")
        r.heading("Überschrift")
        r.table(["A", "B"], [["x" * 200, "1,00 €"]] * 120, [3, 1], "lr")
        b = r.to_bytes()
        self.assertIn(b"/Count ", b)
        self.assertGreater(b.count(b"/Type /Page "), 2)


# ------------------------------------------------------------------ Neue Anbieter
class NewProviderTests(unittest.TestCase):
    def test_scaleway(self):
        res = scaleway.fetch({"zones": ["fr-par"]}, get=lambda url: load("scaleway_products.json"))
        by = {r.key: r for r in res.records}
        pop = by["/compute/pop2_2c_8g/run_fr-par-1"]
        self.assertEqual((pop.price, pop.unit, pop.category, pop.region), (0.107, "Stunde", "Compute", "fr-par-1"))
        self.assertEqual((pop.attributes["vcpu"], pop.attributes["ram_gb"]), (2, 8.0))
        self.assertNotIn("/compute/dev1_s/run_nl-ams-1", by)                # Zonenfilter
        self.assertFalse(any("old" in k for k in by))                       # end_of_life
        self.assertEqual(by["/storage/object/standard/fr-par"].unit, "GB/Monat")
        self.assertEqual(by["/k8s/kapsule/dedicated4"].category, "Kubernetes")
        self.assertTrue(any("übersprungen" in w for w in res.warnings))
        n = normalize("scaleway", pop.category, pop.product, pop.unit, pop.price, "EUR", pop.attributes, {})
        self.assertEqual((n["unit_norm"], n["vcpu"], n["ram_gb"]), ("Monat", 2.0, 8.0))
        gpu = by["/compute/l4_1_24g/run_fr-par-1"]
        self.assertEqual(normalize("scaleway", "Compute", gpu.product, gpu.unit, 1, "EUR", gpu.attributes, {})["gpu"], 1)

    def test_hetzner(self):
        with self.assertRaises(Exception):
            with mock.patch.dict("os.environ", {"HCLOUD_TOKEN": ""}):
                hetzner.fetch({})
        types, pricing = load("hetzner_server_types.json"), load("hetzner_pricing.json")
        with mock.patch.dict("os.environ", {"HCLOUD_TOKEN": "t"}):
            res = hetzner.fetch({}, get=lambda url: pricing if url.endswith("/pricing") else types)
        by = {r.key: r for r in res.records}
        cx = by["server|cx22|fsn1"]
        self.assertEqual((cx.price, cx.unit, cx.attributes["cores"]), (3.79, "pro Monat", 2))
        self.assertNotIn("server|cx11|fsn1", by)                           # abgekündigt
        self.assertEqual(by["volume"].unit, "GB/Monat")
        self.assertAlmostEqual(by["traffic|cx22|fsn1"].price, 0.001)
        self.assertEqual(res.scopes_ok, {"preise", "server"})
        n = normalize("hetzner", "Compute", cx.product, cx.unit, cx.price, "EUR", cx.attributes, {})
        self.assertEqual((n["price_norm"], n["vcpu"], n["ram_gb"]), (3.79, 2.0, 4.0))

    def test_ovh(self):
        res = ovh.fetch({}, get=lambda url: load("ovh_catalog.json"))
        by = {r.key: r for r in res.records}
        b3 = by["b3-8.consumption"]
        self.assertAlmostEqual(b3.price, 0.0505)
        self.assertEqual((b3.category, b3.unit, b3.region), ("Compute", "pro Stunde", "ovh-eu"))
        self.assertEqual(by["b3-8.consumption.3AZ"].region, "eu-west-par")
        self.assertEqual(by["b2-7.monthly.postpaid"].unit, "pro Monat")
        self.assertEqual(by["volume.high-speed.consumption"].category, "Storage")
        self.assertNotIn("credit", by)
        n = normalize("ovh", "Compute", b3.product, b3.unit, b3.price, "EUR", b3.attributes, {})
        self.assertEqual((n["vcpu"], n["ram_gb"]), (2.0, 8.0))
        self.assertEqual(normalize("ovh", "Compute", "l4", "pro Stunde", 1, "EUR",
                                   by["l4-90.consumption"].attributes, {})["gpu"], 1)

    def test_registered(self):
        self.assertTrue({"hetzner", "scaleway", "ovh"} <= set(PROVIDERS))


# ------------------------------------------------------------------ Kursverlauf
HIST = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
<Cube><Cube time="2026-11-03"><Cube currency="USD" rate="1.10"/></Cube>
<Cube time="2026-10-01"><Cube currency="USD" rate="1.20"/><Cube currency="GBP" rate="0.86"/></Cube></Cube>
</gesmes:Envelope>"""


class FxHistoryTests(unittest.TestCase):
    def test_history_and_rate_at(self):
        with TempDB() as t:
            self.assertIn("2026-10-01", fx.refresh_history(t.db, get=lambda url: HIST))
            self.assertEqual(fx.rate_at(t.db, "USD", "2026-10-15T10:00:00+00:00"), 1.20)
            self.assertEqual(fx.rate_at(t.db, "USD", "2026-11-05"), 1.10)
            self.assertEqual(fx.rate_at(t.db, "USD", "2020-01-01"), 1.20)    # vor Beginn: ältester Kurs
            self.assertEqual(fx.rate_at(t.db, "EUR", "2020-01-01"), 1.0)
            self.assertEqual(len(fx.history_points(t.db, "USD")), 2)

    def test_display_currencies(self):
        with TempDB('[waehrung]\nanzeige = ["usd", "EUR", "GBP"]\n'):
            self.assertEqual(fx.display_currencies(), ["USD", "GBP"])
        with TempDB():
            self.assertEqual(fx.display_currencies(), ["USD"])


# ------------------------------------------------------------------ Zweisprachigkeit
class I18nTests(unittest.TestCase):
    def test_translate_and_format(self):
        from app.ui import fmt_dt, fmt_eur, fmt_price
        with i18n.use("en"):
            self.assertEqual(i18n.t("Aktuelle Preise"), "Current prices")
            self.assertEqual(i18n.t("{n} Preise", n=3), "3 prices")
            self.assertEqual(i18n.t("unbekannter Text"), "unbekannter Text")
            self.assertEqual(fmt_eur(1234.5), "1,234 €" if fmt_eur(1234.5).startswith("1,234") else fmt_eur(1234.5))
            self.assertEqual(fmt_price(0.5), "0.50")
            self.assertTrue(fmt_dt("2026-11-09T12:00:00+00:00").startswith("2026-11-09"))
        with i18n.use("de"):
            self.assertEqual(i18n.t("Aktuelle Preise"), "Aktuelle Preise")
            self.assertEqual(fmt_price(0.5), "0,50")

    def test_all_ui_strings_translated(self):
        import ast
        import pathlib
        from app.i18n_en import EN
        missing = set()
        for f in pathlib.Path("app").glob("*.py"):
            for node in ast.walk(ast.parse(f.read_text())):
                if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "t" and node.args
                        and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                    if node.args[0].value not in EN:
                        missing.add(node.args[0].value)
        self.assertEqual(missing, set())


# ------------------------------------------------------------------ Web und JSON-API
class WebApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = TempDB('[general]\nfreigabe = "nie"\n').__enter__()
        cls.fxp = mock.patch("app.jobs.fx.refresh", return_value="Kurse aktuell")
        cls.fxp.start()
        fill(cls.t, "azure", azure_records())
        fill(cls.t, "aws", aws_records(), run_id=2)
        fill(cls.t, "scaleway", scaleway.fetch({}, get=lambda url: load("scaleway_products.json")).records, run_id=3)
        with cls.t.db.connect() as con:
            con.execute("INSERT INTO fetch_runs (id, provider, started_at, finished_at, status) VALUES "
                        "(1,'azure','2026-11-01T00:00:00+00:00','2026-11-01T00:00:00+00:00','ok'),"
                        "(2,'aws','2026-11-01T00:00:00+00:00','2026-11-01T00:00:00+00:00','ok'),"
                        "(3,'scaleway','2026-11-01T00:00:00+00:00','2026-11-01T00:00:00+00:00','ok')")
            con.execute("INSERT INTO scenarios (id, name, created_at, months, growth_pct) VALUES "
                        "(1, 'S', '2026-11-01T00:00:00+00:00', 6, 2)")
            key = con.execute("SELECT key FROM prices_current WHERE provider='azure' AND price_type='On-Demand' "
                              "AND vcpu IS NOT NULL LIMIT 1").fetchone()[0]
            con.execute("INSERT INTO scenario_items (scenario_id, variant, provider, key, quantity, label) "
                        "VALUES (1, 'A', 'azure', ?, 3, 'x')", (key,))
        web.Handler.db = cls.t.db
        cls.srv = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.fxp.stop()
        cls.t.__exit__()

    def get(self, path, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/{path}", headers=headers or {})
        try:
            with NO_PROXY.open(req, timeout=10) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def test_pages(self):
        for path in ("", "vergleich?gpu=1&vcpu=1&ram=1", "vergleich?souv=eu", "vergleich/speicher",
                     "vergleich/traffic", "vergleich/kubernetes", "vergleich/datenbanken?engine=postgres",
                     "vergleich/rabatte", "vergleich/rabatte?sort=od&price_type=Reserviert%201%20J.", "bericht",
                     "bericht?zeitraum=2026-Q4", "kurse", "szenario/1", "szenarien", "protokoll", "archiv"):
            status, _, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertNotIn(b"Interner Fehler", body, path)
        self.assertEqual(self.get("vergleich/unbekannt")[0], 404)

    def test_downloads(self):
        for path, magic in (("export.xlsx", b"PK"), ("archiv.xlsx", b"PK"), ("vergleich/rabatte.xlsx", b"PK"),
                            ("bericht.xlsx", b"PK"), ("szenario/1/export.xlsx", b"PK"), ("bericht.pdf", b"%PDF"),
                            ("bericht.csv", b"\xef\xbb\xbf")):
            status, headers, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertTrue(body.startswith(magic), path)
            self.assertIn("attachment", headers["Content-Disposition"])

    def test_language_switch(self):
        status, headers, _ = self.get("sprache?lang=en", {"Referer": f"http://127.0.0.1:{self.port}/vergleich"})
        self.assertEqual(status, 200)    # Weiterleitung wird gefolgt
        _, _, body = self.get("vergleich", {"Cookie": "pv_lang=en"})
        self.assertIn(b"Comparison", body)
        self.assertIn(b'lang="en"', body)
        _, _, body = self.get("vergleich", {"Cookie": "pv_lang=xx"})
        self.assertIn(b"Vergleich", body)

    def test_api(self):
        def j(path):
            status, _, body = self.get(path)
            return status, json.loads(body)
        s, d = j("api")
        self.assertEqual(s, 200)
        self.assertIn("/api/preise", d["endpunkte"])
        s, d = j("api/preise?provider=azure&limit=2")
        self.assertEqual((s, len(d["items"])), (200, 2))
        self.assertGreater(d["total"], 2)
        s, d = j("api/preise?provider=aws&attributes=1&limit=1")
        self.assertIn("attributes", d["items"][0])
        s, d = j("api/preise?souv=eu_souveraen")
        self.assertTrue(all(i["provider"] == "scaleway" for i in d["items"]))
        s, d = j("api/vergleich?vcpu=2&ram=8")
        self.assertIn("azure", d)
        s, d = j("api/vergleich/speicher?typ=objekt")
        self.assertIn("providers", d)
        s, d = j("api/rabatte?provider=aws")
        self.assertEqual(d["total"], 2)
        s, d = j("api/bericht?zeitraum=2026-Q4")
        self.assertEqual(d["label"], "Q4/2026")
        s, d = j("api/szenario/1?monate=12")
        self.assertEqual(len(d["projection"]["months"]), 12)
        s, d = j("api/anbieter")
        self.assertTrue(any(p["name"] == "azure" and p["jurisdiction"] == "US" for p in d))
        s, d = j("api/kurse?waehrung=USD")
        self.assertIn("history", d)
        key = j("api/preise?provider=aws&limit=1")[1]["items"][0]["key"]
        s, d = j(f"api/verlauf?provider=aws&key={urllib.request.quote(key)}")
        self.assertEqual(s, 200)
        self.assertIn("price_eur_damals", d["points"][0])
        self.assertEqual(j("api/unbekannt")[0], 404)
        self.assertEqual(j("api/szenario/99")[0], 404)

    def test_scenario_settings(self):
        import re
        _, _, page = self.get("szenarien")
        token = re.search(rb'name="csrf" value="([^"]+)"', page).group(1).decode()
        data = urllib.parse.urlencode({"csrf": token, "months": "24", "growth_pct": "3,5"}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/szenario/1/einstellungen", data=data)
        with NO_PROXY.open(req, timeout=10):
            pass
        with self.t.db.connect() as con:
            row = con.execute("SELECT months, growth_pct FROM scenarios WHERE id=1").fetchone()
        self.assertEqual((row[0], row[1]), (24, 3.5))
        with self.t.db.connect() as con:
            item = con.execute("SELECT id FROM scenario_items WHERE scenario_id=1").fetchone()[0]
        data = urllib.parse.urlencode({"csrf": token, "quantity": "5", "growth_pct": ""}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/szenario/1/position/{item}/aendern", data=data)
        with NO_PROXY.open(req, timeout=10):
            pass
        with self.t.db.connect() as con:
            row = con.execute("SELECT quantity, growth_pct FROM scenario_items WHERE id=?", (item,)).fetchone()
        self.assertEqual((row[0], row[1]), (5.0, None))


import urllib.parse  # noqa: E402  (für urlencode in den Tests)
