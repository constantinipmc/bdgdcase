# -*- coding: utf-8 -*-
"""A geração distribuída pendurada na rede.

Fotovoltaica na esmagadora maioria, mas não só: a BDGD registra hidráulica,
térmica e eólica na mesma tabela, com potências e fatores de capacidade que não
têm nada a ver entre si. Tratar tudo como painel solar põe uma PCH gerando ao
meio-dia e parando à noite.

**A potência vem em kW, e às vezes não vem.** Vários campos de potência da BDGD
trazem, no lugar dela, a média do mês de ponta — energia dividida por 720 h.
Num gerador isso aparece como 0,4 kVA onde deveria haver 4 kW, e a diferença é
de ordem de grandeza. `modelo.cadastro` detecta e corrige por unidade; aqui o
valor já chega correto e o que se decide é a curva.

**A curva é por tipo.** Solar vem de `Curva_PV`, gerada por Monte Carlo sobre uma
Beta — não uma parábola limpa, porque nuvem existe e o pico instantâneo importa
para a tensão. Os demais tipos usam o fator de capacidade da própria
tecnologia.
"""
from __future__ import annotations

import geopandas as gpd
import os
import pandas as pd

from bdgdcase import ajustes as _ajustes
from bdgdcase.gd_tipos import fc_efetivo as _fc_efetivo
from bdgdcase.modelo import config
from bdgdcase.modelo.registro import _diag_set, _fmt_amostra
from bdgdcase.modelo.config import (
    TENSAO_BT_KV,)
from bdgdcase.modelo.cadastro import (
    RENDIMENTO_ANO_KWH_POR_KW,
    _bus_bt_valido, _classificar_tipo_gd, _num, eh_center_tap, fases_info,
    kv_bt_para_fases, LIMITE_MINIGD_KW, pot_inst_kw,
    sanitizar_bus, sanitizar_bus_bt, segundo_enrolamento_por_trafo,)
from bdgdcase.modelo.cadastro_csv import _primeiro, _texto
from bdgdcase.modelo.curvas import _CURVA_POR_TIPO_GD
from bdgdcase.modelo.topologia import (
    _construir_gdf_endpoints_bt, _construir_gdf_endpoints_mt,
    _construir_mapa_bt, _resolver_bus_bt, _trafo_bus_map, nos_vivos,)


