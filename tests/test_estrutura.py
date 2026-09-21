# -*- coding: utf-8 -*-
"""Nenhum nome pode ser definido duas vezes no mesmo escopo.

Python aceita calado: a segunda definição substitui a primeira, e a primeira
vira código que ninguém executa nem apaga. Não há aviso, o import funciona, os
testes passam.

Foi exatamente o que aconteceu com `_Geometria`: nove métodos ficaram definidos
duas vezes, e as duas cópias divergiam num ponto — a que mostrava a ampacidade
junto da corrente era a primeira, e portanto a que não rodava. O painel exibia
"1956%" sem dizer que a ampacidade cadastrada era de 9 A, que é o que faz o
número absurdo se explicar. A melhoria estava escrita, revisada e morta.

A causa foi um script de correção aplicado duas vezes. A classe inteira de erro
custa uma varredura de AST, e é esta.

O mesmo vale para os testes, onde o custo é pior: um `def test_x` repetido
apaga o anterior, e a suíte fica verde justamente porque deixou de verificar
alguma coisa.
"""
import ast
import io
import os

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASTAS = ('src', 'tests', 'scripts')

#: Decoradores que autorizam repetir o nome — a linguagem os usa assim.
#: `@x.setter` e `@x.deleter` acompanham o `@property` de mesmo nome, e
#: `@overload` declara assinaturas alternativas da mesma função.
REDEFINIDORES = ('setter', 'getter', 'deleter', 'register', 'overload')


def _modulos():
    for pasta in PASTAS:
        for base, dirs, nomes in os.walk(os.path.join(RAIZ, pasta)):
            dirs[:] = [d for d in dirs if d != '__pycache__']
            for nome in sorted(nomes):
                if nome.endswith('.py'):
                    yield os.path.join(base, nome)


def _autorizado(no):
    for dec in getattr(no, 'decorator_list', []):
        alvo = dec.func if isinstance(dec, ast.Call) else dec
        nome = getattr(alvo, 'attr', None) or getattr(alvo, 'id', None)
        if nome in REDEFINIDORES:
            return True
    return False


def _repetidos(corpo, escopo):
    """Nomes definidos mais de uma vez entre os filhos DIRETOS de um escopo.

    Filhos diretos, e não `ast.walk`: definir a mesma função nos dois ramos de
    um `try/except ImportError` ou de um `if` é padrão corrente e legítimo — é
    escolha, não descuido.
    """
    vistos = {}
    fora = []
    for no in corpo:
        if not isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            continue
        if no.name in vistos and not _autorizado(no):
            fora.append('%s.%s definido na linha %d e de novo na %d'
                        % (escopo, no.name, vistos[no.name], no.lineno))
        vistos[no.name] = no.lineno
    return fora


@pytest.mark.parametrize('caminho', sorted(_modulos()),
                         ids=lambda p: os.path.relpath(p, RAIZ))
def test_nenhuma_definicao_repetida(caminho):
    arv = ast.parse(io.open(caminho, encoding='utf-8').read())
    rel = os.path.relpath(caminho, RAIZ)
    achados = _repetidos(arv.body, rel)
    for no in ast.walk(arv):
        if isinstance(no, ast.ClassDef):
            achados += _repetidos(no.body, '%s::%s' % (rel, no.name))
    assert not achados, (
        'definição repetida — a segunda apaga a primeira, e a primeira vira '
        'código morto que ninguém percebe:\n  ' + '\n  '.join(achados))


def test_a_varredura_alcanca_os_modulos_grandes():
    """Varredura que não varre nada passa calada; esta não pode."""
    nomes = {os.path.basename(m) for m in _modulos()}
    assert {'mapa.py', 'rede.py', 'motor.py', 'solucao.py'} <= nomes


def test_a_regra_pega_o_caso_que_a_motivou():
    """Uma classe com o método repetido tem de ser acusada."""
    codigo = ('class C:\n'
              '    def f(self):\n'
              '        return 1\n'
              '    def f(self):\n'
              '        return 2\n')
    cls = ast.parse(codigo).body[0]
    assert _repetidos(cls.body, 'C')


EXTRACAO = os.path.join(RAIZ, 'src', 'bdgdcase', 'extracao')


def test_os_modulos_de_assunto_nao_dependem_do_motor():
    """A dependência anda num sentido só, e é isso que faz o corte valer.

    `camadas`, `catalogos` e `agregacao` respondem perguntas sobre a BDGD;
    `motor` decide a ordem em que elas são feitas. Se um dos três voltar a
    importar o motor, o pacote vira um nó de novo — e o motivo do corte era
    justamente que o trabalho estava preso dentro de outra coisa (antes, uma
    janela Tk).
    """
    for nome in ('camadas.py', 'catalogos.py', 'agregacao.py'):
        arv = ast.parse(io.open(os.path.join(EXTRACAO, nome),
                                encoding='utf-8').read())
        for no in ast.walk(arv):
            alvo = (no.module if isinstance(no, ast.ImportFrom) else None)
            nomes = [a.name for a in getattr(no, 'names', [])] \
                if isinstance(no, ast.Import) else []
            assert 'motor' not in (alvo or ''), '%s importa o motor' % nome
            assert not any('motor' in n for n in nomes), \
                '%s importa o motor' % nome


def test_o_pacote_esta_todo_documentado():
    """Toda função e toda classe do pacote têm docstring.

    Eram 167 de 414 sem, quando esta modularização começou. Documentar foi
    metade do trabalho dela, e sem um teste o número volta a subir sozinho:
    ninguém repara na função nova sem docstring ao revisar um diff.

    A regra vale para as privadas também. `_resolver_pac_zero` é privada e é
    onde mora a decisão sobre o que fazer com um PAC zerado — exatamente o tipo
    de escolha que alguém vai querer entender, e que o nome não conta.

    O que se pede: **o que faz** sempre; **por que existe** quando não for
    óbvio pelo nome. É onde este repositório põe o valor dele, e o que faz o
    código valer a leitura.
    """
    faltando = []
    for caminho in _modulos():
        rel = os.path.relpath(caminho, RAIZ).replace(os.sep, '/')
        if not rel.startswith('src/'):
            continue
        arv = ast.parse(io.open(caminho, encoding='utf-8').read())
        for no in ast.walk(arv):
            if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)) and not ast.get_docstring(no):
                faltando.append('%s:%d %s' % (rel, no.lineno, no.name))
    assert not faltando, (
        'sem docstring — o que faz sempre, por que existe quando '
        'não for óbvio pelo nome:\n  ' + '\n  '.join(faltando))


def test_a_regra_deixa_passar_property_e_import_condicional():
    codigo = ('class C:\n'
              '    @property\n'
              '    def v(self):\n'
              '        return self._v\n'
              '    @v.setter\n'
              '    def v(self, x):\n'
              '        self._v = x\n')
    cls = ast.parse(codigo).body[0]
    assert not _repetidos(cls.body, 'C')

    condicional = ('try:\n'
                   '    def f():\n'
                   '        return 1\n'
                   'except ImportError:\n'
                   '    def f():\n'
                   '        return 2\n')
    assert not _repetidos(ast.parse(condicional).body, 'm')
