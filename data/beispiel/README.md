# Beispieldaten

> **Nicht aktuell, nicht für Preisentscheidungen verwenden.**

Die Dateien wurden mit `python3 scripts/beispieldaten.py data/beispiel` erzeugt, ohne Zugriff auf die
Anbieter-Schnittstellen. Sie dienen zum Ausprobieren der Oberfläche und als Referenz für das Datenformat.

| Datei | Inhalt |
|---|---|
| `preise_beispiel.db` | SQLite-Datenbank mit mehreren simulierten Abrufen je Anbieter, einer offenen Vorschau, Archiv und Normierung |
| `preise_aktuell.csv` | Export der aktuellen Preise (Semikolon, Dezimalkomma, UTF-8 mit BOM) |
| `preise_archiv.csv` | Export des Archivs |
| `abrufprotokoll.csv` | Export der Abrufe |

Quellen:

- **STACKIT:** echter Ausschnitt der öffentlichen PIM-API (Stand 09.06.2026, 133 von 964 SKUs).
- **IONOS:** nachgebauter Auszug der Preisliste der IONOS CLOUD GmbH (Struktur und Beispielwerte).
- **Azure, AWS, Hetzner, Scaleway, OVHcloud:** synthetische Beispielantworten aus `tests/fixtures/`, nach der
  jeweiligen API-Dokumentation aufgebaut. Die Werte sind Beispielwerte, keine echten Preise.
- **Szenario:** ein Beispiel-Warenkorb mit drei Varianten (Azure, Scaleway, Hetzner) und Wachstum für die Hochrechnung.
- **Bericht:** Spätere Abrufe enthalten simulierte Preisänderungen im Vormonat und im laufenden Monat, damit der
  Preisänderungs-Bericht etwas zeigt.
- **Preisänderungen:** zu Demonstrationszwecken simuliert.
- **Wechselkurs:** fester Beispielwert (1 € = 1,085 USD) und ein simulierter Kursverlauf.

Ansehen:

```bash
PV_DB=data/beispiel/preise_beispiel.db python3 -m app serve
```

Echte, aktuelle Daten entstehen erst im Betrieb in `data/preise.db` bzw. im Docker-Volume. Diese Daten werden
bewusst nicht versioniert.
