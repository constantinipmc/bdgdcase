# -*- coding: utf-8 -*-
"""Oito das premissas não são constantes, e confundi-las sai caro.

`ALIMENTADOR`, as duas pastas, `TENSAO_MT_KV`, `TIP_DIA_CURVA_CRVCRG` e os três
modos de reverso do regulador são reatribuídos a cada alimentador e a cada tipo
de dia, por `processar_lote`. Quem os importa pelo nome — `from config import
TENSAO_MT_KV` — guarda uma cópia do valor que valia na hora da importação, e a
reatribuição nunca chega: o caso do segundo alimentador sai com a tensão do
primeiro.

O erro não levanta exceção, não some com nenhum arquivo e não muda o número de
elementos gerados. Ele troca um número por outro número plausível — que é a
classe de falha que este pacote mais persegue. Daí um teste só para isso.
"""
import ast
import io
import os

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACOTE = os.path.join(RAIZ, 'src', 'bdgdcase')
CONFIG = 'bdgdcase.modelo.config'


def _modulos():
    for base, dirs, nomes in os.walk(PACOTE):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for nome in sorted(nomes):
            if nome.endswith('.py'):
                caminho = os.path.join(base, nome)
                yield (os.path.relpath(caminho, PACOTE).replace('\\', '/'),
                       ast.parse(io.open(caminho, encoding='utf-8').read()))


def _achatar(alvo):
    """Um alvo de atribuição pode ser uma árvore: `(a, b) = ...` e `[a] = ...`."""
    if isinstance(alvo, (ast.Tuple, ast.List)):
        for item in alvo.elts:
            for x in _achatar(item):
                yield x
    else:
        yield alvo


def _reatribuidos():
    """Nomes que alguém escreve como `config.X = ...` em algum lugar."""
    fora = {}
    for rel, arv in _modulos():
        for no in ast.walk(arv):
            alvos = (no.targets if isinstance(no, ast.Assign)
                     else [no.target] if isinstance(no, (ast.AugAssign,
                                                         ast.AnnAssign))
                     else [])
            for raiz in alvos:
                for al in _achatar(raiz):
                    if (isinstance(al, ast.Attribute)
                            and isinstance(al.value, ast.Name)
                            and al.value.id == 'config'):
                        fora.setdefault(al.attr, []).append(
                            '%s:%d' % (rel, no.lineno))
    return fora


def test_a_lista_de_mutaveis_bate_com_quem_de_fato_muda():
    """`config.MUTAVEIS` não pode ser uma lista escrita à mão que envelheceu.

    Nos dois sentidos: nome reatribuído fora da lista é o perigo (alguém vai
    importá-lo por valor), e nome na lista que ninguém reatribui é ruído, que
    faz o resto do código ser lido com uma cautela que não precisa.
    """
    from bdgdcase.modelo import config
    declarados = set(config.MUTAVEIS)
    medidos = _reatribuidos()

    fora_da_lista = {k: v for k, v in medidos.items() if k not in declarados}
    assert not fora_da_lista, (
        'reatribuído em tempo de execução, mas ausente de config.MUTAVEIS — '
        'quem importar esse nome por valor não verá a mudança: %s'
        % fora_da_lista)

    nunca_mudam = declarados - set(medidos)
    assert not nunca_mudam, (
        'na lista de mutáveis, mas ninguém reatribui: %s' % sorted(nunca_mudam))


@pytest.mark.parametrize('rel,arv', list(_modulos()),
                         ids=lambda x: x if isinstance(x, str) else '')
def test_ninguem_importa_um_mutavel_pelo_nome(rel, arv):
    """A única leitura correta de um mutável é `config.NOME`."""
    from bdgdcase.modelo import config
    for no in ast.walk(arv):
        if isinstance(no, ast.ImportFrom) and no.module == CONFIG:
            trazidos = {a.name for a in no.names} & set(config.MUTAVEIS)
            assert not trazidos, (
                '%s:%d importa %s pelo nome; leia como `config.NOME`, senão o '
                'valor congela na importação'
                % (rel, no.lineno, ', '.join(sorted(trazidos))))


def test_a_reatribuicao_realmente_chega_em_quem_le():
    """A prova de que a indireção funciona, e não só de que existe.

    Sem ela, o teste acima verificaria a forma do código e não o efeito dele.
    """
    from bdgdcase.modelo import config, rede

    antes = config.TENSAO_MT_KV
    try:
        config.TENSAO_MT_KV = 13.8
        assert rede.config.TENSAO_MT_KV == 13.8
    finally:
        config.TENSAO_MT_KV = antes
    assert rede.config.TENSAO_MT_KV == antes
