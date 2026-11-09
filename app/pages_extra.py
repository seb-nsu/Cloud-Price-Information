"""Weitere Seiten: Kategorie-Vergleiche, Rabatte (Commitments), Preisänderungs-Bericht, Wechselkurse
sowie Excel- und PDF-Exporte."""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime

from . import analysis, compare, config, fx, report, sovereignty, xlsx
from .db import Database
from .i18n import t
from .models import CATEGORIES, PRICE_TYPES
from .pdf import Report
from .providers import PROVIDERS
from .ui import (_num, csrf_field, e, fmt_dt, fmt_eur, fmt_eur_fine, fmt_int, fmt_money, fmt_pct, fmt_price, int_param, layout,
                 line_chart, qs, souv_badge, tabs)


def plabel(name: str) -> str:
    return PROVIDERS[name].LABEL if name in PROVIDERS else name


def compare_tabs(active: str) -> str:
    items = [("vergleich", t("VMs"))] + [(f"vergleich/{k}", t(v.title)) for k, v in compare.KINDS.items()] + \
            [("vergleich/rabatte", t("Reservierung & Savings Plans"))]
    return tabs(items, active)


def _select(name: str, label: str, options: list[tuple[str, str]], params: dict, auto: bool = True) -> str:
    opts = "".join(f'<option value="{e(v)}" {"selected" if params.get(name, "") == v else ""}>{e(lbl)}</option>'
                   for v, lbl in options)
    onchange = ' onchange="this.form.submit()"' if auto else ""
    return f'<label>{e(label)}<select name="{name}"{onchange}>{opts}</select></label>'


def souv_select(params: dict) -> str:
    return _select("souv", t("Datenhoheit"), [(k, t(v)) for k, v in sovereignty.FILTERS.items()], params)


def provider_select(params: dict) -> str:
    return _select("provider", t("Anbieter"), [("", t("Alle"))] + [(k, m.LABEL) for k, m in PROVIDERS.items()], params)


def alt_currencies(db: Database) -> tuple[dict, list[str]]:
    return fx.get_rates(db), fx.display_currencies()


def alt_cells(eur: float | None, rates: dict, curs: list[str], fine: bool = False) -> str:
    f = fmt_eur_fine if fine else fmt_money
    return "".join(f'<td class="num small">{f(fx.convert(eur, c, rates), fx.SYMBOLS.get(c, c))}</td>'
                   for c in curs)


def alt_headers(curs: list[str], suffix: str = "") -> str:
    return "".join(f"<th>{e(c)}{e(suffix)}</th>" for c in curs)


