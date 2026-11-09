"""Gemeinsame Bausteine der Oberfläche: Layout, Formatierung, CSS/JS."""
from __future__ import annotations

import math
import urllib.parse
from datetime import datetime
from html import escape

from .i18n import get_lang, t

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/Berlin")
except Exception:  # tzdata fehlt -> UTC anzeigen
    LOCAL_TZ = None

PAGE_SIZE = 100


def e(v) -> str:
    return escape("" if v is None else str(v))


def _de() -> bool:
    return get_lang() != "en"


def _num(s: str) -> str:
    """Formatierte Zahl im Python-Format (1,234.5) in die Anzeigesprache umwandeln."""
    return s.replace(",", "X").replace(".", ",").replace("X", ".") if _de() else s


def fmt_int(n: int | None) -> str:
    return _num(f"{n or 0:,}")


def fmt_price(v: float | None, min_dec: int = 2) -> str:
    """Deutsches Zahlenformat; kleine Werte mit genügend signifikanten Stellen (nie „0,00“ für > 0)."""
    if v is None:
        return "—"
    if v == 0:
        return ("0," if _de() else "0.") + "0" * min_dec
    a = abs(v)
    dec = 6 if a >= 1e-4 else min(12, -int(math.floor(math.log10(a))) + 3)
    s = f"{v:,.{dec}f}".rstrip("0")
    int_part, _, frac = s.partition(".")
    frac = (frac + "0" * min_dec)[:max(min_dec, len(frac))]
    return f"{int_part.replace(',', '.')},{frac}" if _de() else f"{int_part}.{frac}"


def fmt_money(v: float | None, symbol: str = "€") -> str:
    if v is None:
        return "—"
    if abs(v) >= 1000:
        return _num(f"{v:,.0f}") + f" {symbol}"
    if abs(v) >= 0.01:
        return _num(f"{v:,.2f}") + f" {symbol}"
    return fmt_price(v) + f" {symbol}"


def fmt_eur(v: float | None) -> str:
    return fmt_money(v, "€")


def fmt_eur_fine(v: float | None, symbol: str = "€") -> str:
    """Für Stückpreise (z. B. €/GB): kleine Werte mit genügend Nachkommastellen."""
    if v is None:
        return "—"
    return fmt_money(v, symbol) if abs(v) >= 1 else fmt_price(v) + f" {symbol}"


def fmt_pct(v: float | None, signed: bool = False, dec: int = 1) -> str:
    if v is None:
        return "—"
    return _num(f"{v:+.{dec}f}" if signed else f"{v:.{dec}f}") + " %"


def fmt_alt(eur: float | None, rates: dict, currencies: list[str]) -> str:
    """Zusatzanzeige in weiteren Währungen (z. B. „≈ 12,34 $“)."""
    if eur is None or not currencies:
        return ""
    from .fx import SYMBOLS
    parts = [fmt_money(eur * rates[c], SYMBOLS.get(c, c)) for c in currencies if rates.get(c)]
    return f'<br><span class="small alt">≈ {" · ".join(parts)}</span>' if parts else ""


