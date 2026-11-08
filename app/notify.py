"""Benachrichtigung bei Preisänderungen (Webhook und/oder E-Mail).

Konfiguration in config.toml unter [benachrichtigung], Zugangsdaten als Umgebungsvariablen:
  PV_WEBHOOK_URL                               – z. B. Microsoft-Teams-/Slack-/Mattermost-Webhook
  PV_SMTP_HOST, PV_SMTP_PORT, PV_SMTP_USER,
  PV_SMTP_PASSWORD, PV_MAIL_FROM, PV_MAIL_TO   – E-Mail (mehrere Empfänger mit Komma)
  PV_BASE_URL                                  – Basis-URL der App für Links in der Nachricht
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage

from .config import load_config
from .db import Database
from .http_util import USER_AGENT

log = logging.getLogger(__name__)


def _fmt(v) -> str:
    return "—" if v is None else f"{v:.6g}".replace(".", ",")


def relevant_changes(db: Database, run_id: int, cfg: dict) -> list[dict]:
    min_pct = float(cfg.get("min_aenderung_prozent", 0))
    cats = set(cfg.get("kategorien") or [])
    types = set(cfg.get("arten") or ["geändert", "entfallen", "neu"])
    with db.connect() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM run_changes WHERE run_id=? AND change != 'umbenannt'",
                                             (run_id,))]
    out = []
    for r in rows:
        if r["change"] not in types or (cats and r["category"] not in cats):
            continue
        if r["change"] == "geändert" and r["old_price"]:
            pct = abs(r["new_price"] - r["old_price"]) / r["old_price"] * 100
            if pct < min_pct:
                continue
            r["pct"] = (r["new_price"] - r["old_price"]) / r["old_price"] * 100
        out.append(r)
    return out


def build_message(provider_label: str, run_id: int, changes: list[dict]) -> tuple[str, str]:
    base = os.environ.get("PV_BASE_URL", "").rstrip("/")
    counts = {k: sum(1 for c in changes if c["change"] == k) for k in ("geändert", "neu", "entfallen")}
    subject = (f"Preisverwaltung: {provider_label} – {counts['geändert']} geändert, "
               f"{counts['neu']} neu, {counts['entfallen']} entfallen")
    lines = [subject, ""]
    for c in sorted(changes, key=lambda c: -abs(c.get("pct", 0)))[:25]:
        if c["change"] == "geändert":
            lines.append(f"• {c['product']} ({c['region']}): {_fmt(c['old_price'])} → {_fmt(c['new_price'])} "
                         f"{c['currency']} {c['unit']} ({c['pct']:+.1f} %)")
        elif c["change"] == "neu":
            lines.append(f"• NEU {c['product']} ({c['region']}): {_fmt(c['new_price'])} {c['currency']} {c['unit']}")
        else:
            lines.append(f"• ENTFALLEN {c['product']} ({c['region']}): bisher {_fmt(c['old_price'])} {c['currency']}")
    if len(changes) > 25:
        lines.append(f"… und {len(changes) - 25} weitere")
    if base:
        lines += ["", f"Details: {base}/lauf/{run_id}"]
    return subject, "\n".join(lines)


def send(db: Database, provider_label: str, run_id: int, opener=urllib.request.urlopen,
         smtp_factory=smtplib.SMTP) -> list[str]:
    """Versendet die Benachrichtigung zu einem übernommenen Abruf. Gibt Statusmeldungen zurück."""
    cfg = load_config().get("benachrichtigung", {})
    if not cfg.get("aktiv", False):
        return []
    changes = relevant_changes(db, run_id, cfg)
    if not changes:
        return []
    subject, text = build_message(provider_label, run_id, changes)
    status = []

    url = os.environ.get("PV_WEBHOOK_URL", "")
    if url:
        fmt = cfg.get("webhook_format", "text")
        payload = {"text": text} if fmt in ("text", "teams", "slack", "mattermost") else \
            {"subject": subject, "text": text, "run_id": run_id, "changes": changes[:200]}
        req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
        try:
            with opener(req, timeout=30):
                pass
            status.append("Webhook gesendet")
        except Exception as exc:  # noqa: BLE001
            log.error("Webhook fehlgeschlagen: %s", exc)
            status.append(f"Webhook fehlgeschlagen: {exc}")

    host, to = os.environ.get("PV_SMTP_HOST", ""), os.environ.get("PV_MAIL_TO", "")
    if host and to:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = os.environ.get("PV_MAIL_FROM", "preisverwaltung@localhost")
        msg["To"] = to
        msg.set_content(text)
        try:
            port = int(os.environ.get("PV_SMTP_PORT", "587"))
            with smtp_factory(host, port, timeout=30) as smtp:
                if port != 25:
                    smtp.starttls(context=ssl.create_default_context())
                if os.environ.get("PV_SMTP_USER"):
                    smtp.login(os.environ["PV_SMTP_USER"], os.environ.get("PV_SMTP_PASSWORD", ""))
                smtp.send_message(msg)
            status.append("E-Mail gesendet")
        except Exception as exc:  # noqa: BLE001
            log.error("E-Mail fehlgeschlagen: %s", exc)
            status.append(f"E-Mail fehlgeschlagen: {exc}")
    return status