# ------------------------------------------------------------------ Kategorie-Vergleiche
def page_compare_kind(db: Database, slug: str, params: dict) -> bytes | None:
    kind = compare.KINDS.get(slug)
    if not kind:
        return None
    res = compare.compare_category(db, kind, params)
    rates, curs = alt_currencies(db)
    unit = {"GB-Monat": t("€/GB-Monat"), "GB": t("€/GB"), "Monat": t("€/Monat")}.get(kind.unit_norm, "€")
    f = [f'<form class="filters" method="get" action="vergleich/{slug}">', provider_select(params)]
    if kind.types:
        f.append(_select("typ", t("Art"), [("", t("Alle"))] + [(k, t(lbl)) for k, lbl, _ in kind.types], params))
    if kind.classes:
        f.append(_select("klasse", t("Speicherklasse"), [("", t("Alle"))] + [(k, t(lbl)) for k, lbl, _ in kind.classes],
                         params))
    if kind.engines:
        f.append(_select("engine", t("Datenbank"), [("", t("Alle"))] + [(k, lbl) for k, lbl, _ in kind.engines], params))
    if kind.sizing:
        f.append(f'<label>{t("vCPU mind.")}<input name="vcpu" value="{e(params.get("vcpu", ""))}" size="4"></label>'
                 f'<label>{t("RAM mind. (GB)")}<input name="ram" value="{e(params.get("ram", ""))}" size="4"></label>')
    f.append(souv_select(params))
    f.append(_select("stufe", t("Staffelpreise"), [("guenstigste", t("günstigste Stufe")),
                                                   ("einstieg", t("Einstiegsstufe (höchster Preis)"))], params))
    f.append(f'<label>{t("Region enthält")}<input name="region" value="{e(params.get("region", ""))}" size="10"></label>'
             f'<label>{t("Suche")}<input name="q" value="{e(params.get("q", ""))}" size="14"></label>'
             f'<label>{t("Treffer je Anbieter")}<input name="top" value="{int_param(params, "top", 5, 1, 50)}" size="3">'
             f'</label><label class="check">'
             f'<input type="checkbox" name="kostenlos" value="1" {"checked" if params.get("kostenlos") else ""}>'
             f'{t("inkl. kostenloser Kontingente")}</label><button class="primary">{t("Vergleichen")}</button></form>')
    body = [f'<h1>{t("Vergleich")}: {t(kind.title)}</h1>', compare_tabs(f"vergleich/{slug}"),
            f'<p class="small">{t(kind.intro)} {t("Nur On-Demand-Preise, netto, in EUR normiert.")}</p>', "".join(f)]
    if not res["providers"]:
        body.append(f'<div class="empty">{t("Keine passenden Preise gefunden. Sind die Anbieter schon abgerufen?")}</div>')
        return layout(t(kind.title), "".join(body), "vergleich", config.BASE_PATH)
    best = res["best"] or 0
    extra = (f'<th>{t("vCPU")}</th><th>{t("RAM")}</th>' if kind.sizing else "")
    body.append(f'<p class="small">{t("{n} Produkte gefunden, je Anbieter die günstigsten.", n=fmt_int(res["n"]))}</p>'
                f'<table><tr><th>{t("Anbieter")}</th><th>{t("Produkt")}</th><th>{t("Art")}</th><th>{t("Region")}</th>'
                f'<th>{t("Datenhoheit")}</th>{extra}<th>{e(unit)}</th>{alt_headers(curs)}<th>{t("vs. günstigste")}</th>'
                f'<th>{t("Listenpreis")}</th><th></th></tr>')
    for prov, rows in res["providers"].items():
        for i, r in enumerate(rows):
            diff = (r["eur"] / best - 1) * 100 if best else 0
            kind_lbl = " · ".join(t(x) for x in (r["type"], r["class"], r["engine"]) if x)
            sizing = "".join(f'<td class="num">{v:g}</td>' if v else '<td class="small">—</td>'
                             for v in (r["vcpu"], r["ram"])) if kind.sizing else ""
            add = f'<a class="plus" href="szenario-hinzufuegen{qs({"provider": r["provider"], "key": r["key"]})}">＋</a>'
            body.append(
                f'<tr><td>{e(plabel(prov)) if i == 0 else ""}</td><td title="{e(r["key"])}">{e(r["product"])}'
                f'<br><span class="small">{e(r["service"])}</span></td><td class="small">{e(kind_lbl)}</td>'
                f'<td>{e(r["region"])}</td><td>{souv_badge(r["souv"])}</td>{sizing}'
                f'<td class="num"><b>{fmt_eur_fine(r["eur"])}</b></td>{alt_cells(r["eur"], rates, curs, True)}'
                f'<td class="num">{t("günstigste") if diff < 0.05 else fmt_pct(diff, True, 0)}</td>'
                f'<td class="num small">{fmt_price(r["price"])} {e(r["currency"])}<br>{e(r["unit"])}</td>'
                f'<td>{add}</td></tr>')
    body.append("</table>")
    return layout(t(kind.title), "".join(body), "vergleich", config.BASE_PATH)