def fmt_dt(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
        if LOCAL_TZ and dt.tzinfo:
            dt = dt.astimezone(LOCAL_TZ)
        return dt.strftime("%d.%m.%Y %H:%M" if _de() else "%Y-%m-%d %H:%M")
    except ValueError:
        return e(iso)


def qs(params: dict, **override) -> str:
    p = {k: v for k, v in {**params, **override}.items() if v not in (None, "")}
    return "?" + urllib.parse.urlencode(p) if p else ""


def int_param(params: dict, name: str, default: int = 1, lo: int = 1, hi: int = 10**9) -> int:
    try:
        return max(lo, min(hi, int(params.get(name, default) or default)))
    except (TypeError, ValueError):
        return default


def float_param(params: dict, name: str, default: float) -> float:
    try:
        v = float(str(params.get(name, default)).replace(",", "."))
        return v if math.isfinite(v) and v >= 0 else default
    except (TypeError, ValueError):
        return default


def pager(params: dict, page: int, total: int, size: int = PAGE_SIZE, path: str = "") -> str:
    pages = max(1, (total + size - 1) // size)
    if pages == 1:
        return ""
    href = path or "./"
    prev = f'<a class="btn" href="{href}{qs(params, seite=page - 1)}">‹ {t("zurück")}</a>' if page > 1 else ""
    nxt = f'<a class="btn" href="{href}{qs(params, seite=page + 1)}">{t("weiter")} ›</a>' if page < pages else ""
    label = t("Seite {page} von {pages}", page=page, pages=pages)
    return f'<div class="pager">{prev}<span class="small">{label}</span>{nxt}</div>'


def csrf_field(token: str) -> str:
    return f'<input type="hidden" name="csrf" value="{e(token)}">'


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1d2330;--muted:#667085;--line:#e4e7ec;--acc:#1f5eff;
--ok:#12805c;--err:#c4320a;--warn:#b54708;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
background:var(--bg);color:var(--fg)}a{color:var(--acc)}
header{background:#101828;color:#fff;padding:0 24px;display:flex;align-items:center;gap:28px;height:56px}
header b{font-size:16px;letter-spacing:.2px}header nav a{color:#cfd4dc;text-decoration:none;margin-right:18px}
header nav a.on,header nav a:hover{color:#fff}main{padding:20px 24px;max-width:1500px;margin:0 auto}
h1{font-size:20px;margin:4px 0 14px}h2{font-size:15px;margin:22px 0 10px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.card h3{margin:0 0 2px;font-size:14px}.card .info{color:var(--muted);font-size:12px;min-height:32px}
.card .n{font-size:22px;font-weight:600;margin:6px 0 2px}.card .meta{font-size:12px;color:var(--muted)}
.badge{display:inline-block;border-radius:99px;padding:1px 8px;font-size:11px;font-weight:600;white-space:nowrap}
.b-ok{background:#ecfdf3;color:var(--ok)}.b-error{background:#fef3f2;color:var(--err)}
.b-skipped,.b-verworfen{background:#fffaeb;color:var(--warn)}.b-running{background:#eff4ff;color:var(--acc)}
.b-review{background:#fdf2fa;color:#c11574}.b-none{background:#f2f4f7;color:var(--muted)}
.b-neu{background:#ecfdf3;color:var(--ok)}.b-geändert{background:#eff4ff;color:var(--acc)}
.b-entfallen{background:#fef3f2;color:var(--err)}.b-umbenannt{background:#f2f4f7;color:var(--muted)}
button,.btn{font:inherit;border:1px solid var(--line);background:#fff;border-radius:7px;padding:5px 11px;
cursor:pointer;color:var(--fg);text-decoration:none;display:inline-block}
button.primary,.btn.primary{background:var(--acc);border-color:var(--acc);color:#fff}
button.danger{border-color:#fecdca;color:var(--err)}button:disabled{opacity:.55;cursor:default}
.card form{margin-top:10px;display:inline-block}
.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:end;background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:12px;margin-bottom:12px}
.filters label{display:flex;flex-direction:column;font-size:12px;color:var(--muted);gap:3px}
.filters label.check{flex-direction:row;align-items:center;gap:6px;color:var(--fg)}
.filters label.check input{min-width:0}
.filters input,.filters select{font:inherit;padding:5px 7px;border:1px solid var(--line);border-radius:6px;min-width:110px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:#f9fafb;font-size:12px;color:var(--muted);font-weight:600;white-space:nowrap}
th a{color:inherit;text-decoration:none}td.num{text-align:right;font-family:var(--mono);white-space:nowrap}
td.small,.small{font-size:12px;color:var(--muted)}tr:hover td{background:#fafbff}
.pager{display:flex;gap:8px;align-items:center;margin:12px 0}.flash{background:#eff4ff;border:1px solid #c7d7fe;
padding:9px 12px;border-radius:8px;margin-bottom:12px}.warnbox{background:#fffaeb;border:1px solid #fedf89;
padding:9px 12px;border-radius:8px;margin-bottom:12px}.errbox{background:#fef3f2;border:1px solid #fecdca;
padding:9px 12px;border-radius:8px;margin-bottom:12px}.up{color:var(--err)}.down{color:var(--ok)}
.empty{padding:30px;text-align:center;color:var(--muted);background:var(--card);border:1px dashed var(--line);border-radius:10px}
code{font-family:var(--mono);font-size:12px}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.spark{color:var(--acc);background:var(--card);border:1px solid var(--line);border-radius:10px}
.grid2{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}
details summary{cursor:pointer;color:var(--muted);font-size:12px}pre{white-space:pre-wrap;font-size:12px}
a.plus{text-decoration:none;font-weight:700}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 14px}.tabs a{padding:5px 12px;border-radius:99px;
border:1px solid var(--line);background:#fff;text-decoration:none;color:var(--fg);font-size:13px}
.tabs a.on{background:var(--acc);border-color:var(--acc);color:#fff}
.b-eu_souveraen{background:#ecfdf3;color:var(--ok)}.b-eu_region{background:#eff4ff;color:var(--acc)}
.b-nicht_eu{background:#fffaeb;color:var(--warn)}.b-unbekannt{background:#f2f4f7;color:var(--muted)}
.alt{color:var(--muted)}header .lang{margin-left:auto;color:#cfd4dc;font-size:12px;text-decoration:none}
header .lang:hover{color:#fff}.chart{background:var(--card);border:1px solid var(--line);border-radius:10px}
.chart text{font:11px system-ui,sans-serif;fill:var(--muted)}.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
@media print{header,.filters,.noprint,form{display:none!important}main{padding:0}body{background:#fff}}
"""

JS = """
async function poll(){
  try{
    const r = await fetch(document.baseURI.replace(/[^/]*$/,'') + 'api/status'); const s = await r.json();
    const running = Object.entries(s.providers).filter(([k,v])=>v.running).map(([k])=>k);
    document.querySelectorAll('[data-run]').forEach(b=>{b.disabled = running.includes(b.dataset.run) || (b.dataset.run==='all' && running.length>0)});
    const el = document.getElementById('running');
    if(el){ el.textContent = running.length ? el.dataset.label + ' ' + running.map(k=>s.providers[k].label).join(', ') + ' …' : ''; }
    if(window._wasRunning && running.length===0 && document.getElementById('running')){ location.reload(); return; }
    window._wasRunning = running.length>0;
    if(running.length) setTimeout(poll, 3000);
  }catch(e){ setTimeout(poll, 10000); }
}
poll();
"""

NAV = (("preise", "", "Aktuelle Preise"), ("vergleich", "vergleich", "Vergleich"),
       ("szenarien", "szenarien", "Szenarien"), ("bericht", "bericht", "Bericht"), ("archiv", "archiv", "Archiv"),
       ("protokoll", "protokoll", "Abrufprotokoll"))


def layout(title: str, body: str, active: str = "", base: str = "/") -> bytes:
    nav = "".join(f'<a href="{href or "./"}" class="{"on" if key == active else ""}">{t(label)}</a>'
                  for key, href, label in NAV)
    lang = get_lang()
    other = "en" if lang == "de" else "de"
    switch = (f'<a class="lang" href="sprache?lang={other}" title="{t("Sprache wechseln")}">'
              f'{"English" if other == "en" else "Deutsch"}</a>')
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><base href="{e(base)}">
<title>{e(title)} · {t("Preisverwaltung")}</title><style>{CSS}</style></head>
<body><header><b>{t("Preisverwaltung")}</b><nav>{nav}</nav>{switch}</header><main>{body}</main>
<script>{JS}</script></body></html>""".encode("utf-8")


def tabs(items: list[tuple[str, str]], active: str) -> str:
    """Reiter-Navigation: [(href, Bezeichnung)]."""
    return '<div class="tabs noprint">' + "".join(
        f'<a href="{href}" class="{"on" if href == active else ""}">{e(label)}</a>' for href, label in items) + "</div>"


def souv_badge(s: dict | None) -> str:
    if not s:
        return ""
    title = t(s["hint"]) + (f" · {t(s['country_name'])}" if s.get("country_name") else "")
    return f'<span class="badge b-{e(s["level"])}" title="{e(title)}">{e(t(s["label"]))}</span>'


PALETTE = ["#1f5eff", "#12805c", "#c4320a", "#b54708", "#7a5af8", "#0e9384", "#c11574", "#475467"]


def line_chart(series: dict[str, list[tuple[float, float]]], width: int = 760, height: int = 220,
               x_labels: list[tuple[float, str]] | None = None, y_fmt=None, zero: bool = True) -> str:
    """Einfaches Liniendiagramm als Inline-SVG: {Name: [(x, y), …]}."""
    pts = [p for s in series.values() for p in s]
    if not pts:
        return ""
    y_fmt = y_fmt or (lambda v: fmt_eur(v))
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1 = min(xs), max(xs)
    if zero:
        y0, y1 = min(0.0, min(ys)), max(ys) or 1.0
    else:
        pad = (max(ys) - min(ys)) * 0.1 or abs(max(ys)) * 0.05 or 1.0
        y0, y1 = min(ys) - pad, max(ys) + pad
    pl, pr, pt, pb = 70, 36, 10, 24
    sx = lambda x: pl + (x - x0) / ((x1 - x0) or 1) * (width - pl - pr)  # noqa: E731
    sy = lambda y: height - pb - (y - y0) / ((y1 - y0) or 1) * (height - pt - pb)  # noqa: E731
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px" role="img">']
    for i in range(5):
        v = y0 + (y1 - y0) * i / 4
        out.append(f'<line x1="{pl}" x2="{width - pr}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" stroke="#e4e7ec"/>'
                   f'<text x="{pl - 6}" y="{sy(v) + 4:.1f}" text-anchor="end">{e(y_fmt(v))}</text>')
    for x, lbl in x_labels or []:
        out.append(f'<text x="{sx(x):.1f}" y="{height - 6}" text-anchor="middle">{e(lbl)}</text>')
    legend = []
    for i, (name, s) in enumerate(series.items()):
        col = PALETTE[i % len(PALETTE)]
        if not s:
            continue
        d = " ".join(f"{'M' if j == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}" for j, (x, y) in enumerate(s))
        out.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="2"/>')
        legend.append(f'<span><i style="background:{col}"></i>{e(name)}</span>')
    out.append("</svg>")
    return "".join(out) + (f'<div class="legend">{"".join(legend)}</div>' if len(legend) > 1 else "")
