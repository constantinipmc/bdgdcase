# -*- coding: utf-8 -*-
"""A aba que mostra o alimentador inteiro, e não um ponto dele.

O mapa responde "o que há nesta barra". Esta aba responde "como está a rede":
o inventário — barras, extensão, transformadores, unidades por classe, GD
sobre a capacidade dos trafos — e o dia em números e em três painéis: potência
no ponto de entrega, perdas e faixa de tensão.

É a primeira coisa que se olha depois de simular, e por isso existe separada.
Procurar o pior ponto do dia clicando barra a barra num alimentador de
dezesseis mil é procurar uma palavra num livro sem índice.
"""
from __future__ import annotations

import os

from bdgdcase.interface.base import _mpl
from bdgdcase.interface.geometria import V_LIMITES, _hhmm


class _AbaResumo:
    """O alimentador inteiro, num texto e num gráfico."""

    def _montar_resumo(self):
        """A aba que responde \"o que é este alimentador?\" antes de qualquer mapa.
        """
        FigureCanvasTkAgg, Figure, _LC, _T, _P = _mpl()
        for w in self.quadro_resumo.winfo_children():
            w.destroy()
        self.quadro_resumo.columnconfigure(1, weight=1)
        self.quadro_resumo.rowconfigure(0, weight=1)

        texto = self.tk.Text(self.quadro_resumo, width=52, wrap='none',
                             state='disabled', font=('Consolas', 10))
        texto.grid(row=0, column=0, sticky='ns', padx=(0, 8))
        texto.config(state='normal')
        texto.insert('1.0', self._texto_resumo())
        texto.config(state='disabled')

        fig = Figure(figsize=(7, 6), dpi=100, layout='constrained')
        self._desenhar_curvas(fig)
        canvas = FigureCanvasTkAgg(fig, master=self.quadro_resumo)
        canvas.get_tk_widget().grid(row=0, column=1, sticky='nsew')
        canvas.draw()
        self._canvas_resumo = canvas

    def _texto_resumo(self):
        """Monta o texto do resumo: o alimentador inteiro em uma tela.

        A ordem é a das perguntas que se fazem depois de simular: que rede é
        esta, quanto consome, quanto gera, e como foi o dia — pico, energia,
        perdas, fator de carga. É índice, não relatório — cada linha existe
        para dizer onde olhar em seguida.
        """
        from collections import Counter
        r, inv = self.resultado, (self.resultado.get('inventario') or {})
        cargas, trafos, gd = (inv.get('cargas', []), inv.get('trafos', []),
                              inv.get('gd', []))
        kw = sum(c['kw'] for c in cargas)
        kva_tr = sum(t['kva'] for t in trafos)
        kva_gd = sum(g['kva'] for g in gd)
        tensoes = sorted({round(t, 4) for t in inv.get('tensoes_base', ())},
                         reverse=True)

        L = ['ALIMENTADOR %s' % r['alimentador'],
             '=' * 46, '',
             'Rede',
             '   barras                 %8d' % len(r['barras']),
             '   trechos                %8d' % len(r['linhas']),
             '   extensão               %8.2f km' % inv.get('km_linhas', 0),
             '   transformadores        %8d  (%.0f kVA)' % (len(trafos), kva_tr),
             '   capacitores            %8d' % inv.get('n_capacitores', 0),
             '   reguladores            %8d' % inv.get('n_reguladores', 0),
             '   tensões de base        %s kV'
             % ', '.join('%.4g' % t for t in tensoes[:4]),
             '',
             'Consumo',
             '   unidades consumidoras  %8d' % len(cargas),
             '   demanda nominal        %8.1f kW' % kw]
        for classe, n in Counter(c['classe'] for c in cargas).most_common():
            p = sum(c['kw'] for c in cargas if c['classe'] == classe)
            L.append('     %-4s %6d un.     %8.1f kW (%.0f%%)'
                     % (classe, n, p, 100 * p / kw if kw else 0))

        L += ['', 'Geração distribuída',
              '   unidades               %8d' % len(gd),
              '   potência instalada     %8.1f kVA' % kva_gd]
        if kva_tr:
            L.append('   sobre a capacidade dos trafos  %.1f%%'
                     % (100 * kva_gd / kva_tr))

        L += ['', 'Dia simulado (%s)' % os.path.basename(str(r['pasta'])),
              '   pico                   %8.1f kW às %s'
              % (r['p_max_kw'], _hhmm(r['hora_p_max'])),
              '   energia importada      %8.1f kWh' % r['energia_importada_kwh'],
              '   energia exportada      %8.1f kWh' % r['energia_exportada_kwh'],
              '   perdas                 %8.1f kWh (%.2f%%)'
              % (r['perdas_kwh'], r['perdas_pct_dia']),
              '   fator de carga         %8.2f'
              % (r['energia_importada_kwh'] / (24 * r['p_max_kw'])
                 if r['p_max_kw'] > 0 else float('nan')),
              '   tensão no dia          %8.4f a %.4f pu'
              % (r['v_min_dia_pu'], r['v_max_dia_pu'])]
        if r['passos_nao_convergidos']:
            L += ['',
                  'ATENÇÃO: %d passo(s) não convergiram e foram repetidos do'
                  % len(r['passos_nao_convergidos']),
                  'anterior. Esses pontos da curva são interpolação.']
        return '\n'.join(L)

    def _desenhar_curvas(self, fig):
        """Curva de carga, perdas e faixa de tensão — o dia em três painéis."""
        r = self.resultado
        h = r['horas']
        eixos = fig.subplots(3, 1, sharex=True,
                             gridspec_kw={'height_ratios': [3, 2, 2]})

        eixos[0].plot(h, r['p_kw'], lw=1.6, color='#1f4e9c', label='ativa')
        eixos[0].plot(h, r['q_kvar'], lw=1.0, color='#9c7a1f', ls='--',
                      label='reativa')
        eixos[0].axhline(0, lw=0.8, color='0.6')
        eixos[0].plot([r['hora_p_max']], [r['p_max_kw']], 'v', color='#c02020',
                      ms=7)
        eixos[0].annotate(' pico %.1f kW  %s' % (r['p_max_kw'],
                                                 _hhmm(r['hora_p_max'])),
                          (r['hora_p_max'], r['p_max_kw']), fontsize=8,
                          va='bottom')
        eixos[0].set_ylabel('ponto de entrega\n[kW / kVAr]', fontsize=9)
        eixos[0].legend(fontsize=8, loc='upper left')
        eixos[0].set_title('%s — dia simulado' % r['alimentador'], fontsize=11)

        eixos[1].fill_between(h, r['perdas_kw'], color='#c06020', alpha=0.35)
        eixos[1].plot(h, r['perdas_kw'], lw=1.2, color='#a04010')
        eixos[1].set_ylabel('perdas [kW]', fontsize=9)

        eixos[2].fill_between(h, r['v_min_pu'], r['v_max_pu'], alpha=0.25,
                              color='#1f7a3d')
        eixos[2].plot(h, r['v_med_pu'], lw=1.2, color='#12602d')
        eixos[2].axhline(V_LIMITES[0], lw=0.9, ls=':', color='#c02020')
        eixos[2].set_ylabel('tensão [pu]', fontsize=9)
        eixos[2].set_xlabel('hora do dia')
        eixos[2].set_xlim(0, 24)
        eixos[2].set_xticks(range(0, 25, 3))
        for ax in eixos:
            ax.grid(alpha=0.25)
            ax.tick_params(labelsize=8)
