"""Englische Texte der Oberfläche (Schlüssel = deutscher Originaltext, siehe i18n.py)."""

EN: dict[str, str] = {
    # Navigation, Rahmen
    "Preisverwaltung": "Price Management", "Aktuelle Preise": "Current prices", "Vergleich": "Comparison",
    "Szenarien": "Scenarios", "Bericht": "Report", "Archiv": "Archive", "Abrufprotokoll": "Fetch log",
    "Sprache wechseln": "Switch language", "zurück": "back", "weiter": "next", "Seite {page} von {pages}": "Page {page} of {pages}",
    "Nicht gefunden": "Not found", "Seite nicht gefunden.": "Page not found.", "Fehler": "Error",
    "Interner Fehler – siehe Server-Log.": "Internal error – see server log.",
    "Formular abgelaufen": "Form expired",
    "Das Formular ist abgelaufen (z. B. nach einem Neustart). Bitte Seite neu laden und erneut absenden.":
        "The form has expired (e.g. after a restart). Please reload the page and submit again.",
    # Aktuelle Preise
    "Abruf gestartet – die Seite aktualisiert sich, sobald er fertig ist.":
        "Fetch started – the page refreshes once it has finished.",
    "Für diese Anbieter läuft bereits ein Abruf.": "A fetch is already running for these providers.",
    "Wechselkurse aktualisiert.": "Exchange rates updated.",
    "EZB-Referenzkurs {d}: 1 € = {r} USD": "ECB reference rate {d}: 1 € = {r} USD",
    "Noch keine EZB-Kurse geladen – USD-Preise ohne EUR-Umrechnung": "No ECB rates loaded yet – USD prices not converted to EUR",
    "Abruf läuft:": "Fetch running:", "Alle Anbieter aktualisieren": "Refresh all providers",
    "noch nie abgerufen": "never fetched", "Vorschau prüfen und freigeben": "Review and approve preview",
    "+{n} neu · {c} geändert · {r} entfallen": "+{n} new · {c} changed · {r} removed",
    "Formatwächter: {n} Auffälligkeit(en)": "Format watch: {n} anomaly(ies)",
    "Preise aktuell · Stand": "current prices · as of", "Deaktiviert": "Disabled", "Aktualisieren": "Refresh",
    "Keine Preise vorhanden. Mit „Aktualisieren“ werden die aktuellen Listenpreise beim Anbieter abgefragt.":
        "No prices yet. “Refresh” fetches the current list prices from the provider.",
    "Keine Treffer für diese Filter.": "No results for these filters.",
    "{n} Preise": "{n} prices", "Anbieter": "Provider", "Kategorie": "Category", "Dienst": "Service",
    "Produkt": "Product", "Region": "Region", "Listenpreis": "List price", "Einheit": "Unit",
    "Normiert (EUR)": "Normalised (EUR)", "Normiert": "Normalised", "normiert": "normalised",
    "Stundenpreise × 730 h; „30 Tage“ × 730/720; USD per EZB-Kurs":
        "Hourly prices × 730 h; “30 days” × 730/720; USD at ECB rate",
    "Typ": "Type", "Gültig seit": "Valid since", "Verlauf": "History", "Zu Szenario hinzufügen": "Add to scenario",
    "Alle": "All", "alle": "all", "Preistyp": "Price type", "Normierte Einheit": "Normalised unit", "Suche": "Search",
    "z. B. D4s, m6i.large, Object": "e.g. D4s, m6i.large, Object", "Filtern": "Filter", "Zurücksetzen": "Reset",
    "CSV-Export": "CSV export", "Excel-Export": "Excel export",
    # Archiv
    "Frühere Preise, die durch eine Preisänderung ersetzt wurden oder nicht mehr gelistet sind. Das Archiv wird nur hier angezeigt.":
        "Earlier prices that were replaced by a price change or are no longer listed. The archive is only shown here.",
    "Gefiltert auf Abruf": "Filtered to fetch", "Keine archivierten Preise für diese Auswahl.": "No archived prices for this selection.",
    "{n} archivierte Preise": "{n} archived prices", "Alter Preis": "Old price", "Neuer Preis": "New price",
    "Gültig von": "Valid from", "Gültig bis": "Valid until", "Grund": "Reason",
    # Protokoll / Lauf
    "Noch keine Abrufe.": "No fetches yet.", "Start": "Start", "Ende": "End", "Status": "Status",
    "Abgerufen": "Fetched", "Neu": "New", "Geändert": "Changed", "Entfallen": "Removed", "Umbenannt": "Renamed",
    "Doppelt": "Duplicates", "Meldung": "Message", "Formatwächter": "Format watch",
    "Abruf #{id} · {p}": "Fetch #{id} · {p}", "Gestartet": "Started", "beendet": "finished", "Quelle": "Source",
    "Stand laut Anbieter": "Provider date", "entschieden": "decided", "Schutzregel verletzt:": "Safety rule violated:",
    "Formatwächter: Das Antwortformat des Anbieters hat sich auffällig verändert.":
        "Format watch: the provider’s response format has changed noticeably.",
    "Bitte die Änderungen unten prüfen. Ursache kann eine Formatänderung beim Anbieter sein, die das Abruf-Modul nicht mehr richtig liest.":
        "Please review the changes below. The cause may be a format change at the provider that the fetch module no longer reads correctly.",
    "{n} Hinweise zum Abruf": "{n} notes on this fetch", "Trotzdem übernehmen": "Apply anyway",
    "Änderungen übernehmen": "Apply changes", "Verwerfen": "Discard",
    "Bis zur Freigabe bleibt der aktuelle Bestand unverändert.": "The current data stays unchanged until approval.",
    "Alle ({n})": "All ({n})", "im Archiv ansehen": "view in archive", "Keine Änderungen in diesem Abruf.": "No changes in this fetch.",
    "Keine Einträge.": "No entries.", "Art": "Kind", "Alt": "Old", "Hinweis": "Note", "vorher": "previously",
    # Verlauf
    "Preisverlauf": "Price history", "Kein Preis mit diesem Schlüssel gefunden.": "No price found for this key.",
    "Schlüssel": "Key", "Preis": "Price", "EUR (Kurs damals)": "EUR (rate then)", "EUR (Kurs heute)": "EUR (rate today)",
    "Kurs damals": "rate then", "aktuell": "current",
    "Preis in {c}. „Kurs damals“ rechnet mit dem EZB-Kurs zu Beginn der Gültigkeit, „Kurs heute“ mit dem aktuellen Kurs. So lässt sich eine Preisänderung des Anbieters von einer Wechselkursänderung unterscheiden.":
        "Price in {c}. “Rate then” uses the ECB rate at the start of validity, “rate today” the current rate. This separates a provider price change from an exchange-rate change.",
    # VM-Vergleich
    "VMs": "VMs",
    "Günstigste On-Demand-Optionen je Anbieter mit mindestens der gewünschten Ausstattung, normiert auf EUR pro Monat (730 h Dauerbetrieb, netto). USD-Preise mit EZB-Referenzkurs {d} umgerechnet. Komponentenpreise (Google, IONOS) werden aus Preis je vCPU und je GB RAM zusammengesetzt. Azure-Ausstattung laut Größentabelle (D, E, F, L, B, M und GPU-Serien N).":
        "Cheapest on-demand options per provider with at least the requested specs, normalised to EUR per month (730 h continuous use, net). USD prices converted at ECB reference rate {d}. Component prices (Google, IONOS) are built from price per vCPU and per GB RAM. Azure specs from the size table (D, E, F, L, B, M and GPU series N).",
    "(noch nicht geladen)": "(not loaded yet)", "RAM (GB)": "RAM (GB)", "Betriebssystem": "Operating system",
    "Windows (inkl. Lizenz)": "Windows (incl. licence)", "ohne GPU": "no GPU", "mind. Teil-GPU": "at least partial GPU",
    "mind. 1 GPU": "at least 1 GPU", "mind. 2 GPUs": "at least 2 GPUs", "mind. 4 GPUs": "at least 4 GPUs",
    "mind. 8 GPUs": "at least 8 GPUs", "Region enthält": "Region contains", "z. B. eu, germany, frankfurt": "e.g. eu, germany, frankfurt",
    "Treffer je Anbieter": "Results per provider", "Vergleichen": "Compare", "Option": "Option", "Datenhoheit": "Data sovereignty",
    "€/Monat": "€/month", "/Monat": "/month", "vs. günstigste": "vs. cheapest", "günstigste": "cheapest",
    "Keine passenden Compute-Preise gefunden. Sind die Anbieter schon abgerufen?":
        "No matching compute prices found. Have the providers been fetched yet?",
    "Komponenten": "Components", "Flavor": "Flavor",
    # Kategorie-Vergleiche
    "€/GB-Monat": "€/GB-month", "€/GB": "€/GB", "Speicherklasse": "Storage class", "Datenbank": "Database",
    "vCPU": "vCPU", "RAM": "RAM", "vCPU mind.": "min. vCPU", "RAM mind. (GB)": "min. RAM (GB)",
    "Staffelpreise": "Tiered prices", "günstigste Stufe": "cheapest tier", "Einstiegsstufe (höchster Preis)": "entry tier (highest price)",
    "inkl. kostenloser Kontingente": "incl. free allowances", "Nur On-Demand-Preise, netto, in EUR normiert.":
        "On-demand prices only, net, normalised to EUR.",
    "{n} Produkte gefunden, je Anbieter die günstigsten.": "{n} products found, cheapest per provider.",
    "Keine passenden Preise gefunden. Sind die Anbieter schon abgerufen?": "No matching prices found. Have the providers been fetched yet?",
    "Speicher": "Storage", "Traffic": "Traffic", "Datenbanken": "Databases",
    "Preise je GB und Monat für gespeicherte Daten (ohne Anfragen, Operationen und Traffic).":
        "Prices per GB and month for stored data (excluding requests, operations and traffic).",
    "Preise je übertragenem GB. Staffelpreise erscheinen je Stufe; kostenlose Kontingente sind ausgeblendet, solange „inkl. kostenloser Kontingente“ nicht gesetzt ist.":
        "Prices per transferred GB. Tiered prices are shown per tier; free allowances are hidden unless “incl. free allowances” is ticked.",
    "Verwaltetes Kubernetes: Gebühren für Cluster bzw. Control Plane je Monat (Worker-Knoten sind normale VMs, siehe VM-Vergleich).":
        "Managed Kubernetes: cluster or control-plane fees per month (worker nodes are regular VMs, see VM comparison).",
    "Verwaltete Datenbanken je Monat (730 h Dauerbetrieb). vCPU/RAM sind angegeben, wo der Anbieter sie liefert.":
        "Managed databases per month (730 h continuous use). vCPU/RAM shown where the provider supplies them.",
    "Snapshot/Backup": "Snapshot/backup", "Objektspeicher": "Object storage", "Dateispeicher": "File storage",
    "Blockspeicher": "Block storage", "Selten genutzt": "Infrequent access", "Standard": "Standard",
    "Zwischen Regionen/Zonen": "Between regions/zones", "Ausgehend ins Internet": "Outbound to internet",
    "Eingehend": "Inbound", "Cluster/Control Plane": "Cluster/control plane",
    # Rabatte
    "Reservierung & Savings Plans": "Reservations & savings plans", "Rabatte": "Discounts",
    "Reservierungen, Commitments und Savings Plans im Vergleich zum On-Demand-Preis desselben Produkts. Der effektive Preis enthält eine Vorauszahlung anteilig über die Laufzeit. Break-even: ab dieser Auslastung (Anteil der Laufzeit, in der die Ressource läuft) ist die Bindung günstiger als On-Demand. Amortisation: Monate, bis sich eine Vorauszahlung bei Dauerbetrieb bezahlt gemacht hat.":
        "Reservations, commitments and savings plans compared with the on-demand price of the same product. The effective price spreads any upfront payment over the term. Break-even: above this utilisation (share of the term the resource runs) the commitment is cheaper than on-demand. Payback: months until an upfront payment pays off at continuous use.",
    "Ersparnis mind. (%)": "Min. savings (%)", "{n} Gegenüberstellungen": "{n} comparisons",
    "On-Demand €/Monat": "On-demand €/month", "Effektiv €/Monat": "Effective €/month", "Vorauszahlung": "Upfront",
    "Ersparnis": "Savings", "Ab dieser Auslastung lohnt sich die Bindung": "Above this utilisation the commitment pays off",
    "Break-even": "Break-even", "Amortisation": "Payback", "sofort": "immediately", "{m} Monate": "{m} months",
    "Ø Ersparnis über {n} Produkte, max. {m}": "avg. savings across {n} products, max. {m}",
    "Laufend €/Monat": "Recurring €/month", "Vorauszahlung €": "Upfront €", "Gesamt über Laufzeit €": "Total over term €",
    "Ersparnis %": "Savings %", "Break-even-Auslastung %": "Break-even utilisation %", "Amortisation (Monate)": "Payback (months)",
    "Schlüssel On-Demand": "On-demand key", "Laufzeit (Monate)": "Term (months)",
    "Keine Reservierungs- oder Savings-Plan-Preise mit passendem On-Demand-Preis gefunden. Diese Preistypen müssen in config.toml aktiviert sein (z. B. Azure price_types = [\"Consumption\", \"Reservation\"], savings_plan = true; AWS term_types = [\"OnDemand\", \"Reserved\"]; Google usage_types mit Commit1Yr/Commit3Yr).":
        "No reservation or savings-plan prices with a matching on-demand price found. These price types must be enabled in config.toml (e.g. Azure price_types = [\"Consumption\", \"Reservation\"], savings_plan = true; AWS term_types = [\"OnDemand\", \"Reserved\"]; Google usage_types with Commit1Yr/Commit3Yr).",
    # Bericht
    "Preisänderungs-Bericht": "Price change report", "Zeitraum": "Period", "Top": "Top", "Anzeigen": "Show",
    "Übernommene Preisänderungen im Zeitraum {a} bis {b} (Zeitpunkt der Übernahme). Erstimporte zählen nicht als neu.":
        "Applied price changes from {a} to {b} (time of approval). Initial imports do not count as new.",
    "Preisänderungen": "Price changes", "neu": "new", "entfallen": "removed", "geändert": "changed", "umbenannt": "renamed",
    "Keine übernommenen Preisänderungen in diesem Zeitraum.": "No applied price changes in this period.",
    "Übersicht je Anbieter": "Summary per provider", "Erhöhungen": "Increases", "Senkungen": "Decreases",
    "Median Δ": "Median Δ", "Größte Erhöhung": "Largest increase", "Top-Erhöhungen": "Top increases",
    "Top-Senkungen": "Top decreases", "Keine.": "None.", "Datum": "Date", "Änderung %": "Change %", "Währung": "Currency",
    "Abruf": "Fetch", "Übersicht": "Summary", "Alle Änderungen": "All changes", "Median Δ %": "Median Δ %",
    "Größte Erhöhung %": "Largest increase %", "alle Anbieter": "all providers",
    "Preisverwaltung · erstellt {d}": "Price Management · created {d}",
    "Zeitraum {a} bis {b} · {s} · {g} Preisänderungen, {n} neu, {r} entfallen. Netto-Listenpreise in Originalwährung; Änderung in Prozent gegenüber dem vorherigen Preis.":
        "Period {a} to {b} · {s} · {g} price changes, {n} new, {r} removed. Net list prices in original currency; change in percent versus the previous price.",
    # Kurse
    "Wechselkurse": "Exchange rates",
    "EZB-Referenzkurse (Einheiten je 1 €). USD-Preise (z. B. AWS) werden mit dem jeweils aktuellen Kurs in EUR umgerechnet; im Preisverlauf zusätzlich mit dem Kurs zum damaligen Zeitpunkt. Weitere Anzeigewährungen: [waehrung] anzeige in config.toml.":
        "ECB reference rates (units per 1 €). USD prices (e.g. AWS) are converted to EUR at the current rate; the price history also shows the rate at the time. Additional display currencies: [waehrung] anzeige in config.toml.",
    "Kursverlauf gespeichert: {a} bis {b} ({n} Handelstage)": "Stored rate history: {a} to {b} ({n} trading days)",
    "Aktuelle Kurse laden": "Load current rates", "Vollständigen Kursverlauf seit 1999 laden": "Load full rate history since 1999",
    "90 Tage": "90 days", "1 Jahr": "1 year", "5 Jahre": "5 years", "je 1 €": "per 1 €", "Aktuelle Kurse": "Current rates",
    "Kurs je 1 €": "Rate per 1 €", "Stand": "Date", "Noch keine EZB-Kurse geladen. Fallback-Kurse": "No ECB rates loaded yet. Fallback rates",
    "Kursverlauf konnte nicht geladen werden: {e}": "Rate history could not be loaded: {e}",
    # Szenarien
    "Szenario": "Scenario", "Variante {v}": "Variant {v}", "Variante": "Variant", "Menge": "Quantity",
    "Ein Szenario ist ein Warenkorb mit Mengen (z. B. 10 VMs, 2.000 GB Storage, 5.000 GB Traffic). Positionen lassen sich in Varianten (A, B, …) gruppieren, um Anbieter zu vergleichen. Preise werden immer aus dem aktuellen Bestand berechnet. Mit Laufzeit und monatlichem Wachstum wird das Szenario hochgerechnet. Positionen fügt man über das ＋ in der Preistabelle oder im Vergleich hinzu.":
        "A scenario is a basket with quantities (e.g. 10 VMs, 2,000 GB storage, 5,000 GB traffic). Items can be grouped into variants (A, B, …) to compare providers. Prices are always calculated from current data. Term and monthly growth project the scenario over time. Add items with ＋ in the price table or comparison.",
    "Name": "Name", "z. B. Plattform 2027": "e.g. Platform 2027", "Notiz": "Note", "Szenario anlegen": "Create scenario",
    "Positionen": "Items", "Laufzeit": "Term", "Wachstum": "Growth", "Angelegt": "Created", "Monat": "month",
    "pro Monat (netto, normiert) · {n} Positionen": "per month (net, normalised) · {n} items", "unvollständig": "incomplete",
    "Hochrechnung": "Projection", "Wachstum je Monat (%)": "Growth per month (%)", "Übernehmen": "Apply",
    "Gilt für alle Positionen ohne eigenen Wert. Stückzahlen (VMs, Cluster) werden aufgerundet.":
        "Applies to all items without their own value. Unit counts (VMs, clusters) are rounded up.",
    "Monat 1": "Month 1", "Monat {m}": "Month {m}", "Summe über {m} Monate": "Total over {m} months", "Ø je Monat": "Avg. per month",
    "Wachstum %/Monat": "Growth %/month", "Wachstum %/Monat (leer = Szenario-Standard)": "Growth %/month (empty = scenario default)",
    "Berechnung": "Calculation", "Speichern": "Save", "Position entfernen": "Remove item",
    "Noch keine Positionen. Über das ＋ in der Preistabelle oder im Vergleich hinzufügen.":
        "No items yet. Add them with ＋ in the price table or comparison.",
    "Excel-Export (mit Hochrechnung)": "Excel export (with projection)", "Szenario löschen?": "Delete scenario?",
    "Szenario löschen": "Delete scenario", "Preis nicht gefunden.": "Price not found.", "Menge in „{u}“": "Quantity in “{u}”",
    "neues Szenario …": "new scenario …", "Name (bei neuem Szenario)": "Name (for new scenario)", "Hinzufügen": "Add",
    "Anzahl (z. B. VMs) – Dauerbetrieb 730 h/Monat": "Count (e.g. VMs) – continuous use 730 h/month",
    "GB gespeichert": "GB stored", "GB pro Monat (z. B. Traffic)": "GB per month (e.g. traffic)",
    "Mio. Token pro Monat": "million tokens per month", "Tausend Anfragen pro Monat": "thousand requests per month",
    "nicht mehr gelistet": "no longer listed", "unbekannt": "unknown",
    # Status, Kategorien, Preistypen, Einheiten, Gründe
    "übernommen": "applied", "übersprungen": "skipped", "Freigabe offen": "awaiting approval", "läuft …": "running …",
    "verworfen": "discarded", "Compute": "Compute", "Storage": "Storage", "Kubernetes": "Kubernetes", "Netzwerk": "Network",
    "KI": "AI", "Sonstiges": "Other", "On-Demand": "On-demand", "Spot": "Spot", "Reserviert 1 J.": "Reserved 1 yr",
    "Reserviert 3 J.": "Reserved 3 yr", "Savings Plan 1 J.": "Savings plan 1 yr", "Savings Plan 3 J.": "Savings plan 3 yr",
    "Dev/Test": "Dev/test", "GB-Monat": "GB-month", "GB": "GB", "1 Mio. Token": "1M tokens", "1.000 Anfragen": "1,000 requests",
    "Preisänderung": "Price change", "Nicht mehr gelistet": "No longer listed", "Nicht mehr im Abrufumfang": "No longer in fetch scope",
    "Schlüssel/Name geändert": "Key/name changed",
    # Datenhoheit
    "EU-Anbieter, EU-Region": "EU provider, EU region", "Sitz des Anbieters und Datenstandort in der EU":
        "Provider headquarters and data location in the EU",
    "EU-Region, US-Anbieter": "EU region, US provider", "Daten in der EU, Anbieter unterliegt auch US-Recht (CLOUD Act)":
        "Data in the EU, provider is also subject to US law (CLOUD Act)",
    "außerhalb der EU": "outside the EU", "Datenstandort außerhalb der EU": "Data location outside the EU",
    "Standort unbekannt": "location unknown", "Region lässt sich keinem Land zuordnen (z. B. global)":
        "Region cannot be assigned to a country (e.g. global)",
    "nur EU-Regionen": "EU regions only", "nur EU-Anbieter in EU-Regionen": "EU providers in EU regions only",
    "EU (mehrere Standorte)": "EU (several locations)", "Deutschland": "Germany", "Niederlande": "Netherlands",
    "Irland": "Ireland", "Frankreich": "France", "Schweden": "Sweden", "Italien": "Italy", "Spanien": "Spain",
    "Polen": "Poland", "Finnland": "Finland", "Belgien": "Belgium", "Österreich": "Austria", "Schweiz": "Switzerland",
    "Vereinigtes Königreich": "United Kingdom", "Norwegen": "Norway", "USA": "USA", "Kanada": "Canada",
    "Singapur": "Singapore", "Australien": "Australia", "Indien": "India", "Japan": "Japan", "Dänemark": "Denmark",
}
