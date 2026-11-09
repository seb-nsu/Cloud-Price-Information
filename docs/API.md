# JSON-API (nur lesend)

Die API liegt unter `/api/…`. Es gilt derselbe Zugangsschutz wie für die Oberfläche (Basic Auth aus `PV_USER`
und `PV_PASSWORD`). Alle Endpunkte sind nur lesend (`GET`) und liefern JSON in UTF-8.

`GET /api` liefert die Version, alle Endpunkte mit Kurzbeschreibung, die Kategorien, die Preistypen und die
Anbieter.

## Felder eines Preises

| Feld | Bedeutung |
|---|---|
| `provider`, `key` | Anbieter und stabiler Schlüssel; beides zusammen identifiziert einen Preis |
| `category`, `service`, `product`, `region` | Einordnung |
| `price`, `currency`, `unit` | Preis in Originalwährung und -einheit |
| `price_type` | einheitlicher Preistyp (On-Demand, Spot, Reservierung 1 J. …) |
| `price_eur`, `price_norm`, `unit_norm` | in EUR umgerechnet bzw. auf €/Monat, €/GB-Monat usw. normiert |
| `vcpu`, `ram_gb`, `os`, `gpu` | VM-Merkmale, sofern ableitbar |
| `valid_from`, `last_checked`, `source_date` | Gültig seit, zuletzt bestätigt, Stand laut Anbieter |

## Endpunkte

| Endpunkt | Parameter | Inhalt |
|---|---|---|
| `/api/anbieter` | – | Anbieter mit Status, Anzahl Preisen, letztem Abruf, Formatwarnungen und Rechtsraum |
| `/api/preise` | `provider`, `category`, `region`, `price_type`, `unit_norm`, `q`, `key`, `souv`, `limit` (max. 5000, Standard 500), `offset`, `attributes=1` | aktuelle Preise: `{total, limit, offset, items}` |
| `/api/archiv` | wie `/api/preise`, zusätzlich `run_id` | archivierte Preise mit `valid_to`, `reason`, `new_price` |
| `/api/verlauf` | `provider`, `key` | Preisverlauf; je Punkt auch `price_eur_damals` (EUR zum Kurs am Gültigkeitsbeginn) |
| `/api/vergleich` | `vcpu`, `ram`, `os`, `region`, `top`, `gpu`, `souv` | VM-Vergleich, günstigste Optionen je Anbieter in €/Monat |
| `/api/vergleich/<art>` | `art` = `speicher`, `traffic`, `kubernetes`, `datenbanken`; Filter `provider`, `typ`, `klasse`, `engine`, `region`, `q`, `top`, `souv`, `stufe`, `kostenlos`, `vcpu`, `ram` | Kategorie-Vergleich |
| `/api/rabatte` | `provider`, `category`, `price_type`, `souv`, `min`, `q` | Reservierung, Commitments und Savings Plans gegenüber On-Demand mit Ersparnis in % und Break-even |
| `/api/bericht` | `zeitraum` (`2026-10`, `2026-Q4`, `2026`), `provider`, `category`, `top` | Preisänderungs-Bericht mit Top-Erhöhungen und -Senkungen |
| `/api/szenarien` | – | Liste der Szenarien |
| `/api/szenario/<id>` | `monate`, `wachstum` | Szenario mit Positionen, Summen je Variante und Hochrechnung |
| `/api/kurse` | `waehrung`, `seit` (JJJJ-MM-TT) | aktuelle Wechselkurse und Kursverlauf |
| `/api/status` | – | laufende Abrufe je Anbieter |

`souv` filtert nach Datenhoheit: `eu` (nur EU-Regionen) oder `eu_souveraen` (EU-Anbieter in EU-Regionen).
Siehe [KONZEPT.md](KONZEPT.md#datenhoheit).

## Beispiele

```bash
curl -su admin:passwort 'https://preise.example.com/api/preise?provider=stackit&category=Compute&limit=50'
curl -su admin:passwort 'https://preise.example.com/api/vergleich?vcpu=4&ram=16&souv=eu_souveraen'
curl -su admin:passwort 'https://preise.example.com/api/bericht?zeitraum=2026-Q4&top=20'
```

Fehler liefern einen passenden HTTP-Status mit `{"error": "…"}`, zum Beispiel 404 für einen unbekannten Endpunkt.
