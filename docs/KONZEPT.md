# Konzept und Architektur

## Ziel

Die App fragt die öffentlichen Listenpreise mehrerer Cloud-Anbieter **manuell auf Knopfdruck** ab, zeigt den
**neuesten Stand** und **archiviert ältere Preise nachvollziehbar**. Das Archiv ist nur auf Anforderung
sichtbar. Darüber hinaus macht die App die Preise **vergleichbar**: Sie rechnet in EUR um, normiert die
Einheiten und bietet einen VM-Vergleich sowie Szenarien.

## Komponenten

```
app/
  providers/   Abruf-Module je Anbieter -> liefern PriceRecord-Listen je „Scope“
  sync.py      Diff, Vorschau (staging), Übernahme, Archivierung, Schutzregeln, Umbenennungen
  jobs.py      Ablaufsteuerung: DB-Sperre + Heartbeat, Hintergrundprozess, Freigabe/Verwerfen
  normalize.py Einheiten/Währung/VM-Merkmale normieren
  fx.py        EZB-Referenzkurse (täglich, Fallback aus config.toml)
  analysis.py  VM-Vergleich, Szenarien, Preisverlauf
  notify.py    Webhook/E-Mail bei Preisänderungen
  retention.py Archiv-Aufbewahrung mit CSV.gz-Export
  web.py       HTTP-Server (stdlib), Routing, Basic Auth, CSRF-Token, Zeitplan
  pages.py/ui.py  HTML-Seiten
  db.py        SQLite-Schema mit Migrationen (PRAGMA user_version)
```

Die App kommt ohne Fremdpakete aus. Das hält Installation, Updates und Sicherheitsprüfungen einfach.

## Datenmodell

| Tabelle | Zweck |
|---|---|
| `prices_current` | genau ein aktueller Preis je (Anbieter, Schlüssel) mit Originalwerten und normierten Werten |
| `prices_archive` | frühere Preise mit `valid_from`/`valid_to`, Grund und Nachfolgepreis |
| `fetch_runs` | jeder Abruf mit Status, Zählern, Warnungen, Schutzregel-Verstößen, Entscheidung |
| `staging_records` | Abrufergebnis, das auf Freigabe wartet (Vorschau) |
| `run_changes` | Änderungen je Abruf (geplant bzw. übernommen): neu, geändert, entfallen, umbenannt |
| `fx_rates` | Wechselkurse (EZB) |
| `scenarios`, `scenario_items` | Szenarien/Warenkorb mit Varianten |

**Schlüssel:** Jedes Abruf-Modul vergibt einen stabilen Schlüssel je Preis, zum Beispiel AWS `SKU|RateCode`,
Azure `meterId|skuId|Region|Typ|Laufzeit|Staffel`, STACKIT `sku`, Google `skuId|Region|Staffel`. Bei IONOS
entsteht er als normalisierter Text aus Abschnitt, Tabelle und Zeile.

## Abgleich und Archiv

```
Abruf ──> Vorschau (staging + run_changes) ──> [Freigabe] ──> Übernahme ──> Benachrichtigung, Aufbewahrung
                                            └─> [Verwerfen] ──> Bestand unverändert
```

Bei der Übernahme gelten diese Regeln (in einer Transaktion, gegen den dann aktuellen Bestand neu berechnet):

| Fall | Ergebnis |
|---|---|
| neuer Schlüssel | in `prices_current` |
| Preis, Einheit, Währung oder Preistyp geändert | alter Preis → Archiv „Preisänderung“ (mit neuem Preis), neuer Preis aktuell |
| Schlüssel fehlt, Scope erfolgreich abgerufen | → Archiv „Nicht mehr gelistet“ |
| Scope wird gar nicht mehr abgerufen (Konfiguration geändert) | → Archiv „Nicht mehr im Abrufumfang“ |
| Scope-Abruf fehlgeschlagen | Bestand dieses Scopes bleibt **unangetastet** |
| gleicher Preis, Einheit, Typ und Region; Name oder Schlüssel ähnlich (eindeutig 1:1) | **Umbenennung**: kein Archiv, Verlauf und Szenarien wandern mit |
| nur Beschreibung geändert | Aktualisierung ohne Archiv |

**Scopes:** Ein Scope ist die kleinste Abrufeinheit eines Anbieters, zum Beispiel `AmazonS3/eu-central-1`,
`Virtual Machines/westeurope/Consumption` oder `ecs/eu-de`. Schlägt ein Scope fehl, betrifft das nur ihn.

**Freigabe-Modus** (`[general] freigabe`):

