# -*- coding: utf-8 -*-
"""As cargas: baixa tensão, média tensão e iluminação pública.

Cada carga do caso é o encontro de três coisas — quantas unidades consumidoras
o poste agrega, quanta energia elas consomem, e com que forma ao longo do dia.
As duas primeiras vêm do cadastro (via `modelo.cadastro`), a terceira da curva
(via `modelo.curvas`); aqui elas viram texto `.dss`.

**O modelo é o do PRODIST**, ZIP com os coeficientes de `config.ZIPV_PRODIST`:
carga de distribuição não é potência constante, e tratá-la como tal exagera a
queda de tensão justamente nos pontos que mais interessam.

**A ligação tem de bater com a barra.** Uma carga trifásica escrita numa barra
de center-tap, que tem dois nós, é a receita do estouro numérico — e ele não
aparece na linha errada, aparece no passo de maior carga do dia, longe da
causa. Por isso a resposta sobre center-tap é a mesma que `modelo.topologia`
deu ao transformador: uma fonte só, decidida uma vez.

**A iluminação pública** é carga como as outras, com duas diferenças: a potência
vem do campo de energia (não do de potência, que em várias bases traz energia
disfarçada), e a curva é astronômica, não comportamental.
"""
from __future__ import annotations

import math
import os
import geopandas as gpd
import pandas as pd

from bdgdcase import ajustes as _ajustes
from bdgdcase.modelo import config
from bdgdcase.modelo.config import (
    DEFAULT_TIP_CC_CURVA, FATOR_DEMANDA, FC_MACROCOPICO,
    MODELO_CARGA_PRODIST, ZIPV_PRODIST,)
from bdgdcase.modelo.cadastro import (
    HORAS_IP_MES, _clas_tar_para_tipo, _tip_cc_para_classe_fp,
    _tip_cc_para_prefixo_carga, corrigir_car_inst_pip, fases_info,
    kv_bt_para_fases, kvar_from_kw, parse_lista, sanitizar_bus,
    sanitizar_bus_bt,)
from bdgdcase.modelo.cadastro_csv import _cadastro_da_uc
from bdgdcase.modelo.curvas import (
    CAR_INST_BT_MAX_KW, _lista_tip_cc_linha, _resolver_loadshape_e_fator,
    _tem_consumo_real, calcular_demanda_kw,)
from bdgdcase.modelo.topologia import (
    _construir_gdf_endpoints_bt, _construir_gdf_endpoints_mt,
    _construir_mapa_bt, _construir_mapa_mt, _kv_bt_por_trafo,
    _resolver_bus_bt, _resolver_bus_mt, _trafo_bus_map, _trafo_ext_map,
    nos_vivos,)


