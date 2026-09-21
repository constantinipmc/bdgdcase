# -*- coding: utf-8 -*-
"""Quem se liga a quem, e por onde entra a energia.

A BDGD descreve a rede por pontos de conexão (`PAC`), não por barras. Duas
pontas com o mesmo PAC são o mesmo nó; pontas com PAC diferente que estão no
mesmo lugar do mapa **não são** — e é daí que nascem as ilhas: um alimentador
que se desenha inteiro e que, para o solver, são três redes soltas.

**A fonte.** `detectar_bus_fonte` acha por onde a subestação entra. Sem ela não
há fluxo; com ela no lugar errado, há fluxo e ele corre ao contrário.

**Os mapas de barra.** `_trafo_ext_map`, `_construir_mapa_bt`/`_mt` e os
`_resolver_bus_*` traduzem PAC em nome de barra. Cada um deles tem de concordar
com o que os emissores escrevem, e quando não concordam o caso compila e
diverge: já aconteceu de o mapa dizer trifásico e o emissor escrever center-tap
de dois nós, e o resultado foi um estouro numérico às 19h — longe, em toda
medida, da linha que o causou.

**A conferência.** `_auditar_conectividade_dss` e `diagnostico_topologia` leem o
caso já escrito e contam o que ficou solto. É a diferença entre entregar um
caso que converge e entregar um caso de que se sabe por que converge.
"""
from __future__ import annotations

from bdgdcase import ajustes as _ajustes

import re
import math
import os

from bdgdcase.modelo.registro import _diag_set
from bdgdcase.modelo.config import (
    BUS_FONTE_MANUAL, FORCAR_CHAVES_FECHADAS, TENSAO_BT_KV,
    VALIDAR_FONTE_POR_TOPOLOGIA_MT,)
from bdgdcase.modelo.cadastro import (
    _bus_bt_valido, _chave_bdgd_fechada, _norm_cod_bdgd, eh_center_tap,
    obter_dados_ctmt, sanitizar_bus, sanitizar_bus_bt,
    segundo_enrolamento_por_trafo,)


def _diagnostico_chaves_mt(caminho_linhas_mt):
    """Diagnóstico topológico simples: chaves abertas que ainda têm caminho alternativo."""
    try:
        with open(caminho_linhas_mt, 'r', encoding='utf-8') as f:
            linhas = f.read().splitlines()
    except OSError:
        return

    from collections import defaultdict, deque

    grafo = defaultdict(set)
    chaves_abertas = []  # (nome, b1, b2)

    for i, ln in enumerate(linhas):
        m = re.match(r"New Line\.(MT_|SW_)([^ ]+) bus1=([^ .]+)(?:\.[^ ]*)? bus2=([^ .]+)", ln)
        if not m:
            continue
        tipo, nome, b1, b2 = m.group(1), m.group(2), m.group(3), m.group(4)
        cont = linhas[i + 1] if (i + 1) < len(linhas) and linhas[i + 1].lstrip().startswith('~') else ""
        enabled = not bool(re.search(r"enabled\s*=\s*no", cont, flags=re.IGNORECASE))
        if tipo == 'SW_' and not enabled:
            chaves_abertas.append((nome, b1, b2))
        if enabled:
            grafo[b1].add(b2)
            grafo[b2].add(b1)

    def conectado(a, b):
        """Existe caminho de `a` até `b` andando só por trechos de MT?

        É a pergunta que valida a fonte: uma barra candidata que não alcança o
        resto da rede não é a entrada do alimentador, é uma ilha com cara de
        entrada. Busca em largura, porque só interessa se chega — não por onde.
        """
        if a == b:
            return True
        vis = {a}
        q = deque([a])
        while q:
            u = q.popleft()
            for v in grafo.get(u, ()):
                if v == b:
                    return True
                if v not in vis:
                    vis.add(v)
                    q.append(v)
        return False

    abertas_com_alternativa = [(n, b1, b2) for (n, b1, b2) in chaves_abertas if conectado(b1, b2)]
    print(f"  [DIAG-SW] Chaves abertas: {len(chaves_abertas)} | com caminho alternativo: {len(abertas_com_alternativa)}")
    if abertas_com_alternativa:
        amostra = ", ".join(f"SW_{n}" for (n, _, _) in abertas_com_alternativa[:12])
        print(f"  [DIAG-SW] Amostra de chaves abertas inefetivas (malha): {amostra}")