# ------------------------------------------------------------------ Rabatte
def page_commitments(db: Database, params: dict) -> bytes:
    rows = compare.commitments(db, params)
    summary = compare.commitment_summary(rows)
    page = int_param(params, "seite")
    size = 100
    commit_types = [pt for pt in PRICE_TYPES if pt in compare.COMMIT_TYPES]
    body = [f'<h1>{t("Vergleich")}: {t("Reservierung & Savings Plans")}</h1>', compare_tabs("vergleich/rabatte"),
            f'<p class="small">{t("Reservierungen, Commitments und Savings Plans im Vergleich zum On-Demand-Preis desselben Produkts. Der effektive Preis enthält eine Vorauszahlung anteilig über die Laufzeit. Break-even: ab dieser Auslastung (Anteil der Laufzeit, in der die Ressource läuft) ist die Bindung günstiger als On-Demand. Amortisation: Monate, bis sich eine Vorauszahlung bei Dauerbetrieb bezahlt gemacht hat.")}</p>',
            f'<form class="filters" method="get" action="vergleich/rabatte">{provider_select(params)}',
            _select("category", t("Kategorie"), [("", t("Alle"))] + [(c, t(c)) for c in CATEGORIES], params),
            _select("price_type", t("Preistyp"), [("", t("Alle"))] + [(p, t(p)) for p in commit_types], params),
            souv_select(params),
            f'<label>{t("Ersparnis mind. (%)")}<input name="min" value="{e(params.get("min", ""))}" size="4"></label>'
            f'<label>{t("Suche")}<input name="q" value="{e(params.get("q", ""))}" size="14"></label>'
            f'<button class="primary">{t("Filtern")}</button>'
            f'<a class="btn" href="vergleich/rabatte.xlsx{qs(params, seite=None)}">{t("Excel-Export")}</a></form>']
    if not rows:
        hint = t('Keine Reservierungs- oder Savings-Plan-Preise mit passendem On-Demand-Preis gefunden. Diese Preistypen müssen in config.toml aktiviert sein (z. B. Azure price_types = ["Consumption", "Reservation"], savings_plan = true; AWS term_types = ["OnDemand", "Reserved"]; Google usage_types mit Commit1Yr/Commit3Yr).')
        body.append(f'<div class="empty">{hint}</div>')
        return layout(t("Reservierung & Savings Plans"), "".join(body), "vergleich", config.BASE_PATH)
    body.append('<div class="cards">' + "".join(
        f'<div class="card"><h3>{e(plabel(s["provider"]))}</h3><div class="info">{e(t(s["price_type"]))}</div>'
        f'<div class="n">{fmt_pct(s["avg"], dec=0)}</div><div class="meta">'
        f'{t("Ø Ersparnis über {n} Produkte, max. {m}", n=fmt_int(s["n"]), m=fmt_pct(s["max"], dec=0))}</div></div>'
        for s in summary) + '</div>')

    def th(label, key):
        cur = params.get("sort", "-ersparnis")
        nxt = f"-{key}" if cur == key else key
        arrow = " ▲" if cur == key else (" ▼" if cur == f"-{key}" else "")
        return f'<th><a href="vergleich/rabatte{qs(params, sort=nxt, seite=None)}">{label}{arrow}</a></th>'

    body.append(f'<h2>{t("{n} Gegenüberstellungen", n=fmt_int(len(rows)))}</h2><table><tr>{th(t("Anbieter"), "anbieter")}'
                f'<th>{t("Produkt")}</th><th>{t("Region")}</th><th>{t("Preistyp")}</th>{th(t("On-Demand €/Monat"), "od")}'
                f'{th(t("Effektiv €/Monat"), "commit")}<th>{t("Vorauszahlung")}</th>{th(t("Ersparnis"), "ersparnis")}'
                f'<th title="{t("Ab dieser Auslastung lohnt sich die Bindung")}">{t("Break-even")}</th>'
                f'<th>{t("Amortisation")}</th><th>{t("Datenhoheit")}</th></tr>')
    for r in rows[(page - 1) * size: page * size]:
        amort = t("{m} Monate", m=_num(f"{r['amort_months']:.1f}")) \
            if r["amort_months"] else (t("sofort") if not r["upfront"] else "—")
        cls = "down" if r["saving_pct"] > 0 else "up"
        body.append(
            f'<tr><td>{e(plabel(r["provider"]))}</td><td title="{e(r["commit_product"])}">{e(r["product"])}'
            f'<br><span class="small">{e(r["category"])}</span></td><td>{e(r["region"])}</td>'
            f'<td class="small">{e(t(r["price_type"]))}</td><td class="num">{fmt_eur(r["od_month"])}</td>'
            f'<td class="num"><b>{fmt_eur(r["eff_month"])}</b></td>'
            f'<td class="num">{fmt_eur(r["upfront"]) if r["upfront"] else "—"}</td>'
            f'<td class="num"><span class="{cls}">{fmt_pct(r["saving_pct"], dec=0)}</span></td>'
            f'<td class="num">{fmt_pct(r["break_even_pct"], dec=0)}</td><td class="num small">{amort}</td>'
            f'<td>{souv_badge(r["souv"])}</td></tr>')
    body.append("</table>")
    from .ui import pager
    body.append(pager(params, page, len(rows), size, "vergleich/rabatte"))
    return layout(t("Reservierung & Savings Plans"), "".join(body), "vergleich", config.BASE_PATH)


