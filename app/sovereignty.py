"""Datenhoheit: Rechtsraum des Anbieters und Standort der Region.

Stufen (für Filter und Kennzeichnung):
* ``eu_souveraen`` – Anbieter mit Sitz und Konzernmutter in der EU, Region in der EU
* ``eu_region``    – Region in der EU, Anbieter unterliegt aber (auch) US-Recht (z. B. CLOUD Act)
* ``nicht_eu``     – Region außerhalb der EU (z. B. Schweiz, Vereinigtes Königreich, USA)
* ``unbekannt``    – Region lässt sich keinem Land zuordnen (z. B. „global“)

Die Zuordnung ist eine Orientierung, keine Rechtsberatung. Abweichungen und weitere Regionen
lassen sich in config.toml unter [datenhoheit] ergänzen:

    [datenhoheit]
    regionen = { "eu02" = "AT" }        # Region -> ISO-Ländercode
    anbieter = { stackit = "DE" }       # Anbieter -> Rechtsraum (Sitz der Konzernmutter)
"""
from __future__ import annotations

from .config import load_config

EU = {"EU", "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU", "IE", "IT",
      "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK"}

# Rechtsraum der Konzernmutter
PROVIDER_JURISDICTION = {
    "stackit": "DE", "ionos": "DE", "otc": "DE", "plusserver": "DE", "hetzner": "DE",
    "ovh": "FR", "scaleway": "FR",
    "azure": "US", "aws": "US", "gcp": "US",
}

COUNTRY_NAMES = {"EU": "EU (mehrere Standorte)", "DE": "Deutschland", "NL": "Niederlande", "IE": "Irland", "FR": "Frankreich", "SE": "Schweden",
                 "IT": "Italien", "ES": "Spanien", "PL": "Polen", "FI": "Finnland", "BE": "Belgien",
                 "AT": "Österreich", "CH": "Schweiz", "GB": "Vereinigtes Königreich", "NO": "Norwegen",
                 "US": "USA", "CA": "Kanada", "SG": "Singapur", "AU": "Australien", "IN": "Indien",
                 "JP": "Japan", "DK": "Dänemark"}

REGION_COUNTRY = {
    # Azure
    "germanywestcentral": "DE", "germanynorth": "DE", "westeurope": "NL", "northeurope": "IE",
    "francecentral": "FR", "francesouth": "FR", "swedencentral": "SE", "swedensouth": "SE",
    "italynorth": "IT", "spaincentral": "ES", "polandcentral": "PL", "austriaeast": "AT",
    "belgiumcentral": "BE", "denmarkeast": "DK", "switzerlandnorth": "CH", "switzerlandwest": "CH",
    "uksouth": "GB", "ukwest": "GB", "norwayeast": "NO", "norwaywest": "NO",
    "eastus": "US", "eastus2": "US", "westus": "US", "westus2": "US", "westus3": "US", "centralus": "US",
    # AWS
    "eu-central-1": "DE", "eu-central-2": "CH", "eu-west-1": "IE", "eu-west-2": "GB", "eu-west-3": "FR",
    "eu-north-1": "SE", "eu-south-1": "IT", "eu-south-2": "ES", "eusc-de-east-1": "DE",
    "us-east-1": "US", "us-east-2": "US", "us-west-1": "US", "us-west-2": "US",
    # Google Cloud
    "europe-west1": "BE", "europe-west2": "GB", "europe-west3": "DE", "europe-west4": "NL",
    "europe-west6": "CH", "europe-west8": "IT", "europe-west9": "FR", "europe-west10": "DE",
    "europe-west12": "IT", "europe-north1": "FI", "europe-north2": "SE", "europe-central2": "PL",
    "europe-southwest1": "ES",
    # STACKIT, IONOS, OTC
    "eu01": "DE", "eu02": "AT", "de": "DE", "de/fra": "DE", "de/txl": "DE", "de/fkb": "DE",
    "eu-de": "DE", "eu-nl": "NL", "eu-ch2": "CH",
    # Scaleway
    "fr-par": "FR", "nl-ams": "NL", "pl-waw": "PL", "it-mil": "IT",
    # Hetzner
    "fsn1": "DE", "nbg1": "DE", "hel1": "FI", "ash": "US", "hil": "US", "sin": "SG",
    # OVHcloud
    "gra": "FR", "sbg": "FR", "rbx": "FR", "eu-west-par": "FR", "eu-west-gra": "FR", "eu-west-sbg": "FR",
    "eu-west-rbx": "FR", "ovh-eu": "EU", "lz-eu": "EU", "lz-eurozone": "EU", "lz-af": "", "de1": "DE", "eu-central-waw": "PL", "waw": "PL", "uk1": "GB", "bhs": "CA",
    # plusserver
    "de-north-2": "DE", "de-west-1": "DE", "de-west-2": "DE",
}

LEVELS = {
    "eu_souveraen": ("EU-Anbieter, EU-Region", "Sitz des Anbieters und Datenstandort in der EU"),
    "eu_region": ("EU-Region, US-Anbieter", "Daten in der EU, Anbieter unterliegt auch US-Recht (CLOUD Act)"),
    "nicht_eu": ("außerhalb der EU", "Datenstandort außerhalb der EU"),
    "unbekannt": ("Standort unbekannt", "Region lässt sich keinem Land zuordnen (z. B. global)"),
}
FILTERS = {"": "alle", "eu": "nur EU-Regionen", "eu_souveraen": "nur EU-Anbieter in EU-Regionen"}


def _cfg() -> dict:
    return load_config().get("datenhoheit", {})


class Classifier:
    """Zuordnung mit einmal geladener Konfiguration (für Schleifen über viele Preise)."""

    def __init__(self):
        cfg = _cfg()
        self.regions = {**REGION_COUNTRY, **{str(k).lower(): str(v).upper()
                                             for k, v in (cfg.get("regionen") or {}).items()}}
        self.providers = {**PROVIDER_JURISDICTION, **{str(k): str(v).upper()
                                                      for k, v in (cfg.get("anbieter") or {}).items()}}

    def country(self, region: str) -> str:
        r = (region or "").strip().lower()
        if r in self.regions:
            return self.regions[r]
        # Zonen/Unterregionen: "fr-par-1" -> "fr-par", "eu-de-01" -> "eu-de", "gra7" -> "gra"
        for cand in (r.rsplit("-", 1)[0], r.rstrip("0123456789"), r.split("/")[0]):
            if cand in self.regions:
                return self.regions[cand]
        for prefix, country in (("germany", "DE"), ("france", "FR"), ("europe", ""), ("uk", "GB"),
                                ("switzerland", "CH"), ("norway", "NO"), ("us-", "US"), ("us", "US")):
            if r.startswith(prefix) and country:
                return country
        return ""

    def classify(self, provider: str, region: str) -> dict:
        country = self.country(region)
        jur = self.providers.get(provider, "")
        if not country:
            level = "unbekannt"
        elif country not in EU:
            level = "nicht_eu"
        elif jur in EU:
            level = "eu_souveraen"
        else:
            level = "eu_region"
        label, hint = LEVELS[level]
        return {"level": level, "label": label, "hint": hint, "country": country,
                "country_name": COUNTRY_NAMES.get(country, country), "jurisdiction": jur}

    def matches(self, provider: str, region: str, wanted: str) -> bool:
        if not wanted:
            return True
        level = self.classify(provider, region)["level"]
        if wanted == "eu":
            return level in ("eu_souveraen", "eu_region")
        return level == wanted


def classify(provider: str, region: str) -> dict:
    return Classifier().classify(provider, region)
