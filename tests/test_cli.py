# -*- coding: utf-8 -*-
"""`bdgdcase exemplo` é a primeira coisa que alguém roda, e ela quebrava.

O comando confere a instalação inteira sem exigir dado nenhum do usuário: pega o
caso que viaja dentro do pacote, verifica que ele fecha e roda o dia. Para isso
ele reaproveita `_cmd_verificar` e `_cmd_rodar` — e portanto precisa fornecer, à
mão, tudo o que esses dois leem de `args`.

Quando `rodar` ganhou a opção `--medir-cargas`, ninguém acrescentou o campo aqui.
O resultado foi um `AttributeError` no meio da saída de quem acabou de instalar o
pacote, depois de o comando já ter impresso que o caso convergiu. Nada no diff
daquela mudança denunciava a falta.

Este teste faz a conferência por leitura do código, e não por execução: rodar o
exemplo de verdade custa uma simulação inteira, e uma falha desta espécie tem
de aparecer no segundo em que alguém abre o diff, não minutos depois.
"""
import ast
import inspect
import os

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _funcao(nome):
    from bdgdcase.interface import cli
    return ast.parse(inspect.getsource(getattr(cli, nome))).body[0]


def _lidos(no):
    """Atributos que a função lê de `args` — `args.x` do lado direito."""
    fora = set()
    for m in ast.walk(no):
        if (isinstance(m, ast.Attribute) and isinstance(m.value, ast.Name)
                and m.value.id == 'args' and isinstance(m.ctx, ast.Load)):
            fora.add(m.attr)
    return fora


def _escritos(no):
    """Atributos que a função põe em `args` — inclusive em atribuição múltipla."""
    fora = set()
    for m in ast.walk(no):
        alvos = (m.targets if isinstance(m, ast.Assign)
                 else [m.target] if isinstance(m, (ast.AugAssign, ast.AnnAssign))
                 else [])
        pilha = list(alvos)
        while pilha:
            al = pilha.pop()
            if isinstance(al, (ast.Tuple, ast.List)):
                pilha.extend(al.elts)
            elif (isinstance(al, ast.Attribute) and isinstance(al.value, ast.Name)
                  and al.value.id == 'args'):
                fora.add(al.attr)
    return fora


def test_o_exemplo_fornece_tudo_que_verificar_e_rodar_leem():
    """A falta de um campo aqui só apareceria rodando, e é tarde demais."""
    exemplo = _funcao('_cmd_exemplo')
    precisa = _lidos(_funcao('_cmd_verificar')) | _lidos(_funcao('_cmd_rodar'))
    tem = _escritos(exemplo)
    faltando = sorted(precisa - tem)
    assert not faltando, (
        '`bdgdcase exemplo` chama verificar/rodar sem definir %s em args — '
        'AttributeError na cara de quem acabou de instalar o pacote'
        % ', '.join(faltando))


def test_a_conferencia_enxerga_atribuicao_em_tupla():
    """`args.csv, args.medir_cargas = None, False` conta como definir os dois.

    Sem isto o teste acima passaria por engano: o campo estaria definido, e a
    varredura não o veria — verde por cegueira, que é pior que vermelho.
    """
    no = ast.parse('def f(args):\n'
                   '    args.a, args.b = 1, 2\n'
                   '    args.c = 3\n').body[0]
    assert _escritos(no) == {'a', 'b', 'c'}


def test_a_conferencia_separa_leitura_de_escrita():
    """`args.x = 1` não pode contar como leitura, nem o contrário."""
    no = ast.parse('def f(args):\n'
                   '    args.escrito = args.lido\n').body[0]
    assert _lidos(no) == {'lido'}
    assert _escritos(no) == {'escrito'}
