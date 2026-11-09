# Unabhängiges Review (Claude Fable) und Umsetzungsstand

Das Review stammt vom 06.11.2026 und bezieht sich auf Version 1.0.0.

**Gesamturteil des Reviews:** Die Lösung ist sinnvoll und handwerklich sauber, aber noch nicht betriebsreif.
Ausschlaggebend waren zwei Probleme im Docker-Setup, Fehlarchivierungen bei Teilausfällen und Schlüsselwechseln
sowie fehlende Vergleichbarkeit zwischen Anbietern.

Version 2.0.0 setzt alle Befunde um. Die Spalte „Umsetzung“ verweist auf die betroffenen Stellen, die Tests
liegen in `tests/`.

## Kritisch

| # | Befund | Umsetzung |
|---|---|---|
| K1 | Docker: Datenverzeichnis (Bind-Mount, root) für den App-Benutzer nicht beschreibbar | Named Volume `preisdaten` in `docker-compose.yml`; Alternative in BETRIEB.md |
| K2 | Healthcheck scheitert bei aktivem Login (401) | `/health` wird vor der Anmeldung beantwortet (`web.py`); Test `test_auth_and_health` |
| K3 | 50-%-Schutz greift nicht bei Teilausfällen und Schlüsselwechseln, deshalb Massen-Fehlarchivierung | Scopes je Abrufeinheit; fehlgeschlagene Scopes bleiben unangetastet; neue Schutzregeln (Entfall > 20 %, leerer Scope, Währungswechsel); Vorschau mit manueller Freigabe; Umbenennungserkennung (`sync.py`); Tests `test_partial_failure_keeps_failed_scope`, `test_empty_scope_is_violation`, `test_guard_removed_ratio`, `test_rename_detection` |

## Hoch

| # | Befund | Umsetzung |
|---|---|---|
| H1 | IONOS: Schlüssel hängen am Wortlaut und an der Position | Normalisierte Schlüssel (letzte 2 Ebenen, ohne Satz- und Leerzeichen) plus Umbenennungserkennung im Abgleich; Test `test_keys_stable_under_cosmetic_heading_change` |
| H2 | IONOS: HTML-Tabellen werden verworfen | `html_tables_to_markdown` mit `html.parser`; Test `test_html_tables` |
| H3 | IONOS: Zahlenformat mehrdeutig („2.500 €“) | Dokumentweites Dezimaltrennzeichen; mehrdeutige Werte markiert und gemeldet; Vergleich mit dem Tabellenmedian; Test `test_ambiguous_warning` |
| H4 | Azure: Spot und Low Priority als On-Demand geführt | `price_type_of()` wertet SKU- und Meter-Namen aus; einheitliche Preistypen; Test `test_spot_and_savings_plan` |
| H5 | Keine Normalisierung, deshalb kein Anbietervergleich | `normalize.py` + `fx.py` (EZB): `price_eur`, `unit_norm`, `price_norm`, vCPU/RAM/BS; Spalte „Normiert (EUR)“, VM-Vergleich, Szenarien |
| H6 | OTC: Dezimalkomma, feste Seitengröße, stille Verluste | Englischer Endpunkt, `parse_amount()`, Paging ohne feste Seitengröße (leere oder wiederholte Seite, Gesamtzahl), Warnung bei verworfenen Datensätzen; Tests in `OtcTests` |

## Mittel

| # | Befund | Umsetzung |
|---|---|---|
| M1 | Azure: `NextPageLink` blind gefolgt, Währung kann wechseln | Folge-URL selbst gebaut (`$skip` + eigene Parameter); Prüfung der Währung je Position; Test `test_paging_builds_own_url`, `test_currency_mismatch_fails_scope` |
| M2 | AWS: CPU-Last, kein gzip, falscher Spaltenname, Bare Metal fehlt | `csv.reader` mit Spaltenindex und Vorfilter auf `TermType`; gzip (`open_stream`); Spalten-Aliase; Bare Metal ergänzt; Abruf im eigenen Prozess; Tests `test_gzip_stream`, `test_parse_and_filter` |
| M3 | Job: TOML-Fehler bzw. DB-Timeout lassen den Lauf hängen | Konfiguration im `try`-Block; `_finish()` mit Wiederholung; Heartbeat; Test `test_config_error_is_logged` |
| M4 | Sperre nur im Prozess, CLI markiert Web-Läufe als abgebrochen | DB-Sperre mit Heartbeat (`acquire`, `cleanup_stale`) statt Bereinigung beim Start; Test `test_lock_and_stale_cleanup` |
| M5 | Diagnose: Duplikate und `n_fetched` gehen verloren, keine Änderungsansicht pro Lauf | `n_dupes`, `n_renamed`, Warnungen und Verstöße in `fetch_runs`; Tabelle `run_changes`; Seite `/lauf/<id>`; Archiv-Filter `run_id` |
| M6 | Web: `?seite=abc` führt zu 500, Origin-Check hinter Proxy, CSV-Formeln, „0,00“ bei Kleinstpreisen, `LIKE` nur für ASCII | `int_param()`; CSRF-Token statt Origin-Check; `_safe_cell()`; `fmt_price()` mit signifikanten Stellen; `pv_lower()` (casefold); Tests in `WebTests` |
| M7 | Backup-Anleitung im WAL-Modus unvollständig | `python -m app backup` (SQLite-Backup-API); BETRIEB.md; Test `test_backup` |

