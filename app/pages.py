"""HTML-Seiten der Preisverwaltung."""
from __future__ import annotations

import csv
import io
import json

from . import analysis, config, fx, jobs
from .db import Database
from .models import CATEGORIES, PRICE_TYPES
from .providers import PROVIDERS
from .ui import (PAGE_SIZE, csrf_field, e, float_param, fmt_dt, fmt_eur, fmt_int, fmt_price, int_param,
                 layout, pager, qs)


def plabel(name: str) -> str:
    return PROVIDERS[name].LABEL if name in PROVIDERS else name


# ------------------------------------------------------------------ Abfragen
CURRENT_SORT = {"anbieter": "provider", "kategorie": "category", "produkt": "product", "region": "region",
                "preis": "price", "norm": "price_norm", "seit": "valid_from", "dienst": "service"}
FILTER_FIELDS = ("provider", "category", "region", "price_type", "unit_norm")


def where_clause(params: dict, extra: tuple[str, ...] = ()) -> tuple[str, list]:
    clauses, args = [], []
    for field in FILTER_FIELDS + extra:
        if params.get(field):
            clauses.append(f"{field} = ?")
            args.append(params[field])
    if params.get("key"):
        clauses.append("key = ?")
        args.append(params["key"])
    if params.get("q"):
        for word in params["q"].split():
            clauses.append("(pv_lower(product) LIKE ? OR pv_lower(service) LIKE ? OR pv_lower(key) LIKE ?)")
            args += [f"%{word.casefold()}%"] * 3
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", args


def provider_overview(db: Database) -> list[dict]:
    with db.connect() as con:
        counts = {r[0]: r[1] for r in con.execute("SELECT provider, COUNT(*) FROM prices_current GROUP BY provider")}
        last = {r["provider"]: dict(r) for r in con.execute(
            "SELECT * FROM fetch_runs WHERE id IN (SELECT MAX(id) FROM fetch_runs GROUP BY provider)")}
        last_ok = {r[0]: r[1] for r in con.execute(
            "SELECT provider, MAX(COALESCE(decided_at, finished_at)) FROM fetch_runs WHERE status='ok' GROUP BY provider")}
        review = {r[0]: r[1] for r in con.execute(
            "SELECT provider, MAX(id) FROM fetch_runs WHERE status='review' GROUP BY provider")}
    cfg = config.load_config()["providers"]
    running = {name for name in PROVIDERS if jobs.is_running(db, name)}
    return [{"name": n, "label": m.LABEL, "info": m.INFO, "enabled": cfg.get(n, {}).get("enabled", True),
             "count": counts.get(n, 0), "last": last.get(n), "last_ok": last_ok.get(n),
             "review": review.get(n), "running": n in running} for n, m in PROVIDERS.items()]


def distinct(db: Database, table: str, field: str, params: dict) -> list[str]:
    sub = {"provider": params["provider"]} if params.get("provider") and field != "provider" else {}
    where, args = where_clause(sub)
    with db.connect() as con:
        return [r[0] for r in con.execute(f"SELECT DISTINCT {field} FROM {table}{where} ORDER BY {field}", args)
                if r[0] not in (None, "")]


def status_badge(status: str, title: str = "") -> str:
    label = {"ok": "übernommen", "error": "Fehler", "skipped": "übersprungen", "review": "Freigabe offen",
             "running": "läuft …", "verworfen": "verworfen"}.get(status, status)
    return f'<span class="badge b-{e(status)}" title="{e(title)}">{e(label)}</span>'


