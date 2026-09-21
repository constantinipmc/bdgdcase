# -*- coding: utf-8 -*-
"""Geometria solar — quem define a janela de sol da curva fotovoltaica.

São só duas propriedades, mas erradas deslocariam a geração inteira no tempo:
o dia é mais longo no verão do hemisfério em que o alimentador está, e a
duração do dia é simétrica em torno do meio-dia solar.
"""
from datetime import date

import pytest

from bdgdcase.solar import nascer_por_do_sol

# Araranguá/SC — o campus da UFSC de onde vem o trabalho.
LAT, LON, TZ = -28.94, -49.49, -3


def _duracao(dia, lat=LAT, lon=LON):
    nasce, poe = nascer_por_do_sol(lat, lon, dia, tz=TZ)
    return poe - nasce


def test_ordem_e_faixa():
    nasce, poe = nascer_por_do_sol(LAT, LON, date(2024, 3, 20), tz=TZ)
    assert 0.0 < nasce < poe < 24.0


def test_equinocio_da_cerca_de_doze_horas():
    assert _duracao(date(2024, 3, 20)) == pytest.approx(12.0, abs=0.2)


def test_no_hemisferio_sul_o_dia_longo_e_em_dezembro():
    verao = _duracao(date(2024, 12, 21))
    inverno = _duracao(date(2024, 6, 21))
    assert verao > 13.0 > 11.0 > inverno
    assert verao - inverno == pytest.approx(3.0, abs=1.0)


def test_no_hemisferio_norte_a_estacao_inverte():
    # Mesma data, latitude espelhada: o solstício de dezembro passa a ser o curto.
    assert _duracao(date(2024, 12, 21), lat=-LAT) < \
           _duracao(date(2024, 6, 21), lat=-LAT)


def test_meio_dia_solar_e_o_centro_da_janela():
    nasce, poe = nascer_por_do_sol(LAT, LON, date(2024, 9, 10), tz=TZ)
    assert 11.0 < (nasce + poe) / 2.0 < 13.0


def test_no_equador_o_dia_quase_nao_varia():
    assert abs(_duracao(date(2024, 12, 21), lat=0.0, lon=LON)
               - _duracao(date(2024, 6, 21), lat=0.0, lon=LON)) < 0.2
