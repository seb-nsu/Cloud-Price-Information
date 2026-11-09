"""HTTP-Server der Preisverwaltung (nur Python-Standardbibliothek)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, api, config, fx, i18n, jobs, pages, pages_extra
from .db import Database, now_iso
from .providers import PROVIDERS
from .sync import renormalize_all
from .i18n import t
from .ui import layout

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

log = logging.getLogger(__name__)

_SECRET = os.environ.get("PV_SECRET", "").encode() or secrets.token_bytes(32)
CSRF_TOKEN = hmac.new(_SECRET, b"csrf", hashlib.sha256).hexdigest()[:32]

# einfacher Schutz gegen das Durchprobieren von Passwörtern
MAX_FAILS, FAIL_WINDOW = 10, 300
_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


def _too_many_fails(ip: str) -> bool:
    with _fails_lock:
        now = time.time()
        lst = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
        _fails[ip] = lst
        return len(lst) >= MAX_FAILS


def _register_fail(ip: str) -> None:
    with _fails_lock:
        _fails.setdefault(ip, []).append(time.time())


def health_details(db: Database) -> dict:
    max_age = int(config.load_config().get("general", {}).get("warnung_nach_tagen", 14) or 0)
    out = {"status": "ok", "version": __version__, "providers": {}}
    overview = pages.provider_overview(db)
    for p in overview:
        stale = False
        if max_age and p["enabled"] and p["last_ok"]:
            stale = datetime.fromisoformat(p["last_ok"]) < datetime.fromisoformat(now_iso()) - timedelta(days=max_age)
        out["providers"][p["name"]] = {"count": p["count"], "last_ok": p["last_ok"], "stale": stale,
                                       "review_pending": bool(p["review"]),
                                       "last_status": p["last"]["status"] if p["last"] else None}
        if stale or (p["last"] and p["last"]["status"] == "error"):
            out["status"] = "warnung"
    return out


class Handler(BaseHTTPRequestHandler):
    db: Database = None  # wird in serve() gesetzt
    server_version = f"Preisverwaltung/{__version__}"

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    # -------------------------------------------------------------- Hilfen
    def _client_ip(self) -> str:
        return self.client_address[0] if self.client_address else "?"

    def _auth_ok(self) -> bool:
        if not (config.AUTH_USER and config.AUTH_PASSWORD):
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
            except Exception:
                return False
            return hmac.compare_digest(user, config.AUTH_USER) and hmac.compare_digest(pw, config.AUTH_PASSWORD)
        return False

    def _send(self, body: bytes, ctype="text/html; charset=utf-8", status=200, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", config.BASE_PATH + location.lstrip("/"))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _guard(self) -> bool:
        ip = self._client_ip()
        if config.AUTH_USER and _too_many_fails(ip):
            self._send(b"Zu viele Fehlversuche - bitte spaeter erneut versuchen", "text/plain", 429,
                       {"Retry-After": str(FAIL_WINDOW)})
            return False
        if not self._auth_ok():
            if self.headers.get("Authorization"):
                _register_fail(ip)
            self._send(b"Anmeldung erforderlich", "text/plain; charset=utf-8", 401,
                       {"WWW-Authenticate": 'Basic realm="Preisverwaltung", charset="UTF-8"'})
            return False
        return True

    def _route(self) -> tuple[list[str], dict]:
        url = urllib.parse.urlsplit(self.path)
        path = url.path
        if config.BASE_PATH != "/" and path.startswith(config.BASE_PATH.rstrip("/")):
            path = path[len(config.BASE_PATH.rstrip("/")):]
        parts = [urllib.parse.unquote(p) for p in path.strip("/").split("/") if p]
        params = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
        return parts, params

    def _not_found(self):
        self._send(layout(t("Nicht gefunden"), f'<div class="empty">{t("Seite nicht gefunden.")}</div>', "",
                          config.BASE_PATH), status=404)

    def _set_lang(self) -> None:
        lang = ""
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "pv_lang":
                lang = v.strip()
        i18n.set_lang(lang if lang in i18n.LANGS else i18n.default_lang())

    def _download(self, body: bytes, ctype: str, filename: str):
        self._send(body, ctype, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    def _json(self, data, status: int = 200):
        self._send(pages_extra.json_bytes(data), "application/json; charset=utf-8", status)

    # -------------------------------------------------------------- GET
    def do_GET(self):
        parts, params = self._route()
        # Healthcheck ohne Anmeldung (für Docker/Monitoring), ohne sensible Inhalte
        if parts == ["health"]:
            self._send(b"ok", "text/plain")
            return
        if not self._guard():
            return
        self._set_lang()
        db = self.db
        try:
            if parts == ["sprache"]:
                lang = params.get("lang", "de") if params.get("lang") in i18n.LANGS else "de"
                ref = urllib.parse.urlsplit(self.headers.get("Referer", ""))
                target = ref.path + (f"?{ref.query}" if ref.query else "") if ref.path else config.BASE_PATH
                if not target.startswith(config.BASE_PATH):
                    target = config.BASE_PATH
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", target)
                self.send_header("Set-Cookie", f"pv_lang={lang}; Path={config.BASE_PATH}; Max-Age=31536000; SameSite=Lax")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if parts and parts[0] == "api" and parts[1:] != ["status"]:
                status, data = api.handle(db, parts[1:], params)
                self._json(data, status)
                return
            if not parts:
                flash = {"gestartet": t("Abruf gestartet – die Seite aktualisiert sich, sobald er fertig ist."),
                         "laeuft": t("Für diese Anbieter läuft bereits ein Abruf."),
                         "kurse": t("Wechselkurse aktualisiert.")}.get(params.pop("info", ""), "")
                self._send(pages.page_prices(db, params, CSRF_TOKEN, flash))
            elif parts == ["archiv"]:
                self._send(pages.page_archive(db, params))
            elif parts == ["protokoll"]:
                self._send(pages.page_log(db, params))
            elif len(parts) == 2 and parts[0] == "lauf" and parts[1].isdigit():
                flash = params.pop("info", "")
                body = pages.page_run(db, int(parts[1]), params, CSRF_TOKEN, flash)
                self._send(body) if body else self._not_found()
            elif parts == ["verlauf"]:
                self._send(pages.page_history(db, params))
            elif parts == ["vergleich"]:
                self._send(pages.page_compare(db, params))
            elif parts == ["vergleich", "rabatte"]:
                self._send(pages_extra.page_commitments(db, params))
            elif parts == ["vergleich", "rabatte.xlsx"]:
                self._download(pages_extra.export_commitments_xlsx(db, params), XLSX, "rabatte.xlsx")
            elif len(parts) == 2 and parts[0] == "vergleich":
                body = pages_extra.page_compare_kind(db, parts[1], params)
                self._send(body) if body else self._not_found()
            elif parts == ["bericht"]:
                self._send(pages_extra.page_report(db, params))
            elif parts == ["bericht.pdf"]:
                self._download(pages_extra.export_report_pdf(db, params), "application/pdf",
                               f"preisbericht_{params.get('zeitraum', 'aktuell')}.pdf")
            elif parts == ["bericht.xlsx"]:
                self._download(pages_extra.export_report_xlsx(db, params), XLSX,
                               f"preisbericht_{params.get('zeitraum', 'aktuell')}.xlsx")
            elif parts == ["bericht.csv"]:
                self._download(pages_extra.export_report_csv(db, params), "text/csv; charset=utf-8",
                               f"preisbericht_{params.get('zeitraum', 'aktuell')}.csv")
            elif parts == ["kurse"]:
                flash = {"kurse": t("Wechselkurse aktualisiert.")}.get(params.pop("info", ""), params.pop("meldung", ""))
                self._send(pages_extra.page_rates(db, params, CSRF_TOKEN, flash))
            elif parts == ["export.xlsx"]:
                self._download(pages_extra.export_table_xlsx(db, "prices_current", params), XLSX, "preise_aktuell.xlsx")
            elif parts == ["archiv.xlsx"]:
                self._download(pages_extra.export_table_xlsx(db, "prices_archive", params), XLSX, "preise_archiv.xlsx")
            elif len(parts) == 3 and parts[0] == "szenario" and parts[1].isdigit() and parts[2] == "export.xlsx":
                body = pages_extra.export_scenario_xlsx(db, int(parts[1]))
                self._download(body, XLSX, f"szenario_{parts[1]}.xlsx") if body else self._not_found()
            elif parts == ["szenarien"]:
                self._send(pages.page_scenarios(db, CSRF_TOKEN))
            elif parts == ["szenario-hinzufuegen"]:
                self._send(pages.page_scenario_add(db, params, CSRF_TOKEN))
            elif len(parts) == 2 and parts[0] == "szenario" and parts[1].isdigit():
                body = pages.page_scenario(db, int(parts[1]), CSRF_TOKEN)
                self._send(body) if body else self._not_found()
            elif len(parts) == 3 and parts[0] == "szenario" and parts[1].isdigit() and parts[2] == "export.csv":
                self._send(pages.export_scenario_csv(db, int(parts[1])), "text/csv; charset=utf-8",
                           headers={"Content-Disposition": f'attachment; filename="szenario_{parts[1]}.csv"'})
            elif parts == ["export.csv"]:
                self._send(pages.export_csv(db, "prices_current", params), "text/csv; charset=utf-8",
                           headers={"Content-Disposition": 'attachment; filename="preise_aktuell.csv"'})
            elif parts == ["archiv.csv"]:
                self._send(pages.export_csv(db, "prices_archive", params), "text/csv; charset=utf-8",
                           headers={"Content-Disposition": 'attachment; filename="preise_archiv.csv"'})
            elif parts == ["api", "status"]:
                self._send(pages.status_json(db), "application/json")
            elif parts == ["health", "details"]:
                self._send(json.dumps(health_details(db)).encode(), "application/json")
            else:
                self._not_found()
        except Exception:
            log.exception("Fehler bei GET %s", self.path)
            self._send(layout(t("Fehler"), f'<div class="empty">{t("Interner Fehler – siehe Server-Log.")}</div>', "",
                              config.BASE_PATH), status=500)

    # -------------------------------------------------------------- POST
    def do_POST(self):
        if not self._guard():
            return
        self._set_lang()
        parts, _ = self._route()
        length = min(int(self.headers.get("Content-Length", "0") or 0), 20_000)
        form = {k: v[-1] for k, v in urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8")).items()}
        # CSRF-Schutz über Token (funktioniert auch hinter Reverse-Proxys mit umgeschriebenem Host)
        if not hmac.compare_digest(form.get("csrf", ""), CSRF_TOKEN):
            self._send(layout(t("Formular abgelaufen"), '<div class="empty">' + t(
                "Das Formular ist abgelaufen (z. B. nach einem Neustart). Bitte Seite neu laden und erneut absenden.")
                + '</div>', "", config.BASE_PATH), status=403)
            return
        db = self.db
        try:
            if parts == ["aktualisieren"]:
                target = form.get("provider", "all")
                enabled = {k for k in PROVIDERS if config.load_config()["providers"].get(k, {}).get("enabled", True)}
                names = sorted(enabled) if target == "all" else [target] if target in PROVIDERS else []
                started = jobs.start_background(db, names, triggered_by="manuell (Web)")
                self._redirect("?info=gestartet" if started else "?info=laeuft")
            elif len(parts) == 3 and parts[0] == "lauf" and parts[1].isdigit() and parts[2] in ("freigeben", "verwerfen"):
                msg = jobs.decide(db, int(parts[1]), parts[2] == "freigeben", by=f"Web ({config.AUTH_USER or self._client_ip()})")
                self._redirect(f"lauf/{parts[1]}?" + urllib.parse.urlencode({"info": msg}))
            elif parts == ["kurse"]:
                fx.refresh(db, force=True)
                renormalize_all(db, fx.get_rates(db))
                self._redirect("kurse?info=kurse" if "kurse" in (self.headers.get("Referer") or "") else "?info=kurse")
            elif parts == ["kurse", "verlauf"]:
                try:
                    msg = fx.refresh_history(db, full=True)
                except Exception as exc:  # noqa: BLE001
                    msg = t("Kursverlauf konnte nicht geladen werden: {e}", e=exc)
                self._redirect("kurse?" + urllib.parse.urlencode({"meldung": msg}))
            elif len(parts) == 3 and parts[0] == "szenario" and parts[1].isdigit() and parts[2] == "einstellungen":
                with db.connect() as con:
                    con.execute("UPDATE scenarios SET months=?, growth_pct=? WHERE id=?",
                                (max(1, min(120, int(_num(form.get("months"), 12)))),
                                 max(-100.0, min(1000.0, _num(form.get("growth_pct"), 0.0))), int(parts[1])))
                self._redirect(f"szenario/{parts[1]}")
            elif len(parts) == 5 and parts[0] == "szenario" and parts[2] == "position" and parts[4] == "aendern":
                growth = form.get("growth_pct", "").strip()
                with db.connect() as con:
                    con.execute("UPDATE scenario_items SET quantity=?, growth_pct=? WHERE id=? AND scenario_id=?",
                                (max(0.0, _num(form.get("quantity"), 1.0)),
                                 None if growth == "" else max(-100.0, min(1000.0, _num(growth, 0.0))),
                                 int(parts[3]), int(parts[1])))
                self._redirect(f"szenario/{parts[1]}")
            elif parts == ["szenarien"]:
                sid = self._create_scenario(form.get("name", "").strip() or "Neues Szenario", form.get("note", ""))
                self._redirect(f"szenario/{sid}")
            elif parts == ["szenario-hinzufuegen"]:
                sid = form.get("scenario_id", "neu")
                if sid == "neu" or not sid.isdigit():
                    sid = self._create_scenario(form.get("name", "").strip() or "Neues Szenario", "")
                qty = _num(form.get("quantity"), 1.0)
                growth = form.get("growth_pct", "").strip()
                growth_v = None if growth == "" else _num(growth, 0.0)
                with db.connect() as con:
                    p = con.execute("SELECT product FROM prices_current WHERE provider=? AND key=?",
                                    (form.get("provider", ""), form.get("key", ""))).fetchone()
                    if p and con.execute("SELECT 1 FROM scenarios WHERE id=?", (int(sid),)).fetchone():
                        con.execute("INSERT INTO scenario_items (scenario_id, variant, provider, key, quantity, note, label, "
                                    "growth_pct) VALUES (?,?,?,?,?,?,?,?)",
                                    (int(sid), (form.get("variant") or "A")[:20], form["provider"], form["key"], qty,
                                     form.get("note", "")[:200], p["product"], growth_v))
                self._redirect(f"szenario/{sid}")
            elif len(parts) == 5 and parts[0] == "szenario" and parts[2] == "position" and parts[4] == "loeschen":
                with db.connect() as con:
                    con.execute("DELETE FROM scenario_items WHERE id=? AND scenario_id=?", (int(parts[3]), int(parts[1])))
                self._redirect(f"szenario/{parts[1]}")
            elif len(parts) == 3 and parts[0] == "szenario" and parts[2] == "loeschen":
                with db.connect() as con:
                    con.execute("DELETE FROM scenarios WHERE id=?", (int(parts[1]),))
                self._redirect("szenarien")
            else:
                self._not_found()
        except Exception:
            log.exception("Fehler bei POST %s", self.path)
            self._send(layout(t("Fehler"), f'<div class="empty">{t("Interner Fehler – siehe Server-Log.")}</div>', "",
                              config.BASE_PATH), status=500)

    def _create_scenario(self, name: str, note: str) -> int:
        with self.db.connect() as con:
            return con.execute("INSERT INTO scenarios (name, note, created_at) VALUES (?,?,?)",
                               (name[:120], note[:500], now_iso())).lastrowid


def _num(v, default: float) -> float:
    try:
        x = float(str(v).replace(",", "."))
        return x if x == x and abs(x) != float("inf") else default
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ optionaler Zeitplan
def _scheduler(db: Database, stop: threading.Event) -> None:
    """Täglicher Abruf zur konfigurierten Uhrzeit (nur wenn [zeitplan] aktiv = true)."""
    last_day = None
    while not stop.wait(30):
        cfg = config.load_config().get("zeitplan", {})
        if not cfg.get("aktiv", False):
            continue
        now = datetime.now()
        if now.strftime("%H:%M") == str(cfg.get("uhrzeit", "03:00")) and last_day != now.date():
            last_day = now.date()
            wanted = cfg.get("anbieter", ["all"])
            names = list(PROVIDERS) if "all" in wanted else [n for n in wanted if n in PROVIDERS]
            enabled = [n for n in names if config.load_config()["providers"].get(n, {}).get("enabled", True)]
            log.info("Zeitplan: starte Abruf für %s", ", ".join(enabled))
            jobs.start_background(db, enabled, triggered_by="Zeitplan")


def serve(db: Database, host: str = config.HOST, port: int = config.PORT):
    Handler.db = db
    jobs.cleanup_stale(db)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    stop = threading.Event()
    threading.Thread(target=_scheduler, args=(db, stop), daemon=True, name="zeitplan").start()
    log.info("Preisverwaltung läuft auf http://%s:%s%s", host, port, config.BASE_PATH)
    if not (config.AUTH_USER and config.AUTH_PASSWORD):
        log.warning("Kein Zugangsschutz gesetzt (PV_USER/PV_PASSWORD) – nur im internen Netz betreiben!")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