def export_commitments_xlsx(db: Database, params: dict) -> bytes:
    rows = compare.commitments(db, params)
    header = [t("Anbieter"), t("Produkt"), t("Kategorie"), t("Region"), t("Preistyp"), t("Laufzeit (Monate)"),
              t("On-Demand €/Monat"), t("Effektiv €/Monat"), t("Laufend €/Monat"), t("Vorauszahlung €"),
              t("Gesamt über Laufzeit €"), t("Ersparnis %"), t("Break-even-Auslastung %"), t("Amortisation (Monate)"),
              t("Datenhoheit"), t("Schlüssel"), t("Schlüssel On-Demand")]
    data = ([plabel(r["provider"]), r["product"], t(r["category"]), r["region"], t(r["price_type"]), r["months"],
             r["od_month"], r["eff_month"], r["recurring_month"], r["upfront"], r["total"], r["saving_pct"],
             r["break_even_pct"], r["amort_months"], t(r["souv"]["label"]), r["key"], r["od_key"]] for r in rows)
    return xlsx.single_sheet(t("Rabatte"), header, data, widths=[18, 50, 12, 18, 18, 10, 14, 14, 14, 14, 16, 10, 12,
                                                                 12, 22, 30, 30],
                             number_formats={6: "eur", 7: "eur", 8: "eur", 9: "eur", 10: "eur", 11: "pct", 12: "pct",
                                             13: "num"})


# ------------------------------------------------------------------ Bericht
def _report_params(params: dict) -> tuple[str, str, str]:
    return params.get("zeitraum", ""), params.get("provider", ""), params.get("category", "")