## Niedrig

| # | Befund | Umsetzung |
|---|---|---|
| N1 | Google: API-Key im Query-String; globale Lizenz-SKUs vermischt | Header `x-goog-api-key`; `global_services` (Standard: Networking); Test `test_default_get_sends_header` |
| N2 | Azure-Hinweistext zur Währung | Text präzisiert; laut Microsoft-Dokumentation sind Nicht-USD-Preise Orientierungswerte, deshalb bleibt der Hinweis bestehen |
| N3 | STACKIT: leerer Preis wird zu 0 € | Wird übersprungen und gemeldet; Test `test_empty_price_skipped` |
| N4 | Keine Schema-Versionierung, kein Index `run_id`, keine Aufbewahrung | Migrationen über `PRAGMA user_version` (v1→v2 inkl. Bestandsdaten); Indizes; `retention.py`; Tests `test_v1_database_is_migrated`, `test_retention` |
| N5 | Kein Schutz gegen Brute-Force | Sperre nach 10 Fehlversuchen in 5 Minuten je IP; Sicherheits-Header; Test `test_auth_and_health` |
| N6 | Testlücken | 58 Tests (vorher 18) für Anmeldung, CSRF, Fehlerpfade, Duplikate, OTC-Varianten, IONOS-HTML, Schutzregeln, Migration, Benachrichtigung, Aufbewahrung, Hintergrundprozess |
| N7 | Protokoll ohne Paging | Paging und Filter nach Anbieter |

## Vorgeschlagene Erweiterungen

| Vorschlag | Umsetzung |
|---|---|
| Vorschau vor Übernahme (Staging) | `staging_records`, `/lauf/<id>` mit „Übernehmen“/„Verwerfen“, `freigabe = "immer"` |
| Änderungsansicht pro Abruf | `run_changes`, Reiter neu/geändert/entfallen/umbenannt mit Δ % |
| Normalisierung auf €/Monat, EZB-Kurse | `normalize.py`, `fx.py`, `python -m app kurse` |
| Einheitliche Preistypen | `models.PRICE_TYPES`, alle Module angepasst |
| VM-Vergleichsmatrix | `/vergleich` inkl. Komponentenpreisen (Google, IONOS) |
| Warenkorb bzw. Szenario-Rechner | `/szenarien`, Varianten, CSV-Export |
| Preisverlauf mit Sparkline | `/verlauf` |
| Benachrichtigung bei Preisänderungen | `notify.py` (Webhook/E-Mail, Schwelle, Kategorien) |
| Archiv-Aufbewahrung | `retention.py` mit CSV.gz-Export |
| Abruf im Subprozess, DB-Sperre | `jobs.start_background` (multiprocessing), Heartbeat |
| Optionaler Zeitplan | `[zeitplan]` (standardmäßig aus, Abruf erfolgt manuell) |
| Monitoring | `/health/details` mit `stale` und offener Freigabe |

## Offene Punkte

- **Live-Test** aller Anbieter-Schnittstellen (siehe [BETRIEB.md](BETRIEB.md#go-live-test)). Er war in der
  Entwicklungsumgebung nicht möglich.
- ~~RAM-Ableitung für Azure-Serien außerhalb von D, E und F (z. B. B, M, L, N) sowie GPU-Größen~~: in Version
  2.1.0 über die Größentabelle `app/azure_sizes.py` umgesetzt (Test `AzureSizeTests`).
