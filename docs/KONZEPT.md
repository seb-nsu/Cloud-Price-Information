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
  fx.py        EZB-Referenzkurse (täglich, Fallback aus config.toml) und Kursverlauf
  analysis.py  VM-Vergleich, Szenarien mit Hochrechnung, Preisverlauf
  compare.py   Vergleiche für Speicher, Traffic, Kubernetes, Datenbanken; Commitments vs. On-Demand
  sovereignty.py  Datenhoheit: Region -> Land, Anbieter -> Rechtsraum
  watch.py     Formatwächter (Formatprofil je Abruf)
  report.py    Preisänderungs-Bericht je Monat/Quartal/Jahr
  azure_sizes.py  vCPU/RAM/GPU für Azure-Größen außerhalb der D/E/F-Regel
  api.py       lesende JSON-API (/api/…)
  xlsx.py/pdf.py  Excel- und PDF-Erzeugung ohne Fremdpakete
  i18n.py/i18n_en.py  Oberfläche Deutsch/Englisch
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
    Für B, M, L, N, H, ältere Serien und GPU-Größen gibt es eine Größentabelle (`app/azure_sizes.py`).
  - Hetzner und Scaleway liefern vCPU und RAM direkt über die API.
- `gpu`: Anzahl GPUs, wo erkennbar (Azure N-Serie, AWS, Scaleway, OTC). Der VM-Vergleich kann danach filtern.

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

## Weitere Vergleiche

Unter „Vergleich“ gibt es neben dem VM-Vergleich eigene Seiten:

- **Speicher** in €/GB-Monat, getrennt nach Block, Objekt, Datei und Backup sowie nach Klasse (Standard, Kalt, Archiv …).
- **Traffic** in €/GB, getrennt nach Richtung (ausgehend, eingehend, zwischen Regionen). Freikontingente und Staffeln werden angezeigt.
- **Managed Kubernetes**: Management-Gebühr je Cluster und Monat.
- **Datenbanken** nach Engine (PostgreSQL, MySQL/MariaDB, SQL Server, Redis …) und Größe.

Die Zuordnung erfolgt über Dienst- und Produktnamen. Sie ist eine Orientierung, Ausreißer bitte am Original prüfen.

## Reservierung, Commitments und Savings Plans

Die Seite „Rabatte“ stellt jedem Reservierungs-, Commitment- oder Savings-Plan-Preis den On-Demand-Preis
desselben Produkts in derselben Region gegenüber.

- **Effektiver Preis:** €/Monat einschließlich anteiliger Vorauszahlung über die Laufzeit.
- **Ersparnis in %** gegenüber On-Demand bei Dauerbetrieb.
- **Break-even:** der Anteil der Laufzeit, ab dem sich die Bindung lohnt (effektiver Preis / On-Demand-Preis).
  Wer eine VM weniger als diesen Anteil der Zeit braucht, fährt mit On-Demand günstiger.
- **Amortisation:** bei Vorauszahlung die Zahl der Monate, nach der die Vorauszahlung durch die Ersparnis ausgeglichen ist.

## Datenhoheit

Jeder Preis wird über Anbieter und Region einer Stufe zugeordnet:

| Stufe | Bedeutung |
|---|---|
| EU-Anbieter, EU-Region | Sitz der Konzernmutter und Datenstandort in der EU (z. B. STACKIT, IONOS, OTC, Hetzner, Scaleway, OVHcloud) |
| EU-Region, US-Anbieter | Daten in der EU, der Anbieter unterliegt aber auch US-Recht (CLOUD Act): Azure, AWS, Google |
| außerhalb der EU | Region z. B. in der Schweiz, im Vereinigten Königreich oder in den USA |
| unbekannt | Region lässt sich keinem Land zuordnen, z. B. „global“ |

Preisliste, Vergleiche, Rabatte und API lassen sich danach filtern. Die Zuordnung ist eine Orientierung und keine
Rechtsberatung. Ergänzungen sind in `config.toml` unter `[datenhoheit]` möglich.

## Formatwächter

