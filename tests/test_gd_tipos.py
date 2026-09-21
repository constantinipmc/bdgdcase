# -*- coding: utf-8 -*-
"""A recuperação da fonte da geração, que a BDGD não declara.

`TIP_GER` não existe nas tabelas de unidades geradoras, `TIP_SIST` vale
'RD_INTERLIG' em 100% dos registros, e o prefixo `GD.` do CEG significa micro ou
minigeração — não solar. O tipo é reconstruído pelo fator de capacidade anual, e
é esse critério que estes testes fixam.
"""
import pandas as pd
import pytest

from bdgdcase.gd_tipos import (COLS_ENE, FC_MAX_SOLAR, classificar_tipo_gd,
                               fator_capacidade, fator_capacidade_row,
                               fc_efetivo, prefixo_ceg)


def _linha(pot_inst, fc_alvo, ceg='GD.SC.000001-1.01'):
    """Uma linha de UGBT com POT_INST e ENE_01..12 que dão exatamente `fc_alvo`."""
    energia_mes = pot_inst * 8760.0 * fc_alvo / 12.0
    d = {'POT_INST': pot_inst, 'CEG_GD': ceg}
    d.update({c: energia_mes for c in COLS_ENE})
    return pd.Series(d)


def test_fator_capacidade_e_a_definicao():
    # 10 kW rendendo 8.760 kWh no ano = 10% de fator de capacidade.
    assert fator_capacidade(10.0, [730.0] * 12) == pytest.approx(0.10)


def test_fator_capacidade_indeterminado_da_zero():
    assert fator_capacidade(0.0, [100.0] * 12) == 0.0
    assert fator_capacidade(10.0, []) == 0.0
    assert fator_capacidade(None, None) == 0.0


def test_fator_capacidade_row_le_as_doze_colunas():
    assert fator_capacidade_row(_linha(5.0, 0.18)) == pytest.approx(0.18)


@pytest.mark.parametrize('ceg,esperado', [
    ('CGH.SC.000001-1.01', 'CGH'),
    ('PCH.SC.000001-1.01', 'CGH'),
    ('UHE.SC.000001-1.01', 'CGH'),
    ('EOL.SC.000001-1.01', 'EOL'),
    ('UTE.SC.000001-1.01', 'UTE'),
    ('UFV.SC.000001-1.01', 'PV'),
])
def test_prefixo_declarado_decide_sozinho(ceg, esperado):
    """Quando o CEG traz código próprio da ANEEL, o FC não é consultado."""
    assert classificar_tipo_gd(ceg, fc=0.99) == esperado


def test_prefixo_gd_nao_significa_solar():
    """O ponto do módulo: `GD.` é enquadramento regulatório, não fonte."""
    assert prefixo_ceg('GD.SC.000001-1.01') == 'GD'
    # Abaixo do teto: nada contradiz o telhado fotovoltaico.
    assert classificar_tipo_gd('GD.SC.000001-1.01', fc=0.08) == 'PV'
    # Acima do teto: nenhum FV alcança isso — é geração de base disfarçada.
    assert classificar_tipo_gd('GD.SC.000001-1.01', fc=0.47) == 'NSOL'


def test_limiar_e_estrito():
    assert classificar_tipo_gd('GD.SC.1-1.01', fc=FC_MAX_SOLAR) == 'PV'
    assert classificar_tipo_gd('GD.SC.1-1.01', fc=FC_MAX_SOLAR + 1e-9) == 'NSOL'


def test_sem_fc_o_registro_e_tratado_como_solar():
    """Sem energia declarada não há sinal contrário; assume-se micro-GD solar."""
    assert classificar_tipo_gd('GD.SC.1-1.01', fc=None) == 'PV'
    assert classificar_tipo_gd('', fc=None) == 'PV'


def test_uma_cgh_enquadrada_como_mini_gd_nao_vira_fotovoltaica():
    """O caso concreto que motiva o módulo, do CSV à classificação."""
    tipo = classificar_tipo_gd('GD.SC.000123-4.01',
                               fc=fator_capacidade_row(_linha(300.0, 0.46)))
    assert tipo == 'NSOL'


def test_fc_efetivo_cai_na_mediana_do_tipo_quando_falta_medida():
    assert fc_efetivo('CGH', None) == pytest.approx(0.46)
    assert fc_efetivo('CGH', 0.0) == pytest.approx(0.46)
    assert fc_efetivo('CGH', 'lixo') == pytest.approx(0.46)
    assert fc_efetivo('CGH', 0.51) == pytest.approx(0.51)   # medida válida vence
    assert fc_efetivo('DESCONHECIDO', None) == pytest.approx(0.45)
