# -*- coding: utf-8 -*-
"""O painel do ponto, e a navegação por ele.

Clicar numa barra abre o que está pendurado nela: as unidades consumidoras com
nome e consumo, o transformador com a ficha, o regulador com o tape que tomou a
cada quarto de hora, a geração distribuída com a curva do dia.

**Cada resposta vem com a procedência.** Não "12,4 kW", mas "12,4 kW = sete UCs,
três delas com a potência corrigida pela regra do fator 720" — porque quem
discorda do número precisa de um fio para puxar, e refazer a extração só para
conferir custa horas.

A navegação guarda de onde se veio: entrar num transformador a partir de uma
barra e voltar tem de devolver aquela barra, não o começo.
"""
from __future__ import annotations

from bdgdcase.interface.base import TEMA, _mpl
from bdgdcase.interface.geometria import _hhmm
from bdgdcase.interface.mapa import CAMADAS_BARRA


class _Inspetor:
    """O painel do ponto, e a navegação entre pontos."""

    # ── Seleção ──────────────────────────────────────────────────────────────

    def _selecionar(self, x, y):
        """Escolhe a barra mais próxima do clique, entre as camadas visíveis.

        Só entre as visíveis: clicar num ponto e receber a ficha de algo que
        não está desenhado ali é a forma mais rápida de fazer alguém desconfiar
        do mapa.

        Cliques repetidos no mesmo lugar ciclam entre as barras empilhadas —
        num poste com transformador, MT e BT ocupam o mesmo ponto.
        """
        if self.geo is None or x is None:
            return
        visiveis = [nivel for camada, nivel in CAMADAS_BARRA.items()
                    if self.v_camada[camada].get()]
        j = self.geo.barra_mais_proxima(x, y, self._selecionada,
                                        niveis=visiveis or None)
        if j is None:
            return
        self._selecionada = j
        self._marca.set_data([self.geo.xy[j][0]], [self.geo.xy[j][1]])
        self._realcar(self.geo.barras[j])
        self._canvas.draw_idle()
        self._mostrar(j)

    def _tags_do_inspetor(self):
        """Os estilos do painel. Uma tag por papel, e não por caso."""
        t = self.txt
        t.tag_configure('secao', font=(self.fonte_ui, 9, 'bold'),
                        foreground=TEMA['rotulo'], spacing1=12, spacing3=4)
        t.tag_configure('conta', font=(self.fonte_ui, 9),
                        foreground=TEMA['nota'], justify='right')
        t.tag_configure('rotulo', font=(self.fonte_ui, 10),
                        foreground=TEMA['rotulo'])
        t.tag_configure('valor', font=(self.fonte_mono, 10),
                        foreground=TEMA['valor'])
        t.tag_configure('nota', font=(self.fonte_ui, 9, 'italic'),
                        foreground=TEMA['nota'], spacing3=2, lmargin2=12)
        # A continuação de uma linha quebrada recua até a coluna dos valores,
        # senão ela volta à margem e se confunde com um rótulo novo.
        for chave in ('valor', 'bom', 'atencao', 'ruim'):
            t.tag_configure(chave, lmargin2=150)
        for chave in ('bom', 'atencao', 'ruim'):
            t.tag_configure(chave, font=(self.fonte_mono, 10, 'bold'),
                            foreground=TEMA[chave])
        # O link se anuncia pela cor, e não por sublinhado: os nomes de
        # elemento são cheios de `_`, e o traço do sublinhado passa por cima
        # deles — `ucbt_res_1835587_0` sublinhado lê-se com espaços.
        t.tag_configure('link', font=(self.fonte_ui, 10),
                        foreground=TEMA['destaque'])
        t.tag_configure('trilha', font=(self.fonte_ui, 9),
                        foreground=TEMA['nota'], spacing3=8)
        t.tag_configure('voltar', font=(self.fonte_ui, 9, 'underline'),
                        foreground=TEMA['destaque'], spacing3=8)

    # ── Navegação em profundidade ────────────────────────────────────────────

    def _abrir(self, alvo):
        """Entra na ficha de um elemento, guardando de onde se veio."""
        if self.geo is None:
            return
        if alvo[0] == 'barra':
            # Voltar ao mapa: o alvo é uma barra, e quem manda no mapa é a
            # seleção, não o foco.
            j = self.geo.indice.get(alvo[1])
            if j is not None:
                self._foco, self._trilha = None, []
                self._selecionar(*self.geo.xy[j])
            return
        self._trilha.append((self._foco, self._rotulo_do_foco()))
        self._foco = alvo
        self._pintar()

    def _voltar(self, _evento=None):
        """Sobe um nível na navegação, devolvendo de onde se veio.

        A trilha existe para isto: entrar num transformador a partir de uma
        barra e voltar tem de devolver aquela barra, e não o começo.
        """
        if not self._trilha:
            self._foco = None
        else:
            self._foco, _ = self._trilha.pop()
        self._pintar()

    def _rotulo_do_foco(self):
        """O título do painel: 'Ponto', ou o tipo do elemento aberto."""
        if self._foco is None:
            return 'Ponto'
        rotulo = {'uc': 'Unidade', 'trafo': 'Transformador',
                  'gd': 'Geração', 'capacitor': 'Capacitor',
                  'regulador': 'Regulador'}.get(self._foco[0], '')
        return '%s %s' % (rotulo, self._foco[1])

    def _mostrar(self, j):
        """O painel do ponto. Zera o foco: clicar no mapa recomeça a navegação."""
        self._foco, self._trilha = None, []
        self._pintar()

    def _ir(self, passo):
        """Vai para um passo do dia, dando a volta nas pontas.

        A volta é o que faz a animação continuar do 95 para o 0 sem parar, e o
        que permite passar do começo para o fim do dia com um clique.
        """
        if self.geo is None:
            return
        n = self.resultado['passos']
        self.passo = max(0, min(n - 1, int(passo)))
        self.escala.set(self.passo)
        self.v_hora.set(_hhmm(self.resultado['horas'][self.passo]))
        self._redesenhar()
        self._resumir()
        if getattr(self, '_selecionada', None) is not None:
            self._pintar()

    def _resumir(self):
        """O que se precisa saber do instante sem clicar em nada.

        A contagem por faixa importa porque a cor do mapa é categórica: sem
        ela, não dá para distinguir "duas barras precárias" de "um bairro
        inteiro precário" só olhando o amarelo. E o carregamento máximo avisa
        quando o mapa de corrente parece todo claro porque a rede está folgada
        — e não porque quebrou."""
        import numpy as np
        r = self.resultado
        v = self.geo.tensao(self.passo)
        alta = self.geo.tensao_alta(self.passo)
        viva = ~np.isnan(v)
        _ok, precarias, criticas, mortas = self.geo.contar_faixas(self.passo)
        carga = self.geo.carregamento(self.passo)
        acima = int((carga > 100).sum()) if len(carga) else 0
        self.v_status.set(
            '%s às %s   ·   P = %.1f kW   ·   tensão %.4f–%.4f pu   ·   '
            '%d precária(s), %d crítica(s)   ·   '
            '%d trecho(s) acima da ampacidade   ·   %d sem tensão'
            % (r['alimentador'], _hhmm(r['horas'][self.passo]),
               r['p_kw'][self.passo], np.nanmin(v) if viva.any() else float('nan'),
               # O máximo vem da fase mais ALTA. Com o mínimo por barra, a
               # faixa exibida contradizia a série do dia da aba de resumo,
               # que sempre olhou todos os nós.
               np.nanmax(alta) if viva.any() else float('nan'),
               precarias, criticas, acima, mortas))

    def _inserir_grafico(self, bloco):
        """A série do dia como figura pequena, dentro do próprio painel.

        Dentro do Text, e não numa aba à parte, porque a curva é sobre o
        elemento aberto: mandar o olho para outro lugar da janela para ver o
        gráfico de quem está aqui desfaria o que a navegação acabou de juntar.
        """
        _canvas_agg, _figura, _lc, _barra, _paletas = _mpl()
        titulo, serie, nota = bloco[1], bloco[2] or [0.0], bloco[3]
        # A unidade vem do bloco: nem toda série é potência. Antes estava fixa
        # em kW aqui, e o gráfico do tape anunciava "-3.000 kW".
        unidade = bloco[4] if len(bloco) > 4 else ''

        quadro = self.tk.Frame(self.txt, background=TEMA['painel'])
        fig = _figura(figsize=(3.9, 1.45), dpi=100)
        fig.patch.set_facecolor(TEMA['painel'])
        ax = fig.add_axes([0.16, 0.26, 0.80, 0.60])
        horas = [k * 24.0 / max(1, len(serie)) for k in range(len(serie))]
        ax.plot(horas, serie, color=TEMA['destaque'], linewidth=1.4)
        ax.fill_between(horas, serie, color=TEMA['destaque'], alpha=0.13)

        # O instante que o resto da janela está mostrando, marcado: sem isso o
        # gráfico e o relógio contariam histórias separadas.
        k = min(self.passo, len(serie) - 1)
        ax.axvline(horas[k], color=TEMA['ruim'], linewidth=1.0, alpha=0.8)
        ax.plot([horas[k]], [serie[k]], 'o', ms=4, color=TEMA['ruim'])

        ax.set_title(('%s  ·  %.3f %s' % (titulo, serie[k], unidade)).strip(),
                     fontsize=8, color=TEMA['titulo'], loc='left', pad=4)
        ax.set_xlim(0, 24)
        ax.set_xticks([0, 6, 12, 18, 24])
        ax.tick_params(labelsize=7, colors=TEMA['rotulo'], length=2)
        for lado in ax.spines.values():
            lado.set_color(TEMA['borda'])
        ax.set_facecolor(TEMA['painel'])
        ax.grid(True, color=TEMA['borda'], linewidth=0.5, alpha=0.7)

        canvas = _canvas_agg(fig, master=quadro)
        canvas.draw()
        canvas.get_tk_widget().pack()
        self.txt.window_create('end', window=quadro)
        self.txt.insert('end', chr(10))
        self.txt.insert('end', nota + chr(10), 'nota')
        self._graficos.append(quadro)
