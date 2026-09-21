"""Mapa georreferenciado do alimentador, percorrível ao longo do dia.

O `rodar_dia` devolve o alimentador inteiro resumido a algumas séries. Isso
responde "como foi o dia", e não responde "onde". Um alimentador com tensão
mínima de 0,93 pu pode ter um único ramal ruim ou meio bairro afundando, e a
diferença muda o que se faz a respeito.

Esta janela mostra os dois eixos que faltavam. O espaço: cada barra no seu
lugar geográfico, colorida pela tensão; cada trecho colorido pelo quanto do
condutor está sendo usado. E o tempo: uma barra deslizante de 96 posições, 15
em 15 minutos, com animação — dá para ver a ponta da noite chegar pelo
alimentador.

Exige `matplotlib`, o OpenDSS e o Tkinter. Os dois primeiros vêm com o
pacote; o Tkinter vem com o Python (no Debian/Ubuntu, `apt install python3-tk`).

A memória cresce com a rede, não com o tempo: os 96 instantes de um alimentador
urbano de 16 mil barras ocupam cerca de 22 MB, guardados de uma vez para que
mover a barra deslizante não resolva nada de novo.
"""
from __future__ import annotations

from bdgdcase.interface.base import TEMA, _mpl
from bdgdcase.interface.geometria import (
    CINZA_SEM_TENSAO, CORES_FAIXA, FAIXAS, _Geometria)

__all__ = ['_AbaMapa', 'ordem_por_gravidade']


#: As camadas do mapa e o que vem ligado. Cabos e barras sempre; o resto é
#: marcador, e marcador demais num alimentador de dezesseis mil barras vira
#: mancha. Quem precisa, liga.
#:
#: **Chaves vêm ligadas, e é conexão, não enfeite.** A chave é um equipamento
#: SOBRE o condutor: o cabo existe com ela aberta ou fechada. Desligada, a
#: camada tirava do desenho trechos de tronco de até 150 m, e o mapa mostrava
#: dois postes de média sem nada entre eles — que se lê como rede partida, e é
#: a conclusão errada. Medido: em cada alimentador há de 2 a 22 barras de média
#: cujo ÚNICO caminho desenhado passava por uma chave.
#:
#: O que se desenha é só o que conduz: a geometria já deixa de fora as chaves
#: que o caso emitiu desabilitadas — 14 das 113 no alimentador medido.
CAMADAS_ROTULO = {
    'mt': 'cabos MT',
    'bt': 'cabos BT',
    'trafos': 'trafos',
    'chaves': 'chaves',
    'capacitores': 'capacitores',
    'reguladores': 'reguladores',
    'gd': 'GD',
    'barras_mt': 'barras MT',
    'barras_bt': 'barras BT',
}
CAMADAS_PADRAO = {'mt': True, 'bt': True, 'trafos': True, 'chaves': True,
                  'capacitores': True, 'reguladores': True,
                  'gd': True, 'barras_mt': True, 'barras_bt': True}

#: As camadas de barra, e o nível que cada uma desenha.
CAMADAS_BARRA = {'barras_mt': 'mt', 'barras_bt': 'bt'}

#: Cor de cada camada, e a forma com que a legenda a desenha.
#:
#: Média em vermelho e baixa em azul: é a convenção de quem lê diagrama de
#: distribuição, e separa os dois níveis de tensão sem depender de espessura —
#: que some quando o mapa está afastado. As cores são trocáveis pela legenda; o
#: que está aqui é onde se começa.
#:
#: `barras` não tem cor fixa: elas são pintadas pela escala de tensão, e uma cor
#: sólida ali apagaria justamente o que o mapa mostra.
CORES_PADRAO = {
    'mt': '#c0392b',
    'bt': '#2e6fd6',
    'trafos': '#ffd24a',
    'chaves': '#ff8c1a',
    'capacitores': '#8e44ad',
    'reguladores': '#00a3c4',
    'gd': '#16a085',
}

