from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Einheitliche Kategorien für die Oberfläche
CATEGORIES = ["Compute", "Storage", "Kubernetes", "Datenbank", "Netzwerk", "KI", "Sonstiges"]

# Einheitliche Preistypen (Taxonomie) – alle Abruf-Module verwenden nur diese Werte
PT_ON_DEMAND = "On-Demand"
PT_SPOT = "Spot"
PT_RESERVED_1Y = "Reserviert 1 J."
PT_RESERVED_3Y = "Reserviert 3 J."
PT_SAVINGS_1Y = "Savings Plan 1 J."
PT_SAVINGS_3Y = "Savings Plan 3 J."
PT_DEVTEST = "Dev/Test"
PT_OTHER = "Sonstiges"
PRICE_TYPES = [PT_ON_DEMAND, PT_SPOT, PT_RESERVED_1Y, PT_RESERVED_3Y, PT_SAVINGS_1Y,
               PT_SAVINGS_3Y, PT_DEVTEST, PT_OTHER]


@dataclass
class PriceRecord:
    """Ein einzelner Listenpreis, wie ihn ein Abruf-Modul liefert.

    `key` muss je Anbieter eindeutig und über Abrufe hinweg stabil sein –
    darüber wird erkannt, ob sich ein Preis geändert hat.
    `scope` ist die Abrufeinheit (z. B. "AmazonS3/eu-central-1"), aus der der
    Preis stammt. Preise werden nur innerhalb erfolgreich abgerufener Scopes
    als „nicht mehr gelistet“ archiviert.
    """

    key: str
    category: str
    service: str
    product: str
    region: str
    unit: str
    price: float
    currency: str
    price_type: str = PT_ON_DEMAND
    attributes: dict[str, Any] = field(default_factory=dict)
    scope: str = ""


@dataclass
class FetchResult:
    records: list[PriceRecord]
    source: str = ""          # URL / Beschreibung der Quelle
    source_date: str = ""     # Stand laut Anbieter, falls bekannt
    warnings: list[str] = field(default_factory=list)
    # Scopes, die vollständig und erfolgreich abgerufen wurden (auch wenn leer).
    # None = Abruf-Modul kennt keine Scopes -> ein einziger Scope "" für alles.
    scopes_ok: set[str] | None = None
    # Scopes, deren Abruf fehlgeschlagen ist (Bestand bleibt dort unangetastet)
    scopes_failed: dict[str, str] = field(default_factory=dict)

    def effective_scopes_ok(self) -> set[str]:
        if self.scopes_ok is not None:
            return set(self.scopes_ok)
        return {r.scope for r in self.records} | {""}


class ProviderNotConfigured(Exception):
    """Anbieter kann ohne zusätzliche Konfiguration (z. B. API-Key) nicht abgerufen werden."""


class ScopeCollector:
    """Hilfsklasse für Abruf-Module: führt je Scope aus und sammelt Erfolg/Fehler."""

    def __init__(self):
        self.records: list[PriceRecord] = []
        self.ok: set[str] = set()
        self.failed: dict[str, str] = {}
        self.warnings: list[str] = []

    def run(self, scope: str, func) -> list[PriceRecord] | None:
        try:
            recs = list(func())
        except ProviderNotConfigured:
            raise
        except Exception as exc:  # noqa: BLE001
            self.failed[scope] = f"{type(exc).__name__}: {exc}"[:500]
            self.warnings.append(f"{scope}: Abruf fehlgeschlagen ({type(exc).__name__}: {str(exc)[:200]})")
            return None
        for r in recs:
            r.scope = scope
        self.records.extend(recs)
        self.ok.add(scope)
        if not recs:
            self.warnings.append(f"{scope}: keine Preise geliefert")
        return recs

    def result(self, source: str = "", source_date: str = "") -> FetchResult:
        if not self.ok and self.failed:
            raise RuntimeError("Alle Abrufe fehlgeschlagen: " + "; ".join(
                f"{k}: {v}" for k, v in list(self.failed.items())[:5]))
        return FetchResult(records=self.records, source=source, source_date=source_date,
                           warnings=self.warnings, scopes_ok=self.ok, scopes_failed=self.failed)
