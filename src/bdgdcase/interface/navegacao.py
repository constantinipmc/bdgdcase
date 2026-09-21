# -*- coding: utf-8 -*-
"""Um mapa em Web Mercator: roda amplia, arrasto desloca, azulejos ao fundo.

Isto era parte de `interface.mapa`, e funcionava — mas estava amarrado ao `self`
da janela: `self._ax`, `self._canvas`, `self._fundo_img`, um de cada. Com um
mapa só isso não se notava. Com dois — o do alimentador simulado e o panorama de
toda a base —, o segundo apagaria o fundo do primeiro, e os dois disputariam a
mesma imagem.

A classe existe para que cada mapa tenha os seus. O que está aqui é o que os
dois fazem igual: a moldura sem margem, a projeção, a roda, o arrasto, o
enquadramento que preenche a moldura e a busca dos azulejos. O que cada um
desenha por dentro fica com ele.

## O que o dono precisa fornecer

`caixa_util()`
    O retângulo a enquadrar, em metros de Mercator, ou `None`. O mapa do caso
    devolve as barras energizadas; o panorama, a base inteira.
`ao_clicar(x, y, ctrl)`
    Clique parado com o botão esquerdo. Arrastar não chama.
`ao_mover(lon, lat)`
    A posição do cursor, já em graus.

## A regra de thread, que continua valendo

Buscar azulejos vai à rede, e a rede é lenta e imprevisível. A busca roda numa
thread que **não toca em widget**: ela empurra `('fundo', (vista, …))` na fila,
e o laço do Tk pinta. A `vista` vai na mensagem porque agora há mais de uma, e
sem ela o mosaico de um mapa acabaria pintado no outro.
"""
from __future__ import annotations

import threading

from bdgdcase.interface.base import _mpl

__all__ = ['_Vista']


