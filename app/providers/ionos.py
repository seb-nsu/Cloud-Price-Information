"""IONOS Cloud – offizielle Preisliste (Markdown-Fassung der Doku-Seite).

IONOS bietet keine eigene Preis-API. Die Preisliste wird unter docs.ionos.com
gepflegt und ist als Markdown abrufbar (URL + ".md"). Gelesen werden sowohl
Pipe-Tabellen als auch eingebettete HTML-Tabellen (GitBook nutzt sie für komplexe Zellen).

Robustheit:
* Schlüssel werden normalisiert (Kleinschreibung, ohne Satz-/Leerzeichen, nur die
  letzten zwei Überschriftenebenen), damit redaktionelle Änderungen nicht zu
  „entfallen + neu“ führen. Verbleibende Umbenennungen erkennt der Abgleich.
* Zahlenformat: das im Dokument überwiegende Dezimaltrennzeichen wird ermittelt;
  mehrdeutige Werte (z. B. „2.500 €“) werden markiert und als Warnung gemeldet.
"""
from __future__ import annotations

import re
import statistics
from html.parser import HTMLParser

from ..http_util import get_text
from ..models import PT_ON_DEMAND, PT_SAVINGS_1Y, PT_SAVINGS_3Y, FetchResult, PriceRecord

LABEL = "IONOS Cloud"
INFO = "Preisliste IONOS CLOUD GmbH (docs.ionos.com) · Netto in EUR"
DEFAULT_URL = "https://docs.ionos.com/cloud/support/general-information/price-list/ionos-cloud-gmbh-de.md"

SECTION_CATEGORY = [
    ("ai", "KI"), ("backup", "Storage"), ("storage", "Storage"), ("compute", "Compute"),
    ("container", "Kubernetes"), ("kubernetes", "Kubernetes"), ("database", "Datenbank"),
    ("network", "Netzwerk"), ("netzwerk", "Netzwerk"),
]
FREE_WORDS = ("kostenfrei", "kostenlos", "no charge", "free of charge", "inklusive", "included")
CURRENCY_SIGNS = {"€": "EUR", "£": "GBP", "$": "USD"}
_NUM = re.compile(r"\d[\d.,]*")


