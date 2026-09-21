# -*- coding: utf-8 -*-
"""O mínimo que toda parte da janela precisa para existir.

Três coisas, e nenhuma delas é desenho:

**Os imports adiados.** `_tk()` e `_mpl()` importam tkinter e matplotlib na hora
do uso, não na carga do módulo, e levantam um erro que diz o que instalar. É o
que permite `import bdgdcase` funcionar num contêiner de CI sem X e sem
matplotlib — a afirmação central do README, e a que `tests/test_importacao.py`
defende.

**A fonte.** `_fontes()` escolhe uma fonte que exista na máquina, porque a que
está bonita no Windows não existe no Linux e o Tk cai num substituto de largura
diferente, que desalinha a tabela inteira.

**O tema.** Uma paleta só, para as cinco abas terem a mesma cara.
"""
from __future__ import annotations


#: Paleta da interface. Cinza-azulado frio para a moldura, e cor só onde ela
#: significa alguma coisa — o valor que diagnostica. Interface que colore tudo
#: não destaca nada.
TEMA = {
    'fundo':      '#f4f6f8',
    'painel':     '#ffffff',
    'borda':      '#dde3ea',
    'titulo':     '#1b2733',
    'rotulo':     '#68788a',
    'valor':      '#1b2733',
    'nota':       '#8b9bad',
    'bom':        '#1f7a3d',
    'atencao':    '#a86a10',
    'ruim':       '#c0392b',
    'destaque':   '#1060d0',
}

def _fontes(tk):
    """Fonte de interface e fonte de números, com recuo para o que existir."""
    from tkinter import font as tkfont
    disponiveis = set(tkfont.families())

    def _primeira(*nomes):
        """A primeira das fontes pedidas que exista nesta máquina."""
        return next((n for n in nomes if n in disponiveis), None)

    ui = _primeira('Segoe UI', 'Inter', 'Helvetica Neue', 'DejaVu Sans') or 'TkDefaultFont'
    # Números numa fonte de largura fixa: é o que mantém a coluna de valores
    # alinhada quando um deles tem um dígito a mais.
    mono = _primeira('Cascadia Mono', 'Consolas', 'JetBrains Mono',
                     'DejaVu Sans Mono') or 'TkFixedFont'
    return ui, mono

def _tk():
    """Importa o tkinter na hora do uso, com erro que diz o que fazer.

    No topo do módulo, este import tornaria `import bdgdcase` impossível num
    contêiner de CI ou num servidor sem X — e é ali que a conversão em lote faz
    mais sentido rodar. Adiado, a biblioteca funciona nesses lugares e só abrir
    janela falha, com uma mensagem que nomeia o pacote a instalar.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            'Interface gráfica indisponível: este Python não tem tkinter.') from exc
    return tk, filedialog, messagebox, ttk

def _mpl():
    """Importa o matplotlib e fixa o backend TkAgg, na hora do uso.

    Adiado pela mesma razão de `_tk`: importar a biblioteca não precisa
    carregar uma pilha de gráficos. O backend é
    escolhido aqui, e não pelo ambiente: com outro backend a figura abre numa
    janela própria em vez de dentro da aba, e o que sai não é a mesma coisa.
    """
    try:
        import matplotlib
        matplotlib.use('TkAgg')
        from matplotlib.backends.backend_tkagg import (FigureCanvasTkAgg,
                                                       NavigationToolbar2Tk)
        from matplotlib.collections import LineCollection
        from matplotlib.figure import Figure
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            'O matplotlib não pôde ser carregado. Ele é dependência do '
            'pacote, então a instalação está incompleta ou quebrada: '
            'pip install --force-reinstall bdgdcase') from exc

    # `matplotlib.colormaps` chegou na 3.5 e `cm.get_cmap` saiu na 3.9. Uma
    # casquinha fina evita amarrar o pacote a uma janela estreita de versões.
    class _Paletas:
        """Um substituto para a API de colormaps que mudou de lugar.

        Matplotlib moveu `cm.get_cmap` para `matplotlib.colormaps` e depois
        passou a avisar sobre a forma antiga. Esta casca faz o resto do código
        pedir por nome sem saber a versão instalada.
        """
        @staticmethod
        def get_cmap(nome):
            """A paleta pelo nome, na API que esta versão do matplotlib oferece."""
            try:
                return matplotlib.colormaps[nome]
            except (AttributeError, KeyError):  # pragma: no cover
                return matplotlib.cm.get_cmap(nome)

    return (FigureCanvasTkAgg, Figure, LineCollection, NavigationToolbar2Tk,
            _Paletas)