# ------------------------------------------------------------------ Aktuelle Preise
def page_prices(db: Database, params: dict, csrf: str, flash: str = "") -> bytes:
    cards = []
    for p in provider_overview(db):
        last = p["last"]
        if p["running"]:
            badge = status_badge("running")
        elif last:
            badge = status_badge(last["status"], last["message"])
        else:
            badge = '<span class="badge b-none">noch nie abgerufen</span>'
        detail = ""
        if p["review"]:
            detail = f'<a href="lauf/{p["review"]}"><b>Vorschau prüfen und freigeben →</b></a>'
        elif last and last["status"] == "ok":
            detail = (f'<a href="lauf/{last["id"]}">+{last["n_new"]} neu · {last["n_changed"]} geändert · '
                      f'{last["n_removed"]} entfallen</a>')
        elif last and last["message"]:
            detail = e(last["message"][:160])
        disabled = "disabled" if p["running"] or not p["enabled"] else ""
        cards.append(f"""<div class="card"><h3>{e(p['label'])} {badge}</h3>
<div class="info">{e(p['info'])}</div><div class="n">{fmt_int(p['count'])}</div>
<div class="meta">Preise aktuell · Stand {fmt_dt(p['last_ok'])}</div><div class="meta">{detail}</div>
<form method="post" action="aktualisieren">{csrf_field(csrf)}<input type="hidden" name="provider" value="{p['name']}">
<button data-run="{p['name']}" {disabled}>{'Deaktiviert' if not p['enabled'] else 'Aktualisieren'}</button></form></div>""")

    where, args = where_clause(params)
    sort = params.get("sort", "anbieter")
    order = CURRENT_SORT.get(sort.lstrip("-"), "provider")
    direction = "DESC" if sort.startswith("-") else "ASC"
    nulls = f"{order} IS NULL, " if order == "price_norm" else ""
    page = int_param(params, "seite")
    with db.connect() as con:
        total = con.execute(f"SELECT COUNT(*) FROM prices_current{where}", args).fetchone()[0]
        rows = con.execute(
            f"""SELECT c.*, (SELECT COUNT(*) FROM prices_archive a WHERE a.provider=c.provider AND a.key=c.key) AS n_hist
                FROM prices_current c{where} ORDER BY {nulls}{order} {direction}, provider, product
                LIMIT ? OFFSET ?""", args + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()

    body = ['<h1>Aktuelle Preise</h1>']
    if flash:
        body.append(f'<div class="flash">{e(flash)}</div>')
    rate = fx.rates_info(db)
    rate_txt = (f'EZB-Referenzkurs {rate["rate_date"]}: 1 € = {fmt_price(rate["rate"], 4)} USD'
                if rate else "Noch keine EZB-Kurse geladen – USD-Preise ohne EUR-Umrechnung")
    body.append(f"""<div class="row" style="justify-content:space-between;margin-bottom:10px">
<div class="small"><span id="running"></span> {e(rate_txt)}</div>
<form method="post" action="aktualisieren">{csrf_field(csrf)}<input type="hidden" name="provider" value="all">
<button class="primary" data-run="all">Alle Anbieter aktualisieren</button></form></div>""")
    body.append(f'<div class="cards">{"".join(cards)}</div>')
    body.append(filter_form(db, "prices_current", params, "", export="export.csv"))

    if not rows:
        body.append('<div class="empty">Keine Preise vorhanden. Mit „Aktualisieren“ werden die aktuellen '
                    'Listenpreise beim Anbieter abgefragt.</div>' if total == 0 and not where
                    else '<div class="empty">Keine Treffer für diese Filter.</div>')
        return layout("Aktuelle Preise", "".join(body), "preise", config.BASE_PATH)

    def th(label, key, title=""):
        cur = params.get("sort", "anbieter")
        nxt = f"-{key}" if cur == key else key
        arrow = " ▲" if cur == key else (" ▼" if cur == f"-{key}" else "")
        return f'<th title="{e(title)}"><a href="./{qs(params, sort=nxt, seite=None)}">{label}{arrow}</a></th>'

    body.append(f'<h2>{fmt_int(total)} Preise</h2><table><tr>{th("Anbieter", "anbieter")}'
                f'{th("Kategorie", "kategorie")}{th("Dienst", "dienst")}{th("Produkt", "produkt")}'
                f'{th("Region", "region")}{th("Listenpreis", "preis")}<th>Einheit</th>'
                f'{th("Normiert (EUR)", "norm", "Stundenpreise × 730 h; „30 Tage“ × 730/720; USD per EZB-Kurs")}'
                f'<th>Typ</th>{th("Gültig seit", "seit")}<th></th></tr>')
    for r in rows:
        hist = (f'<a href="verlauf{qs({"provider": r["provider"], "key": r["key"]})}">Verlauf ({r["n_hist"]})</a>'
                if r["n_hist"] else "")
        add = (f'<a class="plus" title="Zu Szenario hinzufügen" '
               f'href="szenario-hinzufuegen{qs({"provider": r["provider"], "key": r["key"]})}">＋</a>')
        norm = f'{fmt_eur(r["price_norm"])}<br><span class="small">/{e(r["unit_norm"])}</span>' \
            if r["price_norm"] is not None else '<span class="small">—</span>'
        vm = f' <span class="small">({r["vcpu"]:g} vCPU / {r["ram_gb"]:g} GB)</span>' if r["vcpu"] and r["ram_gb"] else ""
        body.append(
            f'<tr><td>{e(plabel(r["provider"]))}</td><td>{e(r["category"])}</td><td class="small">{e(r["service"])}</td>'
            f'<td title="{e(r["key"])}">{e(r["product"])}{vm}</td><td>{e(r["region"])}</td>'
            f'<td class="num">{fmt_price(r["price"])} {e(r["currency"])}</td><td class="small">{e(r["unit"])}</td>'
            f'<td class="num">{norm}</td><td class="small">{e(r["price_type"])}</td>'
            f'<td class="small">{fmt_dt(r["valid_from"])}</td><td class="small">{add} {hist}</td></tr>')
    body.append("</table>")
    body.append(pager(params, page, total, path="./"))
    return layout("Aktuelle Preise", "".join(body), "preise", config.BASE_PATH)


def filter_form(db: Database, table: str, params: dict, action: str, export: str = "",
                hidden: tuple[str, ...] = ("key",)) -> str:
    def select(name, label, options, labels=None):
        opts = ['<option value="">Alle</option>'] + [
            f'<option value="{e(o)}" {"selected" if params.get(name) == o else ""}>{e((labels or {}).get(o, o))}</option>'
            for o in options]
        return f'<label>{label}<select name="{name}" onchange="this.form.submit()">{"".join(opts)}</select></label>'

    present = set(distinct(db, table, "category", params))
    cats = [c for c in CATEGORIES if c in present]
    types_present = set(distinct(db, table, "price_type", params))
    types = [t for t in PRICE_TYPES if t in types_present] + sorted(types_present - set(PRICE_TYPES))
    hid = "".join(f'<input type="hidden" name="{h}" value="{e(params[h])}">' for h in hidden if params.get(h))
    export_link = f'<a class="btn" href="{export}{qs(params, seite=None)}">CSV-Export</a>' if export else ""
    return f"""<form class="filters" method="get" action="{action or './'}">{hid}
{select("provider", "Anbieter", list(PROVIDERS), {k: m.LABEL for k, m in PROVIDERS.items()})}
{select("category", "Kategorie", cats)}
{select("region", "Region", distinct(db, table, "region", params))}
{select("price_type", "Preistyp", types)}
{select("unit_norm", "Normierte Einheit", distinct(db, table, "unit_norm", params))}
<label>Suche<input name="q" value="{e(params.get('q', ''))}" placeholder="z. B. D4s, m6i.large, Object"></label>
<button type="submit">Filtern</button><a class="btn" href="{action or './'}">Zurücksetzen</a>{export_link}</form>"""


# ------------------------------------------------------------------ Archiv
def page_archive(db: Database, params: dict) -> bytes:
    body = ['<h1>Archiv</h1><p class="small">Frühere Preise, die durch eine Preisänderung ersetzt wurden oder '
            'nicht mehr gelistet sind. Das Archiv wird nur hier angezeigt.</p>']
    body.append(filter_form(db, "prices_archive", params, "archiv", export="archiv.csv", hidden=("key", "run_id")))
    where, args = where_clause(params, ("run_id",))
    page = int_param(params, "seite")
    with db.connect() as con:
        total = con.execute(f"SELECT COUNT(*) FROM prices_archive{where}", args).fetchone()[0]
        rows = con.execute(f"SELECT * FROM prices_archive{where} ORDER BY valid_to DESC, provider, product "
                           f"LIMIT ? OFFSET ?", args + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
    if params.get("run_id"):
        body.append(f'<div class="flash">Gefiltert auf Abruf <a href="lauf/{e(params["run_id"])}">#{e(params["run_id"])}</a></div>')
    if not rows:
        body.append('<div class="empty">Keine archivierten Preise für diese Auswahl.</div>')
        return layout("Archiv", "".join(body), "archiv", config.BASE_PATH)
    body.append(f'<h2>{fmt_int(total)} archivierte Preise</h2><table><tr><th>Anbieter</th><th>Kategorie</th>'
                '<th>Produkt</th><th>Region</th><th>Alter Preis</th><th>Neuer Preis</th><th>Einheit</th>'
                '<th>Gültig von</th><th>Gültig bis</th><th>Grund</th><th></th></tr>')
    for r in rows:
        if r["new_price"] is not None:
            cls = "up" if r["new_price"] > r["price"] else "down"
            newp = f'<span class="{cls}">{fmt_price(r["new_price"])}</span>'
        else:
            newp = "—"
        body.append(
            f'<tr><td>{e(plabel(r["provider"]))}</td><td>{e(r["category"])}</td><td title="{e(r["key"])}">{e(r["product"])}</td>'
            f'<td>{e(r["region"])}</td><td class="num">{fmt_price(r["price"])} {e(r["currency"])}</td>'
            f'<td class="num">{newp}</td><td class="small">{e(r["unit"])}</td><td class="small">{fmt_dt(r["valid_from"])}</td>'
            f'<td class="small">{fmt_dt(r["valid_to"])}</td><td class="small">{e(r["reason"])}</td>'
            f'<td class="small"><a href="verlauf{qs({"provider": r["provider"], "key": r["key"]})}">Verlauf</a></td></tr>')
    body.append("</table>")
    body.append(pager(params, page, total, path="archiv"))
    return layout("Archiv", "".join(body), "archiv", config.BASE_PATH)


# ------------------------------------------------------------------ Protokoll & Lauf
def page_log(db: Database, params: dict) -> bytes:
    page = int_param(params, "seite")
    where, args = ("", [])
    if params.get("provider"):
        where, args = " WHERE provider=?", [params["provider"]]
    with db.connect() as con:
        total = con.execute(f"SELECT COUNT(*) FROM fetch_runs{where}", args).fetchone()[0]
        rows = con.execute(f"SELECT * FROM fetch_runs{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                           args + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
    body = ['<h1>Abrufprotokoll</h1>']
    if not rows:
        body.append('<div class="empty">Noch keine Abrufe.</div>')
        return layout("Abrufprotokoll", "".join(body), "protokoll", config.BASE_PATH)
    body.append('<table><tr><th>#</th><th>Start</th><th>Ende</th><th>Anbieter</th><th>Status</th><th>Abgerufen</th>'
                '<th>Neu</th><th>Geändert</th><th>Entfallen</th><th>Umbenannt</th><th>Doppelt</th><th>Meldung</th></tr>')
    for r in rows:
        body.append(
            f'<tr><td><a href="lauf/{r["id"]}">#{r["id"]}</a></td><td class="small">{fmt_dt(r["started_at"])}</td>'
            f'<td class="small">{fmt_dt(r["finished_at"])}</td><td>{e(plabel(r["provider"]))}</td>'
            f'<td>{status_badge(r["status"])}</td><td class="num">{r["n_fetched"]}</td><td class="num">{r["n_new"]}</td>'
            f'<td class="num">{r["n_changed"]}</td><td class="num">{r["n_removed"]}</td><td class="num">{r["n_renamed"] or 0}</td>'
            f'<td class="num">{r["n_dupes"] or 0}</td><td class="small">{e((r["message"] or "")[:300])}</td></tr>')
    body.append("</table>")
    body.append(pager(params, page, total, path="protokoll"))
    return layout("Abrufprotokoll", "".join(body), "protokoll", config.BASE_PATH)


def page_run(db: Database, run_id: int, params: dict, csrf: str, flash: str = "") -> bytes | None:
    with db.connect() as con:
        run = con.execute("SELECT * FROM fetch_runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            return None
        counts = {r[0]: r[1] for r in con.execute(
            "SELECT change, COUNT(*) FROM run_changes WHERE run_id=? GROUP BY change", (run_id,))}
        change = params.get("art", "")
        page = int_param(params, "seite")
        where, args = " WHERE run_id=?", [run_id]
        if change:
            where += " AND change=?"
            args.append(change)
        total = con.execute(f"SELECT COUNT(*) FROM run_changes{where}", args).fetchone()[0]
        rows = con.execute(f"SELECT * FROM run_changes{where} ORDER BY change, category, product LIMIT ? OFFSET ?",
                           args + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
    is_review = run["status"] == "review"
    title = f"Abruf #{run_id} · {plabel(run['provider'])}"
    body = [f'<h1>{e(title)} {status_badge(run["status"])}</h1>']
    if flash:
        body.append(f'<div class="flash">{e(flash)}</div>')
    body.append(f'<p class="small">Gestartet {fmt_dt(run["started_at"])} ({e(run["triggered_by"])}) · beendet '
                f'{fmt_dt(run["finished_at"])} · Quelle {e(run["source"])} · Stand laut Anbieter {e(run["source_date"] or "—")}'
                + (f' · entschieden {fmt_dt(run["decided_at"])} ({e(run["decided_by"])})' if run["decided_at"] else "")
                + '</p>')
    if run["violations"]:
        body.append('<div class="errbox"><b>Schutzregel verletzt:</b><br>'
                    + "<br>".join(e(v) for v in run["violations"].splitlines()) + '</div>')
    if run["message"] and not run["violations"]:
        body.append(f'<div class="{"errbox" if run["status"] == "error" else "flash"}">{e(run["message"])}</div>')
    if run["warnings"]:
        warns = run["warnings"].splitlines()
        body.append(f'<details class="warnbox"><summary>{len(warns)} Hinweise zum Abruf</summary>'
                    f'<pre>{e(chr(10).join(warns[:300]))}</pre></details>')
    if is_review:
        label = "Trotzdem übernehmen" if run["violations"] else "Änderungen übernehmen"
        body.append(f"""<div class="row" style="margin:10px 0 16px">
<form method="post" action="lauf/{run_id}/freigeben">{csrf_field(csrf)}<button class="primary">{label}</button></form>
<form method="post" action="lauf/{run_id}/verwerfen">{csrf_field(csrf)}<button class="danger">Verwerfen</button></form>
<span class="small">Bis zur Freigabe bleibt der aktuelle Bestand unverändert.</span></div>""")

    tabs = [("", f"Alle ({sum(counts.values())})")] + [
        (k, f"{k} ({counts.get(k, 0)})") for k in ("geändert", "neu", "entfallen", "umbenannt")]
    body.append('<div class="row" style="margin-bottom:10px">' + "".join(
        f'<a class="btn {"primary" if change == k else ""}" href="lauf/{run_id}{qs({"art": k})}">{e(lbl)}</a>'
        for k, lbl in tabs) + (f' <a class="small" href="archiv?run_id={run_id}">im Archiv ansehen</a>'
                               if run["status"] == "ok" else "") + '</div>')
    if run["status"] == "ok" and not counts:
        body.append('<div class="empty">Keine Änderungen in diesem Abruf.</div>')
    elif not rows:
        body.append('<div class="empty">Keine Einträge.</div>')
    else:
        body.append('<table><tr><th>Art</th><th>Kategorie</th><th>Produkt</th><th>Region</th><th>Alt</th>'
                    '<th>Neu</th><th>Δ</th><th>Einheit</th><th>Hinweis</th></tr>')
        for r in rows:
            delta = ""
            if r["old_price"] and r["new_price"] is not None and r["change"] == "geändert":
                pct = (r["new_price"] - r["old_price"]) / r["old_price"] * 100
                delta = f'<span class="{"up" if pct > 0 else "down"}">{pct:+.1f} %</span>'
            note = e(r["reason"]) + (f'<br><span class="small">vorher: {e(r["old_key"])}</span>' if r["old_key"] else "")
            body.append(
                f'<tr><td><span class="badge b-{e(r["change"])}">{e(r["change"])}</span></td><td>{e(r["category"])}</td>'
                f'<td title="{e(r["key"])}">{e(r["product"])}</td><td>{e(r["region"])}</td>'
                f'<td class="num">{fmt_price(r["old_price"])}</td><td class="num">{fmt_price(r["new_price"])} {e(r["currency"])}</td>'
                f'<td class="num">{delta}</td><td class="small">{e(r["unit"])}</td><td class="small">{note}</td></tr>')
        body.append("</table>")
        body.append(pager(params, page, total, path=f"lauf/{run_id}"))
    return layout(title, "".join(body), "protokoll", config.BASE_PATH)


# ------------------------------------------------------------------ Verlauf
def page_history(db: Database, params: dict) -> bytes:
    provider, key = params.get("provider", ""), params.get("key", "")
    cur, arch = analysis.history(db, provider, key)
    if not cur and not arch:
        return layout("Verlauf", '<div class="empty">Kein Preis mit diesem Schlüssel gefunden.</div>', "", config.BASE_PATH)
    ref = cur or arch[-1]
    body = [f'<h1>Preisverlauf</h1><p><b>{e(plabel(provider))}</b> · {e(ref["product"])} · {e(ref["region"])} · '
            f'{e(ref["price_type"])}<br><span class="small">Schlüssel: <code>{e(key)}</code></span></p>']
    points = [(a["valid_from"], a["price"]) for a in arch]
    if cur:
        points.append((cur["valid_from"], cur["price"]))
    if len(points) > 1:
        body.append(analysis.sparkline_svg(points))
    body.append('<table><tr><th>Gültig von</th><th>Gültig bis</th><th>Preis</th><th>Einheit</th><th>Normiert</th><th>Status</th></tr>')
    if cur:
        body.append(f'<tr><td>{fmt_dt(cur["valid_from"])}</td><td>—</td><td class="num"><b>{fmt_price(cur["price"])} '
                    f'{e(cur["currency"])}</b></td><td class="small">{e(cur["unit"])}</td>'
                    f'<td class="num">{fmt_eur(cur["price_norm"])}</td><td><span class="badge b-ok">aktuell</span></td></tr>')
    for a in reversed(arch):
        body.append(f'<tr><td>{fmt_dt(a["valid_from"])}</td><td>{fmt_dt(a["valid_to"])}</td>'
                    f'<td class="num">{fmt_price(a["price"])} {e(a["currency"])}</td><td class="small">{e(a["unit"])}</td>'
                    f'<td class="num">{fmt_eur(a["price_norm"])}</td><td class="small">{e(a["reason"])}</td></tr>')
    body.append("</table>")
    return layout("Preisverlauf", "".join(body), "", config.BASE_PATH)


# ------------------------------------------------------------------ Vergleich
def page_compare(db: Database, params: dict) -> bytes:
    vcpu = float_param(params, "vcpu", 2)
    ram = float_param(params, "ram", 8)
    os_ = params.get("os", "linux") if params.get("os", "linux") in ("linux", "windows", "linux-lizenz", "") else "linux"
    region = params.get("region", "")
    top = int_param(params, "top", 3, 1, 20)
    res = analysis.compare_vms(db, vcpu, ram, os_, top, region)
    rate = fx.rates_info(db)
    os_opts = "".join(f'<option value="{v}" {"selected" if os_ == v else ""}>{lbl}</option>' for v, lbl in
                      (("linux", "Linux"), ("windows", "Windows (inkl. Lizenz)"), ("linux-lizenz", "RHEL/SUSE"), ("", "alle")))
    body = [f"""<h1>VM-Vergleich</h1>
<p class="small">Günstigste On-Demand-Optionen je Anbieter mit mindestens der gewünschten Ausstattung, normiert auf
EUR pro Monat (730 h Dauerbetrieb, netto). USD-Preise mit EZB-Referenzkurs
{e(rate["rate_date"]) if rate else "(noch nicht geladen)"} umgerechnet. Komponentenpreise (Google, IONOS) werden aus
Preis je vCPU und je GB RAM zusammengesetzt. RAM bei Azure aus der Serie abgeleitet (D: 4 GB, E: 8 GB, F: 2 GB je vCPU).</p>
<form class="filters" method="get" action="vergleich">
<label>vCPU<input name="vcpu" value="{vcpu:g}" size="4"></label><label>RAM (GB)<input name="ram" value="{ram:g}" size="4"></label>
<label>Betriebssystem<select name="os">{os_opts}</select></label>
<label>Region enthält<input name="region" value="{e(region)}" placeholder="z. B. eu, germany, frankfurt"></label>
<label>Treffer je Anbieter<input name="top" value="{top}" size="3"></label><button class="primary">Vergleichen</button></form>"""]
    if not res:
        body.append('<div class="empty">Keine passenden Compute-Preise gefunden. Sind die Anbieter schon abgerufen?</div>')
        return layout("Vergleich", "".join(body), "vergleich", config.BASE_PATH)
    best = min(o["eur_month"] for opts in res.values() for o in opts)
    body.append('<table><tr><th>Anbieter</th><th>Option</th><th>Art</th><th>Region</th><th>vCPU</th><th>RAM</th>'
                '<th>€/Monat</th><th>vs. günstigste</th><th></th></tr>')
    for prov, opts in res.items():
        for i, o in enumerate(opts):
            diff = (o["eur_month"] / best - 1) * 100 if best else 0
            add = (f'<a class="plus" href="szenario-hinzufuegen{qs({"provider": o["provider"], "key": o["key"]})}">＋</a>'
                   if o["key"] else "")
            body.append(f'<tr><td>{e(plabel(prov)) if i == 0 else ""}</td><td>{e(o["label"])}</td><td class="small">{e(o["kind"])}</td>'
                        f'<td>{e(o["region"])}</td><td class="num">{o["vcpu"]:g}</td><td class="num">{o["ram"]:g}</td>'
                        f'<td class="num"><b>{fmt_eur(o["eur_month"])}</b></td>'
                        f'<td class="num">{"günstigste" if diff < 0.05 else f"+{diff:.0f} %"}</td><td>{add}</td></tr>')
    body.append("</table>")
    return layout("Vergleich", "".join(body), "vergleich", config.BASE_PATH)


# ------------------------------------------------------------------ Szenarien
def page_scenarios(db: Database, csrf: str) -> bytes:
    with db.connect() as con:
        rows = con.execute("SELECT s.*, (SELECT COUNT(*) FROM scenario_items i WHERE i.scenario_id=s.id) n "
                           "FROM scenarios s ORDER BY s.id DESC").fetchall()
    body = ["""<h1>Szenarien</h1><p class="small">Ein Szenario ist ein Warenkorb mit Mengen (z. B. 10 VMs, 2.000 GB Storage,
5.000 GB Traffic). Positionen lassen sich in Varianten (A, B, …) gruppieren, um Anbieter zu vergleichen. Preise werden immer
aus dem aktuellen Bestand berechnet. Positionen fügt man über das ＋ in der Preistabelle oder im Vergleich hinzu.</p>"""]
    body.append(f"""<form class="filters" method="post" action="szenarien">{csrf_field(csrf)}
<label>Name<input name="name" required placeholder="z. B. Plattform 2027"></label>
<label>Notiz<input name="note" size="40"></label><button class="primary">Szenario anlegen</button></form>""")
    if rows:
        body.append('<table><tr><th>Name</th><th>Positionen</th><th>Angelegt</th><th>Notiz</th></tr>')
        for r in rows:
            body.append(f'<tr><td><a href="szenario/{r["id"]}">{e(r["name"])}</a></td><td class="num">{r["n"]}</td>'
                        f'<td class="small">{fmt_dt(r["created_at"])}</td><td class="small">{e(r["note"])}</td></tr>')
        body.append("</table>")
    return layout("Szenarien", "".join(body), "szenarien", config.BASE_PATH)


def page_scenario(db: Database, sid: int, csrf: str) -> bytes | None:
    with db.connect() as con:
        s = con.execute("SELECT * FROM scenarios WHERE id=?", (sid,)).fetchone()
    if not s:
        return None
    lines = analysis.scenario_lines(db, sid)
    totals = analysis.scenario_totals(lines)
    body = [f'<h1>Szenario: {e(s["name"])}</h1><p class="small">{e(s["note"])}</p>']
    if totals:
        cheapest = min((t["sum"] for t in totals.values() if t["n"]), default=0)
        body.append('<div class="cards">' + "".join(
            f'<div class="card"><h3>Variante {e(v)}</h3><div class="n">{fmt_eur(t["sum"])}</div>'
            f'<div class="meta">pro Monat (netto, normiert) · {t["n"]} Positionen'
            + (' · <span class="up">unvollständig</span>' if t["incomplete"] else "")
            + ('' if t["sum"] > cheapest or len(totals) == 1 else ' · <b>günstigste</b>') + '</div></div>'
            for v, t in totals.items()) + '</div>')
        body.append('<h2>Positionen</h2><table><tr><th>Variante</th><th>Anbieter</th><th>Produkt</th><th>Region</th>'
                    '<th>Menge</th><th>Preis</th><th>€/Monat</th><th>Berechnung</th><th>Status</th><th></th></tr>')
        for ln in lines:
            it, p = ln["item"], ln["price"] or {}
            status = ('<span class="badge b-ok">aktuell</span>' if ln["status"] == "aktuell"
                      else f'<span class="badge b-error">{e(ln["status"])}</span>')
            body.append(
                f'<tr><td>{e(it["variant"])}</td><td>{e(plabel(it["provider"]))}</td>'
                f'<td>{e(p.get("product") or it["label"])}{"<br><span class=small>" + e(it["note"]) + "</span>" if it["note"] else ""}</td>'
                f'<td>{e(p.get("region", ""))}</td><td class="num">{it["quantity"]:g}</td>'
                f'<td class="num">{fmt_price(p.get("price"))} {e(p.get("currency", ""))}<br><span class="small">{e(p.get("unit", ""))}</span></td>'
                f'<td class="num"><b>{fmt_eur(ln.get("eur_month"))}</b></td><td class="small">{e(ln.get("calc", ""))}</td>'
                f'<td>{status}</td><td><form method="post" action="szenario/{sid}/position/{it["id"]}/loeschen">'
                f'{csrf_field(csrf)}<button class="danger" title="Position entfernen">×</button></form></td></tr>')
        body.append('</table>')
    else:
        body.append('<div class="empty">Noch keine Positionen. Über das ＋ in der Preistabelle oder im Vergleich hinzufügen.</div>')
    body.append(f'<p style="margin-top:20px"><a class="btn" href="szenario/{sid}/export.csv">CSV-Export</a></p>'
                f'<form method="post" action="szenario/{sid}/loeschen" onsubmit="return confirm(\'Szenario löschen?\')">'
                f'{csrf_field(csrf)}<button class="danger">Szenario löschen</button></form>')
    return layout(s["name"], "".join(body), "szenarien", config.BASE_PATH)


def page_scenario_add(db: Database, params: dict, csrf: str) -> bytes:
    provider, key = params.get("provider", ""), params.get("key", "")
    with db.connect() as con:
        p = con.execute("SELECT * FROM prices_current WHERE provider=? AND key=?", (provider, key)).fetchone()
        scen = con.execute("SELECT id, name FROM scenarios ORDER BY id DESC").fetchall()
    if not p:
        return layout("Szenario", '<div class="empty">Preis nicht gefunden.</div>', "szenarien", config.BASE_PATH)
    hint = analysis.QTY_HINT.get(p["unit_norm"], f"Menge in „{p['unit']}“")
    opts = "".join(f'<option value="{s["id"]}">{e(s["name"])}</option>' for s in scen)
    body = [f"""<h1>Zu Szenario hinzufügen</h1><p><b>{e(plabel(provider))}</b> · {e(p['product'])} · {e(p['region'])}<br>
{fmt_price(p['price'])} {e(p['currency'])} {e(p['unit'])} · normiert {fmt_eur(p['price_norm'])} /{e(p['unit_norm'] or '—')}</p>
<form class="filters" method="post" action="szenario-hinzufuegen">{csrf_field(csrf)}
<input type="hidden" name="provider" value="{e(provider)}"><input type="hidden" name="key" value="{e(key)}">
<label>Szenario<select name="scenario_id">{opts}<option value="neu">➕ neues Szenario …</option></select></label>
<label>Name (bei neuem Szenario)<input name="name"></label>
<label>Variante<input name="variant" value="A" size="4"></label>
<label>Menge ({e(hint)})<input name="quantity" value="1" size="8"></label>
<label>Notiz<input name="note" size="30"></label><button class="primary">Hinzufügen</button></form>"""]
    return layout("Zu Szenario hinzufügen", "".join(body), "szenarien", config.BASE_PATH)


# ------------------------------------------------------------------ CSV
def _safe_cell(v):
    """Schutz vor CSV-/Formel-Injektion in Tabellenkalkulationen."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _csv(cols: list[str], rows, num_cols: set[str]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(cols)
    for row in rows:
        w.writerow([str(row[c]).replace(".", ",") if c in num_cols and row[c] is not None else _safe_cell(row[c])
                    for c in cols])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def export_csv(db: Database, table: str, params: dict) -> bytes:
    where, args = where_clause(params, ("run_id",) if table == "prices_archive" else ())
    with db.connect() as con:
        cur = con.execute(f"SELECT * FROM {table}{where} ORDER BY provider, category, product", args)
        cols = [d[0] for d in cur.description if d[0] != "id"]
        return _csv(cols, cur, {"price", "new_price", "price_eur", "price_norm", "vcpu", "ram_gb"})


def export_scenario_csv(db: Database, sid: int) -> bytes:
    rows = []
    for ln in analysis.scenario_lines(db, sid):
        p = ln["price"] or {}
        rows.append({"variante": ln["item"]["variant"], "anbieter": ln["item"]["provider"],
                     "produkt": p.get("product") or ln["item"]["label"], "region": p.get("region", ""),
                     "menge": ln["item"]["quantity"], "preis": p.get("price"), "waehrung": p.get("currency", ""),
                     "einheit": p.get("unit", ""), "eur_monat": ln.get("eur_month"), "status": ln["status"],
                     "schluessel": ln["item"]["key"]})
    cols = ["variante", "anbieter", "produkt", "region", "menge", "preis", "waehrung", "einheit", "eur_monat",
            "status", "schluessel"]
    return _csv(cols, rows, {"menge", "preis", "eur_monat"})


def status_json(db: Database) -> bytes:
    return json.dumps({"providers": {p["name"]: {"label": p["label"], "running": p["running"], "count": p["count"],
                                                 "last_ok": p["last_ok"], "review": p["review"]}
                                     for p in provider_overview(db)}}).encode()