def detectar_bus_fonte(ssdmt, unsemt, ctmt_df=None, unremt_df=None):
    """Detecta o barramento de fonte, validando PAC_INI contra topologia MT."""
    from collections import defaultdict

    def _no_valido(v):
        """Devolve o nome de barra saneado, ou None se o PAC não presta.

        PAC vazio, nulo, ou que sanitiza para string vazia, não vira nó:
        ligá-lo criaria uma barra fantasma que atrai vizinhos e falseia a
        componente.
        """
        if v is None:
            return None
        s = sanitizar_bus(v)
        if not s:
            return None
        if s.strip().lower() in ('nan', 'none', 'null', '0'):
            return None
        return s

    def _arestas_mt_e_candidatos(ssdmt_df, unsemt_df, unremt_df_local):
        """As arestas da rede de MT, e por onde ela pode começar.

        Cada trecho `SSDMT` é uma aresta entre os seus dois PACs. Chave
        (`UNSEMT`) e regulador (`UNREMT`) também ligam dois pontos e entram
        como aresta — chave aberta continua sendo caminho para efeito de
        topologia, porque o que se quer saber aqui é se a rede é UMA, e não se
        ela está energizada agora.

        Os candidatos a raiz saem da própria BDGD: os pontos marcados como
        saída de subestação. Podem ser vários, podem ser nenhum, e podem estar
        errados — quem decide entre eles é `detectar_bus_fonte`.
        """
        arestas = []
        candidatos_raiz = []
        if ssdmt_df is None or ssdmt_df.empty:
            return arestas, candidatos_raiz
        if not {'PAC_1', 'PAC_2'}.issubset(ssdmt_df.columns):
            return arestas, candidatos_raiz

        pac1_series_raw = ssdmt_df['PAC_1'].astype(str).str.strip()
        pac2_series_raw = ssdmt_df['PAC_2'].astype(str).str.strip()
        pac1_series = pac1_series_raw.map(_no_valido)
        pac2_series = pac2_series_raw.map(_no_valido)

        for p1, p2 in zip(pac1_series, pac2_series):
            if not p1 or not p2 or p1 == p2:
                continue
            arestas.append((p1, p2))

        pac1_set = {x for x in pac1_series if x}
        pac2_set = {x for x in pac2_series if x}
        candidatos_set = pac1_set - pac2_set
        if candidatos_set:
            contagens = pac1_series.value_counts(dropna=True)
            for cand in contagens.index:
                if cand in candidatos_set:
                    candidatos_raiz.append(cand)

        if unsemt_df is not None and not unsemt_df.empty and {'PAC_1', 'PAC_2'}.issubset(unsemt_df.columns):
            for _, row in unsemt_df.iterrows():
                b1 = _no_valido(row.get('PAC_1', ''))
                b2 = _no_valido(row.get('PAC_2', ''))
                if not b1 or not b2 or b1 == b2:
                    continue
                if FORCAR_CHAVES_FECHADAS or _chave_bdgd_fechada(row.get('P_N_OPE', '')):
                    arestas.append((b1, b2))

        # Reguladores UNREMT também fecham continuidade entre PAC_1 e PAC_2.
        # Ignorar esses vínculos pode deslocar a "fonte" para dentro do alimentador.
        if (
            unremt_df_local is not None
            and not unremt_df_local.empty
            and {'PAC_1', 'PAC_2'}.issubset(unremt_df_local.columns)
        ):
            for _, row in unremt_df_local.iterrows():
                b1 = _no_valido(row.get('PAC_1', ''))
                b2 = _no_valido(row.get('PAC_2', ''))
                if not b1 or not b2 or b1 == b2:
                    continue
                arestas.append((b1, b2))
        return arestas, candidatos_raiz

    def _componentes(arestas):
        """As ilhas da rede: cada conjunto de barras que se alcançam entre si.

        Um alimentador saudável tem uma componente só. Duas ou mais quer dizer
        que o cadastro descreve pedaços que não se tocam — e o OpenDSS resolve
        o pedaço da fonte e reporta o resto como fora de serviço, sem dizer que
        faltou alguma coisa.
        """
        from collections import deque
        grafo = defaultdict(set)
        for a, b in arestas:
            grafo[a].add(b)
            grafo[b].add(a)
        comp_id = {}
        comp_sizes = {}
        atual = 0
        for n in grafo.keys():
            if n in comp_id:
                continue
            atual += 1
            fila = deque([n])
            comp_id[n] = atual
            tam = 0
            while fila:
                u = fila.popleft()
                tam += 1
                for v in grafo.get(u, ()):
                    if v not in comp_id:
                        comp_id[v] = atual
                        fila.append(v)
            comp_sizes[atual] = tam
        return grafo, comp_id, comp_sizes

    if BUS_FONTE_MANUAL:
        return sanitizar_bus(BUS_FONTE_MANUAL)

    # 1) Candidato CTMT (oficial)
    bus_ctmt = None
    if ctmt_df is not None and not ctmt_df.empty:
        bus_ctmt, _, _ = obter_dados_ctmt(ctmt_df)

    arestas, candidatos = _arestas_mt_e_candidatos(ssdmt, unsemt, unremt_df)
    grafo, comp_id, comp_sizes = _componentes(arestas)
    comp_maior = max(comp_sizes, key=comp_sizes.get) if comp_sizes else None
    tam_maior = int(comp_sizes.get(comp_maior, 0)) if comp_maior else 0
    _diag_set("mt_componentes", len(comp_sizes))
    _diag_set("mt_componente_maior_barras", tam_maior)

    if bus_ctmt and not VALIDAR_FONTE_POR_TOPOLOGIA_MT:
        print(f"  [Auto-fonte] Fonte lida com sucesso do CTMT (PAC_INI) -> bus fonte = {bus_ctmt}")
        return bus_ctmt

    # 2) Validação topológica do CTMT e fallback para componente dominante
    if bus_ctmt and comp_id:
        comp_ctmt = comp_id.get(bus_ctmt)
        if comp_ctmt is not None and comp_ctmt == comp_maior:
            print(f"  [Auto-fonte] CTMT validado na componente principal MT -> bus fonte = {bus_ctmt}")
            return bus_ctmt
        if comp_ctmt is not None:
            tam_ctmt = int(comp_sizes.get(comp_ctmt, 0))
            print(
                f"  [Auto-fonte][Aviso] PAC_INI={bus_ctmt} está em componente MT menor "
                f"({tam_ctmt} barras) vs principal ({tam_maior}). Aplicando fallback topológico."
            )
        else:
            print(
                f"  [Auto-fonte][Aviso] PAC_INI={bus_ctmt} não encontrado na malha MT/UNSEMT. "
                "Aplicando fallback topológico."
            )

    if candidatos and comp_maior:
        for cand in candidatos:
            if comp_id.get(cand) == comp_maior:
                print(f"  [Auto-fonte] Topologia raiz encontrada (Plano B validado) -> bus fonte = {cand}")
                return cand

    if comp_maior and grafo:
        no_comp = [n for n, cid in comp_id.items() if cid == comp_maior]
        if no_comp:
            no_grau = max(no_comp, key=lambda n: len(grafo.get(n, ())))
            print(
                "  [Auto-fonte][Fallback] Sem candidato raiz confiável; "
                f"usando nó de maior grau da componente principal -> {no_grau}"
            )
            return no_grau

    if bus_ctmt:
        print(f"  [Auto-fonte][Fallback] Sem topologia MT confiável; mantendo CTMT -> bus fonte = {bus_ctmt}")
        return bus_ctmt

    if ssdmt is not None and not ssdmt.empty and 'PAC_1' in ssdmt.columns:
        pac1_vals = ssdmt['PAC_1'].astype(str).str.strip().map(_no_valido)
        pac1_vals = [v for v in pac1_vals if v]
        if pac1_vals:
            bus = pac1_vals[0]
            print(f"  [Auto-fonte][Fallback] Topologia mínima, usando primeiro cabo -> bus fonte = {bus}")
            return bus

    return "FONTE_DESCONHECIDA"


def fracao_mt_ligada(ssdmt, unsemt=None, unremt=None, bus_fonte=None):
    """Quanto da media tensao chega a fonte SO pelo encadeamento de PAC.

    Devolve a fracao dos nos de media alcancaveis a partir de `bus_fonte`
    seguindo `PAC_1`/`PAC_2` dos trechos, das chaves e dos reguladores — sem
    nenhuma costura por coordenada. Sem fonte conhecida, mede a maior
    componente.

    Serve para uma decisao, e nao para diagnostico: as bases diferem no que
    encadeiam. A Celesc fecha o alimentador so com os PACs, e ai a fracao da
    1,0. A Copel nao encadeia — a topologia dela esta na geometria, com pontas
    coincidentes —, e a mesma conta da 0,02. Quem depende da geometria precisa
    da costura ligada; quem nao depende nao pode recebe-la de graca, porque
    cada emenda vira um jumper, e jumper entre nos que ja estao ligados e um
    laco numa rede que e radial.

    `1.0` quando nao ha media tensao para medir — nao ha o que consertar.
    """
    import collections

    if ssdmt is None or getattr(ssdmt, 'empty', True):
        return 1.0
    if 'PAC_1' not in ssdmt.columns or 'PAC_2' not in ssdmt.columns:
        return 1.0

    adj = collections.defaultdict(set)

    def ligar(df):
        """Acrescenta ao grafo os pares PAC_1/PAC_2 de uma tabela, se ela os tiver."""
        if df is None or getattr(df, 'empty', True):
            return
        if 'PAC_1' not in df.columns or 'PAC_2' not in df.columns:
            return
        for a, b in zip(df['PAC_1'].astype(str).str.strip(),
                        df['PAC_2'].astype(str).str.strip()):
            if a and b and a not in ('nan', 'None', '0') and b not in ('nan', 'None', '0'):
                adj[a].add(b)
                adj[b].add(a)

    for df in (ssdmt, unsemt, unremt):
        ligar(df)
    if not adj:
        return 1.0

    def componente(inicio):
        """Todos os nos alcancaveis a partir deste, andando pelo grafo."""
        vistos, fila = {inicio}, collections.deque([inicio])
        while fila:
            x = fila.popleft()
            for y in adj[x]:
                if y not in vistos:
                    vistos.add(y)
                    fila.append(y)
        return vistos

    partida = str(bus_fonte).strip() if bus_fonte else ''
    if partida in adj:
        alcancados = len(componente(partida))
    else:
        vistos, maior = set(), 0
        for n in adj:
            if n in vistos:
                continue
            c = componente(n)
            vistos |= c
            maior = max(maior, len(c))
        alcancados = maior
    return alcancados / float(len(adj))

