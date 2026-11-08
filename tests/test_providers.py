"""Abruf-Module mit Beispieldaten (ohne Netzwerk)."""
from __future__ import annotations

import gzip
import io
import json
import os
import unittest
import urllib.parse
from contextlib import contextmanager
from unittest import mock

from app import http_util
from app.models import PT_ON_DEMAND, PT_RESERVED_1Y, PT_SAVINGS_1Y, PT_SAVINGS_3Y, PT_SPOT, ProviderNotConfigured
from app.providers import aws, azure, gcp, ionos, otc, stackit

from .helpers import FIX


# ------------------------------------------------------------------ STACKIT
class StackitTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((FIX / "stackit_skus.json").read_text())

    def test_parse_real_snapshot(self):
        recs, _ = stackit.parse(self.data, {})
        self.assertGreater(len(recs), 80)
        self.assertEqual(len({r.key for r in recs}), len(recs))
        vm = next(r for r in recs if r.attributes.get("flavor") == "c1.1" and r.region == "eu01")
        self.assertEqual((vm.category, vm.currency, vm.scope), ("Compute", "EUR", "alle"))
        self.assertAlmostEqual(vm.price, 0.01826770833)

    def test_region_filter(self):
        recs, _ = stackit.parse(self.data, {"regions": ["eu02"]})
        self.assertTrue(recs)
        self.assertTrue(all(r.region in ("eu02", "global") for r in recs))

    def test_empty_price_skipped(self):
        data = {"services": [dict(self.data["services"][0], price=""), self.data["services"][1]]}
        recs, warnings = stackit.parse(data, {})
        self.assertEqual(len(recs), 1)
        self.assertIn("übersprungen", warnings[0])


# ------------------------------------------------------------------ IONOS
class IonosTests(unittest.TestCase):
    def setUp(self):
        self.md = (FIX / "ionos_pricelist.md").read_text()
        self.recs = ionos.parse(self.md)
        self.by = {r.product: r for r in self.recs}

    def test_numbers(self):
        self.assertEqual(ionos.parse_number("7.500,00 €", ","), (7500.0, False))
        self.assertEqual(ionos.parse_number("0,012€", ","), (0.012, False))
        self.assertEqual(ionos.parse_number("0.047 €", ","), (0.047, False))
        self.assertEqual(ionos.parse_number("2.500 €", ","), (2500.0, True))      # mehrdeutig -> markiert
        self.assertEqual(ionos.parse_number("2.500 €", "."), (2.5, False))
        self.assertEqual(ionos.parse_number("1,500 €", "."), (1500.0, True))
        self.assertEqual(ionos.detect_decimal(self.md), ",")

    def test_parse(self):
        self.assertEqual(len({r.key for r in self.recs}), len(self.recs))
        vcpu = self.by["vCPU-Server · CPU Typ: vCPU"]
        self.assertEqual((vcpu.price, vcpu.unit, vcpu.category), (0.012, "pro Stunde", "Compute"))
        self.assertEqual(self.by["Block Storage · Speicher: Essential"].unit, "pro 30 Tage (GB)")
        self.assertEqual(self.by["Private Cloud powered by VMware · Basic bundle (Cluster bestehend aus 3 Hosts, "
                                 "incl. VMware licenses): M-512"].price, 7500.0)
        dc = sorted((r for r in self.recs if "1 Dedicated Core" in r.product), key=lambda r: r.price)
        self.assertEqual([r.price_type for r in dc], [PT_SAVINGS_3Y, PT_SAVINGS_1Y])
        self.assertFalse(any("Bitte beachten" in r.product for r in self.recs))
        self.assertTrue(all("space.vars" not in r.product for r in self.recs))
        k8s = next(r for r in self.recs if "Managed Kubernetes" in r.attributes["Abschnitt"])
        self.assertEqual(k8s.category, "Kubernetes")

    def test_keys_stable_under_cosmetic_heading_change(self):
        md2 = self.md.replace("#### vCPU-Server", "#### vCPU Server").replace("## Compute\n", "## Compute & Server\n")
        keys1 = {r.key for r in self.recs}
        keys2 = {r.key for r in ionos.parse(md2)}
        changed = keys1 ^ keys2
        # Bindestrich/Leerzeichen in Überschrift und Top-Level-Umbenennung ändern keine Schlüssel in Ebene ≥3
        self.assertLessEqual(len(changed), 0, changed)

    def test_html_tables(self):
        html = ("## Compute\n### Cloud GPU VM\n<table><thead><tr><th>Template</th><th>GPU</th><th>Price per hour</th>"
                "<th>Price group</th></tr></thead><tbody><tr><td>H200-S</td><td>NVIDIA<br>H200</td><td>3,00 €</td>"
                "<td>PG C1</td></tr><tr><td>H200-M</td><td>2× H200</td><td>6,00 €</td><td>PG C1</td></tr>"
                "</tbody></table>\n")
        recs = ionos.parse(html)
        self.assertEqual(sorted(r.price for r in recs), [3.0, 6.0])
        self.assertIn("GPU: NVIDIA H200", recs[0].product)

    def test_ambiguous_warning(self):
        md = ("## Compute\n### GPU\n| Typ | Preis pro Stunde |\n| --- | --- |\n| A | 0,50 € |\n| B | 0,40 € |\n"
              "| C | 2.500 € |\n")
        w = []
        recs = ionos.parse(md, w)
        self.assertTrue(next(r for r in recs if r.product.endswith("C")).attributes["Hinweis"].startswith("Zahlformat mehrdeutig"))
        self.assertTrue(any("bitte prüfen" in x for x in w))


