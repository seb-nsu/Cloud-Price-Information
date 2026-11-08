FROM python:3.12-slim

# Keine externen Python-Pakete nötig – nur Standardbibliothek
ENV PYTHONUNBUFFERED=1 \
    PV_DB=/data/preise.db \
    PV_CONFIG=/app/config.toml \
    PV_PORT=8080 \
    TZ=Europe/Berlin

WORKDIR /app
COPY app ./app
COPY config.toml ./config.toml

# /data gehört dem App-Benutzer; ein *named volume* übernimmt diese Rechte beim ersten Start.
RUN useradd --system --uid 10001 pv && mkdir -p /data && chown pv:pv /data
USER pv
VOLUME ["/data"]
EXPOSE 8080

# /health ist ohne Anmeldung erreichbar (auch wenn PV_USER/PV_PASSWORD gesetzt sind)
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ['PV_PORT']+'/health')" || exit 1

CMD ["python", "-m", "app", "serve"]
