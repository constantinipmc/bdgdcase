# -*- coding: utf-8 -*-
"""Todos os alimentadores da base no mapa, para escolher clicando.

A aba de preparar lista os alimentadores como texto: oitocentos códigos num
`Listbox`. O código é a chave que a extração exige, e não diz onde o
alimentador passa — quem procura o do bairro escolhia por tentativa, e cada
tentativa custa os minutos de uma extração.

Esta aba desenha os oitocentos de uma vez, cada um com a sua cor, sobre o mapa
base. Clicar num deles diz qual é e o marca na lista da aba de preparar, de onde
o **Executar** segue como sempre seguiu.

## O que este mapa NÃO é

Não é a rede. O traçado vem de `extracao.panorama`, costurado e simplificado:
os vãos de poste a poste viram um traço contínuo por ramo, com os vértices que
não mudam o desenho descartados. O traço está **inteiro** — o que não está é a
posição exata de cada poste. É um mapa de localização, para responder *onde* e
*qual* antes de gastar minutos extraindo o que talvez seja outro.

A rede de verdade está na aba do mapa, depois de converter o caso.

## Duas coisas que a legibilidade exige

**Cor não identifica, realce identifica.** Vinte cores para oitocentos
alimentadores: elas se repetem, e é inevitável. Servem para **separar
vizinhos** — para que dois alimentadores que se tocam não pareçam um só. Quem
responde "qual é este" é o realce do clique, mais a ficha.

**Sobre satélite, cor sozinha some.** A imagem é escura e cheia de textura, e um
traço de um pixel e meio em cor média desaparece nela — foi assim que o mapa
pareceu *perder* os alimentadores ao trocar o fundo claro pelo híbrido. Não era
ordem de desenho: o traço estava por cima, e invisível assim mesmo. O contorno
escuro por baixo devolve o contraste, e só é desenhado nos fundos que precisam
dele: sobre o fundo claro seria o dobro de traço sem retorno nenhum.
"""
from __future__ import annotations

from bdgdcase.interface.base import _mpl

__all__ = ['_AbaPanorama']

#: Quantas cores a paleta cíclica tem. `tab20` é qualitativa — feita para
#: distinguir categorias, e não para sugerir ordem entre elas, que é o que uma
#: paleta contínua faria com códigos de alimentador.
N_CORES = 20

#: Raio do clique, em pixels. Largo o bastante para acertar um traço fino sem
#: mira, apertado o bastante para que clicar no vazio desmarque.
RAIO_PX = 14

#: Espessura do traço comum, a do alimentador em foco, e a do contorno que vai
#: por baixo nos fundos escuros.
TRACO = 1.4
TRACO_FOCO = 2.8
TRACO_CONTORNO = 3.2

#: Quanto o resto do mapa apaga quando há um alimentador escolhido. Apagar, e
#: não esconder: a vizinhança é o que situa o escolhido no mapa.
ALFA_APAGADO = 0.3

#: Os fundos em que o traço precisa de contorno para ser visto. Imagem de
#: satélite é escura e cheia de textura; o mapa claro e o de ruas não são.
FUNDOS_ESCUROS = ('satelite', 'hibrido')


