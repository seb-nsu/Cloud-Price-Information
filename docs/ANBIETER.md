# Anbieter – Quellen, Umfang, Risiken

> **Wichtig:** Während der Entwicklung waren die Anbieter-Schnittstellen nicht erreichbar. Nur STACKIT wurde
> mit einem **echten API-Ausschnitt** getestet. Alle anderen Module sind nach der jeweiligen Dokumentation gebaut
> und mit Beispielantworten getestet. Vor dem Produktivbetrieb ist deshalb der Go-Live-Test aus
> [BETRIEB.md](BETRIEB.md#go-live-test) nötig.

| Anbieter | Quelle | Scopes | Währung | Einschätzung |
|---|---|---|---|---|
| STACKIT | `https://pim.api.stackit.cloud/v1/skus` (PIM-API, öffentlich) | `alle` | EUR | **gering**: echter Datenausschnitt getestet |
| Azure | `https://prices.azure.com/api/retail/prices` (Retail Prices API) | Dienst/Region/Preistyp | EUR (Orientierungswert, Abrechnungsbasis USD) | gering bis mittel |
| AWS | Price List Bulk API, regionale CSV je Dienst | Dienst/Region | USD | gering bis mittel: große Dateien |
| Google | Cloud Billing Catalog API (API-Key) | Dienst | EUR | gering, aber **API-Key nötig** |
| IONOS | Preisliste auf docs.ionos.com als Markdown | `preisliste` | EUR | **mittel**: es gibt keine API, die App liest die Dokumentationsseite aus |
| Open Telekom Cloud | Price Calculator API | Dienst/Region | EUR | **mittel bis hoch**: nur knapp dokumentiert |

## STACKIT

- Übernommen werden alle SKUs außer abgekündigten (`include_deprecated`) und in der Preisliste ausgeblendeten (`include_hidden`).
- SKUs ohne gültigen Preis werden übersprungen und gemeldet.
- `monthlyPrice` (720 h) steht als Attribut zur Verfügung. Für den Vergleich normiert die App auf 730 h.

## Microsoft Azure

- Konfigurierbar sind Dienste, Regionen und Preistypen (`Consumption`, `Reservation`); Savings-Plan-Preise sind optional.
- Die App fragt nur „primäre“ Meter ab (`meterRegion='primary'`).
- **Paging:** Die Folge-URL baut die App selbst aus `$skip` und den eigenen Parametern. Jede Position wird auf
  die erwartete Währung geprüft. Weicht sie ab, scheitert nur dieser Scope.
- **Spot und Low Priority** werden am SKU- bzw. Meter-Namen erkannt und als „Spot“ geführt, nicht als On-Demand.
- Microsoft rechnet in USD ab. EUR-Preise sind laut Dokumentation Orientierungswerte.

## AWS

- Je Dienst und Region wird `…/<Dienst>/current/<Region>/index.csv` gestreamt, mit gzip, wenn der Server es anbietet.
- Gefiltert wird spaltenindexbasiert, und zwar zuerst nach `TermType`.
- EC2 berücksichtigt geteilte Tenancy, aktive Kapazität, keine vorinstallierte Software, die konfigurierten
  Betriebssysteme und auch Bare-Metal-Instanzen.
- Spaltennamen, die sich zwischen Dateiversionen unterscheiden, werden über Aliase aufgelöst (`usageType` ↔ `Usage Type`).
- Die EC2-Datei für eine Region umfasst mehrere hundert MB. Der Abruf läuft deshalb in einem eigenen Prozess und
  braucht einige Minuten.

## Google Cloud

- Der API-Key steht in `GCP_API_KEY` und geht im Header `x-goog-api-key` an Google.
- `global_services` legt fest, für welche Dienste globale SKUs übernommen werden (Standard: Networking).
- Die Usage Types `OnDemand`, `Preemptible`, `Commit1Yr` und `Commit3Yr` lassen sich auswählen.
- Gestaffelte Preise werden als eigene Einträge geführt.

## IONOS Cloud

- Die App liest Pipe-Tabellen **und eingebettete HTML-Tabellen**.
- **Schlüssel** sind normalisiert: Kleinschreibung, ohne Satz- und Leerzeichen, nur die letzten zwei
  Überschriftenebenen. Kosmetische Änderungen führen deshalb nicht zu „entfallen und neu“. Verbleibende
  Umbenennungen erkennt der Abgleich.
- **Zahlenformat:** Die App ermittelt das überwiegende Dezimaltrennzeichen. Mehrdeutige Werte wie „2.500 €“
  werden markiert und als Warnung im Abruf gemeldet. Weicht ein Wert stark vom Tabellenmedian ab, kommt der
  Hinweis „bitte prüfen“ hinzu.
- **Risiko:** IONOS kann das Seitenlayout jederzeit ändern. In diesem Fall greifen die Schutzregeln, und die
  Vorschau zeigt die Auswirkungen, bevor etwas übernommen wird.

## Open Telekom Cloud

- Die App nutzt den englischen Endpunkt (Dezimalpunkt). Dezimalkomma wird trotzdem akzeptiert.
- **Paging** setzt keine feste Seitengröße voraus. Der Abruf endet bei einer leeren Seite, einer sich
  wiederholenden Seite (die API ignoriert `limitFrom`) oder wenn die gemeldete Gesamtzahl erreicht ist.
- Datensätze ohne lesbaren Preis werden verworfen und als Warnung gemeldet.
- **Risiko:** Antwortformat und Dienstkürzel sind nur knapp dokumentiert. Beim ersten Abruf deshalb die Hinweise
  im Abrufprotokoll und die Preisanzahl je Dienst prüfen.

## Neues Abruf-Modul anlegen

1. Datei `app/providers/<name>.py` mit `LABEL`, `INFO` und `fetch(cfg) -> FetchResult` anlegen.
2. Je Abrufeinheit `ScopeCollector.run(scope, func)` verwenden. Dadurch scheitern Fehler nur im betroffenen Scope.
3. Die Preistypen aus `app/models.py` verwenden und einen stabilen `key` vergeben.
4. Das Modul in `app/providers/__init__.py` registrieren und einen Abschnitt in `config.toml` ergänzen.
5. Tests mit Beispielantworten in `tests/test_providers.py` schreiben.
