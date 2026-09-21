"""Cálculo de nascer/pôr-do-sol via algoritmo NOAA (sem dependência externa).

Referência: https://gml.noaa.gov/grad/solcalc/calcdetails.html Precisão: ~1
minuto para latitudes |φ| < 60°.

Uso típico no projeto:
    from bdgdcase.solar import nascer_por_do_sol
    from datetime import date
    h_nasc, h_por = nascer_por_do_sol(-27.02, -51.15, date(2024, 12, 15), tz=-3)
    # ≈ (5.30, 19.05)

Esses horários alimentam `modelo/curvas.py`: a janela da Curva_PV (Beta) e a
da curva genérica de iluminação pública. Em uso normal a posição vem do
centroide do alimentador; `LATITUDE`/`LONGITUDE`/`DATA_SIMULACAO` em
`modelo/config.py` são o fallback.
"""

from __future__ import annotations

import math
from datetime import date


def _dia_juliano(d: date) -> int:
    """Número de dia juliano (sem fração horária)."""
    a = (14 - d.month) // 12
    y = d.year + 4800 - a
    m = d.month + 12 * a - 3
    return d.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045


def nascer_por_do_sol(
    lat: float,
    lon: float,
    dia: date,
    tz: int = -3,
) -> tuple[float, float]:
    """Retorna (h_nascer, h_por) em horas decimais locais.

    Args:
        lat: latitude em graus (sul negativo).
        lon: longitude em graus (oeste negativo).
        dia: data civil local.
        tz:  fuso horário em horas inteiras (Brasil sem horário de verão = -3).

    Returns:
        (nascer, pôr) em horas decimais (ex.: 5.30 = 05h18).
    """
    jd = _dia_juliano(dia) - 2451545.0 + 0.5 - lon / 360.0

    g = math.radians((357.5291 + 0.98560028 * jd) % 360)
    lam = math.radians(
        (
            280.4665
            + 0.98564736 * jd
            + 1.9148 * math.sin(g)
            + 0.0200 * math.sin(2 * g)
        )
        % 360
    )

    obliq = math.radians(23.44)
    decl = math.asin(math.sin(obliq) * math.sin(lam))

    # Right ascension trazido para [0, 360) para casar com lam_deg
    alpha_deg = math.degrees(
        math.atan2(math.sin(lam) * math.cos(obliq), math.cos(lam))
    ) % 360
    lam_deg = math.degrees(lam)
    diff = lam_deg - alpha_deg
    # Garante |diff| <= 180 (lam e alpha estao no mesmo "ramo")
    if diff > 180:
        diff -= 360
    elif diff < -180:
        diff += 360
    eqt = 4 * (diff - 0.0057183)

    cos_h = (
        math.sin(math.radians(-0.83)) - math.sin(math.radians(lat)) * math.sin(decl)
    ) / (math.cos(math.radians(lat)) * math.cos(decl))
    cos_h = max(-1.0, min(1.0, cos_h))
    H = math.degrees(math.acos(cos_h)) / 15.0

    meio_dia_solar = 12.0 - lon / 15.0 - eqt / 60.0 + tz
    return meio_dia_solar - H, meio_dia_solar + H

