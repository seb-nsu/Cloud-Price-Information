"""Zugangsdaten der Anbieter (API-Token) in der Oberfläche pflegen, statt Umgebungsvariablen von Hand zu setzen.

Gespeichert in `zugaenge.json` neben der Datenbank (Dateirechte 600). Beim Start und nach dem Speichern werden die Werte
in die Umgebung übernommen; eine von außen gesetzte Umgebungsvariable hat immer Vorrang. Die Seite ist nur erreichbar,
wenn ein Zugangsschutz (PV_USER/PV_PASSWORD) gesetzt ist oder der Aufruf vom selben Rechner kommt."""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path

from . import config

# Variable, Anbieter, Hinweis
KEYS: list[tuple[str, str, str]] = [
    ("GCP_API_KEY", "Google Cloud", "API-Key aus einem Projekt mit aktivierter Cloud Billing API"),
    ("HCLOUD_TOKEN", "Hetzner Cloud", "API-Token mit Leserechten (Hetzner Console → Sicherheit → API-Tokens)"),
]
_FROM_FILE: set[str] = set()


def path() -> Path:
    return config.DB_PATH.parent / "zugaenge.json"


def _read() -> dict[str, str]:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
        return {k: str(v) for k, v in data.items() if isinstance(v, str)}
    except (OSError, ValueError):
        return {}


def load_into_env() -> None:
    """Gespeicherte Werte in die Umgebung übernehmen (Umgebungsvariablen von außen bleiben unberührt)."""
    for key, value in _read().items():
        if value and (key not in os.environ or key in _FROM_FILE):
            os.environ[key] = value
            _FROM_FILE.add(key)


def status() -> dict[str, str]:
    """Je Variable: 'umgebung' (von außen gesetzt), 'gespeichert' oder 'fehlt'."""
    out = {}
    for key, _, _ in KEYS:
        if os.environ.get(key) and key not in _FROM_FILE:
            out[key] = "umgebung"
        elif os.environ.get(key):
            out[key] = "gespeichert"
        else:
            out[key] = "fehlt"
    return out


def save(updates: dict[str, str]) -> list[str]:
    """Nicht leere Werte speichern; der Wert „-“ löscht einen gespeicherten Schlüssel. Gibt die geänderten Namen zurück."""
    allowed = {k for k, _, _ in KEYS}
    data = _read()
    changed = []
    for key, value in updates.items():
        value = value.strip()
        if key not in allowed or not value:
            continue
        if value == "-":
            data.pop(key, None)
            if key in _FROM_FILE:
                os.environ.pop(key, None)
                _FROM_FILE.discard(key)
        else:
            data[key] = value
        changed.append(key)
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    load_into_env()
    return changed


def allowed(client_ip: str) -> bool:
    """Token-Seite nur mit Zugangsschutz oder vom selben Rechner."""
    if config.AUTH_USER and config.AUTH_PASSWORD:
        return True
    try:
        return ipaddress.ip_address(client_ip).is_loopback
    except ValueError:
        return False
