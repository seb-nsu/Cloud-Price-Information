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
| Hetzner Cloud | `https://api.hetzner.cloud/v1/pricing` und `/v1/server_types` | `preise`, `servertypen` | EUR (netto) | gering, aber **API-Token nötig** |
| Scaleway | Product Catalog API (`/product-catalog/v2alpha1/public-catalog/products`, öffentlich) | Zone/Region | EUR | gering bis mittel: API ist als „alpha“ gekennzeichnet |
| OVHcloud | öffentlicher Bestellkatalog Public Cloud (`eu.api.ovh.com/v1/order/catalog/public/cloud`) | `katalog` | EUR | **hoch, experimentell**, standardmäßig deaktiviert |

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
- **Größen:** vCPU und RAM der Serien D, E und F ab v4 werden aus dem Namen abgeleitet. Für B, M, L, N, H, A,
  ältere D/E/F-Generationen und alle GPU-Größen gibt es eine Tabelle in `app/azure_sizes.py` (Stand
  Microsoft-Dokumentation 2026). Neue Größen dort ergänzen; nicht erkannte Größen erscheinen weiter in der
  Preisliste, nur nicht im VM-Vergleich.

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

## Hetzner Cloud

- Die API verlangt ein Token (`HCLOUD_TOKEN`). Ein Token mit **Leserechten** aus einem beliebigen, auch leeren
  Projekt genügt. Ohne Token wird Hetzner beim Abruf übersprungen.
- Übernommen werden **Netto-Preise** in EUR: Servertypen je Standort (Monatspreis; Hetzner rechnet stündlich ab,
  höchstens bis zum Monatspreis), Volumes, Snapshots/Images, Primary IPs, Load Balancer und Traffic über dem
  Inklusivvolumen.
- `locations` begrenzt die Standorte, `include_deprecated` nimmt abgekündigte Servertypen mit.
- vCPU, RAM und Typ (shared/dedicated) kommen direkt aus `/v1/server_types`.

## Scaleway

- Die Product Catalog API ist ohne Anmeldung erreichbar. `SCW_SECRET_KEY` ist optional.
- `zones` begrenzt die Standorte; ein Präfix genügt (`fr-par` umfasst `fr-par-1/2/3`).
- Hardware-Angaben (vCPU, RAM, GPU) kommen aus dem Katalog. Instanzen, Block- und Objektspeicher, Load Balancer,
  Kubernetes, Datenbanken und KI-Dienste werden übernommen.
- **Risiko:** Die API trägt die Version `v2alpha1`. Der Formatwächter meldet Formatänderungen.

## OVHcloud (experimentell)

- Quelle ist der öffentliche Bestellkatalog Public Cloud (`ovhSubsidiary = DE`). Preise stehen dort in
  1/100.000.000 der Währung.
- Instanzen gibt es stündlich (`.consumption`) und monatlich (`.monthly.postpaid`). Die Region „Paris 3-AZ“ und
  Local Zones werden als eigene Regionen geführt.
- Das Format ist nur teilweise dokumentiert und konnte nicht live geprüft werden. Der Anbieter ist deshalb in
  `config.toml` **deaktiviert** (`enabled = false`). Vor dem Einschalten einen Abruf machen und Hinweise,
  Formatwächter und Stichproben prüfen.

## plusserver und IONOS-API

- **plusserver** (pluscloud open) veröffentlicht keine maschinenlesbare Preisliste und keine Preis-API. Die
  Preise stehen nur als Webseite bzw. im Angebot. Eine Anbindung wäre über eine gepflegte Tabelle möglich. Die
  Regionen `de-north-2`, `de-west-1` und `de-west-2` sind für die Datenhoheit bereits hinterlegt.
- **IONOS** bietet für Cloud-Listenpreise keine öffentliche Preis-API. Die Billing API braucht einen
  Vertragszugang und bezieht sich auf Verbräuche, Rechnungen und Preise dieses Vertrags, nicht auf die
  öffentliche Liste. Deshalb bleibt es beim Auslesen der Preisliste auf docs.ionos.com. Wer einen Vertrag mit
  Sonderkonditionen hat, könnte die Billing API später als eigenes Modul anbinden.

## Neues Abruf-Modul anlegen

1. Datei `app/providers/<name>.py` mit `LABEL`, `INFO` und `fetch(cfg) -> FetchResult` anlegen.
2. Je Abrufeinheit `ScopeCollector.run(scope, func)` verwenden. Dadurch scheitern Fehler nur im betroffenen Scope.
3. Die Preistypen aus `app/models.py` verwenden und einen stabilen `key` vergeben.
4. Das Modul in `app/providers/__init__.py` registrieren und einen Abschnitt in `config.toml` ergänzen.
5. Tests mit Beispielantworten in `tests/fixtures/` und `tests/test_providers.py` bzw. `tests/test_extensions.py` schreiben.
6. Pflichtfelder für den Formatwächter in `app/watch.py` (`REQUIRED`) und Regionen in `app/sovereignty.py` ergänzen.
