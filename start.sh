#!/usr/bin/env bash
# Startet die Preisverwaltung lokal (nur Python 3.11+, keine weiteren Pakete) und öffnet den Browser.
cd "$(dirname "$0")"
export PV_HOST="${PV_HOST:-127.0.0.1}"
( for i in $(seq 1 60); do
    if curl -fs http://127.0.0.1:8080/health >/dev/null 2>&1; then
      (command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:8080) || (command -v open >/dev/null && open http://127.0.0.1:8080)
      break
    fi; sleep 1
  done ) >/dev/null 2>&1 &
exec python3 -m app serve