# ------------------------------------------------------------------ Azure
AZ_ITEM = {
    "currencyCode": "EUR", "tierMinimumUnits": 0.0, "retailPrice": 0.1763, "unitPrice": 0.1763,
    "armRegionName": "germanywestcentral", "location": "DE West Central", "effectiveStartDate": "2024-01-01T00:00:00Z",
    "meterId": "m-1", "meterName": "D4s v5", "productId": "DZH318Z0BQPS", "skuId": "DZH318Z0BQPS/00TG",
    "productName": "Virtual Machines Dsv5 Series", "skuName": "D4s v5", "serviceName": "Virtual Machines",
    "serviceFamily": "Compute", "unitOfMeasure": "1 Hour", "type": "Consumption", "isPrimaryMeterRegion": True,
    "armSkuName": "Standard_D4s_v5",
    "savingsPlan": [{"unitPrice": 0.12, "retailPrice": 0.12, "term": "1 Year"},
                    {"unitPrice": 0.08, "retailPrice": 0.08, "term": "3 Years"}]}


class AzureTests(unittest.TestCase):
    def test_filter(self):
        self.assertEqual(azure.build_filter("Virtual Machines", "westeurope", "Consumption"),
                         "serviceName eq 'Virtual Machines' and priceType eq 'Consumption' and armRegionName eq 'westeurope'")

    def test_paging_builds_own_url(self):
        calls = []

        def fake_get(url):
            calls.append(url)
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            if "$skip" in q:
                return {"Items": [dict(AZ_ITEM, meterId="m-2")], "NextPageLink": None}
            # NextPageLink ohne currencyCode -> darf nicht blind verwendet werden
            return {"Items": [AZ_ITEM], "NextPageLink": "https://prices.azure.com/api/retail/prices?$skip=1000"}

        res = azure.fetch({"regions": ["germanywestcentral"], "services": ["Virtual Machines"]}, get=fake_get)
        self.assertEqual(len(res.records), 2)
        q2 = urllib.parse.parse_qs(urllib.parse.urlsplit(calls[1]).query)
        self.assertEqual((q2["$skip"], q2["currencyCode"]), (["1000"], ["'EUR'"]))
        self.assertEqual(res.scopes_ok, {"Virtual Machines/germanywestcentral/Consumption"})

    def test_currency_mismatch_fails_scope(self):
        res = azure.fetch({"regions": ["westeurope", "germanywestcentral"], "services": ["Virtual Machines"]},
                          get=lambda url: {"Items": [dict(AZ_ITEM, currencyCode="USD" if "westeurope" in url else "EUR")]})
        self.assertIn("Virtual Machines/westeurope/Consumption", res.scopes_failed)
        self.assertEqual(len(res.records), 1)

    def test_spot_and_savings_plan(self):
        spot = dict(AZ_ITEM, skuName="D4s v5 Spot", meterName="D4s v5 Spot", meterId="m-s")
        recs = azure.parse_items([AZ_ITEM, spot], "EUR", include_savings_plan=True)
        types = sorted(r.price_type for r in recs)
        self.assertEqual(types, sorted([PT_ON_DEMAND, PT_SPOT, PT_SAVINGS_1Y, PT_SAVINGS_3Y]))
        res = dict(AZ_ITEM, type="Reservation", reservationTerm="1 Year")
        self.assertEqual(azure.parse_items([res])[0].price_type, PT_RESERVED_1Y)


