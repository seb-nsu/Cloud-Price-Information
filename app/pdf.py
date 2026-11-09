"""Minimaler PDF-Writer für Berichte (A4 quer/hoch, Helvetica, Tabellen) – nur Standardbibliothek.

Verwendet die PDF-Standardschrift Helvetica mit WinAnsi-Kodierung (deutsche Umlaute und € werden
unterstützt). Für Tabellen werden Spalten anhand der Helvetica-Zeichenbreiten gekürzt bzw.
rechtsbündig gesetzt.
"""
from __future__ import annotations

import zlib
from datetime import datetime

# Helvetica-Zeichenbreiten (1/1000 em) für ASCII 32–126 laut AFM
_W = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556,
      556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278,
      500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469,
      556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500,
      278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]


def text_width(s: str, size: float, bold: bool = False) -> float:
    w = sum(_W[ord(c) - 32] if 32 <= ord(c) <= 126 else 556 for c in s)
    return w * size / 1000 * (1.05 if bold else 1.0)


def _enc(s: str) -> bytes:
    b = s.encode("cp1252", errors="replace")
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def fit(s: str, width: float, size: float, bold: bool = False) -> str:
    s = str(s)
    if text_width(s, size, bold) <= width:
        return s
    while s and text_width(s + "…", size, bold) > width:
        s = s[:-1]
    return s + "…"


class Report:
    def __init__(self, title: str, landscape: bool = True, footer: str = ""):
        self.w, self.h = (842, 595) if landscape else (595, 842)
        self.margin = 36
        self.title = title
        self.footer = footer or f"Erstellt {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        self.pages: list[list[bytes]] = []
        self.y = 0.0
        self._new_page()

    # -------------------------------------------------------------- Grundfunktionen
    def _new_page(self):
        self.pages.append([])
        self.y = self.h - self.margin
        self._text(self.margin, self.y - 10, self.title, 9, bold=True, gray=0.45)
        self.y -= 24

    def _cmd(self, b: bytes):
        self.pages[-1].append(b)

    def _text(self, x: float, y: float, s: str, size: float, bold: bool = False, gray: float = 0.0,
              color: tuple[float, float, float] | None = None):
        font = b"/F2" if bold else b"/F1"
        fill = (b"%.3f %.3f %.3f rg " % color) if color else (b"%.2f g " % gray)
        self._cmd(b"BT " + fill + font + b" %.1f Tf %.2f %.2f Td (" % (size, x, y) + _enc(s) + b") Tj ET\n")

    def _line(self, x1, y1, x2, y2, gray=0.85, width=0.5):
        self._cmd(b"%.2f G %.2f w %.2f %.2f m %.2f %.2f l S\n" % (gray, width, x1, y1, x2, y2))

    def _ensure(self, height: float):
        if self.y - height < self.margin + 20:
            self._new_page()

    # -------------------------------------------------------------- Bausteine
    def heading(self, s: str, size: float = 16):
        self._ensure(size + 14)
        self.y -= size
        self._text(self.margin, self.y, s, size, bold=True)
        self.y -= 10

    def paragraph(self, s: str, size: float = 9.5, gray: float = 0.3):
        words, line = s.split(), ""
        maxw = self.w - 2 * self.margin
        for wd in words:
            cand = f"{line} {wd}".strip()
            if text_width(cand, size) > maxw and line:
                self._ensure(size + 4)
                self.y -= size + 3
                self._text(self.margin, self.y, line, size, gray=gray)
                line = wd
            else:
                line = cand
        if line:
            self._ensure(size + 4)
            self.y -= size + 3
            self._text(self.margin, self.y, line, size, gray=gray)
        self.y -= 6

    def table(self, header: list[str], rows: list[list], widths: list[float], align: str = "",
              size: float = 8.5, colors: list[tuple | None] | None = None):
        """`widths` relativ (werden auf die Seitenbreite skaliert); `align`: je Spalte 'l' oder 'r'."""
        total = self.w - 2 * self.margin
        scale = total / sum(widths)
        ws = [w * scale for w in widths]
        align = (align or "l" * len(header)).ljust(len(header), "l")
        row_h = size + 6

        def draw_row(cells, bold=False, color_row=None):
            self._ensure(row_h)
            self.y -= row_h
            x = self.margin
            for i, (c, w) in enumerate(zip(cells, ws)):
                s = fit("" if c is None else str(c), w - 6, size, bold)
                tx = x + 3 if align[i] == "l" else x + w - 3 - text_width(s, size, bold)
                col = color_row[i] if color_row and i < len(color_row) else None
                self._text(tx, self.y + 4, s, size, bold=bold, gray=0.35 if bold else 0.05, color=col)
                x += w
            self._line(self.margin, self.y, self.margin + total, self.y)

        self.y -= 4
        draw_row(header, bold=True)
        for i, r in enumerate(rows):
            if self.y - row_h < self.margin + 20:
                self._new_page()
                draw_row(header, bold=True)
            draw_row(r, color_row=colors[i] if colors else None)
        self.y -= 10

    # -------------------------------------------------------------- Ausgabe
    def to_bytes(self) -> bytes:
        objs: list[bytes] = []

        def add(b: bytes) -> int:
            objs.append(b)
            return len(objs)

        f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        f2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        page_ids = []
        n = len(self.pages)
        for i, cmds in enumerate(self.pages):
            foot = (b"BT 0.55 g /F1 7.5 Tf %.2f %.2f Td (" % (self.margin, self.margin - 14)
                    + _enc(f"{self.footer} · Seite {i + 1} von {n}") + b") Tj ET\n")
            stream = zlib.compress(b"".join(cmds) + foot)
            cid = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream) + stream + b"\nendstream")
            page_ids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
                                b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> >>"
                                % (0, self.w, self.h, cid, f1, f2)))
        pages_id = add(b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % p for p in page_ids)
                       + b"] /Count %d >>" % n)
        for p in page_ids:   # Parent nachtragen
            objs[p - 1] = objs[p - 1].replace(b"/Parent 0 0 R", b"/Parent %d 0 R" % pages_id)
        info = add(b"<< /Title (" + _enc(self.title) + b") /Producer (Preisverwaltung) >>")
        catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, o in enumerate(objs):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % (i + 1) + o + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
        out += (b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                % (len(objs) + 1, catalog, info, xref))
        return bytes(out)