def _auditar_conectividade_dss(pasta_alim, bus_fonte):
    """Auditoria rápida da conectividade no DSS exportado (MT+BT+trafos)."""
    from collections import defaultdict, deque

    def _base_bus(token):
        """O nome da barra dentro de um token do `.dss`, sem nós nem comentário.

        `bus1=BT_123.1.2.3   ! comentário` é a mesma barra que `BT_123`.
        Comparar os tokens crus faria a mesma barra parecer várias, e a
        auditoria de conectividade acusaria ilhas que não existem.
        """
        s = str(token or '').strip().strip('"').strip("'")
        if not s:
            return None
        s = s.split('!')[0].strip()
        if not s:
            return None
        base = s.split('.', 1)[0].strip()
        if not base or base.lower() in ('nan', 'none'):
            return None
        return base

    def _add_aresta(grafo, a, b):
        """Liga duas barras no grafo, ignorando o que não é ligação.

        Laço (`a == b`) e ponta vazia não são aresta. Deixá-los entrar infla o
        grau dos nós e faz um trecho degenerado parecer um caminho.
        """
        if not a or not b or a == b:
            return
        grafo[a].add(b)
        grafo[b].add(a)

    def _parse_linhas(caminho, grafo):
        """Lê `Linhas_*.dss` e liga bus1 a bus2 no grafo da auditoria.

        A auditoria roda sobre o `.dss` **já escrito**, e não sobre os
        DataFrames que o geraram. É deliberado: o que interessa é a
        conectividade do arquivo que o solver vai compilar, com todas as
        decisões da conversão já dentro dele — inclusive as que a conversão
        errou.
        """
        if not os.path.isfile(caminho):
            return
        rgx = re.compile(r"^New Line\.[^ ]+\s+bus1=([^ ]+)\s+bus2=([^ ]+)", re.IGNORECASE)
        with open(caminho, 'r', encoding='utf-8') as f:
            for ln in f:
                m = rgx.match(ln.strip())
                if not m:
                    continue
                _add_aresta(grafo, _base_bus(m.group(1)), _base_bus(m.group(2)))

    def _parse_reguladores_linhas_mt(caminho, grafo):
        """Lê os reguladores, emitidos como transformador de dois enrolamentos.

        No `.dss` um regulador é `New Transformer.REG_… buses=[a b]`, e
        portanto liga as duas barras. Ignorá-lo partiria a rede em duas
        exatamente onde ela está inteira.
        """
        if not os.path.isfile(caminho):
            return
        rgx = re.compile(
            r"^New Transformer\.REG_[^ ]+ .*?\bbuses=\[([^ \]]+) ([^\]]+)\]",
            re.IGNORECASE
        )
        with open(caminho, 'r', encoding='utf-8') as f:
            for ln in f:
                m = rgx.match(ln.strip())
                if not m:
                    continue
                _add_aresta(grafo, _base_bus(m.group(1)), _base_bus(m.group(2)))

    def _parse_trafos(caminho, grafo):
        """Lê `Transformadores.dss` e liga primário a secundário.

        O transformador é o que costura a MT à BT. Sem ele no grafo, toda a
        baixa tensão aparece como ilha e a auditoria vira um muro de falso
        positivo.
        """
        if not os.path.isfile(caminho):
            return
        rgx_new = re.compile(r"^New Transformer\.", re.IGNORECASE)
        rgx_bus = re.compile(r"\bbus=([^ \]]+)", re.IGNORECASE)
        blocos = []
        with open(caminho, 'r', encoding='utf-8') as f:
            for ln in f:
                txt = ln.strip()
                if rgx_new.match(txt):
                    if blocos:
                        for i in range(len(blocos)):
                            for j in range(i + 1, len(blocos)):
                                _add_aresta(grafo, blocos[i], blocos[j])
                    blocos = []
                if txt.startswith('~'):
                    m = rgx_bus.search(txt)
                    if m:
                        b = _base_bus(m.group(1))
                        if b:
                            blocos.append(b)
            if blocos:
                for i in range(len(blocos)):
                    for j in range(i + 1, len(blocos)):
                        _add_aresta(grafo, blocos[i], blocos[j])

    def _parse_buses_cargas(caminhos):
        """As barras onde há carga ou geração — as que precisam existir.

        Barra sem nada pendurado que fique isolada é irrelevante para o
        resultado. Barra com carga que fique isolada é energia que some do
        balanço, e é esse o achado que a auditoria persegue.
        """
        rgx = re.compile(r"^New (?:Load|PVSystem)\.[^ ]+\s+bus1=([^ ]+)", re.IGNORECASE)
        bases = []
        for caminho in caminhos:
            if not os.path.isfile(caminho):
                continue
            with open(caminho, 'r', encoding='utf-8') as f:
                for ln in f:
                    m = rgx.match(ln.strip())
                    if m:
                        b = _base_bus(m.group(1))
                        if b:
                            bases.append(b)
        return bases

    grafo = defaultdict(set)
    _parse_linhas(os.path.join(pasta_alim, 'Linhas_MT.dss'), grafo)
    _parse_reguladores_linhas_mt(os.path.join(pasta_alim, 'Linhas_MT.dss'), grafo)
    _parse_linhas(os.path.join(pasta_alim, 'Linhas_BT.dss'), grafo)
    _parse_linhas(os.path.join(pasta_alim, 'Jumpers.dss'), grafo)
    _parse_trafos(os.path.join(pasta_alim, 'Transformadores.dss'), grafo)

    if not grafo:
        print("  [DIAG-ILHA] Auditoria DSS: grafo vazio (sem linhas/trafo).")
        return

    comp = {}
    tam = {}
    cid = 0
    for no in grafo.keys():
        if no in comp:
            continue
        cid += 1
        fila = deque([no])
        comp[no] = cid
        t = 0
        while fila:
            u = fila.popleft()
            t += 1
            for v in grafo.get(u, ()):
                if v not in comp:
                    comp[v] = cid
                    fila.append(v)
        tam[cid] = t

    c_maior = max(tam, key=tam.get)
    b_fonte = _base_bus(bus_fonte)
    c_fonte = comp.get(b_fonte)
    tam_fonte = int(tam.get(c_fonte, 0)) if c_fonte else 0
    tam_maior = int(tam.get(c_maior, 0))

    buses_uso = _parse_buses_cargas(
        [
            os.path.join(pasta_alim, 'Cargas_BT.dss'),
            os.path.join(pasta_alim, 'Cargas_MT.dss'),
            os.path.join(pasta_alim, 'GD.dss'),
        ]
    )
    rede = set(grafo.keys())
    orfas = [b for b in buses_uso if b not in rede]
    fora_fonte = [b for b in buses_uso if b in rede and comp.get(b) != c_fonte]

    print(
        f"  [DIAG-ILHA] Componente da fonte: {tam_fonte} barras | "
        f"maior componente: {tam_maior} barras | componentes totais: {len(tam)}"
    )
    print(
        f"  [DIAG-ILHA] Buses de carga/GD: {len(buses_uso)} | "
        f"órfãos sem rede: {len(orfas)} | fora da componente da fonte: {len(fora_fonte)}"
    )
    if tam_fonte and tam_maior and tam_fonte < 0.5 * tam_maior:
        print(
            "  [DIAG-ILHA][ALERTA] Componente da fonte muito menor que a principal. "
            "Indício de cabeceira/topologia inconsistente para este alimentador."
        )

    # O terminal rola e some; a aba de ajustes fica com o caso. Um pedaço do
    # alimentador que não chega à fonte é carga que o dia inteiro deixa de ver,
    # e quem for tirar conclusão de perda ou de tensão precisa saber que ela
    # está aí. Medido: de catorze casos, cinco não têm ilha nenhuma — então
    # isto não é ruído de toda conversão, é achado.
    if fora_fonte:
        outras = sorted((t for c, t in tam.items() if c != c_fonte),
                        reverse=True)
        _ajustes.registrar(
            'topologia', 'Parte do alimentador não chega à fonte',
            quantos=len(fora_fonte), unidade='pontos com carga ou geração',
            efeito='nao_corrigido',
            detalhe='O cadastro descreve %d trecho(s) de rede separados do que '
                    'a fonte alimenta — o maior com %d barras. As cargas que '
                    'estão neles entram no caso e ficam sem tensão o dia '
                    'inteiro.'
                    % (len(outras), outras[0] if outras else 0),
            porque='Não é o modelo que as desliga: é o cadastro que não '
                   'publica o trecho que as ligaria. Costurar por proximidade '
                   'inventaria um condutor que a base não tem — e a distância '
                   'entre a ilha e o tronco costuma ser de quilômetros. Elas '
                   'aparecem no mapa em cinza, e é assim que se acham.',
            exemplos=sorted(fora_fonte)[:4])

    if orfas:
        _ajustes.registrar(
            'topologia', 'Carga pendurada em barra que não existe na rede',
            quantos=len(orfas), unidade='pontos com carga ou geração',
            efeito='nao_corrigido',
            detalhe='Estes pontos são citados por uma carga ou por uma geração, '
                    'e nenhum trecho, transformador ou jumper os alcança.',
            porque='O OpenDSS cria a barra assim que alguém a cita, então o '
                   'caso compila e a carga simplesmente não participa de nada. '
                   'É a forma mais silenciosa de perder carga, e por isso está '
                   'dita aqui.',
            exemplos=sorted(orfas)[:4])