Zu jedem Abruf speichert die App ein **Formatprofil**: Anzahl je Bereich und Kategorie, Anteil der Datensätze
je Attributfeld, Anteil normierbarer Einheiten und erkannter VM-Größen, Einheiten und Währungen. Vor der
Übernahme wird es mit dem Profil des letzten übernommenen Abrufs verglichen. Auffällig ist:

- ein Feld, das bisher fast immer vorkam, fehlt jetzt weitgehend („Spalte fehlt“);
- die Preisanzahl eines Bereichs oder einer Kategorie bricht um mehr als `formatwaechter_einbruch` ein;
- deutlich weniger Einheiten lassen sich normieren;
- viele unbekannte Einheiten oder eine neue Währung tauchen auf.

Mit `formatwaechter = "freigabe"` (Standard) landet ein solcher Abruf in der Vorschau, und es geht eine
Benachrichtigung raus. Die Meldungen stehen im Abrufprotokoll und unter `/api/anbieter`. Das ist besonders für
IONOS (Dokumentationsseite) und OTC (knapp dokumentierte API) gedacht.

## Preisänderungs-Bericht

Der Bericht fasst die übernommenen Änderungen eines Monats, Quartals oder Jahres zusammen: Top-Erhöhungen und
Top-Senkungen (in % und €/Monat), neue und entfallene Preise sowie Kennzahlen je Anbieter. Grundlage sind die
Änderungen übernommener Abrufe, eingeordnet nach dem Zeitpunkt der Übernahme. Download als **CSV**, **Excel**
oder **PDF**.

## Szenarien

Ein Szenario ist ein Warenkorb aus konkreten Preisen mit Mengen, gruppiert in Varianten (A, B, …).

- **Berechnung:** Monatskosten = Menge × `price_norm`. Je nach Einheit sind das zum Beispiel VMs, GB-Monate
  oder GB Traffic.
- **Aktualität:** Es zählt immer der aktuelle Bestand.
- **Entfallene Preise:** Ist ein Preis entfallen, nimmt das Szenario den letzten archivierten Preis und
  markiert die Position.
- **Hochrechnung:** Über n Monate (bis 120) mit monatlichem Wachstum in % (Zinseszins), als Standard je Szenario
  oder je Position. Mengen in Stück (VMs, Cluster) werden aufgerundet, GB und Token bleiben anteilig.
  Die Hochrechnung lässt sich als Excel-Datei herunterladen.

## Währungen

Grundlage bleibt EUR. Zusätzliche Anzeigewährungen (`[waehrung] anzeige`, Standard USD) erscheinen in Preisliste,
Vergleichen und Szenarien. Die App speichert zusätzlich den **EZB-Kursverlauf** (automatisch 90 Tage,
vollständig mit `python3 -m app kurse --verlauf`). Der Preisverlauf zeigt bei Fremdwährungen den EUR-Wert zum
damaligen und zum heutigen Kurs. So ist erkennbar, ob sich der Preis oder nur der Kurs geändert hat. Die Seite
„Kurse“ zeigt den Verlauf als Diagramm.

## Sprache

Die Oberfläche gibt es auf Deutsch und Englisch (Umschalter oben rechts, Cookie `pv_lang`). Voreinstellung über
`[general] sprache` oder `PV_LANG`. Die deutschen Texte dienen als Schlüssel, die englischen stehen in
`app/i18n_en.py`. Fehlt eine Übersetzung, erscheint der deutsche Text. Dokumentation, API-Feldnamen und
Exporte bleiben deutsch.

## Sicherheit

- **Anmeldung:** HTTP Basic Auth (`PV_USER`/`PV_PASSWORD`) mit Vergleich in konstanter Zeit. Nach 10
  Fehlversuchen innerhalb von 5 Minuten wird die IP gesperrt.
- **CSRF:** Formulare tragen einen Token. Das funktioniert auch hinter Reverse-Proxys.
- **Ausgabe und Datenbank:** Alle Ausgaben werden escaped. SQL läuft parametrisiert, Sortierfelder sind über
  eine Whitelist festgelegt.
- **CSV-Export:** Zellen mit `= + - @` am Anfang werden entschärft.
- **API-Key:** Der Google-Key steht im Header und nicht in der URL. In Fehlermeldungen werden Keys entfernt.
- **Healthcheck:** `/health` ist ohne Anmeldung erreichbar und enthält keine Daten.
