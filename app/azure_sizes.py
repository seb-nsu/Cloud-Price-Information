"""Ausstattung von Azure-VM-Größen (vCPU, RAM, GPU) für den VM-Vergleich.

Die Azure Retail Prices API liefert nur den Größennamen (`armSkuName`), keine Ausstattung.
Für Serien mit festem RAM-Verhältnis wird der RAM aus dem Namen abgeleitet, für alle
anderen (B v1, M, N/GPU) steht die Ausstattung in den Tabellen unten.
Quelle: Microsoft Learn, „Sizes for virtual machines in Azure“ (Stand 2026).
Neue Größen lassen sich hier ergänzen; unbekannte Größen erscheinen nicht im VM-Vergleich.
"""
from __future__ import annotations

import re

# Größe ohne "Standard_" -> (vCPU, RAM in GB, GPUs, GPU-Modell)
# GPUs < 1 = Anteil einer GPU (Teil-GPU-Größen der NV-Serien)
FIXED: dict[str, tuple[float, float, float, str]] = {
    # --- B-Serie v1 (burstfähig, uneinheitliches RAM-Verhältnis)
    "B1ls": (1, 0.5, 0, ""), "B1s": (1, 1, 0, ""), "B1ms": (1, 2, 0, ""), "B2s": (2, 4, 0, ""),
    "B2ms": (2, 8, 0, ""), "B4ms": (4, 16, 0, ""), "B8ms": (8, 32, 0, ""), "B12ms": (12, 48, 0, ""),
    "B16ms": (16, 64, 0, ""), "B20ms": (20, 80, 0, ""),
    # --- M-Serie (arbeitsspeicheroptimiert, SAP HANA)
    "M8ms": (8, 218.75, 0, ""), "M16ms": (16, 437.5, 0, ""), "M32ts": (32, 192, 0, ""),
    "M32ls": (32, 256, 0, ""), "M32ms": (32, 875, 0, ""), "M64s": (64, 1024, 0, ""),
    "M64ls": (64, 512, 0, ""), "M64ms": (64, 1792, 0, ""), "M128s": (128, 2048, 0, ""),
    "M128ms": (128, 3892, 0, ""), "M64": (64, 1000, 0, ""), "M64m": (64, 1750, 0, ""),
    "M128": (128, 2000, 0, ""), "M128m": (128, 3800, 0, ""),
    "M32ms_v2": (32, 875, 0, ""), "M64s_v2": (64, 1024, 0, ""), "M64ms_v2": (64, 1792, 0, ""),
    "M128s_v2": (128, 2048, 0, ""), "M128ms_v2": (128, 3892, 0, ""), "M192is_v2": (192, 2048, 0, ""),
    "M192ims_v2": (192, 4096, 0, ""), "M32dms_v2": (32, 875, 0, ""), "M64ds_v2": (64, 1024, 0, ""),
    "M64dms_v2": (64, 1792, 0, ""), "M128ds_v2": (128, 2048, 0, ""), "M128dms_v2": (128, 3892, 0, ""),
    "M192ids_v2": (192, 2048, 0, ""), "M192idms_v2": (192, 4096, 0, ""),
    "M208s_v2": (208, 2850, 0, ""), "M208ms_v2": (208, 5700, 0, ""), "M416s_v2": (416, 5700, 0, ""),
    "M416ms_v2": (416, 11400, 0, ""),
    "M12s_v3": (12, 240, 0, ""), "M24s_v3": (24, 480, 0, ""), "M12ds_v3": (12, 240, 0, ""),
    "M24ds_v3": (24, 480, 0, ""),
    # --- N-Serie (GPU)
    "NC4as_T4_v3": (4, 28, 1, "T4"), "NC8as_T4_v3": (8, 56, 1, "T4"), "NC16as_T4_v3": (16, 110, 1, "T4"),
    "NC64as_T4_v3": (64, 440, 4, "T4"),
    "NC6s_v3": (6, 112, 1, "V100"), "NC12s_v3": (12, 224, 2, "V100"), "NC24s_v3": (24, 448, 4, "V100"),
    "NC24rs_v3": (24, 448, 4, "V100"),
    "NC24ads_A100_v4": (24, 220, 1, "A100"), "NC48ads_A100_v4": (48, 440, 2, "A100"),
    "NC96ads_A100_v4": (96, 880, 4, "A100"),
    "NC40ads_H100_v5": (40, 320, 1, "H100"), "NC80adis_H100_v5": (80, 640, 2, "H100"),
    "ND96asr_v4": (96, 900, 8, "A100"), "ND96amsr_A100_v4": (96, 1900, 8, "A100"),
    "ND96isr_H100_v5": (96, 1900, 8, "H100"),
    "NV6ads_A10_v5": (6, 55, 1 / 6, "A10"), "NV12ads_A10_v5": (12, 110, 1 / 3, "A10"),
    "NV18ads_A10_v5": (18, 220, 0.5, "A10"), "NV36ads_A10_v5": (36, 440, 1, "A10"),
    "NV36adms_A10_v5": (36, 880, 1, "A10"), "NV72ads_A10_v5": (72, 880, 2, "A10"),
    "NV12s_v3": (12, 112, 1, "M60"), "NV24s_v3": (24, 224, 2, "M60"), "NV48s_v3": (48, 448, 4, "M60"),
    "NV4as_v4": (4, 14, 0.125, "MI25"), "NV8as_v4": (8, 28, 0.25, "MI25"), "NV16as_v4": (16, 56, 0.5, "MI25"),
    "NV32as_v4": (32, 112, 1, "MI25"),
}

