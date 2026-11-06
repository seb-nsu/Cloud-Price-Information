# Changelog

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
