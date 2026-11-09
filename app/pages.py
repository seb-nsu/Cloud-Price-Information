"""HTML-Seiten der Preisverwaltung."""
from __future__ import annotations

import csv
import io
import json

from . import analysis, config, fx, jobs
from .db import Database
from .i18n import t
from .models import CATEGORIES, PRICE_TYPES
from .providers import PROVIDERS
from .sovereignty import Classifier
from .ui import (PAGE_SIZE, csrf_field, e, float_param, fmt_alt, fmt_dt, fmt_eur, fmt_eur_fine, fmt_int, fmt_money, fmt_pct,
                 fmt_price, int_param, layout, line_chart, pager, qs, souv_badge)


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


STATUS_LABELS = {"ok": "übernommen", "error": "Fehler", "skipped": "übersprungen", "review": "Freigabe offen",
                 "running": "läuft …", "verworfen": "verworfen"}


def status_badge(status: str, title: str = "") -> str:
    label = t(STATUS_LABELS.get(status, status))
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
            badge = f'<span class="badge b-none">{t("noch nie abgerufen")}</span>'
        detail = ""
        if p["review"]:
            detail = f'<a href="lauf/{p["review"]}"><b>{t("Vorschau prüfen und freigeben")} →</b></a>'
        elif last and last["status"] == "ok":
            detail = (f'<a href="lauf/{last["id"]}">' + t("+{n} neu · {c} geändert · {r} entfallen", n=last["n_new"],
                                                         c=last["n_changed"], r=last["n_removed"]) + '</a>')
        elif last and last["message"]:
            detail = e(last["message"][:160])
        if last and last.get("format_alerts"):
            n_alerts = len(last["format_alerts"].splitlines())
            detail += (f'<br><a href="lauf/{last["id"]}" class="up">⚠ '
                       + t("Formatwächter: {n} Auffälligkeit(en)", n=n_alerts) + '</a>')
        disabled = "disabled" if p["running"] or not p["enabled"] else ""
        cards.append(f"""<div class="card"><h3>{e(p['label'])} {badge}</h3>
<div class="info">{e(p['info'])}</div><div class="n">{fmt_int(p['count'])}</div>
<div class="meta">{t("Preise aktuell · Stand")} {fmt_dt(p['last_ok'])}</div><div class="meta">{detail}</div>
<form method="post" action="aktualisieren">{csrf_field(csrf)}<input type="hidden" name="provider" value="{p['name']}">
<button data-run="{p['name']}" {disabled}>{t('Deaktiviert') if not p['enabled'] else t('Aktualisieren')}</button></form></div>""")

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

    body = [f'<h1>{t("Aktuelle Preise")}</h1>']
    if flash:
        body.append(f'<div class="flash">{e(flash)}</div>')
    rate = fx.rates_info(db)
    rate_txt = (t("EZB-Referenzkurs {d}: 1 € = {r} USD", d=rate["rate_date"], r=fmt_price(rate["rate"], 4))
                if rate else t("Noch keine EZB-Kurse geladen – USD-Preise ohne EUR-Umrechnung"))
    body.append(f"""<div class="row" style="justify-content:space-between;margin-bottom:10px">
<div class="small"><span id="running" data-label="{t('Abruf läuft:')}"></span> <a href="kurse">{e(rate_txt)}</a></div>
<form method="post" action="aktualisieren">{csrf_field(csrf)}<input type="hidden" name="provider" value="all">
<button class="primary" data-run="all">{t("Alle Anbieter aktualisieren")}</button></form></div>""")
    body.append(f'<div class="cards">{"".join(cards)}</div>')
    body.append(filter_form(db, "prices_current", params, "", export="export"))

    if not rows:
        body.append(f'<div class="empty">{t("Keine Preise vorhanden. Mit „Aktualisieren“ werden die aktuellen Listenpreise beim Anbieter abgefragt.")}</div>'
                    if total == 0 and not where else f'<div class="empty">{t("Keine Treffer für diese Filter.")}</div>')
        return layout(t("Aktuelle Preise"), "".join(body), "preise", config.BASE_PATH)

    def th(label, key, title=""):
        cur = params.get("sort", "anbieter")
        nxt = f"-{key}" if cur == key else key
        arrow = " ▲" if cur == key else (" ▼" if cur == f"-{key}" else "")
        return f'<th title="{e(title)}"><a href="./{qs(params, sort=nxt, seite=None)}">{label}{arrow}</a></th>'

    rates, curs = fx.get_rates(db), fx.display_currencies()
    cls = Classifier()
    body.append(f'<h2>{t("{n} Preise", n=fmt_int(total))}</h2><table><tr>{th(t("Anbieter"), "anbieter")}'
                f'{th(t("Kategorie"), "kategorie")}{th(t("Dienst"), "dienst")}{th(t("Produkt"), "produkt")}'
                f'{th(t("Region"), "region")}{th(t("Listenpreis"), "preis")}<th>{t("Einheit")}</th>'
                f'{th(t("Normiert (EUR)"), "norm", t("Stundenpreise × 730 h; „30 Tage“ × 730/720; USD per EZB-Kurs"))}'
                f'<th>{t("Typ")}</th>{th(t("Gültig seit"), "seit")}<th></th></tr>')
    for r in rows:
        hist = (f'<a href="verlauf{qs({"provider": r["provider"], "key": r["key"]})}">{t("Verlauf")} ({r["n_hist"]})</a>'
                if r["n_hist"] else "")
        add = (f'<a class="plus" title="{t("Zu Szenario hinzufügen")}" '
               f'href="szenario-hinzufuegen{qs({"provider": r["provider"], "key": r["key"]})}">＋</a>')
        norm = (f'{fmt_eur(r["price_norm"])}<br><span class="small">/{e(t(r["unit_norm"]))}</span>'
                + fmt_alt(r["price_norm"], rates, curs)) if r["price_norm"] is not None else '<span class="small">—</span>'
        vm = ""
        if r["vcpu"] and r["ram_gb"]:
            vm = f' <span class="small">({r["vcpu"]:g} vCPU / {r["ram_gb"]:g} GB' + \
                 (f' · {r["gpu"]:g} GPU' if r["gpu"] else "") + ")</span>"
        souv = cls.classify(r["provider"], r["region"])
        body.append(
            f'<tr><td>{e(plabel(r["provider"]))}</td><td>{e(t(r["category"]))}</td><td class="small">{e(r["service"])}</td>'
            f'<td title="{e(r["key"])}">{e(r["product"])}{vm}</td>'
            f'<td><span title="{e(t(souv["hint"]))}">{e(r["region"])}</span></td>'
            f'<td class="num">{fmt_price(r["price"])} {e(r["currency"])}</td><td class="small">{e(r["unit"])}</td>'
            f'<td class="num">{norm}</td><td class="small">{e(t(r["price_type"]))}</td>'
            f'<td class="small">{fmt_dt(r["valid_from"])}</td><td class="small">{add} {hist}</td></tr>')
    body.append("</table>")
    body.append(pager(params, page, total, path="./"))
    return layout(t("Aktuelle Preise"), "".join(body), "preise", config.BASE_PATH)


