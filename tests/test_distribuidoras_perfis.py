# -*- coding: utf-8 -*-
"""Os perfis por distribuidora: identificar, conferir, e não atrapalhar.

O que estes perfis fazem é dizer, em voz alta, quando a base que chegou não é a
que se documentou. É a peça que faltava quando a segunda distribuidora entrou:
ela converteu, resolveu, mostrou a baixa a 1,76 pu, e nada no caminho disse
"isto não é o que se esperava".

Por isso o teste mais importante deste arquivo não é nenhum dos que conferem —
é `test_perfil_desconhecido_nao_atrapalha`. Uma conferência que impeça o caso de
sair troca um modo de falha silencioso por outro pior.
"""
import pandas as pd
import pytest

from bdgdcase.distribuidoras import (
    DESCONHECIDA,
    PERFIS,
    identificar,
    perfil_por_dist,
)
from bdgdcase.distribuidoras import celesc, coopera, copel, rge


# ═════════════════════════════════════════════════════════════════════════════
# Identificação
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize('dist, esperado', [
    ('5697', 'Celesc Distribuição (SC)'),
    ('396', 'RGE Sul (RS)'),
    ('5370', 'Coopera (SC)'),
    ('2866', 'Copel Distribuição (PR)'),
])
def test_o_dist_identifica_a_base(dist, esperado):
    assert perfil_por_dist(dist).nome == esperado


def test_o_dist_sobrevive_a_travessia_por_csv():
    """`396` vira `396.0` quando a coluna passa por CSV e volta float."""
    assert perfil_por_dist('396.0').dist == '396'
    assert perfil_por_dist(396).dist == '396'
    assert perfil_por_dist(' 396 ').dist == '396'


def test_identificar_aceita_o_ctmt_como_ele_chega():
    ctmt = pd.DataFrame({'DIST': ['396'], 'COD_ID': ['URB13']})
    assert identificar(ctmt).dist == '396'
    assert identificar({'DIST': '5370'}).dist == '5370'
    assert identificar('5697').dist == '5697'


@pytest.mark.parametrize('entrada', [
    None,
    pd.DataFrame(),
    pd.DataFrame({'COD_ID': ['X']}),          # sem a coluna DIST
    '99999',                                   # base ainda não vista
    object(),
])
def test_o_que_nao_se_reconhece_vira_desconhecida_sem_levantar(entrada):
    """Identificar é para conferir, não para converter: nunca pode falhar."""
    assert identificar(entrada) is DESCONHECIDA


def test_perfil_desconhecido_nao_atrapalha():
    """O teste que mais importa aqui.

    Uma quarta distribuidora tem de converter igual. As regras do conversor são
    sobre o dado, e não sobre quem o publicou — se um perfil ausente virasse
    obstáculo, estes módulos teriam trocado um modo de falha silencioso por um
    pior.
    """
    assert DESCONHECIDA.conferir({'untrmt': pd.DataFrame({'TIP_TRAFO': ['Z'],
                                                          'TEN_LIN_SE': [0.99]})}) == []
    assert DESCONHECIDA.conferir({}) == []


# ═════════════════════════════════════════════════════════════════════════════
# Conferência
# ═════════════════════════════════════════════════════════════════════════════

def test_tabela_ausente_nao_gera_achado():
    """Conferir o que não veio é ruído; disso já cuida o log de camadas."""
    for perfil in PERFIS.values():
        assert perfil.conferir({}) == []


def test_tip_trafo_fora_do_documentado_e_erro():
    """O roteamento de fases sai do TIP_TRAFO. Um valor novo o compromete."""
    achados = celesc.PERFIL.conferir({
        'untrmt': pd.DataFrame({'TIP_TRAFO': ['T', 'MT', 'B'],
                                'TEN_LIN_SE': [0.38, 0.44, 0.38]})})
    erros = [a for a in achados if a.nivel == 'erro' and 'TIP_TRAFO' in a.texto]
    assert erros and "'B'" in erros[0].texto


def test_o_bifasico_da_rge_e_contado_por_ser_concessao():
    """A única simplificação consciente do conjunto tem de aparecer no log."""
    achados = rge.PERFIL.conferir({
        'untrmt': pd.DataFrame({'TIP_TRAFO': ['T', 'B', 'B'],
                                'TEN_LIN_SE': [0.38, 0.22, 0.22]})})
    texto = ' '.join(a.texto for a in achados)
    assert '2 transformador(es) bifasico' in texto
    assert '254 V' in texto