class _AbaPanorama:
    """A aba que mostra a base inteira. Só métodos com `_ui_` tocam em widget."""

    def _panorama_iniciar(self):
        """O estado da aba, montado antes de os widgets existirem.

        Espelha `_preparar_iniciar`: a aba é um mixin, e quem constrói a janela
        é `interface.janela`.
        """
        tk = self.tk
        self.pan = None                 # o Panorama de extracao.panorama
        self.pan_linhas = None          # as polilinhas dele, em Web Mercator
        self.pan_sel = []               # os códigos escolhidos, na ordem
        self.vista_pan = None
        self._pan_cores = []
        self._pan_col = self._pan_foco = self._pan_contorno = None
        self._pan_halo = None

        # Claro, e não satélite: numa vista de estado inteiro a imagem de
        # satélite é ruído colorido por baixo de traços coloridos. O satélite
        # continua a um clique, e aí o contorno entra para dar contraste.
        self.v_pan_fundo = tk.StringVar(value='claro')
        self.v_pan_busca = tk.StringVar()
        self.v_pan_status = tk.StringVar(
            value='Varra uma base na aba "Preparar" para desenhar o mapa.')

    def _panorama_ligar(self):
        """Liga a busca, depois que os widgets já existem."""
        self.v_pan_busca.trace_add('write', lambda *_: self._ui_pan_buscar())

    # ── Construção ───────────────────────────────────────────────────────────

    def _ui_pan_montar(self):
        """Monta a aba dentro do quadro que a janela reservou para ela."""
        tk, ttk = self.tk, self.ttk
        m = ttk.Frame(self.quadro_panorama, padding=8)
        m.pack(fill=tk.BOTH, expand=True)
        m.columnconfigure(0, weight=1)
        m.rowconfigure(1, weight=1)

        topo = ttk.Frame(m)
        topo.grid(row=0, column=0, sticky='ew')
        topo.columnconfigure(3, weight=1)
        ttk.Label(topo, text='Buscar:').grid(row=0, column=0)
        ttk.Entry(topo, textvariable=self.v_pan_busca, width=18).grid(
            row=0, column=1, padx=(6, 12))
        self.btn_pan_lista = ttk.Button(topo, text='Ir para a lista',
                                        state='disabled',
                                        command=self._ui_pan_para_lista)
        self.btn_pan_lista.grid(row=0, column=2)
        self.prog_pan = ttk.Progressbar(topo, mode='determinate')
        self.prog_pan.grid(row=0, column=3, sticky='ew', padx=12)
        ttk.Label(topo, text='Fundo:').grid(row=0, column=4)
        from bdgdcase.interface.azulejos import FUNDOS
        for i, chave in enumerate(('claro', 'ruas', 'hibrido', 'satelite',
                                   'nenhum')):
            ttk.Radiobutton(topo, text=FUNDOS[chave]['rotulo'], value=chave,
                            variable=self.v_pan_fundo,
                            command=self._ui_pan_trocar_fundo
                            ).grid(row=0, column=5 + i, padx=(6, 0))

        self.quadro_pan_mapa = ttk.Frame(m)
        self.quadro_pan_mapa.grid(row=1, column=0, sticky='nsew', pady=(8, 0))

        ttk.Label(m, textvariable=self.v_pan_status, style='Rodape.TLabel'
                  ).grid(row=2, column=0, sticky='w', pady=(6, 0))

    # ── Desenho ──────────────────────────────────────────────────────────────

    def _ui_pan_desenhar(self, pan):
        """Recebe o panorama pronto e monta o mapa da base inteira.

        Refaz a vista do zero: trocar de `.gdb` muda o número de alimentadores,
        a paleta e a extensão, e reaproveitar os artistas antigos deixaria o
        traçado da base anterior por baixo do da nova.
        """
        _canvas_tk, _figura, LineCollection, _barra, _cm = _mpl()

        self.pan = pan
        self.pan_sel = []
        for w in self.quadro_pan_mapa.winfo_children():
            w.destroy()

        if not len(pan):
            self.vista_pan = None
            self.pan_linhas = None
            self.v_pan_status.set(
                'A base não trouxe geometria de média tensão — sem SSDMT não '
                'há o que desenhar.')
            return

        self.pan_linhas = self._pan_projetar(pan)
        self._pan_cores = self._pan_paleta()

        from bdgdcase.interface.navegacao import _Vista
        self.vista_pan = _Vista(
            self.quadro_pan_mapa, self.tk, self.root, self.fila,
            self.v_pan_fundo, caixa_util=self._pan_caixa,
            ao_clicar=self._pan_clicou,
            ao_mover=lambda lon_, lat_: None)
        ax = self.vista_pan.ax

        # O contorno vem primeiro, por baixo de tudo: é ele que faz o traço
        # colorido sobreviver à imagem de satélite. Fica escondido nos fundos
        # claros, onde seria só custo — ver `_ui_pan_contornar`.
        self._pan_contorno = LineCollection(
            self.pan_linhas, linewidths=TRACO_CONTORNO, colors='#0d1117',
            alpha=0.75, zorder=1.5, capstyle='round')
        ax.add_collection(self._pan_contorno)

        # Uma coleção só, com uma cor por traço. Oitocentos artistas — um por
        # alimentador — não redesenham a tempo de acompanhar o arrasto.
        self._pan_col = LineCollection(
            self.pan_linhas, linewidths=TRACO, capstyle='round', zorder=2,
            colors=[self._pan_cores[i] for i in pan.indice])
        ax.add_collection(self._pan_col)

        # O realce: um halo branco por baixo e o traço grosso por cima. Halo
        # porque sobre satélite, ou sobre um vizinho da mesma cor, engrossar
        # sozinho não separa.
        self._pan_halo = LineCollection([], linewidths=6.5, colors='#ffffff',
                                        alpha=0.9, zorder=3, capstyle='round')
        self._pan_foco = LineCollection([], linewidths=TRACO_FOCO, zorder=4,
                                        capstyle='round')
        ax.add_collection(self._pan_halo)
        ax.add_collection(self._pan_foco)

        self._ui_pan_contornar()
        self._ui_pan_ficha()
        self.btn_pan_lista.config(state='normal')
        # O enquadramento depende da moldura em pixels, e a moldura só tem
        # tamanho depois que o Tk desenha. Daí a espera de um ciclo.
        self.root.after(60, self.vista_pan.reenquadrar)

    def _pan_projetar(self, pan):
        """As polilinhas em Web Mercator, que é onde os azulejos assentam.

        A projeção é feita nos vértices todos de uma vez, e as polilinhas são
        fatias desse array: são centenas de milhares de pontos, e projetar um a
        um custaria segundos a cada abertura.
        """
        import numpy as np

        from bdgdcase.interface.azulejos import RAIO

        lon = np.radians(pan.coords[:, 0])
        lat = np.radians(np.clip(pan.coords[:, 1], -85.05, 85.05))
        xy = np.stack([RAIO * lon,
                       RAIO * np.log(np.tan(np.pi / 4.0 + lat / 2.0))], axis=-1)
        return [xy[pan.partes[p]:pan.partes[p + 1]]
                for p in range(len(pan.indice))]

    def _pan_paleta(self):
        """Uma cor por alimentador, com vizinhos geográficos em cores distintas.

        As cores se repetem — são vinte para centenas —, e o que decide se a
        repetição atrapalha é *onde* ela cai. Ordenar por longitude do centro
        antes de ciclar afasta no mapa os que compartilham a cor: dois
        alimentadores da mesma cor ficam a vinte posições de distância no
        sentido leste-oeste, que é longe.
        """
        import numpy as np
        from matplotlib.colors import to_hex

        _c, _f, _lc, _b, cm = _mpl()
        paleta = cm.get_cmap('tab20')
        n = len(self.pan.alimentadores)

        # A longitude em que cada traço começa basta para ordenar: o que se
        # quer é a posição grosseira do alimentador dentro do estado.
        lon = self.pan.coords[self.pan.partes[:-1], 0]
        soma, conta = np.zeros(n), np.zeros(n)
        np.add.at(soma, self.pan.indice, lon)
        np.add.at(conta, self.pan.indice, 1.0)
        centro = np.full(n, np.inf)
        vivo = conta > 0
        centro[vivo] = soma[vivo] / conta[vivo]

        cores = [None] * n
        for posicao, i in enumerate(np.argsort(centro, kind='stable')):
            cores[int(i)] = to_hex(paleta(posicao % N_CORES))
        return cores

    def _pan_caixa(self):
        """A extensão da base, em metros de Mercator, para o enquadramento."""
        import numpy as np

        if not self.pan_linhas:
            return None
        pontos = np.concatenate(self.pan_linhas)
        o, l = float(np.min(pontos[:, 0])), float(np.max(pontos[:, 0]))
        s_, n = float(np.min(pontos[:, 1])), float(np.max(pontos[:, 1]))
        folga = 0.03
        mx = max((l - o) * folga, 50.0)
        my = max((n - s_) * folga, 50.0)
        return o - mx, s_ - my, l + mx, n + my

    # ── Escolha ──────────────────────────────────────────────────────────────

    def _pan_clicou(self, x, y, ctrl):
        """Um clique parado no mapa: escolhe o alimentador mais próximo.

        O raio vem da escala da vista, e não de um número fixo em graus: a
        mesma tolerância de catorze pixels vale afastado, olhando o estado, e
        ampliado, olhando uma rua.
        """
        if self.pan is None or self.vista_pan is None:
            return
        from bdgdcase.interface.azulejos import merc_inv

        ax = self.vista_pan.ax
        x0, x1 = ax.get_xlim()
        largura = max(ax.get_window_extent().width, 1.0)
        passo = RAIO_PX * (x1 - x0) / largura
        lon, lat = merc_inv(x, y)
        # O raio sai em graus de LATITUDE, que é a unidade em que
        # `Panorama.mais_proximo` mede — ela corrige a longitude pelo cosseno.
        _lon2, lat2 = merc_inv(x, y + passo)
        alvo = self.pan.mais_proximo(lon, lat, abs(lat2 - lat))

        if alvo is None:
            # Clique no vazio limpa a seleção. Quem clica longe de tudo não
            # está escolhendo — e sem isto não haveria como desmarcar.
            self.pan_sel = []
        elif ctrl:
            self.pan_sel = ([c for c in self.pan_sel if c != alvo]
                            if alvo in self.pan_sel else self.pan_sel + [alvo])
        else:
            self.pan_sel = [alvo]

        self._ui_pan_realcar()
        self._ui_pan_ficha()
        self._ui_pan_sincronizar()

    def _ui_pan_realcar(self):
        """Põe em evidência os escolhidos e apaga o resto."""
        import numpy as np

        if self._pan_col is None:
            return
        if not self.pan_sel:
            self._pan_col.set_alpha(1.0)
            self._pan_halo.set_segments([])
            self._pan_foco.set_segments([])
        else:
            partes = np.concatenate([self.pan.partes_de(c)
                                     for c in self.pan_sel])
            realce = [self.pan_linhas[p] for p in partes]
            self._pan_col.set_alpha(ALFA_APAGADO)
            self._pan_halo.set_segments(realce)
            self._pan_foco.set_segments(realce)
            self._pan_foco.set_color([self._pan_cores[i]
                                      for i in self.pan.indice[partes]])
        self._ui_pan_contornar()
        self.vista_pan.desenhar()

    def _ui_pan_contornar(self):
        """Liga o contorno escuro só nos fundos que o exigem.

        Sobre satélite, um traço de 1,4 px em cor média some na imagem — foi
        assim que o mapa pareceu perder os alimentadores ao trocar o fundo. O
        contorno devolve o contraste. Sobre o fundo claro ele não acrescenta
        nada e dobraria o traço a desenhar, então fica desligado.

        O contorno acompanha o esmaecimento do traço que ele contorna: senão,
        com um alimentador escolhido, o resto do mapa perderia a cor e ficaria
        só a sombra — mais visível que o próprio traço, e sem dizer nada.
        """
        if self._pan_contorno is None:
            return
        self._pan_contorno.set_visible(
            self.v_pan_fundo.get() in FUNDOS_ESCUROS)
        self._pan_contorno.set_alpha(
            0.75 * (ALFA_APAGADO if self.pan_sel else 1.0))

    def _ui_pan_ficha(self):
        """Escreve no rodapé o que se sabe do que está escolhido."""
        if self.pan is None:
            return
        if not self.pan_sel:
            self.v_pan_status.set(
                '%d alimentadores em %s traços. Clique num deles para '
                'escolher; Ctrl+clique acumula. O traçado é simplificado — '
                'serve para localizar, não para medir.'
                % (len(self.pan.alimentadores),
                   '{:,}'.format(len(self.pan)).replace(',', '.')))
            return
        if len(self.pan_sel) > 1:
            self.v_pan_status.set(
                '%d escolhidos: %s' % (len(self.pan_sel),
                                       ', '.join(self.pan_sel)))
            return

        cod = self.pan_sel[0]
        f = self.pan.info.get(cod, {})
        partes = [cod]
        if f.get('nome') and f['nome'] != cod:
            partes.append(f['nome'])
        if f.get('kv'):
            partes.append('%.1f kV' % f['kv'])
        if f.get('km'):
            partes.append('%.1f km de rede' % f['km'])
        if f.get('mwh_ano'):
            partes.append('%.0f MWh/ano' % f['mwh_ano'])
        self.v_pan_status.set('  ·  '.join(partes)
                              + '   — marcado na lista da aba Preparar.')

    def _ui_pan_sincronizar(self):
        """Marca na lista da aba de preparar o que se escolheu no mapa.

        O filtro é limpo antes: ele esconde da lista o que não casa com o que
        está digitado, e esconder justamente o que se acabou de escolher no
        mapa faria o `Executar` parecer que perdeu a seleção.
        """
        if self.v_filtro.get():
            self.v_filtro.set('')       # o trace repovoa a lista
        else:
            self._ui_repovoar()
        self.lst.selection_clear(0, 'end')
        escolhidos = set(self.pan_sel)
        primeiro = None
        for i in range(self.lst.size()):
            if self.lst.get(i) in escolhidos:
                self.lst.selection_set(i)
                if primeiro is None:
                    primeiro = i
        if primeiro is not None:
            self.lst.see(primeiro)

    def _ui_pan_buscar(self):
        """Digitar um código escolhe e enquadra o alimentador correspondente.

        Casa por prefixo, e só age quando sobra um: com a busca em `URB` e
        trinta candidatos, saltar para um deles seria adivinhar.
        """
        alvo = self.v_pan_busca.get().strip().upper()
        if self.pan is None or not alvo:
            return
        casam = [c for c in self.pan.alimentadores if c.upper().startswith(alvo)]
        if len(casam) != 1:
            return
        self.pan_sel = casam
        self._ui_pan_realcar()
        self._ui_pan_ficha()
        self._ui_pan_sincronizar()
        self._ui_pan_enquadrar(casam[0])

    def _ui_pan_enquadrar(self, codigo):
        """Aproxima a vista no alimentador pedido."""
        import numpy as np

        partes = self.pan.partes_de(codigo)
        if not len(partes):
            return
        pontos = np.concatenate([self.pan_linhas[p] for p in partes])
        o, l = float(np.min(pontos[:, 0])), float(np.max(pontos[:, 0]))
        s_, n = float(np.min(pontos[:, 1])), float(np.max(pontos[:, 1]))
        folga = max((l - o) * 0.15, (n - s_) * 0.15, 200.0)
        self.vista_pan.enquadrar((o - folga, s_ - folga, l + folga, n + folga))
        self.vista_pan.desenhar()
        self.vista_pan.agendar_fundo()

    def _ui_pan_trocar_fundo(self):
        """Troca o mapa base desta aba — só dela, e não o da aba do mapa."""
        if self.vista_pan is not None:
            self._ui_pan_contornar()
            self.vista_pan.trocar_fundo()

    def _ui_pan_para_lista(self):
        """Leva para a aba de preparar, com o que foi escolhido já marcado.

        Não dispara a extração: um clique errado no mapa custaria minutos, e
        desfazer extração não existe. Quem manda continua sendo o `Executar`.
        """
        self.abas.select(self.quadro_preparar)

    def _ui_pan_progresso(self, fracao, texto):
        """Anda a barra e escreve o andamento enquanto a base é lida."""
        self.prog_pan['value'] = 100.0 * float(fracao)
        self.v_pan_status.set(texto)
