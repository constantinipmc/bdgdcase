# -*- coding: utf-8 -*-
"""O registro do que o conversor mudou entre o cadastro cru e o caso.

A BDGD publicada não fecha um fluxo de potência como está. Entre ela e um caso
que converge há dezenas de decisões, e até aqui nenhuma delas chegava a quem
abre o resultado — o `.dss` tinha a mesma aparência de autoridade quer o
cadastro estivesse completo, quer tivesse sido remendado em quinze lugares.

O que estes testes guardam é a **honestidade** do registro, mais que a mecânica:
que um ajuste que não aconteceu não seja anotado, que uma pasta sem registro
diga que não sabe em vez de dizer que nada foi feito, e que falhar ao gravar
nunca custe o caso.
"""

import pytest

from bdgdcase import ajustes


@pytest.fixture(autouse=True)
def registro_limpo():
    ajustes.limpar(alimentador='TESTE', dia='DU')
    yield
    ajustes.limpar()


# ═════════════════════════════════════════════════════════════════════════════
# O que entra, e o que não entra
# ═════════════════════════════════════════════════════════════════════════════

def test_o_ajuste_anotado_aparece():
    ajustes.registrar('tensao', 'Alguma coisa', quantos=3, unidade='trafos',
                      detalhe='o que', porque='por que')
    (a,) = ajustes.listar()
    assert a['titulo'] == 'Alguma coisa'
    assert a['quantos'] == 3
    assert a['efeito'] == 'simulacao'


@pytest.mark.parametrize('quantos', [0, 0.0])
def test_o_ajuste_que_nao_aconteceu_nao_e_anotado(quantos):
    """Zero transformadores bifásicos não é uma linha na aba: é silêncio.

    Sem isso a aba encheria de "0 disto, 0 daquilo" e o que de fato aconteceu
    afundaria no meio.
    """
    ajustes.registrar('tensao', 'Não aconteceu', quantos=quantos)
    assert ajustes.listar() == []


def test_o_ajuste_sem_contagem_e_anotado():
    """Nem tudo se conta — "sem o arquivo de unidades por poste" não tem número."""
    ajustes.registrar('completude', 'Sem contagem', quantos=None,
                      efeito='nao_corrigido')
    assert len(ajustes.listar()) == 1


def test_os_exemplos_sao_poucos():
    """Seis bastam para ir conferir; sessenta viram parede de texto."""
    ajustes.registrar('condutores', 'Muitos', quantos=99,
                      exemplos=['e%d' % i for i in range(40)])
    assert len(ajustes.listar()[0]['exemplos']) == 6


# ═════════════════════════════════════════════════════════════════════════════
# A ordem
# ═════════════════════════════════════════════════════════════════════════════

def test_o_que_muda_o_resultado_vem_antes():
    """Quem lê a aba tem de encontrar primeiro o que pode mudar a conclusão."""
    ajustes.registrar('tensao', 'Ficha', quantos=1, efeito='cadastro')
    ajustes.registrar('tensao', 'Desenho', quantos=1, efeito='desenho')
    ajustes.registrar('tensao', 'Resultado', quantos=1, efeito='simulacao')
    ajustes.registrar('tensao', 'Pendente', quantos=1, efeito='nao_corrigido')
    ordenados = ajustes._ordenar(ajustes.listar())
    assert [a['titulo'] for a in ordenados] == ['Resultado', 'Pendente',
                                                'Desenho', 'Ficha']


def test_as_categorias_saem_na_ordem_declarada(tmp_path):
    ajustes.registrar('nao_corrigido', 'Z', quantos=1, efeito='nao_corrigido')
    ajustes.registrar('tensao', 'A', quantos=1)
    ajustes.salvar(tmp_path)
    grupos = ajustes.por_categoria(ajustes.carregar(tmp_path))
    assert [g[0] for g in grupos] == ['tensao', 'nao_corrigido']


def test_toda_categoria_usada_tem_rotulo_e_descricao():
    for chave, rotulo, descricao in ajustes.CATEGORIAS:
        assert chave and rotulo and descricao
        assert rotulo[0].isupper()