def page_report(db: Database, params: dict) -> bytes:
    period, prov, cat = _report_params(params)
    rep = report.build(db, period, prov, cat, top=int_param(params, "top", 20, 5, 200))
    opts = report.period_options(db)
    cur_value = period or opts[0][0]
    sel = "".join(f'<option value="{e(v)}" {"selected" if v == cur_value else ""}>{e(lbl)}</option>' for v, lbl in opts)
    exp = qs({"zeitraum": cur_value, "provider": prov, "category": cat})
    body = [f'<h1>{t("Preisänderungs-Bericht")} {e(rep["label"])}</h1>',
            f'<p class="small">{t("Übernommene Preisänderungen im Zeitraum {a} bis {b} (Zeitpunkt der Übernahme). Erstimporte zählen nicht als neu.", a=_day(rep["start"]), b=_day(rep["end"], -1))}</p>',
            f'<form class="filters" method="get" action="bericht"><label>{t("Zeitraum")}<select name="zeitraum" '
            f'onchange="this.form.submit()">{sel}</select></label>{provider_select(params)}',
            _select("category", t("Kategorie"), [("", t("Alle"))] + [(c, t(c)) for c in CATEGORIES], params),
            f'<label>{t("Top")}<input name="top" value="{int_param(params, "top", 20, 5, 200)}" size="3"></label>'
            f'<button class="primary">{t("Anzeigen")}</button>'
            f'<a class="btn" href="bericht.pdf{exp}">PDF</a><a class="btn" href="bericht.xlsx{exp}">Excel</a>'
            f'<a class="btn" href="bericht.csv{exp}">CSV</a></form>']
    c = rep["counts"]
    body.append('<div class="cards">' + "".join(
        f'<div class="card"><h3>{e(t(lbl))}</h3><div class="n">{fmt_int(n)}</div></div>'
        for lbl, n in (("Preisänderungen", c["geändert"]), ("neu", c["neu"]), ("entfallen", c["entfallen"]))) + "</div>")
    if not rep["rows"]:
        body.append(f'<div class="empty">{t("Keine übernommenen Preisänderungen in diesem Zeitraum.")}</div>')
        return layout(t("Bericht"), "".join(body), "bericht", config.BASE_PATH)
    body.append(f'<h2>{t("Übersicht je Anbieter")}</h2><table><tr><th>{t("Anbieter")}</th><th>{t("Erhöhungen")}</th>'
                f'<th>{t("Senkungen")}</th><th>{t("Median Δ")}</th><th>{t("Größte Erhöhung")}</th><th>{t("neu")}</th>'
                f'<th>{t("entfallen")}</th></tr>')
    for p, s in rep["summary"].items():
        body.append(f'<tr><td>{e(plabel(p))}</td><td class="num up">{s["up"]}</td><td class="num down">{s["down"]}</td>'
                    f'<td class="num">{fmt_pct(s["median"], True)}</td><td class="num">{fmt_pct(s["max_up"], True)}</td>'
                    f'<td class="num">{s["neu"]}</td><td class="num">{s["entfallen"]}</td></tr>')
    body.append("</table>")
    for title, key in (("Top-Erhöhungen", "increases"), ("Top-Senkungen", "decreases")):
        body.append(f'<h2>{t(title)}</h2>')
        if not rep[key]:
            body.append(f'<div class="empty">{t("Keine.")}</div>')
            continue
        body.append(_change_table(rep[key], True))
    for title, key in (("Neu", "new"), ("Entfallen", "removed")):
        if rep[key]:
            body.append(f'<h2>{t(title)}</h2>' + _change_table(rep[key], False))
    return layout(t("Bericht"), "".join(body), "bericht", config.BASE_PATH)


def _day(iso: str, offset: int = 0) -> str:
    d = date.fromordinal(date.fromisoformat(iso[:10]).toordinal() + offset)
    return fmt_dt(d.isoformat() + "T12:00:00")[:10]


def _change_table(rows: list[dict], with_delta: bool) -> str:
    out = [f'<table><tr><th>{t("Datum")}</th><th>{t("Anbieter")}</th><th>{t("Kategorie")}</th><th>{t("Produkt")}</th>'
           f'<th>{t("Region")}</th><th>{t("Alt")}</th><th>{t("Neu")}</th>' + (f'<th>Δ</th>' if with_delta else "")
           + f'<th>{t("Einheit")}</th><th></th></tr>']
    for r in rows:
        delta = f'<td class="num"><span class="{"up" if (r["pct"] or 0) > 0 else "down"}">{fmt_pct(r["pct"], True)}</span></td>' \
            if with_delta else ""
        out.append(f'<tr><td class="small">{fmt_dt(r["changed_at"])}</td><td>{e(plabel(r["provider"]))}</td>'
                   f'<td>{e(t(r["category"]))}</td><td title="{e(r["key"])}">{e(r["product"])}</td><td>{e(r["region"])}</td>'
                   f'<td class="num">{fmt_price(r["old_price"])}</td><td class="num">{fmt_price(r["new_price"])} '
                   f'{e(r["currency"])}</td>{delta}<td class="small">{e(r["unit"])}</td>'
                   f'<td class="small"><a href="lauf/{r["run_id"]}">#{r["run_id"]}</a> · '
                   f'<a href="verlauf{qs({"provider": r["provider"], "key": r["key"]})}">{t("Verlauf")}</a></td></tr>')
    out.append("</table>")
    return "".join(out)


REPORT_COLS = ["changed_at", "provider", "change", "category", "product", "region", "old_price", "new_price", "pct",
               "currency", "unit", "key", "run_id"]


def _report_header() -> list[str]:
    return [t("Datum"), t("Anbieter"), t("Art"), t("Kategorie"), t("Produkt"), t("Region"), t("Alter Preis"),
            t("Neuer Preis"), t("Änderung %"), t("Währung"), t("Einheit"), t("Schlüssel"), t("Abruf")]


