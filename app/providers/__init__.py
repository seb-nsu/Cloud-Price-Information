"""Registry der Abruf-Module.

Jedes Modul stellt bereit:
    LABEL: str             – Anzeigename
    INFO: str              – kurzer Hinweis zur Quelle
    fetch(cfg) -> FetchResult
"""
from . import aws, azure, gcp, hetzner, ionos, otc, ovh, scaleway, stackit

PROVIDERS = {
    "stackit": stackit,
    "ionos": ionos,
    "otc": otc,
    "azure": azure,
    "aws": aws,
    "gcp": gcp,
    "hetzner": hetzner,
    "scaleway": scaleway,
    "ovh": ovh,
}
