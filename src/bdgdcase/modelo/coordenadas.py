# -*- coding: utf-8 -*-
"""Onde cada barra fica no mapa.

Coordenada não muda o fluxo de potência — muda quem consegue olhar para o
resultado e enxergar o que está errado. Um trecho que atravessa a cidade em
diagonal salta aos olhos; o mesmo trecho sem coordenada nenhuma simplesmente
não é desenhado, e escapa da inspeção justamente por ser anômalo.

A BDGD dá geometria para cabos e postes, mas não para toda barra que o caso
cria: nó de chave, barra interna de transformador e ponta de ramal nascem da
conversão e não têm ponto no cadastro. Aqui elas herdam a coordenada do vizinho
— a chave, do trecho que ela abre; o ramal, do poste de qualquer um dos dois
lados — até não sobrar segmento indesenhável.

`_robust_median_lonlat` usa mediana, e não média, de propósito: uma coordenada
com sinal trocado ou vírgula fora do lugar arrasta a média para o meio do
Atlântico, e a mediana ignora.
"""
from __future__ import annotations

import os
import pandas as pd
import statistics

from bdgdcase import ajustes as _ajustes
from bdgdcase.modelo.cadastro import (
    _bus_bt_valido, sanitizar_bus, sanitizar_bus_bt,)


def _geom_line_endpoints_xy(geom):
    """Retorna ((x_ini, y_ini), (x_fim, y_fim)) para LineString / MultiLineString.

    Para MultiLineString (comum em GPKG do BDGD), o erro típico é usar só
    geoms[0]: o PAC_2 acabava no fim do *primeiro* trecho, não no fim lógico do
    cabo — o mapa desenhava MT entre pontos que não coincidem com os nós
    elétricos reais. Convenção alinhada a _construir_gdf_endpoints_mt /
    _construir_gdf_endpoints_bt.
    """
    if geom is None or geom.is_empty:
        return None
    try:
        gt = geom.geom_type
        if gt == 'LineString':
            c = list(geom.coords)
            if len(c) < 1:
                return None
            return (c[0][0], c[0][1]), (c[-1][0], c[-1][1])
        if gt == 'MultiLineString':
            subs = list(geom.geoms)
            if not subs:
                return None
            c_first = list(subs[0].coords)
            c_last = list(subs[-1].coords)
            if not c_first or not c_last:
                return None
            return (c_first[0][0], c_first[0][1]), (c_last[-1][0], c_last[-1][1])
    except Exception:
        return None
    return None

def _ssdmt_edges_xy(ssdmt):
    """Lista (PAC_1, PAC_2, S, E) com S/E = extremos geométricos (lon, lat)."""
    edges = []
    if ssdmt is None or getattr(ssdmt, 'empty', True):
        return edges
    for _, row in ssdmt.iterrows():
        geom = row.get('geometry')
        if geom is None or geom.is_empty:
            continue
        if geom.geom_type not in ('LineString', 'MultiLineString'):
            continue
        ends = _geom_line_endpoints_xy(geom)
        if not ends:
            continue
        S, E = ends
        p1 = sanitizar_bus(str(row.get('PAC_1', '')).strip())
        p2 = sanitizar_bus(str(row.get('PAC_2', '')).strip())
        if not p1 or not p2:
            continue
        edges.append((p1, p2, S, E))
    return edges

def _robust_median_lonlat(points):
    """Mediana (lon, lat) com descarte grosso de outliers entre observações do mesmo PAC.

    Vários trechos SSDMT podem referir o mesmo poste; uma geometria trocada
    gera um ponto longe dos demais — a mediana sozinha fica no meio do nada.
    Descartamos pontos mais distantes que ~3–4 km da mediana provisória e
    recomputamos.
    """
    if not points:
        return None
    if len(points) == 1:
        return float(points[0][0]), float(points[0][1])
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    mx = statistics.median(xs)
    my = statistics.median(ys)
    # ~0,03° ≈ 3 km (latitude média BR) — raio para manter cluster principal
    thr_deg = 0.032
    filt = [(x, y) for x, y in points if (float(x) - mx) ** 2 + (float(y) - my) ** 2 <= thr_deg ** 2]
    if len(filt) >= 1:
        xs = [float(t[0]) for t in filt]
        ys = [float(t[1]) for t in filt]
        mx = statistics.median(xs)
        my = statistics.median(ys)
    return mx, my

