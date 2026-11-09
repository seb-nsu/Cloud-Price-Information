"""Minimaler Excel-Writer (.xlsx, Office Open XML) – nur Standardbibliothek.

Unterstützt mehrere Tabellenblätter, Zahlen, Texte, fette Kopfzeile, fixierte Kopfzeile,
Autofilter und Spaltenbreiten. Zeilen werden direkt in das ZIP-Archiv geschrieben, damit
auch große Exporte (z. B. AWS) wenig Speicher brauchen.
"""
from __future__ import annotations

import io
import math
import zipfile
from typing import Iterable
from xml.sax.saxutils import escape

_ILLEGAL = {c: None for c in range(32) if c not in (9, 10, 13)}


def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref: str, v, style: int = 0) -> str:
    st = f' s="{style}"' if style else ""
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return f'<c r="{ref}" t="b"{st}><v>{int(v)}</v></c>'
    if isinstance(v, (int, float)) and math.isfinite(v):
        return f'<c r="{ref}"{st}><v>{repr(float(v)) if isinstance(v, float) else v}</v></c>'
    text = escape(str(v).translate(_ILLEGAL))[:32767]
    return f'<c r="{ref}" t="inlineStr"{st}><is><t xml:space="preserve">{text}</t></is></c>'


class Workbook:
    def __init__(self):
        self.buf = io.BytesIO()
        self.zip = zipfile.ZipFile(self.buf, "w", zipfile.ZIP_DEFLATED)
        self.sheets: list[str] = []

    def add_sheet(self, name: str, header: list[str], rows: Iterable[Iterable], widths: list[int] | None = None,
                  number_formats: dict[int, str] | None = None) -> None:
        """`number_formats`: Spaltenindex -> 'eur' | 'pct' | 'num' (Anzeigeformat)."""
        idx = len(self.sheets) + 1
        clean = "".join(ch for ch in name if ch not in '[]:*?/\\')[:31] or f"Blatt{idx}"
        self.sheets.append(clean)
        fmt_style = {"eur": 2, "pct": 3, "num": 4}
        styles = {i: fmt_style.get(f, 0) for i, f in (number_formats or {}).items()}
        n_cols = len(header)
        widths = widths or [max(10, min(60, len(h) + 4)) for h in header]
        with self.zip.open(f"xl/worksheets/sheet{idx}.xml", "w") as fh:
            w = lambda s: fh.write(s.encode("utf-8"))  # noqa: E731
            w('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
              'state="frozen"/></sheetView></sheetViews><cols>')
            for i, wd in enumerate(widths):
                w(f'<col min="{i + 1}" max="{i + 1}" width="{wd}" customWidth="1"/>')
            w('</cols><sheetData><row r="1">')
            w("".join(_cell(f"{_col(i)}1", h, 1) for i, h in enumerate(header)))
            w("</row>")
            n = 1
            for row in rows:
                n += 1
                cells = "".join(_cell(f"{_col(i)}{n}", v, styles.get(i, 0)) for i, v in enumerate(row))
                w(f'<row r="{n}">{cells}</row>')
            w("</sheetData>")
            if n_cols:
                w(f'<autoFilter ref="A1:{_col(n_cols - 1)}{max(n, 1)}"/>')
            w("</worksheet>")

    def to_bytes(self) -> bytes:
        if not self.sheets:
            self.add_sheet("Leer", [], [])
        sheets = self.sheets
        z = self.zip
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/'
                             f'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(len(sheets)))
                   + '</Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                   'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                             for i, n in enumerate(sheets))
                   + '</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/'
                             f'2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                             for i in range(len(sheets)))
                   + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/'
                     f'officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<numFmts count="2"><numFmt numFmtId="164" formatCode="#,##0.00####\\ &quot;€&quot;"/>'
                   '<numFmt numFmtId="165" formatCode="0.0\\ &quot;%&quot;"/></numFmts>'
                   '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
                   '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                   '<fills count="3"><fill><patternFill patternType="none"/></fill>'
                   '<fill><patternFill patternType="gray125"/></fill>'
                   '<fill><patternFill patternType="solid"><fgColor rgb="FFF2F4F7"/></patternFill></fill></fills>'
                   '<borders count="1"><border/></borders>'
                   '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
                   '<cellXfs count="5"><xf/>'
                   '<xf fontId="1" fillId="2" applyFont="1" applyFill="1"/>'
                   '<xf numFmtId="164" applyNumberFormat="1"/>'
                   '<xf numFmtId="165" applyNumberFormat="1"/>'
                   '<xf numFmtId="4" applyNumberFormat="1"/></cellXfs>'
                   '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
                   '</styleSheet>')
        z.close()
        return self.buf.getvalue()


def single_sheet(name: str, header: list[str], rows: Iterable[Iterable], **kw) -> bytes:
    wb = Workbook()
    wb.add_sheet(name, header, rows, **kw)
    return wb.to_bytes()
