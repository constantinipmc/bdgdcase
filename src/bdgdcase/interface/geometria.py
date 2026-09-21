# -*- coding: utf-8 -*-
"""Ler um caso já resolvido, e responder perguntas sobre ele.

`_Geometria` é a camada entre os arquivos de um caso — o `.dss`, o `BusCoords`,
os `Cadastro_*.csv`, o resultado da simulação — e as perguntas que uma pessoa
faz olhando para o mapa: que tensão tem esta barra, o que está pendurado nela,
de onde veio esta carga, qual foi o tape do regulador às 19h.

Ela não desenha nada, e é de propósito: o mapa, o inspetor e o resumo fazem as
mesmas perguntas e dão respostas em formatos diferentes. Enquanto a resposta
mora aqui, as três concordam entre si — quando a resposta se duplica, elas
divergem, e a divergência aparece como um número que muda ao trocar de aba.

## As faixas de tensão são do PRODIST

`FAIXAS` e `_faixa` implementam o Módulo 8: adequada, precária, crítica. Os
limites dependem da tensão nominal — 220/127 não tem a mesma faixa que 380/220
— e é por isso que a classificação precisa da barra, não só do valor em pu.

`PU_MORTO` separa fase desligada de fase com problema. Uma fase que não existe
naquela barra lê zero, e zero não é subtensão: é ausência. Tratar as duas como
a mesma coisa pinta de vermelho meia cidade que está bem.
"""
from __future__ import annotations

from bdgdcase.solucao import PU_FASE_ATIVA

__all__ = ['PRODIST', 'FAIXAS', 'CORES_FAIXA', 'V_LIMITES',
           'PU_MORTO', 'nivel_de', '_Geometria']


#: PRODIST Módulo 8, Anexo 8.A — faixas de classificação da tensão em regime
#: permanente, como fração da tensão de referência:
#: ``(crítica abaixo de, precária abaixo de, adequada até, precária até)``.
#:
#: As duas réguas existem porque a norma cobra uma de cada nível, e a diferença
#: não é pequena: 0,925 pu é **adequada** em baixa e **precária** em média.
#: Julgar a rede inteira por uma tabela só pinta de amarelo um bairro de baixa
#: que a norma considera em ordem.
#:
#: **Média** (Tabela 3, acima de 2,3 kV e abaixo de 69 kV) não tem faixa
#: precária superior: acima de 1,05 TR passa direto a crítica. O quarto valor
#: repete o terceiro, e a faixa fica vazia.
#:
#: **Baixa** (Tabela 5, sistema 380/220 V) tem, e é estreita: a norma dá os
#: limites em volts — adequada de 350 a 399 V, precária de 331 a 350 **ou de
#: 399 a 403**, crítica fora disso —, e são esses volts que estão escritos
#: abaixo, divididos pelos 380 V nominais de linha, que é a base do `pu` do
#: OpenDSS. Escrever a divisão em vez do decimal deixa a origem à vista.
#:
#: A norma tem uma tabela por tensão nominal de baixa (220/127, 440/220,
#: 208/120…), e as diferenças entre elas ficam na terceira casa do `pu`. Usa-se
#: a de 380/220 por ser o sistema predominante nos alimentadores em questão.
PRODIST = {
    'mt': (0.90, 0.93, 1.05, 1.05),
    'bt': (331 / 380., 350 / 380., 399 / 380., 403 / 380.),
}

#: Onde a norma separa as tabelas de média e de baixa, em kV de linha. São
#: 2,3 kV, e não 1 kV: a Tabela 3 vale acima de 2,3 kV, e as de baixa valem
#: para "tensão nominal igual ou inferior a 2,3 kV".
KV_LIMITE_BT = 2.3

#: Nome de cada faixa, na ordem em que o mapa as numera.
FAIXAS = ('adequada', 'precária', 'crítica')

#: Cor de cada faixa no mapa. Mais saturadas que as do texto: sobre imagem de
#: satélite, o verde-escuro do painel vira mancha e o âmbar vira marrom.
CORES_FAIXA = ('#21a35a', '#f5c518', '#e03131')

#: Cinza da barra sem nenhuma fase energizada. É o `bad` do mapa de cores, e
#: precisa de nome porque a legenda também o desenha: ausência de tensão não é
#: uma quarta faixa do PRODIST — é a falta das três —, e por isso tem linha
#: própria na legenda em vez de virar um quarto bloco na régua de cor.
CINZA_SEM_TENSAO = '#c8c8c8'

#: Faixa de tensão desenhada nos gráficos da aba de resumo, em pu. Não é a régua
#: da cor do mapa — essa é o PRODIST acima —, é só o enquadramento do eixo.
V_LIMITES = (0.90, 1.05)

#: Prefixo do nome da linha → camada. É como o gerador nomeia, e não há campo no
#: OpenDSS que diga "isto é um ramal de ligação".
TIPO_POR_PREFIXO = {'mt': 'mt', 'bt': 'bt', 'rl': 'bt', 'sw': 'chaves'}


def tipo_do_trecho(nome):
    """A camada de um trecho, pelo nome com que o gerador o batizou.

    O prefixo basta para quase tudo: `MT_`, `BT_`, `RL_` (ramal de ligação, que
    é baixa) e `SW_` (chave). O jumper é a exceção, e ela custou caro: ele se
    chama `JUMP_<nível>_…`, e olhar só o primeiro pedaço punha todo jumper na
    média — inclusive o `JUMP_TR_`, que é justamente o que liga o secundário de
    um transformador ao circuito de baixa dele.

    O efeito era duplo, e os dois sintomas eram do mapa e não da rede: o realce
    do circuito parava no transformador, porque a travessia da baixa não
    atravessa um trecho classificado como média; e o jumper aparecia desenhado
    com a cor da média, no meio da baixa.

    `JUMP_TR_` conta como baixa. Ele une o secundário — que é nó de baixa — ao
    condutor de baixa do mesmo poste; o que passa por ele é a corrente daquele
    circuito.
    """
    partes = str(nome).lower().split('_')
    chave = partes[0] if partes else ''
    if chave == 'jump' and len(partes) > 1:
        chave = {'tr': 'bt'}.get(partes[1], partes[1])
    return TIPO_POR_PREFIXO.get(chave, 'mt')

#: Limiar de fase conectada. Vem de `solucao` para que mapa e séries agregadas
#: não possam divergir: o dia dizer 0,99 pu e o mapa pintar vermelho no mesmo
#: instante seria pior que qualquer um dos dois errar sozinho.
PU_MORTO = PU_FASE_ATIVA

#: Quantos elementos o painel do ponto lista antes de resumir. Um poste com
#: cinquenta unidades existe, e listar as cinquenta faria o painel do ponto
#: virar a ficha de todas elas — que é justamente o que as telas de detalhe
#: existem para evitar.
LIMITE_LISTA = 25

def _hhmm(hora):
    """O passo do dia como hora de relógio — 42 vira '10:30'."""
    h = int(hora)
    return '%02d:%02d' % (h, round((hora - h) * 60))

def _faixa(pu, nivel='bt'):
    """Estilo de um valor de tensão, pela tabela do PRODIST do nível dado.

    Devolve '', 'bom', 'atencao' ou 'ruim' — os nomes das tags do painel, que
    correspondem a adequada, precária e crítica.

    O `nivel` tem padrão 'bt' por ser o caso mais comum num alimentador de
    distribuição, mas quem tem a barra em mão deve passá-lo: a mesma tensão
    muda de faixa entre os dois níveis.

    Sobretensão tem tratamento diferente nos dois: em baixa há uma faixa
    precária estreita acima da adequada, e em média não há nenhuma.

    Uma ressalva que o painel repete: isto classifica **um instante**. O
    enquadramento da norma se apura sobre uma janela de leituras, com índices
    próprios, e não sobre uma foto.
    """
    if pu is None or pu != pu:
        return ''
    critica, precaria, adequada, precaria_alta = PRODIST.get(nivel,
                                                             PRODIST['bt'])
    if pu < critica or pu > precaria_alta:
        return 'ruim'
    if pu < precaria or pu > adequada:
        return 'atencao'
    return 'bom'