def gerar_gd(ugbt, ugmt, ponnot, ssdmt, ssdbt, ramlig, untrmt,
             caminho_saida, cadastro=None, eqtrmt=None,
             barras_existentes=None, nos_existentes=None):
    """Cria GD.dss com Grampo Topológico Híbrido (Herança de Fases do Trafo).

    ``barras_existentes``: as barras de baixa que os emissores de linhas e de
    transformadores DE FATO escreveram (nomes em minúsculas). Uma usina
    resolvida para barra fora deste conjunto vai ao secundário do
    transformador do cadastro, ou fica de fora, contada.

    ``nos_existentes``: ``{barra: [nós]}`` que esses mesmos emissores
    escreveram. Os nós da usina são grampeados a eles (:func:`nos_vivos`).

    As duas são a guarda que importa: um PVSystem numa barra ou num nó que a
    rede não energiza não é um kW perdido, é o dia inteiro do alimentador
    com o balanço aberto.

    **O autoconsumo não é reconstituído, e é deliberado.** A geração que já
    consta da BDGD vira PVSystem com os dados originais, e a carga daquela mesma
    unidade usa o `ENE_MED` como está publicado — que já é energia LÍQUIDA, o
    que sobrou depois de a geração ter abatido parte do consumo.

    Somar de volta o consumo bruto exigiria supor quanto cada unidade
    autoconsumiu, e essa suposição não está na base. O `ENE_MED` publicado é o
    estado do alimentador no instante da extração, e é o que este pacote
    modela: o que a distribuidora mediu, não o que teria sido medido sem a
    geração.
    """
    import math
    import re as _re

    linhas = [
        "! ============================================================",
        "! GD.dss – Geracao Distribuida Individual (UGBT / UGMT)",
        "! ============================================================",
        "",
    ]

    # ── 1. MAPAS GLOBAIS DE RESOLUÇÃO ──────────────────────────────────────────
    valid_buses, ramlig_map = _construir_mapa_bt(ssdbt, ramlig)
    trafo_bus_map = _trafo_bus_map(untrmt) if untrmt is not None else {}
    _FASE_NO = {'A': '1', 'B': '2', 'C': '3'}

    # NOVO: Dicionário estendido de Propriedades dos Trafos
    trafo_props = {}
    _seg_gd = segundo_enrolamento_por_trafo(eqtrmt)
    if untrmt is not None and not untrmt.empty:
        for _, row in untrmt.iterrows():
            cod = sanitizar_bus(str(row['COD_ID']).strip())
            tip = str(row.get('TIP_TRAFO', 'T')).upper().strip()
            ten = float(row.get('TEN_LIN_SE', TENSAO_BT_KV) or TENSAO_BT_KV)
            fas_s = str(row.get('FAS_CON_S', '')).upper().strip()
            trafo_props[cod] = {'tip': tip, 'ten': ten, 'fas_s': fas_s,
                                'ct': _seg_gd.get(cod)}

    # Sniffer BT e MT (Mantidos) — NaN no FAS_CON é normalizado para ABC
    def _parse_fas(fas_raw):
        """Converte FAS_CON string → conjunto de letras de fase válidas (A/B/C). NaN → ABC."""
        s = str(fas_raw).strip()
        if s.lower() in ('nan', 'none', ''):
            return 'ABC'
        result = ''.join(c for c in s.upper() if c in ('A', 'B', 'C'))
        return result if result else 'ABC'

    bt_bus_fases = {}
    if ssdbt is not None and not ssdbt.empty:
        for _, row in ssdbt.iterrows():
            fas = _parse_fas(row.get('FAS_CON', 'ABC'))
            for pac in ('PAC_1', 'PAC_2'):
                b = sanitizar_bus_bt(str(row.get(pac, '')).strip())
                if b not in bt_bus_fases: bt_bus_fases[b] = set()
                for f in fas:
                    if f in _FASE_NO: bt_bus_fases[b].add(_FASE_NO[f])

    mt_bus_fases = {}
    if ssdmt is not None and not ssdmt.empty:
        for _, row in ssdmt.iterrows():
            fas = _parse_fas(row.get('FAS_CON', 'ABC'))
            for pac in ('PAC_1', 'PAC_2'):
                b = sanitizar_bus(str(row.get(pac, '')).strip())
                if b not in mt_bus_fases: mt_bus_fases[b] = set()
                for f in fas:
                    if f in _FASE_NO: mt_bus_fases[b].add(_FASE_NO[f])

    # Mapa bus BT -> conjunto de UNI_TR_MT que o referenciam (SSDBT + PAC_2 do trafo).
    # Usado para validar se o fallback espacial "atravessou" para outro transformador.
    bus_trafos_bt = {}
    if ssdbt is not None and not ssdbt.empty and 'UNI_TR_MT' in ssdbt.columns:
        for _, row in ssdbt.iterrows():
            _uni = sanitizar_bus(str(row.get('UNI_TR_MT', '')).strip())
            if not _uni or _uni.lower() in ('nan', 'none', ''):
                continue
            for pac in ('PAC_1', 'PAC_2'):
                b = sanitizar_bus_bt(str(row.get(pac, '')).strip())
                if b:
                    bus_trafos_bt.setdefault(b, set()).add(_uni)
    if untrmt is not None and not untrmt.empty:
        for _, row in untrmt.iterrows():
            _uni = sanitizar_bus(str(row.get('COD_ID', '')).strip())
            b = sanitizar_bus_bt(str(row.get('PAC_2', '')).strip())
            if _uni and b:
                bus_trafos_bt.setdefault(b, set()).add(_uni)

    cnt_bt_direto = cnt_bt_ramlig = cnt_bt_espacial = cnt_bt_trafo = cnt_bt_skip = 0
    cnt_bt_espacial_corrigido = 0
    cnt_mt_ok = cnt_mt_skip = 0
    ugmt_skip_pot = []
    # Quantas unidades tiveram a potencia reconstruida porque o cadastro trazia
    # a media do mes de pico no lugar da instalada. Sai no log porque e
    # estimativa, e quem usa o caso tem de saber disso sem ler o codigo.
    cnt_pot_estimada = 0
    acima_do_teto = []
    teto_sem_energia = []
    #: kW de geração acumulados por transformador, para conferir contra a
    #: potência dele no fim.
    gd_por_trafo = {}
    ugmt_skip_bus = []
    ugbt_barra_invalida = []
    cnt_bt_barra_morta_remetida = 0
    cnt_bt_no_morto = 0
    ugmt_usou_pac = 0
    ugmt_usou_pn  = 0
    contagem = 0
    contagem_por_tipo = {}

    # ── 2. UGBT – PAINÉIS FOTOVOLTAICOS EM BAIXA TENSÃO ────────────────────────
    if ugbt is not None and not ugbt.empty:
        linhas.append("! --- UGBT: Painéis FV em Baixa Tensão ---")
        linhas.append("")

        # Mapa espacial UGBT: PN_CON (PONNOT) -> nearest PAC do SSDBT.
        # Evita cair sempre no fallback do trafo quando PN_CON e PAC usam codificações distintas.
        spatial_map_ugbt = {}
        if (ssdbt is not None and not ssdbt.empty
                and ponnot is not None and not ponnot.empty
                and hasattr(ponnot, 'geometry')):
            try:
                gdf_ep_bt = _construir_gdf_endpoints_bt(ssdbt)
                if gdf_ep_bt is not None and not gdf_ep_bt.empty:
                    col_cod = None
                    for c in ('COD_ID', 'COD_PONNOT'):
                        if c in ponnot.columns:
                            col_cod = c
                            break
                    if col_cod is not None:
                        pn_set = {
                            sanitizar_bus(str(v).strip())
                            for v in ugbt.get('PN_CON', pd.Series(dtype=str)).tolist()
                            if str(v).strip() not in ('', 'nan', 'None')
                        }
                        pts = ponnot[[col_cod, 'geometry']].copy()
                        pts[col_cod] = pts[col_cod].astype(str).str.strip()
                        pts['_pn'] = pts[col_cod].map(lambda x: sanitizar_bus(x))
                        pts = pts[pts['_pn'].isin(pn_set)]
                        pts = pts[pts.geometry.notna() & ~pts.geometry.is_empty]
                        if not pts.empty:
                            if pts.crs != gdf_ep_bt.crs:
                                pts = pts.to_crs(gdf_ep_bt.crs)
                            if pts.crs is not None and pts.crs.is_geographic:
                                try:
                                    proj = pts.estimate_utm_crs()
                                except Exception:
                                    proj = 'EPSG:5880'
                                pts = pts.to_crs(proj)
                                gdf_ep_bt = gdf_ep_bt.to_crs(proj)

                            joined = gpd.sjoin_nearest(
                                pts.set_geometry('geometry'),
                                gdf_ep_bt[['bus', 'geometry']].set_geometry('geometry'),
                                how='left',
                            )
                            for _, jrow in joined.iterrows():
                                pn_key = sanitizar_bus(str(jrow.get('_pn', '')).strip())
                                bnn = jrow.get('bus', None)
                                if pn_key and pd.notna(bnn):
                                    spatial_map_ugbt[pn_key] = sanitizar_bus_bt(str(bnn))
            except Exception:
                pass

        for _, row in ugbt.iterrows():
            pn_raw = str(row.get('PN_CON', '')).strip()
            uni_tr = str(row.get('UNI_TR_MT', '')).strip()
            cod_raw_bt = str(row.get('COD_ID', '')).strip()
            pot, origem_pot = pot_inst_kw(row)
            if origem_pot == 'estimada':
                cnt_pot_estimada += 1
            elif origem_pot == 'teto':
                acima_do_teto.append((str(row.get('COD_ID', ''))[:30],
                                      _num(row.get('POT_INST'), 0.0), pot))
            elif origem_pot == 'teto_sem_energia':
                teto_sem_energia.append((str(row.get('COD_ID', ''))[:30],
                                         _num(row.get('POT_INST'), 0.0)))
            _uni_gd = sanitizar_bus(str(row.get('UNI_TR_MT', '')).split(';')[0].strip())
            if _uni_gd and pot > 0:
                gd_por_trafo[_uni_gd] = gd_por_trafo.get(_uni_gd, 0.0) + pot
            if math.isnan(pot) or pot <= 0:
                cnt_bt_skip += 1
                continue

            bus, resolucao = _resolver_bus_bt(pn_raw, valid_buses, ramlig_map)

            if resolucao == 'flutuante':
                nn_bt = spatial_map_ugbt.get(sanitizar_bus(pn_raw))
                if nn_bt:
                    bus = nn_bt
                    resolucao = 'espacial'

            # Validação cruzada pós-espacial:
            # se o PAC nearest pertence a outro trafo (vs. UNI_TR_MT da UGBT),
            # redireciona ao PAC_2 do trafo correto.
            if resolucao == 'espacial' and trafo_bus_map and uni_tr and bus:
                unis_cadastro = {
                    sanitizar_bus(u.strip())
                    for u in str(uni_tr).split(';')
                    if u.strip() and u.strip().lower() not in ('nan', 'none')
                }
                unis_bus = bus_trafos_bt.get(bus, set())
                if unis_cadastro and unis_bus and unis_cadastro.isdisjoint(unis_bus):
                    for uni in unis_cadastro:
                        fb = trafo_bus_map.get(uni)
                        if fb:
                            bus = fb
                            resolucao = 'trafo'
                            cnt_bt_espacial_corrigido += 1
                            break

            if resolucao == 'flutuante' and uni_tr and uni_tr.lower() not in ('nan', 'none', ''):
                for uni in str(uni_tr).split(';'):
                    fb = trafo_bus_map.get(sanitizar_bus(uni.strip()))
                    if fb:
                        bus = fb
                        resolucao = 'trafo'
                        break

            # A carga do mesmo poste já recusa placeholder aqui (cargas.py);
            # a usina tem de recusar também. Uma fonte numa barra que nenhuma
            # linha nem transformador toca não é "um kW a menos": é uma barra
            # morta com injeção, e isso derruba a solução do circuito inteiro.
            if not _bus_bt_valido(bus):
                ugbt_barra_invalida.append((cod_raw_bt[:30], bus))
                cnt_bt_skip += 1
                continue

            # O nome passou; a barra existe? Só quem escreveu as linhas e os
            # transformadores sabe, e a resposta vem em `barras_existentes`.
            if barras_existentes is not None and bus.lower() not in barras_existentes:
                remetida = None
                for uni in str(uni_tr).split(';'):
                    cand = trafo_bus_map.get(sanitizar_bus(uni.strip()))
                    if cand and cand.lower() in barras_existentes:
                        remetida = cand
                        break
                if remetida:
                    bus = remetida
                    resolucao = 'trafo'
                    cnt_bt_barra_morta_remetida += 1
                else:
                    ugbt_barra_invalida.append((cod_raw_bt[:30], bus))
                    cnt_bt_skip += 1
                    continue

            if resolucao == 'flutuante':
                cnt_bt_skip += 1
                continue

            if resolucao == 'direto':  cnt_bt_direto += 1
            elif resolucao == 'ramlig': cnt_bt_ramlig += 1
            elif resolucao == 'espacial': cnt_bt_espacial += 1
            else:                       cnt_bt_trafo  += 1

            fas_gd = str(row.get('FAS_CON', 'AN')).upper().strip()
            _, _, sfx_pedido, _ = fases_info(fas_gd)
            cod = str(row.get('COD_ID', '')).strip()[:30]

            is_mrt = False
            kv_trafo = TENSAO_BT_KV
            fas_trafo_sec = ''
            t_tip_gd = ''
            _ct_gd = None

            # Rastreia o Transformador
            if uni_tr:
                cod_trafo = sanitizar_bus(uni_tr.split(';')[0].strip())
                if cod_trafo in trafo_props:
                    t_tip = t_tip_gd = trafo_props[cod_trafo]['tip']
                    _ct_gd = trafo_props[cod_trafo].get('ct')
                    t_ten = trafo_props[cod_trafo]['ten']
                    fas_trafo_sec = trafo_props[cod_trafo]['fas_s']
                    kv_trafo = t_ten
                    
                    # Trafo Monofásico Puro ou MRT Center-Tap -> Limita as fases
                    fases_reais = fas_trafo_sec.replace('N', '')
                    if (eh_center_tap(t_tip, t_ten,
                                      trafo_props[cod_trafo].get('ct'))
                            or len(fases_reais) == 1):
                        is_mrt = True

            # Grampo de Fase com HERANÇA
            nos_locais = bt_bus_fases.get(bus, set())
            pedidos = set(_re.findall(r'[1-3]', sfx_pedido))
            
            # CORREÇÃO MRT: sniffer usa rótulo MT (ex: fase C → nó 3) que não casa com os
            # nós reais do trafo center-tap (p_bt1/p_bt2). Replicar fallback FAS_CON_S de
            # gerar_transformadores: se sniffer deu < 2 letras, usar FAS_CON_S do trafo.
            if is_mrt and fas_trafo_sec:
                _ltrs = {k for k, v in _FASE_NO.items() if v in nos_locais}
                if len(_ltrs) < 2:
                    _ltrs = {c for c in fas_trafo_sec.replace('N', '') if c in _FASE_NO}
                if _ltrs:
                    _sl = sorted(_ltrs)
                    _p1 = _FASE_NO[_sl[0]]
                    _p2 = _FASE_NO[_sl[1]] if len(_sl) > 1 else ('2' if _p1 == '1' else '1')
                    nos_locais = {_p1, _p2}
            elif not nos_locais and fas_trafo_sec:
                for f in fas_trafo_sec.replace('N', ''):
                    if f in _FASE_NO: nos_locais.add(_FASE_NO[f])

            validos = sorted(pedidos & nos_locais) if nos_locais else sorted(pedidos)
            if not validos:
                validos = [sorted(nos_locais)[0]] if nos_locais else ['1']

            # `is_mrt` acima é ligado também por secundário de uma fase só,
            # que na RGE é o monofásico comum e não um center-tap — daí
            # perguntar de novo, pela regra única.
            e_center_tap = eh_center_tap(t_tip_gd, kv_trafo, _ct_gd)
            par_kv = ((kv_trafo, kv_trafo / 2.0) if e_center_tap
                      else (kv_trafo, kv_trafo / math.sqrt(3)))

            if is_mrt:
                # O center-tap tem DUAS pernas, e o atendimento a três fios usa
                # as duas. A carga do mesmo poste já sai assim — `FAS_CON=ABN`
                # vira `.1.2.0 phases=2` —, e a usina saía numa perna só, com o
                # comentário de que era "a física do trafo". Não era: jogar
                # todo o kW numa perna dobra a corrente nela, deixa a outra
                # vazia e desequilibra o center-tap. Medido num alimentador da
                # Celesc, 116 usinas assim: a perna injetada ia a 320,5 V e a
                # outra ficava em 218,9, e o caso fechava em 1,461 pu. Ligando
                # as duas, as pernas empatam em 266 V e o caso vai a 1,215.
                #
                # Só se usa a segunda perna quando o cadastro a pede E o
                # transformador a tem. Secundário de uma fase só continua numa
                # perna, que ali é a rede inteira.
                if e_center_tap and len(validos) >= 2:
                    validos = validos[:2]
                else:
                    validos = [validos[0]]
                nph_gd = len(validos)
                sfx_bt = '.' + '.'.join(validos) + '.0'
            else:
                nph_gd = len(validos)
                sfx_bt = '.' + '.'.join(validos) + ('.0' if ('N' in fas_gd or nph_gd < 3) else '')

            # Última palavra sobre os nós: os que a rede escreveu nesta barra.
            if nos_existentes is not None:
                validos, mudou = nos_vivos(validos, nos_existentes.get(bus.lower()))
                if mudou:
                    cnt_bt_no_morto += 1
                    nph_gd = len(validos)
                    sfx_bt = '.' + '.'.join(validos) + '.0'

            # A mesma regra da carga, e pela mesma razão: o OpenDSS divide a
            # `kV` do elemento multifásico por √3, tenha o secundário as pernas
            # a 180° ou as fases a 120°.
            kv_use = kv_bt_para_fases(nph_gd, par_kv)

            tipo_gd, fc_gd = _classificar_tipo_gd(row, pot)
            if tipo_gd == 'PV':
                linhas += [
                    f"New PVSystem.GD_BT_{cod} bus1={bus}{sfx_bt} phases={nph_gd}",
                    f"~ kv={kv_use:.4f} kva={pot:.3f} pmpp={pot:.3f} pf=1.0 daily=Curva_PV",
                    f"  ! tipo=PV fc={fc_gd:.0%} FAS_PEDIDA={fas_gd} FAS_REAL={fas_trafo_sec} mrt={is_mrt}", ""
                ]
            else:
                # Geração de base: a potência despachada é a MÉDIA (POT_INST × FC).
                # A curva é flat, então kw já é a potência média — usar POT_INST
                # faria a unidade gerar 100% da nominal 24 h por dia.
                _curva = _CURVA_POR_TIPO_GD.get(tipo_gd, 'Curva_CGH')
                _fc = _fc_efetivo(tipo_gd, fc_gd)
                _kw = pot * _fc
                linhas += [
                    f"New Generator.GD_BT_{tipo_gd}_{cod} bus1={bus}{sfx_bt} phases={nph_gd}",
                    f"~ kv={kv_use:.4f} kw={_kw:.3f} pf=1.0 model=1 daily={_curva}",
                    f"  ! tipo={tipo_gd} CEG={row.get('CEG_GD','')} pot_nom={pot:.1f}kW fc={_fc:.0%} "
                    f"FAS_PEDIDA={fas_gd} mrt={is_mrt}", ""
                ]
            contagem += 1
            contagem_por_tipo[tipo_gd] = contagem_por_tipo.get(tipo_gd, 0) + 1
            if cadastro is not None:
                cadastro.append({
                    'elemento': (('gd_%s_%s' % ('bt', cod)) if tipo_gd == 'PV'
                                 else ('gd_%s_%s_%s' % ('bt', tipo_gd, cod))).lower(),
                    'cod_id': str(row.get('COD_ID', '') or '').strip(),
                    'ceg_gd': _texto(row.get('CEG_GD')),
                    # A fonte não vem da BDGD: `TIP_GER` não existe nas tabelas
                    # de unidade geradora, e o prefixo `GD.` do CEG significa
                    # micro ou minigeração, não solar. Isto aqui é o que o
                    # conversor concluiu, e o fator de capacidade que o levou lá.
                    'fonte': tipo_gd,
                    'pot_inst_kw': pot,
                    'pot_origem': origem_pot,
                    'fc_pct': 100.0 * fc_gd,
                    'fas_con': fas_gd,
                    'bus': bus,
                    'kv': kv_use,
                    'mun': _primeiro(row.get('MUN')),
                    **{('ene_%02d' % m): _texto(row.get('ENE_%02d' % m))
                       for m in range(1, 13)},
                })

    # ── 3. UGMT – USINAS SOLARES EM MÉDIA TENSÃO ───────────────────────────────
    if ugmt is not None and not ugmt.empty:
        _diag_set("ugmt_total", len(ugmt))
        linhas.append("! --- UGMT: Usinas FV em Média Tensão ---")
        linhas.append("")

        # Mapa espacial UGMT → nearest PAC do SSDMT (mesmo padrão do UCMT)
        spatial_map_ugmt = {}
        if ssdmt is not None and not ssdmt.empty and hasattr(ugmt, 'geometry'):
            try:
                _gdf_ep = _construir_gdf_endpoints_mt(ssdmt)
                if _gdf_ep is not None:
                    _ugmt_pts = ugmt[['geometry']].copy()
                    _ugmt_pts = _ugmt_pts[_ugmt_pts.geometry.notna() & ~_ugmt_pts.geometry.is_empty]
                    if not _ugmt_pts.empty:
                        if _ugmt_pts.crs != _gdf_ep.crs:
                            _ugmt_pts = _ugmt_pts.to_crs(_gdf_ep.crs)
                        if _ugmt_pts.crs is not None and _ugmt_pts.crs.is_geographic:
                            try:
                                _proj = _ugmt_pts.estimate_utm_crs()
                            except Exception:
                                _proj = 'EPSG:5880'
                            _ugmt_pts = _ugmt_pts.to_crs(_proj)
                            _gdf_ep   = _gdf_ep.to_crs(_proj)
                        _joined = gpd.sjoin_nearest(
                            _ugmt_pts.set_geometry('geometry'),
                            _gdf_ep[['bus', 'geometry']].set_geometry('geometry'),
                            how='left',
                        )
                        for _idx, _jrow in _joined.iterrows():
                            _nn = _jrow.get('bus', None)
                            if pd.notna(_nn):
                                spatial_map_ugmt[_idx] = sanitizar_bus(str(_nn))
            except Exception:
                pass
        elif ssdmt is not None and not ssdmt.empty and ponnot is not None and not ponnot.empty and hasattr(ponnot, "geometry"):
            # UGMT_tab pode vir como CSV sem geometria (por otimização do Extrator).
            # Nesse caso, tentamos recuperar a geometria via PONNOT(COD_ID=PN_CON) e
            # fazer o mesmo nearest PAC do SSDMT.
            try:
                _gdf_ep = _construir_gdf_endpoints_mt(ssdmt)
                if _gdf_ep is not None and "COD_ID" in ponnot.columns:
                    pn_col = "PN_CON" if "PN_CON" in ugmt.columns else None
                    if pn_col:
                        pn_geom = ponnot[["COD_ID", "geometry"]].copy()
                        pn_geom["COD_ID"] = pn_geom["COD_ID"].astype(str).str.strip()
                        pn_geom = pn_geom[pn_geom.geometry.notna() & ~pn_geom.geometry.is_empty]
                        lookup = {str(r["COD_ID"]).strip(): r["geometry"] for _, r in pn_geom.iterrows()}

                        pts = ugmt[[pn_col]].copy()
                        pts[pn_col] = pts[pn_col].astype(str).str.strip()
                        pts["geometry"] = pts[pn_col].map(lambda x: lookup.get(str(x).strip()))
                        pts = pts[pts["geometry"].notna()]
                        if not pts.empty:
                            pts = gpd.GeoDataFrame(pts, geometry="geometry", crs=ponnot.crs)
                            if pts.crs != _gdf_ep.crs:
                                pts = pts.to_crs(_gdf_ep.crs)
                            if pts.crs is not None and pts.crs.is_geographic:
                                try:
                                    _proj = pts.estimate_utm_crs()
                                except Exception:
                                    _proj = "EPSG:5880"
                                pts = pts.to_crs(_proj)
                                _gdf_ep = _gdf_ep.to_crs(_proj)

                            _joined = gpd.sjoin_nearest(
                                pts.set_geometry("geometry"),
                                _gdf_ep[["bus", "geometry"]].set_geometry("geometry"),
                                how="left",
                            )
                            for _idx, _jrow in _joined.iterrows():
                                _nn = _jrow.get("bus", None)
                                if pd.notna(_nn):
                                    spatial_map_ugmt[_idx] = sanitizar_bus(str(_nn))
            except Exception:
                pass

        for idx_ugmt, row in ugmt.iterrows():
            pn_raw = str(row.get('PN_CON', '')).strip()
            pot, origem_pot = pot_inst_kw(row)
            if origem_pot == 'estimada':
                cnt_pot_estimada += 1
            elif origem_pot == 'teto':
                acima_do_teto.append((str(row.get('COD_ID', ''))[:30],
                                      _num(row.get('POT_INST'), 0.0), pot))
            elif origem_pot == 'teto_sem_energia':
                teto_sem_energia.append((str(row.get('COD_ID', ''))[:30],
                                         _num(row.get('POT_INST'), 0.0)))
            _uni_gd = sanitizar_bus(str(row.get('UNI_TR_MT', '')).split(';')[0].strip())
            if _uni_gd and pot > 0:
                gd_por_trafo[_uni_gd] = gd_por_trafo.get(_uni_gd, 0.0) + pot
            cod_raw = str(row.get('COD_ID', '')).strip()
            if math.isnan(pot) or pot <= 0:
                cnt_mt_skip += 1
                ugmt_skip_pot.append((cod_raw[:30], pn_raw, pot))
                continue

            # Preferência: se houver PAC na tabela, ele é o barramento elétrico correto.
            pac_raw = str(row.get("PAC", "")).strip() if "PAC" in ugmt.columns else ""
            if pac_raw and pac_raw.lower() not in ("nan", "none", "0", ""):
                bus = sanitizar_bus(pac_raw)
                ugmt_usou_pac += 1
            else:
                bus = sanitizar_bus(pn_raw)
                ugmt_usou_pn += 1
            if bus not in mt_bus_fases:
                # Fallback espacial: nearest PAC do SSDMT
                nn_bus = spatial_map_ugmt.get(idx_ugmt)
                if nn_bus:
                    bus = nn_bus
                else:
                    cnt_mt_skip += 1
                    ugmt_skip_bus.append((cod_raw[:30], pn_raw))
                    continue

            fas_gd = str(row.get('FAS_CON', 'ABC')).upper().strip()
            _, sfx_pedido, _, _ = fases_info(fas_gd)
            cod = cod_raw[:30]

            nos_locais = mt_bus_fases.get(bus, set())
            pedidos = set(_re.findall(r'[1-3]', sfx_pedido))
            validos = sorted(pedidos & nos_locais) if nos_locais else sorted(pedidos)
            if not validos:
                validos = [sorted(nos_locais)[0]] if nos_locais else ['1', '2', '3']

            nph_gd = len(validos)
            sfx_mt = '.' + '.'.join(validos)
            kv_use = (config.TENSAO_MT_KV / math.sqrt(3)) if nph_gd == 1 else config.TENSAO_MT_KV

            tipo_gd, fc_gd = _classificar_tipo_gd(row, pot)
            if tipo_gd == 'PV':
                linhas += [
                    f"New PVSystem.GD_MT_{cod} bus1={bus}{sfx_mt} phases={nph_gd}",
                    f"~ kv={kv_use:.4f} kva={pot:.3f} pmpp={pot:.3f} pf=1.0 daily=Curva_PV",
                    f"  ! tipo=PV fc={fc_gd:.0%} FAS={fas_gd} clamp={validos}", ""
                ]
            else:
                # Geração de base: kw = POT_INST × FC (a curva é flat — ver GD BT).
                _curva = _CURVA_POR_TIPO_GD.get(tipo_gd, 'Curva_CGH')
                _fc = _fc_efetivo(tipo_gd, fc_gd)
                _kw = pot * _fc
                linhas += [
                    f"New Generator.GD_MT_{tipo_gd}_{cod} bus1={bus}{sfx_mt} phases={nph_gd}",
                    f"~ kv={kv_use:.4f} kw={_kw:.3f} pf=1.0 model=1 daily={_curva}",
                    f"  ! tipo={tipo_gd} CEG={row.get('CEG_GD','')} pot_nom={pot:.1f}kW fc={_fc:.0%} "
                    f"FAS={fas_gd} clamp={validos}", ""
                ]
            cnt_mt_ok += 1
            contagem += 1
            contagem_por_tipo[tipo_gd] = contagem_por_tipo.get(tipo_gd, 0) + 1
            if cadastro is not None:
                cadastro.append({
                    'elemento': (('gd_mt_%s' % cod) if tipo_gd == 'PV'
                                 else ('gd_mt_%s_%s' % (tipo_gd, cod))).lower(),
                    'cod_id': str(row.get('COD_ID', '') or '').strip(),
                    'ceg_gd': _texto(row.get('CEG_GD')),
                    'fonte': tipo_gd,
                    'pot_inst_kw': pot,
                    'pot_origem': origem_pot,
                    'fc_pct': 100.0 * fc_gd,
                    'fas_con': fas_gd,
                    'bus': bus,
                    'kv': kv_use,
                    'mun': _primeiro(row.get('MUN')),
                    **{('ene_%02d' % m): _texto(row.get('ENE_%02d' % m))
                       for m in range(1, 13)},
                })
    _diag_set("ugmt_ok", int(cnt_mt_ok))
    _diag_set("ugmt_skip_total", int(cnt_mt_skip))
    _diag_set("ugmt_skip_pot", len(ugmt_skip_pot))
    _diag_set("ugmt_skip_bus", len(ugmt_skip_bus))
    _diag_set("ugbt_barra_invalida", len(ugbt_barra_invalida))

    with open(caminho_saida, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(linhas))

    print(f"  [OK] {os.path.basename(caminho_saida)} – {contagem} geradores GD com Herança de Fase")
    if contagem_por_tipo:
        _tipos_str = ' '.join(f"{k}:{v}" for k, v in sorted(contagem_por_tipo.items()))
        print(f"  [GD-TIPOS] {_tipos_str}")
    print(f"  [DIAG] UGBT -> direto:{cnt_bt_direto} ramlig:{cnt_bt_ramlig} espacial:{cnt_bt_espacial} trafo:{cnt_bt_trafo} skip:{cnt_bt_skip}")
    if cnt_bt_espacial_corrigido:
        print(f"  [DIAG] UGBT -> espacial->trafo (cross-trafo): {cnt_bt_espacial_corrigido}")
    print(f"  [DIAG] UGMT -> ok:{cnt_mt_ok} skip:{cnt_mt_skip}")
    if ugmt is not None and not ugmt.empty:
        print(f"  [DIAG] UGMT -> resolução de barramento: PAC={ugmt_usou_pac} PN_CON={ugmt_usou_pn} spatial={len(spatial_map_ugmt)}")
    if ugmt_skip_pot:
        print(f"  [DIAG] UGMT descartada por POT_INST<=0/NaN: {len(ugmt_skip_pot)}")
        print(f"         amostra (COD_ID, PN_CON, POT): {_fmt_amostra(ugmt_skip_pot)}")
    # A geração não pode ser muito maior que o transformador que a alimenta: o
    # inversor não passa por onde não cabe. Quando o cadastro diz que passa, é
    # inconsistência dele — transformador trocado sem o registro acompanhar, ou
    # a unidade ligada noutro ponto —, e o caso resolvido mostra a rede de baixa
    # daquele trecho em sobretensão sem que nada explique por quê.
    #
    # Não se corrige aqui, e é decisão: cortar pela potência do transformador
    # inventaria um dado que a base não tem, e esconderia justamente o que
    # precisa ser visto. O caso sai como o cadastro manda, e o aviso diz onde
    # olhar.
    _kva_trafo = {}
    if untrmt is not None and not getattr(untrmt, 'empty', True):
        for _, _t in untrmt.iterrows():
            _c = sanitizar_bus(str(_t.get('COD_ID', '')).strip())
            _k = _num(_t.get('POT_NOM'), 0.0)
            if _c and _k > 0:
                _kva_trafo[_c] = _k
    _excedidos = []
    for _uni, _kw in gd_por_trafo.items():
        _kva = _kva_trafo.get(_uni)
        if _kva and _kw > 1.2 * _kva:
            _excedidos.append((_uni, _kw, _kva))
    if _excedidos:
        _excedidos.sort(key=lambda x: -x[1] / x[2])
        print('  [ATENCAO] %d transformador(es) com geracao acima da propria '
              'potencia:' % len(_excedidos))
        for _uni, _kw, _kva in _excedidos[:5]:
            print('            TR_%s: %.1f kW de GD em %.0f kVA (%.1fx). A rede '
                  'de baixa dele vai aparecer' % (_uni, _kw, _kva, _kw / _kva))
            print('            em sobretensao, e a causa esta no cadastro, nao '
                  'no modelo.')
        if len(_excedidos) > 5:
            print('            ... e mais %d.' % (len(_excedidos) - 5))
        _ajustes.registrar(
            'nao_corrigido', 'Geração maior que o transformador que a alimenta',
            quantos=len(_excedidos), unidade='transformadores',
            efeito='nao_corrigido',
            detalhe='O cadastro registra mais geração do que o transformador '
                    'comporta. O maior caso: %.1f kW em %.0f kVA (%.1f vezes).'
                    % (_excedidos[0][1], _excedidos[0][2],
                       _excedidos[0][1] / _excedidos[0][2]),
            porque='A rede de baixa desses transformadores vai aparecer em '
                   'sobretensão, e a causa está no registro — transformador '
                   'trocado sem o cadastro acompanhar, ou unidade ligada '
                   'noutro ponto. Cortar pela potência do transformador '
                   'inventaria um dado que a base não tem e esconderia '
                   'justamente isto.',
            exemplos=['TR_%s (%.0f kW / %.0f kVA)' % (u, k, v)
                      for u, k, v in _excedidos[:4]])

    if acima_do_teto:
        maior = max(acima_do_teto, key=lambda x: x[1])
        _ajustes.registrar(
            'potencia', 'Geração acima do teto legal, potência reconstruída',
            quantos=len(acima_do_teto), unidade='unidades geradoras',
            efeito='simulacao',
            detalhe='O cadastro declarava mais de %.0f MW numa unidade '
                    'geradora de consumidor. O maior caso: %.0f kW '
                    'declarados, %.1f kW reconstruídos da energia do ano.'
                    % (LIMITE_MINIGD_KW / 1000.0, maior[1], maior[2]),
            porque='A Lei 14.300/2022 limita a minigeração distribuída a '
                   '%.0f MW; acima disso não é geração distribuída, é central '
                   'geradora. Na unidade que motivou a regra, o campo '
                   '`POT_INST` era dígito por dígito o `ENE_12` da mesma '
                   'linha. Mantida como estava, ela sozinha valia nove vezes '
                   'a carga média do alimentador e invertia o fluxo inteiro — '
                   'um resultado que parece estudo de hospedagem e é erro de '
                   'cadastro.' % (LIMITE_MINIGD_KW / 1000.0),
            exemplos=['%s: %.0f kW declarados → %.1f kW' % (c, d, r)
                      for c, d, r in acima_do_teto[:4]])

    if teto_sem_energia:
        _ajustes.registrar(
            'nao_corrigido', 'Geração acima do teto legal, e sem energia',
            quantos=len(teto_sem_energia), unidade='unidades geradoras',
            efeito='nao_corrigido',
            detalhe='Declaram mais de %.0f MW e não registram geração no ano, '
                    'então não há de onde reconstruir a potência. Entraram '
                    'como estão.'
                    % (LIMITE_MINIGD_KW / 1000.0),
            porque='Reconstruir de uma energia que não existe seria inventar '
                   'o número, e aparar no teto inventaria outro. Ficam como o '
                   'cadastro as publicou, ditas em voz alta: quem for usar '
                   'este alimentador para falar de fluxo reverso precisa '
                   'saber que elas estão aí.',
            exemplos=['%s: %.0f kW' % (c, d) for c, d in teto_sem_energia[:4]])

    if cnt_pot_estimada:
        _ajustes.registrar(
            'potencia', 'Potência da geração distribuída reconstruída',
            quantos=cnt_pot_estimada, unidade='unidades geradoras',
            efeito='simulacao',
            detalhe='Nestas unidades `POT_INST × 720` é exatamente o maior mês de '
                    'energia: o campo é a potência MÉDIA do mês de maior '
                    'geração, e não a instalada. A instalada foi ESTIMADA '
                    'dividindo a energia do ano por %.0f kWh/kW.'
                    % RENDIMENTO_ANO_KWH_POR_KW,
            porque='Sem reconstruir, cada usina entra com cerca de um sétimo da '
                   'potência real — e o fator de capacidade calculado a partir '
                   'dela sai sete vezes maior, o que fazia a geração solar ser '
                   'classificada como não-solar e gerar as 24 horas do dia.')
        print('  [ATENCAO] %d unidade(s) de geracao com POT_INST reconstruido.'
              % cnt_pot_estimada)
        print('            Nessas linhas o cadastro traz POT_INST x 720 igual ao '
              'maior mes de energia:')
        print('            o campo e a potencia MEDIA do mes de pico, e nao a '
              'instalada -- cerca de 7x menor.')
        print('            A instalada foi ESTIMADA pela energia do ano / %.0f kWh/kW. '
              'Ver a coluna pot_origem'
              % RENDIMENTO_ANO_KWH_POR_KW)
        print('            em Cadastro_GD.csv.')
    if ugbt_barra_invalida or cnt_bt_barra_morta_remetida:
        print(f"  [DIAG] UGBT em barra que a rede não cria: {cnt_bt_barra_morta_remetida} remetidas ao "
              f"secundário do trafo, {len(ugbt_barra_invalida)} descartadas")
        if ugbt_barra_invalida:
            print(f"         amostra (COD_ID, barra): {_fmt_amostra(ugbt_barra_invalida)}")
    if cnt_bt_no_morto:
        print(f"  [DIAG] UGBT em nó que a rede não energiza, grampeadas aos nós reais: {cnt_bt_no_morto}")
    _ajustes.registrar(
        'topologia', 'Geração ligada a nó que a rede não energiza',
        quantos=cnt_bt_no_morto, unidade='unidades geradoras', efeito='simulacao',
        detalhe='A auto-cura de fases escolheu, pelo cadastro dos cabos, um nó '
                'que nenhuma linha nem transformador escreve naquela barra. '
                'A usina foi ligada aos nós que existem lá, com o mesmo '
                'número de fases.',
        porque='O center-tap sai em `.1.3`, a usina pedia `.2`, e o nó 2 só '
                'existia porque ela o criou: 0,0 pu com 40 kW injetados. Uma '
                'fonte num nó morto abre o balanço do alimentador inteiro — '
                '50 de 96 passos num alimentador da Copel, por usinas assim.')
    _ajustes.registrar(
        'topologia', 'Geração resolvida para barra que a rede não cria',
        quantos=cnt_bt_barra_morta_remetida + len(ugbt_barra_invalida),
        unidade='unidades geradoras', efeito='simulacao',
        detalhe='O ponto de conexão da usina caiu numa barra que nenhuma linha '
                'nem transformador do caso escreve. %d foram remetidas ao '
                'secundário do transformador do cadastro; %d ficaram de fora.'
                % (cnt_bt_barra_morta_remetida, len(ugbt_barra_invalida)),
        porque='Uma fonte numa barra morta não é carga a menos: o OpenDSS '
               'aceita o circuito, "converge" em duas iterações e entrega um '
               'dia inteiro com o balanço de potência aberto — 21 de 24 '
               'passos num alimentador da Celesc, por UMA usina de 40 kW '
               'num toco `BT-0 → BT-D-P2`. Com a usina fora, o mesmo caso '
               'fecha o dia com os reguladores ligados.',
        exemplos=['%s → %s' % (c, b) for c, b in ugbt_barra_invalida[:4]])
    if ugmt_skip_bus:
        print(f"  [DIAG] UGMT descartada por barramento MT não encontrado: {len(ugmt_skip_bus)}")
        print(f"         amostra (COD_ID, PN_CON): {_fmt_amostra(ugmt_skip_bus)}")