#: Como a legenda desenha o símbolo de cada camada: 'linha' grossa, 'linha'
#: fina, ou o marcador correspondente.
FORMA_CAMADA = {
    'mt': ('linha', 3.0),
    'bt': ('linha', 1.8),
    'trafos': ('quadrado', 7),
    'chaves': ('xis', 8),
    'capacitores': ('losango', 8),
    'reguladores': ('pentagono', 9),
    'gd': ('triangulo', 8),
    # As barras aparecem como as três faixas, no tamanho com que o mapa as
    # desenha: em média são maiores que em baixa, e é assim que se distinguem
    # olhando — a cor pertence à faixa, não ao nível.
    'barras_mt': ('faixas', 5),
    'barras_bt': ('faixas', 3),
}

#: Tamanho do ponto de cada nível no mapa, em pontos². Média maior porque é a
#: espinha do alimentador; baixa menor porque são muitas e viram mancha.
TAMANHO_BARRA = {'mt': 26, 'bt': 13}


#: Largura do rótulo no inspetor, em caracteres. Fixa para que os valores
#: alinhem numa coluna — é o alinhamento que faz a lista ser lida como tabela e
#: não como texto corrido.
COL_ROTULO = 24


def ordem_por_gravidade(f):
    """A ordem em que desenhar os pontos de uma camada: o pior por último.

    Barras diferentes caem na **mesma coordenada**. Um poste com transformador
    tem a barra do ramal, a do trecho que chega e a do que sai, e no mapa isso é
    um ponto só. Quem decide a cor daquele ponto é o último desenhado — e sem
    ordenar, "último" é a ordem em que o caso foi escrito, que não quer dizer
    nada.

    O resultado era uma barra adequada tapando três precárias, e o painel
    discordando do mapa no mesmo clique. Medido em ISL07 às 18h30: 990 pontos
    com barras empilhadas, e em 79 deles a de cima escondia uma faixa pior.

    Ordenar por gravidade faz o ponto mostrar o pior que há ali, que é o que o
    mapa existe para deixar ver. Nenhuma barra muda de cor — muda quem fica por
    cima —, e clicar no ponto continua percorrendo a pilha inteira, uma a uma.

    Barra sem fase energizada (NaN) vai para o fundo: ausência de tensão não
    pode esconder problema de tensão.
    """
    import numpy as np
    return np.argsort(np.nan_to_num(np.asarray(f, dtype=float), nan=-1.0),
                      kind='stable')


