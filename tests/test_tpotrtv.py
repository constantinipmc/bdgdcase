# -*- coding: utf-8 -*-
"""A decodificação de potência do banco de capacitores.

`POT_NOM` do UNCRMT é um código do catálogo TPOTRTV, não um valor: `18` quer
dizer 3.600 kVAr. Quem lê a coluna crua e a trata como potência erra por três
ordens de grandeza.

O que estes testes guardam são dois modos de falha opostos, e o segundo nasceu
da correção do primeiro:

1. o valor que o catálogo não conhece era **destruído** — virava `NaN` e o
   conversor caía no padrão sem saber que a base tinha dito outra coisa;
2. preservá-lo por atribuição parcial exige que o valor caiba no dtype que a
   coluna já tem, e há base em que `POT_NOM` vem como **texto**. Escrever um
   float ali levanta `TypeError` e derruba a extração da camada inteira.
"""
import pandas as pd
import pytest

from bdgdcase.extracao import TPOTRTV_KVAR, aplicar_tpotrtv


def test_o_codigo_vira_kvar():
    df = aplicar_tpotrtv(pd.DataFrame({'POT_NOM': [6, 10, 18, 22]}))
    assert list(df['POT_NOM']) == [300.0, 600.0, 3600.0, 7200.0]
    assert list(df['POT_NOM_KVAR']) == [300.0, 600.0, 3600.0, 7200.0]
    assert list(df['POT_NOM_COD']) == [6, 10, 18, 22]


@pytest.mark.parametrize('dtype', ['object', 'str', 'string'])
def test_a_coluna_de_texto_nao_derruba_a_extracao(dtype):
    """O defeito medido: `TypeError: Invalid value for dtype 'str'`.

    A coluna é substituída inteira justamente por isso — trocar a coluna leva o
    dtype junto, e é o que se quer, porque o consumidor a jusante espera kVAr
    numérico.
    """
    df = pd.DataFrame({'POT_NOM': pd.Series(['6', '10', '18'], dtype=dtype)})
    fora = aplicar_tpotrtv(df)
    assert list(fora['POT_NOM']) == [300.0, 600.0, 3600.0]
    assert str(fora['POT_NOM'].dtype) == 'float64'


def test_o_valor_fora_do_catalogo_e_preservado(caplog):
    """600 não é código do TPOTRTV; é kVAr que alguma base pode ter escrito.

    Destruí-lo entregava `NaN` ao conversor, que caía no padrão. Preservado, ele
    chega até quem sabe distinguir código de valor pelo tamanho.
    """
    df = aplicar_tpotrtv(pd.DataFrame({'POT_NOM': [6, 600, 18]}))
    assert list(df['POT_NOM']) == [300.0, 600.0, 3600.0]
    # e o que veio do catálogo continua separado do que foi preservado
    assert df['POT_NOM_KVAR'].isna().tolist() == [False, True, False]
    assert 'fora do dominio' in caplog.text


def test_o_ilegivel_continua_virando_nan():
    """Sem valor não há o que preservar, e `NaN` é a resposta honesta."""
    df = aplicar_tpotrtv(pd.DataFrame({'POT_NOM': ['6', 'sem valor', None]}))
    assert df['POT_NOM'].iloc[0] == 300.0
    assert df['POT_NOM'].isna().tolist() == [False, True, True]


def test_sem_a_coluna_a_tabela_passa_inteira():
    df = pd.DataFrame({'COD_ID': ['a', 'b']})
    assert aplicar_tpotrtv(df) is df


def test_o_catalogo_cobre_a_faixa_da_norma():
    """PRODIST Módulo 10: 0 a 28, de 45 kVAr a 30 MVAr."""
    assert TPOTRTV_KVAR[0] == 0
    assert TPOTRTV_KVAR[16] == 2400
    assert set(TPOTRTV_KVAR) == set(range(29))