def _report_row(r: dict) -> list:
    return [r["changed_at"], plabel(r["provider"]), t(r["change"]), t(r["category"]), r["product"], r["region"],
            r["old_price"], r["new_price"], r["pct"], r["currency"], r["unit"], r["key"], r["run_id"]]


def export_report_csv(db: Database, params: dict) -> bytes:
    from .pages import _safe_cell
    period, prov, cat = _report_params(params)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(_report_header())
    for r in report.changes(db, period, prov, cat):
        w.writerow([str(round(v, 10)).replace(".", ",") if isinstance(v, float) else _safe_cell(v)
                    for v in _report_row(r)])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def export_report_xlsx(db: Database, params: dict) -> bytes:
    period, prov, cat = _report_params(params)
    rep = report.build(db, period, prov, cat, top=int_param(params, "top", 50, 5, 500))
    wb = xlsx.Workbook()
    wb.add_sheet(t("Übersicht"), [t("Anbieter"), t("Erhöhungen"), t("Senkungen"), t("Median Δ %"),
                                  t("Größte Erhöhung %"), t("neu"), t("entfallen")],
                 ([plabel(p), s["up"], s["down"], s["median"], s["max_up"], s["neu"], s["entfallen"]]
                  for p, s in rep["summary"].items()), widths=[22, 12, 12, 12, 16, 8, 10],
                 number_formats={3: "pct", 4: "pct"})
    fmts = {6: "num", 7: "num", 8: "pct"}
    widths = [20, 18, 12, 12, 60, 16, 12, 12, 10, 8, 18, 30, 8]
    wb.add_sheet(t("Top-Erhöhungen"), _report_header(), (_report_row(r) for r in rep["increases"]), widths, fmts)
    wb.add_sheet(t("Top-Senkungen"), _report_header(), (_report_row(r) for r in rep["decreases"]), widths, fmts)
    wb.add_sheet(t("Alle Änderungen"), _report_header(), (_report_row(r) for r in rep["rows"]), widths, fmts)
    return wb.to_bytes()


def export_report_pdf(db: Database, params: dict) -> bytes:
    period, prov, cat = _report_params(params)
    rep = report.build(db, period, prov, cat, top=int_param(params, "top", 25, 5, 200))
    title = t("Preisänderungs-Bericht") + f" {rep['label']}"
    pdf = Report(title, footer=t("Preisverwaltung · erstellt {d}", d=fmt_dt(datetime.now().astimezone().isoformat())))
    pdf.heading(title, 18)
    scope = ", ".join(x for x in (plabel(prov) if prov else "", t(cat) if cat else "") if x) or t("alle Anbieter")
    c = rep["counts"]
    pdf.paragraph(t("Zeitraum {a} bis {b} · {s} · {g} Preisänderungen, {n} neu, {r} entfallen. Netto-Listenpreise in "
                    "Originalwährung; Änderung in Prozent gegenüber dem vorherigen Preis.",
                    a=_day(rep["start"]), b=_day(rep["end"], -1), s=scope, g=c["geändert"], n=c["neu"],
                    r=c["entfallen"]))
    if rep["summary"]:
        pdf.heading(t("Übersicht je Anbieter"), 12)
        pdf.table([t("Anbieter"), t("Erhöhungen"), t("Senkungen"), t("Median Δ"), t("Größte Erhöhung"), t("neu"),
                   t("entfallen")],
                  [[plabel(p), s["up"], s["down"], fmt_pct(s["median"], True), fmt_pct(s["max_up"], True), s["neu"],
                    s["entfallen"]] for p, s in rep["summary"].items()], [3, 1.3, 1.3, 1.3, 1.6, 1, 1], "lrrrrrr")
    for title, key in (("Top-Erhöhungen", "increases"), ("Top-Senkungen", "decreases")):
        if not rep[key]:
            continue
        pdf.heading(t(title), 12)
        rows = [[fmt_dt(r["changed_at"])[:10], plabel(r["provider"]), r["product"], r["region"],
                 fmt_price(r["old_price"]), fmt_price(r["new_price"]) + f" {r['currency']}", fmt_pct(r["pct"], True),
                 r["unit"]] for r in rep[key]]
        colors = [((0.77, 0.2, 0.04) if (r["pct"] or 0) > 0 else (0.07, 0.5, 0.36),) for r in rep[key]]
        pdf.table([t("Datum"), t("Anbieter"), t("Produkt"), t("Region"), t("Alt"), t("Neu"), "Δ", t("Einheit")], rows,
                  [1.2, 1.8, 6, 1.4, 1.2, 1.5, 1, 1.8], "lllllrrl",
                  colors=[[None] * 6 + [col[0]] for col in colors])
    for title, key in (("Entfallen", "removed"), ("Neu", "new")):
        if rep[key]:
            pdf.heading(t(title), 12)
            pdf.table([t("Datum"), t("Anbieter"), t("Produkt"), t("Region"), t("Preis"), t("Einheit")],
                      [[fmt_dt(r["changed_at"])[:10], plabel(r["provider"]), r["product"], r["region"],
                        fmt_price(r["old_price"] if r["change"] == "entfallen" else r["new_price"]) + f" {r['currency']}",
                        r["unit"]] for r in rep[key]], [1.2, 1.8, 7, 1.4, 1.6, 2], "llllrl")
    if not rep["rows"]:
        pdf.paragraph(t("Keine übernommenen Preisänderungen in diesem Zeitraum."))
    return pdf.to_bytes()