def test_todo_efeito_tem_rotulo_legivel():
    for efeito, (rotulo, ordem) in ajustes.EFEITOS.items():
        assert rotulo and isinstance(ordem, int)


# ═════════════════════════════════════════════════════════════════════════════
# Gravar e ler
# ═════════════════════════════════════════════════════════════════════════════

def test_o_registro_viaja_com_o_caso(tmp_path):
    """Na pasta do caso, junto dos `.dss`: a pasta sai com a explicação."""
    ajustes.contexto(distribuidora={'dist': '396', 'nome': 'Alguma (XX)'})
    ajustes.registrar('curvas', 'Curvas de outro lugar', quantos=61,
                      unidade='curvas', detalhe='d', porque='p')
    caminho = ajustes.salvar(tmp_path)
    assert caminho and (tmp_path / ajustes.NOME_ARQUIVO).exists()

    lido = ajustes.carregar(tmp_path)
    assert lido['alimentador'] == 'TESTE'
    assert lido['distribuidora']['nome'] == 'Alguma (XX)'
    assert lido['ajustes'][0]['quantos'] == 61


def test_a_pasta_sem_registro_devolve_nada(tmp_path):
    """`None` é resposta legítima, e a janela precisa distingui-la de zero.

    Um caso convertido por uma versão anterior não tem o arquivo. A aba tem de
    dizer "não há como saber", e não "nada foi ajustado".
    """
    assert ajustes.carregar(tmp_path) is None
    assert ajustes.por_categoria(None) == []


def test_o_arquivo_corrompido_nao_derruba(tmp_path):
    (tmp_path / ajustes.NOME_ARQUIVO).write_text('{isto não é json',
                                                 encoding='utf-8')
    assert ajustes.carregar(tmp_path) is None


def test_falhar_ao_gravar_nao_custa_o_caso(tmp_path):
    """O `.dss` já está escrito. Perder o registro é perder informação, não resultado."""
    assert ajustes.salvar(tmp_path / 'pasta_que_nao_existe') is None


def test_o_acento_sobrevive_ao_arquivo(tmp_path):
    """O texto é lido por gente, e vai para um JSON e de volta."""
    ajustes.registrar('tensao', 'Secundário bifásico', quantos=1,
                      detalhe='referência de terra',
                      porque='a concessão é o ângulo: 180° e não 120°')
    ajustes.salvar(tmp_path)
    bruto = (tmp_path / ajustes.NOME_ARQUIVO).read_text(encoding='utf-8')
    assert 'bifásico' in bruto, 'gravado sem escapar'
    a = ajustes.carregar(tmp_path)['ajustes'][0]
    assert a['titulo'] == 'Secundário bifásico'
    assert '180°' in a['porque']


def test_limpar_recomeca_do_zero():
    ajustes.registrar('tensao', 'A', quantos=1)
    ajustes.limpar(alimentador='OUTRO')
    assert ajustes.listar() == []


def test_contexto_nao_apaga_o_que_ja_foi_anotado():
    """A distribuidora só se conhece depois de ler o CTMT, e a essa altura já
    houve ajuste registrado — parte deles acontece ao carregar as tabelas."""
    ajustes.registrar('completude', 'Anotado antes de saber a base', quantos=1)
    ajustes.contexto(distribuidora={'dist': '1', 'nome': 'X'})
    assert len(ajustes.listar()) == 1


# ═════════════════════════════════════════════════════════════════════════════
# O caso de referência
# ═════════════════════════════════════════════════════════════════════════════

def test_o_caso_de_exemplo_traz_o_registro():
    """O que o pacote publica tem de sair com a explicação do que há nele."""
    from bdgdcase.solucao import caminho_exemplo

    dados = ajustes.carregar(caminho_exemplo())
    assert dados is not None, 'o caso de exemplo saiu sem Ajustes.json'
    assert dados.get('alimentador')
    for a in dados.get('ajustes') or []:
        assert a['categoria'] in dict((c, r) for c, r, _ in ajustes.CATEGORIAS)
        assert a['efeito'] in ajustes.EFEITOS
        assert a['titulo'] and a['porque'], 'todo ajuste diz o que e por quê'