def _trafo_bus_map(untrmt):
    """Retorna dict {COD_ID_trafo: bus_secundario_dss}."""
    return {
        sanitizar_bus(str(row['COD_ID']).strip()): sanitizar_bus_bt(str(row['PAC_2']).strip())
        for _, row in untrmt.iterrows()
    }

def _kv_bt_por_trafo(untrmt, eqtrmt=None):
    """{COD_ID do trafo: (kV de linha, kV fase-neutro)} do secundário.

    É por transformador, e não por alimentador: a mesma rede de baixa pode ter
    220 e 380 lado a lado, e cada unidade consumidora sabe de qual trafo
    depende pelo `UNI_TR_MT`.

    A fase-neutro sai calculada aqui, com a MESMA regra que o emissor do
    transformador usa, para que ninguém mais precise adivinhá-la. Enquanto eram
    duas contas escritas em dois lugares, discordaram — e a carga saía
    declarada numa tensão que o transformador acima dela não produz, num caso
    que resolve sem reclamar de nada.

    A regra tem três ramos, e o que os separa é a **forma do secundário**, não
    o `TIP_TRAFO` nem a tensão:

    - dois enrolamentos em série (center-tap): a perna é metade;
    - secundário trifásico a quatro fios: a fase é a de linha sobre √3;
    - enrolamento único entre fase e neutro: a tensão declarada JÁ É a
      fase-neutro, e não se divide por nada.
    """
    fora = {}
    if untrmt is None or untrmt.empty or 'COD_ID' not in untrmt.columns:
        return fora
    seg = segundo_enrolamento_por_trafo(eqtrmt)
    tem_ten = 'TEN_LIN_SE' in untrmt.columns
    for _, row in untrmt.iterrows():
        cod = sanitizar_bus(str(row.get('COD_ID', '')).strip())
        if not cod:
            continue
        ten = row.get('TEN_LIN_SE') if tem_ten else None
        try:
            ten = float(ten)
        except (TypeError, ValueError):
            continue
        if ten != ten or ten <= 0:      # exclui NaN
            continue
        fas_s = str(row.get('FAS_CON_S', row.get('FAS_CON', 'ABCN'))
                    or 'ABCN').upper().strip()
        n_fases_sec = len([c for c in fas_s if c in 'ABC'])
        if eh_center_tap(row.get('TIP_TRAFO'), ten, seg.get(cod)):
            fora[cod] = (ten, ten / 2.0)
        elif n_fases_sec >= 2:
            fora[cod] = (ten, ten / math.sqrt(3))
        else:
            fora[cod] = (ten, ten)
    return fora

