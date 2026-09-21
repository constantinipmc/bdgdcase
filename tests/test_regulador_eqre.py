# -*- coding: utf-8 -*-
"""O regulador sai do cadastro, e não de uma constante.

Até aqui **todo** regulador de **toda** distribuidora saía igual: 333,3 kVA por
fase, `XHL=1%`, `%LoadLoss=0,02%`. Nenhum desses números veio de cadastro
nenhum — e a BDGD tem os três, na camada `EQRE`, junto com as perdas em ferro
e em cobre e as relações de TP e TC.

O modo que os lê (`REG_MODE='bdgd_electric'`) já existia e já estava ligado. O
que faltava eram duas coisas, e cada uma sozinha bastava para anular a outra: a
extração descartava `UN_RE`, que é o elo entre o catálogo e a unidade, e o
pipeline nunca carregava a `EQRE`. O código lia `row.get('XHL')` de uma linha
que não tinha a coluna, e caía no padrão — sempre.
"""
import math

import pandas as pd
import pytest

from bdgdcase.modelo.cadastro import (
    REG_KVA_PASSAGEM_MAX, REG_KVA_PASSAGEM_MIN, REG_XHL_CRIVEL_MAX,
    REG_XHL_CRIVEL_MIN, TPOTAPRT_KVA, juntar_eqre, kva_de_codigo_tpotaprt,
    xhl_regulador)


# ═════════════════════════════════════════════════════════════════════════════
# A reatância: ao pé da letra, dentro da faixa de um regulador
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize('publicado', [0.352, 0.422, 0.592, 0.711, 0.825, 0.990, 1.000])
def test_o_xhl_publicado_e_lido_ao_pe_da_letra(publicado):
    """Os valores das quatro bases medidas já são a percentagem.

    Copel publica 0,352 a 0,592; a Celesc, 0,494 a 1,000; a RGE, 0,825; a
    Coopera, 0,400 a 1,000. Todos caem na faixa física de um regulador de
    degraus, e ler outra coisa ali é inventar.
    """
    assert xhl_regulador(publicado) == pytest.approx(publicado)


def test_o_x10_nao_se_aplica_ao_regulador():
    """A regra do ×10 punha a Celesc em 9,9%, que é impedância de transformador.

    Medido num alimentador dela: derruba a tensão mínima de 0,843 para 0,753 pu
    e leva os passos que não fecham o balanço de 22 para 45 em 96. A leitura
    literal dá 0,71% e reproduz a tensão de antes.
    """
    assert xhl_regulador(0.99) == pytest.approx(0.99)
    assert xhl_regulador(0.99) != pytest.approx(9.9)


def test_o_valor_em_decimos_ou_em_fracao_ainda_e_alcancado():
    """A faixa crível é o critério, e não a escala em que a base escreveu."""
    assert xhl_regulador(0.04) == pytest.approx(0.4)     # ×10
    assert xhl_regulador(0.0685) == pytest.approx(0.685)  # ×10
    assert xhl_regulador(0.008) == pytest.approx(0.8)    # ×100


@pytest.mark.parametrize('fora', [9.9, 55.0, 3.0, None, 'x', 0, -0.0])
def test_o_que_nao_descreve_regulador_cai_no_padrao(fora):
    """O 'muito fora da realidade': impedância errada não dá erro, dá queda."""
    assert xhl_regulador(fora, default=1.0) == 1.0


def test_a_faixa_crivel_cobre_o_que_as_bases_publicam():
    """Se alguém apertar a faixa, é aqui que o dado real reclama."""
    assert REG_XHL_CRIVEL_MIN <= 0.352
    assert REG_XHL_CRIVEL_MAX >= 1.000


# ═════════════════════════════════════════════════════════════════════════════
# A junção EQRE → UNREMT
# ═════════════════════════════════════════════════════════════════════════════

def _eqre(un_re='A', **campos):
    base = {'UN_RE': [un_re], 'POT_NOM': [61], 'R': [0.7029], 'XHL': [0.711],
            'PER_FER': [300.0], 'PER_TOT': [900.0]}
    base.update({k: [v] for k, v in campos.items()})
    return pd.DataFrame(base)


def test_o_catalogo_chega_na_unidade():
    j = juntar_eqre(pd.DataFrame({'COD_ID': ['A']}), _eqre())
    assert j['XHL'].iloc[0] == pytest.approx(0.711)
    assert j['R'].iloc[0] == pytest.approx(0.7029)
    assert j['PER_TOT'].iloc[0] == pytest.approx(900.0)


