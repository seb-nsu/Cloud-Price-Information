"""Normalisierung für den Anbietervergleich.

Jeder Preis bekommt zusätzlich:
* price_eur   – Preis in EUR (EZB-Referenzkurs; ohne Kurs: leer)
* unit_norm   – einheitliche Bezugsgröße: "Monat", "GB-Monat", "GB", "1 Mio. Token",
                "1.000 Anfragen" oder "" (nicht normierbar)
* price_norm  – EUR je normierter Einheit; Stundenpreise werden mit 730 h/Monat
                hochgerechnet, „30 Tage“-Preise mit 730/720
* vcpu, ram_gb, os – für Compute-Flavors (Vergleichsmatrix)

Die Originalwerte (price, unit, currency) bleiben unverändert maßgeblich für
den Abgleich und das Archiv.
"""
from __future__ import annotations

import re
from typing import Any

HOURS_PER_MONTH = 730.0
DAYS30_TO_MONTH = HOURS_PER_MONTH / 720.0

U_MONTH = "Monat"
U_GB_MONTH = "GB-Monat"
U_GB = "GB"
U_TOKENS = "1 Mio. Token"
U_REQ = "1.000 Anfragen"

_QTY = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([kKmM])?\b\s*(.*)$")


def _leading_quantity(unit: str) -> tuple[float, str]:
    """'100 Hours' -> (100, 'hours'); '10K' -> (10000, ''); '1 GB/Month' -> (1, 'gb/month')."""
    m = _QTY.match(unit)
    if not m:
        return 1.0, unit
    qty = float(m.group(1).replace(",", "."))
    mult = {"k": 1_000, "m": 1_000_000}.get((m.group(2) or "").lower(), 1)
    rest = m.group(3)
    # "1 GB/Month": die Zahl gehört zur Mengenangabe, "GB" bleibt Teil der Einheit
    return qty * mult, rest


def normalize_unit(unit: str) -> tuple[str, float]:
    """-> (normierte Einheit, Faktor), sodass price_norm = price_eur * Faktor."""
    raw = (unit or "").strip()
    u = raw.lower()
    if not u or u in ("—", "-"):
        return "", 0.0

    # IONOS: "pro 10.000 Calls"
    m = re.search(r"pro\s+([\d.]+)\s*(calls|anfragen|requests)", u)
    if m:
        return U_REQ, 1000.0 / float(m.group(1).replace(".", ""))

    qty, rest = _leading_quantity(u)
    r = rest.lower()
    if qty <= 0:
        qty = 1.0

    is_gb = bool(re.search(r"\bgb\b|gib|gigabyte|gibibyte|\(gb\)|/gb", r))
    is_hour = bool(re.search(r"hour|stunde|\bhrs?\b|^h$|\bh\b", r))
    is_month = bool(re.search(r"month|monat|\bmo\b|-mo\b", r))
    is_30d = "30 tage" in r or "30 days" in r
    is_token = "token" in r
    is_req = bool(re.search(r"request|anfrage|call|transaction|operation", r))

    if is_token:
        if "million" in r or "mio" in r:
            return U_TOKENS, 1.0 / qty
        return U_TOKENS, 1_000_000.0 / qty
    if is_gb and is_hour:
        return U_GB_MONTH, HOURS_PER_MONTH / qty
    if is_gb and (is_month or is_30d):
        return U_GB_MONTH, (DAYS30_TO_MONTH if is_30d else 1.0) / qty
    if is_hour:
        return U_MONTH, HOURS_PER_MONTH / qty
    if is_30d:
        return U_MONTH, DAYS30_TO_MONTH / qty
    if is_month:
        return U_MONTH, 1.0 / qty
    if is_req:
        return U_REQ, 1000.0 / qty
    if is_gb:
        return U_GB, 1.0 / qty
    return "", 0.0


# ------------------------------------------------------------------ VM-Merkmale
_OTC_SIZE = {"small": 1, "medium": 1, "large": 2, "xlarge": 4}
_AZURE_RAM_PER_VCPU = {"D": 4, "E": 8, "F": 2}


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    m = re.search(r"\d+(?:[.,]\d+)?", str(v))
    return float(m.group(0).replace(",", ".")) if m else None


