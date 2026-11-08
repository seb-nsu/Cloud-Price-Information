from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("PV_CONFIG", BASE_DIR / "config.toml"))
DB_PATH = Path(os.environ.get("PV_DB", BASE_DIR / "data" / "preise.db"))
HOST = os.environ.get("PV_HOST", "0.0.0.0")
PORT = int(os.environ.get("PV_PORT", "8080"))
# Pfad, unter dem die App hinter einem Reverse-Proxy erreichbar ist (z. B. "/preise/")
BASE_PATH = "/" + os.environ.get("PV_BASE_PATH", "/").strip("/") + "/" if os.environ.get("PV_BASE_PATH", "/").strip("/") else "/"
# Optionaler Zugangsschutz (HTTP Basic Auth) – empfohlen, sobald die App im Netz erreichbar ist
AUTH_USER = os.environ.get("PV_USER", "")
AUTH_PASSWORD = os.environ.get("PV_PASSWORD", "")


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {"general": {}, "providers": {}}
    with open(CONFIG_PATH, "rb") as fh:
        cfg = tomllib.load(fh)
    cfg.setdefault("general", {})
    cfg.setdefault("providers", {})
    return cfg


def provider_config(name: str) -> dict[str, Any]:
    return load_config()["providers"].get(name, {})
