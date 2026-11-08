"""Registry der Abruf-Module.

Jedes Modul stellt bereit:
    LABEL: str             – Anzeigename
    INFO: str              – kurzer Hinweis zur Quelle
    fetch(cfg) -> FetchResult
"""
from . import aws, azure, gcp, ionos, otc, stackit

PROVIDERS = {
    "stackit": stackit,
    "ionos": ionos,
    "otc": otc,
    "azure": azure,
    "aws": aws,
    "gcp": gcp,
}
