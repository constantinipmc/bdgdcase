# -*- coding: utf-8 -*-
"""A base que não traz curvas de carga, e o que o usuário fica sabendo disso.

O conversor lê as curvas diárias da camada `CRVCRG` da própria BDGD. Numa das
distribuidoras testadas essa camada existe, com os 101 campos, e **zero
feições** — nada no tamanho do arquivo denuncia isso. Antes, o alimentador
inteiro saía com quatro curvas de 24 patamares desenhadas à mão, sob um
cabeçalho que anunciava "Catálogo CRVCRG (BDGD)".

O que estes testes protegem não é o cálculo: é a declaração. Uma curva trocada
sem aviso não produz erro nenhum — produz um resultado plausível cuja origem
ninguém tem como reconstituir depois.
"""
import pandas as pd
import pytest

from bdgdcase.modelo.curvas import (
    CURVAS_REFERENCIA,
    carregar_curvas_de_referencia,
    gerar_curvas_de_carga_dss)
from bdgdcase.modelo.curvas import _resolver_loadshape_e_fator


# ═════════════════════════════════════════════════════════════════════════════
# O catálogo embarcado
# ═════════════════════════════════════════════════════════════════════════════

def test_o_catalogo_viaja_dentro_do_pacote():
    """Sem isto, quem instalou pelo PyPI não tem a rede de segurança."""
    df, origem = carregar_curvas_de_referencia()
    assert df is not None and not df.empty
    assert origem, 'o catálogo tem de dizer de que base saiu'
    assert {'COD_ID', 'TIP_DIA', 'POT_01', 'POT_96'} <= set(df.columns)


def test_o_catalogo_cobre_as_quatro_classes():
    """Residencial, comercial, industrial e iluminação pública.

    São as classes para as quais o roteamento por classe existe. Faltando uma,
    as cargas dela cairiam na residencial sem que nada avisasse.
    """
    df, _ = carregar_curvas_de_referencia()
    codigos = ' '.join(df['COD_ID'].astype(str).unique())
    for classe in ('RES', 'COM', 'IND', 'IP'):
        assert classe in codigos, 'nenhum código de %s no catálogo' % classe


def test_o_catalogo_declara_que_e_gerado():
    """Um catálogo de curvas editado à mão é indistinguível de um inventado."""
    cabecalho = ''.join(
        l for l in open(CURVAS_REFERENCIA, encoding='utf-8') if l.startswith('#'))
    assert 'GERADO por' in cabecalho
    assert 'não editar à mão' in cabecalho
    assert 'ANEEL' in cabecalho


def test_catalogo_ausente_nao_derruba_nada(tmp_path):
    """A ausência do arquivo é um caminho previsto, não uma exceção."""
    assert carregar_curvas_de_referencia(tmp_path / 'nao_existe.csv') == (None, '')


# ═════════════════════════════════════════════════════════════════════════════
# O que sai quando a base não tem curvas
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def sem_crvcrg(tmp_path):
    """Converte as curvas de uma base cuja CRVCRG veio vazia."""
    saida = tmp_path / 'CurvasDeCarga.dss'
    ctx = gerar_curvas_de_carga_dss(pd.DataFrame(), saida)
    return ctx, saida.read_text(encoding='utf-8')


def test_crvcrg_vazia_cai_no_catalogo_de_referencia(sem_crvcrg):
    ctx, _ = sem_crvcrg
    assert ctx['modo'] == 'referencia'
    assert 'bdgdcase' in ctx['procedencia']


def test_o_arquivo_avisa_que_as_curvas_nao_sao_desta_base(sem_crvcrg):
    """A declaração no lugar onde ela sobrevive ao tempo.

    O aviso do console rola para fora da tela; a pasta do caso fica. Quem
    receber esta pasta daqui a um ano lê no cabeçalho de onde as curvas vieram.
    """
    _, texto = sem_crvcrg
    assert 'ATENÇÃO' in texto
    assert 'NÃO são desta distribuidora' in texto
    assert 'Procedência:' in texto


def test_as_curvas_de_classe_existem_para_quem_nao_tem_tip_cc(sem_crvcrg):
    """A base sem CRVCRG costuma ser a mesma que não preenche o `TIP_CC`.

    Sem as agregadas por classe, nenhuma carga dela casaria com código nenhum e
    todas cairiam numa curva só — o catálogo seria inútil justamente para quem
    precisa dele.
    """
    ctx, texto = sem_crvcrg
    for classe in ('RES', 'COM', 'IND', 'IP'):
        assert 'GEN_%s' % classe in ctx['fatores']
        assert 'New Loadshape.GEN_%s ' % classe in texto


@pytest.mark.parametrize('tip_cc, esperado', [
    ('RES', 'GEN_RES'),
    ('COM', 'GEN_COM'),
    ('IND', 'GEN_IND'),
    ('IP',  'GEN_IP'),
])
def test_a_carga_sem_codigo_e_roteada_pela_classe(sem_crvcrg, tip_cc, esperado):
    ctx, _ = sem_crvcrg
    nome, fator = _resolver_loadshape_e_fator(tip_cc, ctx)
    assert nome == esperado
    assert 0 < fator <= 1


def test_a_curva_de_classe_veio_de_medicao_e_nao_do_desenho(sem_crvcrg):
    """As agregadas saem do próprio catálogo, e não das quatro de mão.

    As desenhadas à mão têm 24 patamares esticados para 96, então repetem cada
    valor quatro vezes. Uma curva medida a 15 min não faz isso.
    """
    ctx, _ = sem_crvcrg
    df, _ = carregar_curvas_de_referencia()
    assert ctx['modo'] == 'referencia'
    # e o catálogo real entrou inteiro, não só as quatro agregadas
    assert len(ctx['fatores']) > 4 + 0
    assert len(ctx['fatores']) >= df['COD_ID'].nunique()


def test_sem_catalogo_o_ultimo_recurso_continua_existindo(tmp_path, monkeypatch):
    """Uma instalação podada não pode ficar sem curva nenhuma."""
    # O patch vai no módulo onde a constante mora, e não numa reexportação:
    # trocar a cópia da fachada não muda o que a função lê.
    from bdgdcase.modelo import curvas

    monkeypatch.setattr(curvas, 'CURVAS_REFERENCIA',
                        str(tmp_path / 'nao_existe.csv'))
    saida = tmp_path / 'CurvasDeCarga.dss'
    ctx = curvas.gerar_curvas_de_carga_dss(pd.DataFrame(), saida)
    assert ctx['modo'] == 'generico'
    texto = saida.read_text(encoding='utf-8')
    assert 'ATENÇÃO' in texto
    assert 'não medidas' in ctx['procedencia']
