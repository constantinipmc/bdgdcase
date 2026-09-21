# -*- coding: utf-8 -*-
"""Um pedaço do alimentador que não chega à fonte, e quem fica sabendo.

Relato real, no alimentador 866960007: um trecho de média com 59 barras, com os
próprios reguladores, desenhado no mapa e separado do tronco por 2,5 km. Não é
erro de conversão — os trechos trazem `CTMT` deste alimentador e o cadastro
simplesmente não publica o que os ligaria. O caso reproduz a base fielmente.

O defeito era outro: **ninguém era avisado**. A auditoria de conectividade já
media tudo — componentes, tamanho de cada uma, cargas fora da parte alimentada —
e apenas imprimia no terminal, que rola e some. E, pior, ela rodava DEPOIS de o
registro de ajustes ser salvo, então mesmo o que registrasse não chegava ao
arquivo que viaja com o caso.

Medido em catorze casos: cinco não têm ilha nenhuma. Isto é achado, não ruído de
toda conversão.
"""
import ast
import io
import os

import pytest

from bdgdcase import ajustes

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MASTER = 'New Circuit.teste\n~ bus1=fonte.1.2.3 basekv=13.8\n'
#: Duas ilhas: a da fonte, e uma solta com carga dentro.
LINHAS = (
    'New Line.MT_1 bus1=fonte.1.2.3 bus2=meio.1.2.3\n'
    'New Line.MT_2 bus1=meio.1.2.3 bus2=ponta.1.2.3\n'
    'New Line.MT_9 bus1=longe_a.1.2.3 bus2=longe_b.1.2.3\n'
)
CARGAS = (
    'New Load.UCMT_ok bus1=ponta.1.2.3 kW=10\n'
    'New Load.UCMT_ilhada bus1=longe_b.1.2.3 kW=10\n'
)


def _caso(pasta, cargas=CARGAS, linhas=LINHAS):
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / 'Master.dss').write_text(MASTER, encoding='utf-8')
    (pasta / 'Linhas_MT.dss').write_text(linhas, encoding='utf-8')
    (pasta / 'Cargas_MT.dss').write_text(cargas, encoding='utf-8')
    return str(pasta)


@pytest.fixture(autouse=True)
def registro_limpo():
    """Cada teste começa com o registro vazio e não suja o seguinte."""
    ajustes.limpar(alimentador='teste', dia='DU')
    yield
    ajustes.limpar(alimentador='teste', dia='DU')


def test_carga_fora_da_parte_alimentada_e_registrada(tmp_path):
    """A ilha entra no registro que viaja com o caso, não só no terminal."""
    from bdgdcase.modelo.topologia import _auditar_conectividade_dss

    _auditar_conectividade_dss(_caso(tmp_path / 'caso'), 'fonte')

    achados = [a for a in ajustes.listar()
               if 'não chega à fonte' in a['titulo']]
    assert achados, [a['titulo'] for a in ajustes.listar()]
    assert achados[0]['quantos'] == 1
    assert achados[0]['efeito'] == 'nao_corrigido'


def test_alimentador_inteiro_nao_registra_nada(tmp_path):
    """Sem ilha, nada é dito — senão o aviso vira ruído de toda conversão.

    Cinco dos catorze casos medidos não têm ilha nenhuma, e o caso de
    referência é um deles: se este registro disparasse sempre, ele mudaria o
    golden e deixaria de significar alguma coisa.
    """
    from bdgdcase.modelo.topologia import _auditar_conectividade_dss

    linhas = ('New Line.MT_1 bus1=fonte.1.2.3 bus2=meio.1.2.3\n'
              'New Line.MT_2 bus1=meio.1.2.3 bus2=ponta.1.2.3\n')
    cargas = 'New Load.UCMT_ok bus1=ponta.1.2.3 kW=10\n'
    _auditar_conectividade_dss(
        _caso(tmp_path / 'caso', cargas=cargas, linhas=linhas), 'fonte')

    assert not [a for a in ajustes.listar() if a['categoria'] == 'topologia']


def test_carga_em_barra_que_a_rede_nao_alcanca_e_registrada(tmp_path):
    """Barra citada só pela carga: o OpenDSS a cria, e ela não participa de nada.

    É a forma mais silenciosa de perder carga — o caso compila, converge, e
    aquela unidade simplesmente não existe para o fluxo.
    """
    from bdgdcase.modelo.topologia import _auditar_conectividade_dss

    cargas = (CARGAS + 'New Load.UCMT_orfa bus1=ninguem_liga.1.2.3 kW=10\n')
    _auditar_conectividade_dss(
        _caso(tmp_path / 'caso', cargas=cargas), 'fonte')

    achados = [a for a in ajustes.listar()
               if 'não existe na rede' in a['titulo']]
    assert achados and achados[0]['quantos'] == 1


def test_a_auditoria_roda_antes_de_o_registro_ser_salvo():
    """A ordem é o que fazia o achado não chegar ao arquivo.

    A auditoria lê os `.dss` já escritos, então tem de vir depois deles — mas
    antes de `salvar`. Estava depois das duas coisas, e o que ela descobria
    morria no terminal: um alimentador com 366 pontos de carga fora da parte
    alimentada saía com o `Ajustes.json` em silêncio sobre isso.
    """
    fonte = io.open(os.path.join(RAIZ, 'src', 'bdgdcase', 'modelo',
                                 'pipeline.py'), encoding='utf-8').read()
    arv = ast.parse(fonte)
    principal = next(n for n in arv.body
                     if isinstance(n, ast.FunctionDef) and n.name == 'main')

    auditoria = salvar = None
    for no in ast.walk(principal):
        if not isinstance(no, ast.Call):
            continue
        alvo = no.func
        nome = getattr(alvo, 'id', None) or getattr(alvo, 'attr', None)
        if nome == '_auditar_conectividade_dss':
            auditoria = no.lineno
        elif nome == 'salvar':
            salvar = no.lineno
    assert auditoria and salvar, (auditoria, salvar)
    assert auditoria < salvar, (
        'a auditoria (linha %d) roda depois de salvar (linha %d): o que ela '
        'registrar não chega ao Ajustes.json' % (auditoria, salvar))
