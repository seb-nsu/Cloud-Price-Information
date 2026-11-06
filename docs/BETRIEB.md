# Betrieb

## Installation (Docker)

```bash
git clone https://github.com/seb-nsu/price_management.git && cd price_management
cp .env.example .env            # mindestens PV_USER / PV_PASSWORD setzen
docker compose up -d --build
docker compose logs -f
```

- Die Datenbank liegt im **Named Volume** `preisdaten` unter `/data/preise.db`. Docker übernimmt beim ersten
  Start die Rechte des App-Benutzers (`uid 10001`).
- Wer ein Host-Verzeichnis statt des Volumes verwenden will, muss vorher `mkdir -p data && sudo chown 10001 data`
  ausführen und in `docker-compose.yml` `./data:/data` eintragen.
- `config.toml` wird schreibgeschützt eingebunden. Änderungen wirken ab dem nächsten Abruf, ein Neustart ist nicht nötig.

## Umgebungsvariablen

| Variable | Zweck |
|---|---|
| `PV_USER`, `PV_PASSWORD` | Basic Auth für die Oberfläche (empfohlen) |
| `PV_SECRET` | fester Schlüssel für Formular-Token (sonst nach Neustart neu → offene Formulare neu laden) |
| `GCP_API_KEY` | Google Cloud Billing API |
| `PV_BASE_PATH` | Pfad hinter einem Reverse-Proxy, z. B. `/preise/` |
| `PV_BASE_URL` | öffentliche URL für Links in Benachrichtigungen |
| `PV_WEBHOOK_URL` | Webhook für Benachrichtigungen |
| `PV_SMTP_HOST`, `PV_SMTP_PORT`, `PV_SMTP_USER`, `PV_SMTP_PASSWORD`, `PV_MAIL_FROM`, `PV_MAIL_TO` | E-Mail-Benachrichtigung |
| `PV_DB`, `PV_CONFIG`, `PV_HOST`, `PV_PORT` | Pfade, Bindung und Port |

## Netzwerkfreigaben (ausgehend, HTTPS)

Der Server muss diese Hosts per HTTPS erreichen können:

- `pim.api.stackit.cloud`
- `prices.azure.com`
- `pricing.us-east-1.amazonaws.com`
- `cloudbilling.googleapis.com`
- `docs.ionos.com`
- `calculator.otc-service.com`
- `www.ecb.europa.eu`

## Reverse-Proxy

Die App unterstützt kein TLS, das übernimmt ein vorgeschalteter Proxy. Beispiel für nginx:

```nginx
location /preise/ {
    proxy_pass http://127.0.0.1:8080/preise/;
    proxy_set_header Host $host;
    proxy_read_timeout 120s;
}
```

Dazu `PV_BASE_PATH=/preise/` setzen. Der CSRF-Schutz arbeitet mit Token und funktioniert deshalb auch dann,
wenn der Proxy den Host-Header umschreibt.

## Abläufe

- **Abruf:** Über die Oberfläche je Anbieter oder für alle („Alle Anbieter aktualisieren“), alternativ per
  `python -m app refresh …`. Abrufe aus der Oberfläche laufen in eigenen Prozessen.
- **Sperre:** Pro Anbieter läuft immer nur ein Abruf, auch zwischen Oberfläche und Kommandozeile. Ein Abruf
  ohne Lebenszeichen für 15 Minuten gilt als abgebrochen.
- **Freigabe:** Die Startseite zeigt „Vorschau prüfen und freigeben“. Die Abruf-Seite listet alle geplanten
  Änderungen mit Δ % und bietet „Änderungen übernehmen“ oder „Verwerfen“. Per Kommandozeile geht das mit
  `python -m app freigeben <id>` bzw. `verwerfen <id>`.
- **Zeitplan (optional):** In `config.toml` unter `[zeitplan]` `aktiv = true` und `uhrzeit = "03:00"` setzen.
  Die Ergebnisse durchlaufen dieselbe Freigabe wie manuelle Abrufe.
- **Benachrichtigung (optional):** In `config.toml` unter `[benachrichtigung]` `aktiv = true` setzen und
  `PV_WEBHOOK_URL` und/oder die SMTP-Variablen angeben. Gemeldet werden übernommene Änderungen ab
  `min_aenderung_prozent`.

## Sicherung

Die Datenbank läuft im WAL-Modus. Eine einfache Dateikopie von `preise.db` kann deshalb unvollständig sein.
Für konsistente Sicherungen eine dieser Varianten verwenden:

```bash
docker exec preisverwaltung python -m app backup /data/backup/preise-$(date +%F).db
# oder auf dem Host mit sqlite3:
sqlite3 preise.db ".backup '/backup/preise.db'"
```

Die Archiv-Aufbewahrung (`archiv_aufbewahrung_monate`, Standard 36) exportiert ältere Archiveinträge vor dem
Löschen nach `/data/archiv-export/*.csv.gz`. Diese Dateien sollten mitgesichert werden.

## Monitoring

| Endpunkt | Inhalt |
|---|---|
| `GET /health` | `ok`, ohne Anmeldung, für Docker und Load-Balancer |
| `GET /health/details` | JSON je Anbieter: Anzahl Preise, letzter erfolgreicher Abruf, `stale` (älter als `warnung_nach_tagen`), offene Freigabe, letzter Status. Gesamtstatus `warnung`, wenn ein Anbieter veraltet ist oder zuletzt fehlschlug |

Die Logs gehen nach stdout (`docker compose logs`).

## Go-Live-Test

Die Anbieter-Schnittstellen waren bei der Entwicklung nicht erreichbar. Vor dem Produktivbetrieb deshalb einmal
diesen Test durchgehen:

1. Container starten und `/health/details` aufrufen.
2. Je Anbieter einzeln „Aktualisieren“ klicken und danach im **Abrufprotokoll** prüfen:
   - Ist der Status „übernommen“?
   - Ist die Anzahl plausibel (siehe Tabelle unten)?
   - Gibt es Hinweise oder Warnungen?
3. Stichproben ziehen: Je Anbieter 3 bis 5 Preise mit der Preisseite bzw. dem Preisrechner des Anbieters vergleichen.
4. Bei **OTC** und **IONOS** besonders auf diese Punkte achten:
   - verworfene Datensätze
   - „mehrdeutiges Zahlformat“
   - leere Dienste
5. Im **VM-Vergleich** eine bekannte Größe prüfen, z. B. 2 vCPU / 8 GB, und die Plausibilität der €/Monat-Werte kontrollieren.
6. Einen Anbieter erneut abrufen. Erwartet wird „keine Änderungen“ und eine direkte Übernahme.

Richtwerte für die Anzahl Preise mit der Standardkonfiguration (grobe Erwartung, kann abweichen):

| Anbieter | Erwartung |
|---|---|
| STACKIT | ca. 800–1.000 |
| IONOS | ca. 150–300 |
| OTC | einige hundert bis wenige tausend |
| Azure | mehrere tausend bis zehntausende |
| AWS | mehrere tausend |
| Google | einige tausend |

Wenn ein Modul scheitert, die Meldung aus dem Abrufprotokoll und gegebenenfalls einen Auszug der Rohantwort
sichern. Damit lässt sich das Modul gezielt anpassen.