def _trafo_ext_map(untrmt, ssdbt=None, eqtrmt=None):
    """Retorna dict {COD_ID_trafo: (bus_bt, tip_trafo, kv_sec_ll, avail_nodes)} com info para GD.

    tip_trafo   : 'MT' = monofásico (MRT; pode ser center-tap 440/220 V)
                  'T'  = trifásico padrão (380 V)
    kv_sec_ll   : tensão linha-a-linha do secundário (ex: 0.44, 0.38)
    avail_nodes : set com os nós reais do secundário, usando o mesmo sniffer
    SSDBT
                  que gerar_transformadores usa (source-of-truth: quais fases os cabos
                  BT conectam em cada bus secundário).
    """
    _FASE_NO = {'A': '1', 'B': '2', 'C': '3'}

    # Replicar o sniffer BT igual a gerar_transformadores
    bt_bus_fases = {}
    if ssdbt is not None and not ssdbt.empty:
        for _, row in ssdbt.iterrows():
            for pac in ('PAC_1', 'PAC_2'):
                b = sanitizar_bus_bt(str(row.get(pac, '')).strip())
                _fr = str(row.get('FAS_CON', 'ABC')).strip()
                fas = 'ABC' if _fr.lower() in ('nan', 'none', '') else (
                    ''.join(c for c in _fr.upper() if c in ('A', 'B', 'C')) or 'ABC')
                if b not in bt_bus_fases:
                    bt_bus_fases[b] = set()
                for f in fas:
                    bt_bus_fases[b].add(f)

    # Decidido AQUI, uma vez, e carregado no resultado. Cada ponto que
    # reperguntava "isto é center-tap?" era uma chance de discordar do emissor
    # do transformador — e discordar significa a carga declarada numa tensão que
    # o transformador não produz, ou pendurada num nó que ele não energiza.
    #
    # Foi exatamente o que aconteceu ao acrescentar a evidência do catálogo ao
    # predicado sem levá-la a todos os pontos: numa base, as cargas de
    # iluminação pública ficaram sobre um nó morto do center-tap e o caso
    # divergiu às 19:00, quando a curva da iluminação acende.
    seg_ct = segundo_enrolamento_por_trafo(eqtrmt)

    result = {}
    for _, row in untrmt.iterrows():
        cod = sanitizar_bus(str(row['COD_ID']).strip())
        bus = sanitizar_bus_bt(str(row['PAC_2']).strip())
        tip = str(row.get('TIP_TRAFO', 'T')).upper().strip()
        ten = float(row.get('TEN_LIN_SE', TENSAO_BT_KV) or TENSAO_BT_KV)

        # Replica o roteamento de `gerar_transformadores`, e tem de replicá-lo
        # EXATAMENTE. Quando os dois divergem, a carga é pendurada num nó que o
        # transformador não energiza — e uma carga de potência constante sobre
        # um nó a 0 V faz a solução divergir. Foi assim que um alimentador
        # explodiu às 19:00, quando a curva da iluminação pública acende: o
        # emissor tinha posto o center-tap em dois nós e esta réplica dizia três.
        #
        # A condição de entrada é a do emissor: o que decide o SECUNDÁRIO é o
        # secundário. Um primário de duas fases com secundário center-tap vem
        # para cá, e não para o ramo bifásico.
        _tem_seg_rot = seg_ct.get(cod)
        fas_p_rot = str(row.get('FAS_CON_P', row.get('FAS_CON', 'ABC'))
                        or 'ABC').upper().strip().replace('N', '')
        if not fas_p_rot:
            fas_p_rot = 'ABC'
        if len(fas_p_rot) <= 2 and (len(fas_p_rot) <= 1
                                    or eh_center_tap(tip, ten, _tem_seg_rot)):
            fas_p = str(row.get('FAS_CON_P', row.get('FAS_CON', 'ABC')) or 'ABC').upper().strip()
            fas_s = str(row.get('FAS_CON_S', row.get('FAS_CON', 'ABCN')) or 'ABCN').upper().strip()
            fas_mt = fas_p.replace('N', '')
            if not fas_mt: fas_mt = 'ABC'

            # Monofasico: sniffer BT
            fases_reais_bt = list(bt_bus_fases.get(bus, set()))
            if len(fases_reais_bt) < 2:
                fas_s_clean = fas_s.replace('N', '')
                if len(fas_s_clean) >= 2:
                    fases_reais_bt = sorted(list(fas_s_clean))

            is_mrt_center_tap = (eh_center_tap(tip, ten, _tem_seg_rot)
                                 or (_tem_seg_rot is None
                                     and len(fases_reais_bt) >= 2))

            if is_mrt_center_tap:
                # Espelha gerar_transformadores: default da perna 1 = no da fase MT primaria
                # quando o sniffer SSDBT nao encontra fases BT. Mantem avail_nodes sincronizado
                # com a string DSS gerada.
                fases_reais_bt = sorted(fases_reais_bt)
                p_mt_node = _FASE_NO.get(fas_mt[0], '1')
                if fases_reais_bt:
                    p_bt1 = _FASE_NO.get(fases_reais_bt[0], p_mt_node)
                else:
                    p_bt1 = p_mt_node
                if len(fases_reais_bt) > 1:
                    p_bt2 = _FASE_NO.get(fases_reais_bt[1], '2')
                else:
                    p_bt2 = next(n for n in ('1', '2', '3') if n != p_bt1)
                if p_bt1 == p_bt2:
                    # Degradado: 2-winding mono, so 1 no real
                    avail_nodes = {p_bt1}
                else:
                    avail_nodes = {p_bt1, p_bt2}
            else:
                # Monofasico simples: 1 no
                p_bt1 = _FASE_NO.get(fases_reais_bt[0], '1') if fases_reais_bt else _FASE_NO.get(fas_mt[0], '1')
                avail_nodes = {p_bt1}
        elif len(fas_p_rot) == 2:
            # Bifásico: os dois nós do par de fases do primário, que é o que o
            # emissor usa para `p_a`/`p_b`.
            avail_nodes = set()
            for c in fas_p_rot:
                n = _FASE_NO.get(c)
                if n:
                    avail_nodes.add(n)
            if not avail_nodes:
                avail_nodes = {'1', '2'}
        else:
            avail_nodes = {'1', '2', '3'}  # trifasico: todos os nos disponiveis

        # O quinto campo é a resposta já dada: quem precisar dela lê, e não
        # recalcula.
        result[cod] = (bus, tip, ten, avail_nodes,
                       bool(seg_ct.get(cod))
                       if seg_ct.get(cod) is not None
                       else eh_center_tap(tip, ten))
    return result

def _construir_mapa_bt(ssdbt, ramlig):
    """Constroi conjunto de buses validos e mapa RAMLIG para resolucao BT com prefixos padronizados.

    Só entra aqui barra que o emissor de linhas aceita (`_bus_bt_valido`). A
    SSDBT traz tocos de cadastro — `PAC_1=BT-0`, `PAC_2=BT-D-P2-<id>`,
    `FAS_CON=N` — que o emissor descarta; se o nome deles ficasse neste mapa,
    a resolução "direta" entregaria um barramento que nenhuma linha cria.
    """
    valid_buses = set()
    ramlig_map  = {}

    if ssdbt is not None:
        for _, row in ssdbt.iterrows():
            for col in ('PAC_1', 'PAC_2'):
                # FIX: Usar sanitizar_bus_bt para garantir que o prefixo BT_ suma do mapa
                v = sanitizar_bus_bt(str(row.get(col, '')).strip())
                if v and v not in ('nan', 'none', '') and _bus_bt_valido(v):
                    valid_buses.add(v)

    if ramlig is not None and len(ramlig) > 0:
        for _, row in ramlig.iterrows():
            # FIX: Ramais (RAMLIG) também são BT, precisam do mesmo tratamento
            p1 = sanitizar_bus_bt(str(row.get('PAC_1', '')).strip())
            p2 = sanitizar_bus_bt(str(row.get('PAC_2', '')).strip())
            p1_ok = bool(p1) and p1 not in ('nan', 'none', '') and _bus_bt_valido(p1)
            p2_ok = bool(p2) and p2 not in ('nan', 'none', '') and _bus_bt_valido(p2)
            if p1_ok:
                valid_buses.add(p1)
            if p2_ok:
                valid_buses.add(p2)
                if p1_ok:
                    ramlig_map[p2] = p1   # endpoint do ramal → poste BT

    return valid_buses, ramlig_map