def nivel_de(kv_linha):
    """'mt' ou 'bt' pela tensão nominal de linha, no corte de 2,3 kV da norma."""
    return 'mt' if (kv_linha or 0.0) > KV_LIMITE_BT else 'bt'

class _Geometria:
    """Traduz o resultado detalhado em coisas que se desenham.

    Fica separado da janela porque é a parte testável: dado um resultado, quais
    barras têm lugar no mapa, qual segmento liga o quê, e qual cor cada um
    recebe num instante. Nada aqui toca em widget.
    """

    def __init__(self, resultado):
        """Indexa um resultado de simulação para as perguntas do mapa.

        O trabalho todo acontece aqui, uma vez: montar os índices de barra,
        trecho, carga, transformador, regulador e geração. Depois disso cada
        pergunta é uma consulta, e não uma varredura — o que importa porque o
        mapa refaz as perguntas a cada um dos 96 passos, e ao arrastar a régua
        do tempo.
        """
        import numpy as np

        self.r = resultado
        coord = {k.lower(): v for k, v in resultado['coordenadas'].items()}

        # Só entram barras com coordenada. As sem coordenada existem no circuito
        # (o OpenDSS as resolve), mas não têm onde ser desenhadas.
        self.barras = [b for b in resultado['barras'] if b.lower() in coord]
        self.indice = {b: i for i, b in enumerate(self.barras)}

        # `xy` é Web Mercator, em metros — é nele que tudo se desenha, porque é
        # a projeção dos azulejos: assim a rede assenta sobre a imagem em
        # qualquer ampliação, e o aspecto do eixo é 1:1 sem correção nenhuma.
        # `lonlat` fica só para mostrar a posição no painel, que é onde a
        # unidade que interessa é o grau.
        from bdgdcase.interface.azulejos import merc
        self.lonlat = np.array([coord[b.lower()] for b in self.barras],
                               dtype=float)
        self.xy = np.array([merc(lo, la) for lo, la in self.lonlat],
                           dtype=float)

        # Tensão por barra: os DOIS extremos entre as fases vivas, e não a
        # média — um desequilíbrio some na média, e é justamente o que se
        # procura.
        #
        # As duas pontas, e não só a mais baixa: guardar apenas o mínimo faz a
        # barra ficar cega para sobretensão. Uma barra com as fases em 1,0513,
        # 1,0019 e 1,0019 pu era reduzida a 1,0019 e saía "adequada", com a
        # primeira fase já precária por sobretensão — a violação existia e não
        # aparecia em lugar nenhum.
        n_passos = resultado['v_no_pu'].shape[0]
        v = np.full((n_passos, len(self.barras)), np.nan, dtype=np.float32)
        va = np.full((n_passos, len(self.barras)), np.nan, dtype=np.float32)
        # Fase viva é a que tem caminho até a fonte (`no_conectado`, que a
        # solução grava). A solta tem tensão por acoplamento — 0,555 pu no
        # IAL02 — e nenhum limiar a separa com segurança; `PU_MORTO` fica
        # como recuo para resultado gravado antes da máscara existir.
        conectado = resultado.get('no_conectado')
        if conectado is not None and len(conectado) != len(resultado['no_para_barra']):
            conectado = None
        for k, (ib, _f) in enumerate(zip(resultado['no_para_barra'],
                                         resultado['fase_do_no'])):
            nome = resultado['barras'][ib]
            j = self.indice.get(nome)
            if j is None:
                continue
            coluna = resultado['v_no_pu'][:, k]
            if conectado is not None:
                viva = np.isfinite(coluna) & (coluna > 0.0) if conectado[k]                     else np.zeros_like(coluna, dtype=bool)
            else:
                viva = coluna > PU_MORTO
            if not viva.any():
                continue
            atual = v[:, j]
            v[:, j] = np.where(viva & (np.isnan(atual) | (coluna < atual)),
                               coluna, atual)
            alto = va[:, j]
            va[:, j] = np.where(viva & (np.isnan(alto) | (coluna > alto)),
                                coluna, alto)
        self.v_barra = v
        self.v_barra_alta = va

        # Segmentos: só os que têm as duas pontas no mapa.
        segs, carga = [], []
        norm = resultado['norm_amps']
        for k, (b1, b2) in enumerate(resultado['linhas']):
            i, j = self.indice.get(b1), self.indice.get(b2)
            if i is None or j is None:
                continue
            segs.append([self.xy[i], self.xy[j]])
            carga.append(k)
        # Nível de tensão de cada barra, para escolher a tabela do PRODIST.
        # Sem `kv_base_barra` — pastas resolvidas por uma versão anterior —
        # tudo cai em baixa, que é a tabela mais permissiva: errar para o lado
        # de não acusar violação é melhor que inventar uma.
        bases = resultado.get('kv_base_barra') or []
        por_nome = {resultado['barras'][i]: (bases[i] if i < len(bases) else 0.0)
                    for i in range(len(resultado['barras']))}
        self.kv_base = np.array([por_nome.get(b, 0.0) for b in self.barras],
                                dtype=float)
        # O nome manda quando ele diz. A base de tensão vem do `CalcVoltageBases`
        # do OpenDSS, que a escolhe pela tensão CALCULADA — e numa barra sem
        # nenhuma fase energizada não há tensão calculada, então ele atribui a
        # da fonte. O resultado eram barras de baixa entrando na camada de
        # média: desenhadas no dobro do tamanho e por cima da baixa, em cinza,
        # tapando os ramais vivos que dividiam a coordenada com elas. Medido:
        # 112 barras num alimentador, 35 noutro, 7 no de exemplo.
        #
        # O prefixo `BT_` é do próprio gerador, e é a mesma convenção em que
        # `_classificar_trechos` já se apoia: o OpenDSS não tem campo que diga
        # de que lado do transformador uma barra está.
        self.nivel = np.array(
            ['bt' if str(b).lower().startswith('bt_') else nivel_de(k)
             for b, k in zip(self.barras, self.kv_base)], dtype=object)

        self.segmentos = np.array(segs) if segs else np.zeros((0, 2, 2))
        self.idx_linha = np.array(carga, dtype=int)
        amp = resultado['amp_linha'][:, self.idx_linha] if len(carga) else None
        base = np.array([max(norm[k], 1e-9) for k in self.idx_linha])
        self.carreg = (100.0 * amp / base) if amp is not None else None
        self._indexar_elementos()
        self._classificar_trechos()
        self._posicionar_marcadores()
        self._montar_vizinhanca_bt()

    def _classificar_trechos(self):
        """Cada segmento desenhado vira 'mt', 'bt' ou 'chave'.

        Pelo prefixo do nome, que é como o gerador os distingue — `rl_` é ramal
        de ligação e entra em BT, `sw_` é chave. O OpenDSS não tem campo que
        diga isso, e a tensão de base não separa chave de cabo.
        """
        import numpy as np
        nomes = self.r.get('nomes_linha') or []
        tipos = []
        for k in self.idx_linha:
            nome = nomes[k] if k < len(nomes) else ''
            tipos.append(tipo_do_trecho(nome))
        self.tipo_trecho = np.array(tipos, dtype=object)
        self.indice_por_tipo = {t: np.where(self.tipo_trecho == t)[0]
                                for t in ('mt', 'bt', 'chaves')}

    def _posicionar_marcadores(self):
        """Onde desenhar transformador, chave e geração."""
        import numpy as np
        inv = self.r.get('inventario') or {}

        def _xy(nomes_barra):
            """As coordenadas das barras pedidas, e os índices delas."""
            pontos = [self.indice[b] for b in nomes_barra if b in self.indice]
            return (self.xy[pontos] if pontos else np.zeros((0, 2))), pontos

        # O transformador é marcado no secundário: é de lá que sai o circuito
        # BT, e é o que se procura ao clicar nele.
        self.trafos = [t for t in inv.get('trafos', ())
                       if len(t['buses']) > 1 and t['buses'][1] in self.indice]
        self.trafo_xy, self.trafo_ponto = _xy([t['buses'][1]
                                               for t in self.trafos])
        self.gd = [g for g in inv.get('gd', ()) if g['bus'] in self.indice]
        self.gd_xy, _ = _xy([g['bus'] for g in self.gd])

        # Capacitor tem uma barra. Regulador tem duas, e o que se marca é a de
        # montante — é onde o equipamento está no poste, e é de lá que se
        # pergunta o que ele faz com a tensão daqui para a frente.
        self.capacitores = [c for c in inv.get('capacitores', ())
                            if c.get('bus') in self.indice]
        self.cap_xy, _ = _xy([c['bus'] for c in self.capacitores])
        self.reguladores = [g for g in inv.get('reguladores', ())
                            if g.get('buses') and g['buses'][0] in self.indice]
        self.reg_xy, _ = _xy([g['buses'][0] for g in self.reguladores])

        chaves = self.indice_por_tipo.get('chaves')
        if chaves is not None and len(chaves):
            self.chave_xy = self.segmentos[chaves].mean(axis=1)
        else:
            self.chave_xy = np.zeros((0, 2))

    def _montar_vizinhanca_bt(self):
        """Lista de adjacência só com trechos de baixa tensão.

        Serve ao realce: dado o secundário de um transformador, andar por ela
        dá exatamente o circuito que aquele transformador alimenta.
        """
        from collections import defaultdict
        self.vizinhos_bt = defaultdict(list)
        for pos in self.indice_por_tipo.get('bt', ()):
            k = int(self.idx_linha[pos])
            b1, b2 = self.r['linhas'][k]
            self.vizinhos_bt[b1].append((b2, pos))
            self.vizinhos_bt[b2].append((b1, pos))

    def circuito_bt(self, nome_trafo):
        """Barras e trechos que o transformador alimenta em baixa tensão.

        Busca em largura a partir do secundário, andando só por trechos BT — a
        travessia para sozinha no primário do próximo transformador, porque
        nenhum trecho BT sai de lá.

        Devolve ``(índices de barra, índices de segmento)``.
        """
        alvo = next((t for t in self.trafos if t['nome'] == nome_trafo), None)
        if alvo is None:
            return [], []
        raiz = alvo['buses'][1]
        vistas, trechos, fila = {raiz}, set(), [raiz]
        while fila:
            atual = fila.pop()
            for vizinha, pos in self.vizinhos_bt.get(atual, ()):
                trechos.add(pos)
                if vizinha not in vistas:
                    vistas.add(vizinha)
                    fila.append(vizinha)
        pontos = [self.indice[b] for b in vistas if b in self.indice]
        return pontos, sorted(trechos)

    def trafos_da_barra(self, nome):
        """Transformadores cujo secundário é esta barra."""
        return [t for t in self.trafos if t['buses'][1] == nome]

    def _indexar_elementos(self):
        """Agrupa por barra tudo o que o inspetor precisa achar a partir dela.

        Percorrer as listas a cada clique custaria pouco em TRO05 e muito num
        alimentador de doze mil cargas. Mas o clique não é o caso difícil: o
        painel é remontado **a cada passo**, inclusive nos 120 ms da animação,
        e ali uma varredura dos quarenta e três mil nós ou das quinze mil
        linhas acontece quatro vezes por segundo. Os índices abaixo são
        montados uma vez, na abertura.

        As chaves vêm de `self.r`, e não de `self.barras`: a lista filtrada só
        tem as barras com coordenada, e quem pergunta aqui pergunta pelo nome
        que o OpenDSS devolveu.
        """
        from collections import defaultdict
        inv = self.r.get('inventario') or {}
        self.por_barra = defaultdict(lambda: {'cargas': [], 'trafos': [],
                                              'gd': [], 'capacitores': [],
                                              'reguladores': []})
        for c in inv.get('cargas', ()):
            self.por_barra[c['bus']]['cargas'].append(c)
        for g in inv.get('gd', ()):
            self.por_barra[g['bus']]['gd'].append(g)
        for t in inv.get('trafos', ()):
            for b in t['buses']:
                self.por_barra[b]['trafos'].append(t)
        for c in inv.get('capacitores', ()):
            self.por_barra[c['bus']]['capacitores'].append(c)
        for g in inv.get('reguladores', ()):
            # Nas duas barras: quem clica no lado de jusante quer saber que a
            # tensão que está vendo passou por um regulador.
            for b in g.get('buses', ())[:2]:
                self.por_barra[b]['reguladores'].append(g)

        # barra → nós do circuito, para a tensão fase a fase
        self.nos_da_barra = defaultdict(list)
        for k, ib in enumerate(self.r['no_para_barra']):
            self.nos_da_barra[self.r['barras'][ib]].append(k)

        # barra → trechos ligados a ela. O conjunto `{b1, b2}` é o que impede
        # que um laço próprio (bus1 == bus2, que existe no cadastro) apareça
        # duas vezes na mesma barra.
        self.trechos_da_barra = defaultdict(list)
        for k, (b1, b2) in enumerate(self.r['linhas']):
            for b in {b1, b2}:
                self.trechos_da_barra[b].append((k, b1, b2))

        # nome do elemento → o elemento, para a navegação em profundidade
        self.carga_por_nome = {c['nome']: c for c in inv.get('cargas', ())}
        # Posição de cada carga em `pq_carga`, quando a medição foi pedida.
        self.medida_da_carga = {n.lower(): j for j, n
                                in enumerate(self.r.get('nomes_carga') or ())}
        self.trafo_por_nome = {t['nome']: t for t in inv.get('trafos', ())}
        self.gd_por_nome = {g['nome']: g for g in inv.get('gd', ())}
        self.capacitor_por_nome = {c['nome']: c
                                   for c in inv.get('capacitores', ())}
        self.regulador_por_nome = {g['nome']: g
                                   for g in inv.get('reguladores', ())}

    def vaos_de_regulador(self):
        """Os trechos de tronco que passam por dentro de um regulador.

        Um regulador é emitido como `Transformer`, e não como `Line` — então
        ele não entra em `segmentos`, que é o que o solver reporta como linha.
        O tronco, porém, passa por ele: as duas barras são pontos consecutivos
        da média, e entre elas há condutor.

        Sem isto o mapa abre um vão exatamente no regulador. Onde as duas
        barras dividem a coordenada o vão tem comprimento zero e ninguém nota;
        onde não dividem, ele mede: 80 m no alimentador que motivou a correção,
        52 m noutro.

        Um banco de três reguladores monofásicos rende três registros com o
        mesmo par de barras — o par é contado uma vez só.
        """
        import numpy as np
        vistos, segs = set(), []
        for reg in (self.regulador_por_nome or {}).values():
            buses = reg.get('buses') or []
            if len(buses) < 2:
                continue
            a, b = str(buses[0]).lower(), str(buses[1]).lower()
            if a == b or (a, b) in vistos or (b, a) in vistos:
                continue
            ia, ib = self.indice.get(a), self.indice.get(b)
            if ia is None or ib is None:
                continue
            vistos.add((a, b))
            segs.append([self.xy[ia], self.xy[ib]])
        return np.array(segs, dtype=float) if segs else np.zeros((0, 2, 2))

    def resumo_das_camadas(self, passo=0):
        """Quanto há de cada camada, para a legenda dizer ao lado do símbolo.

        Cabo em quilômetros, o resto em unidades. O número não é enfeite: um
        alimentador de 3 km e um de 127 km se parecem no mapa depois do
        enquadramento, e a diferença só aparece se estiver escrita.

        O comprimento vem do `length` que o caso declara em cada linha, e não
        da distância entre as coordenadas do desenho: a segunda erra sempre que
        o cadastro põe as duas pontas no mesmo poste, e nesta base isso é
        comum.

        `barras_sem_tensao` conta as que ficam cinzas naquele passo — as que não
        têm nenhuma fase acima de `PU_MORTO`. Elas não são violação de tensão,
        são ausência dela, e é por isso que têm entrada própria na legenda em
        vez de virarem uma quarta faixa do PRODIST.
        """
        import numpy as np

        km = self.r.get('km_linha') or []
        por_camada = {}
        for camada in ('mt', 'bt', 'chaves'):
            pos = self.indice_por_tipo.get(camada)
            total = 0.0
            if pos is not None and len(km):
                for p in pos:
                    k = self.idx_linha[p]
                    if k < len(km):
                        total += float(km[k])
            por_camada[camada] = total

        faixa = self.faixa(passo)
        inv = self.r.get('inventario') or {}
        return {
            'mt': ('%.1f km' % por_camada['mt']),
            'bt': ('%.1f km' % por_camada['bt']),
            'chaves': ('%d · %.1f km'
                       % (len(self.indice_por_tipo.get('chaves', ())),
                          por_camada['chaves'])),
            'trafos': str(len(self.trafo_por_nome)),
            'capacitores': str(inv.get('n_capacitores',
                                       len(self.capacitor_por_nome))),
            'reguladores': str(inv.get('n_reguladores',
                                       len(self.regulador_por_nome))),
            'gd': str(len(self.gd_por_nome)),
            'barras_mt': str(int((self.nivel == 'mt').sum())),
            'barras_bt': str(int((self.nivel == 'bt').sum())),
            'barras_sem_tensao': str(int(np.isnan(faixa).sum())),
        }

    def tensao(self, passo):
        """As tensões em pu de todas as barras, naquele passo."""
        return self.v_barra[passo]

    def barras_do_nivel(self, nivel):
        """Máscara booleana das barras de um nível ('mt' ou 'bt')."""
        return self.nivel == nivel

    def tensao_alta(self, passo):
        """A fase mais alta de cada barra no instante. NaN se nenhuma viva."""
        return self.v_barra_alta[passo]

    def faixa(self, passo):
        """Faixa do PRODIST de cada barra: 0 adequada, 1 precária, 2 crítica.

        NaN para barra sem fase energizada — que não é violação de tensão, e
        sim ausência dela; o mapa a pinta de cinza.

        A faixa é a **pior entre as fases**, e por isso olha as duas pontas: a
        mais baixa acusa subtensão e a mais alta acusa sobretensão. Classificar
        só pela mais baixa deixava passar uma fase acima do limite sempre que
        outra estivesse dentro da faixa — que é o caso comum, já que o
        desequilíbrio é justamente o que empurra uma fase para cima.

        Basta olhar os extremos: a classificação piora nas duas pontas e é
        melhor no meio, então nenhuma fase intermediária pode ser pior que o
        mínimo ou o máximo.

        A régua é a do nível de cada barra, e não uma só: 0,925 pu é adequada
        em baixa e precária em média.
        """
        import numpy as np
        baixa, alta_v = self.tensao(passo), self.tensao_alta(passo)
        f = np.full(len(baixa), np.nan)
        viva = ~np.isnan(baixa)
        for nome, (critica, precaria, adequada, alta) in PRODIST.items():
            m = viva & (self.nivel == nome)
            if not m.any():
                continue
            f[m] = 0.0
            f[m & ((baixa < precaria) | (alta_v > adequada))] = 1.0
            f[m & ((baixa < critica) | (alta_v > alta))] = 2.0
        return f

    def contar_faixas(self, passo):
        """(adequadas, precárias, críticas, sem tensão) no instante."""
        import numpy as np
        f = self.faixa(passo)
        return (int((f == 0).sum()), int((f == 1).sum()),
                int((f == 2).sum()), int(np.isnan(f).sum()))

    def carregamento(self, passo):
        """O carregamento de cada trecho naquele passo, ou zeros se não houver."""
        import numpy as np
        return (self.carreg[passo] if self.carreg is not None
                else np.zeros(len(self.segmentos)))

    def barra_mais_proxima(self, x, y, atual=None, niveis=None):
        """Índice da barra mais perto de (x, y). Para o clique no mapa.

        `niveis` restringe a busca aos níveis dados. Serve para o clique
        respeitar o que está visível: com as barras de média desligadas, clicar
        não pode selecionar uma delas — seria escolher o que não se vê.

        Barras empilhadas no mesmo ponto são a regra, não a exceção: no
        alimentador de exemplo, um terço das posições carrega mais de uma — os
        dois lados de um transformador, vários ramais saindo do mesmo poste,
        até cinco no mesmo lugar. Devolver sempre a primeira deixaria as outras
        inalcançáveis, então clicar de novo no mesmo ponto passa para a
        seguinte.
        """
        import numpy as np
        if not len(self.xy):
            return None
        d = (self.xy[:, 0] - x) ** 2 + (self.xy[:, 1] - y) ** 2
        if niveis is not None:
            permitido = np.isin(self.nivel, list(niveis))
            if not permitido.any():
                return None
            d = np.where(permitido, d, np.inf)
        j = int(np.argmin(d))
        grupo = np.where((self.xy[:, 0] == self.xy[j, 0])
                         & (self.xy[:, 1] == self.xy[j, 1]))[0]
        if len(grupo) > 1 and atual is not None and atual in grupo:
            return int(grupo[(list(grupo).index(atual) + 1) % len(grupo)])
        return int(grupo[0])

    def empilhadas(self, j):
        """Quantas barras dividem a posição da barra `j`."""
        return int(((self.xy[:, 0] == self.xy[j, 0])
                    & (self.xy[:, 1] == self.xy[j, 1])).sum())

    # ── O que o inspetor mostra ──────────────────────────────────────────────
    #
    # `blocos()` devolve estrutura, não texto formatado: a janela decide
    # tipografia, e `detalhe()` deriva o texto puro para quem só quer ler. Os
    # tipos são poucos de propósito:
    #
    #   ('secao',   titulo, nota)                 cabeçalho de bloco
    #   ('par',     rótulo, valor, estilo)         rótulo e valor
    #   ('link',    rótulo, valor, estilo, alvo)   um par em que se clica
    #   ('grafico', titulo, valores, nota, unidade)  a série do dia, embutida
    #   ('nota',    texto)                         linha em cinza, largura toda
    #   ('vazio',)                                 respiro
    #
    # `estilo` é '', 'bom', 'atencao' ou 'ruim' — só onde o número é
    # diagnóstico, e não em toda linha, senão a cor deixa de significar algo.
    #
    # `alvo` é ('uc', nome), ('trafo', nome), ('gd', nome) ou ('ponto', j): é o
    # que a janela abre ao clique. Fica no bloco, e não num mapa à parte, para
    # que a tela e a navegação nasçam do mesmo lugar e não possam discordar.

    def blocos(self, j, passo):
        """O que se sabe da barra `j` no instante `passo`, em blocos."""
        import numpy as np

        nome = self.barras[j]
        nivel = self.nivel[j]
        b = [('secao', 'Barra',
              'média tensão' if nivel == 'mt' else 'baixa tensão'),
             ('par', 'identificador', nome, ''),
             ('par', 'tensão nominal', '%.4g kV' % self.kv_base[j], ''),
             ('par', 'latitude, longitude',
              '%.6f, %.6f' % (self.lonlat[j][1], self.lonlat[j][0]), '')]
        n = self.empilhadas(j)
        if n > 1:
            b.append(('nota', '%d barras neste ponto — clique de novo para as '
                              'outras' % n))

        b.append(('vazio',))
        fases = self._fases_da_barra(nome, passo)
        if fases:
            b.append(('secao', 'Tensão', _hhmm(self.r['horas'][passo])))
            # Cada fase com a SUA faixa: é o que mostra de imediato a fase
            # sozinha fora do limite, sem depender de quem lê comparar com os
            # cortes de cabeça.
            for fase, pu in sorted(fases):
                b.append(('par', 'fase %d' % fase, '%.4f pu' % pu,
                          _faixa(pu, nivel)))
            # A pior é a de pior FAIXA, e não a mais baixa. Numa barra com
            # 1,0513 / 1,0019 / 1,0019, a mais baixa está adequada e a pior é a
            # de cima, precária por sobretensão.
            severidade = {'': 0, 'bom': 0, 'atencao': 1, 'ruim': 2}
            pior = max((pu for _, pu in fases),
                       key=lambda x: (severidade[_faixa(x, nivel)],
                                      abs(x - 1.0)))
            b.append(('par', 'pior fase', '%.4f pu' % pior,
                      _faixa(pior, nivel)))
            # A faixa vem escrita, e não só colorida: a cor diz que há algo, o
            # nome diz o quê. E o corte é o do nível desta barra — em média,
            # 0,93 e 0,90; em baixa, 0,92 e 0,87.
            critica, precaria, adequada, alta = PRODIST[nivel]
            estado = _faixa(pior, nivel)
            b.append(('par', 'faixa (PRODIST M8)',
                      {'bom': 'adequada', 'atencao': 'precária',
                       'ruim': 'crítica'}.get(estado, '—'), estado))
            # Os cortes por extenso, os DESTA barra: em baixa há faixa precária
            # dos dois lados da adequada, em média só do lado de baixo, e quem
            # olha o painel não tem de lembrar qual é qual.
            faixas_altas = ('' if alta <= adequada
                            else ' e de %.3f a %.3f' % (adequada, alta))
            b.append(('nota', 'nesta barra: adequada de %.3f a %.3f pu, '
                              'precária de %.3f a %.3f%s, crítica fora disso. '
                              'É a leitura DESTE instante — o enquadramento da '
                              'norma se apura sobre uma janela de leituras'
                              % (precaria, adequada, critica, precaria,
                                 faixas_altas)))
            if len(fases) >= 2:
                m = float(np.mean([pu for _, pu in fases]))
                desq = 100 * max(abs(pu - m) for _, pu in fases) / m if m else 0
                b.append(('par', 'desequilíbrio', '%.2f %%' % desq,
                          'atencao' if desq > 2.0 else ''))
        else:
            b.append(('secao', 'Tensão', _hhmm(self.r['horas'][passo])))
            b.append(('nota', 'Sem fase conectada neste instante.'))

        b += self._blocos_trechos(nome, passo)
        b += self._blocos_consumo(nome)
        return b

    def _fases_da_barra(self, nome, passo):
        """As fases daquela barra, com a tensão de cada uma.

        Fase abaixo de `PU_MORTO` não entra como subtensão: é fase que não
        existe ali. Numa lateral monofásica os outros dois nós flutuam,
        acoplados só capacitivamente, e contá-los como tensão ruim pinta de
        vermelho uma rede sã.
        """
        fases = []
        conectado = self.r.get('no_conectado')
        if conectado is not None and len(conectado) != len(self.r['no_para_barra']):
            conectado = None
        for k in self.nos_da_barra.get(nome, ()):
            pu = float(self.r['v_no_pu'][passo, k])
            viva = (bool(conectado[k]) and pu > 0.0) if conectado is not None                 else pu > PU_MORTO
            if viva:
                fases.append((self.r['fase_do_no'][k], pu))
        return fases

    def _blocos_trechos(self, nome, passo):
        """Os trechos que chegam naquela barra, com corrente e carregamento."""
        ligadas = self.trechos_da_barra.get(nome, ())
        if not ligadas:
            return []
        out = [('vazio',), ('secao', 'Trechos ligados', '%d' % len(ligadas))]
        for k, b1, b2 in ligadas[:8]:
            amp = float(self.r['amp_linha'][passo, k])
            norm = self.r['norm_amps'][k]
            pct = (100 * amp / norm) if norm else 0.0
            outro = b2 if b1 == nome else b1
            estilo = 'ruim' if pct >= 100 else ('atencao' if pct >= 80 else '')
            # A ampacidade vai junto de propósito. Sem ela, "1956%" lê como
            # sobrecarga; com ela — "176 A de 9 A" — fica claro que o
            # implausível é o cadastro do condutor, não a corrente. É a mesma
            # lição das fases flutuantes: o número sozinho engana.
            out.append(('par', outro, '%.1f A de %.0f A · %.0f%%'
                        % (amp, norm, pct), estilo))
        if len(ligadas) > 8:
            out.append(('nota', '… e mais %d' % (len(ligadas) - 8)))
        return out

    # ── As telas de detalhe ───────────────────────────────
    #
    # O ponto responde "o que existe aqui"; estas respondem "o que é isto". A
    # separação é o que permite listar cinquenta unidades num poste sem que o
    # painel do ponto vire um despejo — cada uma é um link, e a ficha só é
    # montada para a que se abriu.

    #: Rótulo dos meses do consumo, na ordem da BDGD.
    MESES = ('jan', 'fev', 'mar', 'abr', 'mai', 'jun',
             'jul', 'ago', 'set', 'out', 'nov', 'dez')

    def blocos_de(self, foco, passo):
        """A ficha do elemento em foco: ('uc'|'trafo'|'gd', nome)."""
        tipo, nome = foco
        if tipo == 'uc':
            return self._blocos_uc(nome, passo)
        if tipo == 'trafo':
            return self._blocos_trafo(nome, passo)
        if tipo == 'gd':
            return self._blocos_gd(nome, passo)
        if tipo == 'capacitor':
            return self._blocos_capacitor(nome, passo)
        if tipo == 'regulador':
            return self._blocos_regulador(nome, passo)
        return [('nota', 'Nada a mostrar.')]

    def curva_da_carga(self, nome):
        """(valores por passo, nota) da potência ativa de uma carga.

        Duas procedências, e o painel precisa dizer qual está mostrando.
        Medida, é o que o fluxo de potência serviu. Nominal, é a placa vezes a
        forma da curva da classe — o que o cadastro afirma, e não o que
        aconteceu: as cargas são de impedância parcialmente constante, de modo
        que a potência servida acompanha a tensão. No alimentador de exemplo a
        diferença fica na casa de 1%, mas cresce onde a tensão cai — que é onde
        se olha.
        """
        j = self.medida_da_carga.get(nome.lower())
        pq = self.r.get('pq_carga')
        if j is not None and pq is not None:
            return [float(x) for x in pq[:, j, 0]], 'medida no fluxo de potência'

        c = self.carga_por_nome.get(nome)
        if not c:
            return None, ''
        mult = (self.r.get('curvas') or {}).get(str(c.get('curva', '')).lower())
        if not mult:
            return None, ''
        n = self.v_barra.shape[0]
        return ([c['kw'] * m for m in mult[:n]],
                'nominal: placa × curva da classe, não a servida')

    def _cad(self, dicionario):
        """A parte de cadastro de um registro, ou um dicionário vazio.

        O cadastro pode faltar — caso gerado por versão antiga, ou pasta em que
        o `Cadastro_*.csv` não veio junto. Faltar é caminho previsto: o
        inspetor mostra menos, e não quebra.
        """
        return (dicionario or {}).get('cadastro') or {}

    def _blocos_consumo_mensal(self, cad):
        """Os doze meses, quando o cadastro os trouxe."""
        valores = []
        for i, rotulo in enumerate(self.MESES):
            try:
                valores.append((rotulo, float(cad.get('ene_%02d' % (i + 1)))))
            except (TypeError, ValueError):
                valores.append((rotulo, None))
        if not any(v is not None for _, v in valores):
            return []
        total = sum(v for _, v in valores if v)
        out = [('vazio',), ('secao', 'Consumo mensal', 'kWh'),
               ('par', 'no ano', '%.0f kWh' % total, '')]
        for rotulo, v in valores:
            out.append(('par', '  %s' % rotulo,
                        '—' if v is None else '%.0f' % v, ''))
        return out

    def _fases_pedidas(self, cad):
        """As fases que o cadastro declara para aquela unidade."""
        return [x for x in str(cad.get('fas_con', '')).upper() if x in 'ABC']

    def _blocos_uc(self, nome, passo):
        """A ficha de uma carga: quem ela é, de onde veio, quanto consome.

        Este é o bloco que justifica o `Cadastro_*.csv` existir. Ele não diz só
        o valor da carga — diz de quantas unidades ele saiu, quais são, e qual
        premissa entrou onde o cadastro estava vazio.
        """
        c = self.carga_por_nome.get(nome)
        if not c:
            return [('nota', 'Esta carga não está no circuito.')]
        cad = self._cad(c)

        out = [('secao', 'Unidade consumidora', cad.get('tipo') or c['classe'])]
        cod = cad.get('cod_id')
        if cod:
            # O identificador da BDGD é um SHA-256 de 64 caracteres. Inteiro não
            # cabe na coluna e não se lê; os primeiros dígitos bastam para
            # reconhecer, e o resto está no arquivo para quem for cruzar dados.
            out.append(('par', 'identificador BDGD', '%s…' % cod[:20], ''))
        out.append(('par', 'elemento', nome, ''))
        if cad.get('poste'):
            out.append(('par', 'poste', cad['poste'], ''))
        out.append(('link', 'barra', c['bus'], '', ('barra', c['bus'])))

        rotulos = (('classe de consumo', 'classe_consumo'),
                   ('subclasse', 'clas_sub'),
                   ('tipo de edificação', 'tip_edificacao'),
                   ('grupo tarifário', 'gru_tar'),
                   ('subgrupo de tensão', 'gru_ten'),
                   ('tensão de fornecimento', 'ten_forn'),
                   ('CNAE', 'cnae'),
                   ('área', 'are_loc'),
                   ('município (IBGE)', 'mun'))
        presentes = [(r, cad[k]) for r, k in rotulos if cad.get(k)]
        if presentes:
            out += [('vazio',), ('secao', 'Cadastro', None)]
            out += [('par', r, v, '') for r, v in presentes]

        out += [('vazio',), ('secao', 'Ligação', None)]
        pedidas = self._fases_pedidas(cad)
        ligadas = str(cad.get('fases_ligadas', ''))
        curada = bool(pedidas and ligadas and len(ligadas) != len(pedidas))
        if pedidas:
            out.append(('par', 'fases no cadastro', cad['fas_con'], ''))
        out.append(('par', 'fases ligadas', ligadas or '%d' % c['fases'],
                    'atencao' if curada else ''))
        if curada:
            out.append(('nota', 'a unidade pede fase que não existe no ponto de '
                                'derivação — a ligação foi ajustada'))
        if cad.get('car_inst_kw'):
            out.append(('par', 'carga instalada',
                        '%s kW' % cad['car_inst_kw'], ''))
        out.append(('par', 'demanda nominal',
                    '%.3f kW · %.3f kVAr' % (c['kw'], c['kvar']), ''))
        out.append(('par', 'tensão', '%.4g kV' % c['kv'], ''))
        if c.get('curva'):
            out.append(('par', 'curva de carga', c['curva'], ''))
        if cad.get('tip_cc'):
            out.append(('par', 'tipo de curva (BDGD)', cad['tip_cc'], ''))

        serie, nota = self.curva_da_carga(nome)
        if serie:
            out += [('vazio',),
                    ('grafico', 'Potência ao longo do dia', serie, nota,
                     'kW'),
                    ('nota', 'a curva é a da CLASSE, regulatória — uma para toda '
                             'a concessionária, e não a desta casa')]

        out += self._blocos_consumo_mensal(cad)
        return out

    def _blocos_trafo(self, nome, passo):
        """A ficha de um transformador: potência, ligação, tensões, carregamento."""
        t = self.trafo_por_nome.get(nome)
        if not t:
            return [('nota', 'Este transformador não está no circuito.')]
        cad = self._cad(t)

        out = [('secao', 'Transformador', cad.get('tip_trafo') or None),
               ('par', 'elemento', nome, '')]
        if cad.get('cod_id'):
            out.append(('par', 'identificador BDGD', cad['cod_id'], ''))
        out.append(('par', 'potência nominal', '%.0f kVA' % t['kva'], ''))
        for rotulo, chave, sufixo in (
                ('tensão do secundário', 'ten_lin_se_kv', ' kV'),
                ('tape', 'tap', ''),
                ('fases no primário', 'fas_con_p', ''),
                ('fases no secundário', 'fas_con_s', ''),
                ('perdas no ferro', 'per_fer_w', ' W'),
                ('perdas totais', 'per_tot_w', ' W'),
                ('banco', 'banc', ''),
                ('retorno por terra (MRT)', 'mrt', '')):
            # Zero em `banc` e `mrt` é "não se aplica", e não um valor. Mostrar
            # "banco 0" é gastar uma linha para não dizer nada.
            if cad.get(chave) and str(cad[chave]).strip() not in ('0', '0.0'):
                out.append(('par', rotulo, '%s%s' % (cad[chave], sufixo), ''))
        if len(t['buses']) >= 2:
            out.append(('link', 'barra do primário', t['buses'][0], '',
                        ('barra', t['buses'][0])))
            out.append(('link', 'barra do secundário', t['buses'][1], '',
                        ('barra', t['buses'][1])))

        pontos, trechos = self.circuito_bt(nome)
        cargas = [c for p in pontos
                  for c in self.por_barra.get(self.barras[p],
                                              {'cargas': []})['cargas']]
        out += [('vazio',),
                ('secao', 'Circuito de baixa', '%d unidades' % len(cargas)),
                ('par', 'alcance',
                 '%d barras · %d trechos' % (len(pontos), len(trechos)), '')]
        kw = sum(c['kw'] for c in cargas)
        out.append(('par', 'demanda nominal somada', '%.1f kW' % kw, ''))
        if t['kva']:
            uso = 100 * kw / t['kva']
            out.append(('par', 'carregamento nominal', '%.0f %%' % uso,
                        'ruim' if uso >= 100 else
                        ('atencao' if uso >= 80 else 'bom')))
        out.append(('nota', 'circuito BT em destaque no mapa'))

        # A soma das curvas do circuito é a curva do transformador que se pode
        # afirmar: o OpenDSS não guarda potência por trafo ao longo do dia, e
        # somar o que ele alimenta é mais honesto que estimar.
        somada, nota = None, ''
        for c in cargas:
            serie, nota = self.curva_da_carga(c['nome'])
            if not serie:
                continue
            if somada is None:
                somada = list(serie)
            else:
                for k, v in enumerate(serie):
                    somada[k] += v
        if somada:
            out += [('vazio',),
                    ('grafico', 'Carga do circuito ao longo do dia', somada,
                     '%s — soma das %d unidades' % (nota, len(cargas)),
                     'kW')]
            if t['kva']:
                out.append(('nota', 'a máquina é de %.0f kVA e o pico do dia é '
                                    'de %.1f kW' % (t['kva'], max(somada))))
        if cargas:
            out += [('vazio',), ('secao', 'Unidades atendidas',
                                 '%d' % len(cargas))]
            for c in cargas[:20]:
                out.append(('link', '  %s' % c['nome'], '%.2f kW' % c['kw'],
                            '', ('uc', c['nome'])))
            if len(cargas) > 20:
                out.append(('nota', '… e mais %d' % (len(cargas) - 20)))
        return out

    def _blocos_capacitor(self, nome, passo):
        """A ficha de um capacitor: potência do banco e estado no passo."""
        c = self.capacitor_por_nome.get(nome)
        if not c:
            return [('nota', 'Este capacitor n\u00e3o est\u00e1 no circuito.')]
        cad = self._cad(c)
        ligado = bool(c.get('ligado', True))
        out = [('secao', 'Banco de capacitores',
                None if ligado else 'desligado no caso'),
               ('par', 'elemento', nome, '')]
        if cad.get('cod_id'):
            out.append(('par', 'identificador BDGD', cad['cod_id'], ''))
        out.append(('par', 'pot\u00eancia reativa', '%.0f kVAr' % c['kvar'],
                    '' if ligado else 'atencao'))
        out.append(('par', 'tens\u00e3o', '%.4g kV' % (c.get('kv') or 0.0), ''))
        if (c.get('passos') or 1) > 1:
            out.append(('par', 'passos', '%d' % c['passos'], ''))
        for rotulo, chave in (('fases', 'fas_con'), ('tipo', 'tip_unid'),
                              ('banco', 'banc'), ('munic\u00edpio (IBGE)', 'mun')):
            if cad.get(chave):
                out.append(('par', rotulo, cad[chave], ''))
        out.append(('link', 'barra', c['bus'], '', ('barra', c['bus'])))
        if not ligado:
            out.append(('nota', 'entrou no caso desligado \u2014 o conversor deixa '
                                'os bancos de subesta\u00e7\u00e3o fora por padr\u00e3o, '
                                'porque ligados em vazio distorcem a madrugada'))
        else:
            out.append(('nota', 'capacitor fixo: injeta o mesmo reativo o dia '
                                'inteiro, e n\u00e3o comuta por tens\u00e3o'))
        return out

    def tape_do_regulador(self, banco, passo):
        """[(controle, tape, razão)] de cada fase do banco no instante.

        O tape é o único estado do caso que muda por decisão do próprio
        elemento ao longo do dia — todo o resto responde à curva de carga. Ler
        só o valor final, como se fazia, esconde exatamente o que se quer ver.
        """
        nomes = self.r.get('nomes_regcontrol') or []
        taps = self.r.get('tap_regulador')
        if taps is None or not nomes:
            return []
        p = min(int(passo), taps.shape[0] - 1)
        passo_tape = banco.get('tap_passo') or 0.00625
        fora = []
        for controle in banco.get('controles') or []:
            try:
                j = nomes.index(controle)
            except ValueError:
                continue
            n = int(taps[p, j])
            fora.append((controle, n, 1.0 + n * passo_tape))
        return fora

    def _blocos_regulador(self, nome, passo):
        """A ficha de um regulador, com o tape que ele tomou no dia.

        O tape é a resposta do regulador à rede, e a série dele ao longo do dia
        é o que mostra se ele está regulando ou correndo atrás — um regulador
        que bate no fim de curso e fica lá está dizendo que o problema é a
        montante dele.
        """
        g = self.regulador_por_nome.get(nome)
        if not g:
            return [('nota', 'Este regulador n\u00e3o est\u00e1 no circuito.')]
        cad = self._cad(g)
        out = [('secao', 'Regulador de tens\u00e3o',
                '%d fases' % g.get('fases', 1)),
               ('par', 'elemento', nome, '')]
        if g.get('ativo') is False:
            # O recuo de `rodar_dia` o desligou: fica no mapa, com o tape em
            # zero, e a ficha diz por que. O detalhe esta na aba de ajustes.
            out.append(('par', 'estado', 'DESLIGADO nesta simula\u00e7\u00e3o', 'ruim'))
            out.append(('nota', 'Com ele habilitado o dia n\u00e3o fechava o '
                        'balan\u00e7o de pot\u00eancia; o tape fica em zero. '
                        'Ver "Ajustes do cadastro".'))
        if cad.get('cod_id'):
            out.append(('par', 'identificador BDGD', cad['cod_id'], ''))
        out.append(('par', 'pot\u00eancia do banco', '%.0f kVA' % (g.get('kva') or 0), ''))
        if g.get('enrolamentos') and len(g['enrolamentos']) > 1:
            out.append(('nota', 'um enrolamento por fase: %s'
                        % ', '.join(g['enrolamentos'])))

        # ── O que o regulador está fazendo NESTE instante ───────────────────
        montante, jusante = (g['buses'] + ['', ''])[:2]
        rotulo_hora = _hhmm(self.r['horas'][passo])
        out += [('vazio',), ('secao', 'Tensão no instante', rotulo_hora)]
        for lado, barra in (('entrada', montante), ('saída', jusante)):
            fases = self._fases_da_barra(barra, passo) if barra else []
            if not fases:
                out.append(('par', lado, 'sem fase energizada', 'ruim'))
                continue
            nivel = self.nivel[self.indice[barra]] if barra in self.indice else 'mt'
            for fase, pu in sorted(fases):
                out.append(('par', '%s · fase %d' % (lado, fase),
                            '%.4f pu' % pu, _faixa(pu, nivel)))

        # O ganho é o que o regulador entregou: sem ele, as duas pontas teriam
        # a mesma tensão a menos da queda no próprio enrolamento.
        v_ent = self._fases_da_barra(montante, passo) if montante else []
        v_sai = self._fases_da_barra(jusante, passo) if jusante else []
        if v_ent and v_sai:
            m_ent = min(pu for _f, pu in v_ent)
            m_sai = min(pu for _f, pu in v_sai)
            out.append(('par', 'ganho na pior fase',
                        '%+.4f pu (%+.2f %%)'
                        % (m_sai - m_ent, 100 * (m_sai - m_ent)), ''))

        tapes = self.tape_do_regulador(g, passo)
        if tapes:
            out += [('vazio',), ('secao', 'Tape', rotulo_hora)]
            for controle, n, razao in tapes:
                out.append(('par', controle, '%+d  ·  razão %.4f' % (n, razao),
                            'atencao' if abs(n) >= 14 else ''))
            serie = self._serie_de_tape(g)
            if serie:
                faixa_dia = (min(serie), max(serie))
                out.append(('par', 'no dia', '%+d a %+d'
                            % (int(faixa_dia[0]), int(faixa_dia[1])), ''))
                out.append(('grafico', 'Tape ao longo do dia', serie,
                            'a linha vermelha é o instante mostrado',
                            'passos'))

        out += [('vazio',), ('secao', 'Controle',
                             ', '.join(g.get('controles') or []) or None)]
        if g.get('modo_rotulo'):
            out.append(('par', 'em fluxo reverso', g['modo_rotulo'], ''))
            out.append(('nota', g.get('modo_nota') or ''))
            if g.get('limiar_reverso_kw'):
                out.append(('par', 'limiar de reverso',
                            '%s kW' % g['limiar_reverso_kw'], ''))
        if g.get('vreg'):
            out.append(('par', 'tensão de referência',
                        '%.1f V (no secundário do TP)' % g['vreg'], ''))
        if g.get('banda'):
            out.append(('par', 'banda morta', '%.1f V' % g['banda'], ''))
        if g.get('modo') in ('cogeracao', 'bidirecional') and g.get('vreg_reverso'):
            out.append(('par', 'referência reversa',
                        '%.1f V · banda %.1f V'
                        % (g['vreg_reverso'], g.get('banda_reversa') or 0), ''))
        if g.get('ptratio'):
            out.append(('par', 'relação do TP', '%.1f' % g['ptratio'], ''))
        if g.get('ctprim'):
            out.append(('par', 'primário do TC', '%.0f A' % g['ctprim'], ''))
        if g.get('max_passos'):
            out.append(('par', 'passos por atuação', '%d' % g['max_passos'], ''))
        if g.get('atraso_s'):
            out.append(('par', 'atraso', '%.0f s' % g['atraso_s'], ''))
        if g.get('barra_monitorada'):
            out.append(('par', 'barra monitorada', g['barra_monitorada'], ''))
        for rotulo, chave in (('tipo', 'tip_regu'), ('rela\u00e7\u00e3o de TP', 'rel_tp'),
                              ('rela\u00e7\u00e3o de TC', 'rel_tc'),
                              ('corrente nominal', 'cor_nom')):
            if cad.get(chave):
                out.append(('par', rotulo, cad[chave], ''))

        if len(g.get('buses', ())) >= 2:
            out += [('vazio',), ('secao', 'Liga\u00e7\u00e3o', None),
                    ('link', 'montante', g['buses'][0], '',
                     ('barra', g['buses'][0])),
                    ('link', 'jusante', g['buses'][1], '',
                     ('barra', g['buses'][1]))]
            out.append(('nota', 'o tape comuta ao longo do dia \u2014 a simula\u00e7\u00e3o '
                                'roda em ControlMode=Time, e n\u00e3o congelada num '
                                'instante'))
        return out

    def _serie_de_tape(self, banco):
        """O tape da primeira fase do banco ao longo do dia."""
        nomes = self.r.get('nomes_regcontrol') or []
        taps = self.r.get('tap_regulador')
        controles = banco.get('controles') or []
        if taps is None or not nomes or not controles:
            return None
        try:
            j = nomes.index(controles[0])
        except ValueError:
            return None
        return [float(x) for x in taps[:, j]]

    def _blocos_gd(self, nome, passo):
        """A ficha de uma geração distribuída, com a curva do dia."""
        g = self.gd_por_nome.get(nome)
        if not g:
            return [('nota', 'Esta geração não está no circuito.')]
        cad = self._cad(g)
        out = [('secao', 'Geração distribuída', cad.get('fonte') or None),
               ('par', 'elemento', nome, '')]
        if cad.get('cod_id'):
            out.append(('par', 'identificador BDGD',
                        '%s…' % cad['cod_id'][:20], ''))
        if cad.get('ceg_gd'):
            out.append(('par', 'código CEG', cad['ceg_gd'], ''))
        if cad.get('fonte'):
            out.append(('par', 'fonte', cad['fonte'], ''))
            out.append(('nota', 'a fonte não vem da BDGD: o prefixo GD. do CEG '
                                'significa micro ou minigeração, e não solar. '
                                'Foi deduzida do fator de capacidade'))
        if cad.get('fc_pct'):
            out.append(('par', 'fator de capacidade',
                        '%s %%' % cad['fc_pct'], ''))
        out.append(('par', 'potência instalada', '%.1f kVA' % g['kva'], ''))
        if g.get('pmpp'):
            out.append(('par', 'pmpp', '%.1f kW' % g['pmpp'], ''))
        if cad.get('fas_con'):
            out.append(('par', 'fases', cad['fas_con'], ''))
        out.append(('link', 'barra', g['bus'], '', ('barra', g['bus'])))

        mult = (self.r.get('curvas') or {}).get('curva_pv')
        if mult:
            n = self.v_barra.shape[0]
            out += [('vazio',),
                    ('grafico', 'Geração ao longo do dia',
                     [g['kva'] * m for m in mult[:n]],
                     'nominal: potência × curva fotovoltaica do caso', 'kW')]
        out += self._blocos_consumo_mensal(cad)
        return out

    def detalhe(self, j, passo):
        """O conteúdo do inspetor para a barra `j`, em texto puro."""
        return self._texto(self.blocos(j, passo))

    def detalhe_de(self, foco, passo):
        """A ficha de um elemento, em texto puro."""
        return self._texto(self.blocos_de(foco, passo))

    @staticmethod
    def _texto(blocos):
        """Renderização em texto puro — a única, para os dois caminhos.

        Todo tipo de bloco é tratado explicitamente. Havia aqui um `else` que
        devolvia linha em branco, e ele era uma armadilha: um tipo novo sumia
        da tela sem erro nenhum, enquanto os testes que procuram substring
        continuavam verdes.
        """
        linhas = []
        for bl in blocos:
            if bl[0] == 'secao':
                linhas.append('%s%s' % (bl[1], '  %s' % bl[2] if bl[2] else ''))
            elif bl[0] in ('par', 'link'):
                # O link é um par em que se clica; em texto puro não há clique,
                # e o que resta é o par.
                linhas.append('   %-26s %s' % (bl[1], bl[2]))
            elif bl[0] == 'nota':
                linhas.append('   %s' % bl[1])
            elif bl[0] == 'grafico':
                # A série não vira ASCII: o que o texto pode dizer dela com
                # honestidade é o intervalo e a procedência.
                serie = bl[2] or [0.0]
                unidade = bl[4] if len(bl) > 4 else ''
                linhas.append(('   %s: %.3f a %.3f %s (%s)'
                               % (bl[1], min(serie), max(serie), unidade,
                                  bl[3])).replace('  (', ' ('))
            elif bl[0] == 'vazio':
                linhas.append('')
            else:
                raise AssertionError('bloco de tipo desconhecido: %r' % (bl,))
        return chr(10).join(linhas)

    def _blocos_consumo(self, nome):
        """O transformador, as unidades consumidoras e a geração do poste.

        É a pergunta que o mapa faz nascer: uma barra em subtensão atende o
        quê? Cinco casas ou um supermercado muda o que se conclui. As potências
        são as **nominais** do cadastro, não as do instante — a carga no passo
        varia com a curva da classe, e misturar as duas num painel só confunde.
        """
        from collections import Counter

        grupo = self.por_barra.get(nome)
        if not grupo:
            return [('vazio',), ('nota', 'Nenhum elemento neste ponto.')]

        out = []
        for t in grupo['trafos'][:4]:
            lado = 'primário' if t['buses'][0] == nome else 'secundário'
            out += [('vazio',),
                    ('secao', 'Transformador', lado),
                    ('link', t['nome'], '%.0f kVA' % t['kva'], '',
                     ('trafo', t['nome']))]
            if lado != 'secundário':
                continue
            pontos, trechos = self.circuito_bt(t['nome'])
            cargas_bt = [c for p in pontos
                         for c in self.por_barra.get(self.barras[p],
                                                     {'cargas': []})['cargas']]
            kw = sum(c['kw'] for c in cargas_bt)
            out.append(('nota', 'circuito BT em destaque no mapa'))
            out.append(('par', 'alcance',
                        '%d barras · %d trechos' % (len(pontos), len(trechos)),
                        ''))
            out.append(('par', 'unidades atendidas',
                        '%d · %.1f kW' % (len(cargas_bt), kw), ''))
            if t['kva']:
                uso = 100 * kw / t['kva']
                out.append(('par', 'carregamento nominal', '%.0f %%' % uso,
                            'ruim' if uso >= 100 else
                            ('atencao' if uso >= 80 else 'bom')))

        cargas = grupo['cargas']
        if cargas:
            kw = sum(c['kw'] for c in cargas)
            kvar = sum(c['kvar'] for c in cargas)
            out += [('vazio',),
                    ('secao', 'Unidades consumidoras', '%d' % len(cargas)),
                    ('par', 'demanda nominal',
                     '%.2f kW · %.2f kVAr' % (kw, kvar), '')]
            tensoes = sorted({round(c['kv'], 4) for c in cargas})
            out.append(('par', 'tensão',
                        '%s kV' % ', '.join('%.4g' % t for t in tensoes), ''))
            for classe, n in Counter(c['classe'] for c in cargas).most_common():
                p = sum(c['kw'] for c in cargas if c['classe'] == classe)
                out.append(('par', '  %s' % classe,
                            '%d un. · %.2f kW (%.0f%%)'
                            % (n, p, 100 * p / kw if kw else 0), ''))
            curvas = sorted({c['curva'] for c in cargas if c['curva']})
            if curvas:
                out.append(('par', 'curvas',
                            ', '.join(c[:14] for c in curvas[:3]), ''))
            # Todas, e não só quando cabem doze. Antes, um poste com treze
            # unidades mostrava o agregado e escondia quem eram — e o README
            # prometia "cada uma". Agora cada uma é um link, e a ficha inteira
            # só é montada para a que se abrir.
            out.append(('vazio',))
            for c in cargas[:LIMITE_LISTA]:
                out.append(('link', '  %s' % c['nome'],
                            '%.3f kW · %dφ' % (c['kw'], c['fases']), '',
                            ('uc', c['nome'])))
            if len(cargas) > LIMITE_LISTA:
                out.append(('nota', '… e mais %d neste ponto'
                            % (len(cargas) - LIMITE_LISTA)))

        caps = grupo.get('capacitores') or []
        if caps:
            out += [('vazio',),
                    ('secao', 'Capacitores', '%d' % len(caps))]
            for c in caps:
                out.append(('link', '  %s' % c['nome'],
                            '%.0f kVAr%s' % (c['kvar'],
                                             '' if c.get('ligado', True)
                                             else ' · desligado'),
                            '' if c.get('ligado', True) else 'atencao',
                            ('capacitor', c['nome'])))

        regs = grupo.get('reguladores') or []
        if regs:
            out += [('vazio',),
                    ('secao', 'Reguladores de tensão', '%d' % len(regs))]
            for g in regs:
                lado = ('montante' if g['buses'][0] == nome else 'jusante')
                out.append(('link', '  %s' % g['nome'],
                            '%d fases · %s' % (g.get('fases', 1), lado), '',
                            ('regulador', g['nome'])))

        gd = grupo['gd']
        if gd:
            out += [('vazio',),
                    ('secao', 'Geração distribuída', '%d' % len(gd)),
                    ('par', 'potência instalada',
                     '%.1f kVA' % sum(g['kva'] for g in gd), '')]
            for g in gd[:LIMITE_LISTA]:
                out.append(('link', '  %s' % g['nome'], '%.1f kVA' % g['kva'],
                            '', ('gd', g['nome'])))
        return out
