# Changelog

## 2.1.0 – 2026-11-09

- **CI:** GitHub Actions mit Tests (Python 3.11 bis 3.13) und Docker-Build samt Smoke-Test bei jedem Push.
- **Formatwächter:** Formatprofil je Abruf; fehlende Felder, eingebrochene Preisanzahl, neue Einheiten oder
  Währungen halten das Ergebnis in der Vorschau und lösen eine Benachrichtigung aus.
- **Azure:** Größentabelle für B, M, L, N, H, ältere Serien und GPU-Größen. Sie erscheinen jetzt im VM-Vergleich.
- **Neue Vergleiche:** Speicher, Traffic, Managed Kubernetes, Datenbanken.
- **Rabatte:** Reservierung, Commitments und Savings Plans gegenüber On-Demand mit Ersparnis, Break-even und
  Amortisation.
- **Datenhoheit:** Kennzeichnung und Filter nach Region und Rechtsraum.
- **Preisänderungs-Bericht** je Monat, Quartal oder Jahr als CSV, Excel oder PDF.
- **Excel-Export** für Preise, Archiv, Szenarien und Rabatte. **Hochrechnung** von Szenarien mit Wachstum.
- **Lesende JSON-API** (`/api/preise`, `/api/vergleich`, …), siehe [docs/API.md](docs/API.md).
- **Neue Anbieter:** Hetzner Cloud (Token nötig), Scaleway, OVHcloud (experimentell, standardmäßig aus).
  plusserver und IONOS bieten keine öffentliche Preis-API, siehe [docs/ANBIETER.md](docs/ANBIETER.md).
- **Währungen:** zusätzliche Anzeigewährungen, EZB-Kursverlauf, Seite „Kurse“, Preisverlauf zum damaligen Kurs.
- **Sprache:** Oberfläche auf Deutsch und Englisch.
- **Datenbank:** Schema-Version 3 (Formatprofile, Kursverlauf, neue Spalten). Bestehende Datenbanken werden
  automatisch migriert.
- **Tests:** 88 statt 58.

## 2.0.0 – 2026-11-06

Diese Version setzt alle Befunde und Vorschläge aus dem Review um (siehe [docs/REVIEW.md](docs/REVIEW.md)).

- **Vorschau und Freigabe:** Abrufergebnisse warten auf Übernahme oder Verwerfen. Neue Seite `/lauf/<id>` mit allen Änderungen.
- **Scopes und Schutzregeln:** Teilausfälle archivieren nichts mehr. Zusätzliche Regeln für Entfall über 20 %,
  leere Bereiche und Währungswechsel.
- **Umbenennungserkennung:** Verlauf und Szenarien bleiben bei geänderten Schlüsseln erhalten.
- **Normalisierung:** EUR über EZB-Kurse, €/Monat, €/GB-Monat und weitere Einheiten; einheitliche Preistypen; vCPU, RAM und BS.
- **Neue Seiten:** VM-Vergleich, Szenarien (Warenkorb mit Varianten), Preisverlauf mit Diagramm.
- **Optionale Dienste:** Benachrichtigung (Webhook/E-Mail), Archiv-Aufbewahrung, täglicher Zeitplan.
- **Anbieter:**
  - IONOS: HTML-Tabellen, stabile Schlüssel, mehrdeutige Zahlen
  - Azure: Spot, eigenes Paging, Währungsprüfung, Savings Plan
  - AWS: gzip, schnellere CSV-Verarbeitung, Bare Metal
  - OTC: robustes Paging, Dezimalkomma
  - Google: API-Key im Header
  - STACKIT: leere Preise werden übersprungen
- **Betrieb:**
  - Named Volume und Healthcheck ohne Anmeldung
  - DB-Sperre mit Heartbeat, Abrufe in eigenen Prozessen
  - Backup-Befehl, `/health/details`
  - CSRF-Token, Brute-Force-Schutz, Schutz vor CSV-Formeln
- **Datenbank:** Migrationen über `PRAGMA user_version`. Datenbanken aus Version 1 werden automatisch übernommen.
- **Tests und Dokumentation:** 58 Tests statt 18; Dokumentation in `docs/`; Beispieldaten in `data/beispiel/`.

## 1.0.0 – 2026-11-06

- Erste Version: Abruf von sechs Anbietern, aktuelle Preise und Archiv, Abrufprotokoll, CSV-Export, Docker.