def _assign_coords_mt_median_endpoints(ssdmt, untrmt):
    """Coordenadas MT só a partir dos extremos dos segmentos SSDMT (cadastro BDGD).

    Convenção: PAC_1 ↔ primeiro vértice lógico S, PAC_2 ↔ último vértice E
    (``_geom_line_endpoints_xy``). Para cada PAC, **mediana robusta** de todos
    os (S ou E) dos trechos em que participa.

    Não usar posição de UNTRMT como “vizinho” para propagar ao longo da linha:
    o ponto do trafo costuma estar deslocado do eixo do cabo e fazia o
    algoritmo anterior escolher o vértice errado (S vs E), gerando segmentos
    enormes no mapa.

    UNTRMT preenche apenas PACs que **não** aparecem no SSDMT (trafo isolado).
    """
    from collections import defaultdict

    edges = _ssdmt_edges_xy(ssdmt)
    pts = defaultdict(list)
    for p1, p2, S, E in edges:
        pts[p1].append(S)
        pts[p2].append(E)

    reg = {}
    for bus, lst in pts.items():
        xy = _robust_median_lonlat(lst)
        if xy:
            reg[bus] = {'bus': bus, 'x': xy[0], 'y': xy[1]}

    if untrmt is not None and len(untrmt) > 0:
        for _, row in untrmt.iterrows():
            geom = row.get('geometry')
            if geom is None or geom.is_empty or getattr(geom, 'geom_type', '') != 'Point':
                continue
            bus_mt = sanitizar_bus(str(row.get('PAC_1', '')).strip())
            if bus_mt and bus_mt not in reg:
                reg[bus_mt] = {'bus': bus_mt, 'x': float(geom.x), 'y': float(geom.y)}

    return reg