def filter_form(db: Database, table: str, params: dict, action: str, export: str = "",
                hidden: tuple[str, ...] = ("key",)) -> str:
    def select(name, label, options, labels=None):
        opts = [f'<option value="">{t("Alle")}</option>'] + [
            f'<option value="{e(o)}" {"selected" if params.get(name) == o else ""}>{e((labels or {}).get(o, t(o)))}</option>'
            for o in options]
        return f'<label>{label}<select name="{name}" onchange="this.form.submit()">{"".join(opts)}</select></label>'

    present = set(distinct(db, table, "category", params))
    cats = [c for c in CATEGORIES if c in present]
    types_present = set(distinct(db, table, "price_type", params))
    types = [x for x in PRICE_TYPES if x in types_present] + sorted(types_present - set(PRICE_TYPES))
    hid = "".join(f'<input type="hidden" name="{h}" value="{e(params[h])}">' for h in hidden if params.get(h))
    regions = distinct(db, table, "region", params)
    export_link = (f'<a class="btn" href="{export}.csv{qs(params, seite=None)}">{t("CSV-Export")}</a>'
                   f'<a class="btn" href="{export}.xlsx{qs(params, seite=None)}">{t("Excel-Export")}</a>') if export else ""
    return f"""<form class="filters" method="get" action="{action or './'}">{hid}
{select("provider", t("Anbieter"), list(PROVIDERS), {k: m.LABEL for k, m in PROVIDERS.items()})}
{select("category", t("Kategorie"), cats)}
{select("region", t("Region"), regions, {r: r for r in regions})}
{select("price_type", t("Preistyp"), types)}
{select("unit_norm", t("Normierte Einheit"), distinct(db, table, "unit_norm", params))}
<label>{t("Suche")}<input name="q" value="{e(params.get('q', ''))}" placeholder="{t('z. B. D4s, m6i.large, Object')}"></label>
<button type="submit">{t("Filtern")}</button><a class="btn" href="{action or './'}">{t("Zurücksetzen")}</a>{export_link}</form>"""