_RULE = re.compile(r"^([A-Z]+)(\d+)(-\d+)?([a-z]*)_v(\d+)$")
_RULE_V1 = re.compile(r"^([A-Z]+)(\d+)([a-z]*)$")


def _ratio(series: str, suffix: str, gen: int) -> float | None:
    """GB RAM je vCPU für Serien mit festem Verhältnis, sonst None."""
    s = series[0]
    if s == "D" and gen >= 3:
        return 2 if "l" in suffix else 4
    if s == "E" and gen >= 3:
        return 8
    if s == "F" and series == "F":
        if gen >= 6:
            return 2 if "l" in suffix else 8 if "m" in suffix else 4
        return 2                                   # F v1 und Fsv2
    if s == "L":
        return 8                                   # Ls, Lsv2, Lsv3, Lasv3
    if s == "B" and gen == 2:
        return 0.5 if "t" in suffix else 2 if "l" in suffix else 4
    return None


def lookup(arm_sku: str) -> tuple[float, float, float, str] | None:
    """'Standard_D4s_v5' -> (4, 16, 0, ''); unbekannt bzw. vCPU-eingeschränkt -> None."""
    name = (arm_sku or "").removeprefix("Standard_")
    if not name:
        return None
    if name in FIXED:
        v, r, g, m = FIXED[name]
        return float(v), float(r), float(g), m
    m = _RULE.match(name)
    if m:
        if m.group(3):          # vCPU-eingeschränkte Varianten (z. B. D8-4s_v5) auslassen
            return None
        series, vcpu, suffix, gen = m.group(1), float(m.group(2)), m.group(4), int(m.group(5))
    else:
        m = _RULE_V1.match(name)
        if not m:
            return None
        series, vcpu, suffix, gen = m.group(1), float(m.group(2)), m.group(3), 1
        if series[0] not in "FL":   # D/E v1/v2 haben abweichende Verhältnisse
            return None
    if series[0] in "NM" or (series[0] == "B" and gen == 1):
        return None             # nur über die Tabelle
    ratio = _ratio(series, suffix, gen)
    if ratio is None:
        return None
    return vcpu, vcpu * ratio, 0.0, ""