- `immer` (Standard): Jeder Abruf mit Änderungen wartet auf Freigabe.
- `bei_warnung`: Abrufe werden automatisch übernommen, außer eine Schutzregel ist verletzt.

Der erste Abruf eines Anbieters und Abrufe ohne Änderungen werden immer direkt übernommen. Eine neue Vorschau
ersetzt eine ältere, noch offene Vorschau desselben Anbieters.

## Schutzregeln

Ist eine dieser Regeln verletzt, **wartet der Abruf immer auf manuelle Freigabe**. Dort lässt er sich mit
„Trotzdem übernehmen“ übernehmen oder verwerfen:

1. Es kommen weniger als `min_ratio` (50 %) der bisherigen Preise zurück.
2. Mehr als `max_removed_ratio` (20 %) der bisherigen Preise würden entfallen (ab 10 Preisen).
3. Ein Scope mit mindestens `min_scope_size` (3) Preisen liefert plötzlich keine mehr.
4. Bei bestehenden Preisen wechselt die Währung (z. B. wenn beim Paging Parameter verloren gehen).

Ein Abruf ganz ohne Preise wird als Fehler abgebrochen.

## Normalisierung

Die Originalwerte bleiben maßgeblich für Abgleich und Archiv. Für Vergleiche kommen diese Felder hinzu:

- `price_eur`: Der Preis in EUR. Grundlage ist der EZB-Referenzkurs, der höchstens einmal täglich beim Abruf
  geladen wird. Ist die EZB nicht erreichbar, gilt `fallback_kurse`.
- `unit_norm` und `price_norm`: Die normierte Einheit und der Preis in EUR je normierter Einheit.
  - Stunde → **Monat** (× 730 h)
  - „pro 30 Tage“ → **Monat** (× 730/720)
  - GB-Stunde und GB-Monat → **GB-Monat**
  - Datenübertragung → **GB**
  - Token → **1 Mio. Token**
  - Anfragen → **1.000 Anfragen**
  - Mengenangaben wie „100 Hours“ oder „10 GB/Month“ werden herausgerechnet.
- `vcpu`, `ram_gb`, `os`: die Merkmale von Compute-Flavors.
  - AWS, STACKIT und OTC liefern sie aus Attributen bzw. dem Flavor-Namen.
  - Bei Azure werden sie für die Serien D, E und F ab v4 aus dem Namen abgeleitet (4, 8 bzw. 2 GB je vCPU).

**Preistypen** sind über alle Anbieter einheitlich:

- On-Demand
- Spot
- Reserviert 1 J. und Reserviert 3 J.
- Savings Plan 1 J. und Savings Plan 3 J.
- Dev/Test
- Sonstiges

## VM-Vergleich

Der Vergleich sucht je Anbieter die günstigsten **On-Demand**-Optionen, die mindestens die gewünschte
vCPU-Zahl und RAM-Größe haben, gefiltert nach Betriebssystem. Ergebnis ist der Preis in €/Monat bei
730 Betriebsstunden.

Anbieter mit **Komponentenpreisen** werden zusammengesetzt: Google (Core und RAM je Familie und Region) und
IONOS (vCPU bzw. Dedicated Core und RAM). Dabei gilt: Preis = vCPU × Core-Preis + RAM × GB-Preis.

## Szenarien

Ein Szenario ist ein Warenkorb aus konkreten Preisen mit Mengen, gruppiert in Varianten (A, B, …).

- **Berechnung:** Monatskosten = Menge × `price_norm`. Je nach Einheit sind das zum Beispiel VMs, GB-Monate
  oder GB Traffic.
- **Aktualität:** Es zählt immer der aktuelle Bestand.
- **Entfallene Preise:** Ist ein Preis entfallen, nimmt das Szenario den letzten archivierten Preis und
  markiert die Position.

## Sicherheit

- **Anmeldung:** HTTP Basic Auth (`PV_USER`/`PV_PASSWORD`) mit Vergleich in konstanter Zeit. Nach 10
  Fehlversuchen innerhalb von 5 Minuten wird die IP gesperrt.
- **CSRF:** Formulare tragen einen Token. Das funktioniert auch hinter Reverse-Proxys.
- **Ausgabe und Datenbank:** Alle Ausgaben werden escaped. SQL läuft parametrisiert, Sortierfelder sind über
  eine Whitelist festgelegt.
- **CSV-Export:** Zellen mit `= + - @` am Anfang werden entschärft.
- **API-Key:** Der Google-Key steht im Header und nicht in der URL. In Fehlermeldungen werden Keys entfernt.
- **Healthcheck:** `/health` ist ohne Anmeldung erreichbar und enthält keine Daten.