def _os_from_text(text: str) -> str:
    t = text.lower()
    if "windows" in t:
        return "windows"
    if any(x in t for x in ("rhel", "red hat", "suse", "sles")):
        return "linux-lizenz"
    return "linux"


def extract_vm(provider: str, product: str, attrs: dict[str, Any]) -> tuple[float | None, float | None, str]:
    """-> (vCPU, RAM in GB, Betriebssystem) für Compute-Flavors, sonst (None, None, '')."""
    if provider == "aws":
        if "Instance Type" not in attrs:
            return None, None, ""
        os_ = attrs.get("Operating System", "")
        os_norm = "windows" if os_ == "Windows" else "linux" if os_ == "Linux" else "linux-lizenz"
        return _num(attrs.get("vCPU")), _num(attrs.get("Memory")), os_norm

    if provider == "stackit":
        if attrs.get("generalProductGroup") not in ("Virtual Machines", "Windows Server"):
            return None, None, ""
        os_norm = "windows" if attrs.get("generalProductGroup") == "Windows Server" else "linux"
        return _num(attrs.get("vCPU")), _num(attrs.get("ram")), os_norm

    if provider == "otc":
        vcpu, ram = _num(attrs.get("vCpu") or attrs.get("vcpu")), _num(attrs.get("ram"))
        flavor = attrs.get("opiFlavour", "")
        m = re.match(r"^[a-z]+\d*[a-z]*\.(\d*)(small|medium|large|xlarge)\.(\d+)$", flavor or "")
        if m and (vcpu is None or ram is None):
            mult = int(m.group(1)) if m.group(1) else 1
            vcpu = float(_OTC_SIZE[m.group(2)] * mult)
            ram = vcpu * int(m.group(3))
        if vcpu is None:
            return None, None, ""
        return vcpu, ram, _os_from_text(str(attrs.get("osUnit", "")) or product)

    if provider == "azure":
        sku = attrs.get("armSkuName", "")
        # nur aktuelle Generationen (v4+) mit festem RAM-Verhältnis; vCPU-eingeschränkte
        # Varianten (z. B. D8-4s) auslassen
        m = re.match(r"^Standard_([A-Z]+)(\d+)(-\d+)?([a-z]*)_v(\d+)$", sku)
        if not m or m.group(3) or int(m.group(5)) < 4 or m.group(1)[0] not in "DEF":
            return None, None, ""
        vcpu = float(m.group(2))
        ratio = _AZURE_RAM_PER_VCPU[m.group(1)[0]]
        if m.group(1)[0] == "D" and "l" in m.group(4):   # Dl-Serie: 2 GB/vCPU
            ratio = 2
        return vcpu, vcpu * ratio, _os_from_text(attrs.get("productName", "") + " " + product)

    if provider == "ionos":
        m = re.search(r"(\d+)\s*/\s*(\d+)\s*GB", product)
        if m and "Cube" in product:
            return float(m.group(1)), float(m.group(2)), "linux"
    return None, None, ""


# ------------------------------------------------------------------ Gesamt
def normalize(provider: str, category: str, product: str, unit: str, price: float,
              currency: str, attrs: dict[str, Any], rates: dict[str, float]) -> dict[str, Any]:
    if currency == "EUR":
        price_eur = price
    elif currency in rates and rates[currency] > 0:
        price_eur = price / rates[currency]
    else:
        price_eur = None
    unit_norm, factor = normalize_unit(unit)
    price_norm = round(price_eur * factor, 8) if (price_eur is not None and unit_norm) else None
    if price_eur is not None:
        price_eur = round(price_eur, 10)
    vcpu = ram = None
    os_ = ""
    if category == "Compute" and unit_norm == U_MONTH:
        vcpu, ram, os_ = extract_vm(provider, product, attrs)
    return {"price_eur": price_eur, "unit_norm": unit_norm, "price_norm": price_norm,
            "vcpu": vcpu, "ram_gb": ram, "os": os_}
