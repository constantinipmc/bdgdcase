"""Preparar um caso: BDGD → tabelas → caso → simulação.

A linha de comando é a interface primária e continua sendo o que a documentação
descreve. Este painel existe porque a cadeia tem cinco passos, e o segundo
deles — descobrir o código do alimentador entre centenas — é justamente o tipo
de coisa que uma lista clicável resolve melhor que um argumento.

Tkinter **não** é dependência do pacote. Importar este módulo num ambiente sem
ele funciona; só abrir a janela é que falha, com mensagem explícita. Por isso
todo `import tkinter` mora dentro de função.

Arquitetura, em uma frase: o trabalho pesado roda numa thread, que não toca em
widget nenhum — ela empurra mensagens para uma fila, e o laço do Tk drena essa
fila. Widget de Tk só pode ser tocado pela thread que criou a janela, e ignorar
isso dá travamento intermitente, do tipo que só aparece na máquina do usuário.
"""
from __future__ import annotations

import os
import queue
import threading
import traceback

__all__ = ['abrir', 'JANELAS', 'pasta_de_trabalho_suspeita']

#: As janelas que `abrir` aceita. Uma só, hoje: houve uma para extrair e
#: outra para converter, e as duas faziam o que esta faz.
JANELAS = ('completa',)

#: Assinatura de uma geodatabase de verdade no disco.
_MARCA_GDB = '.gdbtable'


def resolver_gdb(caminho):
    """Encontra a geodatabase de verdade a partir do que o usuário escolheu.

    O `.zip` da ANEEL costuma descompactar a `.gdb` **aninhada dentro de outra
    pasta de mesmo nome**. Quem escolhe a de fora recebe do GDAL um `Permission
    denied` — mensagem que manda procurar no lugar errado e custa um tempo
    desproporcional.

    Devolve o caminho que de fato contém os `.gdbtable`, ou `None`.
    """
    caminho = os.path.abspath(str(caminho))
    if not os.path.isdir(caminho):
        return None
    try:
        if any(f.endswith(_MARCA_GDB) for f in os.listdir(caminho)):
            return caminho
        for nome in sorted(os.listdir(caminho)):
            filho = os.path.join(caminho, nome)
            if os.path.isdir(filho) and any(
                    f.endswith(_MARCA_GDB) for f in os.listdir(filho)):
                return filho
    except OSError:
        return None
    return None


#: As pastas que a própria ferramenta cria dentro da pasta de trabalho.
_PASTAS_DE_SAIDA = ('output', 'opendss')


def pasta_de_trabalho_suspeita(caminho):
    """Devolve a pasta que provavelmente se quis escolher, ou None se está boa.

    A pasta de trabalho é onde `Output/` e `OpenDSS/` são **criadas**. Escolher
    a própria `OpenDSS/` faz nascer `OpenDSS/OpenDSS/` e `OpenDSS/Output/` —
    e como o seletor de pastas do sistema reabre onde parou, o engano se
    aprofunda a cada vez: já apareceu `OpenDSS/OpenDSS/OpenDSS/` numa máquina
    de verdade.

    Nada disso levanta erro. A extração roda, a conversão roda, e o caso vai
    parar num lugar que ninguém procura depois.

    Devolve o ancestral mais próximo que não é pasta de saída — que é o que a
    pessoa quase certamente queria — ou None quando o caminho já está certo.
    """
    if not caminho:
        return None
    p = os.path.abspath(str(caminho).strip())
    subiu = False
    while os.path.basename(p).lower() in _PASTAS_DE_SAIDA:
        pai = os.path.dirname(p)
        if pai == p:
            break
        p, subiu = pai, True
    return p if subiu else None


