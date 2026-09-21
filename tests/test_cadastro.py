# -*- coding: utf-8 -*-
"""O cadastro que acompanha o caso: o que a BDGD sabe e o `.dss` não guarda.

Um caso OpenDSS descreve um circuito. `Load.ucbt_com_13610584_2` diz kW, kV e a
curva; não diz que unidade consumidora é aquela, de que classe, quanto consumiu
em cada mês, nem de que bitola é o cabo que a alimenta. Os `Cadastro_*.csv`
guardam isso ao lado dos `.dss`, e estes testes cuidam das três promessas que
eles fazem: cobrir todo elemento do circuito, não inventar nada, e não levar
junto o que não deve ser publicado.
"""
import csv
import io

import pytest

from test_conversao import ESPERADO, MOTIVO_FALTA, falta

CADASTROS = sorted(p.name for p in ESPERADO.glob('Cadastro_*.csv'))


def _ler(caminho):
    """As linhas de um `Cadastro_*.csv`, pulando o aviso de sigilo."""
    with io.open(caminho, encoding='utf-8') as f:
        while True:
            onde = f.tell()
            linha = f.readline()
            if not linha or not linha.startswith('#'):
                f.seek(onde)
                break
        return list(csv.DictReader(f))


def test_o_exemplo_traz_cadastro():
    assert CADASTROS, 'o caso de exemplo devia trazer os Cadastro_*.csv'
    assert 'Cadastro_Cargas.csv' in CADASTROS


@pytest.mark.parametrize('nome', CADASTROS)
def test_todo_arquivo_avisa_do_sigilo(nome):
    """Quem receber a pasta tem de saber o que está levando.

    A BDGD é aberta, mas uma pasta com CEP e CNAE por unidade consumidora não
    é a mesma coisa que a base da ANEEL: muda a facilidade, e muda quem assina
    a redistribuição. O aviso é a primeira coisa que se lê no arquivo.
    """
    with io.open(str(ESPERADO / nome), encoding='utf-8') as f:
        cabecalho = f.readline()
    assert cabecalho.startswith('#')
    assert 'identificavel' in cabecalho.lower()


@pytest.mark.parametrize('nome', CADASTROS)
def test_o_numero_da_conta_nunca_entra(nome):
    """`DESCR` traz `NR_CONTA_CNS_GENESIS: <conta do cliente>` no formato da
    distribuidora. Não é campo que se decide caso a caso: nunca entra."""
    colunas = set()
    for linha in _ler(str(ESPERADO / nome)):
        colunas.update(k.lower() for k in linha if k)
    assert 'descr' not in colunas
    assert not any('conta' in c for c in colunas), colunas


@pytest.mark.parametrize('nome', CADASTROS)
def test_toda_linha_tem_elemento_e_ele_e_unico_por_arquivo(nome):
    linhas = _ler(str(ESPERADO / nome))
    assert linhas
    nomes = [x['elemento'] for x in linhas]
    assert all(n and n == n.lower().strip() for n in nomes)
    assert len(set(nomes)) == len(nomes), 'elemento repetido em %s' % nome


def test_o_exemplo_publico_nao_leva_dado_identificavel():
    """No exemplo, as colunas identificáveis existem e vêm vazias.

    Vazias e não ausentes de propósito: a coluna documenta que o campo existe
    na BDGD e que foi retirado daqui, em vez de deixar quem lê achar que o
    conversor não sabe daquilo.
    """
    for linha in _ler(str(ESPERADO / 'Cadastro_Cargas.csv')):
        for coluna in ('cep', 'cnae', 'clas_sub', 'gru_tar'):
            assert linha.get(coluna, '') == '', (coluna, linha['elemento'])


# ── O cadastro contra o circuito de verdade ──────────────────────────────────

#: A marca separa quem precisa resolver o caso de quem so le os `.csv`. Ela
#: continua util depois de o OpenDSS ter virado dependencia: `pytest -m solver`
#: roda so a parte cara, e o `importorskip` abaixo mantem o arquivo utilizavel
#: numa instalacao incompleta.
exige_solver = pytest.mark.solver


@pytest.fixture(scope='module')
def caso():
    """O caso de referencia resolvido, para conferir o cadastro contra ele."""
    if falta:
        pytest.skip(MOTIVO_FALTA)
    pytest.importorskip(
        'opendssdirect',
        reason='o OpenDSS nao carregou (instalacao incompleta)')
    from bdgdcase.solucao import rodar_dia
    return rodar_dia(str(ESPERADO), passos=1, detalhado=True)


