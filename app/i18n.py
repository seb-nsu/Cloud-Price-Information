"""Zweisprachige Oberfläche (Deutsch/Englisch).

Texte werden im Code auf Deutsch geschrieben und mit `t()` übersetzt; die englischen Texte stehen in
`i18n_en.py` (Schlüssel = deutscher Text). Fehlt eine Übersetzung, erscheint der deutsche Text.
Die Sprache gilt je Anfrage (Cookie `pv_lang`, umschaltbar über `?lang=en|de`); Standard ist
`[general] sprache` in config.toml bzw. die Umgebungsvariable PV_LANG.
"""
from __future__ import annotations

import contextvars
import os

from .i18n_en import EN

LANGS = ("de", "en")
_lang: contextvars.ContextVar[str] = contextvars.ContextVar("pv_lang", default="de")


def default_lang() -> str:
    env = os.environ.get("PV_LANG", "").lower()
    if env in LANGS:
        return env
    try:
        from .config import load_config
        v = str(load_config().get("general", {}).get("sprache", "de")).lower()
        return v if v in LANGS else "de"
    except Exception:  # noqa: BLE001
        return "de"


def set_lang(lang: str) -> None:
    _lang.set(lang if lang in LANGS else "de")


def get_lang() -> str:
    return _lang.get()


def t(text: str, **kw) -> str:
    """Übersetzt `text` in die aktuelle Sprache; `kw` werden per str.format eingesetzt."""
    s = EN.get(text, text) if _lang.get() == "en" else text
    return s.format(**kw) if kw else s


class use:
    """Kontextmanager für Tests und Exporte: `with i18n.use("en"): ...`"""

    def __init__(self, lang: str):
        self.lang = lang

    def __enter__(self):
        self.token = _lang.set(self.lang if self.lang in LANGS else "de")
        return self

    def __exit__(self, *exc):
        _lang.reset(self.token)