def gerar_cargas_bt(ucbt_por_poste, ssdbt, ramlig, untrmt, caminho_saida,
                    ctx_curvas, cadastro=None, eqtrmt=None,
                    barras_existentes=None, nos_existentes=None):
    """Cria Cargas_BT.dss com Auto-Cura de Fases e Logs de Diagnóstico.

    ``nos_existentes``: ``{barra: [nós]}`` que linhas e transformadores
    escreveram; os nós de cada carga são grampeados a eles
    (:func:`~bdgdcase.modelo.topologia.nos_vivos`). Carga em nó morto não
    derruba a solução como a usina derruba, mas não consome nada — some do
    caso em silêncio.

    ``barras_existentes``: as barras de baixa que os emissores de linhas e de
    transformadores DE FATO escreveram (nomes em minúsculas). Quando vem, é a
    palavra final: uma carga resolvida para barra fora deste conjunto é
    remetida ao secundário do transformador do cadastro, e, se nem ele
    existir, fica de fora — contada. Sem o conjunto, vale só o crivo por nome.

    ctx_curvas: retorno de gerar_curvas_de_carga_dss (fatores PU, default_san,
    modo). cadastro: lista opcional que recebe uma linha por carga emitida, com
    o que a
        BDGD sabe da unidade e o `.dss` não guarda. Coletar aqui dentro, e não
        num segundo passeio pelas tabelas, é o que garante que o cadastro fale
        exatamente das cargas que existem — com o mesmo índice e as mesmas
        listas que decidiram cada uma.
    """
    import re as _re
    linhas = [
        "! ============================================================",
        "! Cargas_BT.dss – Cargas BT (Consumo Médio c/ Grampo de Fase)",
        "! ============================================================",
        "",
    ]

    if ucbt_por_poste is None or len(ucbt_por_poste) == 0:
        linhas.append("! Nenhum dado de carga BT encontrado.")
        with open(caminho_saida, 'w', encoding='utf-8', newline='\n') as f: f.write('\n'.join(linhas))
        return

    valid_buses, ramlig_map = _construir_mapa_bt(ssdbt, ramlig)
    trafo_bus = _trafo_bus_map(untrmt) if untrmt is not None else {}
    # FIX: Mapa estendido de trafos com avail_nodes real (source-of-truth para MRTs)
    trafo_ext = (_trafo_ext_map(untrmt, ssdbt, eqtrmt)
                 if untrmt is not None else {})
    # Cada unidade sabe de que trafo depende pelo UNI_TR_MT, e é o secundário
    # dele que diz em que tensão ela está.
    kv_por_trafo = (_kv_bt_por_trafo(untrmt, eqtrmt)
                    if untrmt is not None else {})
    _FASE_NO = {'A': '1', 'B': '2', 'C': '3'}

    # --- SNIFFER BT: Lê as Fases Reais dos Cabos da Rua ---
    bt_bus_fases = {}
    if ssdbt is not None and not ssdbt.empty:
        for _, row in ssdbt.iterrows():
            fas_raw = str(row.get('FAS_CON', 'ABC')).strip()
            if fas_raw.lower() in ('nan', 'none', ''):
                fas = 'ABC'
            else:
                fas = ''.join(c for c in fas_raw.upper() if c in ('A', 'B', 'C'))
                if not fas:
                    fas = 'ABC'
            for pac in ('PAC_1', 'PAC_2'):
                b = sanitizar_bus_bt(str(row.get(pac, '')).strip())
                if b not in bt_bus_fases: bt_bus_fases[b] = set()
                for f in fas:
                    if f in _FASE_NO: bt_bus_fases[b].add(_FASE_NO[f])

    trafo_fases = {}
    if untrmt is not None and not untrmt.empty:
        for _, row in untrmt.iterrows():
            cod = str(row['COD_ID']).strip()
            fas_raw = str(row.get('FAS_CON', 'ABC')).strip()
            if fas_raw.lower() in ('nan', 'none', ''):
                fas = 'ABC'
            else:
                fas = ''.join(c for c in fas_raw.upper() if c in ('A', 'B', 'C'))
                if not fas:
                    fas = 'ABC'
            trafo_fases[cod] = len(fas) if len(fas) > 0 else 3

    # Mapa MRT: {bus_bt -> avail_nodes} usando _trafo_ext_map como source-of-truth.
    # Substitui o antigo mrt_sec_fases que usava FAS_CON_S (frequentemente incorreto).
    # O _trafo_ext_map replica o mesmo algoritmo de gerar_transformadores, garantindo
    # que os nos disponiveis sejam EXATAMENTE os mesmos do .dss gerado.
    mrt_avail_nodes = {}  # {bus_bt: set de nos reais do trafo}
    for _cod, (_bus, _tip, _ten, _avail, _ct) in trafo_ext.items():
        if _ct:
            mrt_avail_nodes[_bus] = _avail

    # Mapa bus_bt → conjunto de UNI_TR_MT que referenciam o bus no SSDBT.
    # Usado para validar se o bus resolvido via fallback espacial pertence ao mesmo
    # trafo do cadastro (UNI_TR_MT da carga). Se divergir, redireciona p/ PAC_2 do
    # trafo correto — evita carga "atravessar" para outro trafo por proximidade.
    bus_trafos = {}
    if ssdbt is not None and not ssdbt.empty and 'UNI_TR_MT' in ssdbt.columns:
        for _, row in ssdbt.iterrows():
            _uni = sanitizar_bus(str(row.get('UNI_TR_MT', '')).strip())
            if not _uni or _uni.lower() in ('nan', 'none', ''):
                continue
            for pac in ('PAC_1', 'PAC_2'):
                b = sanitizar_bus_bt(str(row.get(pac, '')).strip())
                if b:
                    bus_trafos.setdefault(b, set()).add(_uni)
    # Adiciona também o PAC_2 do próprio trafo ao seu UNI_TR_MT
    if untrmt is not None and not untrmt.empty:
        for _, row in untrmt.iterrows():
            _uni = sanitizar_bus(str(row.get('COD_ID', '')).strip())
            b = sanitizar_bus_bt(str(row.get('PAC_2', '')).strip())
            if _uni and b:
                bus_trafos.setdefault(b, set()).add(_uni)

    cnt_direto = cnt_ramlig = cnt_espacial = cnt_trafo = cnt_flutuante = 0
    cnt_fases_corrigidas = 0
    cnt_espacial_corrigido = 0
    cnt_barra_morta_remetida = 0
    barra_morta_perdida = []
    cnt_no_morto = 0
    avisos_car = {}
    contagem = 0

    # ── Mapa espacial BT: índice do ucbt_por_poste → bus BT mais próximo (nearest PAC do SSDBT) ──
    # Idêntico ao mecanismo do MT. Resolve o mismatch entre IDs do PONNOT (PN_CON) e IDs do SSDBT (PAC).
    spatial_map_bt = {}
    if (ssdbt is not None and not ssdbt.empty
            and ucbt_por_poste is not None
            and hasattr(ucbt_por_poste, 'geometry')):
        try:
            gdf_ep_bt = _construir_gdf_endpoints_bt(ssdbt)
            if gdf_ep_bt is None:
                print(f"  [AVISO-BT] Nenhum endpoint extraído do SSDBT — spatial join BT desativado.")
            else:
                print(f"  [DIAG-BT] Endpoints SSDBT prontos: {len(gdf_ep_bt)} nós com coordenadas")
                ucbt_pts = ucbt_por_poste[['geometry']].copy()
                n_total = len(ucbt_pts)
                ucbt_pts = ucbt_pts[ucbt_pts.geometry.notna() & ~ucbt_pts.geometry.is_empty]
                print(f"  [DIAG-BT] UCBT: {len(ucbt_pts)}/{n_total} registros com geometria válida")
                if not ucbt_pts.empty:
                    if ucbt_pts.crs != gdf_ep_bt.crs:
                        ucbt_pts = ucbt_pts.to_crs(gdf_ep_bt.crs)
                    if ucbt_pts.crs is not None and ucbt_pts.crs.is_geographic:
                        try:
                            proj_crs = ucbt_pts.estimate_utm_crs()
                        except Exception:
                            proj_crs = 'EPSG:5880'
                        ucbt_pts   = ucbt_pts.to_crs(proj_crs)
                        gdf_ep_bt  = gdf_ep_bt.to_crs(proj_crs)
                    joined_bt = gpd.sjoin_nearest(
                        ucbt_pts.set_geometry('geometry'),
                        gdf_ep_bt[['bus', 'geometry']].set_geometry('geometry'),
                        how='left',
                    )
                    for idx, jrow in joined_bt.iterrows():
                        nn = jrow.get('bus', None)
                        if pd.notna(nn):
                            spatial_map_bt[idx] = sanitizar_bus_bt(str(nn))
                    print(f"  [DIAG-BT] Mapa espacial BT: {len(spatial_map_bt)} UCs -> nearest PAC do SSDBT")
        except Exception as _e:
            print(f"  [AVISO-BT] Spatial join BT falhou ({_e}); usando resolução por string/trafo.")

    for idx, row in ucbt_por_poste.iterrows():
        pn_raw = str(row.get('PN_CON', '')).strip()
        uni_tr = str(row.get('UNI_TR_MT', '')).strip()

        lista_pns = pn_raw.split(';') if ';' in pn_raw else [pn_raw]
        if not any(p and p.lower() != 'nan' for p in lista_pns):
            lista_pns = [str(row.get('COD_PONNOT', '')).strip()]

        bus_resolvido = None
        resolucao = 'flutuante'

        for pn in lista_pns:
            if not pn or pn.lower() == 'nan': continue
            b, res = _resolver_bus_bt(pn, valid_buses, ramlig_map)
            if res in ['direto', 'ramlig']:
                bus_resolvido = b
                resolucao = res
                break

        # Fallback espacial: nearest PAC do SSDBT (antes de cair no trafo secundário)
        if resolucao not in ['direto', 'ramlig']:
            nn_bus_bt = spatial_map_bt.get(idx)
            if nn_bus_bt:
                bus_resolvido = nn_bus_bt
                resolucao = 'espacial'

        # Validação cruzada: se espacial resolveu para bus de outro trafo (vs. UNI_TR_MT
        # do cadastro), redirecionar ao PAC_2 do trafo correto. Preserva spatial na
        # maioria dos casos, só corrige conflitos trafo-cruzado.
        if resolucao == 'espacial' and trafo_bus and uni_tr and bus_resolvido:
            unis_cadastro = {sanitizar_bus(u.strip()) for u in str(uni_tr).split(';') if u.strip()}
            unis_bus = bus_trafos.get(bus_resolvido, set())
            if unis_cadastro and unis_bus and unis_cadastro.isdisjoint(unis_bus):
                for uni in unis_cadastro:
                    fb = trafo_bus.get(uni)
                    if fb:
                        bus_resolvido = fb
                        resolucao = 'trafo'
                        cnt_espacial_corrigido += 1
                        break

        if resolucao not in ['direto', 'ramlig', 'espacial', 'trafo']:
            if trafo_bus and uni_tr:
                for uni in str(uni_tr).split(';'):
                    fb = trafo_bus.get(uni.strip())
                    if fb:
                        bus_resolvido = fb
                        resolucao = 'trafo'
                        break

        bus = bus_resolvido
        # FIX Bug 3: Filtrar buses orfaos (BT_0, BT_D_P2_*) que nao existem na rede
        if not bus or bus in ('nan', 'None', '', 'BT_0'):
            cnt_flutuante += 1
            continue
        if bus.startswith('BT_D_P2_'):
            cnt_flutuante += 1
            continue

        # O crivo por nome pega o placeholder; este pega o resto. A barra pode
        # ter nome perfeitamente normal e mesmo assim não existir no caso —
        # basta o único trecho que a citava ter sido descartado pelo emissor
        # (a outra ponta era `BT-0`, por exemplo). Uma carga numa barra que
        # nada energiza não pesa nada: some do caso em silêncio.
        if barras_existentes is not None and bus.lower() not in barras_existentes:
            remetida = None
            for uni in str(uni_tr).split(';'):
                cand = trafo_bus.get(uni.strip())
                if cand and cand.lower() in barras_existentes:
                    remetida = cand
                    break
            if remetida:
                bus = remetida
                resolucao = 'trafo'
                cnt_barra_morta_remetida += 1
            else:
                barra_morta_perdida.append(bus)
                cnt_flutuante += 1
                continue

        # Incrementa os contadores de diagnóstico corretamente
        if resolucao == 'direto': cnt_direto += 1
        elif resolucao == 'ramlig': cnt_ramlig += 1
        elif resolucao == 'espacial': cnt_espacial += 1
        elif resolucao == 'trafo': cnt_trafo += 1
        else: cnt_flutuante += 1

        uni_tr_lista = str(uni_tr).split(';')
        uni_tr_principal = uni_tr_lista[0] if uni_tr_lista else ''
        qtd_fases_trafo = trafo_fases.get(uni_tr_principal, 3)
        kv_bt_trafo = kv_por_trafo.get(sanitizar_bus(uni_tr_principal))

        fases_lista = parse_lista(row.get('LISTA_FAS_CON', ''), str)
        car_lista   = parse_lista(row.get('LISTA_CAR_INST', ''), float)
        clas_lista  = _lista_tip_cc_linha(row)
        pn_tag = sanitizar_bus(str(row.get('COD_PONNOT', bus)))

        # Tipo dominante do poste (para o kW_pico do bloco agregado sem lista de fases)
        tip0 = clas_lista[0] if clas_lista else ''
        tipo_poste = _clas_tar_para_tipo(tip0)
        daily0, fc0 = _resolver_loadshape_e_fator(tip0, ctx_curvas)
        pref0 = _tip_cc_para_prefixo_carga(tip0)
        kw_poste = calcular_demanda_kw(
            row, idx_uc=None, fator_demanda=FATOR_DEMANDA, tipo=tipo_poste,
            fator_carga_curva=fc0, teto_car_inst_kw=CAR_INST_BT_MAX_KW,
            avisos=avisos_car)

        # --- AUTO-CURA DA CARGA ---
        nos_locais = bt_bus_fases.get(bus, set())

        # FIX Bug 2: Para buses MRT, SEMPRE usar os avail_nodes do _trafo_ext_map
        # como source-of-truth. O sniffer SSDBT frequentemente discorda do trafo
        # (226 buses com mismatch) pois os cabos BT usam rotulos MT (ex: fase C -> no 3)
        # que nao correspondem aos nos reais do center-tap (p_bt1/p_bt2).
        if bus in mrt_avail_nodes:
            nos_locais = mrt_avail_nodes[bus]

        # Blocos sem discriminação de fases
        if not fases_lista or not car_lista:
            if kw_poste > 0:
                validos = sorted(nos_locais) if nos_locais else (['1'] if qtd_fases_trafo == 1 else ['1','2','3'])
                if nos_existentes is not None:
                    validos, mudou = nos_vivos(validos, nos_existentes.get(bus.lower()))
                    cnt_no_morto += int(mudou)
                nph_real = len(validos)
                sfx_real = '.' + '.'.join(validos) + '.0'

                cls_fp0 = _tip_cc_para_classe_fp(tip0)
                linhas += [
                    f"New Load.UCBT_{pref0}_{pn_tag} bus1={bus}{sfx_real} phases={nph_real}",
                    f"~ kv={kv_bt_para_fases(nph_real, kv_bt_trafo):.3f} kw={kw_poste:.4f} kvar={kvar_from_kw(kw_poste, cls_fp0):.4f} daily={daily0}",
                    f"~ {MODELO_CARGA_PRODIST} conn=wye {ZIPV_PRODIST}", ""
                ]
                contagem += 1
                if cadastro is not None:
                    # Bloco sem discriminação de fases: a carga é o poste
                    # inteiro somado, e não uma unidade. O cadastro diz isso em
                    # vez de atribuir o bloco à primeira unidade da lista.
                    cadastro.append({
                        'elemento': f'ucbt_{pref0}_{pn_tag}'.lower(),
                        'tipo': 'poste agregado',
                        'poste': pn_tag,
                        'idx': '',
                        'bus': bus,
                        'fases_ligadas': ''.join(validos),
                        'tip_cc': tip0,
                        'classe': pref0,
                        'curva': daily0,
                        'curva_origem': ctx_curvas.get('modo', ''),
                        'kw_pico': kw_poste,
                        'kvar_pico': kvar_from_kw(kw_poste, cls_fp0),
                        'kv': kv_bt_para_fases(nph_real, kv_bt_trafo),
                        'uni_tr_mt': uni_tr,
                        'mun': str(row.get('MUN', '') or '').strip(),
                    })
            continue

        # Geração individual por UC com Clamp
        n = min(len(fases_lista), len(car_lista))
        for i in range(n):
            fas = fases_lista[i]
            clas = clas_lista[i] if i < len(clas_lista) else ''
            tipo = _clas_tar_para_tipo(clas)
            daily_i, fc_i = _resolver_loadshape_e_fator(clas, ctx_curvas)
            pref_i = _tip_cc_para_prefixo_carga(clas)
            kw = calcular_demanda_kw(
                row, idx_uc=i, fator_demanda=FATOR_DEMANDA, tipo=tipo,
                fator_carga_curva=fc_i, teto_car_inst_kw=CAR_INST_BT_MAX_KW,
                avisos=avisos_car)
            if kw <= 0: continue
            _, _, sfx_pedido, _ = fases_info(fas)

            pedidos = set(_re.findall(r'[1-3]', sfx_pedido))
            validos = sorted(pedidos & nos_locais) if nos_locais else sorted(pedidos)

            # Se a carga pediu fase fantasma, realoca pra primeira fase viva do cabo
            if not validos:
                validos = [sorted(nos_locais)[0]] if nos_locais else ['1']
                cnt_fases_corrigidas += 1

            # Última palavra sobre os nós: os que a rede escreveu nesta barra.
            if nos_existentes is not None:
                validos, mudou = nos_vivos(validos, nos_existentes.get(bus.lower()))
                cnt_no_morto += int(mudou)

            nph_real = len(validos)
            sfx_real = '.' + '.'.join(validos) + ('.0' if ('N' in fas or nph_real < 3) else '')

            cls_fp = _tip_cc_para_classe_fp(clas)
            linhas += [
                f"New Load.UCBT_{pref_i}_{pn_tag}_{i} bus1={bus}{sfx_real} phases={nph_real}",
                f"~ kv={kv_bt_para_fases(nph_real, kv_bt_trafo):.4f} kw={kw:.4f} kvar={kvar_from_kw(kw, cls_fp):.4f} daily={daily_i}",
                f"~ {MODELO_CARGA_PRODIST} conn=wye {ZIPV_PRODIST}  ! FAS_BDGD={fas} CLAMP={validos} TIP_CC={clas}", ""
            ]
            contagem += 1
            if cadastro is not None:
                cadastro.append(_cadastro_da_uc(
                    row, i,
                    elemento=f'ucbt_{pref_i}_{pn_tag}_{i}'.lower(),
                    tipo='unidade consumidora BT',
                    bus=bus,
                    # Vêm do laço, e não da lista: são exatamente os valores
                    # que decidiram esta carga.
                    tip_cc=clas,
                    fas_con=fas,
                    car_inst_kw=(car_lista[i] if i < len(car_lista) else ''),
                    # As fases PEDIDAS ficam em `fas_con`, vindas do cadastro;
                    # aqui vão as que sobraram depois da auto-cura. A diferença
                    # entre as duas é achado, não ruído: é uma unidade ligada a
                    # uma fase que não existe no ponto de derivação.
                    fases_ligadas=''.join(validos),
                    classe=pref_i,
                    curva=daily_i,
                    curva_origem=ctx_curvas.get('modo', ''),
                    kw_pico=kw,
                    kvar_pico=kvar_from_kw(kw, cls_fp),
                    kv=kv_bt_para_fases(nph_real, kv_bt_trafo)))

    with open(caminho_saida, 'w', encoding='utf-8', newline='\n') as f: f.write('\n'.join(linhas))

    # RESTAURADO: Prints de Diagnóstico Clássicos
    print(f"  [OK] {os.path.basename(caminho_saida)} – {contagem} cargas BT geradas ({cnt_fases_corrigidas} auto-curadas)")
    print(f"  [DIAG] UCBT -> direto:{cnt_direto} ramlig:{cnt_ramlig} espacial:{cnt_espacial} trafo:{cnt_trafo} flutuante:{cnt_flutuante}")
    _ajustes.registrar(
        'topologia', 'Unidades ligadas à rede por aproximação espacial',
        quantos=cnt_espacial, unidade='postes', efeito='simulacao',
        detalhe='O `PN_CON` destas unidades não casou com nenhuma barra da rede '
                'de baixa nem com um ramal de ligação. Foram ligadas ao ponto '
                'mais próximo geograficamente.',
        porque='Sem isso a carga fica flutuante e some do caso — o alimentador '
               'resolve com menos carga do que tem, e nada avisa.')
    _ajustes.registrar(
        'topologia', 'Unidades ligadas direto no secundário do transformador',
        quantos=cnt_trafo, unidade='postes', efeito='simulacao',
        detalhe='Nem o cadastro nem a geometria resolveram o ponto de conexão '
                'destas unidades; foram penduradas na barra de baixa do '
                'transformador que o cadastro indica.',
        porque='Concentra a carga na cabeceira do secundário, então a queda de '
               'tensão ao longo do ramal daquelas unidades não aparece. É o '
               'último recurso antes de perder a carga.')
    _ajustes.registrar(
        'topologia', 'Unidades que não encontraram ponto de conexão',
        quantos=cnt_flutuante, unidade='postes', efeito='nao_corrigido',
        detalhe='Nem por cadastro, nem por ramal, nem por proximidade.',
        porque='Estas cargas NÃO entram no caso. O alimentador resolve com '
               'menos carga do que o cadastro declara.')
    if cnt_espacial_corrigido:
        print(f"  [DIAG] UCBT -> espacial->trafo (cross-trafo): {cnt_espacial_corrigido}")
    if cnt_barra_morta_remetida or barra_morta_perdida:
        print(f"  [DIAG] UCBT -> barra que a rede não cria: {cnt_barra_morta_remetida} remetidas ao "
              f"secundário do trafo, {len(barra_morta_perdida)} perdidas")
    if cnt_no_morto:
        print(f"  [DIAG] UCBT -> nó que a rede não energiza, grampeadas aos nós reais: {cnt_no_morto}")
    if avisos_car:
        print(f"  [DIAG] UCBT -> CAR_INST fora do que a baixa comporta (sem energia faturada): {avisos_car}")
    _ajustes.registrar(
        'potencia', 'Carga instalada lida como watts',
        quantos=avisos_car.get('car_inst_watts', 0), unidade='unidades',
        efeito='simulacao',
        detalhe='Unidades sem energia faturada, cuja demanda sai de '
                '`CAR_INST × %.2f`, traziam `CAR_INST` acima de %.0f kW — mil '
                'vezes o que uma unidade de baixa comporta. Foi lido como '
                'watt.' % (FATOR_DEMANDA, CAR_INST_BT_MAX_KW),
        porque='Medido no IAL02: `CAR_INST=9500` numa residência sem energia '
               'virava 1.634 kW numa fase de um transformador de 75 kVA — '
               '1.389 A, 0,18 pu no fim da rua, e o caso recusado. Com '
               'energia faturada o número não entra; sem ela, é ele que faz '
               'a carga.')
    _ajustes.registrar(
        'nao_corrigido', 'Carga instalada implausível, unidade sem carga',
        quantos=avisos_car.get('car_inst_implausivel', 0), unidade='unidades',
        efeito='nao_corrigido',
        detalhe='Sem energia faturada e com `CAR_INST` acima de %.0f kW '
                'mesmo dividido por mil: não há de onde tirar a demanda. '
                'A unidade entra sem carga.' % CAR_INST_BT_MAX_KW,
        porque='Inventar um número seria pior do que a falta dele — e uma '
               'carga assim derruba a fase inteira.')
    _ajustes.registrar(
        'topologia', 'Cargas ligadas a nó que a rede não energiza',
        quantos=cnt_no_morto, unidade='cargas', efeito='simulacao',
        detalhe='A auto-cura de fases escolheu, pelo cadastro dos cabos, um nó '
                'que nenhuma linha nem transformador escreve naquela barra. '
                'A carga foi ligada aos nós que existem lá, com o mesmo '
                'número de fases.',
        porque='Carga em nó morto fica a 0 V e não consome: o alimentador '
               'resolve com menos carga do que o cadastro declara, sem aviso.')
    _ajustes.registrar(
        'topologia', 'Unidades resolvidas para barra que a rede não cria',
        quantos=cnt_barra_morta_remetida + len(barra_morta_perdida),
        unidade='postes', efeito='simulacao',
        detalhe='O ponto de conexão tinha nome de barra, mas nenhuma linha nem '
                'transformador do caso a escreve — o trecho que a citava foi '
                'descartado (a outra ponta era placeholder). %d foram '
                'remetidas ao secundário do transformador do cadastro; %d não '
                'tinham nem isso e ficaram de fora.'
                % (cnt_barra_morta_remetida, len(barra_morta_perdida)),
        porque='Uma carga numa barra que nada energiza não consome nada: some '
               'do caso sem aviso. Medido: 40 unidades numa só barra morta de '
               'um alimentador da Copel.',
        exemplos=sorted(set(barra_morta_perdida))[:4])