def gerar_buscoords(ssdmt, ssdbt, untrmt, caminho_saida, ramlig=None,
                    unsemt=None, unremt=None):
    """Cria BusCoords.csv extraindo coordenadas diretamente das geometrias dos cabos.

    Geometrias MultiLineString: PAC_1 = primeiro vértice da primeira parte;
    PAC_2 = último vértice da última parte (não só o fim da primeira parte —
    evita mapas com trechos MT “voando” para posições erradas).

    MT: mediana robusta dos extremos SSDMT por PAC (ver
    _assign_coords_mt_median_endpoints).

    RAMLIG (ramais de ligação) não tem geometria própria no BDGD — são conexões
    tabulares de UC ao poste. Os buses PAC_2 desses ramais ficam sem
    coordenadas se não forem tratados aqui. A solução é herdar a coord do PAC_1
    pai (o poste da rede BT ao qual o ramal se conecta).

    UNSEMT e UNREMT (chaves e reguladores) têm o mesmo problema, e pela mesma
    razão: o nó do outro lado do dispositivo não é extremo de cabo nenhum,
    então nenhuma geometria passa por ele. Herda também — um dispositivo de
    manobra tem comprimento zero, e seus dois terminais estão no mesmo poste.
    """
    registros = {}

    def _bus_coord_valido(bus, is_bt=False):
        """A barra tem nome que mereça uma coordenada?

        Coordenada de barra inexistente polui o `BusCoords.csv` e faz o mapa
        desenhar ponto solto no meio do nada — que é justamente o que se
        confunde com erro de rede.
        """
        if not bus:
            return False
        if str(bus).lower() in ('nan', 'none', ''):
            return False
        if is_bt and not _bus_bt_valido(bus):
            return False
        return True

    def extrair_coords_linha(df, is_bt=False):
        """Colhe as coordenadas das pontas de cada trecho, da geometria dele.

        É a fonte primária de coordenada do caso: o poste tem ponto, mas nem
        toda barra é poste. As pontas dos trechos cobrem o resto, e é o que
        permite desenhar um alimentador cujo cadastro de postes está
        incompleto.
        """
        if df is None or len(df) == 0:
            return
        for _, row in df.iterrows():
            geom = row.get('geometry')
            if geom is None or geom.is_empty or geom.geom_type not in ('LineString', 'MultiLineString'):
                continue
            ends = _geom_line_endpoints_xy(geom)
            if not ends:
                continue
            (x1, y1), (x2, y2) = ends
            p1 = sanitizar_bus_bt(row.get('PAC_1', '')) if is_bt else sanitizar_bus(row.get('PAC_1', ''))
            p2 = sanitizar_bus_bt(row.get('PAC_2', '')) if is_bt else sanitizar_bus(row.get('PAC_2', ''))
            if _bus_coord_valido(p1, is_bt=is_bt) and p1 not in registros:
                registros[p1] = {'bus': p1, 'x': x1, 'y': y1}
            if _bus_coord_valido(p2, is_bt=is_bt) and p2 not in registros:
                registros[p2] = {'bus': p2, 'x': x2, 'y': y2}

    # 1. MT: extremos de geometria por trecho + mediana por PAC (sem propagar a partir de UNTRMT)
    registros.update(_assign_coords_mt_median_endpoints(ssdmt, untrmt))

    # 2. BT: coordenadas por extremos de geometria (como antes)
    extrair_coords_linha(ssdbt, is_bt=True)

    # 3. Transformadores: ponto geométrico UNTRMT cobre PAC_1 (MT) e PAC_2 (BT),
    #    que estão fisicamente no mesmo poste. Necessário para plotar GD/cargas
    #    cujo bus foi redirecionado ao PAC_2 do trafo via fallback UNI_TR_MT.
    if untrmt is not None and len(untrmt) > 0:
        for _, row in untrmt.iterrows():
            geom = row.get('geometry')
            if geom is None or geom.is_empty or geom.geom_type != 'Point': continue
            bus_mt = sanitizar_bus(row.get('PAC_1', ''))
            if _bus_coord_valido(bus_mt, is_bt=False) and bus_mt not in registros:
                registros[bus_mt] = {'bus': bus_mt, 'x': geom.x, 'y': geom.y}
            bus_bt = sanitizar_bus_bt(row.get('PAC_2', ''))
            if _bus_coord_valido(bus_bt, is_bt=True) and bus_bt not in registros:
                registros[bus_bt] = {'bus': bus_bt, 'x': geom.x, 'y': geom.y}

    # 4. Ramais de ligação (RAMLIG): PAC_2 = endpoint do UC, sem geometria própria.
    #    Herda a coordenada do PAC_1 (poste pai da rede BT) que já tem coords.
    n_ramlig_ok = 0
    n_ramlig_skip = 0
    if ramlig is not None and len(ramlig) > 0:
        for _, row in ramlig.iterrows():
            p1 = sanitizar_bus_bt(str(row.get('PAC_1', '')))
            p2 = sanitizar_bus_bt(str(row.get('PAC_2', '')))
            if not _bus_coord_valido(p1, is_bt=True) or not _bus_coord_valido(p2, is_bt=True):
                continue
            if p1 in registros and p2 in registros:
                continue
            if p1 not in registros and p2 not in registros:
                n_ramlig_skip += 1
                continue
            # Em qualquer dos dois sentidos, e não só de PAC_1 para PAC_2.
            #
            # Qual das pontas é o poste e qual é a unidade consumidora é
            # convenção da distribuidora, e ela não é a mesma em todas: numa
            # base medida o PAC_1 é a UC (`UC30872`) e o PAC_2 é o poste
            # (`BT278011`) — exatamente ao contrário das outras duas. Assumir
            # um sentido fazia o laço tentar dar ao poste a coordenada da UC,
            # não conseguir, e sair sem herdar nada: 0 de 664 ramais, e metade
            # da rede de baixa daquele alimentador sem como ser desenhada.
            #
            # Quem tem coordenada é o pai, seja qual for o campo em que esteja.
            de, para = (p1, p2) if p1 in registros else (p2, p1)
            registros[para] = {'bus': para,
                               'x': registros[de]['x'],
                               'y': registros[de]['y']}
            n_ramlig_ok += 1

        print(f"  [OK] RAMLIG: {n_ramlig_ok} buses de ramal com coord herdada, {n_ramlig_skip} sem pai conhecido")
        _ajustes.registrar(
            'topologia', 'Ramais de ligação herdaram a coordenada do poste',
            quantos=n_ramlig_ok, unidade='ramais', efeito='desenho',
            detalhe='O ramal de ligação não tem geometria própria na BDGD: é uma '
                    'conexão tabular da unidade ao poste. A coordenada é '
                    'herdada do lado que a tiver, em qualquer sentido.',
            porque='Qual das pontas é o poste é convenção da distribuidora, e não '
                   'é a mesma em todas — numa base o PAC_1 é a unidade e o '
                   'PAC_2 é o poste, ao contrário das outras. Assumir um '
                   'sentido deixava metade da rede de baixa sem como ser '
                   'desenhada.')
        if n_ramlig_skip:
            _ajustes.registrar(
                'topologia', 'Ramais sem poste com coordenada conhecida',
                quantos=n_ramlig_skip, unidade='ramais',
                efeito='nao_corrigido',
                detalhe='Nenhuma das duas pontas destes ramais tinha coordenada.',
                porque='Ficam sem posição no mapa. Não afeta a simulação.')

    # 5. Nós internos de chave e de regulador.
    #
    #    O dispositivo liga PAC_1 a PAC_2, e frequentemente um desses dois nós
    #    existe SÓ ali: nenhum cabo do SSDMT o tem por extremo, de modo que
    #    nenhuma geometria passa por ele e ele fica sem coordenada. O trecho
    #    continua no `.dss` e a simulação continua certa — mas o mapa não
    #    consegue desenhar nada que o toque, e o alimentador aparece com um
    #    buraco: a rede para num poste e recomeça no seguinte.
    #
    #    A escala disso depende da convenção da distribuidora. Onde os nós de
    #    chave coincidem com PACs de cabo, quase não aparece. Noutras bases é a
    #    regra: medimos alimentadores em que 100% das barras de média sem
    #    coordenada vinham só do UNSEMT, e um único nó desses — o do barramento
    #    de manobra — apagava 35 trechos do desenho de uma vez.
    #
    #    Herdar é o certo, e não um remendo: uma chave tem comprimento zero, e
    #    seus dois terminais estão no mesmo poste. O laço repete porque há
    #    cadeias — chave que liga a chave —, e para quando ninguém mais aprende.
    #    Antes da herança vem a geometria do próprio dispositivo, quando ela
    #    existe: a chave é um ponto no cadastro, e nas seis extrações medidas
    #    ela vem preenchida em 100% dos registros, nas três distribuidoras.
    #    Ninguém a usava. É a coordenada mais exata que há para esses nós —
    #    herdar do vizinho põe o nó no poste ao lado, o que basta para fechar o
    #    desenho mas não é o lugar dele.
    n_disp_geom = 0
    for df in (unsemt, unremt):
        if df is None or len(df) == 0 or 'geometry' not in getattr(df, 'columns', []):
            continue
        for _, row in df.iterrows():
            geom = row.get('geometry')
            if geom is None or geom.is_empty or geom.geom_type != 'Point':
                continue
            for pac in ('PAC_1', 'PAC_2'):
                bus = sanitizar_bus(str(row.get(pac, '')))
                if _bus_coord_valido(bus) and bus not in registros:
                    registros[bus] = {'bus': bus, 'x': geom.x, 'y': geom.y}
                    n_disp_geom += 1
    if n_disp_geom:
        print(f"  [OK] Manobra: {n_disp_geom} no(s) com coord do proprio "
              f"dispositivo (ponto do UNSEMT/UNREMT)")
        _ajustes.registrar(
            'topologia', 'Nós de chave localizados pelo ponto do dispositivo',
            quantos=n_disp_geom, unidade='barras', efeito='desenho',
            detalhe='Estas barras existem só no UNSEMT/UNREMT: nenhum cabo as tem '
                    'por extremo, então nenhuma geometria de linha passa por '
                    'elas. A coordenada veio do ponto do próprio dispositivo.',
            porque='Sem coordenada, o mapa não desenha nada que toque a barra, e '
                   'o alimentador aparece com buracos: a rede para num poste e '
                   'recomeça no seguinte. Um único nó de barramento de manobra '
                   'chegou a apagar 35 trechos de uma vez.')

    n_disp_ok = 0
    for _ in range(10):
        aprendeu = 0
        for df in (unsemt, unremt):
            if df is None or len(df) == 0:
                continue
            for _, row in df.iterrows():
                a = sanitizar_bus(str(row.get('PAC_1', '')))
                b = sanitizar_bus(str(row.get('PAC_2', '')))
                if not _bus_coord_valido(a) or not _bus_coord_valido(b):
                    continue
                for de, para in ((a, b), (b, a)):
                    if de in registros and para not in registros:
                        registros[para] = {'bus': para,
                                           'x': registros[de]['x'],
                                           'y': registros[de]['y']}
                        aprendeu += 1
        n_disp_ok += aprendeu
        if not aprendeu:
            break
    if n_disp_ok:
        print(f"  [OK] Manobra: {n_disp_ok} no(s) interno(s) de chave/regulador "
              f"com coord herdada do outro terminal")
        _ajustes.registrar(
            'topologia', 'Nós de chave sem ponto herdaram a coordenada do vizinho',
            quantos=n_disp_ok, unidade='barras', efeito='desenho',
            detalhe='Nestas o próprio dispositivo também não tinha ponto, e a '
                    'coordenada veio do outro terminal dele.',
            porque='Uma chave tem comprimento zero: seus dois terminais estão no '
                   'mesmo poste, então herdar é o certo, e não um remendo.')

    if not registros:
        print(f"  [AVISO] {os.path.basename(caminho_saida)} – sem coordenadas geradas")
        return

    df_coords = pd.DataFrame(list(registros.values()))
    df_coords.to_csv(caminho_saida, index=False, float_format='%.8f',
                     lineterminator='\n')
    print(f"  [OK] {os.path.basename(caminho_saida)} ({len(df_coords)} barramentos mapeados)")