# ------------------------------------------------------------------ AWS
AWS_CSV = '''"FormatVersion","v1.0"
"Disclaimer","This pricing list is for informational purposes only."
"Publication Date","2026-10-30T18:00:00Z"
"Version","20261030180000"
"OfferCode","AmazonEC2"
"SKU","OfferTermCode","RateCode","TermType","PriceDescription","EffectiveDate","StartingRange","EndingRange","Unit","PricePerUnit","Currency","LeaseContractLength","PurchaseOption","OfferingClass","Product Family","serviceCode","Location","Location Type","Instance Type","vCPU","Memory","Tenancy","Operating System","License Model","Pre Installed S/W","CapacityStatus","Region Code","Volume API Name","usageType"
"AAA","JRTCKXETXF","AAA.JRTCKXETXF.6YS6EN2CT7","OnDemand","$0.0464 per On Demand Linux t3.medium Instance Hour","2026-10-01","0","Inf","Hrs","0.0464","USD","","","","Compute Instance","AmazonEC2","EU (Frankfurt)","AWS Region","t3.medium","2","4 GiB","Shared","Linux","No License required","NA","Used","eu-central-1","","EUC1-BoxUsage:t3.medium"
"BBB","JRTCKXETXF","BBB.JRTCKXETXF.6YS6EN2CT7","OnDemand","$0.10 per Dedicated","2026-10-01","0","Inf","Hrs","0.10","USD","","","","Compute Instance","AmazonEC2","EU (Frankfurt)","AWS Region","t3.medium","2","4 GiB","Dedicated","Linux","No License required","NA","Used","eu-central-1","",""
"CCC","4NA7Y494T4","CCC.4NA7Y494T4.6YS6EN2CT7","Reserved","reserved","2026-10-01","0","Inf","Hrs","0.03","USD","1yr","No Upfront","standard","Compute Instance","AmazonEC2","EU (Frankfurt)","AWS Region","t3.medium","2","4 GiB","Shared","Linux","No License required","NA","Used","eu-central-1","",""
"DDD","JRTCKXETXF","DDD.JRTCKXETXF.6YS6EN2CT7","OnDemand","$0.0952 per GB-month of gp3","2026-10-01","0","Inf","GB-Mo","0.0952","USD","","","","Storage","AmazonEC2","EU (Frankfurt)","AWS Region","","","","","","","","","eu-central-1","gp3",""
"EEE","JRTCKXETXF","EEE.JRTCKXETXF.1","OnDemand","$0.09 per GB - first 10 TB","2026-10-01","1","10240","GB","0.09","USD","","","","Data Transfer","AmazonEC2","EU (Frankfurt)","AWS Region","","","","","","","","","eu-central-1","",""
"FFF","JRTCKXETXF","FFF.JRTCKXETXF.1","OnDemand","Dedicated host","2026-10-01","0","Inf","Hrs","1.0","USD","","","","Dedicated Host","AmazonEC2","EU (Frankfurt)","AWS Region","","","","","","","","","eu-central-1","",""
"GGG","JRTCKXETXF","GGG.JRTCKXETXF.6YS6EN2CT7","OnDemand","$5.0 per m5.metal","2026-10-01","0","Inf","Hrs","5.0","USD","","","","Compute Instance (bare metal)","AmazonEC2","EU (Frankfurt)","AWS Region","m5.metal","96","384 GiB","Shared","Linux","No License required","NA","Used","eu-central-1","",""
'''