class _AbaMapa:
    """O desenho do alimentador, e a navegação sobre ele."""

    # ── O mapa ───────────────────────────────────────────────────────────────

    def _construir_mapa(self):
        """Monta a figura do mapa e desenha o alimentador pela primeira vez.

        Refaz a figura inteira, e não só os dados: trocar de caso muda o número
        de trechos, a extensão e a projeção, e reaproveitar os artistas antigos
        deixa resíduo do caso anterior na tela.
        """
        _canvas_tk, _figura, LineCollection, _barra, _cm = _mpl()
        import numpy as np
        for w in self.quadro_mapa.winfo_children():
            w.destroy()

        self.geo = _Geometria(self.resultado)
        # Estado de navegação zerado junto com a geometria. Ele é feito de
        # ÍNDICES no vetor de barras, e cada caso tem o seu: a barra 5.000 do
        # alimentador anterior não existe num caso com 4.525, e o painel abria
        # com IndexError antes de desenhar nada.
        #
        # Não era possível antes de as duas janelas virarem uma: cada caso
        # nascia num processo próprio e morria com ele. Agora a mesma janela
        # simula um caso atrás do outro, e o que sobra de um contamina o
        # seguinte.
        self._selecionada = None
        self._foco = None
        self._trilha = []
        self._graficos = []
        # A moldura, a projeção e a navegação são as mesmas do panorama da
        # base, e moram em `interface.navegacao`. O que fica aqui é o que ESTE
        # mapa desenha por dentro — barras, cabos, marcadores.
        from bdgdcase.interface.navegacao import _Vista
        self.vista = _Vista(
            self.quadro_mapa, self.tk, self.root, self.fila, self.v_fundo,
            caixa_util=self._caixa_util,
            ao_clicar=lambda x, y, _ctrl: self._selecionar(x, y),
            ao_mover=lambda lon, lat: self.v_posicao.set('%.6f, %.6f'
                                                         % (lat, lon)))
        fig, ax = self.vista.fig, self.vista.ax

        # Uma coleção por camada. Poderia ser uma só com cores por segmento, mas
        # aí ligar e desligar camada exigiria remontar o array a cada clique;
        # assim é `set_visible`, que é imediato mesmo com dezesseis mil trechos.
        self._colecoes = {}
        for camada, largura, zordem in (('mt', 1.9, 2), ('bt', 1.0, 1),
                                        ('chaves', 2.4, 3)):
            pos = self.geo.indice_por_tipo.get(camada)
            segs = (self.geo.segmentos[pos] if pos is not None and len(pos)
                    else np.zeros((0, 2, 2)))
            # Cor sólida, sem mapa de cores: os cabos identificam o nível de
            # tensão e não medem nada. A paleta e a escala de corrente que
            # ficavam aqui serviam ao modo "carga nos trechos", que saiu.
            col = LineCollection(segs, linewidths=largura, zorder=zordem)
            ax.add_collection(col)
            self._colecoes[camada] = col

        # O vão do regulador é tronco, e não sai de `segmentos`: o regulador é
        # um `Transformer`, e o solver não o reporta como linha. Entra na mesma
        # caixa de seleção do marcador do regulador — quem desliga um, desliga
        # o outro, porque são a mesma coisa vista de duas maneiras.
        col = LineCollection(self.geo.vaos_de_regulador(), linewidths=2.4,
                             zorder=3)
        ax.add_collection(col)
        self._colecoes['reguladores'] = col

        # O realce do circuito BT: vazio até alguém clicar num transformador.
        #
        # É um halo por baixo dos cabos, e não mais uma cor por cima deles.
        # Cor por cima disputaria com a paleta das camadas — que agora o
        # usuário escolhe, e pode escolher justamente a do realce; o halo
        # destaca o traçado seja qual for a cor que o cabo tenha.
        self._realce = LineCollection(np.zeros((0, 2, 2)), linewidths=5.5,
                                      colors='#ffffff', alpha=0.85, zorder=0.5,
                                      capstyle='round')
        ax.add_collection(self._realce)

        # Barra sem tensão vira NaN, e NaN vira cinza. Escondê-la seria pior:
        # trecho morto no cadastro é achado, não sujeira — no alimentador de
        # exemplo são 29 barras num fragmento a 19 km, que nunca energiza.
        # Três cores, e não um gradiente: a pergunta que o mapa responde é
        # "esta barra está dentro da norma?", que tem três respostas. Um
        # degradê convida a ler diferença onde a norma não vê nenhuma — 1,00 e
        # 1,04 pu são a mesma coisa para ela — e esconde o degrau que importa,
        # que é a passagem de uma faixa para a outra.
        from matplotlib.colors import ListedColormap
        paleta = ListedColormap(CORES_FAIXA).with_extremes(
            bad=CINZA_SEM_TENSAO)
        # Um artista por nível, e não um só com tudo dentro: é o que permite
        # apagar as barras de média e enxergar a baixa por baixo delas, que num
        # alimentador urbano ficam praticamente sobrepostas.
        faixa0 = self.geo.faixa(0)
        self._pontos = {}
        for camada, nivel in CAMADAS_BARRA.items():
            m = self.geo.barras_do_nivel(nivel)
            f = faixa0[m]
            ordem = ordem_por_gravidade(f)
            self._pontos[camada] = ax.scatter(
                self.geo.xy[m, 0][ordem], self.geo.xy[m, 1][ordem],
                c=f[ordem], s=TAMANHO_BARRA[nivel], cmap=paleta,
                vmin=-0.5, vmax=2.5, linewidths=0,
                zorder=2 if nivel == 'bt' else 2.1)
        # Marcadores, um por camada. Formas distintas em vez de só cores: sobre
        # satélite, cor sozinha some.
        g = self.geo
        self._marcadores = {
            'trafos': ax.plot(g.trafo_xy[:, 0], g.trafo_xy[:, 1], 's',
                              ms=7, mfc=self.cores['trafos'], mec='#2b2b2b',
                              mew=0.9, ls='none', zorder=6)[0],
            'chaves': ax.plot(g.chave_xy[:, 0], g.chave_xy[:, 1], 'X',
                              ms=8, mfc=self.cores['chaves'], mec='#2b2b2b',
                              mew=0.8, ls='none', zorder=6)[0],
            'gd': ax.plot(g.gd_xy[:, 0], g.gd_xy[:, 1], '^',
                          ms=9, mfc=self.cores['gd'], mec='#2b2b2b',
                          mew=0.9, ls='none', zorder=7)[0],
            'capacitores': ax.plot(g.cap_xy[:, 0], g.cap_xy[:, 1], 'D',
                                   ms=8, mfc=self.cores['capacitores'],
                                   mec='#2b2b2b', mew=0.9, ls='none',
                                   zorder=6.5)[0],
            'reguladores': ax.plot(g.reg_xy[:, 0], g.reg_xy[:, 1], 'p',
                                   ms=11, mfc=self.cores['reguladores'],
                                   mec='#2b2b2b', mew=0.9, ls='none',
                                   zorder=6.6)[0],
        }
        self._marca, = ax.plot([], [], 'o', ms=15, mfc='none', mec='#1060d0',
                               mew=2.2, zorder=8)
        eixo_cor = ax.inset_axes((0.018, 0.07, 0.014, 0.30))
        # A barra de cor serve às duas camadas: mesma paleta, mesma norma.
        self._barra_cor = fig.colorbar(self._pontos['barras_mt'],
                                       cax=eixo_cor)
        self._barra_cor.outline.set_edgecolor('#00000055')
        eixo_cor.tick_params(labelsize=8, pad=2)
        self._rotular_faixas()
        # Rótulo em cima da barra, na horizontal: ao lado, num eixo de 1,4% da
        # largura, ele encavalava os próprios números.
        self._rotulo_cor = ax.text(0.018, 0.395, '', transform=ax.transAxes,
                                   ha='left', va='bottom', fontsize=8,
                                   color='#f4f6f8',
                                   bbox={'facecolor': '#00000055', 'pad': 2.0,
                                         'edgecolor': 'none'})

        # Os três apelidos ficam: as outras seiscentas linhas desta aba —
        # inspetor, legenda, repintura — falam com o mapa por eles.
        self._fig, self._ax = fig, ax
        self._canvas = self.vista.canvas
        self._montar_legenda(self.vista.widget)
        # O enquadramento depende do tamanho da moldura em pixels, e a moldura
        # só tem tamanho depois que o Tk desenha. Daí a espera de um ciclo.
        self.root.after(60, self._reenquadrar)

        r = self.resultado
        self._aplicar_camadas()      # honra as caixas já ao montar
        self._montar_resumo()
        self._montar_ajustes()
        self._ir(int(round(r['hora_p_max'] * 60 / 15)))   # abre no pico
        self._trocar_fundo()


    # ── A aba dos ajustes ────────────────────────────────────────────────────

    #: Cor de cada efeito, do mais grave ao menos. `nao_corrigido` fica em
    #: vermelho de propósito: é o único em que o resultado depende de um dado
    #: que continua errado.
    CORES_EFEITO = {
        'simulacao': 'atencao',
        'nao_corrigido': 'ruim',
        'desenho': 'nota',
        'cadastro': 'nota',
    }


    def _reenquadrar(self):
        """Volta ao enquadramento inicial, preenchendo a moldura atual."""
        if self.geo is not None:
            self.vista.reenquadrar()


    def _caixa_util(self):
        """Retângulo (oeste, sul, leste, norte) das barras energizadas.

        Enquadrar o cadastro todo faria um fragmento desconectado a quilômetros
        de distância espremer o alimentador de verdade num canto. As barras
        distantes continuam desenhadas; é só a vista que as ignora, e a
        navegação chega nelas.
        """
        import numpy as np

        v = self.geo.v_barra
        viva = ~np.isnan(v).all(axis=0)
        xy = self.geo.xy[viva] if viva.any() else self.geo.xy
        if not len(xy):
            return None
        folga = 0.05
        o, l = float(xy[:, 0].min()), float(xy[:, 0].max())
        s_, n = float(xy[:, 1].min()), float(xy[:, 1].max())
        mx = max((l - o) * folga, 2e-4)
        my = max((n - s_) * folga, 2e-4)
        return o - mx, s_ - my, l + mx, n + my

    def _trocar_fundo(self):
        """Troca o mapa base. Ligado aos radiobuttons do rodapé da janela.

        O invólucro existe pela guarda: os radiobuttons vivem fora das abas e
        podem ser clicados antes de haver caso nenhum, quando a vista deste
        mapa ainda não foi montada.
        """
        if self.geo is not None:
            self.vista.trocar_fundo()


    # ── Legenda ──────────────────────────────────────────────────────────────

    def _montar_legenda(self, sobre):
        """Painel de camadas sobreposto ao canto superior esquerdo do mapa.

        Fica sobre o mapa, e não numa barra distante, porque ligar uma camada e
        ver o que muda são a mesma ação: com o controle na outra ponta da
        janela, o olho tem de viajar a cada clique.

        É Tk sobre o canvas do matplotlib, e não artistas dentro do eixo: caixa
        de marcar e escolha de cor são widgets, e desenhá-los à mão dentro da
        figura seria reimplementar o que o Tk já faz.
        """
        tk = self.tk

        moldura = tk.Frame(sobre, background=TEMA['painel'],
                           highlightbackground=TEMA['borda'],
                           highlightthickness=1)
        moldura.place(x=12, y=12)
        self._legenda = moldura

        tk.Label(moldura, text='CAMADAS', background=TEMA['painel'],
                 foreground=TEMA['rotulo'],
                 font=(self.fonte_ui, 8, 'bold')).grid(
                     row=0, column=0, columnspan=2, sticky='w',
                     padx=10, pady=(8, 4))

        self._amostras = {}
        # A quantidade vai ao lado do símbolo, e não num painel à parte: é
        # olhando a legenda que se pergunta "quanto disso tem aqui?". Cabo em
        # quilômetros, o resto em unidades — um alimentador de 3 km e um de
        # 127 km se parecem no mapa depois do enquadramento, e a diferença só
        # aparece se estiver escrita.
        self._quantidades = {}
        for i, (chave, rotulo) in enumerate(CAMADAS_ROTULO.items(), start=1):
            amostra = tk.Canvas(moldura, width=34, height=16,
                                background=TEMA['painel'], highlightthickness=0,
                                cursor='hand2' if chave in self.cores else 'arrow')
            amostra.grid(row=i, column=0, padx=(10, 6), pady=1)
            self._amostras[chave] = amostra
            self._desenhar_amostra(chave)
            if chave in self.cores:
                amostra.bind('<Button-1>',
                             lambda _e, c=chave: self._escolher_cor(c))

            caixa = tk.Checkbutton(
                moldura, text=rotulo, variable=self.v_camada[chave],
                command=self._aplicar_camadas, background=TEMA['painel'],
                foreground=TEMA['titulo'], activebackground=TEMA['painel'],
                highlightthickness=0, anchor='w', padx=0,
                font=(self.fonte_ui, 9))
            caixa.grid(row=i, column=1, sticky='w', padx=(0, 6))

            valor = tk.Label(moldura, text='', background=TEMA['painel'],
                             foreground=TEMA['valor'], anchor='e',
                             font=(self.fonte_ui, 9))
            valor.grid(row=i, column=2, sticky='e', padx=(0, 12))
            self._quantidades[chave] = valor

        # Barra sem nenhuma fase energizada. Não é camada — não se liga nem se
        # desliga, porque é um estado das barras que já estão desenhadas. Mas
        # precisa de linha na legenda: sem nome, o cinza no mapa se lê como
        # falha do desenho, e não como o que é, que é falta de tensão.
        linha_cinza = len(CAMADAS_ROTULO) + 1
        amostra = tk.Canvas(moldura, width=34, height=16,
                            background=TEMA['painel'], highlightthickness=0)
        amostra.create_oval(13, 4, 21, 12, fill=CINZA_SEM_TENSAO, outline='')
        amostra.grid(row=linha_cinza, column=0, padx=(10, 6), pady=(4, 1))
        tk.Label(moldura, text='sem tensão', background=TEMA['painel'],
                 foreground=TEMA['titulo'], anchor='w',
                 font=(self.fonte_ui, 9)).grid(
                     row=linha_cinza, column=1, sticky='w', padx=(0, 6),
                     pady=(4, 1))
        rotulo_cinza = tk.Label(moldura, text='', background=TEMA['painel'],
                                foreground=TEMA['valor'], anchor='e',
                                font=(self.fonte_ui, 9))
        rotulo_cinza.grid(row=linha_cinza, column=2, sticky='e',
                          padx=(0, 12), pady=(4, 1))
        self._quantidades['barras_sem_tensao'] = rotulo_cinza

        tk.Label(moldura, text='clique no símbolo para trocar a cor',
                 background=TEMA['painel'], foreground=TEMA['nota'],
                 font=(self.fonte_ui, 7)).grid(
                     row=linha_cinza + 1, column=0, columnspan=3,
                     sticky='w', padx=10, pady=(3, 7))
        self._atualizar_quantidades()

    def _atualizar_quantidades(self):
        """Põe na legenda quanto há de cada camada, no passo atual.

        Só a contagem de barras sem tensão muda com a hora; o resto é do caso e
        não do instante. Recalcular tudo custa menos que decidir o que
        recalcular.
        """
        if self.geo is None or not getattr(self, '_quantidades', None):
            return
        resumo = self.geo.resumo_das_camadas(self.passo)
        for chave, rotulo in self._quantidades.items():
            rotulo.config(text=resumo.get(chave, ''))

    def _desenhar_amostra(self, chave):
        """Redesenha o símbolo da camada como ele aparece no mapa."""
        c = self._amostras.get(chave)
        if c is None:
            return
        c.delete('all')
        forma, tam = FORMA_CAMADA[chave]
        cor = self.cores.get(chave, '#888888')
        mx, my = 17, 8

        if forma == 'linha':
            c.create_line(3, my, 31, my, fill=cor, width=tam)
        elif forma == 'quadrado':
            c.create_rectangle(mx - tam // 2, my - tam // 2,
                               mx + tam // 2, my + tam // 2,
                               fill=cor, outline='#3a2a00')
        elif forma == 'triangulo':
            c.create_polygon(mx, my - tam // 2 - 1,
                             mx - tam // 2 - 1, my + tam // 2,
                             mx + tam // 2 + 1, my + tam // 2,
                             fill=cor, outline='#00293a')
        elif forma == 'losango':
            c.create_polygon(mx, my - tam // 2, mx + tam // 2, my,
                             mx, my + tam // 2, mx - tam // 2, my,
                             fill=cor, outline='#2b2b2b')
        elif forma == 'pentagono':
            import math as _m
            pontos = []
            for k in range(5):
                a = -_m.pi / 2 + k * 2 * _m.pi / 5
                pontos += [mx + (tam / 2.0) * _m.cos(a),
                           my + (tam / 2.0) * _m.sin(a)]
            c.create_polygon(*pontos, fill=cor, outline='#2b2b2b')
        elif forma == 'xis':
            for dx, dy in ((-1, -1), (-1, 1)):
                c.create_line(mx + dx * tam // 2, my + dy * tam // 2,
                              mx - dx * tam // 2, my - dy * tam // 2,
                              fill=cor, width=2.4)
        else:
            # As barras não têm cor própria: são pintadas pela faixa do
            # PRODIST, e a amostra mostra as três — não um degradê, que
            # sugeriria uma continuidade que a norma não tem. O tamanho do
            # ponto é o do mapa, e é o que separa média de baixa na legenda.
            for k, cor_faixa in enumerate(CORES_FAIXA):
                cx = 8 + k * 9
                c.create_oval(cx - tam, my - tam, cx + tam, my + tam,
                              fill=cor_faixa, outline='')

    def _escolher_cor(self, chave):
        """Abre o seletor e aplica a cor à camada."""
        from tkinter import colorchooser
        _rgb, hexa = colorchooser.askcolor(
            color=self.cores.get(chave), parent=self.root,
            title='Cor de %s' % CAMADAS_ROTULO[chave])
        if not hexa:
            return
        self.cores[chave] = hexa
        self._desenhar_amostra(chave)
        self._redesenhar()

    def _rotular_faixas(self):
        """A barra de cor no modo tensão é uma legenda, não uma escala.

        Ela mostra três blocos com o nome de cada faixa. Números de pu ali
        seriam a régua errada: o corte muda entre média e baixa, e uma escala
        só não pode mostrar as duas.
        """
        self._barra_cor.set_ticks([0, 1, 2])
        self._barra_cor.set_ticklabels(list(FAIXAS))
        self._barra_cor.ax.tick_params(labelsize=7, pad=2)

    def _aplicar_camadas(self):
        """Liga e desliga camadas. Só visibilidade — nada é remontado."""
        if self.geo is None:
            return
        for camada, col in self._colecoes.items():
            col.set_visible(self.v_camada[camada].get())
        for camada, art in self._marcadores.items():
            art.set_visible(self.v_camada[camada].get())
        for camada, art in self._pontos.items():
            art.set_visible(self.v_camada[camada].get())
        self._canvas.draw_idle()

    def _realcar(self, nome_barra):
        """Realça o circuito BT do transformador cujo secundário é esta barra.

        A pergunta que segue um transformador no mapa é o que ele atende. O
        caminhamento anda só por trechos de baixa, então para sozinho no
        primário do transformador seguinte — o realce é exatamente a zona
        daquele equipamento.
        """
        import numpy as np
        trafos = self.geo.trafos_da_barra(nome_barra)
        if not trafos:
            self._realce.set_segments(np.zeros((0, 2, 2)))
            return None
        pontos, trechos = self.geo.circuito_bt(trafos[0]['nome'])
        self._realce.set_segments(self.geo.segmentos[trechos] if trechos
                                  else np.zeros((0, 2, 2)))
        return trafos[0], pontos, trechos

    def _redesenhar(self):
        """Repinta o alimentador com as cores do passo atual.

        Só as cores e os dados dos artistas já existentes — refazer a figura a
        cada passo faria a animação piscar e não fecharia o tempo de um quadro.
        """
        if self.geo is None:
            return
        faixa = self.geo.faixa(self.passo)
        # A ordem é refeita a cada passo porque a gravidade muda com a hora: a
        # barra que estava por baixo às 3h é a que precisa aparecer às 18h30.
        for camada, nivel in CAMADAS_BARRA.items():
            m = self.geo.barras_do_nivel(nivel)
            f = faixa[m]
            ordem = ordem_por_gravidade(f)
            self._pontos[camada].set_offsets(self.geo.xy[m][ordem])
            self._pontos[camada].set_array(f[ordem])
        # Os cabos identificam o NÍVEL de tensão, e não medem nada: cada um com
        # a sua cor, que a legenda mostra e deixa trocar. Colori-los pela
        # corrente competiria com as barras, que é onde a medida está.
        for camada, col in self._colecoes.items():
            col.set_array(None)
            col.set_color(self.cores[camada])
            col.set_alpha(1.0 if camada != 'bt' else 0.9)

        for camada, art in self._marcadores.items():
            art.set_markerfacecolor(self.cores[camada])

        for camada, nivel in CAMADAS_BARRA.items():
            n = int(self.geo.barras_do_nivel(nivel).sum())
            self._pontos[camada].set_sizes([TAMANHO_BARRA[nivel]] * n)
        self._barra_cor.update_normal(self._pontos['barras_mt'])
        self._rotular_faixas()
        self._atualizar_quantidades()
        self._rotulo_cor.set_text('tensão na barra — PRODIST M8')
        self._canvas.draw_idle()


    def _arrastar(self, valor):
        """A régua do tempo mudou de posição: vai para aquele passo."""
        if self.geo is not None and int(float(valor)) != self.passo:
            self._ir(float(valor))

    def _animar(self):
        """Liga e desliga a animação do dia."""
        self.animando = not self.animando
        self.btn_play.config(text='❚❚' if self.animando else '▶')
        if self.animando:
            self._quadro()

    def _quadro(self):
        """Um quadro da animação: avança um passo e agenda o próximo.

        Agendado pelo laço do Tk, e não por um laço próprio com pausa: laço
        próprio bloquearia a janela, e a animação passaria a impedir de clicar
        em qualquer coisa.
        """
        if not self.animando or self.geo is None:
            return
        self._ir((self.passo + 1) % self.resultado['passos'])
        self.root.after(120, self._quadro)


    def _pintar(self):
        """Desenha os blocos do inspetor com tipografia, não como texto corrido.

        O rótulo vai à esquerda em cinza, o valor à direita em fonte de largura
        fixa — é o alinhamento da coluna de valores que faz a lista ser lida
        como tabela. Cor só no valor que diagnostica.
        """
        if self.geo is None or self._selecionada is None:
            return
        t = self.txt
        t.config(state='normal')
        t.delete('1.0', 'end')
        # Os gráficos embutidos são widgets dentro do Text; apagar o texto não
        # os destrói, e sem isto eles se acumulariam a cada repintura.
        for w in self._graficos:
            w.destroy()
        self._graficos = []

        if self._foco is not None:
            self._pintar_trilha()
            blocos = self.geo.blocos_de(self._foco, self.passo)
        else:
            blocos = self.geo.blocos(self._selecionada, self.passo)

        for i, bloco in enumerate(blocos):
            tipo = bloco[0]
            if tipo == 'secao':
                titulo, conta = bloco[1], bloco[2]
                t.insert('end', titulo.upper(), 'secao')
                if conta:
                    t.insert('end', '   %s' % conta, 'conta')
                t.insert('end', chr(10))
            elif tipo in ('par', 'link'):
                rotulo, valor, estilo = bloco[1], bloco[2], bloco[3]
                enche = max(1, COL_ROTULO - len(rotulo))
                if tipo == 'link':
                    marca = 'alvo%d' % i
                    t.insert('end', rotulo + ' ' * enche, ('link', marca))
                    t.tag_bind(marca, '<Button-1>',
                               lambda _e, a=bloco[4]: self._abrir(a))
                    t.tag_bind(marca, '<Enter>',
                               lambda _e: t.config(cursor='hand2'))
                    t.tag_bind(marca, '<Leave>',
                               lambda _e: t.config(cursor=''))
                else:
                    t.insert('end', rotulo + ' ' * enche, 'rotulo')
                t.insert('end', valor + chr(10), estilo or 'valor')
            elif tipo == 'nota':
                t.insert('end', bloco[1] + chr(10), 'nota')
            elif tipo == 'grafico':
                self._inserir_grafico(bloco)
            else:
                t.insert('end', chr(10))
        t.config(state='disabled')

    def _pintar_trilha(self):
        """A migalha de pão, com o caminho de volta clicável."""
        t = self.txt
        t.insert('end', '\u25c0 voltar', ('voltar', 'voltar'))
        t.tag_bind('voltar', '<Button-1>', self._voltar)
        caminho = [r for _, r in self._trilha] + [self._rotulo_do_foco()]
        t.insert('end', '   ' + '  \u25b8  '.join(caminho) + chr(10), 'trilha')