class _UniaoDeBarras:
    """Union-find sobre nomes de barra: `ligadas(a, b)` e `unir(a, b)`.

    É o que responde, em tempo constante, "estas duas barras já estão no
    mesmo circuito?" — a pergunta que a costura por coordenada tem de fazer
    antes de cada jumper, e que o emissor de chaves faz para cada regulador.
    """

    def __init__(self):
        """Começa sem barra nenhuma; elas entram conforme são citadas."""
        self.pai = {}

    def _raiz(self, x):
        """Raiz do conjunto de `x`, com compressão de caminho."""
        self.pai.setdefault(x, x)
        while self.pai[x] != x:
            self.pai[x] = self.pai[self.pai[x]]
            x = self.pai[x]
        return x

    def ligadas(self, a, b):
        """As duas barras já têm caminho entre si?"""
        return self._raiz(a) == self._raiz(b)

    def unir(self, a, b):
        """Passa a considerar `a` e `b` no mesmo circuito."""
        self.pai[self._raiz(a)] = self._raiz(b)


_RX_BUS = re.compile(r'\bbus\d?=([^\s\]]+)', re.I)
_RX_BUSES = re.compile(r'\bbuses=\[([^\]]+)\]', re.I)


def _nos_de(token):
    """`'BT_1.1.3.0'` → `('bt_1', [1, 3])`; sem lista de nós vale `[1, 2, 3]`."""
    nome, _, resto = token.partition('.')
    nos = [int(x) for x in resto.split('.') if x.isdigit() and int(x) != 0]
    return nome.lower(), (nos or [1, 2, 3])


def _pares_de_um_dss(caminho):
    """Pares de NÓS (`barra.k`) que cada elemento de um `.dss` emitido liga.

    Lê `New Line` e `New Transformer` (com `buses=[...]` ou `~ wdg=n
    bus=...`) e respeita `enabled=no`. Numa linha, o condutor `i` de uma
    ponta liga ao condutor `i` da outra; num transformador, todos os nós de
    todos os enrolamentos ficam juntos. É a mesma leitura que
    :func:`~bdgdcase.solucao.nos_conectados` faz do circuito resolvido — e
    tem de ser por nó: dois PACs "já ligados" por um trecho de UMA fase não
    estão ligados nas outras duas, e o jumper que as trazia era o que
    mantinha a média trifásica (UVA01: dispensado por barra, duas fases da
    média caíram a 2,8 kV e as bases saíram trocadas).
    """
    pares = []
    try:
        texto = open(caminho, encoding='utf-8').read().splitlines()
    except OSError:
        return pares
    bloco = []

    def fechar():
        """Converte o bloco acumulado (`New ...` e continuações) em pares."""
        if not bloco:
            return
        corpo = ' '.join(bloco)
        if re.search(r'enabled\s*=\s*(no|false)', corpo, re.I):
            return
        tokens = _RX_BUS.findall(corpo)
        m = _RX_BUSES.search(corpo)
        if m:
            tokens += m.group(1).split()
        terminais = [_nos_de(t) for t in tokens if t]
        if len(terminais) < 2:
            return
        if bloco[0].lower().startswith('new transformer.'):
            todos = ['%s.%d' % (b, k) for b, nos in terminais for k in nos]
            for x in todos[1:]:
                if x != todos[0]:
                    pares.append((todos[0], x))
            return
        (a, na), (b, nb) = terminais[0], terminais[1]
        for ka, kb in zip(na, nb):
            if a != b or ka != kb:
                pares.append(('%s.%d' % (a, ka), '%s.%d' % (b, kb)))

    for ln in texto:
        s = ln.strip()
        if s.lower().startswith('new '):
            fechar()
            bloco = [s] if s.lower().startswith(('new line.', 'new transformer.')) else []
        elif s.startswith('~') and bloco:
            bloco.append(s[1:])
    fechar()
    return pares


def uniao_dos_emitidos(caminhos):
    """:class:`_UniaoDeBarras` sobre NÓS, com tudo o que os `.dss` dados ligam."""
    u = _UniaoDeBarras()
    for c in caminhos or ():
        for a, b in _pares_de_um_dss(c):
            u.unir(a, b)
    return u


def chaves_que_bypassam_reguladores(pares_linhas, chaves, reguladores):
    """Quais chaves fechadas abrir para nenhum regulador ficar em paralelo
    com um caminho que o contorna; e quais reguladores ficam contornados
    mesmo assim (o caminho é de linhas, e não há chave para abrir).

    ``pares_linhas``: `[(a, b)]` dos trechos de média emitidos.
    ``chaves``: `[(cod, a, b, fechada)]`.
    ``reguladores``: `[(cod, a, b)]`.
    Devolve ``(abrir, sem_saida)``: `{cod_chave: cod_regulador}` e
    `[cod_regulador]`.

    Um regulador com um caminho fechado ao lado dele não regula: com o tape
    fora de 1,0 o caminho vira um curto sobre o transformador e a corrente
    circula. Medido no UVA01: 1.638 A num regulador de subestação com a
    fonte entregando 457 A, 16,9 MVAr consumidos pelos reguladores, a média
    inteira a 0,8 pu. O contorno era um caminho de TRÊS chaves pelo pátio da
    subestação, todas `P_N_OPE=F` no cadastro — que nunca fica assim com o
    regulador em serviço. A guarda antiga só via chave no MESMO par de PACs.

    Abre-se a chave do caminho que não toca os terminais do regulador (a de
    contorno propriamente dita); se todas tocam, qualquer uma. Repete até
    não sobrar caminho, porque um pátio tem mais de um.
    """
    from collections import deque

    abrir = {}
    sem_saida = []
    fechada = {c: (a, b) for c, a, b, f in chaves if f}
    for cod_reg, ra, rb in reguladores:
        while True:
            adj = {}
            for a, b in pares_linhas:
                adj.setdefault(a, []).append((b, None))
                adj.setdefault(b, []).append((a, None))
            for c, (a, b) in fechada.items():
                if c in abrir:
                    continue
                adj.setdefault(a, []).append((b, c))
                adj.setdefault(b, []).append((a, c))
            pai = {ra: None}
            q = deque([ra])
            while q and rb not in pai:
                x = q.popleft()
                for y, c in adj.get(x, ()):
                    if y not in pai:
                        pai[y] = (x, c)
                        q.append(y)
            if rb not in pai:
                break                                  # sem contorno: bom
            chaves_no_caminho = []
            x = rb
            while pai[x] is not None:
                ant, c = pai[x]
                if c is not None:
                    chaves_no_caminho.append((c, ant, x))
                x = ant
            if not chaves_no_caminho:
                sem_saida.append(cod_reg)
                break
            fora = [c for c, a, b in chaves_no_caminho if ra not in (a, b) and rb not in (a, b)]
            abrir[(fora or [chaves_no_caminho[0][0]])[0]] = cod_reg
    return abrir, sem_saida


