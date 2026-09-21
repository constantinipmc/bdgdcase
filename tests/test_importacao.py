# -*- coding: utf-8 -*-
"""O pacote importa e a CLI responde num ambiente sem interface gráfica.

Este é o teste que justifica a separação entre biblioteca e GUI. O projeto de
origem importava tkinter na carga do módulo, o que torna a biblioteca
inutilizável em contêiner de CI, servidor ou WSL sem X. Se alguém reintroduzir
esse import, é aqui que o CI acusa — o runner Linux do GitHub não tem Tk.
"""
import importlib
import subprocess
import sys

import pytest

MODULOS = ['bdgdcase', 'bdgdcase.caminhos', 'bdgdcase.api', 'bdgdcase.interface.cli',
           'bdgdcase.gd_tipos', 'bdgdcase.curva_pv', 'bdgdcase.solar',
           'bdgdcase.extracao', 'bdgdcase.modelo', 'bdgdcase.modelo.pipeline',
           'bdgdcase.interface', 'bdgdcase.interface.preparar',
           'bdgdcase.solucao', 'bdgdcase.interface.mapa', 'bdgdcase.interface.azulejos']


@pytest.mark.parametrize('nome', MODULOS)
def test_modulo_importa(nome):
    assert importlib.import_module(nome) is not None


def test_versao_declarada():
    import bdgdcase
    assert bdgdcase.__version__.count('.') == 2


def test_tk_nao_e_exigido_na_importacao():
    """Importar num Python cego para tkinter tem de continuar funcionando.

    A exigência é mais forte do que "importa sem quebrar": **nenhum** módulo do
    pacote pode importar tkinter na carga. O único ponto que o importa é
    `interface.base._tk()`, dentro da função — e é por isso que até a janela
    pode ser importada num Python sem Tk, falhando só na hora de abrir.
    """
    codigo = (
        'import sys\n'
        'sys.modules["tkinter"] = None\n'   # qualquer `import tkinter` levanta ImportError
        'import bdgdcase.extracao.motor\n'
        'import bdgdcase.modelo, bdgdcase.modelo.pipeline\n'
        'import bdgdcase.interface.base\n'
        'import bdgdcase.interface.preparar, bdgdcase.interface.janela\n'
        'try:\n'
        '    bdgdcase.interface.base._tk()\n'
        'except RuntimeError as e:\n'
        '    assert "tkinter" in str(e), e\n'
        'else:\n'
        '    raise AssertionError("_tk() deveria ter recusado")\n'
        'print("ok")\n'
    )
    r = subprocess.run([sys.executable, '-c', codigo],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert 'ok' in r.stdout


def test_abrir_janela_sem_tk_da_erro_legivel():
    """A recusa tem de dizer o que instalar, e não estourar um ImportError."""
    from bdgdcase.interface.base import _tk
    try:
        _tk()
    except RuntimeError as exc:
        assert 'tkinter' in str(exc)
    else:
        pytest.skip('este Python tem tkinter; o caminho de erro não se aplica')


def test_importar_solucao_nao_carrega_a_dll_do_opendss():
    """`import bdgdcase.solucao` não pode carregar o simulador junto.

    O OpenDSS é dependência do pacote, mas o `import opendssdirect` fica dentro
    das funções — e isso não é detalhe de estilo. A DLL do simulador é compilada
    em Free Pascal e, ao carregar no Windows, levanta e trata internamente uma
    exceção SEH; ela é benigna, mas suja a saída de qualquer ferramenta que
    apenas importe o pacote. `bdgdcase --help` não tem por que pagar isso.

    O teste roda num Python onde `opendssdirect` é inimportável: se alguém subir
    o import para o topo, o módulo deixa de importar e é aqui que se descobre.
    """
    codigo = (
        'import sys\n'
        'sys.modules["opendssdirect"] = None\n'
        'import bdgdcase.solucao as s\n'
        'assert s.PASSOS_DIA == 96\n'
        'assert "opendssdirect" not in [m for m in sys.modules\n'
        '                               if sys.modules[m] is not None]\n'
        'try:\n'
        '    s._dss()\n'
        'except RuntimeError as e:\n'
        '    assert "OpenDSS" in str(e), e\n'
        'else:\n'
        '    raise AssertionError("_dss() deveria ter recusado")\n'
        'print("ok")\n'
    )
    r = subprocess.run([sys.executable, '-c', codigo],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert 'ok' in r.stdout


def test_importar_o_mapa_nao_carrega_o_matplotlib():
    """Importar o módulo do mapa não pode arrastar a pilha de gráficos.

    Mesma razão do teste acima, e mais uma: o backend do matplotlib é escolhido
    dentro de `_mpl()` (`TkAgg`). Se o módulo fosse importado no topo, quem
    importasse o pacote fixaria o backend sem pedir — e num servidor sem tela
    isso falha na importação, não na hora de desenhar.
    """
    codigo = (
        'import sys\n'
        'sys.modules["matplotlib"] = None\n'
        'import bdgdcase.interface.mapa as m\n'
        'assert m.CAMADAS_PADRAO\n'
        'try:\n'
        '    m._mpl()\n'
        'except RuntimeError as e:\n'
        '    assert "matplotlib" in str(e), e\n'
        'else:\n'
        '    raise AssertionError("_mpl() deveria ter recusado")\n'
        'print("ok")\n'
    )
    r = subprocess.run([sys.executable, '-c', codigo],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert 'ok' in r.stdout


def test_cli_responde():
    # `-m bdgdcase` é a forma documentada, e a que não muda quando o módulo da
    # linha de comando muda de pasta.
    r = subprocess.run([sys.executable, '-m', 'bdgdcase', '--version'],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert 'bdgdcase' in r.stdout


def test_cli_recusa_dia_invalido():
    from bdgdcase.api import converter
    with pytest.raises(ValueError, match='XX'):
        converter(['qualquer'], dias=('XX',))


def test_todo_subcomando_esta_ligado_a_uma_funcao():
    """Um subparser sem `set_defaults(func=...)` explode só quando alguém o usa."""
    from bdgdcase.interface.cli import construir_parser
    sub = [a for a in construir_parser()._actions
           if hasattr(a, 'choices') and isinstance(a.choices, dict)][0]
    assert set(sub.choices) == {'listar', 'panorama', 'extrair', 'converter',
                                'verificar', 'rodar', 'mapa', 'exemplo',
                                'caminhos', 'gui', 'distribuidoras'}
    for nome, p in sub.choices.items():
        assert callable(p.get_default('func')), nome


def test_listar_recusa_caminho_que_nao_e_gdb(tmp_path):
    from bdgdcase.api import listar
    with pytest.raises(Exception):
        listar(tmp_path / 'nao_existe.gdb')