def test_a_potencia_e_um_codigo_e_nao_kva():
    """`POT_NOM=61` na `EQRE` é o código de 4.000 kVA, não 61 kVA.

    Uma primeira versão leu 61 como kVA e dividiu por uma faixa de regulação
    inventada: 610 kVA onde há 4.000. Achado comparando com `bdgd2opendss`.
    """
    j = juntar_eqre(pd.DataFrame({'COD_ID': ['A']}), _eqre())
    assert j['POT_NOM'].iloc[0] == pytest.approx(4000.0)


@pytest.mark.parametrize('codigo, kva', [
    (33, 288), (43, 750), (48, 1500), (51, 2250), (55, 2750),
    (61, 4000), (62, 4200), (65, 6250), (67, 7000),
])
def test_os_codigos_das_quatro_bases(codigo, kva):
    """Os que aparecem nas bases medidas: Copel, Celesc, RGE e Coopera."""
    assert kva_de_codigo_tpotaprt(codigo) == kva


@pytest.mark.parametrize('nao_codigo', [61.5, -1, 107, 'x', None, float('nan')])
def test_o_que_nao_e_codigo_nao_vira_potencia(nao_codigo):
    assert math.isnan(kva_de_codigo_tpotaprt(nao_codigo))


def test_a_tabela_e_monotonica_e_passa_pelos_tamanhos_padrao():
    """A forma da tabela confirma que é o domínio: sobe sempre e bate nos
    tamanhos de catálogo."""
    valores = [TPOTAPRT_KVA[k] for k in sorted(TPOTAPRT_KVA)]
    assert valores == sorted(valores)
    for padrao in (37.5, 45, 75, 112.5, 150, 225, 300, 500, 750, 1000, 1500):
        assert padrao in valores


def test_a_potencia_nao_depende_do_TEN_REG():
    """`TEN_REG` é o valor que o regulador sustenta, não tem a ver com kVA."""
    a = juntar_eqre(pd.DataFrame({'COD_ID': ['A']}), _eqre(TEN_REG=1.0))
    b = juntar_eqre(pd.DataFrame({'COD_ID': ['A']}), _eqre(TEN_REG=1.1))
    assert a['POT_NOM'].iloc[0] == pytest.approx(b['POT_NOM'].iloc[0])


def test_potencia_incrivel_e_descartada():
    """Percentual sobre base absurda é impedância absurda."""
    minusculo = juntar_eqre(pd.DataFrame({'COD_ID': ['A']}), _eqre(POT_NOM=1))    # 3 kVA
    gigante = juntar_eqre(pd.DataFrame({'COD_ID': ['A']}), _eqre(POT_NOM=106))  # 41,5 GVA
    assert math.isnan(minusculo['POT_NOM'].iloc[0])
    assert math.isnan(gigante['POT_NOM'].iloc[0])
    assert REG_KVA_PASSAGEM_MIN < REG_KVA_PASSAGEM_MAX


def test_o_banco_entra_pela_mediana():
    """Um regulador tem uma linha por fase, e reserva. Uma linha destoante não
    pode mandar sozinha."""
    eq = pd.DataFrame({'UN_RE': ['A'] * 4, 'POT_NOM': [61] * 4,
                       'XHL': [0.7, 0.71, 0.72, 99.0]})
    j = juntar_eqre(pd.DataFrame({'COD_ID': ['A']}), eq)
    assert j['XHL'].iloc[0] == pytest.approx(0.715)


def test_a_unidade_manda_sobre_o_catalogo():
    """`UNREMT` é a autoridade sobre si mesma; `EQRE` preenche o que falta."""
    un = pd.DataFrame({'COD_ID': ['A'], 'XHL': [0.5]})
    j = juntar_eqre(un, _eqre())
    assert j['XHL'].iloc[0] == pytest.approx(0.5)


@pytest.mark.parametrize('caso', ['sem eqre', 'sem elo', 'nada casa', 'sem unremt'])
def test_sem_o_que_juntar_a_tabela_volta_intacta(caso):
    """O recuo é o comportamento de antes, e não uma falha."""
    un = pd.DataFrame({'COD_ID': ['A'], 'PAC_1': ['x']})
    if caso == 'sem eqre':
        assert juntar_eqre(un, None) is un
    elif caso == 'sem elo':
        assert juntar_eqre(un, _eqre().drop(columns=['UN_RE'])) is un
    elif caso == 'nada casa':
        assert juntar_eqre(un, _eqre(un_re='OUTRO')) is un
    else:
        assert juntar_eqre(None, _eqre()) is None


def test_a_extracao_guarda_o_elo():
    """Sem `UN_RE` a `EQRE` sai sem dizer de quem é cada linha — e foi o que
    manteve o modo `bdgd_electric` sem dado desde que ele existe."""
    import inspect

    from bdgdcase.extracao import camadas

    fonte = inspect.getsource(camadas.limpar_colunas)
    assert "'UN_RE'" in fonte
    assert "'UNI_TR_MT'" in fonte, 'o elo do transformador é o precedente'
