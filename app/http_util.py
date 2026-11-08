from __future__ import annotations

import gzip
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from typing import Any

from . import __version__

log = logging.getLogger(__name__)

USER_AGENT = f"Preisverwaltung/{__version__} (+https://github.com/seb-nsu/price_management)"
TIMEOUT = 120
SECRET_PARAMS = ("key", "api_key", "apikey", "token", "access_token")


def build_url(base: str, params: dict[str, Any] | list[tuple[str, Any]] | None = None) -> str:
    if not params:
        return base
    query = urllib.parse.urlencode(params, doseq=True, quote_via=urllib.parse.quote)
    return f"{base}{'&' if '?' in base else '?'}{query}"


def open_url(url: str, *, retries: int = 3, timeout: int = TIMEOUT, accept: str = "*/*",
             headers: dict[str, str] | None = None, gzip_ok: bool = False):
    """Öffnet eine URL mit Wiederholungen bei Netzwerk- und 5xx/429-Fehlern."""
    hdrs = {"User-Agent": USER_AGENT, "Accept": accept, **(headers or {})}
    if gzip_ok:
        hdrs["Accept-Encoding"] = "gzip"
    req = urllib.request.Request(url, headers=hdrs)
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code not in (429, 500, 502, 503, 504):
                body = ""
                try:
                    body = exc.read(500).decode("utf-8", "replace")
                except Exception:
                    pass
                raise RuntimeError(f"HTTP {exc.code} bei {redact(url)}: {body}") from exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after and retry_after.isdigit():
                time.sleep(min(int(retry_after), 120))
                continue
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_exc = exc
        wait = 2 ** attempt
        log.warning("Abruf fehlgeschlagen (%s), neuer Versuch in %ss: %s", last_exc, wait, redact(url))
        time.sleep(wait)
    raise RuntimeError(f"Abruf fehlgeschlagen nach {retries} Versuchen: {redact(url)} ({last_exc})")


@contextmanager
def open_stream(url: str, **kw):
    """Wie open_url, liefert aber einen (ggf. gzip-entpackten) Binärstrom."""
    resp = open_url(url, gzip_ok=True, **kw)
    try:
        if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
            with gzip.GzipFile(fileobj=resp) as gz:
                yield gz
        else:
            yield resp
    finally:
        resp.close()


def _read(url: str, **kw) -> bytes:
    with open_stream(url, **kw) as fh:
        return fh.read()


def get_json(url: str, **kw) -> Any:
    return json.loads(_read(url, accept="application/json", **kw).decode("utf-8"))


def get_text(url: str, **kw) -> str:
    return _read(url, **kw).decode("utf-8", "replace")


def redact(url: str) -> str:
    """Entfernt API-Keys aus URLs für Logs und Fehlermeldungen."""
    parts = urllib.parse.urlsplit(url)
    if not parts.query:
        return url
    q = [(k, "***" if k.lower() in SECRET_PARAMS else v)
         for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q)))
