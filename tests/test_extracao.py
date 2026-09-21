# -*- coding: utf-8 -*-
"""A agregação por poste, e a promessa de que mexer nela não mexe no caso.

O extrator junta as unidades consumidoras de um poste em colunas `LISTA_*`,
concatenadas por `;`. O conversor lê a i-ésima entrada de cada uma para montar a
i-ésima carga daquele poste, e o painel lê as mesmas listas para dizer quem é
aquela unidade. O índice, portanto, precisa significar a MESMA unidade em toda
lista — e não significava: cada coluna descartava os seus próprios nulos, e as
listas do mesmo poste saíam com comprimentos diferentes.
"""
import hashlib
import io
import os
import shutil
import subprocess
import sys

import pytest

from test_conversao import ESPERADO, MOTIVO_FALTA, falta

gpd = pytest.importorskip('geopandas', reason='instalação incompleta: geopandas é dependência do pacote')

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(RAIZ, 'tests', 'dados', 'TRO05')


def _quantas(valor, n):
    """Quantas posições a lista declara, com `n` unidades no poste.

    Um poste de uma unidade só, cujo campo esteja vazio, junta em `''` — que é
    indistinguível de lista ausente. Acima de uma unidade a ambiguidade some,
    porque sobram os separadores.
    """
    s = '' if valor is None else str(valor)
    if s == '':
        return 0 if n == 1 else -1
    return len(s.split(';'))


@pytest.fixture(scope='module')
def reagregado(tmp_path_factory):
    """A fixture com os `_por_poste.gpkg` refeitos pelo código atual."""
    if not os.path.isdir(FIXTURE):
        pytest.skip('fixture ausente')
    from bdgdcase.extracao import Extrator

    base = tmp_path_factory.mktemp('reagregado')
    alv = os.path.join(str(base), 'Output', 'TRO05')
    shutil.copytree(FIXTURE, alv)
    for nome in os.listdir(alv):
        if nome.endswith('_por_poste.gpkg'):
            os.remove(os.path.join(alv, nome))

    Extrator(limpar_colunas=True).agregar_ucs_aos_postes(alv)
    return str(base), alv


def test_as_listas_por_uc_sao_posicionais(reagregado):
    """A i-ésima entrada de toda `LISTA_` é a i-ésima unidade do poste.

    Antes não era: o ausente sumia em vez de virar campo vazio, e a lista
    encurtava. Medido em AGA01 antes da correção, `LISTA_CNAE` divergia da
    contagem de unidades em 1741 dos 1962 postes — ler "a CNAE da terceira
    unidade" devolvia a de outra pessoa.
    """
    _, alv = reagregado
    achados = []
    for nome in sorted(os.listdir(alv)):
        if not nome.endswith('_por_poste.gpkg'):
            continue
        g = gpd.read_file(os.path.join(alv, nome))
        listas = [c for c in g.columns if c.startswith('LISTA_')]
        assert listas, nome
        for _, r in g.iterrows():
            n = int(r['QTD_UC'])
            for c in listas:
                if _quantas(r[c], n) not in (n, 0):
                    achados.append('%s %s poste %s: %d posições para %d UCs'
                                   % (nome, c, r['COD_PONNOT'],
                                      _quantas(r[c], n), n))
    assert not achados, '\n'.join(achados[:20])


def test_reagregar_nao_muda_o_caso(reagregado):
    """A prova de que a mudança é de informação, e não de modelo.

    A agregação posicional acrescenta campos vazios onde antes não havia nada.
    Quem consome as listas para montar o `.dss` usa `parse_lista`, que descarta
    vazios — então a sequência que chega ao conversor é a mesma, e o caso tem de
    sair byte a byte idêntico ao de referência. Se um dia não sair, a mudança
    deixou de ser inócua e é aqui que se descobre, não no resultado de alguém.
    """
    if falta:
        pytest.skip(MOTIVO_FALTA)
    base, alv = reagregado
    env = dict(os.environ, BDGD_BASE_DIR=base,
               PYTHONPATH=os.path.join(RAIZ, 'src'))
    r = subprocess.run(
        [sys.executable, '-c',
         'import sys; from bdgdcase.modelo.pipeline import processar_lote; '
         'processar_lote([sys.argv[1]], dias=("DU",))', alv],
        env=env, capture_output=True, text=True)
    saida = os.path.join(base, 'OpenDSS', 'TRO05_DU')
    assert os.path.isdir(saida), (r.stderr or r.stdout)[-2000:]

    def sha(p):
        return hashlib.sha256(io.open(p, 'rb').read()).hexdigest()

    esperados = sorted(p.name for p in ESPERADO.iterdir() if p.is_file())
    diferentes = [n for n in esperados
                  if not os.path.exists(os.path.join(saida, n))
                  or sha(os.path.join(saida, n)) != sha(str(ESPERADO / n))]
    assert not diferentes, 'mudaram: %s' % ', '.join(diferentes)


def test_a_whitelist_de_colunas_cobre_o_cadastro_do_equipamento(reagregado):
    """O extrator guarda mais que o mínimo elétrico, e é para guardar.

    Coluna que não é extraída não volta: reextrair um alimentador custa
    minutos, e reextrair os que já se tem custa horas. Carregar uma coluna a
    mais custa bytes.
    """
    from bdgdcase.extracao import camadas
    import inspect
    fonte = inspect.getsource(camadas.limpar_colunas)
    for coluna in ('MAT', 'ALT', 'ESF', 'ESTR',        # poste
                   'CMAX', 'BIT_FAS_1', 'MAT_FAS_1',   # condutor
                   'GRU_TEN', 'TEN_FORN', 'ARE_LOC',   # enquadramento
                   'DEM_CONT', 'DEM_01',               # demanda
                   'TIPO_LAMP', 'POT_LAMP'):           # iluminação pública
        assert "'%s'" % coluna in fonte, coluna