def nos_energizados_bt(nos_secundario, nos_por_ligacao):
    """``{barra: [nós]}`` de baixa com caminho até um secundário de transformador.

    Parte de cada secundário com os nós que o transformador escreveu e anda
    pelas linhas de baixa; a cada linha, ficam só os nós que ela conduz.
    Barra alcançada por mais de um caminho junta o que chega por cada um.
    Barra sem caminho nenhum não entra no resultado.

    "Uma linha toca este nó" não é o mesmo que "este nó tem tensão". O caso
    que mostrou a diferença: center-tap emitido em `.1.3`, ramal cadastrado
    `.1.2.3` saindo dele, e a usina posta em `.1.2` — o nó 2 existia na
    linha, e estava a 0 V, porque nada o alimentava. A carga do mesmo poste
    já saía em `.1.3`, porque olha os nós do transformador; a usina tem de
    olhar a mesma coisa, e aqui a resposta vale para qualquer barra da rede
    de baixa, não só a do secundário.
    """
    vizinhos = {}
    for par, nos in (nos_por_ligacao or {}).items():
        lados = tuple(par)
        if len(lados) != 2:
            continue
        a, b = lados
        vizinhos.setdefault(a, []).append((b, set(nos)))
        vizinhos.setdefault(b, []).append((a, set(nos)))
    energizados = {}
    fila = []
    for barra, nos in (nos_secundario or {}).items():
        conj = {int(n) for n in nos if int(n) != 0}
        if not conj:
            continue
        if not conj - energizados.get(barra, set()):
            continue
        energizados.setdefault(barra, set()).update(conj)
        fila.append((barra, conj))
    while fila:
        barra, conj = fila.pop()
        for outra, nos_linha in vizinhos.get(barra, ()):
            chega = conj & nos_linha
            if not chega:
                continue
            ja = energizados.setdefault(outra, set())
            if chega - ja:
                ja.update(chega)
                fila.append((outra, set(ja)))
    return {b: sorted(n) for b, n in energizados.items()}


def nos_vivos(pedidos, reais):
    """Grampeia os nós pedidos por carga ou usina aos que a rede CRIA na barra.

    ``pedidos``: nós ('1','2','3') que a auto-cura de fases escolheu.
    ``reais``: nós que as linhas e os transformadores de fato escreveram
    nessa barra (``nos_bt``/``nos_secundario`` do pipeline), ou vazio quando
    a barra não está lá. Devolve ``(nos, mudou)``.

    Fica o que existe, e a CONTAGEM de fases é preservada enquanto houver
    nó real para isso: pediu `.1.2` num center-tap que tem `.1.3`, sai em
    `.1.3` — duas pernas, como o cadastro quis —, e não numa perna só. Se
    nada do pedido existe, entram os nós reais, na mesma quantidade. Sem
    ``reais`` nada muda: quem não sabe não opina.

    O caso que motivou isto: um center-tap emitido em `.3.0`/`.0.1`, a rede
    de baixa em `.1.3`, e a usina em `.2.0`. O nó 2 só existia porque a usina
    o criou, e ficou em 0,0 pu com 40 kW "injetados" nele. Uma fonte num nó
    morto não é potência perdida: o Newton "converge" em duas iterações e o
    balanço do alimentador inteiro sai aberto — 50 de 96 passos.
    """
    if not reais:
        return list(pedidos), False
    reais = [str(n) for n in reais if str(n) != '0']
    if not reais:
        return list(pedidos), False
    vivos = [n for n in pedidos if n in reais]
    if vivos == list(pedidos):
        return vivos, False
    faltam = max(1, len(pedidos)) - len(vivos)
    vivos = vivos + [n for n in reais if n not in vivos][:faltam]
    return sorted(vivos), True


def _resolver_bus_bt(pn_con, valid_buses, ramlig_map):
    """Resolve o nó puramente por strings e nomenclaturas (Mundo ideal do BDGD)"""
    bus = sanitizar_bus_bt(str(pn_con).strip())
    if bus in valid_buses:
        return bus, 'direto'
    if bus in ramlig_map:
        return ramlig_map[bus], 'ramlig'
    return bus, 'flutuante'

def _construir_mapa_mt(ssdmt):
    """Constrói conjunto de buses MT válidos a partir de PAC_1/PAC_2 do SSDMT."""
    valid_buses = set()
    if ssdmt is not None and not ssdmt.empty:
        for _, row in ssdmt.iterrows():
            for col in ('PAC_1', 'PAC_2'):
                v = sanitizar_bus(str(row.get(col, '')).strip())
                if v and v.lower() not in ('nan', 'none', '', '0'):
                    valid_buses.add(v)
    return valid_buses

def _construir_gdf_endpoints_mt(ssdmt):
    """Extrai os endpoints (PAC_1/PAC_2) do SSDMT como GeoDataFrame de pontos.

    Suporta LineString e MultiLineString (formato comum em GPKGs exportados de
    GDB). Deduplica por nome de bus e retorna um GDF pronto para sjoin_nearest.
    """
    import geopandas as gpd
    from shapely.geometry import Point

    pontos = {}   # bus_name → Point (deduplica)
    erros = 0
    for _, row in ssdmt.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        try:
            gt = geom.geom_type
            if gt == 'LineString':
                coords = list(geom.coords)
                pt_inicio, pt_fim = coords[0], coords[-1]
            elif gt == 'MultiLineString':
                # Usa o primeiro ponto da primeira sub-linha e o último da última
                sub_linhas = list(geom.geoms)
                pt_inicio = list(sub_linhas[0].coords)[0]
                pt_fim    = list(sub_linhas[-1].coords)[-1]
            else:
                erros += 1
                continue

            for pac, pt_coords in [('PAC_1', pt_inicio), ('PAC_2', pt_fim)]:
                bus = sanitizar_bus(str(row.get(pac, '')).strip())
                if bus and bus.lower() not in ('nan', 'none', '0', ''):
                    pontos.setdefault(bus, Point(pt_coords))
        except Exception:
            erros += 1
            continue

    if erros:
        print(f"  [DIAG-MT] Endpoints SSDMT: {len(pontos)} extraídos, {erros} geometrias ignoradas")

    if not pontos:
        return None

    return gpd.GeoDataFrame(
        {'bus': list(pontos.keys())},
        geometry=list(pontos.values()),
        crs=ssdmt.crs,
    )

def _resolver_bus_mt(pn_con, valid_mt_buses):
    """Resolve o ponto de conexão MT contra os buses reais da rede SSDMT.

    Retorna (bus_dss, resolucao) onde resolucao é 'direto' ou 'flutuante'.
    Espelha _resolver_bus_bt, mas sem prefixo BT_ e sem mapa de ramais.
    """
    bus = sanitizar_bus(str(pn_con).strip())
    if bus and bus.lower() not in ('nan', 'none', ''):
        if bus in valid_mt_buses:
            return bus, 'direto'
    return bus, 'flutuante'