# ------------------------------------------------------------------ Archiv
def page_archive(db: Database, params: dict) -> bytes:
    body = [f'<h1>{t("Archiv")}</h1><p class="small">{t("Frühere Preise, die durch eine Preisänderung ersetzt wurden oder nicht mehr gelistet sind. Das Archiv wird nur hier angezeigt.")}</p>']
    body.append(filter_form(db, "prices_archive", params, "archiv", export="archiv", hidden=("key", "run_id")))
    where, args = where_clause(params, ("run_id",))
    page = int_param(params, "seite")
    with db.connect() as con:
        total = con.execute(f"SELECT COUNT(*) FROM prices_archive{where}", args).fetchone()[0]
        rows = con.execute(f"SELECT * FROM prices_archive{where} ORDER BY valid_to DESC, provider, product "
                           f"LIMIT ? OFFSET ?", args + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
    if params.get("run_id"):
        body.append(f'<div class="flash">{t("Gefiltert auf Abruf")} <a href="lauf/{e(params["run_id"])}">#{e(params["run_id"])}</a></div>')
    if not rows:
        body.append(f'<div class="empty">{t("Keine archivierten Preise für diese Auswahl.")}</div>')
        return layout(t("Archiv"), "".join(body), "archiv", config.BASE_PATH)
    body.append(f'<h2>{t("{n} archivierte Preise", n=fmt_int(total))}</h2><table><tr><th>{t("Anbieter")}</th>'
                f'<th>{t("Kategorie")}</th><th>{t("Produkt")}</th><th>{t("Region")}</th><th>{t("Alter Preis")}</th>'
                f'<th>{t("Neuer Preis")}</th><th>{t("Einheit")}</th><th>{t("Gültig von")}</th><th>{t("Gültig bis")}</th>'
                f'<th>{t("Grund")}</th><th></th></tr>')
    for r in rows:
        if r["new_price"] is not None:
            cls = "up" if r["new_price"] > r["price"] else "down"
            newp = f'<span class="{cls}">{fmt_price(r["new_price"])}</span>'
        else:
            newp = "—"
        body.append(
            f'<tr><td>{e(plabel(r["provider"]))}</td><td>{e(t(r["category"]))}</td><td title="{e(r["key"])}">{e(r["product"])}</td>'
            f'<td>{e(r["region"])}</td><td class="num">{fmt_price(r["price"])} {e(r["currency"])}</td>'
            f'<td class="num">{newp}</td><td class="small">{e(r["unit"])}</td><td class="small">{fmt_dt(r["valid_from"])}</td>'
            f'<td class="small">{fmt_dt(r["valid_to"])}</td><td class="small">{e(t(r["reason"]))}</td>'
            f'<td class="small"><a href="verlauf{qs({"provider": r["provider"], "key": r["key"]})}">{t("Verlauf")}</a></td></tr>')
    body.append("</table>")
    body.append(pager(params, page, total, path="archiv"))
    return layout(t("Archiv"), "".join(body), "archiv", config.BASE_PATH)


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
    body = [f'<h1>{t("Abrufprotokoll")}</h1>']
    if not rows:
        body.append(f'<div class="empty">{t("Noch keine Abrufe.")}</div>')
        return layout(t("Abrufprotokoll"), "".join(body), "protokoll", config.BASE_PATH)
    body.append(f'<table><tr><th>#</th><th>{t("Start")}</th><th>{t("Ende")}</th><th>{t("Anbieter")}</th>'
                f'<th>{t("Status")}</th><th>{t("Abgerufen")}</th><th>{t("Neu")}</th><th>{t("Geändert")}</th>'
                f'<th>{t("Entfallen")}</th><th>{t("Umbenannt")}</th><th>{t("Doppelt")}</th><th>{t("Meldung")}</th></tr>')
    for r in rows:
        alert = ' <span class="up" title="' + t("Formatwächter") + '">⚠</span>' if r["format_alerts"] else ""
        body.append(
            f'<tr><td><a href="lauf/{r["id"]}">#{r["id"]}</a></td><td class="small">{fmt_dt(r["started_at"])}</td>'
            f'<td class="small">{fmt_dt(r["finished_at"])}</td><td>{e(plabel(r["provider"]))}</td>'
            f'<td>{status_badge(r["status"])}{alert}</td><td class="num">{r["n_fetched"]}</td><td class="num">{r["n_new"]}</td>'
            f'<td class="num">{r["n_changed"]}</td><td class="num">{r["n_removed"]}</td><td class="num">{r["n_renamed"] or 0}</td>'
            f'<td class="num">{r["n_dupes"] or 0}</td><td class="small">{e((r["message"] or "")[:300])}</td></tr>')
    body.append("</table>")
    body.append(pager(params, page, total, path="protokoll"))
    return layout(t("Abrufprotokoll"), "".join(body), "protokoll", config.BASE_PATH)


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
    title = t("Abruf #{id} · {p}", id=run_id, p=plabel(run["provider"]))
    body = [f'<h1>{e(title)} {status_badge(run["status"])}</h1>']
    if flash:
        body.append(f'<div class="flash">{e(flash)}</div>')
    body.append(f'<p class="small">{t("Gestartet")} {fmt_dt(run["started_at"])} ({e(run["triggered_by"])}) · {t("beendet")} '
                f'{fmt_dt(run["finished_at"])} · {t("Quelle")} {e(run["source"])} · {t("Stand laut Anbieter")} '
                f'{e(run["source_date"] or "—")}'
                + (f' · {t("entschieden")} {fmt_dt(run["decided_at"])} ({e(run["decided_by"])})' if run["decided_at"] else "")
                + '</p>')
    if run["violations"]:
        body.append(f'<div class="errbox"><b>{t("Schutzregel verletzt:")}</b><br>'
                    + "<br>".join(e(v) for v in run["violations"].splitlines()) + '</div>')
    if run["format_alerts"]:
        body.append(f'<div class="warnbox"><b>{t("Formatwächter: Das Antwortformat des Anbieters hat sich auffällig verändert.")}</b><br>'
                    + "<br>".join(e(v) for v in run["format_alerts"].splitlines())
                    + f'<br><span class="small">{t("Bitte die Änderungen unten prüfen. Ursache kann eine Formatänderung beim Anbieter sein, die das Abruf-Modul nicht mehr richtig liest.")}</span></div>')
    if run["message"] and not run["violations"]:
        body.append(f'<div class="{"errbox" if run["status"] == "error" else "flash"}">{e(run["message"])}</div>')
    if run["warnings"]:
        warns = run["warnings"].splitlines()
        body.append(f'<details class="warnbox"><summary>{t("{n} Hinweise zum Abruf", n=len(warns))}</summary>'
                    f'<pre>{e(chr(10).join(warns[:300]))}</pre></details>')
    if is_review:
        label = t("Trotzdem übernehmen") if (run["violations"] or run["format_alerts"]) else t("Änderungen übernehmen")
        body.append(f"""<div class="row" style="margin:10px 0 16px">
<form method="post" action="lauf/{run_id}/freigeben">{csrf_field(csrf)}<button class="primary">{label}</button></form>
<form method="post" action="lauf/{run_id}/verwerfen">{csrf_field(csrf)}<button class="danger">{t("Verwerfen")}</button></form>
<span class="small">{t("Bis zur Freigabe bleibt der aktuelle Bestand unverändert.")}</span></div>""")

    tab_list = [("", t("Alle ({n})", n=sum(counts.values())))] + [
        (k, f"{t(k)} ({counts.get(k, 0)})") for k in ("geändert", "neu", "entfallen", "umbenannt")]
    body.append('<div class="row" style="margin-bottom:10px">' + "".join(
        f'<a class="btn {"primary" if change == k else ""}" href="lauf/{run_id}{qs({"art": k})}">{e(lbl)}</a>'
        for k, lbl in tab_list) + (f' <a class="small" href="archiv?run_id={run_id}">{t("im Archiv ansehen")}</a>'
                                   if run["status"] == "ok" else "") + '</div>')
    if run["status"] == "ok" and not counts:
        body.append(f'<div class="empty">{t("Keine Änderungen in diesem Abruf.")}</div>')
    elif not rows:
        body.append(f'<div class="empty">{t("Keine Einträge.")}</div>')
    else:
        body.append(f'<table><tr><th>{t("Art")}</th><th>{t("Kategorie")}</th><th>{t("Produkt")}</th><th>{t("Region")}</th>'
                    f'<th>{t("Alt")}</th><th>{t("Neu")}</th><th>Δ</th><th>{t("Einheit")}</th><th>{t("Hinweis")}</th></tr>')
        for r in rows:
            delta = ""
            if r["old_price"] and r["new_price"] is not None and r["change"] == "geändert":
                pct = (r["new_price"] - r["old_price"]) / r["old_price"] * 100
                delta = f'<span class="{"up" if pct > 0 else "down"}">{fmt_pct(pct, True)}</span>'
            note = e(t(r["reason"])) + (f'<br><span class="small">{t("vorher")}: {e(r["old_key"])}</span>' if r["old_key"] else "")
            body.append(
                f'<tr><td><span class="badge b-{e(r["change"])}">{e(t(r["change"]))}</span></td><td>{e(t(r["category"]))}</td>'
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
        return layout(t("Verlauf"), f'<div class="empty">{t("Kein Preis mit diesem Schlüssel gefunden.")}</div>', "",
                      config.BASE_PATH)
    ref = cur or arch[-1]
    currency = ref["currency"]
    body = [f'<h1>{t("Preisverlauf")}</h1><p><b>{e(plabel(provider))}</b> · {e(ref["product"])} · {e(ref["region"])} · '
            f'{e(t(ref["price_type"]))}<br><span class="small">{t("Schlüssel")}: <code>{e(key)}</code></span></p>']
    points = [(a["valid_from"], a["price"]) for a in arch]
    if cur:
        points.append((cur["valid_from"], cur["price"]))
    if len(points) > 1:
        body.append(analysis.sparkline_svg(points))
    # Fremdwährung: EUR zum damaligen und zum heutigen Kurs; EUR-Preise zusätzlich in den Anzeigewährungen
    rates = fx.get_rates(db)
    foreign = currency != "EUR"
    alt = [c for c in fx.display_currencies() if c != currency] if not foreign else []
    head = (f'<th>{t("EUR (Kurs damals)")}</th><th>{t("EUR (Kurs heute)")}</th>' if foreign else
            "".join(f'<th>{e(c)} ({t("Kurs damals")})</th>' for c in alt))
    if foreign:
        body.append(f'<p class="small">{t("Preis in {c}. „Kurs damals“ rechnet mit dem EZB-Kurs zu Beginn der Gültigkeit, „Kurs heute“ mit dem aktuellen Kurs. So lässt sich eine Preisänderung des Anbieters von einer Wechselkursänderung unterscheiden.", c=e(currency))}</p>')
    body.append(f'<table><tr><th>{t("Gültig von")}</th><th>{t("Gültig bis")}</th><th>{t("Preis")}</th><th>{t("Einheit")}</th>'
                f'<th>{t("Normiert")}</th>{head}<th>{t("Status")}</th></tr>')

    def fx_cells(row) -> str:
        then = fx.rate_at(db, currency, row["valid_from"] or "")
        if foreign:
            now = rates.get(currency)
            return (f'<td class="num">{fmt_eur_fine(row["price"] / then) if then else "—"}</td>'
                    f'<td class="num">{fmt_eur_fine(row["price"] / now) if now else "—"}</td>')
        cells = ""
        for c in alt:
            r_then = fx.rate_at(db, c, row["valid_from"] or "")
            cells += f'<td class="num">{fmt_eur_fine(row["price"] * r_then, fx.SYMBOLS.get(c, c)) if r_then else "—"}</td>'
        return cells

    if cur:
        body.append(f'<tr><td>{fmt_dt(cur["valid_from"])}</td><td>—</td><td class="num"><b>{fmt_price(cur["price"])} '
                    f'{e(cur["currency"])}</b></td><td class="small">{e(cur["unit"])}</td>'
                    f'<td class="num">{fmt_eur(cur["price_norm"])}</td>{fx_cells(cur)}'
                    f'<td><span class="badge b-ok">{t("aktuell")}</span></td></tr>')
    for a in reversed(arch):
        body.append(f'<tr><td>{fmt_dt(a["valid_from"])}</td><td>{fmt_dt(a["valid_to"])}</td>'
                    f'<td class="num">{fmt_price(a["price"])} {e(a["currency"])}</td><td class="small">{e(a["unit"])}</td>'
                    f'<td class="num">{fmt_eur(a["price_norm"])}</td>{fx_cells(a)}<td class="small">{e(t(a["reason"]))}</td></tr>')
    body.append("</table>")
    return layout(t("Preisverlauf"), "".join(body), "", config.BASE_PATH)


# ------------------------------------------------------------------ Vergleich
def page_compare(db: Database, params: dict) -> bytes:
    from .pages_extra import alt_cells, alt_headers, compare_tabs, souv_select
    vcpu = float_param(params, "vcpu", 2)
    ram = float_param(params, "ram", 8)
    gpu = float_param(params, "gpu", 0)
    os_ = params.get("os", "linux") if params.get("os", "linux") in ("linux", "windows", "linux-lizenz", "") else "linux"
    region = params.get("region", "")
    souv = params.get("souv", "")
    top = int_param(params, "top", 3, 1, 20)
    res = analysis.compare_vms(db, vcpu, ram, os_, top, region, gpu, souv)
    rate = fx.rates_info(db)
    rates, curs = fx.get_rates(db), fx.display_currencies()
    os_opts = "".join(f'<option value="{v}" {"selected" if os_ == v else ""}>{lbl}</option>' for v, lbl in
                      (("linux", "Linux"), ("windows", t("Windows (inkl. Lizenz)")), ("linux-lizenz", "RHEL/SUSE"),
                       ("", t("alle"))))
    gpu_opts = "".join(f'<option value="{v:g}" {"selected" if gpu == v else ""}>{lbl}</option>' for v, lbl in
                       ((0, t("ohne GPU")), (0.1, t("mind. Teil-GPU")), (1, t("mind. 1 GPU")), (2, t("mind. 2 GPUs")),
                        (4, t("mind. 4 GPUs")), (8, t("mind. 8 GPUs"))))
    body = [f'<h1>{t("Vergleich")}: {t("VMs")}</h1>', compare_tabs("vergleich"),
            f'<p class="small">{t("Günstigste On-Demand-Optionen je Anbieter mit mindestens der gewünschten Ausstattung, normiert auf EUR pro Monat (730 h Dauerbetrieb, netto). USD-Preise mit EZB-Referenzkurs {d} umgerechnet. Komponentenpreise (Google, IONOS) werden aus Preis je vCPU und je GB RAM zusammengesetzt. Azure-Ausstattung laut Größentabelle (D, E, F, L, B, M und GPU-Serien N).", d=e(rate["rate_date"]) if rate else t("(noch nicht geladen)"))}</p>',
            f"""<form class="filters" method="get" action="vergleich">
<label>vCPU<input name="vcpu" value="{vcpu:g}" size="4"></label><label>{t("RAM (GB)")}<input name="ram" value="{ram:g}" size="4"></label>
<label>{t("Betriebssystem")}<select name="os">{os_opts}</select></label>
<label>GPU<select name="gpu">{gpu_opts}</select></label>
{souv_select(params)}
<label>{t("Region enthält")}<input name="region" value="{e(region)}" placeholder="{t('z. B. eu, germany, frankfurt')}"></label>
<label>{t("Treffer je Anbieter")}<input name="top" value="{top}" size="3"></label><button class="primary">{t("Vergleichen")}</button></form>"""]
    if not res:
        body.append(f'<div class="empty">{t("Keine passenden Compute-Preise gefunden. Sind die Anbieter schon abgerufen?")}</div>')
        return layout(t("Vergleich"), "".join(body), "vergleich", config.BASE_PATH)
    best = min(o["eur_month"] for opts in res.values() for o in opts)
    gpu_head = "<th>GPU</th>" if gpu else ""
    body.append(f'<table><tr><th>{t("Anbieter")}</th><th>{t("Option")}</th><th>{t("Art")}</th><th>{t("Region")}</th>'
                f'<th>{t("Datenhoheit")}</th><th>vCPU</th><th>RAM</th>{gpu_head}<th>{t("€/Monat")}</th>'
                f'{alt_headers(curs, t("/Monat"))}<th>{t("vs. günstigste")}</th><th></th></tr>')
    for prov, opts in res.items():
        for i, o in enumerate(opts):
            diff = (o["eur_month"] / best - 1) * 100 if best else 0
            add = (f'<a class="plus" href="szenario-hinzufuegen{qs({"provider": o["provider"], "key": o["key"]})}">＋</a>'
                   if o["key"] else "")
            gpu_cell = f'<td class="num">{o["gpu"]:g}</td>' if gpu else ""
            body.append(f'<tr><td>{e(plabel(prov)) if i == 0 else ""}</td><td>{e(o["label"])}</td>'
                        f'<td class="small">{e(t(o["kind"]))}</td><td>{e(o["region"])}</td><td>{souv_badge(o.get("souv"))}</td>'
                        f'<td class="num">{o["vcpu"]:g}</td><td class="num">{o["ram"]:g}</td>{gpu_cell}'
                        f'<td class="num"><b>{fmt_eur(o["eur_month"])}</b></td>{alt_cells(o["eur_month"], rates, curs)}'
                        f'<td class="num">{t("günstigste") if diff < 0.05 else fmt_pct(diff, True, 0)}</td><td>{add}</td></tr>')
    body.append("</table>")
    return layout(t("Vergleich"), "".join(body), "vergleich", config.BASE_PATH)


# ------------------------------------------------------------------ Szenarien
def page_scenarios(db: Database, csrf: str) -> bytes:
    with db.connect() as con:
        rows = con.execute("SELECT s.*, (SELECT COUNT(*) FROM scenario_items i WHERE i.scenario_id=s.id) n "
                           "FROM scenarios s ORDER BY s.id DESC").fetchall()
    body = [f'<h1>{t("Szenarien")}</h1><p class="small">{t("Ein Szenario ist ein Warenkorb mit Mengen (z. B. 10 VMs, 2.000 GB Storage, 5.000 GB Traffic). Positionen lassen sich in Varianten (A, B, …) gruppieren, um Anbieter zu vergleichen. Preise werden immer aus dem aktuellen Bestand berechnet. Mit Laufzeit und monatlichem Wachstum wird das Szenario hochgerechnet. Positionen fügt man über das ＋ in der Preistabelle oder im Vergleich hinzu.")}</p>']
    body.append(f"""<form class="filters" method="post" action="szenarien">{csrf_field(csrf)}
<label>{t("Name")}<input name="name" required placeholder="{t('z. B. Plattform 2027')}"></label>
<label>{t("Notiz")}<input name="note" size="40"></label><button class="primary">{t("Szenario anlegen")}</button></form>""")
    if rows:
        body.append(f'<table><tr><th>{t("Name")}</th><th>{t("Positionen")}</th><th>{t("Laufzeit")}</th>'
                    f'<th>{t("Wachstum")}</th><th>{t("Angelegt")}</th><th>{t("Notiz")}</th></tr>')
        for r in rows:
            body.append(f'<tr><td><a href="szenario/{r["id"]}">{e(r["name"])}</a></td><td class="num">{r["n"]}</td>'
                        f'<td class="num">{t("{m} Monate", m=r["months"])}</td>'
                        f'<td class="num">{fmt_pct(r["growth_pct"], dec=1)}/{t("Monat")}</td>'
                        f'<td class="small">{fmt_dt(r["created_at"])}</td><td class="small">{e(r["note"])}</td></tr>')
        body.append("</table>")
    return layout(t("Szenarien"), "".join(body), "szenarien", config.BASE_PATH)


def page_scenario(db: Database, sid: int, csrf: str) -> bytes | None:
    with db.connect() as con:
        s = con.execute("SELECT * FROM scenarios WHERE id=?", (sid,)).fetchone()
    if not s:
        return None
    lines = analysis.scenario_lines(db, sid)
    totals = analysis.scenario_totals(lines)
    rates, curs = fx.get_rates(db), fx.display_currencies()
    body = [f'<h1>{t("Szenario")}: {e(s["name"])}</h1><p class="small">{e(s["note"])}</p>']
    if totals:
        cheapest = min((x["sum"] for x in totals.values() if x["n"]), default=0)
        body.append('<div class="cards">' + "".join(
            f'<div class="card"><h3>{t("Variante {v}", v=e(v))}</h3><div class="n">{fmt_eur(x["sum"])}</div>'
            f'<div class="meta">{t("pro Monat (netto, normiert) · {n} Positionen", n=x["n"])}'
            + (f' · <span class="up">{t("unvollständig")}</span>' if x["incomplete"] else "")
            + ('' if x["sum"] > cheapest or len(totals) == 1 else f' · <b>{t("günstigste")}</b>') + '</div>'
            + fmt_alt(x["sum"], rates, curs) + '</div>'
            for v, x in totals.items()) + '</div>')

        # Hochrechnung
        proj = analysis.projection(lines, s["months"], s["growth_pct"])
        body.append(f'<h2>{t("Hochrechnung")}</h2><form class="filters" method="post" action="szenario/{sid}/einstellungen">'
                    f'{csrf_field(csrf)}<label>{t("Laufzeit (Monate)")}<input name="months" value="{s["months"]}" size="4"></label>'
                    f'<label>{t("Wachstum je Monat (%)")}<input name="growth_pct" value="{s["growth_pct"]:g}" size="5"></label>'
                    f'<button>{t("Übernehmen")}</button><span class="small">{t("Gilt für alle Positionen ohne eigenen Wert. Stückzahlen (VMs, Cluster) werden aufgerundet.")}</span></form>')
        months = proj["months"]
        series = {t("Variante {v}", v=v): [(m, val) for m, val in zip(months, p["monthly"])]
                  for v, p in proj["variants"].items()}
        step = max(1, len(months) // 12)
        if len(months) > 1:
            body.append(line_chart(series, x_labels=[(m, str(m)) for m in months[::step]]))
        body.append(f'<table style="margin-top:10px"><tr><th>{t("Variante")}</th><th>{t("Monat 1")}</th>'
                    f'<th>{t("Monat {m}", m=months[-1])}</th><th>{t("Summe über {m} Monate", m=months[-1])}</th>'
                    f'{alt_sum_head(curs)}<th>{t("Ø je Monat")}</th></tr>')
        for v, p in proj["variants"].items():
            body.append(f'<tr><td>{e(v)}{" <span class=up>(" + t("unvollständig") + ")</span>" if p["incomplete"] else ""}</td>'
                        f'<td class="num">{fmt_eur(p["first"])}</td><td class="num">{fmt_eur(p["last"])}</td>'
                        f'<td class="num"><b>{fmt_eur(p["total"])}</b></td>'
                        + "".join(f'<td class="num small">{fmt_money(p["total"] * rates[c], fx.SYMBOLS.get(c, c))}</td>'
                                  for c in curs if rates.get(c))
                        + f'<td class="num">{fmt_eur(p["total"] / len(months))}</td></tr>')
        body.append("</table>")

        body.append(f'<h2>{t("Positionen")}</h2><table><tr><th>{t("Variante")}</th><th>{t("Anbieter")}</th>'
                    f'<th>{t("Produkt")}</th><th>{t("Region")}</th><th>{t("Menge")} / {t("Wachstum %/Monat")}</th>'
                    f'<th>{t("Preis")}</th><th>{t("€/Monat")}</th><th>{t("Berechnung")}</th><th>{t("Status")}</th><th></th></tr>')
        for ln in lines:
            it, p = ln["item"], ln["price"] or {}
            status = (f'<span class="badge b-ok">{t("aktuell")}</span>' if ln["status"] == "aktuell"
                      else f'<span class="badge b-error">{e(t(ln["status"]))}</span>')
            growth = "" if it["growth_pct"] is None else f'{it["growth_pct"]:g}'
            body.append(
                f'<tr><td>{e(it["variant"])}</td><td>{e(plabel(it["provider"]))}</td>'
                f'<td>{e(p.get("product") or it["label"])}{"<br><span class=small>" + e(it["note"]) + "</span>" if it["note"] else ""}</td>'
                f'<td>{e(p.get("region", ""))}</td>'
                f'<td><form method="post" action="szenario/{sid}/position/{it["id"]}/aendern" class="row" style="gap:4px">'
                f'{csrf_field(csrf)}<input name="quantity" value="{it["quantity"]:g}" size="6" title="{t("Menge")}">'
                f'<input name="growth_pct" value="{growth}" size="4" placeholder="{s["growth_pct"]:g}" '
                f'title="{t("Wachstum %/Monat (leer = Szenario-Standard)")}"><button title="{t("Speichern")}">✓</button></form></td>'
                f'<td class="num">{fmt_price(p.get("price"))} {e(p.get("currency", ""))}<br><span class="small">{e(p.get("unit", ""))}</span></td>'
                f'<td class="num"><b>{fmt_eur(ln.get("eur_month"))}</b></td><td class="small">{e(ln.get("calc", ""))}</td>'
                f'<td>{status}</td><td><form method="post" action="szenario/{sid}/position/{it["id"]}/loeschen">'
                f'{csrf_field(csrf)}<button class="danger" title="{t("Position entfernen")}">×</button></form></td></tr>')
        body.append('</table>')
    else:
        body.append(f'<div class="empty">{t("Noch keine Positionen. Über das ＋ in der Preistabelle oder im Vergleich hinzufügen.")}</div>')
    body.append(f'<p style="margin-top:20px"><a class="btn" href="szenario/{sid}/export.csv">{t("CSV-Export")}</a> '
                f'<a class="btn" href="szenario/{sid}/export.xlsx">{t("Excel-Export (mit Hochrechnung)")}</a></p>'
                f'<form method="post" action="szenario/{sid}/loeschen" onsubmit="return confirm(\'{t("Szenario löschen?")}\')">'
                f'{csrf_field(csrf)}<button class="danger">{t("Szenario löschen")}</button></form>')
    return layout(s["name"], "".join(body), "szenarien", config.BASE_PATH)


def alt_sum_head(curs: list[str]) -> str:
    return "".join(f"<th>{e(c)}</th>" for c in curs)


def page_scenario_add(db: Database, params: dict, csrf: str) -> bytes:
    provider, key = params.get("provider", ""), params.get("key", "")
    with db.connect() as con:
        p = con.execute("SELECT * FROM prices_current WHERE provider=? AND key=?", (provider, key)).fetchone()
        scen = con.execute("SELECT id, name FROM scenarios ORDER BY id DESC").fetchall()
    if not p:
        return layout(t("Szenario"), f'<div class="empty">{t("Preis nicht gefunden.")}</div>', "szenarien", config.BASE_PATH)
    hint = t(analysis.QTY_HINT.get(p["unit_norm"], "")) or t("Menge in „{u}“", u=p["unit"])
    opts = "".join(f'<option value="{s["id"]}">{e(s["name"])}</option>' for s in scen)
    body = [f"""<h1>{t("Zu Szenario hinzufügen")}</h1><p><b>{e(plabel(provider))}</b> · {e(p['product'])} · {e(p['region'])}<br>
{fmt_price(p['price'])} {e(p['currency'])} {e(p['unit'])} · {t("normiert")} {fmt_eur(p['price_norm'])} /{e(t(p['unit_norm']) or '—')}</p>
<form class="filters" method="post" action="szenario-hinzufuegen">{csrf_field(csrf)}
<input type="hidden" name="provider" value="{e(provider)}"><input type="hidden" name="key" value="{e(key)}">
<label>{t("Szenario")}<select name="scenario_id">{opts}<option value="neu">➕ {t("neues Szenario …")}</option></select></label>
<label>{t("Name (bei neuem Szenario)")}<input name="name"></label>
<label>{t("Variante")}<input name="variant" value="A" size="4"></label>
<label>{t("Menge")} ({e(hint)})<input name="quantity" value="1" size="8"></label>
<label>{t("Wachstum %/Monat (leer = Szenario-Standard)")}<input name="growth_pct" size="5"></label>
<label>{t("Notiz")}<input name="note" size="30"></label><button class="primary">{t("Hinzufügen")}</button></form>"""]
    return layout(t("Zu Szenario hinzufügen"), "".join(body), "szenarien", config.BASE_PATH)


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
        return _csv(cols, cur, {"price", "new_price", "price_eur", "price_norm", "vcpu", "ram_gb", "gpu"})


def export_scenario_csv(db: Database, sid: int) -> bytes:
    rows = []
    for ln in analysis.scenario_lines(db, sid):
        p = ln["price"] or {}
        rows.append({"variante": ln["item"]["variant"], "anbieter": ln["item"]["provider"],
                     "produkt": p.get("product") or ln["item"]["label"], "region": p.get("region", ""),
                     "menge": ln["item"]["quantity"], "wachstum_prozent": ln["item"]["growth_pct"],
                     "preis": p.get("price"), "waehrung": p.get("currency", ""),
                     "einheit": p.get("unit", ""), "eur_monat": ln.get("eur_month"), "status": ln["status"],
                     "schluessel": ln["item"]["key"]})
    cols = ["variante", "anbieter", "produkt", "region", "menge", "wachstum_prozent", "preis", "waehrung", "einheit",
            "eur_monat", "status", "schluessel"]
    return _csv(cols, rows, {"menge", "wachstum_prozent", "preis", "eur_monat"})


def status_json(db: Database) -> bytes:
    return json.dumps({"providers": {p["name"]: {"label": p["label"], "running": p["running"], "count": p["count"],
                                                 "last_ok": p["last_ok"], "review": p["review"]}
                                     for p in provider_overview(db)}}).encode()
