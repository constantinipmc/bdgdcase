# -*- coding: utf-8 -*-
"""A janela: as abas, e a thread que não pode tocar nelas.

`_Janela` não desenha e não calcula — ela monta as abas e compõe as partes
que fazem o trabalho: preparar, o panorama da base, o mapa com o inspetor, o
resumo e o registro de ajustes. Cada uma mora no seu módulo, e todas compartilham o mesmo `self`: os
widgets, o caso carregado, a fila.

## A disciplina de thread, que é requisito e não detalhe

Simular um dia leva minutos. Se isso rodar no laço do Tk, a janela congela e o
sistema operacional oferece encerrar o programa. Então roda numa thread — e
essa thread **não toca em widget nenhum**: ela empurra mensagens numa fila, e
`_drenar` esvazia a fila de dentro do laço do Tk.

Widget de Tk só pode ser tocado pela thread que criou o interpretador. Violar
isso não dá erro na hora: dá travamento intermitente, na máquina do usuário,
sem traceback. É a razão de a fila existir, e de ela parecer mais complicada do
que precisaria.
"""
from __future__ import annotations

import os
import queue
import threading
import traceback

from bdgdcase.interface.base import TEMA, _fontes, _tk
from bdgdcase.interface.mapa import CAMADAS_PADRAO, CORES_PADRAO
from bdgdcase.interface.inspetor import _Inspetor
from bdgdcase.interface.mapa import _AbaMapa
from bdgdcase.interface.panorama import _AbaPanorama
from bdgdcase.interface.preparar import _AbaPreparar
from bdgdcase.interface.registro import _AbaAjustes
from bdgdcase.interface.resumo import _AbaResumo