def test_tensao_de_baixa_nao_documentada_e_avisada():
    achados = celesc.PERFIL.conferir({
        'untrmt': pd.DataFrame({'TIP_TRAFO': ['T'], 'TEN_LIN_SE': [0.11]})})
    assert any('0.11' in a.texto for a in achados)


def test_a_coopera_declara_que_nao_traz_curvas():
    """E o perfil avisa quando, ao contrário, ela trouxer."""
    assert coopera.PERFIL.tem_curvas_de_carga is False
    achados = coopera.PERFIL.conferir({'crvcrg': pd.DataFrame({'COD_ID': ['x']})})
    assert any('esperava vazia' in a.texto for a in achados)


def test_a_celesc_avisa_quando_a_crvcrg_vem_vazia():
    achados = celesc.PERFIL.conferir({'crvcrg': pd.DataFrame()})
    assert any('catalogo de referencia' in a.texto for a in achados)


def test_o_tip_cc_em_branco_da_coopera_e_reconhecido():
    """Um espaço não é vazio, e é isso que fazia toda carga virar residencial."""
    ucbt = pd.DataFrame({'TIP_CC': [' ', ' ', ' '], 'CLAS_SUB': ['RE1'] * 3})
    achados = coopera.PERFIL.conferir({'ucbt': ucbt})
    assert any('CLAS_SUB' in a.texto for a in achados)


def test_sem_clas_sub_o_tip_cc_em_branco_vira_erro():
    """Aí não há de onde tirar a classe, e toda carga sai residencial."""
    achados = coopera.PERFIL.conferir({'ucbt': pd.DataFrame({'TIP_CC': [' '] * 3})})
    erros = [a for a in achados if a.nivel == 'erro']
    assert erros and 'residencial' in erros[0].texto


def test_o_ramal_invertido_da_coopera_e_reconhecido():
    """PAC_1 é a unidade e PAC_2 é o poste — o contrário das outras duas."""
    ram = pd.DataFrame({'PAC_1': ['UC1', 'UC2'], 'PAC_2': ['BT9', 'BT9']})
    ssdbt = pd.DataFrame({'PAC_1': ['BT9'], 'PAC_2': ['BT8']})
    achados = coopera.PERFIL.conferir({'ramlig': ram, 'ssdbt': ssdbt})
    assert any('invertidos' in a.texto for a in achados)

    # e a base que segue a convenção comum não gera achado nenhum
    ram_normal = pd.DataFrame({'PAC_1': ['BT9', 'BT9'], 'PAC_2': ['UC1', 'UC2']})
    assert not [a for a in celesc.PERFIL.conferir(
        {'ramlig': ram_normal, 'ssdbt': ssdbt}) if 'RAMLIG' in a.texto]


def test_uma_conferencia_que_estoure_nao_derruba_a_conversao():
    """Um perfil com defeito não pode impedir o caso de sair."""
    from bdgdcase.distribuidoras.base import Distribuidora

    def explode(_tabelas):
        raise ValueError('proposital')

    p = Distribuidora(dist='0', nome='teste', resumo='', conferencias=(explode,))
    achados = p.conferir({})
    assert len(achados) == 1 and achados[0].nivel == 'aviso'


# ═════════════════════════════════════════════════════════════════════════════
# O dossiê
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize('modulo', [celesc, rge, coopera, copel])
def test_cada_modulo_documenta_a_sua_base(modulo):
    """O valor destes arquivos está no texto, e texto some sem teste."""
    doc = modulo.__doc__
    assert doc and len(doc) > 1200, 'o dossiê encolheu'
    assert 'DIST=' in doc
    assert modulo.PERFIL.dist in doc
    # tem de haver número medido, não só afirmação
    assert any(c.isdigit() for c in doc)


def test_o_registro_esta_completo():
    assert set(PERFIS) == {'5697', '396', '5370', '2866'}
    for dist, perfil in PERFIS.items():
        assert perfil.dist == dist
        assert perfil.nome and perfil.resumo
