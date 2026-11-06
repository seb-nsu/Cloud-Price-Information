"""Amazon Web Services – AWS Price List Bulk API (öffentlich, ohne Anmeldung).

Je Dienst und Region wird die CSV-Preisdatei gestreamt (gzip, wenn angeboten) und
spaltenindex-basiert vorgefiltert – erst passende Zeilen werden in Datensätze umgewandelt:
https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/<Dienst>/current/<Region>/index.csv
Doku: https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/using-ppslong.html
"""
from __future__ import annotations

import csv
import io
from typing import TextIO

from ..http_util import open_stream
from ..models import (PT_ON_DEMAND, PT_RESERVED_1Y, PT_RESERVED_3Y, FetchResult, PriceRecord,
                      ScopeCollector)

LABEL = "Amazon Web Services"
INFO = "AWS Price List Bulk API · Preise in USD"
BASE = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/{service}/current/{region}/index.csv"

EC2_FAMILIES = {"Compute Instance", "Compute Instance (bare metal)", "Storage", "Storage Snapshot",
                "Data Transfer", "IP Address", "NAT Gateway", "System Operation", "Provisioned Throughput"}
RDS_FAMILIES = {"Database Instance", "Database Storage", "Storage Snapshot",
                "Provisioned IOPS", "Provisioned Throughput", "System Operation"}
INSTANCE_FAMILIES = {"Compute Instance", "Compute Instance (bare metal)"}

SERVICE_CATEGORY = {"AmazonEC2": "Compute", "AmazonS3": "Storage",
                    "AmazonEKS": "Kubernetes", "AmazonRDS": "Datenbank"}
FAMILY_CATEGORY = {"Storage": "Storage", "Storage Snapshot": "Storage",
                   "System Operation": "Storage", "Provisioned Throughput": "Storage",
                   "Data Transfer": "Netzwerk", "IP Address": "Netzwerk", "NAT Gateway": "Netzwerk"}
# Spaltennamen variieren zwischen Dateiversionen
ALIASES = {"Usage Type": ("usageType", "Usage Type"), "Operation": ("operation", "Operation"),
           "Region Code": ("Region Code", "regionCode")}
ATTR_COLS = ("SKU", "RateCode", "Instance Type", "vCPU", "Memory", "Operating System",
             "Database Engine", "Usage Type", "Volume API Name", "Storage Class", "EffectiveDate",
             "PriceDescription", "Deployment Option", "License Model")


def read_offer_csv(fh: TextIO) -> tuple[dict[str, str], list[str], "csv._reader"]:
    """Liest die 5 Metadatenzeilen und die Kopfzeile; liefert danach einen csv.reader."""
    meta = {}
    for _ in range(5):
        row = next(csv.reader([fh.readline()]), [])
        if len(row) >= 2:
            meta[row[0]] = row[1]
    reader = csv.reader(fh)
    header = next(reader)
    return meta, header, reader


class _Cols:
    def __init__(self, header: list[str]):
        self.idx = {h: i for i, h in enumerate(header)}
        for canon, names in ALIASES.items():
            for n in names:
                if n in self.idx:
                    self.idx.setdefault(canon, self.idx[n])
                    break

    def get(self, row: list[str], name: str) -> str:
        i = self.idx.get(name)
        return row[i] if i is not None and i < len(row) else ""


def _keep(service: str, c: _Cols, row: list[str], cfg: dict) -> bool:
    if c.get(row, "TermType") not in cfg.get("term_types", ["OnDemand"]):
        return False
    family = c.get(row, "Product Family")
    if service == "AmazonEC2":
        if family not in EC2_FAMILIES:
            return False
        if family in INSTANCE_FAMILIES:
            oses = cfg.get("ec2_operating_systems", ["Linux", "Windows", "RHEL", "SUSE"])
            return (c.get(row, "Tenancy") == "Shared"
                    and c.get(row, "CapacityStatus") == "Used"
                    and c.get(row, "Pre Installed S/W") in ("NA", "")
                    and c.get(row, "Operating System") in oses
                    and c.get(row, "License Model") != "Bring your own license")
    if service == "AmazonRDS":
        if family not in RDS_FAMILIES:
            return False
        if family == "Database Instance" and c.get(row, "License Model") == "Bring your own license":
            return False
    return True


def _to_record(service: str, region: str, c: _Cols, row: list[str]) -> PriceRecord | None:
    g = lambda name: c.get(row, name)  # noqa: E731
    try:
        price = float(g("PricePerUnit"))
    except ValueError:
        return None
    family = g("Product Family")
    if family in INSTANCE_FAMILIES:
        product = " · ".join(x for x in (g("Instance Type"), g("Operating System"),
                                         f"{g('vCPU')} vCPU / {g('Memory')}" if g("vCPU") else "") if x)
    elif family == "Database Instance":
        product = " · ".join(x for x in (g("Instance Type"), g("Database Engine"),
                                         g("Deployment Option"), g("License Model")) if x)
    else:
        product = g("PriceDescription") or family
    rng_from, rng_to = g("StartingRange"), g("EndingRange")
    if rng_from and (rng_from != "0" or rng_to not in ("Inf", "")):
        product += f" [{rng_from}–{rng_to}]"

    if g("TermType") == "Reserved":
        price_type = PT_RESERVED_3Y if g("LeaseContractLength").startswith("3") else PT_RESERVED_1Y
        product += f" · {g('PurchaseOption')} {g('OfferingClass')}".rstrip()
    else:
        price_type = PT_ON_DEMAND

    attrs = {k: g(k) for k in ATTR_COLS if g(k)}
    return PriceRecord(
        key=f"{g('SKU')}|{g('RateCode')}",
        category=FAMILY_CATEGORY.get(family, SERVICE_CATEGORY.get(service, "Sonstiges")),
        service=f"{service} · {family}" if family else service,
        product=product,
        region=g("Region Code") or region,
        unit=g("Unit"),
        price=price,
        currency=g("Currency") or "USD",
        price_type=price_type,
        attributes=attrs,
    )


def parse_offer(service: str, region: str, fh: TextIO, cfg: dict) -> tuple[dict, list[PriceRecord]]:
    meta, header, reader = read_offer_csv(fh)
    c = _Cols(header)
    if "PricePerUnit" not in c.idx or "TermType" not in c.idx:
        raise RuntimeError(f"Unerwartetes CSV-Format für {service}: Spalten PricePerUnit/TermType fehlen")
    i_term = c.idx["TermType"]
    allowed_terms = set(cfg.get("term_types", ["OnDemand"]))
    out = []
    for row in reader:
        # schneller Vorfilter ohne Dict-Aufbau
        if i_term >= len(row) or row[i_term] not in allowed_terms:
            continue
        if _keep(service, c, row, cfg):
            rec = _to_record(service, region, c, row)
            if rec:
                out.append(rec)
    return meta, out


def fetch(cfg: dict, opener=open_stream) -> FetchResult:
    col = ScopeCollector()
    dates: list[str] = []

    def one(service: str, region: str):
        with opener(BASE.format(service=service, region=region), timeout=900) as raw:
            fh = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            meta, recs = parse_offer(service, region, fh, cfg)
        if meta.get("Publication Date"):
            dates.append(meta["Publication Date"])
        return recs

    for service in cfg.get("services", list(SERVICE_CATEGORY)):
        for region in cfg.get("regions", ["eu-central-1"]):
            col.run(f"{service}/{region}", lambda s=service, r=region: one(s, r))
    return col.result(source=BASE.format(service="…", region="…"), source_date=max(dates) if dates else "")