class _Janela(_AbaPreparar, _AbaPanorama, _AbaMapa, _Inspetor, _AbaResumo,
              _AbaAjustes):
    """Da BDGD ao alimentador no mapa, sem trocar de janela."""

    def __init__(self, root, pasta=None, aba='mapa'):
        """Monta a janela inteira: estado, estilo, abas e os dois drenos.

        `aba` decide por onde se entra — `'preparar'` para `bdgdcase gui`,
        `'mapa'` para `bdgdcase mapa`. É a única diferença entre os dois
        comandos.

        Se `pasta` vier, o caso é simulado de saída: quem passou o caminho já
        disse o que queria ver.
        """
        tk, filedialog, messagebox, ttk = _tk()
        self.tk, self.filedialog, self.messagebox, self.ttk = (
            tk, filedialog, messagebox, ttk)
        self.root = root
        self.fila = queue.Queue()
        self.geo = None
        self.resultado = None
        self.passo = 0
        self.animando = False
        self._pontos = self._colecao = self._barra_cor = None

        root.title('bdgdcase — da BDGD ao caso OpenDSS')

        self.v_pasta = tk.StringVar(value=pasta or '')
        self.v_fundo = tk.StringVar(value='hibrido')
        self.v_camada = {c: tk.BooleanVar(value=p)
                         for c, p in CAMADAS_PADRAO.items()}
        self.cores = dict(CORES_PADRAO)

        # A navegação do inspetor. `_foco` é o elemento aberto — None quando se
        # está vendo o ponto —, e `_trilha` é o caminho até ele. Ficam SEPARADOS
        # de `_selecionada`, que continua sendo só o índice da barra: é
        # `_selecionada` que o clique no mapa cicla entre barras empilhadas, e
        # misturar as duas coisas faria abrir uma unidade mudar de barra.
        self._foco = None
        self._trilha = []
        self._graficos = []
        self._selecionada = None
        self.v_hora = tk.StringVar(value='--:--')
        self.v_status = tk.StringVar(value='Escolha a pasta do caso e simule.')
        self.v_posicao = tk.StringVar(value='')

        self._preparar_iniciar()
        self._panorama_iniciar()

        self._estilizar()
        self._montar()
        self._dimensionar()
        self._preparar_ligar()
        self._panorama_ligar()
        self.root.after(120, self._posicionar_divisoria)
        self.root.after(80, self._drenar)
        self.abas.select(self.quadro_preparar if aba == 'preparar'
                         else self.quadro_mapa_aba)
        if pasta:
            self._simular()

    # ── Construção ───────────────────────────────────────────────────────────

    def _montar(self):
        """Constrói os widgets: a barra do caso, as cinco abas, o tempo, o rodapé.

        A barra do caso, a régua do tempo e o rodapé ficam FORA do caderno de
        abas, e somem na aba de preparar — ver `_aba_trocou`.
        """
        tk, ttk = self.tk, self.ttk
        m = ttk.Frame(self.root, padding=10)
        m.pack(fill=tk.BOTH, expand=True)
        m.columnconfigure(0, weight=1)
        m.rowconfigure(1, weight=1)

        topo = self._barra_caso = ttk.Frame(m)
        topo.grid(row=0, column=0, sticky='ew')
        topo.columnconfigure(1, weight=1)
        ttk.Label(topo, text='Caso:').grid(row=0, column=0)
        ttk.Entry(topo, textvariable=self.v_pasta).grid(row=0, column=1,
                                                       sticky='ew', padx=6)
        ttk.Button(topo, text='Procurar…', command=self._escolher
                   ).grid(row=0, column=2)
        # A medição por carga não tem caixa na janela: é escolha de quem abre o
        # caso, não de quem já está olhando para ele, e trocá-la obriga a
        # resolver o dia de novo. Fica em `bdgdcase mapa --medir-cargas`, e a
        # variável continua aqui porque `abrir_janela` a define a partir dali.
        self.v_medir = tk.BooleanVar(value=False)
        self.btn_sim = ttk.Button(topo, text='Simular', style='Destaque.TButton',
                                  command=self._simular)
        self.btn_sim.grid(row=0, column=3, padx=(6, 0))

        self.abas = ttk.Notebook(m)
        self.abas.grid(row=1, column=0, sticky='nsew', pady=(8, 0))
        self.abas.bind('<<NotebookTabChanged>>', self._aba_trocou)

        # Preparar o caso: da BDGD ao `.dss`.
        #
        # Antes era outro programa, e ver no mapa o que se acabou de extrair
        # exigia abrir uma segunda janela. A fronteira era do código, não do
        # trabalho: quem extrai um alimentador quer olhar para ele em seguida.
        self.quadro_preparar = ttk.Frame(self.abas)
        self.abas.add(self.quadro_preparar, text='  Preparar  ')
        self._ui_montar()

        # Os alimentadores da base no mapa.
        #
        # Entre preparar e o mapa do caso porque é essa a ordem do
        # trabalho: escolher o alimentador, e só depois olhar para ele.
        # O código que a lista mostra não diz onde o alimentador passa, e
        # descobrir isso extraindo custa minutos por tentativa.
        self.quadro_panorama = ttk.Frame(self.abas)
        self.abas.add(self.quadro_panorama,
                      text='  Alimentadores no mapa  ')
        self._ui_pan_montar()

        # O mapa do caso
        # Divisória arrastável: em tela larga o painel sobra, em tela estreita
        # falta. Deixar a régua na mão de quem olha resolve os dois.
        corpo = self.quadro_mapa_aba = ttk.PanedWindow(self.abas,
                                                       orient='horizontal')
        self.abas.add(corpo, text='  Mapa  ')

        self.quadro_mapa = ttk.Frame(corpo)
        corpo.add(self.quadro_mapa, weight=4)

        lado = ttk.LabelFrame(corpo, text=' Ponto selecionado ', padding=6)
        corpo.add(lado, weight=0)
        self._divisoria = corpo
        # `wrap='word'` e não `'none'`: sem barra horizontal, o que não cabe
        # na largura simplesmente some, e era o que acontecia com as notas —
        # justamente as linhas que explicam de onde o número veio.
        self.txt = tk.Text(lado, width=46, wrap='word', state='disabled',
                           font=(self.fonte_ui, 10), background=TEMA['painel'],
                           foreground=TEMA['valor'], relief='flat',
                           borderwidth=0, highlightthickness=0,
                           padx=14, pady=10, spacing1=1, spacing3=1,
                           cursor='arrow')
        self._tags_do_inspetor()
        rolagem = ttk.Scrollbar(lado, orient='vertical', command=self.txt.yview)
        self.txt.configure(yscrollcommand=rolagem.set)
        rolagem.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # O alimentador inteiro
        self.quadro_resumo = ttk.Frame(self.abas, padding=6)
        self.abas.add(self.quadro_resumo, text='  Resumo do alimentador  ')

        # O que foi ajustado para o caso fechar.
        #
        # A BDGD publicada não resolve um fluxo de potência como está, e entre
        # ela e este caso houve dezenas de decisões. Quem usa o resultado para
        # decidir alguma coisa precisa saber quais — que aquela potência foi
        # estimada e não lida, que aquela curva veio de outra distribuidora, que
        # aquele transformador tem mais geração do que aguenta e o modelo não
        # corrigiu.
        #
        # Sem esta aba, o `.dss` tem a mesma aparência de autoridade quer o
        # cadastro estivesse completo, quer tivesse sido remendado em quinze
        # lugares.
        self.quadro_ajustes = ttk.Frame(self.abas, padding=6)
        self.abas.add(self.quadro_ajustes, text='  Ajustes do cadastro  ')

        base = ttk.Frame(m)
        base.grid(row=2, column=0, sticky='ew', pady=(8, 0))
        base.columnconfigure(3, weight=1)

        # A barra de baixo ficou só com o fundo: as camadas foram para a
        # legenda sobre o mapa, e o seletor de "colorir por" saiu junto com o
        # modo de corrente — as barras dizem a tensão, os cabos dizem o nível,
        # e o carregamento dos trechos está no painel, com a ampacidade ao
        # lado, que é onde ele significa alguma coisa.
        col = 3
        ttk.Label(base, text='Fundo:').grid(row=0, column=col + 1)
        from bdgdcase.interface.azulejos import FUNDOS
        for i, chave in enumerate(('hibrido', 'satelite', 'claro', 'ruas',
                                   'nenhum')):
            ttk.Radiobutton(base, text=FUNDOS[chave]['rotulo'], value=chave,
                            variable=self.v_fundo, command=self._trocar_fundo
                            ).grid(row=0, column=col + 2 + i, padx=(6, 0))

        tempo = self._barra_tempo = ttk.Frame(m)
        tempo.grid(row=3, column=0, sticky='ew', pady=(6, 0))
        tempo.columnconfigure(2, weight=1)
        self.btn_play = ttk.Button(tempo, text='▶', width=3,
                                   command=self._animar)
        self.btn_play.grid(row=0, column=0)
        ttk.Button(tempo, text='◀', width=3,
                   command=lambda: self._ir(self.passo - 1)).grid(row=0, column=1)
        self.escala = ttk.Scale(tempo, from_=0, to=95, orient='horizontal',
                                command=self._arrastar)
        self.escala.grid(row=0, column=2, sticky='ew', padx=6)
        ttk.Button(tempo, text='▶|', width=3,
                   command=lambda: self._ir(self.passo + 1)).grid(row=0, column=3)
        ttk.Label(tempo, textvariable=self.v_hora, width=7,
                  style='Relogio.TLabel').grid(row=0, column=4)

        rodape = self._rodape = ttk.Frame(m)
        rodape.grid(row=4, column=0, sticky='ew', pady=(6, 0))
        rodape.columnconfigure(0, weight=1)
        ttk.Label(rodape, textvariable=self.v_status, style='Rodape.TLabel'
                  ).grid(row=0, column=0, sticky='w')
        ttk.Label(rodape, textvariable=self.v_posicao, style='Rodape.TLabel'
                  ).grid(row=0, column=1, sticky='e', padx=(12, 0))
        ttk.Label(rodape, text='roda: zoom  ·  arrastar: mover  ·  '
                               'clique: selecionar', style='Rodape.TLabel'
                  ).grid(row=0, column=2, sticky='e', padx=(16, 0))

    def _aba_trocou(self, _evento=None):
        """Some com os controles do caso enquanto ainda se está preparando um.

        A barra do caso e a régua do tempo falam de um caso já carregado.
        Nas duas abas que vêm ANTES de haver caso — preparar e os
        alimentadores no mapa — não há caso nenhum, e deixá-las à vista ali
        sugere que se pode arrastar o tempo de coisa alguma — o tipo de
        detalhe que faz alguém clicar e concluir que o programa quebrou.
        """
        preparando = self.abas.select() in (str(self.quadro_preparar),
                                            str(self.quadro_panorama))
        for quadro in (self._barra_caso, self._barra_tempo, self._rodape):
            quadro.grid_remove() if preparando else quadro.grid()

    def _estilizar(self):
        """Tema `clam` e cores próprias: o padrão do Tk no Windows é datado.

        `clam` é o único tema embutido que aceita cor de fundo nos widgets —
        com `vista`, os `ttk.Frame` ignoram o que se pede e a janela fica
        remendada.
        """
        ttk = self.ttk
        self.fonte_ui, self.fonte_mono = _fontes(self.tk)
        estilo = ttk.Style()
        try:
            estilo.theme_use('clam')
        except Exception:      # pragma: no cover - depende do build do Tk
            pass

        self.root.configure(background=TEMA['fundo'])
        estilo.configure('.', background=TEMA['fundo'], foreground=TEMA['titulo'],
                         font=(self.fonte_ui, 10))
        estilo.configure('TFrame', background=TEMA['fundo'])
        estilo.configure('TLabel', background=TEMA['fundo'])
        estilo.configure('TCheckbutton', background=TEMA['fundo'])
        estilo.configure('TRadiobutton', background=TEMA['fundo'])
        estilo.configure('TLabelframe', background=TEMA['fundo'],
                         bordercolor=TEMA['borda'])
        estilo.configure('TLabelframe.Label', background=TEMA['fundo'],
                         foreground=TEMA['rotulo'],
                         font=(self.fonte_ui, 9, 'bold'))
        estilo.configure('TNotebook', background=TEMA['fundo'], borderwidth=0)
        estilo.configure('TNotebook.Tab', padding=(16, 7),
                         font=(self.fonte_ui, 10))
        estilo.map('TNotebook.Tab',
                   background=[('selected', TEMA['painel'])],
                   foreground=[('selected', TEMA['titulo'])])
        estilo.configure('TButton', padding=(12, 5))
        estilo.configure('Destaque.TButton', padding=(14, 5),
                         font=(self.fonte_ui, 10, 'bold'))
        estilo.configure('Rodape.TLabel', foreground=TEMA['rotulo'],
                         font=(self.fonte_ui, 9))
        estilo.configure('Relogio.TLabel', foreground=TEMA['titulo'],
                         font=(self.fonte_mono, 13, 'bold'))

    def _posicionar_divisoria(self, fracao=0.78):
        """Dá ao mapa a maior parte da largura.

        O `PanedWindow` do Tk decide a divisão inicial pela largura *pedida*
        pelos widgets, e não pelos pesos. Um `Text` de 44 colunas pede muito
        numa tela de alta densidade — o painel ficava com 38% da janela, e o
        mapa com pouco mais da metade. Aqui a régua é posta na mão, e continua
        arrastável.
        """
        try:
            largura = self._divisoria.winfo_width()
            if largura > 200:
                self._divisoria.sashpos(0, int(largura * fracao))
        except Exception:      # pragma: no cover - versões antigas do Tk
            pass

    def _dimensionar(self):
        """Abre maximizada: o mapa é o conteúdo, e mapa pequeno não se lê.

        `zoomed` é o estado nativo do Windows e do X; onde ele não existe, o
        recuo é preencher a tela na mão.
        """
        self.root.update_idletasks()
        tl, ta = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry('%dx%d+0+0' % (tl, ta - 60))
        try:
            self.root.state('zoomed')
        except Exception:          # pragma: no cover - depende do gerenciador
            try:
                self.root.attributes('-zoomed', True)
            except Exception:
                pass
        self.root.minsize(900, 620)

    # ── Ponte thread → UI ────────────────────────────────────────────────────

    def _drenar(self):
        """Esvazia a fila da simulação, de dentro do laço do Tk.

        Único ponto em que a thread da simulação chega à tela. Ela empurra
        mensagens e nunca toca em widget: widget de Tk só pode ser tocado pela
        thread que criou o interpretador, e violar isso não dá erro na hora —
        dá travamento intermitente, na máquina do usuário, sem traceback.
        """
        try:
            while True:
                tipo, carga = self.fila.get_nowait()
                if tipo == 'status':
                    self.v_status.set(carga)
                elif tipo == 'pronto':
                    self.resultado = carga
                    self._construir_mapa()
                elif tipo == 'fundo':
                    # A vista vai na mensagem porque agora há duas — o mapa do
                    # caso e o panorama da base. Sem ela, o mosaico pedido por
                    # uma acabaria pintado na outra.
                    vista, alvo, imagem, credito = carga
                    if vista.pintar_fundo(alvo, imagem, credito) is None:
                        self.v_status.set(
                            'Sem mapa de fundo (offline ou servidor fora). '
                            'O resto funciona igual.')
                elif tipo == 'fim':
                    self.btn_sim.config(state='normal')
        except queue.Empty:
            pass
        self.root.after(80, self._drenar)

    def _escolher(self):
        """Pede a pasta do caso — `OpenDSS/{ALIMENTADOR}_{DIA}`."""
        d = self.filedialog.askdirectory(
            title='Pasta do caso (OpenDSS/{ALIMENTADOR}_{DIA})')
        if d:
            self.v_pasta.set(d)

    def _simular(self):
        """Roda o dia do caso escolhido, numa thread, e desenha o resultado.

        Recusa pasta sem `Master.dss` antes de começar: é o erro mais comum de
        quem aponta para `Output/` em vez de `OpenDSS/`, e descobri-lo depois
        de minutos de simulação seria caro à toa.
        """
        pasta = self.v_pasta.get().strip()
        if not os.path.isfile(os.path.join(pasta, 'Master.dss')):
            self.messagebox.showwarning(
                'Caso não encontrado',
                'Escolha a pasta que contém o Master.dss — ela costuma se '
                'chamar OpenDSS/{ALIMENTADOR}_{DIA}, e não Output/.')
            return
        self.btn_sim.config(state='disabled')
        # Lida AQUI, na thread da interface. Variável do Tk só existe para a
        # thread que criou o interpretador: lê-la lá dentro levanta "main
        # thread is not in main loop" e o trabalho morre antes de começar.
        medir = bool(self.v_medir.get())

        def trabalho():
            """Resolve os 96 passos fora do laço do Tk, falando pela fila."""
            try:
                from bdgdcase.solucao import rodar_dia
                self.fila.put(('status', 'Resolvendo os 96 passos… '
                               '(uma rede urbana leva ~15 s%s)'
                               % (', o dobro medindo carga a carga'
                                  if medir else '')))
                r = rodar_dia(pasta, detalhado=True, medir_cargas=medir)
                rd = r.get('reguladores_desligados')
                if rd:
                    # O rodape e uma linha so; isto precisa ficar num lugar que
                    # nao some. E o mesmo texto do terminal e da aba de ajustes.
                    self._log('')
                    self._log('ATENCAO em %s: %d regulador(es) de tensao '
                              'DESLIGADOS neste dia.'
                              % (os.path.basename(os.path.normpath(pasta)),
                                 rd['quantos']))
                    self._log('   Com eles habilitados, %d dos %d passos nao '
                              'fechavam o balanco de potencia; desligados, %d.'
                              % (rd['passos_recusados_com'], r['passos'],
                                 rd['passos_recusados_sem']))
                    self._log('   Tensao minima do dia: %.3f -> %.3f pu; pico: '
                              '%.0f -> %.0f kW. O regulador continua no mapa, '
                              'com o tape em zero. Detalhe em "Ajustes do '
                              'cadastro".'
                              % (rd['v_min_com_pu'] or 0.0, rd['v_min_sem_pu'] or 0.0,
                                 rd['p_max_com_kw'] or 0.0, rd['p_max_sem_kw'] or 0.0))
                self.fila.put(('pronto', r))
            except Exception as exc:
                # O rodape e uma linha so, e a mensagem seguinte o apaga; ele
                # ainda some junto com a barra quando se volta para a aba de
                # preparar. O `traceback` ia para a saida padrao, que numa
                # janela aberta por atalho nao existe.
                #
                # Resultado: a PRIMEIRA falha de uma sessao nao deixava rastro
                # nenhum. Isso importa porque, depois de uma violacao de acesso,
                # o motor do OpenDSS passa a recusar tudo repetindo a mensagem
                # velha — e a unica forma de achar a causa e a primeira falha.
                # Mandar dizer "procure no registro" so vale se ela estiver la.
                self.fila.put(('status', 'ERRO ao simular %s — o registro da '
                               'aba Preparar tem o texto inteiro.'
                               % os.path.basename(os.path.normpath(pasta))))
                self._log('')
                self._log('ERRO ao simular %s' % pasta)
                for linha in str(exc).splitlines():
                    self._log('   %s' % linha)
                for linha in traceback.format_exc().splitlines()[-4:]:
                    self._log('   %s' % linha)
                traceback.print_exc()
            finally:
                self.fila.put(('fim', None))

        threading.Thread(target=trabalho, daemon=True).start()


def abrir_janela(pasta=None, medir_cargas=False, aba='mapa'):
    """Abre A janela. Bloqueia até o usuário fechá-la.

    Uma raiz Tk, uma janela, cinco abas. `bdgdcase gui` e `bdgdcase mapa`
    chamam esta mesma função e mudam só a aba inicial — a diferença entre os
    dois comandos é por onde se entra, não que programa se abre.
    """
    tk, _, _, _ = _tk()
    root = tk.Tk()
    janela = _Janela(root, pasta, aba=aba)
    if medir_cargas:
        janela.v_medir.set(True)
    root.mainloop()


def abrir_mapa(pasta=None, medir_cargas=False):
    """Abre a janela já na aba do mapa — o que `bdgdcase mapa` faz."""
    abrir_janela(pasta, medir_cargas, aba='mapa')
