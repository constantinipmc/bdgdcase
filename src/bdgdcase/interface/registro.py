# -*- coding: utf-8 -*-
"""A aba que conta o que foi ajustado no cadastro para o caso convergir.

Um caso que converge não é um caso fiel: entre a BDGD crua e o `.dss` que roda
há dezenas de decisões — tensão corrigida, potência reinterpretada, curva
emprestada, trecho costurado. Cada uma delas é defensável, e nenhuma é óbvia
olhando só para o resultado.

Esta aba mostra todas, agrupadas por assunto e ordenadas por efeito: primeiro o
que muda o resultado da simulação, depois o que foi encontrado e **não**
corrigido, e por último o que muda só o desenho ou a ficha. Quem for defender
um número tirado deste caso precisa saber o que entrou nele.

A ordenação por efeito é deliberada. Numa lista cronológica, a correção que
mudou a tensão de oitenta mil transformadores apareceria no meio de vinte
avisos de coordenada ausente, com o mesmo peso visual.
"""
from __future__ import annotations

from bdgdcase.interface.base import TEMA


class _AbaAjustes:
    """Os ajustes do cadastro, agrupados e ordenados por efeito."""

    def _montar_ajustes(self):
        """Lê o `Ajustes.json` do caso e desenha a aba."""
        from bdgdcase import ajustes as _aj

        tk = self.tk
        for w in self.quadro_ajustes.winfo_children():
            w.destroy()
        self.quadro_ajustes.columnconfigure(0, weight=1)
        self.quadro_ajustes.rowconfigure(0, weight=1)

        txt = tk.Text(self.quadro_ajustes, wrap='word', state='disabled',
                      font=(self.fonte_ui, 10), background=TEMA['painel'],
                      foreground=TEMA['valor'], relief='flat', borderwidth=0,
                      highlightthickness=0, padx=18, pady=12,
                      spacing1=1, spacing3=2, cursor='arrow')
        rol = self.ttk.Scrollbar(self.quadro_ajustes, orient='vertical',
                                 command=txt.yview)
        txt.configure(yscrollcommand=rol.set)
        rol.grid(row=0, column=1, sticky='ns')
        txt.grid(row=0, column=0, sticky='nsew')
        self._tags_dos_ajustes(txt)

        pasta = (self.resultado or {}).get('pasta') or self.v_pasta.get()
        dados = _aj.carregar(pasta)
        txt.config(state='normal')

        if dados is None:
            txt.insert('end', 'Sem registro de ajustes neste caso\n', 'titulo')
            txt.insert('end',
                       'A pasta não tem `%s`. Ou o caso foi convertido por uma '
                       'versão do pacote anterior a este registro, ou os '
                       'arquivos foram copiados sem ele.\n\n'
                       % _aj.NOME_ARQUIVO, 'corpo')
            txt.insert('end',
                       'Isso NÃO quer dizer que nada foi ajustado — quer dizer '
                       'que não há como saber. Converta o alimentador de novo '
                       'para ter o registro.\n', 'nota')
            txt.config(state='disabled')
            return

        itens = dados.get('ajustes') or []
        self._cabecalho_ajustes(txt, dados, itens)
        for _chave, rotulo, descricao, grupo in _aj.por_categoria(dados):
            txt.insert('end', '\n%s\n' % rotulo.upper(), 'categoria')
            if descricao:
                txt.insert('end', '%s\n' % descricao, 'nota')
            for a in grupo:
                self._um_ajuste(txt, a)
        txt.config(state='disabled')

    def _cabecalho_ajustes(self, txt, dados, itens):
        """O resumo de cima: quantos, e quantos deles mudam o resultado."""
        from bdgdcase import ajustes as _aj
        from collections import Counter

        dist = (dados.get('distribuidora') or {}).get('nome') or '—'
        txt.insert('end', 'O que foi ajustado no cadastro\n', 'titulo')
        txt.insert('end',
                   'Alimentador %s · %s\n' % (dados.get('alimentador') or '—',
                                               dist), 'sub')
        txt.insert('end',
                   'A BDGD publicada não fecha um fluxo de potência como está. '
                   'Cada linha abaixo é uma decisão tomada entre o cadastro cru '
                   'e este caso, com quantos elementos atingiu e por quê.\n',
                   'corpo')

        if not itens:
            txt.insert('end',
                       '\nNenhum ajuste foi necessário neste alimentador — o '
                       'cadastro veio completo e coerente.\n', 'corpo')
            return

        conta = Counter(a.get('efeito') for a in itens)
        txt.insert('end', '\n')
        for efeito, (rotulo, _ordem) in sorted(
                _aj.EFEITOS.items(), key=lambda kv: kv[1][1]):
            n = conta.get(efeito, 0)
            if not n:
                continue
            txt.insert('end', '   %2d ' % n, self.CORES_EFEITO[efeito])
            txt.insert('end', '%s\n' % rotulo, 'corpo')

    def _um_ajuste(self, txt, a):
        """Uma entrada: título, quanto atingiu, o que foi feito e por quê."""
        from bdgdcase import ajustes as _aj

        efeito = a.get('efeito') or 'simulacao'
        rotulo = _aj.EFEITOS.get(efeito, ('', 9))[0]
        txt.insert('end', '\n▸ %s\n' % (a.get('titulo') or ''), 'item')

        quantos, unidade = a.get('quantos'), a.get('unidade') or ''
        linha = ('%s %s' % ('{:,}'.format(quantos).replace(',', '.'), unidade)
                 if quantos else '')
        txt.insert('end', '   ')
        if linha:
            txt.insert('end', linha, 'quanto')
            txt.insert('end', '  ·  ')
        txt.insert('end', rotulo + '\n', self.CORES_EFEITO.get(efeito, 'nota'))

        if a.get('detalhe'):
            txt.insert('end', '   %s\n' % a['detalhe'], 'corpo')
        if a.get('porque'):
            txt.insert('end', '   %s\n' % a['porque'], 'porque')
        if a.get('exemplos'):
            txt.insert('end', '   ex.: %s\n' % ' · '.join(a['exemplos']),
                       'exemplo')

    def _tags_dos_ajustes(self, t):
        """Os estilos da aba. Uma tag por papel, como no painel do ponto."""
        t.tag_configure('titulo', font=(self.fonte_ui, 14, 'bold'),
                        foreground=TEMA['valor'], spacing3=2)
        t.tag_configure('sub', font=(self.fonte_ui, 10),
                        foreground=TEMA['rotulo'], spacing3=8)
        t.tag_configure('categoria', font=(self.fonte_ui, 10, 'bold'),
                        foreground=TEMA['rotulo'], spacing1=16, spacing3=2)
        t.tag_configure('item', font=(self.fonte_ui, 11, 'bold'),
                        foreground=TEMA['valor'], spacing1=8, spacing3=1)
        t.tag_configure('quanto', font=(self.fonte_mono, 10, 'bold'),
                        foreground=TEMA['valor'])
        # O corpo e o porquê recuam: a linha que continua tem de ficar sob o
        # texto, e não voltar à margem onde o título mora.
        t.tag_configure('corpo', font=(self.fonte_ui, 10),
                        foreground=TEMA['valor'], lmargin1=3, lmargin2=24,
                        spacing3=1)
        t.tag_configure('porque', font=(self.fonte_ui, 9, 'italic'),
                        foreground=TEMA['nota'], lmargin1=3, lmargin2=24,
                        spacing3=3)
        t.tag_configure('nota', font=(self.fonte_ui, 9, 'italic'),
                        foreground=TEMA['nota'], lmargin2=12, spacing3=2)
        t.tag_configure('exemplo', font=(self.fonte_mono, 9),
                        foreground=TEMA['nota'], lmargin1=3, lmargin2=24,
                        spacing3=4)
        for chave in ('atencao', 'ruim', 'bom'):
            t.tag_configure(chave, font=(self.fonte_ui, 10, 'bold'),
                            foreground=TEMA[chave])