def gerar_cargas_mt(ucmt_por_poste, pip, untrmt, ssdmt, caminho_saida,
                    ssdbt=None, ctx_curvas=None, cadastro=None, eqtrmt=None):
    """Cria Cargas_MT.dss com cargas MT (UCMT baseada em consumo) e Iluminação Pública (PIP).

    ctx_curvas: mesmo dicionário de gerar_curvas_de_carga_dss (obrigatório para
    daily= TIP_CC).
    """
    linhas = [
        "! ============================================================",
        "! Cargas_MT.dss – Consumidores MT (UCMT) + Iluminacao Publica (PIP)",
        "! ============================================================",
        "",
    ]

    import re as _re

    # Mapa de buses MT válidos (PAC_1/PAC_2 do SSDMT) – espelha _construir_mapa_bt para BT
    valid_mt_buses = _construir_mapa_mt(ssdmt)

    # Sniffer MT: fases reais por bus → mesmo padrão do gerar_gd (evita nós flutuantes)
    _FASE_NO = {'A': '1', 'B': '2', 'C': '3'}
    mt_bus_fases = {}
    if ssdmt is not None and not ssdmt.empty:
        for _, _r in ssdmt.iterrows():
            _fas_raw = str(_r.get('FAS_CON', 'ABC')).strip()
            # Garante que NaN/None não seja interpretado como fase 'A' (nan→NAN→NA→A após remove N)
            if _fas_raw.lower() in ('nan', 'none', ''):
                _fas = 'ABC'
            else:
                _fas = ''.join(c for c in _fas_raw.upper() if c in ('A', 'B', 'C'))
                if not _fas:
                    _fas = 'ABC'
            for _pac in ('PAC_1', 'PAC_2'):
                _b = sanitizar_bus(str(_r.get(_pac, '')).strip())
                if _b not in mt_bus_fases:
                    mt_bus_fases[_b] = set()
                for _f in _fas:
                    if _f in _FASE_NO:
                        mt_bus_fases[_b].add(_FASE_NO[_f])

    contagem = 0
    cnt_consumo_real_mt = 0
    cnt_fallback_inst_mt = 0
    kw_consumo_real_mt = 0.0
    kw_fallback_inst_mt = 0.0
    cnt_direto = 0
    cnt_espacial = 0
    cnt_flutuante = 0

    # ── Mapa espacial: COD_ID do UCMT → bus MT mais próximo (nearest PAC do SSDMT) ──
    # Usado como fallback quando PN_CON não bate com nenhum PAC por string.
    spatial_map_mt = {}
    if (ssdmt is not None and not ssdmt.empty
            and ucmt_por_poste is not None
            and hasattr(ucmt_por_poste, 'geometry')):
        try:
            gdf_ep = _construir_gdf_endpoints_mt(ssdmt)
            if gdf_ep is None:
                print(f"  [AVISO-MT] Nenhum endpoint extraído do SSDMT — verifique geometria dos segmentos.")
            else:
                print(f"  [DIAG-MT] Endpoints SSDMT prontos: {len(gdf_ep)} nós com coordenadas")
                # Usa só a coluna geometry — não depende de COD_ID (que pode não existir)
                ucmt_pts = ucmt_por_poste[['geometry']].copy()
                n_total = len(ucmt_pts)
                ucmt_pts = ucmt_pts[ucmt_pts.geometry.notna() & ~ucmt_pts.geometry.is_empty]
                print(f"  [DIAG-MT] UCMT: {len(ucmt_pts)}/{n_total} registros com geometria válida")
                if ucmt_pts.empty:
                    print(f"  [AVISO-MT] UCMT sem geometria — spatial join desativado.")
                else:
                    if ucmt_pts.crs != gdf_ep.crs:
                        ucmt_pts = ucmt_pts.to_crs(gdf_ep.crs)
                    # Reprojetar para CRS métrico (UTM) se estiver em graus (geográfico)
                    if ucmt_pts.crs is not None and ucmt_pts.crs.is_geographic:
                        try:
                            proj_crs = ucmt_pts.estimate_utm_crs()
                        except Exception:
                            proj_crs = 'EPSG:5880'  # SIRGAS 2000 / Brazil Mercator
                        ucmt_pts = ucmt_pts.to_crs(proj_crs)
                        gdf_ep   = gdf_ep.to_crs(proj_crs)
                    joined = gpd.sjoin_nearest(
                        ucmt_pts.set_geometry('geometry'),
                        gdf_ep[['bus', 'geometry']].set_geometry('geometry'),
                        how='left',
                    )
                    # Chave = índice do DataFrame (sempre presente, sem depender de COD_ID)
                    for idx, jrow in joined.iterrows():
                        nn = jrow.get('bus', None)
                        if pd.notna(nn):
                            spatial_map_mt[idx] = sanitizar_bus(str(nn))
                    print(f"  [DIAG-MT] Mapa espacial: {len(spatial_map_mt)} UCs -> nearest PAC do SSDMT")
        except Exception as _e:
            print(f"  [AVISO-MT] Spatial join falhou ({_e}); cargas MT usarão PN_CON como-está.")

    if ucmt_por_poste is not None and len(ucmt_por_poste) > 0:
        linhas.append("! --- Consumidores MT (UCMT) – baseados em consumo real ---")
        linhas.append("")
        for idx, row in ucmt_por_poste.iterrows():
            pn_raw = str(row.get('PN_CON', '')).strip()

            # PN_CON pode conter múltiplos pontos separados por ';' — pega o primeiro válido
            lista_pns = pn_raw.split(';') if ';' in pn_raw else [pn_raw]
            if not any(p and p.lower() != 'nan' for p in lista_pns):
                lista_pns = [str(row.get('COD_PONNOT', row.get('COD_ID', ''))).strip()]

            bus_resolvido = None
            resolucao = 'flutuante'
            for pn in lista_pns:
                if not pn or pn.lower() == 'nan':
                    continue
                b, res = _resolver_bus_mt(pn, valid_mt_buses)
                if res == 'direto':
                    bus_resolvido = b
                    resolucao = 'direto'
                    break
                # Guarda o primeiro candidato mesmo que flutuante (último recurso)
                if bus_resolvido is None and b and b.lower() not in ('nan', 'none', ''):
                    bus_resolvido = b

            # Fallback espacial: nearest PAC do SSDMT (chave = índice do DataFrame)
            if resolucao != 'direto':
                nn_bus = spatial_map_mt.get(idx)
                if nn_bus:
                    bus_resolvido = nn_bus
                    resolucao = 'espacial'

            if not bus_resolvido or bus_resolvido in ('nan', 'None', ''):
                cnt_flutuante += 1
                continue

            if resolucao == 'direto':
                cnt_direto += 1
            elif resolucao == 'espacial':
                cnt_espacial += 1
            else:
                cnt_flutuante += 1

            bus = bus_resolvido
            tip_mt = str(row.get('TIP_CC', '')).strip()
            tipo_mt = _clas_tar_para_tipo(tip_mt)
            daily_mt, fc_mt = _resolver_loadshape_e_fator(tip_mt, ctx_curvas)

            fases_lista = parse_lista(row.get('LISTA_FAS_CON', ''), str)
            car_lista   = parse_lista(row.get('LISTA_CAR_INST', ''), float)

            # Fallback: Sem discriminação na lista
            if not fases_lista or not car_lista:
                fas = str(row.get('FAS_CON', 'ABC')).upper().strip()
                if ';' in fas:
                    tokens = [t.strip() for t in fas.split(';') if t.strip()]
                    fas = max(tokens, key=lambda x: fases_info(x)[0])
                nph, sfx_mt, _, _ = fases_info(fas)
                # Clamp: só fases reais do bus — previne nós flutuantes → divergência
                nos_locais = mt_bus_fases.get(bus, set())
                if nos_locais:
                    pedidos = set(_re.findall(r'[1-3]', sfx_mt))
                    validos = sorted(pedidos & nos_locais) if pedidos else sorted(nos_locais)
                    if not validos: validos = sorted(nos_locais)
                    nph = len(validos)
                    sfx_mt = '.' + '.'.join(validos)
                conn_mt = 'wye' if nph == 1 else 'delta'
                kv_val = (config.TENSAO_MT_KV / math.sqrt(3)) if nph == 1 else config.TENSAO_MT_KV

                kw = calcular_demanda_kw(
                    row, idx_uc=None, fator_demanda=FATOR_DEMANDA, tipo=tipo_mt,
                    fator_carga_curva=fc_mt)
                if kw <= 0:
                    continue

                if _tem_consumo_real(row, idx_uc=None):
                    cnt_consumo_real_mt += 1; kw_consumo_real_mt += kw
                else:
                    cnt_fallback_inst_mt += 1; kw_fallback_inst_mt += kw

                cls_fp_mt = _tip_cc_para_classe_fp(tip_mt)
                kvar = kvar_from_kw(kw, cls_fp_mt)
                linhas += [
                    f"New Load.UCMT_{bus} bus1={bus}{sfx_mt} phases={nph}",
                    f"~ kv={kv_val:.3f} kw={kw:.4f} kvar={kvar:.4f} daily={daily_mt}",
                    f"~ {MODELO_CARGA_PRODIST} conn={conn_mt} {ZIPV_PRODIST}",
                    "",
                ]
                contagem += 1
                continue

            # Com discriminação por UC
            n = min(len(fases_lista), len(car_lista))
            tip_lista_mt = _lista_tip_cc_linha(row)
            for i in range(n):
                fas = fases_lista[i]
                tip_uc = tip_lista_mt[i] if i < len(tip_lista_mt) else tip_mt
                daily_uc, fc_uc = _resolver_loadshape_e_fator(tip_uc, ctx_curvas)
                tipo_uc = _clas_tar_para_tipo(tip_uc)
                kw = calcular_demanda_kw(
                    row, idx_uc=i, fator_demanda=FATOR_DEMANDA, tipo=tipo_uc,
                    fator_carga_curva=fc_uc)
                if kw <= 0:
                    continue

                if _tem_consumo_real(row, idx_uc=i):
                    cnt_consumo_real_mt += 1; kw_consumo_real_mt += kw
                else:
                    cnt_fallback_inst_mt += 1; kw_fallback_inst_mt += kw

                nph, sfx_mt, _, _ = fases_info(fas)
                # Clamp: só fases reais do bus — previne nós flutuantes → divergência
                nos_locais = mt_bus_fases.get(bus, set())
                if nos_locais:
                    pedidos = set(_re.findall(r'[1-3]', sfx_mt))
                    validos = sorted(pedidos & nos_locais) if pedidos else sorted(nos_locais)
                    if not validos: validos = sorted(nos_locais)
                    nph = len(validos)
                    sfx_mt = '.' + '.'.join(validos)
                cls_fp_uc = _tip_cc_para_classe_fp(tip_uc)
                kvar = kvar_from_kw(kw, cls_fp_uc)
                conn_mt = 'wye' if nph == 1 else 'delta'
                kv_val = (config.TENSAO_MT_KV / math.sqrt(3)) if nph == 1 else config.TENSAO_MT_KV

                linhas += [
                    f"New Load.UCMT_{bus}_{i} bus1={bus}{sfx_mt} phases={nph}",
                    f"~ kv={kv_val:.3f} kw={kw:.4f} kvar={kvar:.4f} daily={daily_uc}",
                    f"~ {MODELO_CARGA_PRODIST} conn={conn_mt} {ZIPV_PRODIST}  ! FAS={fas} TIP_CC={tip_uc}",
                    "",
                ]
                contagem += 1
                if cadastro is not None:
                    cadastro.append(_cadastro_da_uc(
                        row, i,
                        elemento=('ucmt_%s_%d' % (bus, i)).lower(),
                        tipo='unidade consumidora MT',
                        bus=bus,
                        fases_ligadas=''.join(sorted(
                            _re.findall(r'[1-3]', sfx_mt))),
                        tip_cc=tip_uc,
                        fas_con=fas,
                        car_inst_kw=(car_lista[i] if i < len(car_lista) else ''),
                        classe='MT',
                        curva=daily_uc,
                        curva_origem=ctx_curvas.get('modo', ''),
                        kw_pico=kw,
                        kvar_pico=kvar,
                        kv=kv_val))

    # PIP (Iluminação Pública)
    # FIX Bug 1: PIP agora faz grampo de fases, idêntico às cargas BT.
    # Antes: sempre .1.2.3.0 phases=3 → fase fantasma em 103 MRTs.
    if pip is not None and len(pip) > 0 and untrmt is not None:
        linhas.append("! --- Iluminacao Publica (PIP) – agregada por transformador ---")
        trafo_bus = _trafo_bus_map(untrmt)
        kv_por_trafo = _kv_bt_por_trafo(untrmt, eqtrmt)
        # Mapa estendido com avail_nodes reais do trafo (source-of-truth para fases)
        trafo_ext_pip = _trafo_ext_map(untrmt, ssdbt, eqtrmt)
        # Sniffer BT para PIP (mesmo usado nas cargas BT)
        _FASE_NO_PIP = {'A': '1', 'B': '2', 'C': '3'}
        bt_bus_fases_pip = {}
        if ssdbt is not None and not ssdbt.empty:
            for _, _r in ssdbt.iterrows():
                _fas_raw = str(_r.get('FAS_CON', 'ABC')).strip()
                if _fas_raw.lower() in ('nan', 'none', ''):
                    _fas = 'ABC'
                else:
                    _fas = ''.join(c for c in _fas_raw.upper() if c in ('A', 'B', 'C'))
                    if not _fas: _fas = 'ABC'
                for _pac in ('PAC_1', 'PAC_2'):
                    _b = sanitizar_bus_bt(str(_r.get(_pac, '')).strip())
                    if _b not in bt_bus_fases_pip: bt_bus_fases_pip[_b] = set()
                    for _f in _fas:
                        if _f in _FASE_NO_PIP: bt_bus_fases_pip[_b].add(_FASE_NO_PIP[_f])

        df_pip = pip.copy()
        df_pip['_uni'] = df_pip['UNI_TR_MT'].astype(str).str.strip()

        def _moda_tip_cc(s):
            """O `TIP_CC` mais frequente do grupo — a curva daquele poste.

            Um poste agrega várias unidades consumidoras, e o OpenDSS quer uma
            curva por carga. A moda é a escolha honesta: média de códigos não
            significa nada, e a primeira ocorrência dependeria da ordem das
            linhas no arquivo.
            """
            s = s.dropna()
            if len(s) == 0:
                return DEFAULT_TIP_CC_CURVA
            m = s.astype(str).str.strip().mode()
            return str(m.iloc[0]) if len(m) else str(s.iloc[0])

        # A potência dos pontos, aferida pela energia deles.
        df_pip, _n_pip, _horas_pip = corrigir_car_inst_pip(df_pip)
        if _n_pip:
            print('  [ATENCAO] CAR_INST do PIP reposto em %d ponto(s).' % _n_pip)
            _ajustes.registrar(
                'potencia', 'Potência da iluminação pública reposta pela energia',
                quantos=_n_pip, unidade='pontos de iluminação',
                efeito='simulacao',
                detalhe='O campo `CAR_INST` destes pontos daria %.2f h acesas '
                        'por mês, e uma luminária fica cerca de 354 — 11,6 h '
                        'por noite. A potência foi reposta por '
                        'max(ENE)/%.0f h.' % (_horas_pip, HORAS_IP_MES),
                porque='A carga de iluminação pública é a SOMA dos pontos de cada '
                       'transformador, então o erro entra multiplicado. Sem '
                       'corrigir, um alimentador medido mostrava 1.324 kW de '
                       'iluminação onde há 132.')
            print('            A razao entre energia e potencia declarada da '
                  '%.2f h acesas por mes, e o normal' % _horas_pip)
            print('            e 354. A potencia foi reposta por '
                  'max(ENE)/%.0f h.' % HORAS_IP_MES)

        agg_pip = df_pip.groupby('_uni').agg(
            kW=('CAR_INST', 'sum'),
            qtd=('COD_ID', 'count'),
            tip_cc=('TIP_CC', _moda_tip_cc),
        ).reset_index()
        cnt_pip_clamped = 0
        for _, row in agg_pip.iterrows():
            uni_cod = sanitizar_bus(str(row['_uni']).strip())
            bus = trafo_bus.get(row['_uni'])
            if not bus:
                continue

            kw = float(row['kW'] or 0) * 1.0 * FC_MACROCOPICO
            if kw <= 0:
                continue
            tip_pip = str(row.get('tip_cc', '') or '').strip()
            daily_pip, _fc_p = _resolver_loadshape_e_fator(tip_pip, ctx_curvas)
            kvar = kvar_from_kw(kw, 'IP')

            # Detectar nós reais do bus do trafo (prioridade: avail_nodes do trafo → sniffer BT)
            nos_pip = set()
            ext = trafo_ext_pip.get(uni_cod)
            if ext:
                nos_pip = ext[3]  # avail_nodes
            if not nos_pip:
                nos_pip = bt_bus_fases_pip.get(bus, set())
            if not nos_pip:
                nos_pip = {'1', '2', '3'}  # fallback conservador para trifásico

            validos_pip = sorted(nos_pip)
            nph_pip = len(validos_pip)
            sfx_pip = '.' + '.'.join(validos_pip) + '.0'
            kv_pip = kv_bt_para_fases(nph_pip, kv_por_trafo.get(uni_cod))

            if nph_pip < 3:
                cnt_pip_clamped += 1

            linhas += [
                f"New Load.PIP_{row['_uni']} bus1={bus}{sfx_pip} phases={nph_pip}",
                f"~ kv={kv_pip:.3f} kw={kw:.6f} kvar={kvar:.6f} daily={daily_pip}",
                f"~ {MODELO_CARGA_PRODIST} conn=wye {ZIPV_PRODIST}  ! {int(row['qtd'])} pontos IP | fases={validos_pip} TIP_CC={tip_pip}",
                "",
            ]
            contagem += 1
            if cadastro is not None:
                # A iluminação pública é agregada por transformador: o `.dss`
                # tem uma carga onde a BDGD tem dezenas de pontos, e os COD_ID
                # individuais não sobrevivem à soma. O cadastro registra a
                # contagem, que é o que resta de verdadeiro.
                cadastro.append({
                    'elemento': ('pip_%s' % row['_uni']).lower(),
                    'tipo': 'iluminação pública (%d pontos)' % int(row['qtd']),
                    'bus': bus,
                    'fases_ligadas': ''.join(validos_pip),
                    'tip_cc': tip_pip,
                    'classe': 'IP',
                    'curva': daily_pip,
                    'curva_origem': ctx_curvas.get('modo', ''),
                    'kw_pico': kw,
                    'kvar_pico': kvar,
                    'kv': kv_pip,
                    'uni_tr_mt': uni_cod,
                })

        if cnt_pip_clamped:
            print(f"  [FIX-PIP] {cnt_pip_clamped} cargas PIP grampeadas para fases reais do trafo (antes: todas .1.2.3.0)")

    with open(caminho_saida, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(linhas))
    total_mt = cnt_consumo_real_mt + cnt_fallback_inst_mt
    pct_cons_mt = cnt_consumo_real_mt / total_mt * 100 if total_mt else 0
    print(f"  [OK] {os.path.basename(caminho_saida)} ({contagem} cargas MT + IP geradas)")
    print(f"  [DIAG] UCMT -> direto:{cnt_direto} espacial:{cnt_espacial} flutuante:{cnt_flutuante}")
    print(f"  [CARGA-MT] Consumo real (ENE_xx) : {cnt_consumo_real_mt:4d} UCs | {kw_consumo_real_mt:8.1f} kW  ({pct_cons_mt:.1f}%)")
    print(f"  [CARGA-MT] Fallback (CAR_INST×fd): {cnt_fallback_inst_mt:4d} UCs | {kw_fallback_inst_mt:8.1f} kW  ({100-pct_cons_mt:.1f}%)")