class AwsTests(unittest.TestCase):
    def test_parse_and_filter(self):
        meta, recs = aws.parse_offer("AmazonEC2", "eu-central-1", io.StringIO(AWS_CSV), {})
        self.assertEqual(meta["Publication Date"], "2026-10-30T18:00:00Z")
        self.assertEqual({r.attributes["SKU"] for r in recs}, {"AAA", "DDD", "EEE", "GGG"})
        t3 = next(r for r in recs if r.attributes["SKU"] == "AAA")
        self.assertEqual((t3.price, t3.currency, t3.category, t3.region), (0.0464, "USD", "Compute", "eu-central-1"))
        self.assertEqual(t3.attributes["Usage Type"], "EUC1-BoxUsage:t3.medium")   # Alias usageType
        dt = next(r for r in recs if r.attributes["SKU"] == "EEE")
        self.assertEqual(dt.category, "Netzwerk")
        self.assertIn("[1–10240]", dt.product)

    def test_reserved_optional(self):
        _, recs = aws.parse_offer("AmazonEC2", "eu-central-1", io.StringIO(AWS_CSV),
                                  {"term_types": ["OnDemand", "Reserved"]})
        self.assertEqual(next(r for r in recs if r.attributes["SKU"] == "CCC").price_type, PT_RESERVED_1Y)

    def test_bad_format_raises(self):
        with self.assertRaises(RuntimeError):
            aws.parse_offer("AmazonS3", "eu", io.StringIO('"a","b"\n' * 5 + '"X","Y"\n"1","2"\n'), {})

    def test_fetch_scopes_and_partial_failure(self):
        @contextmanager
        def opener(url, timeout=0):
            if "AmazonS3" in url:
                raise RuntimeError("HTTP 500")
            yield io.BytesIO(AWS_CSV.encode("utf-8"))
        res = aws.fetch({"services": ["AmazonEC2", "AmazonS3"], "regions": ["eu-central-1"]}, opener=opener)
        self.assertEqual(len(res.records), 4)
        self.assertEqual(res.scopes_ok, {"AmazonEC2/eu-central-1"})
        self.assertIn("AmazonS3/eu-central-1", res.scopes_failed)
        self.assertEqual(res.source_date, "2026-10-30T18:00:00Z")

    def test_gzip_stream(self):
        class Resp(io.BytesIO):
            headers = {"Content-Encoding": "gzip"}
        with mock.patch.object(http_util, "open_url", return_value=Resp(gzip.compress(AWS_CSV.encode()))):
            res = aws.fetch({"services": ["AmazonEC2"], "regions": ["eu-central-1"]})
        self.assertEqual(len(res.records), 4)


# ------------------------------------------------------------------ Google
GCP_SKU = {
    "name": "services/6F81-5844-456A/skus/AAAA-1", "skuId": "AAAA-1", "description": "N2 Instance Core running in Frankfurt",
    "category": {"serviceDisplayName": "Compute Engine", "resourceFamily": "Compute", "resourceGroup": "N2Standard",
                 "usageType": "OnDemand"},
    "serviceRegions": ["europe-west3"],
    "pricingInfo": [{"effectiveTime": "2026-11-01T00:00:00Z", "pricingExpression": {
        "usageUnit": "h", "usageUnitDescription": "hour", "displayQuantity": 1,
        "tieredRates": [{"startUsageAmount": 0, "unitPrice": {"currencyCode": "EUR", "units": "0", "nanos": 35880000}}]}}],
}