def clean(text: str) -> str:
    text = re.sub(r'<code class="expression">space\.vars\.ionos\\?_cloud</code>', "IONOS Cloud", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("**", "").replace("\\", "")
    return re.sub(r"\s+", " ", text).strip()


def norm_key(text: str) -> str:
    return re.sub(r"[^0-9a-zäöüß]+", "", text.casefold())


# ------------------------------------------------------------------ HTML-Tabellen
class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.header_rows = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._in_head = False
        self._cell_is_th = False
        self._row_has_th = False

    def handle_starttag(self, tag, attrs):
        if tag == "thead":
            self._in_head = True
        elif tag == "tr":
            self._row, self._row_has_th = [], False
        elif tag in ("td", "th") and self._row is not None:
            self._cell, self._cell_is_th = [], tag == "th"
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag == "thead":
            self._in_head = False
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(clean("".join(self._cell)))
            self._row_has_th |= self._cell_is_th
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                if (self._in_head or self._row_has_th) and len(self.rows) == self.header_rows:
                    self.header_rows += 1
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def html_tables_to_markdown(md: str) -> str:
    """Ersetzt <table>…</table>-Blöcke durch gleichwertige Pipe-Tabellen."""
    def repl(m: re.Match) -> str:
        p = _TableParser()
        p.feed(m.group(0))
        if not p.rows:
            return ""
        header = p.rows[0]
        n = max(len(r) for r in p.rows)
        esc = lambda c: c.replace("|", "/")  # noqa: E731
        lines = ["| " + " | ".join(esc(c) for c in header + [""] * (n - len(header))) + " |",
                 "| " + " | ".join(["---"] * n) + " |"]
        for r in p.rows[1:]:
            lines.append("| " + " | ".join(esc(c) for c in r + [""] * (n - len(r))) + " |")
        return "\n\n" + "\n".join(lines) + "\n\n"
    return re.sub(r"<table\b.*?</table>", repl, md, flags=re.S | re.I)


# ------------------------------------------------------------------ Zahlen
def detect_decimal(md: str) -> str:
    """Überwiegendes Dezimaltrennzeichen in Preisangaben (',' oder '.')."""
    comma = len(re.findall(r"\d,\d+\s*(?:€|£|\$)", md))
    dot = len(re.findall(r"(?<![\d.])\d{1,3}\.\d{1,2}\s*(?:€|£|\$)|\b0\.\d+\s*(?:€|£|\$)", md))
    return "," if comma >= dot else "."


def parse_number(s: str, decimal: str = ",") -> tuple[float | None, bool]:
    """-> (Wert, mehrdeutig)."""
    m = _NUM.search(s)
    if not m:
        return None, False
    n = m.group(0).rstrip(".,")
    ambiguous = False
    if "," in n and "." in n:                       # letztes Zeichen ist das Dezimaltrennzeichen
        n = n.replace(".", "").replace(",", ".") if n.rfind(",") > n.rfind(".") else n.replace(",", "")
    elif "," in n:
        if decimal == "." and re.fullmatch(r"[1-9]\d{0,2}(,\d{3})+", n):
            n, ambiguous = n.replace(",", ""), True
        else:
            n = n.replace(",", ".")
    elif "." in n:
        if not n.startswith("0.") and decimal == "," and re.fullmatch(r"[1-9]\d{0,2}(\.\d{3})+", n):
            n, ambiguous = n.replace(".", ""), True   # 7.500 -> 7500 (Tausenderpunkt)
    try:
        return float(n), ambiguous
    except ValueError:
        return None, False


def parse_price_cell(cell: str, decimal: str = ",") -> tuple[float, str, str, bool] | None:
    """-> (Preis, Währung, Einheiten-Zusatz, mehrdeutig) oder None, wenn keine Preisangabe."""
    low = cell.lower()
    if any(w in low for w in FREE_WORDS):
        return 0.0, "", "", False
    sign = next((s for s in CURRENCY_SIGNS if s in cell), None)
    if not sign:
        return None
    value, ambiguous = parse_number(cell, decimal)
    if value is None:
        return None
    suffix = cell.split(sign, 1)[1].strip(" /")
    return value, CURRENCY_SIGNS[sign], suffix, ambiguous


# ------------------------------------------------------------------ Tabellen
def _category(path: list[str]) -> str:
    top = (path[0] if path else "").lower()
    for needle, cat in SECTION_CATEGORY:
        if re.search(rf"\b{needle}", top):
            return cat
    return "Sonstiges"


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [clean(c) for c in re.split(r"(?<!\\)\|", line)]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c.replace(" ", "")) for c in cells if c)


def iter_tables(md: str):
    """Liefert (Überschriften-Pfad, Kopfzeile, Datenzeilen) je Tabelle."""
    md = html_tables_to_markdown(md)
    headings: dict[int, str] = {}
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        h = re.match(r"^(#{1,6})\s+(.*)$", line)
        if h:
            level = len(h.group(1))
            headings = {k: v for k, v in headings.items() if k < level}
            headings[level] = clean(h.group(2))
            i += 1
            continue
        if line.lstrip().startswith("|") and i + 1 < len(lines) and _is_separator(_split_row(lines[i + 1])):
            header = _split_row(line)
            rows = []
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(_split_row(lines[i]))
                i += 1
            path = [v for k, v in sorted(headings.items()) if k > 1]   # Ebene 1 = Dokumenttitel
            yield path, header, rows
            continue
        i += 1


def _price_type(header: list[str], row: list[str]) -> str:
    text = " ".join(header + row).lower()
    if not re.search(r"laufzeit|term|savings", " ".join(header).lower()):
        return PT_ON_DEMAND
    return PT_SAVINGS_3Y if re.search(r"\b3\s*(jahre|years?)", text) else PT_SAVINGS_1Y