# ------------------------------------------------------------------ Wechselkurse
def page_rates(db: Database, params: dict, csrf: str, flash: str = "") -> bytes:
    rates = fx.get_rates(db)
    with db.connect() as con:
        cur = [dict(r) for r in con.execute("SELECT * FROM fx_rates ORDER BY currency")]
        span = con.execute("SELECT MIN(rate_date), MAX(rate_date), COUNT(DISTINCT rate_date) FROM fx_history").fetchone()
    shown = fx.display_currencies() or ["USD"]
    days = int_param(params, "tage", 365, 7, 20000)
    since = date.fromordinal(date.today().toordinal() - days).isoformat()
    body = [f'<h1>{t("Wechselkurse")}</h1>']
    if flash:
        body.append(f'<div class="flash">{e(flash)}</div>')
    body.append(f'<p class="small">{t("EZB-Referenzkurse (Einheiten je 1 €). USD-Preise (z. B. AWS) werden mit dem jeweils aktuellen Kurs in EUR umgerechnet; im Preisverlauf zusätzlich mit dem Kurs zum damaligen Zeitpunkt. Weitere Anzeigewährungen: [waehrung] anzeige in config.toml.")}</p>')
    body.append(f'<p class="small">{t("Kursverlauf gespeichert: {a} bis {b} ({n} Handelstage)", a=e(span[0] or "—"), b=e(span[1] or "—"), n=fmt_int(span[2]))}</p>')
    body.append(f'<div class="row noprint" style="margin-bottom:12px"><form method="post" action="kurse">{csrf_field(csrf)}'
                f'<button>{t("Aktuelle Kurse laden")}</button></form><form method="post" action="kurse/verlauf">'
                f'{csrf_field(csrf)}<button>{t("Vollständigen Kursverlauf seit 1999 laden")}</button></form>'
                + "".join(f'<a class="btn {"primary" if days == d else ""}" href="kurse?tage={d}">{lbl}</a>'
                          for d, lbl in ((90, t("90 Tage")), (365, t("1 Jahr")), (1825, t("5 Jahre")))) + '</div>')
    for c in shown:
        pts = fx.history_points(db, c, since)
        if len(pts) > 1:
            base = date.fromisoformat(pts[0][0]).toordinal()
            step = max(1, len(pts) // 6)
            labels = [(date.fromisoformat(d).toordinal() - base, fmt_dt(d + "T00:00:00")[:10]) for d, _ in pts[::step]]
            body.append(f'<h2>{e(c)} {t("je 1 €")}</h2>' + line_chart(
                {c: [(date.fromisoformat(d).toordinal() - base, v) for d, v in pts]}, x_labels=labels,
                y_fmt=lambda v: _num(f"{v:.3f}"), zero=False))
    if cur:
        body.append(f'<h2>{t("Aktuelle Kurse")}</h2><table><tr><th>{t("Währung")}</th><th>{t("Kurs je 1 €")}</th>'
                    f'<th>{t("Stand")}</th><th>{t("Quelle")}</th></tr>')
        for r in cur:
            body.append(f'<tr><td>{e(r["currency"])}</td><td class="num">{fmt_price(r["rate"], 4)}</td>'
                        f'<td>{e(r["rate_date"])}</td><td class="small">{e(r["source"])}</td></tr>')
        body.append("</table>")
    else:
        fb = ", ".join(f"{k} {fmt_price(v, 4)}" for k, v in rates.items() if k != "EUR")
        body.append(f'<div class="empty">{t("Noch keine EZB-Kurse geladen. Fallback-Kurse")}: {e(fb)}</div>')
    return layout(t("Wechselkurse"), "".join(body), "", config.BASE_PATH)


# ------------------------------------------------------------------ Excel-Exporte
NUMERIC = {"price", "new_price", "price_eur", "price_norm", "vcpu", "ram_gb", "gpu"}


def export_table_xlsx(db: Database, table: str, params: dict) -> bytes:
    from .pages import where_clause
    where, args = where_clause(params, ("run_id",) if table == "prices_archive" else ())
    with db.connect() as con:
        cur = con.execute(f"SELECT * FROM {table}{where} ORDER BY provider, category, product", args)
        cols = [d[0] for d in cur.description if d[0] != "id"]
        rates, curs = alt_currencies(db)
        extra = [c for c in curs if rates.get(c)] if table == "prices_current" else []
        header = cols + [f"price_norm_{c.lower()}" for c in extra]
        fmts = {i: "num" for i, c in enumerate(cols) if c in NUMERIC}

        def rows():
            for r in cur:
                vals = [r[c] for c in cols]
                yield vals + [r["price_norm"] * rates[c] if r["price_norm"] is not None else None for c in extra]
        name = t("Aktuelle Preise") if table == "prices_current" else t("Archiv")
        return xlsx.single_sheet(name, header, rows(),
                                 widths=[min(60, max(10, len(c) + 4)) if c not in ("product", "key", "attributes")
                                         else 50 for c in header], number_formats=fmts)


def export_scenario_xlsx(db: Database, sid: int) -> bytes | None:
    with db.connect() as con:
        s = con.execute("SELECT * FROM scenarios WHERE id=?", (sid,)).fetchone()
    if not s:
        return None
    lines = analysis.scenario_lines(db, sid)
    proj = analysis.projection(lines, s["months"], s["growth_pct"])
    wb = xlsx.Workbook()
    wb.add_sheet(t("Positionen"), [t("Variante"), t("Anbieter"), t("Produkt"), t("Region"), t("Menge"),
                                   t("Wachstum %/Monat"), t("Preis"), t("Währung"), t("Einheit"), t("€/Monat"),
                                   t("Status"), t("Schlüssel")],
                 ([ln["item"]["variant"], plabel(ln["item"]["provider"]),
                   (ln["price"] or {}).get("product") or ln["item"]["label"], (ln["price"] or {}).get("region", ""),
                   ln["item"]["quantity"],
                   s["growth_pct"] if ln["item"]["growth_pct"] is None else ln["item"]["growth_pct"],
                   (ln["price"] or {}).get("price"), (ln["price"] or {}).get("currency", ""),
                   (ln["price"] or {}).get("unit", ""), ln.get("eur_month"), t(ln["status"]), ln["item"]["key"]]
                  for ln in lines), widths=[9, 18, 50, 16, 10, 12, 12, 8, 18, 14, 14, 30],
                 number_formats={4: "num", 5: "pct", 6: "num", 9: "eur"})
    variants = list(proj["variants"])
    wb.add_sheet(t("Hochrechnung"), [t("Monat")] + [t("Variante {v}", v=v) for v in variants],
                 ([m] + [proj["variants"][v]["monthly"][m - 1] for v in variants] for m in proj["months"]),
                 widths=[8] + [16] * len(variants), number_formats={i + 1: "eur" for i in range(len(variants))})
    return wb.to_bytes()


def json_bytes(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