class GcpTests(unittest.TestCase):
    def test_requires_key(self):
        with mock.patch.dict(os.environ, {"GCP_API_KEY": ""}):
            with self.assertRaises(ProviderNotConfigured):
                gcp.fetch({})

    def test_fetch_and_header_key(self):
        preempt = json.loads(json.dumps(GCP_SKU))
        preempt["skuId"], preempt["category"]["usageType"] = "BBBB", "Preemptible"
        glob = json.loads(json.dumps(GCP_SKU))
        glob["skuId"], glob["serviceRegions"] = "CCCC", ["global"]

        def fake_get(url):
            self.assertNotIn("key=", url)       # Key nicht in der URL
            if url.split("?")[0].endswith("/v1/services"):
                return {"services": [{"name": "services/6F81-5844-456A", "displayName": "Compute Engine"}]}
            return {"skus": [GCP_SKU, preempt, glob], "nextPageToken": ""}
        with mock.patch.dict(os.environ, {"GCP_API_KEY": "dummy"}):
            res = gcp.fetch({"services": ["Compute Engine"], "regions": ["europe-west3"]}, get=fake_get)
        self.assertEqual(len(res.records), 1)           # Preemptible und global (nicht in global_services) raus
        r = res.records[0]
        self.assertAlmostEqual(r.price, 0.03588)
        self.assertEqual((r.region, r.unit, r.currency, r.price_type), ("europe-west3", "hour", "EUR", PT_ON_DEMAND))

    def test_default_get_sends_header(self):
        seen = {}

        @contextmanager
        def fake_stream(url, **kw):
            seen.update(kw.get("headers", {}))
            yield io.BytesIO(b'{"services": []}')
        with mock.patch.object(gcp, "open_stream", fake_stream):
            gcp.default_get("https://x", "geheim")
        self.assertEqual(seen["x-goog-api-key"], "geheim")


# ------------------------------------------------------------------ OTC
class OtcTests(unittest.TestCase):
    REC = {"id": "OTC_S2M4_LI", "productName": "General Purpose 1:4 v2 s2.m.4 Linux", "opiFlavour": "s2.medium.4",
           "priceAmount": 0.046, "currency": "EUR", "unit": "h", "region": "eu-de", "osUnit": "Linux"}

    def page(self, records, stats=None):
        return {"response": {"url": "x", "responseCode": 200, "code": "success", "message": "Success",
                             "stats": stats or {}, "result": {"services": {"ecs": records}}}}

    def test_paging_without_assumed_page_size(self):
        """API liefert nur 100 je Seite statt 500 -> trotzdem alle Seiten holen."""
        calls = []

        def fake_get(url):
            calls.append(url)
            off = int(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["limitFrom"][0])
            if off >= 250:
                return self.page([])
            return self.page([dict(self.REC, id=f"ID{off + i}") for i in range(min(100, 250 - off))])
        res = otc.fetch({"services": ["ecs"], "regions": ["eu-de"]}, get=fake_get)
        self.assertEqual(len(res.records), 250)
        self.assertIn("serviceName%5B%5D=ecs", calls[0])

    def test_total_and_repeated_page(self):
        res = otc.fetch({"services": ["ecs"], "regions": ["eu-de"]},
                        get=lambda url: self.page([self.REC], {"recordsTotal": 1}))
        self.assertEqual(len(res.records), 1)
        res = otc.fetch({"services": ["ecs"], "regions": ["eu-de"]}, get=lambda url: self.page([self.REC]))
        self.assertEqual(len(res.records), 1)   # API ignoriert limitFrom -> Abbruch statt Endlosschleife

    def test_comma_decimal_and_dropped(self):
        recs = [dict(self.REC, priceAmount="0,046"), dict(self.REC, id="X", priceAmount="n/a")]
        res = otc.fetch({"services": ["ecs"], "regions": ["eu-de"]},
                        get=lambda url: self.page(recs if "limitFrom=0" in url else []))
        self.assertEqual([r.price for r in res.records], [0.046])
        self.assertTrue(any("verworfen" in w for w in res.warnings))
        self.assertEqual(otc.parse_amount("1.234,56"), 1234.56)
        self.assertEqual(otc.parse_amount("1,234.56"), 1234.56)

    def test_api_error(self):
        res_get = lambda url: {"response": {"responseCode": 500, "code": "error", "message": "kaputt"}}  # noqa: E731
        with self.assertRaises(RuntimeError):
            otc.fetch({"services": ["ecs"], "regions": ["eu-de"]}, get=res_get)


if __name__ == "__main__":
    unittest.main()