def parse(md: str, warnings: list[str] | None = None) -> list[PriceRecord]:
    warnings = warnings if warnings is not None else []
    decimal = detect_decimal(md)
    out: list[PriceRecord] = []
    seen: dict[str, int] = {}
    for path, header, rows in iter_tables(md):
        n = len(header)
        price_cols = [j for j, hdr in enumerate(header)
                      if j > 0 and re.search(r"preis|price", hdr, re.I) and not re.search(r"gruppe|group", hdr, re.I)]
        group_col = next((j for j, hdr in enumerate(header) if re.search(r"gruppe|group", hdr, re.I)), None)
        if not price_cols:
            price_cols = [j for j in range(1, n) if any(
                j < len(r) and parse_price_cell(r[j], decimal) and "€" in r[j] for r in rows)]
        desc_cols = [j for j in range(1, n) if j not in price_cols and j != group_col]
        table_title = header[0] if header and header[0] else ""

        table_recs: list[tuple[PriceRecord, bool]] = []
        for r in rows:
            r = r + [""] * (n - len(r))
            label = r[0]
            if not label:
                continue
            descr_vals = [r[j] for j in desc_cols if r[j]]
            descr = " · ".join(f"{header[j]}: {r[j]}" if header[j] else r[j] for j in desc_cols if r[j])
            for j in price_cols:
                parsed = parse_price_cell(r[j], decimal)
                if not parsed:
                    continue
                price, currency, suffix, ambiguous = parsed
                head_unit = re.sub(r"^(preis|price)\s*", "", header[j], flags=re.I).strip()
                if head_unit and suffix:
                    unit = f"{head_unit} ({suffix})"
                else:
                    unit = head_unit or suffix or "—"
                product = label if not table_title or table_title.lower() == label.lower() else f"{table_title}: {label}"
                if path and path[-1].lower() != product.lower():
                    product = f"{path[-1]} · {product}"
                if descr:
                    product += f" ({descr})"
                if len(price_cols) > 1:
                    product += f" – {header[j]}"
                base_key = "|".join([norm_key(p) for p in path[-2:]] + [
                    norm_key(table_title), norm_key(label), norm_key(" ".join(descr_vals)),
                    norm_key(header[j]) if len(price_cols) > 1 else ""])
                seen[base_key] = seen.get(base_key, 0) + 1
                key = base_key if seen[base_key] == 1 else f"{base_key}#{seen[base_key]}"
                attrs = {"Abschnitt": " > ".join(path), "Originalangabe": r[j]}
                if group_col is not None and r[group_col]:
                    attrs["Preisgruppe"] = r[group_col]
                if ambiguous:
                    attrs["Hinweis"] = "Zahlformat mehrdeutig – als Tausendertrennzeichen gelesen"
                rec = PriceRecord(
                    key=key, category=_category(path), service=" > ".join(path[:2]) or "IONOS Cloud",
                    product=product, region="de", unit=unit, price=price, currency=currency or "EUR",
                    price_type=_price_type(header, r), attributes=attrs, scope="preisliste")
                table_recs.append((rec, ambiguous))
        # Plausibilität mehrdeutiger Werte gegen den Median der Tabelle
        others = [rec.price for rec, amb in table_recs if not amb and rec.price > 0]
        med = statistics.median(others) if others else None
        for rec, amb in table_recs:
            if amb:
                note = f"{rec.product}: „{rec.attributes['Originalangabe']}“ als {rec.price:g} gelesen"
                if med and rec.price > 100 * med:
                    note += f" (Median der Tabelle {med:g} – bitte prüfen)"
                warnings.append("Mehrdeutiges Zahlformat: " + note)
            out.append(rec)
    return out


def fetch(cfg: dict, get=get_text) -> FetchResult:
    url = cfg.get("url", DEFAULT_URL)
    md = get(url)
    warnings: list[str] = []
    records = parse(md, warnings)
    if len(records) <= 50:
        warnings.append(f"Nur {len(records)} Preise erkannt – Format der Preisliste prüfen")
    return FetchResult(records=records, source=url.removesuffix(".md"), warnings=warnings,
                       scopes_ok={"preisliste"})
