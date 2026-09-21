# -*- coding: utf-8 -*-
"""A curva fotovoltaica e o determinismo do pacote.

No projeto de origem `PV_BETA_SEED` é None e a curva sai diferente a cada
execução — o que é o que se quer ao amostrar cenários, e é um defeito num
conversor publicado. Aqui a semente é fixa, e este arquivo é o que impede que
volte a não ser.
"""
import pytest

from bdgdcase.curva_pv import (clear_sky_curve, curva_para_linha_dss,
                               gerar_curva_pv_beta, moving_average_same)


def test_curva_de_ceu_claro_e_zero_a_noite():
    c = clear_sky_curve(npts=96, hora_inicio=6.0, hora_fim=18.0,
                        intervalo_horas=0.25)
    assert len(c) == 96
    assert c[0] == 0.0            # meia-noite
    assert c[95] == 0.0           # 23h45
    assert max(c) > 0.0           # e há sol no meio do dia


def test_curva_normalizada_no_pico():
    c = gerar_curva_pv_beta(npts=96, seed=1)
    assert len(c) == 96
    assert max(c) == pytest.approx(1.0)
    assert min(c) >= 0.0


def test_mesma_semente_mesma_curva():
    assert gerar_curva_pv_beta(npts=96, seed=20260101) == \
           gerar_curva_pv_beta(npts=96, seed=20260101)


def test_sementes_diferentes_curvas_diferentes():
    assert gerar_curva_pv_beta(npts=96, seed=1) != \
           gerar_curva_pv_beta(npts=96, seed=2)


def test_conversao_usa_semente_fixa():
    """A diferença deliberada em relação ao projeto de origem, fixada em teste.

    Sem isto não há teste de regressão possível no conversor, nem resultado que
    outra pessoa consiga reproduzir a partir da mesma BDGD.
    """
    from bdgdcase.modelo.config import PV_BETA_SEED
    assert PV_BETA_SEED is not None


def test_media_movel_preserva_o_comprimento():
    assert len(moving_average_same([1.0, 2.0, 3.0, 4.0], window=3)) == 4


def test_parametros_invalidos_sao_recusados():
    with pytest.raises(ValueError):
        gerar_curva_pv_beta(alpha=0.0)
    with pytest.raises(ValueError):
        gerar_curva_pv_beta(beta_param=-1.0)
    with pytest.raises(ValueError):
        gerar_curva_pv_beta(n_dias_mc=0)


def test_linha_dss_tem_a_forma_que_o_opendss_le():
    linha = curva_para_linha_dss(gerar_curva_pv_beta(npts=96, seed=7))
    assert linha.startswith('New Loadshape.Curva_PV npts=96 minterval=15 mult=[')
    assert linha.rstrip().endswith(']')
    assert len(linha.split('mult=[')[1].rstrip(']').split()) == 96