@exige_solver
def test_todo_elemento_do_circuito_tem_cadastro(caso):
    """A promessa central: nada do que foi simulado fica sem ficha.

    Vale para carga, transformador, geração e trecho. Uma carga sem linha de
    cadastro seria um ponto do mapa que o painel abre e não tem o que dizer.
    """
    from bdgdcase.solucao import _ler_cadastro
    cad = _ler_cadastro(str(ESPERADO))
    inv = caso['inventario']
    faltando = {}
    for rotulo, nomes in (
            ('cargas', [c['nome'] for c in inv['cargas']]),
            ('trafos', [t['nome'] for t in inv['trafos']]),
            ('gd', [g['nome'] for g in inv['gd']]),
            ('capacitores', [c['nome'] for c in inv.get('capacitores', ())]),
            # Regulador: um enrolamento por fase, e o cadastro tem uma linha
            # para cada um — o contrato do arquivo é por elemento emitido.
            ('reguladores', [n for g in inv.get('reguladores', ())
                             for n in g.get('enrolamentos', [g['nome']])]),
            ('linhas', list(caso['nomes_linha']))):
        ausentes = [n for n in nomes if n.lower() not in cad]
        if ausentes:
            faltando[rotulo] = ausentes[:5]
    assert not faltando, faltando


@exige_solver
def test_o_inventario_entrega_o_cadastro_junto(caso):
    """E entrega num sub-dicionário, não misturado ao que veio do circuito."""
    cargas = caso['inventario']['cargas']
    com_cadastro = [c for c in cargas if c.get('cadastro')]
    assert com_cadastro, 'nenhuma carga recebeu cadastro'
    um = com_cadastro[0]
    assert 'kw' in um and 'cadastro' in um
    assert 'cod_id' in um['cadastro']
    # o que veio do circuito continua no lugar de sempre
    assert isinstance(um['kw'], float)


@exige_solver
def test_a_classe_vem_do_cadastro_quando_existe(caso):
    """O nome da carga codifica a classe porque o OpenDSS não tem campo para
    ela. Havendo cadastro, ele é a fonte — e as duas têm de concordar."""
    for c in caso['inventario']['cargas']:
        classe_cad = (c.get('cadastro') or {}).get('classe')
        if classe_cad:
            assert c['classe'] == classe_cad


@exige_solver
def test_sem_os_arquivos_o_caso_continua_abrindo(tmp_path):
    """Pasta gerada antes de o cadastro existir não pode virar erro."""
    if falta:
        pytest.skip(MOTIVO_FALTA)
    pytest.importorskip(
        'opendssdirect',
        reason='o OpenDSS nao carregou (instalacao incompleta)')
    from bdgdcase.solucao import _ler_cadastro, rodar_dia
    for p in ESPERADO.iterdir():
        if not p.name.startswith('Cadastro_'):
            (tmp_path / p.name).write_bytes(p.read_bytes())
    assert _ler_cadastro(tmp_path) == {}
    r = rodar_dia(tmp_path, passos=2, detalhado=True)
    assert r['inventario']['cargas']
    assert all(c['cadastro'] == {} for c in r['inventario']['cargas'])
    assert all(c['classe'] for c in r['inventario']['cargas'])


def test_arquivo_ilegivel_nao_derruba_o_mapa(tmp_path):
    from bdgdcase.solucao import _ler_cadastro
    (tmp_path / 'Cadastro_Cargas.csv').write_bytes(b'\xff\xfe\x00lixo\x00')
    assert _ler_cadastro(tmp_path) == {}


# ── A leitura posicional das listas do poste ─────────────────────────────────

def test_a_lista_posicional_preserva_o_vazio():
    from bdgdcase.modelo.cadastro import parse_lista, parse_lista_posicional
    # `parse_lista` encurta; a posicional não — é a diferença que faz o índice
    # do nome da carga significar a mesma unidade em toda lista.
    assert parse_lista('a;;c') == ['a', 'c']
    assert parse_lista_posicional('a;;c', 3) == ['a', '', 'c']


def test_a_lista_posicional_completa_pelo_numero_de_unidades():
    """Um poste de uma unidade só, com o campo vazio, junta em `''`.

    Nesse caso a string não distingue "uma vazia" de "nenhuma", e quem sabe o
    tamanho é o `QTD_UC` do poste — daí `n` vir de fora.
    """
    from bdgdcase.modelo.cadastro import parse_lista_posicional
    assert parse_lista_posicional('', 1) == ['']
    assert parse_lista_posicional('', 3) == ['', '', '']
    assert parse_lista_posicional('a;b;c;d', 2) == ['a', 'b']
    assert parse_lista_posicional(None, 2) == ['', '']
    assert parse_lista_posicional('nan;b', 2) == ['', 'b']


def test_o_primeiro_desfaz_a_repeticao_do_poste():
    """Colunas fora de `CAMPOS_LISTA` chegam concatenadas; município repetido
    cinco vezes numa célula é ruído, não informação."""
    from bdgdcase.modelo.cadastro_csv import _primeiro
    assert _primeiro('4218707;4218707;4218707') == '4218707'
    assert _primeiro('4218707') == '4218707'
    assert _primeiro('nan') == ''
    assert _primeiro(None) == ''