class _Vista:
    """A moldura de um mapa e a navegação sobre ela. Não desenha conteúdo."""

    def __init__(self, quadro, tk, root, fila, v_fundo, caixa_util,
                 ao_clicar=None, ao_mover=None, barra=True, figsize=(7, 6)):
        """Monta figura, eixos, canvas e barra, e liga roda, arrasto e clique."""
        FigureCanvasTkAgg, Figure, _LineCollection, Toolbar, _cm = _mpl()

        self.tk, self.root, self.fila = tk, root, fila
        self.v_fundo = v_fundo
        self.caixa_util = caixa_util
        self.ao_clicar, self.ao_mover = ao_clicar, ao_mover

        # Sem `constrained`, e com os eixos ocupando a figura toda: num mapa,
        # moldura e rótulo de coordenada são espaço tirado do que interessa. A
        # posição é lida no rodapé, que é onde se procura.
        fig = Figure(figsize=figsize, dpi=100)
        ax = fig.add_axes((0.0, 0.0, 1.0, 1.0))
        # Mercator é conforme: um metro em x mede o mesmo que um metro em y, e
        # o aspecto é 1:1 sem correção. Era em graus que a figura saía esticada
        # no sentido leste-oeste e precisava do cosseno da latitude.
        self.aspecto = 1.0
        ax.set_aspect(1.0, adjustable='box')
        # Sem marcas: a moldura é para o mapa. A posição do cursor vai no
        # rodapé, em grau, que é onde se procura por ela.
        ax.set_xticks([])
        ax.set_yticks([])

        # A barra do matplotlib mostraria os metros de Mercator, que não dizem
        # nada a quem olha um mapa. Aqui ela passa a falar grau.
        from bdgdcase.interface.azulejos import merc_inv

        def _coord(x, y):
            """A posição do cursor em latitude e longitude, para o rodapé."""
            lon, lat = merc_inv(x, y)
            return '%.6f, %.6f' % (lat, lon)

        ax.format_coord = _coord
        for lado_ in ax.spines.values():
            lado_.set_visible(False)

        self._fundo_img = None
        self._fundo_alvo = None
        self._tarefa_fundo = None
        self._tarefa_ajuste = None
        self._arrasto = None

        self.creditos = ax.text(0.99, 0.01, '', transform=ax.transAxes,
                                ha='right', va='bottom', fontsize=7,
                                color='#f0f0f0',
                                bbox={'facecolor': '#00000060', 'pad': 1.5,
                                      'edgecolor': 'none'})

        self.fig, self.ax = fig, ax
        canvas = FigureCanvasTkAgg(fig, master=quadro)
        # A barra de navegação dá zoom e arrasto — num alimentador de 16 mil
        # barras, olhar sem zoom não é olhar. Ela é empacotada ANTES do canvas:
        # o canvas expande e, se viesse primeiro, não sobraria altura para ela.
        if barra:
            Toolbar(canvas, quadro, pack_toolbar=False).pack(
                side=tk.BOTTOM, fill=tk.X)
        canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.canvas = canvas
        self._conectar()
        # O '+' é obrigatório: sem ele, `bind` SUBSTITUI a ligação que o
        # FigureCanvasTkAgg instala em <Configure> — e é ela que redimensiona a
        # figura junto com o widget. Sem ela a figura ficava presa no figsize
        # inicial (700x600 px) por maior que fosse a janela, que era o mapa
        # pequeno no canto.
        canvas.get_tk_widget().bind('<Configure>', self._redimensionou, '+')

    @property
    def widget(self):
        """O widget Tk do canvas — é sobre ele que a legenda se sobrepõe."""
        return self.canvas.get_tk_widget()

    def desenhar(self):
        """Repinta quando o laço do Tk puder."""
        self.canvas.draw_idle()

    # ── Roda, arrasto e clique ───────────────────────────────────────────────

    def _conectar(self):
        """Roda amplia, arrasto desloca, clique parado seleciona.

        A barra do matplotlib exige escolher a ferramenta antes de usar; num
        mapa espera-se o contrário — a roda amplia e o arrasto desloca, sem
        modo. A barra continua ali para quem quiser o zoom por retângulo e o
        botão de voltar ao enquadramento inicial.
        """
        self.canvas.mpl_connect('scroll_event', self._rodou)
        self.canvas.mpl_connect('button_press_event', self._apertou)
        self.canvas.mpl_connect('motion_notify_event', self._moveu)
        self.canvas.mpl_connect('button_release_event', self._soltou)
        self._arrasto = None

    def _rodou(self, e):
        """Amplia mantendo sob o cursor o ponto que estava sob o cursor."""
        if e.inaxes is not self.ax or e.xdata is None:
            return
        fator = 0.8 if e.button == 'up' else 1.25
        x0, x1 = self.ax.get_xlim()
        y0, y1 = self.ax.get_ylim()
        self.ax.set_xlim(e.xdata - (e.xdata - x0) * fator,
                         e.xdata + (x1 - e.xdata) * fator)
        self.ax.set_ylim(e.ydata - (e.ydata - y0) * fator,
                         e.ydata + (y1 - e.ydata) * fator)
        self.desenhar()
        self.agendar_fundo()

    def _apertou(self, e):
        """Começa um arrasto, guardando a âncora em pixels.

        Em pixels e não em coordenadas de dados: a âncora em dados se moveria
        junto com a vista durante o próprio arrasto, e o mapa fugiria do
        cursor.
        """
        if e.inaxes is not self.ax or e.xdata is None:
            return
        self._arrasto = {'px': (e.x, e.y), 'dados': (e.xdata, e.ydata),
                         'lim': (self.ax.get_xlim(), self.ax.get_ylim()),
                         'moveu': False, 'botao': e.button,
                         # O Ctrl acumula seleção, e a tecla é lida no APERTO:
                         # soltar o botão depois de largar o Ctrl é comum, e
                         # ler no fim perderia o modificador.
                         'ctrl': 'control' in str(getattr(e, 'key', '') or '')}

    def _moveu(self, e):
        """Atualiza a posição mostrada e, se houver arrasto, desloca a vista."""
        if e.inaxes is self.ax and e.xdata is not None and self.ao_mover:
            from bdgdcase.interface.azulejos import merc_inv
            lon, lat = merc_inv(e.xdata, e.ydata)
            self.ao_mover(lon, lat)
        a = self._arrasto
        if not a or a['botao'] != 1 or e.x is None:
            return
        dx_px, dy_px = e.x - a['px'][0], e.y - a['px'][1]
        if not a['moveu'] and abs(dx_px) + abs(dy_px) < 4:
            return                      # tremor de clique não é arrasto
        a['moveu'] = True
        (x0, x1), (y0, y1) = a['lim']
        caixa = self.ax.get_window_extent()
        dx = -dx_px * (x1 - x0) / max(caixa.width, 1)
        dy = -dy_px * (y1 - y0) / max(caixa.height, 1)
        self.ax.set_xlim(x0 + dx, x1 + dx)
        self.ax.set_ylim(y0 + dy, y1 + dy)
        self.desenhar()

    def _soltou(self, e):
        """Termina o arrasto. Sem movimento, o clique vale como seleção.

        É o que faz um clique parado selecionar e um clique arrastado só mover
        o mapa, sem que o usuário precise escolher entre dois modos.
        """
        a, self._arrasto = self._arrasto, None
        if not a:
            return
        if a['moveu']:
            self.agendar_fundo()
        elif a['botao'] == 1 and self.ao_clicar:
            self.ao_clicar(a['dados'][0], a['dados'][1], a['ctrl'])

    # ── Enquadramento ────────────────────────────────────────────────────────

    def reenquadrar(self):
        """Volta ao enquadramento inicial, preenchendo a moldura atual."""
        self.enquadrar()
        self.desenhar()
        self.agendar_fundo()

    def _redimensionou(self, _evento=None):
        """Redimensionar a janela muda a moldura; a vista tem de acompanhar.

        Sem isto, alargar a janela devolveria a faixa branca — a proporção da
        vista continuaria a da moldura antiga.
        """
        if self._arrasto:
            return
        if self._tarefa_ajuste:
            try:
                self.root.after_cancel(self._tarefa_ajuste)
            except Exception:
                pass
        self._tarefa_ajuste = self.root.after(220, self._ajustar_proporcao)

    def _ajustar_proporcao(self):
        """Mantém o centro e a escala, e reajusta a vista à moldura nova."""
        x0, x1 = self.ax.get_xlim()
        y0, y1 = self.ax.get_ylim()
        self.enquadrar((x0, y0, x1, y1))
        self.desenhar()
        self.agendar_fundo()

    def enquadrar(self, caixa=None):
        """Encaixa a área de interesse preenchendo a moldura toda.

        Deixar o matplotlib impor o aspecto encolhe a *caixa dos eixos* e sobra
        faixa branca em cima e embaixo — que foi o que aconteceu com um
        alimentador largo e baixo. Aqui a conta é ao contrário: o aspecto e o
        tamanho da moldura são dados, e a *vista* é esticada até preencher. O
        excedente vira mais mapa, não vazio.

        Em Web Mercator o aspecto é 1:1, então a moldura de W×H pixels fica
        cheia quando `Δy/Δx = H/W`.
        """
        caixa = caixa or self.caixa_util()
        if caixa is None:
            return
        o, s_, l, n = caixa
        dx, dy = max(l - o, 1e-9), max(n - s_, 1e-9)

        # `get_window_extent` devolve a caixa **já encolhida** por uma aplicação
        # anterior do aspecto. Medir por ela faz o enquadramento convergir para
        # o tamanho reduzido: cada passagem confirma o encolhimento em vez de
        # desfazê-lo, e o mapa nunca recupera a largura perdida.
        # `get_position(original=True)` dá a fatia da figura que os eixos
        # receberam, que é a moldura de verdade.
        pos = self.ax.get_position(original=True)
        fig = self.ax.get_figure()
        larg_fig, alt_fig = fig.get_size_inches() * fig.dpi
        larg = max(pos.width * larg_fig, 1.0)
        alt = max(pos.height * alt_fig, 1.0)
        A = self.ax.get_aspect()
        A = 1.0 if not isinstance(A, (int, float)) else float(A)
        alvo = alt / (larg * A)          # Δlat/Δlon que preenche a moldura

        if dy / dx < alvo:               # sobra altura: estica a latitude
            extra = (dx * alvo - dy) / 2.0
            s_, n = s_ - extra, n + extra
        else:                            # sobra largura: estica a longitude
            extra = (dy / alvo - dx) / 2.0
            o, l = o - extra, l + extra
        self.ax.set_xlim(o, l)
        self.ax.set_ylim(s_, n)

    # ── O fundo ──────────────────────────────────────────────────────────────

    def agendar_fundo(self, atraso=350):
        """Rebusca os azulejos depois que a mão parou.

        Sem a espera, uma rolagem de roda dispararia uma dezena de buscas, e
        todas menos a última seriam jogadas fora.
        """
        if self._tarefa_fundo:
            try:
                self.root.after_cancel(self._tarefa_fundo)
            except Exception:
                pass
        self._tarefa_fundo = self.root.after(atraso, self.trocar_fundo)

    def trocar_fundo(self):
        """Busca os azulejos numa thread e os põe atrás da rede.

        Em thread porque a primeira busca de uma área vai à rede: um mosaico de
        dez azulejos leva cerca de um segundo, e travar a janela nisso seria
        pior que não ter fundo.
        """
        fundo = self.v_fundo.get()
        if self._fundo_img is not None:
            self._fundo_img.remove()
            self._fundo_img = None
        self.creditos.set_text('')
        if fundo == 'nenhum':
            self.desenhar()
            return

        from bdgdcase.interface.azulejos import merc_inv
        x0, x1 = self.ax.get_xlim()
        y0, y1 = self.ax.get_ylim()
        oeste, sul = merc_inv(x0, y0)
        leste, norte = merc_inv(x1, y1)
        alvo = (fundo, oeste, sul, leste, norte)
        self._fundo_alvo = alvo

        def trabalho():
            """Baixa os azulejos do fundo, fora do laço do Tk.

            A rede é lenta e imprevisível. Buscando no laço do Tk, mover o mapa
            congelaria a janela por segundos — e o Windows ofereceria encerrar
            o programa.
            """
            from bdgdcase.interface.azulejos import buscar, credito
            try:
                r = buscar(oeste, sul, leste, norte, fundo=fundo)
            except Exception:
                r = None
            self.fila.put(('fundo', (self, alvo, r, credito(fundo))))

        threading.Thread(target=trabalho, daemon=True).start()

    def pintar_fundo(self, alvo, resultado, credito):
        """Põe na tela o mapa base que a thread trouxe, se ele ainda serve.

        A verificação de `_fundo_alvo` é o ponto: enquanto os azulejos vinham,
        o usuário pode ter movido ou trocado o fundo. Pintar o que chegou tarde
        deixaria a imagem de uma vista que não é mais a atual.

        Devolve `False` quando o mosaico chegou tarde, `None` quando não veio
        fundo nenhum — é o que permite ao dono avisar no rodapé — e `True`
        quando pintou.
        """
        if self._fundo_alvo != alvo:
            return False
        if resultado is None:
            return None
        img, (o, l, s_, n) = resultado
        xlim, ylim = self.ax.get_xlim(), self.ax.get_ylim()
        self._fundo_img = self.ax.imshow(img, extent=(o, l, s_, n),
                                         origin='upper', zorder=0,
                                         interpolation='bilinear')
        # `imshow` impõe aspecto 1.0 aos eixos e reenquadra por conta própria.
        # Deixar passar era o que encolhia o mapa para um retângulo no canto
        # assim que o satélite carregava: o alimentador é largo e baixo, e com
        # aspecto 1.0 a caixa dos eixos encolhe para caber. A vista e o aspecto
        # geográfico são de quem olha, não da imagem de fundo.
        self.ax.set_aspect(self.aspecto, adjustable='box')
        self.ax.set_xlim(xlim)
        self.ax.set_ylim(ylim)
        self.creditos.set_text(credito)
        self.desenhar()
        return True
