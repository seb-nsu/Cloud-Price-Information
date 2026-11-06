from __future__ import annotations

import unittest

from app import fx
from app.normalize import U_GB, U_GB_MONTH, U_MONTH, U_REQ, U_TOKENS, extract_vm, normalize, normalize_unit

from .helpers import TempDB

ECB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
<gesmes:subject>Reference rates</gesmes:subject><Cube><Cube time="2026-11-05">
<Cube currency="USD" rate="1.0850"/><Cube currency="GBP" rate="0.8420"/></Cube></Cube></gesmes:Envelope>"""


class UnitTests(unittest.TestCase):
    CASES = {
        "1 Hour": (U_MONTH, 730), "Hrs": (U_MONTH, 730), "h": (U_MONTH, 730), "per hour": (U_MONTH, 730),
        "pro Stunde": (U_MONTH, 730), "100 Hours": (U_MONTH, 7.3), "per GB/hour": (U_GB_MONTH, 730),
        "gibibyte hour": (U_GB_MONTH, 730), "GB-Mo": (U_GB_MONTH, 1), "1 GB/Month": (U_GB_MONTH, 1),
        "10 GB/Month": (U_GB_MONTH, 0.1), "gibibyte month": (U_GB_MONTH, 1),
        "pro 30 Tage (GB)": (U_GB_MONTH, 730 / 720), "pro 30 Tage": (U_MONTH, 730 / 720), "pro Monat": (U_MONTH, 1),
        "1/Month": (U_MONTH, 1), "GB": (U_GB, 1), "gibibyte": (U_GB, 1), "pro Million Input-Token": (U_TOKENS, 1),
        "mio output token": (U_TOKENS, 1), "1M": ("", 0), "Requests": (U_REQ, 1000), "pro 10.000 Calls": (U_REQ, 0.1),
        "(einmalig)": ("", 0), "—": ("", 0),
    }

    def test_units(self):
        for unit, (norm, factor) in self.CASES.items():
            n, f = normalize_unit(unit)
            self.assertEqual(n, norm, unit)
            self.assertAlmostEqual(f, factor, msg=unit)

    def test_vm_extraction(self):
        self.assertEqual(extract_vm("azure", "", {"armSkuName": "Standard_D4s_v5", "productName": "Dsv5 Series Windows"}),
                         (4.0, 16.0, "windows"))
        self.assertEqual(extract_vm("azure", "", {"armSkuName": "Standard_E8ds_v5"}), (8.0, 64.0, "linux"))
        self.assertEqual(extract_vm("azure", "", {"armSkuName": "Standard_D8-4s_v5"}), (None, None, ""))
        self.assertEqual(extract_vm("azure", "", {"armSkuName": "Standard_DS2_v2"}), (None, None, ""))
        self.assertEqual(extract_vm("otc", "", {"opiFlavour": "c6.2xlarge.4", "osUnit": "Linux"}), (8.0, 32.0, "linux"))
        self.assertEqual(extract_vm("aws", "", {"Instance Type": "t3.medium", "vCPU": "2", "Memory": "4 GiB",
                                                "Operating System": "Linux"}), (2.0, 4.0, "linux"))
        self.assertEqual(extract_vm("stackit", "", {"generalProductGroup": "Virtual Machines", "vCPU": 1, "ram": 2}),
                         (1.0, 2.0, "linux"))

    def test_normalize_usd(self):
        n = normalize("aws", "Compute", "t3", "Hrs", 0.0464, "USD",
                      {"Instance Type": "t3.medium", "vCPU": "2", "Memory": "4 GiB", "Operating System": "Linux"},
                      {"USD": 1.16})
        self.assertAlmostEqual(n["price_eur"], 0.04)
        self.assertAlmostEqual(n["price_norm"], 29.2)
        self.assertEqual((n["vcpu"], n["ram_gb"], n["os"]), (2.0, 4.0, "linux"))
        self.assertIsNone(normalize("aws", "Compute", "", "Hrs", 1, "XYZ", {}, {})["price_eur"])


class FxTests(unittest.TestCase):
    def test_parse_and_store(self):
        date, rates = fx.parse_ecb(ECB_XML)
        self.assertEqual((date, rates["USD"]), ("2026-11-05", 1.085))
        with TempDB('[waehrung]\nfallback_kurse = { USD = 1.5, CHF = 0.9 }\n') as t:
            self.assertEqual(fx.get_rates(t.db)["USD"], 1.5)           # Fallback
            self.assertIn("geladen", fx.refresh(t.db, get=lambda url: ECB_XML))
            self.assertEqual(fx.refresh(t.db, get=lambda url: 1 / 0), "Kurse aktuell")   # Cache
            rates = fx.get_rates(t.db)
            self.assertEqual((rates["USD"], rates["CHF"], rates["EUR"]), (1.085, 0.9, 1.0))


if __name__ == "__main__":
    unittest.main()