def _construir_gdf_endpoints_bt(ssdbt):
    """Extrai os endpoints (PAC_1/PAC_2) do SSDBT como GeoDataFrame de pontos.

    Suporta LineString e MultiLineString. Retorna GDF com colunas [bus,
    geometry] pronto para sjoin_nearest. Retorna None se SSDBT não tiver
    geometria válida.
    """
    import geopandas as gpd
    from shapely.geometry import Point

    pontos = {}  # bus_name (com prefixo BT_) → Point (deduplica)
    erros = 0
    for _, row in ssdbt.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        try:
            gt = geom.geom_type
            if gt == 'LineString':
                coords = list(geom.coords)
                pt_inicio, pt_fim = coords[0], coords[-1]
            elif gt == 'MultiLineString':
                sub_linhas = list(geom.geoms)
                pt_inicio = list(sub_linhas[0].coords)[0]
                pt_fim    = list(sub_linhas[-1].coords)[-1]
            else:
                erros += 1
                continue

            for pac, pt_coords in [('PAC_1', pt_inicio), ('PAC_2', pt_fim)]:
                bus = sanitizar_bus_bt(str(row.get(pac, '')).strip())
                # O toco `BT-0 → BT-D-P2-<id>` tem geometria e tem UNI_TR_MT,
                # e por isso passava por todos os crivos do fallback espacial:
                # era o ponto mais perto do poste da usina, e o transformador
                # do toco era o mesmo do cadastro. Só que o emissor de linhas
                # não escreve o toco — a usina nascia numa barra que ninguém
                # cria, e um PVSystem sozinho numa barra morta bastava para
                # o dia inteiro do alimentador sair com o balanço aberto.
                if (bus and bus.lower() not in ('nan', 'none', '0', 'bt_', '')
                        and _bus_bt_valido(bus)):
                    pontos.setdefault(bus, Point(pt_coords))
        except Exception:
            erros += 1
            continue

    if erros:
        print(f"  [DIAG-BT] Endpoints SSDBT: {len(pontos)} extraídos, {erros} geometrias ignoradas")

    if not pontos:
        return None

    return gpd.GeoDataFrame(
        {'bus': list(pontos.keys())},
        geometry=list(pontos.values()),
        crs=ssdbt.crs,
    )

def _buses_da_rede_mt(ssdmt):
    """As barras que a rede de média realmente tem, já sanitizadas."""
    fora = set()
    if ssdmt is None or getattr(ssdmt, 'empty', True):
        return fora
    for pac in ('PAC_1', 'PAC_2'):
        if pac in ssdmt.columns:
            for v in ssdmt[pac].dropna().astype(str):
                b = sanitizar_bus(v.strip())
                if b:
                    fora.add(b)
    return fora

def _primeiro_cabo_mt_da_fonte(ssdmt, bus_fonte):
    """Retorna o COD_ID do primeiro cabo MT que parte do bus_fonte."""
    bus_raw = bus_fonte  # pode ter sido sanitizado
    for _, row in ssdmt.iterrows():
        p1 = sanitizar_bus(row['PAC_1'])
        if p1 == bus_raw:
            return str(row['COD_ID']).strip()
    return None

def diagnostico_topologia(ponnot, ssdmt, ssdbt, untrmt, ucbt_por_poste=None):
    """O poste e a rede: o que de fato se pode conferir entre os dois.

    A versão anterior cruzava `PONNOT.COD_ID` com os `PAC` dos cabos e relatava
    quantos postes tinham cabo. **Os dois nunca casam**, e não por defeito de
    nenhuma base: na BDGD o identificador do poste e o ponto de acoplamento do
    cabo são espaços de identificador distintos. Medido nas quatro extrações
    disponíveis, incluindo a de referência, a interseção foi de 0 a 85 em
    milhares — e o relatório anunciava, em toda conversão, "0 postes com cabos
    conectados" e milhares de "órfãos".

    Um diagnóstico que grita em todo caso não é diagnóstico: é ruído que ensina
    a ignorar a tela, e foi confundido com erro de extração.

    O que o poste de fato liga é a **unidade consumidora**: `UCBT.PN_CON`
    referencia `PONNOT.COD_ID`, e aí sim o cruzamento é de mesmo espaço — 100%
    na base medida. É esse o número que vale a pena vigiar, porque se ele cair,
    as cargas param de achar o poste onde moram e o mapa perde a navegação do
    ponto para as unidades dele.
    """
    print("\n" + "=" * 60)
    print("  [TOPOLOGIA]")
    print("=" * 60)

    n_postes = len(ponnot) if ponnot is not None else 0

    nos = set()
    for df, sanit in ((ssdmt, sanitizar_bus), (ssdbt, sanitizar_bus_bt)):
        if df is None:
            continue
        for pac in ('PAC_1', 'PAC_2'):
            if pac in getattr(df, 'columns', []):
                nos.update(sanit(str(v)) for v in df[pac])
    if untrmt is not None:
        for pac, sanit in (('PAC_1', sanitizar_bus), ('PAC_2', sanitizar_bus_bt)):
            if pac in getattr(untrmt, 'columns', []):
                nos.update(sanit(str(v)) for v in untrmt[pac])
    nos.discard('')
    nos.discard('nan')
    nos.discard('none')

    print(f"  -> Postes no PONNOT                    : {n_postes}")
    print(f"  -> Nos eletricos distintos na rede     : {len(nos)}")

    # O cruzamento que é do mesmo espaço de identificador.
    if (ucbt_por_poste is not None and len(ucbt_por_poste)
            and ponnot is not None and 'COD_ID' in getattr(ponnot, 'columns', [])):
        # `COD_PONNOT`, e não `PN_CON`. No arquivo agregado por poste o
        # `PN_CON` é uma LISTA separada por `;` — uma entrada por unidade do
        # poste —, de modo que só os postes de uma unidade só têm valor
        # simples. Comparar essa coluna dava 14% onde a medição correta dá
        # 100%, e teria trocado um alarme falso por outro.
        #
        # `_norm_cod_bdgd` dos dois lados porque um identificador numérico lido
        # do `.gpkg` com algum nulo na coluna volta como float, e `661095653`
        # vira `661095653.0`.
        col_pn = ('COD_PONNOT' if 'COD_PONNOT' in ucbt_por_poste.columns
                  else 'PN_CON' if 'PN_CON' in ucbt_por_poste.columns else None)
        if col_pn is None:
            print("=" * 60)
            return
        cod = {_norm_cod_bdgd(v) for v in ponnot['COD_ID']}
        pn = {_norm_cod_bdgd(str(v).split(';')[0])
              for v in ucbt_por_poste[col_pn]}
        pn.discard('')
        cod.discard('')
        casam = len(pn & cod)
        pct = 100.0 * casam / max(len(pn), 1)
        print(f"  -> Postes com unidade consumidora      : {len(pn)}")
        print(f"  -> ... que existem no PONNOT           : {casam} ({pct:.0f}%)")
        if pct < 90:
            print("  [ATENCAO] Parte das unidades nao acha o poste em que mora "
                  "(UCBT.PN_CON x PONNOT.COD_ID).")
            print("            As cargas saem no caso, mas o mapa perde a "
                  "navegacao do ponto para as unidades dele.")
    print("=" * 60 + "\n")