class _AbaPreparar:
    """A aba que percorre a cadeia. Só métodos com `_ui_` tocam em widget."""

    def _preparar_iniciar(self):
        """O estado da aba, montado antes de os widgets existirem.

        Fica separado do `__init__` da janela porque a aba é um mixin: quem
        constrói a janela é `interface.janela`, e o que esta parte precisa é
        declarar as suas variáveis e a sua fila.

        **Fila própria, e não a da simulação.** As duas threads falam com o laço
        do Tk pela mesma disciplina, mas com vocabulários diferentes — `log`,
        `progresso`, `alimentadores`, `fim` aqui; passos e resultado lá. Uma
        fila só faria cada dreno consumir a mensagem do outro, e a mensagem
        perdida não daria erro: daria uma barra de progresso que não anda.
        """
        tk = self.tk
        self.fila_prep = queue.Queue()
        self.trabalhando = False
        self.alimentadores = []
        self.municipios = []
        self.ultimo_dia = None
        self.ultimo_caso = None

        self.v_gdb = tk.StringVar()
        self.v_base = tk.StringVar(value=os.getcwd())
        self.v_filtro = tk.StringVar()
        self.v_municipio = tk.StringVar()
        self.v_mapa = tk.BooleanVar(value=True)
        self.v_extrair = tk.BooleanVar(value=True)
        self.v_converter = tk.BooleanVar(value=True)
        self.v_verificar = tk.BooleanVar(value=True)
        self.v_rodar = tk.BooleanVar(value=True)
        self.v_dias = {d: tk.BooleanVar(value=(d == 'DU'))
                       for d in ('DU', 'SA', 'DO')}

    def _preparar_ligar(self):
        """Liga o filtro e o dreno, depois que os widgets já existem."""
        self.v_filtro.trace_add('write', lambda *_: self._ui_repovoar())
        self.root.after(80, self._ui_drenar)

    # ── Construção ───────────────────────────────────────────────────────────

    def _ui_montar(self):
        """Monta a aba dentro do quadro que a janela reservou para ela."""
        tk, ttk = self.tk, self.ttk
        m = ttk.Frame(self.quadro_preparar, padding=12)
        m.pack(fill=tk.BOTH, expand=True)
        m.columnconfigure(0, weight=1)
        m.rowconfigure(4, weight=1)

        # 1 — base
        g = ttk.LabelFrame(m, text=' 1. Base da BDGD (.gdb) ', padding=8)
        g.grid(row=0, column=0, sticky='ew')
        g.columnconfigure(0, weight=1)
        ttk.Entry(g, textvariable=self.v_gdb).grid(row=0, column=0, sticky='ew')
        ttk.Button(g, text='Procurar…', command=self._ui_escolher_gdb
                   ).grid(row=0, column=1, padx=(6, 0))
        self.btn_varrer = ttk.Button(g, text='Varrer alimentadores',
                                     command=self._ui_varrer)
        self.btn_varrer.grid(row=0, column=2, padx=(6, 0))
        self.lb_gdb = ttk.Label(g, text='Baixe a base em dadosabertos.aneel.gov.br',
                                foreground='gray')
        self.lb_gdb.grid(row=1, column=0, columnspan=3, sticky='w', pady=(6, 0))
        # Ligada por padrão, e desmarcável: desenhar o mapa lê a geometria
        # da base inteira, e isso é minutos numa base estadual contra os
        # vinte segundos da varredura. Quem já sabe o código que quer não
        # tem por que pagar a espera.
        ttk.Checkbutton(g, text='Desenhar também os alimentadores no mapa',
                        variable=self.v_mapa).grid(
                            row=2, column=0, columnspan=3, sticky='w',
                            pady=(4, 0))

        # 2 — alimentadores
        g = ttk.LabelFrame(m, text=' 2. Alimentadores ', padding=8)
        g.grid(row=1, column=0, sticky='ew', pady=(10, 0))
        g.columnconfigure(1, weight=1)
        ttk.Label(g, text='Filtrar:').grid(row=0, column=0, sticky='w')
        ttk.Entry(g, textvariable=self.v_filtro).grid(row=0, column=1, sticky='ew',
                                                     padx=(6, 6))
        ttk.Label(g, text='Município:').grid(row=0, column=2, sticky='e')
        self.cb_mun = ttk.Combobox(g, textvariable=self.v_municipio, width=14,
                                   state='readonly')
        self.cb_mun.grid(row=0, column=3, padx=(6, 0))

        cx = ttk.Frame(g)
        cx.grid(row=1, column=0, columnspan=4, sticky='ew', pady=(8, 0))
        cx.columnconfigure(0, weight=1)
        self.lst = tk.Listbox(cx, selectmode=tk.EXTENDED, height=8,
                              exportselection=False, activestyle='none')
        self.lst.grid(row=0, column=0, sticky='ew')
        sb = ttk.Scrollbar(cx, orient='vertical', command=self.lst.yview)
        sb.grid(row=0, column=1, sticky='ns')
        self.lst.config(yscrollcommand=sb.set)
        ttk.Label(g, text='Ctrl+clique ou Shift+clique para escolher vários.',
                  foreground='gray').grid(row=2, column=0, columnspan=4,
                                          sticky='w', pady=(4, 0))

        # 3 — trabalho
        g = ttk.LabelFrame(m, text=' 3. Pasta de trabalho ', padding=8)
        g.grid(row=2, column=0, sticky='ew', pady=(10, 0))
        g.columnconfigure(0, weight=1)
        ttk.Entry(g, textvariable=self.v_base).grid(row=0, column=0, sticky='ew')
        ttk.Button(g, text='Procurar…', command=self._ui_escolher_base
                   ).grid(row=0, column=1, padx=(6, 0))
        ttk.Label(g, text='As tabelas vão para Output/ e os casos para OpenDSS/, '
                          'aqui dentro.', foreground='gray'
                  ).grid(row=1, column=0, columnspan=2, sticky='w', pady=(6, 0))

        # 4 — etapas
        g = ttk.LabelFrame(m, text=' 4. O que fazer ', padding=8)
        g.grid(row=3, column=0, sticky='ew', pady=(10, 0))
        ttk.Checkbutton(g, text='Extrair da BDGD', variable=self.v_extrair
                        ).grid(row=0, column=0, sticky='w')
        ttk.Checkbutton(g, text='Gerar o caso OpenDSS', variable=self.v_converter
                        ).grid(row=0, column=1, sticky='w', padx=(16, 0))
        d = ttk.Frame(g)
        d.grid(row=0, column=2, sticky='w', padx=(8, 0))
        ttk.Label(d, text='dias:').pack(side=tk.LEFT)
        for nome, var in self.v_dias.items():
            ttk.Checkbutton(d, text=nome, variable=var).pack(side=tk.LEFT)
        ttk.Checkbutton(g, text='Verificar se resolve', variable=self.v_verificar
                        ).grid(row=1, column=0, sticky='w', pady=(6, 0))
        ttk.Checkbutton(g, text='Rodar o dia (96 passos)', variable=self.v_rodar
                        ).grid(row=1, column=1, sticky='w', padx=(16, 0),
                               pady=(6, 0))
        ttk.Label(g, text='As duas últimas resolvem o caso no OpenDSS.',
                  foreground='gray').grid(row=2, column=0, columnspan=3,
                                          sticky='w', pady=(6, 0))

        # 5 — registro
        g = ttk.LabelFrame(m, text=' 5. Registro ', padding=8)
        g.grid(row=4, column=0, sticky='nsew', pady=(10, 0))
        g.columnconfigure(0, weight=1)
        g.rowconfigure(0, weight=1)
        self.txt_log = tk.Text(g, height=7, wrap='word', state='disabled',
                           font=('Consolas', 9))
        self.txt_log.grid(row=0, column=0, sticky='nsew')
        sb = ttk.Scrollbar(g, orient='vertical', command=self.txt_log.yview)
        sb.grid(row=0, column=1, sticky='ns')
        self.txt_log.config(yscrollcommand=sb.set)

        # rodapé
        r = ttk.Frame(m)
        r.grid(row=5, column=0, sticky='ew', pady=(10, 0))
        r.columnconfigure(0, weight=1)
        self.prog = ttk.Progressbar(r, mode='determinate')
        self.prog.grid(row=0, column=0, sticky='ew')
        self.btn_ir = ttk.Button(r, text='Executar', command=self._ui_executar)
        self.btn_ir.grid(row=0, column=1, padx=(8, 0))
        self.btn_abrir = ttk.Button(r, text='Abrir pasta', state='disabled',
                                    command=self._ui_abrir_pasta)
        self.btn_abrir.grid(row=0, column=2, padx=(6, 0))
        self.btn_csv = ttk.Button(r, text='Salvar CSV do dia', state='disabled',
                                  command=self._ui_salvar_csv)
        self.btn_csv.grid(row=0, column=3, padx=(6, 0))
        self.btn_mapa = ttk.Button(r, text='Ver no mapa', state='disabled',
                                   command=self._ui_mapa)
        self.btn_mapa.grid(row=0, column=4, padx=(6, 0))

        self._log('Escolha a base da BDGD e clique em "Varrer alimentadores".')

    # ── Ponte thread → UI ────────────────────────────────────────────────────

    def _log(self, msg):
        """Chamável de qualquer thread: enfileira, não escreve."""
        self.fila_prep.put(('log', msg))

    def _ui_drenar(self):
        """Único ponto que escreve na tela. Roda no laço do Tk."""
        try:
            while True:
                tipo, carga = self.fila_prep.get_nowait()
                if tipo == 'log':
                    self.txt_log.config(state='normal')
                    self.txt_log.insert('end', carga + '\n')
                    self.txt_log.see('end')
                    self.txt_log.config(state='disabled')
                elif tipo == 'progresso':
                    self.prog['value'] = carga
                elif tipo == 'alimentadores':
                    self.alimentadores, self.municipios = carga
                    self.cb_mun['values'] = [''] + self.municipios
                    self._ui_repovoar()
                elif tipo == 'pan_progresso':
                    self._ui_pan_progresso(*carga)
                elif tipo == 'panorama':
                    self._ui_pan_desenhar(carga)
                elif tipo == 'fim':
                    self._ui_travar(False)
                    self.btn_abrir.config(state='normal')
                    self.btn_csv.config(
                        state='normal' if self.ultimo_dia else 'disabled')
                    self.btn_mapa.config(
                        state='normal' if self.ultimo_caso else 'disabled')
        except queue.Empty:
            pass
        self.root.after(80, self._ui_drenar)

    def _ui_travar(self, travado):
        """Trava ou destrava os botões que não podem rodar duas vezes ao mesmo tempo."""
        self.trabalhando = travado
        estado = 'disabled' if travado else 'normal'
        self.btn_ir.config(state=estado)
        self.btn_varrer.config(state=estado)

    def _rodar_em_thread(self, alvo, *args):
        """Roda `alvo` numa thread, com o erro virando linha no registro.

        O `except` largo é deliberado: aqui ele não engole o problema, escreve.
        Uma exceção não tratada numa thread do Tk desaparece em silêncio — o
        trabalho simplesmente para, sem mensagem, e o usuário fica olhando para
        uma barra de progresso parada.
        """
        if self.trabalhando:
            return
        self._ui_travar(True)
        self.prog['value'] = 0

        def envelope():
            """Executa o alvo e garante que o 'fim' chegue à fila, dê no que der.

            O `finally` é o que impede a janela de ficar travada para sempre
            quando o trabalho falha: sem ele, os botões não voltariam a
            habilitar.
            """
            try:
                alvo(*args)
            except Exception as exc:
                self._log('')
                self._log('ERRO: %s' % exc)
                for linha in traceback.format_exc().splitlines()[-4:]:
                    self._log('   %s' % linha)
            finally:
                self.fila_prep.put(('fim', None))

        threading.Thread(target=envelope, daemon=True).start()

    # ── Passo 1 e 2 ──────────────────────────────────────────────────────────

    def _ui_escolher_gdb(self):
        """Pede a pasta `.gdb` da BDGD."""
        escolhido = self.filedialog.askdirectory(
            title='Selecione a pasta .gdb da BDGD')
        if not escolhido:
            return
        real = resolver_gdb(escolhido)
        if real is None:
            self.v_gdb.set(escolhido)
            self.lb_gdb.config(
                text='Não encontrei uma geodatabase aqui dentro.',
                foreground='#a03030')
            return
        self.v_gdb.set(real)
        if os.path.abspath(real) != os.path.abspath(escolhido):
            # O caso comum do zip da ANEEL, resolvido em silêncio — mas dito, para
            # que o caminho na caixa não pareça ter mudado sozinho.
            self.lb_gdb.config(
                text='A geodatabase estava numa subpasta; ajustei o caminho.',
                foreground='#806000')
        else:
            self.lb_gdb.config(text='Geodatabase encontrada.', foreground='gray')

    def _ui_escolher_base(self):
        """Pede a pasta de trabalho, onde `Output/` e `OpenDSS/` vão ficar.

        Se a escolhida for a própria `Output/` ou `OpenDSS/`, oferece o pai:
        ver :func:`pasta_de_trabalho_suspeita` para o porquê.
        """
        escolhido = self.filedialog.askdirectory(
            title='Pasta de trabalho (Output/ e OpenDSS/ ficam aqui)')
        if not escolhido:
            return
        sugerida = pasta_de_trabalho_suspeita(escolhido)
        if sugerida and self.messagebox.askyesno(
                'Pasta de trabalho',
                'Você escolheu uma pasta de saída:\n\n  %s\n\n'
                'É aqui dentro que Output/ e OpenDSS/ seriam CRIADAS, o que '
                'daria caminhos como OpenDSS/OpenDSS/. Quer usar a pasta de '
                'cima?\n\n  %s' % (escolhido, sugerida)):
            escolhido = sugerida
        self.v_base.set(escolhido)

    def _ui_varrer(self):
        """Dispara a varredura da base pelos alimentadores que ela contém."""
        gdb = resolver_gdb(self.v_gdb.get()) if self.v_gdb.get() else None
        if gdb is None:
            self.messagebox.showwarning(
                'Base não encontrada',
                'Escolha a pasta .gdb da BDGD antes de varrer.')
            return
        self.v_gdb.set(gdb)
        # Lida AQUI, na thread da interface: variável do Tk só existe para
        # a thread que criou o interpretador, e lê-la lá dentro levanta
        # "main thread is not in main loop".
        self._rodar_em_thread(self._varrer, gdb, bool(self.v_mapa.get()))

    def _varrer(self, gdb, mapa=True):
        """Lista os alimentadores da base e, depois, os desenha no mapa.

        Uma base estadual leva cerca de vinte segundos para listar, e é a
        primeira coisa que o usuário faz — se travasse a janela, a impressão
        seria de programa quebrado logo no primeiro clique.

        A lista sai PRIMEIRO, e sozinha já serve: quem sabe o código que quer
        pode seguir para o `Executar` sem esperar o resto. O mapa vem em
        seguida, na mesma thread, porque ele lê a geometria da base inteira e
        custa minutos — e ninguém deve ficar sem a lista por causa disso.
        """
        from bdgdcase.api import listar
        self._log('Varrendo %s …' % os.path.basename(gdb))
        self._log('   (uma base estadual leva cerca de 20 s)')
        alimentadores, municipios = listar(gdb)
        self.fila_prep.put(('alimentadores', (alimentadores, municipios)))
        self._log('%d alimentadores e %d municípios encontrados.'
                  % (len(alimentadores), len(municipios)))
        if mapa and alimentadores:
            self._desenhar_no_mapa(gdb)

    def _desenhar_no_mapa(self, gdb):
        """Lê a geometria da média tensão e entrega o mapa à aba dele.

        Falhar aqui não pode custar a varredura: a lista já está na tela e o
        trabalho pode seguir sem o mapa. Por isso o erro vira linha no
        registro, e não exceção que aborta o que já deu certo.
        """
        from bdgdcase.api import panorama
        self._log('')
        self._log('Desenhando os alimentadores no mapa…')
        self._log('   (a primeira vez numa base estadual leva alguns minutos; '
                  'depois sai do cache)')
        try:
            pan = panorama(gdb, progresso=lambda f, t: self.fila_prep.put(
                ('pan_progresso', (f, t))))
        except Exception as exc:
            self._log('   sem mapa: %s' % exc)
            self._log('   A lista acima continua valendo.')
            return
        self.fila_prep.put(('panorama', pan))
        self._log('   %d alimentadores desenhados — veja a aba '
                  '"Alimentadores no mapa".' % len(pan.alimentadores))

    def _ui_repovoar(self):
        """Refiltra a lista de alimentadores pelo que se digitou."""
        alvo = self.v_filtro.get().strip().upper()
        self.lst.delete(0, 'end')
        for a in self.alimentadores:
            if not alvo or alvo in a.upper():
                self.lst.insert('end', a)

    def _ui_selecionados(self):
        """Os alimentadores marcados na lista."""
        return [self.lst.get(i) for i in self.lst.curselection()]

    # ── Passo 4: a cadeia ────────────────────────────────────────────────────

    def _ui_executar(self):
        """Confere o que foi pedido e dispara a cadeia numa thread."""
        alims = self._ui_selecionados()
        if not alims:
            self.messagebox.showwarning(
                'Nenhum alimentador',
                'Selecione ao menos um alimentador na lista.')
            return
        dias = tuple(d for d, v in self.v_dias.items() if v.get())
        if self.v_converter.get() and not dias:
            self.messagebox.showwarning(
                'Nenhum tipo de dia', 'Marque ao menos um dia (DU, SA ou DO).')
            return
        base = self.v_base.get().strip() or os.getcwd()
        gdb = self.v_gdb.get()
        if self.v_extrair.get() and not resolver_gdb(gdb):
            self.messagebox.showwarning(
                'Base não encontrada',
                'Extrair exige a pasta .gdb. Desmarque "Extrair da BDGD" para '
                'usar tabelas já extraídas.')
            return
        self.ultimo_dia = self.ultimo_caso = None
        self._rodar_em_thread(self._cadeia, gdb, base, alims, dias,
                              self.v_municipio.get().strip() or None)

    def _cadeia(self, gdb, base, alims, dias, municipio):
        """Extrai, converte, verifica e roda — para cada alimentador escolhido.

        As quatro etapas são opcionais e independentes: dá para converter de
        novo sem reextrair, que é o caso comum quando se muda uma premissa. O
        progresso conta etapas efetivamente pedidas, e não as quatro.
        """
        etapas = sum((self.v_extrair.get(), self.v_converter.get(),
                      self.v_verificar.get(), self.v_rodar.get()))
        total = max(1, len(alims) * etapas)
        feito = [0]

        def passo():
            """Marca mais uma etapa concluída na barra de progresso."""
            feito[0] += 1
            self.fila_prep.put(('progresso', 100.0 * feito[0] / total))

        out = os.path.join(base, 'Output')
        for alim in alims:
            self._log('')
            self._log('── %s ' % alim + '─' * 40)

            if self.v_extrair.get():
                self._extrair(gdb, out, alim, municipio)
                passo()
            if self.v_converter.get():
                self._converter(os.path.join(out, alim), dias)
                passo()
            for dia in (dias if self.v_converter.get() else ('DU',)):
                caso = os.path.join(base, 'OpenDSS', '%s_%s' % (alim, dia))
                if self.v_verificar.get():
                    self._verificar(caso)
                if self.v_rodar.get():
                    self._rodar(caso)
            if self.v_verificar.get():
                passo()
            if self.v_rodar.get():
                passo()

        self.fila_prep.put(('progresso', 100.0))
        self._log('')
        self._log('Concluído. As saídas estão em %s' % base)

    def _extrair(self, gdb, out, alim, municipio):
        """Recorta um alimentador da geodatabase para `Output/`."""
        from bdgdcase.api import extrair
        self._log('Extraindo da BDGD… (alguns minutos)')
        (_, ok, err), = extrair(gdb, out, [alim], municipio=municipio)
        if not ok:
            raise RuntimeError('extração de %s falhou: %s' % (alim, err))
        pasta = os.path.join(out, alim)
        n = len(os.listdir(pasta))
        mb = sum(os.path.getsize(os.path.join(pasta, f))
                 for f in os.listdir(pasta)) / 2 ** 20
        self._log('   %d arquivos, %.1f MB em Output/%s' % (n, mb, alim))

    def _converter(self, pasta, dias):
        """Gera o caso OpenDSS a partir das tabelas, para os dias pedidos."""
        from bdgdcase.api import converter
        self._log('Gerando o caso OpenDSS (%s)…' % ', '.join(dias))
        erros = converter(pasta, dias=dias)
        if erros:
            raise RuntimeError('; '.join(str(e) for e in erros))
        self._log('   caso gerado.')

    def _verificar(self, caso):
        """Resolve um passo do caso, só para saber se ele fecha.

        Barato e cedo: se o caso não resolve um passo, não vai resolver noventa
        e seis, e descobrir isso agora poupa os minutos do dia inteiro.
        """
        from bdgdcase.solucao import verificar
        r = verificar(caso)
        self.ultimo_caso = caso
        self._log('Verificação de %s:' % os.path.basename(caso))
        self._log('   converge — %d barras, %d cargas, %d linhas, %d trafos'
                  % (r['barras'], r['cargas'], r['linhas'],
                     r['transformadores']))
        self._log('   tensão %.4f a %.4f pu' % (r['v_min_pu'], r['v_max_pu']))

    def _rodar(self, caso):
        """Roda o dia inteiro do caso, 96 passos, e guarda o resultado."""
        from bdgdcase.solucao import rodar_dia
        self._log('Rodando o dia de %s…' % os.path.basename(caso))
        d = rodar_dia(caso)
        self.ultimo_dia, self.ultimo_caso = d, caso
        self._log('   pico %.1f kW às %.2f h' % (d['p_max_kw'], d['hora_p_max']))
        self._log('   %.1f kWh importados, %.1f exportados'
                  % (d['energia_importada_kwh'], d['energia_exportada_kwh']))
        self._log('   perdas %.1f kWh (%.2f%%)'
                  % (d['perdas_kwh'], d['perdas_pct_dia']))
        self._log('   tensão %.4f a %.4f pu no dia'
                  % (d['v_min_dia_pu'], d['v_max_dia_pu']))
        if d['passos_nao_convergidos']:
            # Nunca deixar isto passar calado: parte da curva vira interpolação.
            self._log('   ATENÇÃO: %d passo(s) não convergiram e foram '
                      'repetidos do anterior.'
                      % len(d['passos_nao_convergidos']))

    # ── Rodapé ───────────────────────────────────────────────────────────────

    def _ui_abrir_pasta(self):
        """Abre a pasta de trabalho no explorador de arquivos do sistema."""
        base = self.v_base.get().strip() or os.getcwd()
        try:
            if os.name == 'nt':
                os.startfile(base)  # noqa: S606 - abrir o gerenciador de arquivos
            else:
                import subprocess
                subprocess.Popen(
                    ['open' if os.uname().sysname == 'Darwin' else 'xdg-open',
                     base])
        except Exception as exc:
            self.messagebox.showinfo('Pasta de trabalho',
                                     '%s\n\n(%s)' % (base, exc))

    def _ui_mapa(self):
        """Leva o caso recém-criado para a aba do mapa, na mesma janela.

        Antes isto abria um segundo processo, porque eram dois programas: cada
        um criava a sua raiz Tk, e duas raízes no mesmo processo é o arranjo
        que trava de formas difíceis de reproduzir. Agora é uma janela só, com
        uma raiz só — o mapa é uma aba, e ir para ele é trocar de aba.
        """
        if not self.ultimo_caso:
            return
        self.v_pasta.set(self.ultimo_caso)
        self.abas.select(self.quadro_mapa_aba)
        self._log('Caso %s carregado na aba do mapa.'
                  % os.path.basename(self.ultimo_caso))
        self._simular()

    def _ui_salvar_csv(self):
        """Salva as séries do dia simulado num `.csv` escolhido pelo usuário."""
        if not self.ultimo_dia:
            return
        destino = self.filedialog.asksaveasfilename(
            title='Salvar as séries do dia', defaultextension='.csv',
            initialfile='%s.csv' % self.ultimo_dia['alimentador'],
            filetypes=[('CSV', '*.csv')])
        if not destino:
            return
        from bdgdcase.solucao import para_csv
        para_csv(self.ultimo_dia, destino)
        self._log('CSV salvo em %s' % destino)


def abrir(janela='completa'):
    """Abre a janela na aba de preparar. Bloqueia até o usuário fechá-la.

    `bdgdcase gui` e `bdgdcase mapa` abrem a mesma janela — mudam só a aba em
    que ela começa. É o que faz sentido: extrair um alimentador e não poder
    olhar para ele sem trocar de programa era uma fronteira do código, não do
    trabalho.

    O argumento sobrevive por compatibilidade da linha de comando e hoje só
    aceita ``'completa'``: houve uma janela para extrair e outra para converter,
    e as duas foram aposentadas. Faziam o que esta faz, e manter três
    interfaces com o mesmo propósito custava mais do que valia.
    """
    if janela != 'completa':
        raise ValueError('janela deve ser uma de %s, não %r'
                         % (', '.join(JANELAS), janela))
    from bdgdcase.interface.janela import abrir_janela
    abrir_janela(aba='preparar')
