# -*- coding: utf-8 -*-
"""A rede física, escrita como o OpenDSS a entende.

Trechos de média e baixa tensão, transformadores, capacitores, e os jumpers que
costuram o que a BDGD deixou solto. É o esqueleto do caso: as cargas penduram
nele, e um erro aqui não aparece como carga errada — aparece como tensão
absurda a três barras de distância.

## Duas armadilhas do formato, aprendidas caro

**A ordem das propriedades importa.** `LineCode` tem de vir ANTES de `phases`:
se vier depois, o OpenDSS reconstrói o número de fases a partir do código e
descarta o que se escreveu. O caso compila, não avisa, e a linha fica com o
número de fases errado.

**Capacitor fora da rede vira ilha.** Um banco cujo `PAC_1` não existe na rede
MT gera uma barra sozinha, sem caminho para a terra — e uma matriz singular
derruba a convergência do alimentador inteiro, não só daquele ponto. Por isso
capacitor sem PAC na rede é descartado, e o descarte é registrado.

## O que é premissa, e está declarado como tal

As impedâncias por categoria (`config.IMPEDANCIAS_*`) são típicas, não medidas.
Quem tiver os condutores reais troca lá, num lugar só. Sai no registro de
ajustes para que ninguém confunda premissa com cadastro.
"""
from __future__ import annotations

import math
import re
import os
import pandas as pd
import statistics

from bdgdcase import ajustes as _ajustes
from bdgdcase.modelo import config
from bdgdcase.modelo.registro import _diag_set, _fmt_amostra
from bdgdcase.modelo.config import (
    TOLERANCIA_COSTURA_COORDENADA_M,
    TOLERANCIA_RELIGACAO_TRAFO_M, TRAFO_KVA_PADRAO,
    CAP_SE_CAPCONTROL_ELEMENT, CAP_SE_CAPCONTROL_OFF_KVAR,
    CAP_SE_CAPCONTROL_ON_KVAR, CAP_SE_KVAR_MINIMO, CAP_SE_MODO,
    FILTRO_ESPACIAL_PAC_ZERO_MT, FORCAR_CHAVES_FECHADAS, IMPEDANCIAS_BT,
    IMPEDANCIAS_MT, IMPEDANCIAS_RAMAL, PAC_ZERO_MT_MAX_DIST_FONTE_KM,
    PAC_ZERO_MT_MAX_DIST_NEAREST_KM, REG_BANK_MONOFAIS_INDEPENDENTES,
    REG_BDGD_LOADLOSS_FLOOR_PCT, REG_BDGD_XHL_FLOOR_PCT,
    REG_CONTROL_PT_PHASE, REG_CTPRIM_MAX, REG_CTPRIM_MIN,
    REG_KVA_MULTIPLICADOR, REG_KVA_PADRAO, REG_MAXTAP, REG_MINTAP,
    REG_MODE, REG_PTRATIO_MAX, REG_PTRATIO_MIN, REG_REV_BAND,
    REG_REV_DELAY_S, REG_REV_THRESHOLD_KW, REG_TAP_DELAY_S, REG_VREG_MAX,
    REG_VREG_MIN, TENSAO_BT_KV,)
from bdgdcase.modelo.cadastro import (
    _bus_bt_valido, _bus_mt_valido, _catalog_cod_map, _chave_bdgd_fechada,
    _ctprim_de_trel_tc, _float_cell, _kva_e_cod_pot, _linecode_ramal,
    _norm_cod_bdgd, _num, _parametros_trafo_regulador, _ptratio_de_treltp,
    eh_center_tap, fases_info, kva_trafo, nome_linecode, regulador_fases_bdgd,
    sanitizar_bus, sanitizar_bus_bt, segundo_enrolamento_por_trafo,
    tipo_inst_categoria,)
from bdgdcase.modelo.cadastro_csv import _cadastro_regulador, _primeiro, _texto
from bdgdcase.modelo.coordenadas import _geom_line_endpoints_xy
from bdgdcase.modelo.topologia import (
    _diagnostico_chaves_mt, chaves_que_bypassam_reguladores, uniao_dos_emitidos,)


# ==============================================================================
# GERADORES DE ARQUIVO .DSS
# ==============================================================================

def gerar_linecodes(segcon, ssdmt, ssdbt, ramlig, caminho_saida, cadastro=None):
    """Cria LineCodes.dss com impedâncias reais do catálogo SEGCON e mantém os genéricos como fallback."""
    linhas = [
        "! ============================================================",
        "! LineCodes.dss – Catálogo de Cabos (SEGCON + Genéricos)",
        "! ============================================================",
        "",
    ]

    # 1. Manter os genéricos como Plano B (Fallback para trechos com dados nulos)
    grupos = [
        ("MT", IMPEDANCIAS_MT,    "Linhas de Media Tensao (Fallback)"),
        ("BT", IMPEDANCIAS_BT,    "Linhas de Baixa Tensao (Fallback)"),
        ("RL", IMPEDANCIAS_RAMAL, "Ramais de Ligacao (Fallback)"),
    ]
    for nivel, tabela, descricao in grupos:
        linhas.append(f"! --- {descricao} ---")
        for (nph, cat), (r1, x1, c1, r0, x0) in sorted(tabela.items()):
            nome = nome_linecode(nivel, nph, cat)
            linhas += [
                f"New LineCode.{nome} nphases={nph} units=km",
                f"~ R1={r1:.4f} X1={x1:.4f} C1={c1:.2f}",
                f"~ R0={r0:.4f} X0={x0:.4f} C0={c1:.2f}",
                "",
            ]

    # 2. Resgatar os condutores reais usados fisicamente na rede
    codigos_usados = set()
    codigos_mt = set()
    for df, e_mt in ((ssdmt, True), (ssdbt, False), (ramlig, False)):
        if df is not None and not df.empty and 'TIP_CND' in df.columns:
            codigos = df['TIP_CND'].dropna().astype(str).str.strip().unique()
            usados = {_norm_cod_bdgd(c) for c in codigos
                      if c and c.lower() != 'nan'}
            usados.discard('')
            codigos_usados.update(usados)
            if e_mt:
                codigos_mt.update(usados)

    linhas.append("! --- Condutores Reais do BDGD (Extraídos da Tabela SEGCON) ---")
    contagem_reais = 0
    segcon_limpo = None
    _tem_segcon = (segcon is not None and not segcon.empty and bool(codigos_usados))
    if _tem_segcon:
        # Puxar do catálogo SEGCON apenas os cabos cujo COD_ID foi usado na rede
        # `_norm_cod_bdgd` dos dois lados. Num `COD_ID` só numérico que passe
        # por CSV com alguma célula vazia, o pandas dá `float64` e `390` volta
        # como `390.0`: o catálogo emitiria `CND_390.0` e a linha citaria
        # `CND_390`, dois nomes para o mesmo cabo. Nada quebra de imediato — o
        # trecho ganha impedância genérica —, e o diagnóstico continua dizendo
        # que 100% dos cabos vieram do SEGCON.
        segcon_limpo = segcon[segcon['COD_ID'].map(_norm_cod_bdgd).isin(codigos_usados)]

        for _, row in segcon_limpo.iterrows():
            cod = _norm_cod_bdgd(row['COD_ID'])
            # O `try/except ValueError` que estava aqui pegava o texto ilegível
            # e deixava passar o `NaN`, que é o caso comum: a coluna existe e a
            # célula está vazia. O LineCode saía com `R1=nan`.
            r1 = _num(row.get('R1'), 0.5)
            x1 = _num(row.get('X1'), 0.5)
            # Sem sequência zero, proporção usual — evita matriz singular.
            r0 = _num(row.get('R0'), r1 * 1.5)
            x0 = _num(row.get('X0'), x1 * 3.0)
            cnom = _num(row.get('CNOM'), 100)
            if r1 <= 0:
                r1 = 0.5
            if x1 <= 0:
                x1 = 0.5
            if cnom <= 0:
                cnom = 100

            if r0 <= 0: r0 = r1
            if x0 <= 0: x0 = x1

            # Definimos o catálogo em 3 fases, o OpenDSS trunca automaticamente se a linha for mono/bi.
            linhas += [
                f"New LineCode.CND_{cod} nphases=3 units=km",
                f"~ R1={r1:.6f} X1={x1:.6f} R0={r0:.6f} X0={x0:.6f} NormAmps={cnom:.1f}",
                "",
            ]
            contagem_reais += 1
            if cadastro is not None:
                # O condutor é catálogo: dezenas de tipos servem milhares de
                # trechos. Repetir bitola e material em cada trecho encheria o
                # arquivo de rede com a mesma linha copiada; aqui é uma vez por
                # tipo, e o trecho guarda só a referência.
                cadastro.append({
                    'elemento': ('cnd_%s' % cod).lower(),
                    'cod_id': cod,
                    'r1_ohm_km': r1,
                    'x1_ohm_km': x1,
                    # `cnom_a` é a que vira NormAmps e governa o percentual de
                    # carregamento do painel; `cmax_a` é a máxima do catálogo.
                    # As duas divergem no cadastro real, e ver as duas lado a
                    # lado é o que permite julgar um "1956%".
                    'cnom_a': cnom,
                    'cmax_a': _texto(row.get('CMAX')),
                    'bit_fas': _texto(row.get('BIT_FAS_1')),
                    'bit_neu': _texto(row.get('BIT_NEU')),
                    'mat_fas': _texto(row.get('MAT_FAS_1')),
                    'mat_neu': _texto(row.get('MAT_NEU')),
                    'iso_fas': _texto(row.get('ISO_FAS_1')),
                })

    # Um `TIP_CND` que o SEGCON extraído não cobre deixava a linha citando um
    # LineCode que nunca foi escrito, e o OpenDSS recusa o arquivo inteiro na
    # compilação — o alimentador não sai, e a mensagem fala de um nome que não
    # está em lugar nenhum do cadastro. Aqui cada código citado ganha o
    # genérico da sua rede, e o caso resolve com o aviso à vista.
    emitidos = {_norm_cod_bdgd(row['COD_ID'])
                for _, row in segcon_limpo.iterrows()} if _tem_segcon else set()
    orfaos = sorted(c for c in codigos_usados if c not in emitidos)
    if orfaos:
        linhas.append('! --- Condutores citados pela rede e ausentes do SEGCON ---')
        for cod in orfaos:
            de_mt = cod in codigos_mt
            r1, x1, c1, r0, x0 = (IMPEDANCIAS_MT if de_mt
                                  else IMPEDANCIAS_BT)[(3, 'AER')]
            linhas += [
                f"! CND_{cod}: TIP_CND ausente do SEGCON extraido; "
                f"impedancia generica de {'media' if de_mt else 'baixa'}.",
                f"New LineCode.CND_{cod} nphases=3 units=km",
                f"~ R1={r1:.6f} X1={x1:.6f} R0={r0:.6f} X0={x0:.6f} NormAmps=100.0",
                "",
            ]
        print('  [AVISO] %d condutor(es) citados pela rede nao estao no SEGCON '
              'extraido; emitidos com impedancia generica: %s'
              % (len(orfaos), ', '.join(orfaos[:6])))
        _ajustes.registrar(
            'condutores', 'Condutores citados pela rede e ausentes do catálogo',
            quantos=len(orfaos), unidade='tipos de cabo', efeito='simulacao',
            detalhe='Estes `TIP_CND` aparecem nos trechos mas não têm linha no '
                    'SEGCON extraído. Foram emitidos com impedância genérica '
                    'do nível de tensão em que aparecem.',
            porque='Sem isso o OpenDSS recusa o arquivo inteiro citando um nome '
                   'que não está em lugar nenhum do cadastro, e o alimentador '
                   'não sai. Com a genérica, o caso resolve e a aproximação '
                   'fica à vista.',
            exemplos=list(orfaos[:5]))

    with open(caminho_saida, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(linhas))
    print(f"  [OK] {os.path.basename(caminho_saida)} ({contagem_reais} cabos reais de SEGCON mapeados)")

def gerar_linhas_mt(
    ssdmt,
    unsemt,
    unremt,
    bus_fonte,
    caminho_saida,
    tpotaprt=None,
    treltp=None,
    treltc=None,
    tcor=None,
    cadastro=None,
):
    """Cria Linhas_MT.dss operando chaves e garantindo continuidade, respeitando FASES REAIS.

    Catálogos opcionais (extraídos por bdgdcase.extracao): TPOTAPRT, TRELTP,
    TRELTC, TCOR — para kVA, ptratio e ctprim dos reguladores conforme códigos
    DDA no UNREMT.
    """
    linhas = [
        "! ============================================================",
        "! Linhas_MT.dss – Segmentos MT (SSDMT), Chaves (UNSEMT) e Reguladores (UNREMT)",
        "! ============================================================",
        "",
    ]

    linhas.append("! --- Cabos MT (SSDMT) ---")
    contagem = 0
    n_real_mt = 0
    n_generico_mt = 0

    def _dist_km(p1, p2):
        """Distância aproximada em km entre dois pontos dados em graus.

        Aproximação plana com correção de cosseno na latitude. Num alimentador,
        que raramente passa de algumas dezenas de km, o erro contra haversine é
        irrelevante — e aqui a distância serve para comparar candidatos, não
        para medir.
        """
        if not p1 or not p2:
            return float('inf')
        x1, y1 = p1
        x2, y2 = p2
        dx_km = (x2 - x1) * 111.32 * math.cos(math.radians((y1 + y2) / 2.0))
        dy_km = (y2 - y1) * 110.57
        return float((dx_km * dx_km + dy_km * dy_km) ** 0.5)

    # PAC=0 em SSDMT pode indicar recortes/artefatos de extração que criam conexões
    # irreais direto na fonte. Pré-calcula coordenadas dos PACs válidos para decidir
    # por distância onde ancorar esses pontos.
    mt_pac_coords = {}
    if FILTRO_ESPACIAL_PAC_ZERO_MT and ssdmt is not None and not ssdmt.empty:
        from collections import defaultdict
        _pts = defaultdict(list)
        for _, row in ssdmt.iterrows():
            geom = row.get('geometry')
            if geom is None or geom.is_empty or getattr(geom, 'geom_type', '') not in ('LineString', 'MultiLineString'):
                continue
            ends = _geom_line_endpoints_xy(geom)
            if not ends:
                continue
            sxy, exy = ends
            p1r = str(row.get('PAC_1', '')).strip()
            p2r = str(row.get('PAC_2', '')).strip()
            if p1r not in ('0', '', 'nan', 'None'):
                p1 = sanitizar_bus(p1r)
                if _bus_mt_valido(p1):
                    _pts[p1].append(sxy)
            if p2r not in ('0', '', 'nan', 'None'):
                p2 = sanitizar_bus(p2r)
                if _bus_mt_valido(p2):
                    _pts[p2].append(exy)
        for pac, lst in _pts.items():
            if not lst:
                continue
            xs = [float(t[0]) for t in lst]
            ys = [float(t[1]) for t in lst]
            mt_pac_coords[pac] = (statistics.median(xs), statistics.median(ys))

    coord_fonte_mt = mt_pac_coords.get(bus_fonte)
    pac_zero_fonte_ok = 0
    pac_zero_ancorado_nearest = 0
    pac_zero_fallback_fonte = 0

    def _resolver_pac_zero(coord_zero):
        """Escolhe a barra de um trecho cujo PAC veio zerado.

        `PAC = 0` é comum na BDGD e não quer dizer "sem conexão": quer dizer
        que o campo não foi preenchido. O trecho existe, tem geometria, e
        sumiria do caso se ninguém decidisse onde ele se liga.

        A ordem das tentativas vai do mais seguro ao menos: se a ponta está
        perto da fonte, é a fonte; senão, a barra de MT mais próxima dentro de
        uma margem; senão, a fonte assim mesmo — e aí o caso registra que foi
        arbítrio.
        """
        nonlocal pac_zero_fonte_ok, pac_zero_ancorado_nearest, pac_zero_fallback_fonte
        if not FILTRO_ESPACIAL_PAC_ZERO_MT or not coord_zero:
            pac_zero_fallback_fonte += 1
            return bus_fonte
        if coord_fonte_mt:
            d_fonte = _dist_km(coord_zero, coord_fonte_mt)
            if d_fonte <= float(PAC_ZERO_MT_MAX_DIST_FONTE_KM):
                pac_zero_fonte_ok += 1
                return bus_fonte
        melhor_pac = None
        melhor_d = float('inf')
        for pac, cxy in mt_pac_coords.items():
            if pac == bus_fonte:
                continue
            d = _dist_km(coord_zero, cxy)
            if d < melhor_d:
                melhor_d = d
                melhor_pac = pac
        if melhor_pac is not None and melhor_d <= float(PAC_ZERO_MT_MAX_DIST_NEAREST_KM):
            pac_zero_ancorado_nearest += 1
            return melhor_pac
        pac_zero_fallback_fonte += 1
        return bus_fonte

    if ssdmt is not None and not ssdmt.empty:
        for _, row in ssdmt.iterrows():
            cod   = str(row['COD_ID']).strip()
            p1_raw = str(row['PAC_1']).strip()
            p2_raw = str(row['PAC_2']).strip()
            coord_zero_p1 = None
            coord_zero_p2 = None
            if p1_raw in ('0', '', 'nan', 'None') or p2_raw in ('0', '', 'nan', 'None'):
                geom = row.get('geometry')
                if geom is not None and not geom.is_empty and getattr(geom, 'geom_type', '') in ('LineString', 'MultiLineString'):
                    ends = _geom_line_endpoints_xy(geom)
                    if ends:
                        sxy, exy = ends
                        if p1_raw in ('0', '', 'nan', 'None'):
                            coord_zero_p1 = sxy
                        if p2_raw in ('0', '', 'nan', 'None'):
                            coord_zero_p2 = exy
            # PAC=0 no BDGD: resolve por filtro espacial para evitar conexões irreais.
            p1 = _resolver_pac_zero(coord_zero_p1) if p1_raw in ('0', '', 'nan', 'None') else sanitizar_bus(p1_raw)
            p2 = _resolver_pac_zero(coord_zero_p2) if p2_raw in ('0', '', 'nan', 'None') else sanitizar_bus(p2_raw)
            fas   = str(row.get('FAS_CON', 'ABC')).strip().upper()

            comp_bruto = _num(row.get('COMP'), 100) / 1000.0
            comp = max(comp_bruto, 0.001)

            cat   = tipo_inst_categoria(row.get('TIP_INST', 'AER'))
            tip_cnd = _norm_cod_bdgd(row.get('TIP_CND'))

            nph, sfx_line, _, _ = fases_info(fas)

            if tip_cnd and tip_cnd.lower() != 'nan':
                lc = f"CND_{tip_cnd}"
                n_real_mt += 1
            else:
                lc = nome_linecode('MT', nph, cat)
                n_generico_mt += 1

            # `LineCode` antes de `phases`, e não depois: ao receber o
            # código o OpenDSS remonta o elemento com o número de fases DELE.
            # Um trecho monofásico escrito com `phases=1` na primeira linha e o
            # código trifásico na continuação vira um elemento de 3 fases, com
            # `nodeorder=[3,2,3,...]` — e o nó 2, que ninguém alimenta, passa a
            # existir na barra. Medido: 20.101 trechos assim nos 11
            # alimentadores convertidos, em todas as distribuidoras.
            #
            # O estrago é discreto. A tensão dos nós reais continua certa, mas o
            # `CalcVoltageBases` casa a base da barra pelo conjunto dos nós, e o
            # nó morto puxa a média para baixo: uma barra de 117 V ficou com
            # base de 73 V e apareceu a 1,59 pu.
            linhas += [
                f"New Line.MT_{cod} bus1={p1}{sfx_line} bus2={p2}{sfx_line}",
                f"~ LineCode={lc} phases={nph} length={comp:.6f} units=km",
                "",
            ]
            contagem += 1
            if cadastro is not None:
                cadastro.append({
                    'elemento': ('mt_%s' % cod).lower(), 'cod_id': cod,
                    'familia': 'cabo MT', 'bus1': p1, 'bus2': p2,
                    'comp_m': comp * 1000.0, 'fases': nph, 'fas_con': fas,
                    'tip_inst': _texto(row.get('TIP_INST')),
                    'tip_cnd': tip_cnd, 'linecode': lc,
                    'uni_tr_mt': _primeiro(row.get('UNI_TR_MT')),
                })

    if FILTRO_ESPACIAL_PAC_ZERO_MT:
        total_zero = pac_zero_fonte_ok + pac_zero_ancorado_nearest + pac_zero_fallback_fonte
        if total_zero > 0:
            print(
                f"  [DIAG-MT] PAC_ZERO filtro espacial: total={total_zero} | "
                f"fonte={pac_zero_fonte_ok} | nearest={pac_zero_ancorado_nearest} | fallback_fonte={pac_zero_fallback_fonte}"
            )

    # Trechos com regulador (UNREMT): se existir chave UNSEMT no mesmo par de PACs, ela atua como
    # bypass físico. Com switch=y enabled=yes fica em paralelo ao Transformer.REG_* (~curto vs. trafo),
    # gerando correntes circulantes e Q na fonte ordens de grandeza acima do real. Com o regulador
    # modelado, o bypass deve ficar fora do circuito (enabled=no).
    pares_com_regulador = set()
    if unremt is not None and not unremt.empty:
        for _, rrow in unremt.iterrows():
            rp1 = str(rrow.get('PAC_1', '')).strip()
            rp2 = str(rrow.get('PAC_2', '')).strip()
            if not rp1 or not rp2 or rp1 == 'nan' or rp2 == 'nan':
                continue
            r1_dss = bus_fonte if rp1 in ('0', '') else sanitizar_bus(rp1)
            r2_dss = bus_fonte if rp2 in ('0', '') else sanitizar_bus(rp2)
            pares_com_regulador.add(tuple(sorted((r1_dss, r2_dss))))

    linhas.append("! --- Chaves / Seccionadoras MT (UNSEMT) ---")
    contagem_sw = 0
    # Duas passadas. Na primeira, cada chave decide o próprio estado; na
    # segunda, quem decide é o circuito: uma chave fechada que dá ao
    # regulador um caminho de contorno abre, esteja no mesmo par de PACs ou
    # a três chaves de distância pelo pátio da subestação (UVA01).
    chaves_lidas = []
    if unsemt is not None and not unsemt.empty:
        for _, row in unsemt.iterrows():
            cod = str(row['COD_ID']).strip()
            p1  = str(row.get('PAC_1', '')).strip()
            p2  = str(row.get('PAC_2', '')).strip()
            if not p1 or not p2 or p1 == 'nan' or p2 == 'nan': continue

            p1_dss = bus_fonte if p1 in ('0', '') else sanitizar_bus(p1)
            p2_dss = bus_fonte if p2 in ('0', '') else sanitizar_bus(p2)

            estado = str(row.get('P_N_OPE', 'F')).strip().upper()

            par_sw = tuple(sorted((p1_dss, p2_dss)))
            if par_sw in pares_com_regulador:
                # Segurança elétrica: evita bypass em paralelo com Transformer.REG_*
                sw_enabled = "no"
            else:
                if FORCAR_CHAVES_FECHADAS:
                    sw_enabled = "yes"
                else:
                    sw_enabled = "yes" if _chave_bdgd_fechada(estado) else "no"
            chaves_lidas.append((cod, p1, p2, p1_dss, p2_dss, estado, sw_enabled, row))

    _pares_mt = []
    for _l in linhas:
        _m = re.match(r'New Line\.MT_\S+ bus1=([^\s.]+)\S* bus2=([^\s.]+)', _l)
        if _m:
            _pares_mt.append((_m.group(1).lower(), _m.group(2).lower()))
    _regs = []
    if unremt is not None and not unremt.empty:
        for _, rrow in unremt.iterrows():
            rp1 = str(rrow.get('PAC_1', '')).strip(); rp2 = str(rrow.get('PAC_2', '')).strip()
            if not rp1 or not rp2 or rp1 == 'nan' or rp2 == 'nan':
                continue
            _regs.append((str(rrow.get('COD_ID', '')).strip(),
                          (bus_fonte if rp1 in ('0', '') else sanitizar_bus(rp1)).lower(),
                          (bus_fonte if rp2 in ('0', '') else sanitizar_bus(rp2)).lower()))
    abrir_por_contorno, reg_sem_saida = chaves_que_bypassam_reguladores(
        _pares_mt,
        [(c, p1d.lower(), p2d.lower(), en == 'yes') for c, _, _, p1d, p2d, _, en, _ in chaves_lidas],
        _regs)

    if unsemt is not None and not unsemt.empty:
        for cod, p1, p2, p1_dss, p2_dss, estado, sw_enabled, row in chaves_lidas:
            # CORREÇÃO: Agora a chave descobre quantas fases ela deve ter lendo o BDGD
            fas = str(row.get('FAS_CON', 'ABC')).upper().strip()
            nph, sfx_line, _, _ = fases_info(fas)

            comentario = f"! SE-bus: {p1} | P_N_OPE original: {estado}"
            if cod in abrir_por_contorno:
                sw_enabled = "no"
                comentario += (" | enabled=no: fechada, dava ao regulador %s um caminho de contorno"
                               % abrir_por_contorno[cod])
            elif sw_enabled == "no":
                comentario += " | enabled=no: mesmo trecho tem REG (evita bypass em paralelo ao trafo)"
            if not FORCAR_CHAVES_FECHADAS:
                comentario += " | modo=BDGD"
            else:
                comentario += " | modo=forcar_fechadas"

            linhas += [
                f"New Line.SW_{cod} bus1={p1_dss}{sfx_line} bus2={p2_dss}{sfx_line} phases={nph} {comentario}",
                f"~ switch=y enabled={sw_enabled}",
                ""
            ]
            contagem_sw += 1
            if cadastro is not None:
                cadastro.append({
                    'elemento': ('sw_%s' % cod).lower(), 'cod_id': cod,
                    'familia': 'chave MT', 'bus1': p1_dss, 'bus2': p2_dss,
                    'fases': nph, 'fas_con': fas,
                    # `p_n_ope` é a posição normal do cadastro; o que foi para o
                    # `.dss` pode divergir dela, quando a chave estaria em
                    # paralelo com um regulador.
                    'p_n_ope': estado,
                    'ligada_no_caso': sw_enabled == 'yes',
                })

    linhas.append("! --- Reguladores de Tensão MT (UNREMT) - Continuidade ---")
    map_tpot = _catalog_cod_map(tpotaprt)
    map_tp = _catalog_cod_map(treltp)
    map_tc = _catalog_cod_map(treltc)
    map_cor = _catalog_cod_map(tcor)

    reg_mode = str(REG_MODE).strip().lower()
    if reg_mode != 'bdgd_electric':
        reg_mode = 'stable'

    contagem_reg = 0
    if unremt is not None and not unremt.empty:
        for _, row in unremt.iterrows():
            cod = str(row['COD_ID']).strip()
            p1  = str(row.get('PAC_1', '')).strip()
            p2  = str(row.get('PAC_2', '')).strip()
            if not p1 or not p2 or p1 == 'nan' or p2 == 'nan': continue

            p1_dss = bus_fonte if p1 in ('0', '') else sanitizar_bus(p1)
            p2_dss = bus_fonte if p2 in ('0', '') else sanitizar_bus(p2)

            fas = str(row.get('FAS_CON', 'ABC')).strip().upper()
            nph, sfx_line, _, _ = fases_info(fas)

            # --- MODELAGEM DO REGULADOR DE TENSÃO (RegControl) ---
            # Convenção de buses: PAC_1 (UNREMT) = winding 1 = montante (fonte)
            #                     PAC_2 (UNREMT) = winding 2 = jusante (carga)  <- tap aqui
            # winding=2: RegControl monitora e ajusta o tap no secundário (jusante).
            #   Quando V_jusante cai  →  eleva tap  →  V_jusante sobe  ✓
            #   Quando V_jusante sobe →  reduz tap  →  V_jusante cai   ✓
            #
            # O que o regulador faz quando o fluxo inverte é `config.MODOS_REVERSO`
            # (padrão `neutro`), escolhido em `--regulador-reverso`; o limiar
            # `REG_REV_THRESHOLD_KW` é baixo para que qualquer inversão real conte.
            #
            # XHL: lido ao pé da letra (`cadastro.xhl_regulador`). %LoadLoss: pelo
            # R do catálogo, via `_eqre_r_xhl_para_percent_opendss`.
            #
            # maxtapchange=1: limita a 1 passo por intervalo de controle — evita hunting.

            # PTRatio / ctprim / R XHL: modo 'stable' evita não convergência no Daily (~14 h com GD).
            kv_ln = config.TENSAO_MT_KV / math.sqrt(3)
            ptratio_geo = (kv_ln * 1000.0) / 120.0

            kva_resolvido, _orig_kva = _kva_e_cod_pot(row, map_tpot)
            if kva_resolvido is not None:
                kva_reg = max(kva_resolvido * float(REG_KVA_MULTIPLICADOR), 1.0)
            else:
                kva_reg = max(float(REG_KVA_PADRAO) * float(REG_KVA_MULTIPLICADOR), 1.0)

            if reg_mode == 'bdgd_electric':
                ptratio = ptratio_geo
                cod_rel_tp = _norm_cod_bdgd(row.get('REL_TP'))
                if cod_rel_tp and map_tp:
                    pr = _ptratio_de_treltp(map_tp.get(cod_rel_tp), kv_ln * 1000.0)
                    if pr and pr > 0:
                        ptratio = pr
                if ptratio < REG_PTRATIO_MIN or ptratio > REG_PTRATIO_MAX:
                    ptratio = ptratio_geo

                ctprim = 200.0
                cod_rel_tc = _norm_cod_bdgd(row.get('REL_TC'))
                cod_cor = _norm_cod_bdgd(row.get('COR_NOM'))
                row_tc = map_tc.get(cod_rel_tc) if cod_rel_tc else None
                row_cor = map_cor.get(cod_cor) if cod_cor else None
                ct_try = _ctprim_de_trel_tc(row_tc, row_cor)
                if ct_try and ct_try > 0:
                    ctprim = min(max(ct_try, REG_CTPRIM_MIN), REG_CTPRIM_MAX)

                xhl_u, pct_ld, pct_nl, pct_im = _parametros_trafo_regulador(row, kva_reg)
                xhl_u = max(float(xhl_u), float(REG_BDGD_XHL_FLOOR_PCT))
                pct_ld = max(float(pct_ld), float(REG_BDGD_LOADLOSS_FLOOR_PCT))

                vreg = 120.0
                ten_reg = _float_cell(row, ('TEN_REG',))
                if ten_reg is not None and 0.85 <= float(ten_reg) <= 1.15:
                    vreg = 120.0 * float(ten_reg)
                vreg = min(max(vreg, REG_VREG_MIN), REG_VREG_MAX)
                band = 3.0
            else:
                # Modo estável (padrão): mesmo raciocínio físico do tap; impedâncias mínimas para o solver.
                ptratio = ptratio_geo
                ctprim = 200.0
                xhl_u, pct_ld, pct_nl, pct_im = 1.0, 0.001, 0.001, 0.001
                vreg = 120.0
                band = 3.0

            rev_tail = ""
            if config.REG_CONTROL_REVERSIBLE:
                rev_tail = (
                    f" reversible=yes revThreshold={float(REG_REV_THRESHOLD_KW):.4g} "
                    f"revDelay={float(REG_REV_DELAY_S):.4g} "
                    f"revVreg={vreg:.4f} revBand={float(REG_REV_BAND):.2f}"
                )
            elif config.REG_CONTROL_COGEN:
                rev_tail = (
                    f" Cogen=yes"
                    f" revThreshold={float(REG_REV_THRESHOLD_KW):.4g}"
                    f" revDelay={float(REG_REV_DELAY_S):.4g}"
                    f" revVreg={vreg:.4f} revBand={float(REG_REV_BAND):.2f}"
                )
            elif config.REG_CONTROL_REV_NEUTRAL:
                rev_tail = (
                    f" reversible=yes revNeutral=yes"
                    f" revThreshold={float(REG_REV_THRESHOLD_KW):.4g}"
                    f" revDelay={float(REG_REV_DELAY_S):.4g}"
                )

            rc_core = (
                f"winding=2 vreg={vreg:.4f} band={band:.2f} ptratio={ptratio:.4f} "
                f"ctprim={ctprim:.1f} delay=30 TapDelay={float(REG_TAP_DELAY_S):.4g} "
                f"maxtapchange=1{rev_tail}"
            )
            # Sufixo de tap para o Transformer: mintap/maxtap/numtaps pertencem ao Transformer, nao ao RegControl
            _tap_suffix = (
                f" mintap={float(REG_MINTAP):.5f} maxtap={float(REG_MAXTAP):.5f} "
                f"numtaps=32"
            )

            if REG_BANK_MONOFAIS_INDEPENDENTES:
                fases_regs = regulador_fases_bdgd(fas)
                if not fases_regs:
                    continue
                n_units = len(fases_regs)
                kva_unit = max(kva_reg / float(n_units), 0.001)
                linhas.append(
                    f"! --- Regulador {cod} | montante={p1} jusante={p2} | {n_units}×1φ independentes | "
                    f"kVA_banco={kva_reg:.1f} (≈{kva_unit:.1f} kVA/fase) | REG_MODE={reg_mode} ---"
                )
                for suf, node in fases_regs:
                    tag = f"_{suf}" if n_units > 1 else ""
                    linhas.append(
                        f"New Transformer.REG_{cod}{tag} phases=1 windings=2 bank=REG_{cod} "
                        f"buses=[{p1_dss}.{node} {p2_dss}.{node}] conns=[wye wye] "
                        f"kVs=[{kv_ln:.4f} {kv_ln:.4f}] kVAs=[{kva_unit:.1f} {kva_unit:.1f}] XHL={xhl_u:.4f} "
                        f"%LoadLoss={pct_ld:.6f} %noloadloss={pct_nl:.6f} %imag={pct_im:.6f}{_tap_suffix}"
                    )
                for suf, _node in fases_regs:
                    tag = f"_{suf}" if n_units > 1 else ""
                    linhas.append(
                        f"New RegControl.RC_{cod}{tag} transformer=REG_{cod}{tag} {rc_core}"
                    )
                linhas.append("")
                contagem_reg += n_units
                if cadastro is not None:
                    # Uma linha por enrolamento, e não por banco: o contrato do
                    # arquivo é uma linha por elemento REALMENTE emitido, e o
                    # `.dss` emite um `Transformer` por fase. Os três repetem o
                    # mesmo `cod_id`, que é o equipamento no cadastro — quem lê
                    # os agrupa por aí.
                    for suf, _n in fases_regs:
                        tag = f"_{suf}" if n_units > 1 else ""
                        cadastro.append(_cadastro_regulador(
                            row, cod, ('reg_%s%s' % (cod, tag)).lower(),
                            p1_dss, p2_dss, 1, fas, kva_unit))
            else:
                # Modelo legado: um trafo multifásico + um RegControl (tap único em todas as fases).
                kv_trafo = config.TENSAO_MT_KV if nph >= 2 else kv_ln
                _pt = str(REG_CONTROL_PT_PHASE).strip().lower()
                if _pt not in ("min", "max"):
                    _pt = "min"
                pt_phase = f" PTPhase={_pt}" if nph > 1 else ""
                linhas.extend([
                    f"! --- Regulador {cod} | montante={p1} jusante={p2} ({nph} fases) | kVA={kva_reg:.1f} | REG_MODE={reg_mode} ---",
                    f"New Transformer.REG_{cod} phases={nph} windings=2 "
                    f"buses=[{p1_dss}{sfx_line} {p2_dss}{sfx_line}] conns=[wye wye] "
                    f"kVs=[{kv_trafo:.4f} {kv_trafo:.4f}] kVAs=[{kva_reg:.1f} {kva_reg:.1f}] XHL={xhl_u:.4f} "
                    f"%LoadLoss={pct_ld:.6f} %noloadloss={pct_nl:.6f} %imag={pct_im:.6f}{_tap_suffix}",
                    f"New RegControl.RC_{cod} transformer=REG_{cod} winding=2{pt_phase} {rc_core}",
                    "",
                ])
                contagem_reg += 1
                if cadastro is not None:
                    cadastro.append(_cadastro_regulador(
                        row, cod, ('reg_%s' % cod).lower(),
                        p1_dss, p2_dss, nph, fas, kva_reg))

    with open(caminho_saida, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(linhas))
    _diagnostico_chaves_mt(caminho_saida)
    _ajustes.registrar(
        'topologia', 'Chave de contorno de regulador aberta',
        quantos=len(abrir_por_contorno), unidade='chaves', efeito='simulacao',
        detalhe='O cadastro traz a chave fechada (`P_N_OPE=F`), e fechada ela '
                'dá ao regulador um caminho que o contorna — no mesmo par de '
                'PACs ou por um caminho de chaves pelo pátio da subestação.',
        porque='Regulador em paralelo com um caminho fechado não regula: com o '
               'tape fora de 1,0 o caminho é um curto sobre o transformador, e '
               'a corrente circula. Medido no UVA01: 1.638 A no regulador da '
               'subestação com a fonte entregando 457 A, 16,9 MVAr consumidos '
               'pelos reguladores e a média inteira a 0,8 pu.',
        exemplos=['SW_%s (contorna %s)' % (c, r) for c, r in list(abrir_por_contorno.items())[:4]])
    _ajustes.registrar(
        'nao_corrigido', 'Regulador contornado por linhas',
        quantos=len(reg_sem_saida), unidade='reguladores', efeito='nao_corrigido',
        detalhe='Há um caminho de TRECHOS (não de chaves) ligando os dois lados '
                'do regulador, e não há chave para abrir.',
        porque='A rede de média é malhada no cadastro em torno deste '
               'regulador. Abrir um trecho seria inventar topologia; ele fica '
               'como está, e a corrente que circular por ele é do cadastro.',
        exemplos=['REG_%s' % r for r in reg_sem_saida[:4]])
    pct_real_mt = n_real_mt / contagem * 100 if contagem else 0
    print(f"  [OK] {os.path.basename(caminho_saida)} ({contagem} cabos MT, {contagem_sw} chaves, {contagem_reg} reguladores)")
    print(f"  [CAB-MT] R/X do SEGCON: {n_real_mt}/{contagem} ({pct_real_mt:.1f}%)  |  Genérico: {n_generico_mt}/{contagem} ({100-pct_real_mt:.1f}%)")

def gerar_linhas_bt(ssdbt, ramlig, caminho_saida, untrmt=None,
                    cadastro=None, nos_por_barra=None,
                    ligacoes_por_barra=None, nos_secundario=None,
                    nos_por_ligacao=None):
    """Cria Linhas_BT.dss conectando LineCodes reais com isolamento estrito de MT.

    ``nos_por_ligacao``: dict a preencher com ``{frozenset({b1, b2}): {nós}}``
    — os nós que as linhas entre cada par de barras de baixa de fato
    conduzem. É o que permite saber, barra a barra, quais nós têm caminho
    até um secundário de transformador (:func:`topologia.nos_energizados_bt`).
    """
    linhas = [
        "! ============================================================",
        "! Linhas_BT.dss – Segmentos BT (SSDBT) e Ramais de Ligacao (RAMLIG)",
        "! ============================================================",
        "",
    ]

    # Os nos que cada barra ja tem. Semeado com o secundario dos
    # transformadores — que e onde o ramal costuma pendurar — e completado
    # com os cabos de baixa a medida que saem. Serve ao remapeamento
    # posicional do ramal, adiante.
    nos_disponiveis = {}
    for _b, _ns in (nos_secundario or {}).items():
        nos_disponiveis.setdefault(str(_b).lower(), set()).update(
            int(n) for n in _ns if int(n) != 0)

    def _anotar_nos(barra, sufixo):
        """Guarda os nos que este elemento acabou de dar a barra."""
        alvo = nos_disponiveis.setdefault(str(barra).lower(), set())
        for pedaco in str(sufixo).split('.'):
            if pedaco.isdigit() and int(pedaco) != 0:
                alvo.add(int(pedaco))

    linhas.append("! --- Cabos BT (SSDBT) ---")
    contagem_bt = 0
    n_real_bt = 0
    n_generico_bt = 0
    bt_skip_fas_n = []
    if ssdbt is not None and not ssdbt.empty:
        _diag_set("ssdbt_total", len(ssdbt))
        for _, row in ssdbt.iterrows():
            cod  = str(row['COD_ID']).strip()
            p1   = sanitizar_bus_bt(row['PAC_1'])
            p2   = sanitizar_bus_bt(row['PAC_2'])
            fas  = str(row.get('FAS_CON', 'ABCN')).upper().strip()
            comp = _num(row.get('COMP'), 50) / 1000.0
            cat  = tipo_inst_categoria(row.get('TIP_INST', 'AER'))
            tip_cnd = _norm_cod_bdgd(row.get('TIP_CND'))

            # Cabos de fase=N são retorno pelo neutro/terra — não criam tensão e só
            # geram buses fantasmas no modelo. Pular. Cargas/GDs em buses que só seriam
            # alcançados por N-seg são realocadas para o PAC_2 do trafo correto via
            # UNI_TR_MT (fallback por trafo tem prioridade sobre fallback espacial).
            if fas == 'N':
                bt_skip_fas_n.append(cod)
                continue

            nph, sfx_line, _, _ = fases_info(fas)

            if tip_cnd and tip_cnd.lower() != 'nan':
                lc = f"CND_{tip_cnd}"
                n_real_bt += 1
            else:
                lc = nome_linecode('BT', nph, cat)
                n_generico_bt += 1

            # `LineCode` antes de `phases` — o porquê está no trecho de média,
            # acima.
            linhas += [
                f"New Line.BT_{cod} bus1={p1}{sfx_line} bus2={p2}{sfx_line}",
                f"~ LineCode={lc} phases={nph} length={comp:.6f} units=km", ""
            ]
            _anotar_nos(p1, sfx_line)
            _anotar_nos(p2, sfx_line)
            contagem_bt += 1
            if cadastro is not None:
                cadastro.append({
                    'elemento': ('bt_%s' % cod).lower(), 'cod_id': cod,
                    'familia': 'cabo BT', 'bus1': p1, 'bus2': p2,
                    'comp_m': comp * 1000.0, 'fases': nph, 'fas_con': fas,
                    'tip_inst': _texto(row.get('TIP_INST')),
                    'tip_cnd': tip_cnd, 'linecode': lc,
                    'uni_tr_mt': _primeiro(row.get('UNI_TR_MT')),
                })
    _diag_set("ssdbt_modelados", contagem_bt)
    _diag_set("ssdbt_skip_fas_n", len(bt_skip_fas_n))

    linhas.append("! --- Ramais de Ligacao (RAMLIG) ---")
    ramais_remapeados = []
    ramais_largos = []
    contagem_rl = 0
    n_real_rl = 0
    n_generico_rl = 0
    if ramlig is not None and len(ramlig) > 0:
        for _, row in ramlig.iterrows():
            cod  = str(row['COD_ID']).strip()
            # CORREÇÃO CRÍTICA: Ramais também precisam do sanitizar_bus_bt!
            p1   = sanitizar_bus_bt(row['PAC_1'])
            p2   = sanitizar_bus_bt(row['PAC_2'])
            fas  = str(row.get('FAS_CON', 'AN')).upper().strip()
            comp = _num(row.get('COMP'), 15) / 1000.0
            cat  = tipo_inst_categoria(row.get('TIP_INST', 'AER'))
            tip_cnd = _norm_cod_bdgd(row.get('TIP_CND'))

            nph, sfx_line, _, _ = fases_info(fas)

            if tip_cnd and tip_cnd.lower() != 'nan':
                lc = f"CND_{tip_cnd}"
                n_real_rl += 1
            else:
                lc = _linecode_ramal(nph, cat)
                n_generico_rl += 1

            # `LineCode` antes de `phases` — o porquê está no trecho de média,
            # acima.
            # O ramal cita os nos que o `FAS_CON` dele declara, e esses nem
            # sempre sao os que o circuito acima tem. Medido: um center-tap com
            # o secundario em `.3.0` e `.0.1` — nos 1 e 3 — recebendo um ramal
            # em `.2.3`. O no 2 nao existe ali: fica pendurado, sem caminho
            # para a terra, e a 27,9 V. A tensao dos nos reais continua certa,
            # mas o `CalcVoltageBases` casa a base da barra pelo conjunto dos
            # nos, e o no morto a puxa para baixo — uma barra de 130 V ficou
            # com base de 73,3 V (=127/raiz(3)) e apareceu a 1,773 pu.
            #
            # A ligacao e POSICIONAL, como ja e nos jumpers: o primeiro
            # condutor do ramal no primeiro no do circuito, o segundo no
            # segundo. Casar por identidade e o que deixa o no orfao.
            #
            # So se remapeia quando o NUMERO de condutores bate: ai e pura
            # renomeacao de no, sem tocar em fases nem no condutor do cadastro.
            # Ramal com mais condutores do que a barra oferece e outro defeito,
            # e sai no registro em vez de ser adivinhado.
            _meus = [int(x) for x in sfx_line.split('.') if x.isdigit() and int(x) != 0]
            _tem = None
            for _extremo in (p1, p2):
                _d = nos_disponiveis.get(str(_extremo).lower())
                if _d:
                    _tem = sorted(_d)
                    break
            if _tem and _meus and not set(_meus) <= set(_tem):
                if len(_tem) >= len(_meus):
                    _novos = _tem[:len(_meus)]
                    sfx_line = '.' + '.'.join(str(n) for n in _novos)
                    ramais_remapeados.append((cod, list(_meus), list(_novos)))
                else:
                    ramais_largos.append((cod, list(_meus), list(_tem)))

            linhas += [
                f"New Line.RL_{cod} bus1={p1}{sfx_line} bus2={p2}{sfx_line}",
                f"~ LineCode={lc} phases={nph} length={comp:.6f} units=km", ""
            ]
            _anotar_nos(p1, sfx_line)
            _anotar_nos(p2, sfx_line)
            contagem_rl += 1
            if cadastro is not None:
                cadastro.append({
                    'elemento': ('rl_%s' % cod).lower(), 'cod_id': cod,
                    'familia': 'ramal de ligação', 'bus1': p1, 'bus2': p2,
                    'comp_m': comp * 1000.0, 'fases': nph, 'fas_con': fas,
                    'tip_inst': _texto(row.get('TIP_INST')),
                    'tip_cnd': tip_cnd, 'linecode': lc,
                    'uni_tr_mt': _primeiro(row.get('UNI_TR_MT')),
                })

    if (nos_por_barra is not None or ligacoes_por_barra is not None
            or nos_por_ligacao is not None):
        # Os nos que ESTE emissor usou em cada barra de baixa, lidos da propria
        # saida. Quem precisa e a religacao do secundario solto: um center-tap
        # emite o secundario em `.1.3` e o circuito abaixo dele pode estar em
        # `.1.2` — a ligacao entre os dois e POSICIONAL, primeira perna com
        # primeiro condutor, e sem esta resposta nao ha como faze-la.
        # E, junto, QUEM ENCOSTA EM QUEM. A religacao precisa saber o que ha
        # rio abaixo do ponto onde vai ligar: um secundario de uma perna sobre
        # um circuito que o cadastro declara com tres condutores energiza um
        # no e deixa os outros dois pendurados sem caminho para a terra. Nao e
        # falha de convergencia — a tensao vai a 3,4 pu e o dia inteiro sai
        # errado sem ninguem reclamar.
        import re as _re
        _rx = _re.compile(r'bus\d?=([^.\s]+)((?:\.\d+)+)', _re.I)
        for _l in linhas:
            if not _l.startswith('New Line.'):
                continue
            _achados = _rx.findall(_l)
            for _bruto, _sufixo in _achados:
                _b = _bruto.lower()
                if not _b.startswith('bt_'):
                    continue
                if nos_por_barra is not None:
                    _atual = nos_por_barra.setdefault(_b, [])
                    for _n in (int(x) for x in _sufixo.split('.')
                               if x and x != '0'):
                        if _n not in _atual:
                            _atual.append(_n)
            if len(_achados) >= 2:
                _b1 = _achados[0][0].lower()
                _b2 = _achados[1][0].lower()
                if ligacoes_por_barra is not None:
                    ligacoes_por_barra.setdefault(_b1, set()).add(_b2)
                    ligacoes_por_barra.setdefault(_b2, set()).add(_b1)
                if nos_por_ligacao is not None:
                    _nos_l = {int(x) for x in _achados[0][1].split('.')
                              if x and x != '0'}
                    nos_por_ligacao.setdefault(frozenset((_b1, _b2)), set()).update(_nos_l)
        for _b in (nos_por_barra or {}):
            nos_por_barra[_b].sort()

    with open(caminho_saida, 'w', encoding='utf-8', newline='\n') as f: f.write('\n'.join(linhas))
    total_bt_rl = contagem_bt + contagem_rl
    pct_real_bt = (n_real_bt + n_real_rl) / total_bt_rl * 100 if total_bt_rl else 0
    print(f"  [OK] {os.path.basename(caminho_saida)} ({contagem_bt} BT, {contagem_rl} ramais)")
    print(f"  [CAB-BT] R/X do SEGCON: {n_real_bt}/{contagem_bt} BT + {n_real_rl}/{contagem_rl} ramais ({pct_real_bt:.1f}%)")
    print(f"  [CAB-BT] Genérico      : {n_generico_bt}/{contagem_bt} BT + {n_generico_rl}/{contagem_rl} ramais ({100-pct_real_bt:.1f}%)")
    if bt_skip_fas_n:
        print(f"  [DIAG-BT] SSDBT descartados por FAS_CON='N': {len(bt_skip_fas_n)}")
        print(f"           amostra COD_ID: {_fmt_amostra(bt_skip_fas_n)}")

    if ramais_remapeados:
        print('  [FIX-RL] %d ramal(is) casados posicionalmente com os nos que a '
              'barra tem' % len(ramais_remapeados))
        _ajustes.registrar(
            'topologia', 'Ramal casado com os nós que a barra de fato tem',
            quantos=len(ramais_remapeados), unidade='ramais',
            efeito='simulacao',
            detalhe='O `FAS_CON` destes ramais nomeia nós que o circuito acima '
                    'não tem — um center-tap com o secundário nos nós 1 e 3 '
                    'recebendo um ramal declarado em 2 e 3, por exemplo.',
            porque='O nó que ninguém alimenta não some: fica pendurado, sem '
                   'caminho para a terra. A tensão dos nós reais continua '
                   'certa, mas o `CalcVoltageBases` casa a base da barra pelo '
                   'conjunto dos nós, e o nó morto a puxa para baixo — medido, '
                   'uma barra de 130 V ficou com base de 73,3 V e apareceu a '
                   '1,773 pu. A ligação passa a ser posicional, como já é nos '
                   'jumpers: o primeiro condutor do ramal no primeiro nó do '
                   'circuito. Só se remapeia quando o número de condutores '
                   'bate, e aí é renomear nó — fases e condutor não mudam.',
            exemplos=['RL %s: %s -> %s' % (c, '.'.join(str(x) for x in a),
                                           '.'.join(str(x) for x in b))
                      for c, a, b in ramais_remapeados[:4]])

    if ramais_largos:
        print('  [DIAG-RL] %d ramal(is) com mais condutores do que a barra '
              'oferece' % len(ramais_largos))
        _ajustes.registrar(
            'nao_corrigido',
            'Ramal com mais condutores do que a barra oferece',
            quantos=len(ramais_largos), unidade='ramais', efeito='nao_corrigido',
            detalhe='O `FAS_CON` destes ramais declara mais condutores do que o '
                    'circuito acima deles tem nós.',
            porque='Casar posicionalmente exigiria escolher quais condutores '
                   'descartar, e o cadastro não diz. Ficam como foram '
                   'publicados, e os nós sem alimentação aparecem no caso.',
            exemplos=['RL %s: pede %s, a barra tem %s'
                      % (c, '.'.join(str(x) for x in a),
                         '.'.join(str(x) for x in b))
                      for c, a, b in ramais_largos[:4]])

def gerar_transformadores(untrmt, eqtrmt, ssdmt, ssdbt, caminho_saida,
                          cadastro=None, nos_secundario=None,
                          kv_secundario=None):
    """Cria Transformadores.dss modelando redes trifásicas e center-tap.

    `nos_secundario`, quando dado, é preenchido com `{barra: [nós]}` do
    secundário de cada transformador — os nós que ESTE emissor de fato
    escreveu. Quem precisa dessa resposta é a religação do secundário solto:
    sem ela, o jumper sai sem lista de nós, o OpenDSS liga 1, 2 e 3, e num
    center-tap — que usa dois — nasce um nó flutuante que faz o passo do
    meio-dia explodir.

    `kv_secundario` sai da mesma leitura, com a tensão de cada secundário. Quem
    a usa é o guarda contra o paralelo de tensões diferentes — ver
    `secundario_incompativel`.

    A resposta é lida da PRÓPRIA SAÍDA, e não recalculada. São cinco pontos de
    emissão diferentes (trifásico, center-tap, MRT com uma perna, MRT com
    duas), e reconstruir a regra em outro lugar é como emissor e réplica
    divergem — foi assim que 103 mil MRT saíram declarados a 254 V.
    """
    # Fallback consolidado de literatura (valores em % no modelo OpenDSS).
    # Só é usado quando o parâmetro não existe no registro real do transformador.
    LIT_TRAFO_PARAMS = {
        3.0:   {'xhl': 2.0, 'r': 1.8, 'noload': 0.30},
        5.0:   {'xhl': 2.2, 'r': 1.8, 'noload': 0.30},
        8.0:   {'xhl': 2.8, 'r': 1.6, 'noload': 0.30},
        10.0:  {'xhl': 3.0, 'r': 1.6, 'noload': 0.30},
        13.0:  {'xhl': 3.1, 'r': 1.5, 'noload': 0.28},
        15.0:  {'xhl': 3.2, 'r': 1.5, 'noload': 0.28},
        16.0:  {'xhl': 3.3, 'r': 1.5, 'noload': 0.28},
        25.0:  {'xhl': 3.8, 'r': 1.4, 'noload': 0.25},
        30.0:  {'xhl': 4.0, 'r': 1.3, 'noload': 0.25},
        37.5:  {'xhl': 4.0, 'r': 1.2, 'noload': 0.22},
        43.0:  {'xhl': 4.2, 'r': 1.2, 'noload': 0.22},
        45.0:  {'xhl': 4.2, 'r': 1.2, 'noload': 0.22},
        50.0:  {'xhl': 4.3, 'r': 1.2, 'noload': 0.22},
        64.0:  {'xhl': 4.4, 'r': 1.1, 'noload': 0.20},
        75.0:  {'xhl': 4.5, 'r': 1.1, 'noload': 0.20},
        112.5:{'xhl': 5.0, 'r': 1.0, 'noload': 0.18},
        150.0:{'xhl': 5.2, 'r': 0.9, 'noload': 0.16},
        225.0:{'xhl': 5.6, 'r': 0.8, 'noload': 0.14},
        300.0:{'xhl': 6.0, 'r': 0.7, 'noload': 0.12},
    }

    def _float_pos(row, *campos):
        """O primeiro dos campos que traga um número positivo utilizável.

        Os catálogos de transformador da BDGD gravam a mesma grandeza sob nomes
        diferentes conforme a base, e vários vêm zerados. Zero aqui não é
        medida, é ausência — e usá-lo como impedância dá divisão por zero ou
        tap que não converge.
        """
        for c in campos:
            v = row.get(c, None)
            if pd.notna(v):
                try:
                    fv = float(v)
                    if fv > 0:
                        return fv
                except Exception:
                    pass
        return None

    def _fallback_literatura(kva):
        """Parâmetros típicos de transformador, pela potência mais próxima.

        Quando o catálogo da distribuidora não traz XHL, perdas ou corrente de
        vazio, o transformador ainda precisa de algum valor para existir no
        caso. Estes vêm da literatura e são declarados no registro de ajustes:
        é premissa, e quem for defender uma tensão calculada com eles tem de
        saber disso.
        """
        if not LIT_TRAFO_PARAMS:
            return {'xhl': 4.0, 'r': 1.2, 'noload': 0.2}
        alvo = float(kva) if pd.notna(kva) else 75.0
        k_ref = min(LIT_TRAFO_PARAMS.keys(), key=lambda k: abs(k - alvo))
        return LIT_TRAFO_PARAMS[k_ref]

    eq_por_cod = {}
    if eqtrmt is not None and not eqtrmt.empty and 'COD_ID' in eqtrmt.columns:
        for _, _er in eqtrmt.iterrows():
            _cod = str(_er.get('COD_ID', '')).strip()
            if _cod and _cod.lower() not in ('nan', 'none'):
                eq_por_cod.setdefault(_cod, _er)

    def _resolver_parametros_trafo(row, kva_nom):
        """Prioriza parâmetro real do próprio trafo; fallback só quando faltar campo.

        Ordem por campo:
          1) UNTRMT do próprio equipamento (registro real da unidade)
          2) EQTRMT por vínculo exato (TIP_UNID -> COD_ID), quando existir
          3) Literatura consolidada por potência nominal
        """
        fallback = _fallback_literatura(kva_nom)
        fontes = []
        tip_unid = str(row.get('TIP_UNID', '')).strip()
        eq_row = eq_por_cod.get(tip_unid) if tip_unid else None

        xhl_real = _float_pos(row, 'XHL')
        if xhl_real is None:
            xhl_eq = _float_pos(eq_row, 'XHL') if eq_row is not None else None
            if xhl_eq is not None:
                xhl_val = xhl_eq
                fontes.append('XHL=eqtrmt')
            else:
                xhl_val = fallback['xhl']
                fontes.append('XHL=lit')
        else:
            xhl_val = xhl_real
            fontes.append('XHL=untrmt')

        per_fer = _float_pos(row, 'PER_FER')
        per_tot = _float_pos(row, 'PER_TOT')
        per_fer_eq = _float_pos(eq_row, 'PER_FER') if eq_row is not None else None
        per_tot_eq = _float_pos(eq_row, 'PER_TOT') if eq_row is not None else None
        noloadloss = None
        if per_fer is not None and kva_nom > 0:
            noloadloss = per_fer / (kva_nom * 10.0)
            fontes.append('NoLoad=untrmt(PER_FER)')
        elif per_fer_eq is not None and kva_nom > 0:
            noloadloss = per_fer_eq / (kva_nom * 10.0)
            fontes.append('NoLoad=eqtrmt(PER_FER)')
        else:
            noloadloss = fallback['noload']
            fontes.append('NoLoad=lit')

        r_real = _float_pos(row, 'R')
        if r_real is not None:
            r_val = r_real
            fontes.append('R=untrmt')
        else:
            r_eq = _float_pos(eq_row, 'R') if eq_row is not None else None
            if r_eq is not None:
                r_val = r_eq
                fontes.append('R=eqtrmt')
                return xhl_val, r_val, noloadloss, ', '.join(fontes)

            p_cobre = None
            if per_tot is not None and per_fer is not None:
                p_cobre = per_tot - per_fer
            elif per_tot_eq is not None and per_fer_eq is not None:
                p_cobre = per_tot_eq - per_fer_eq
            if p_cobre is not None and p_cobre > 0 and kva_nom > 0:
                r_val = p_cobre / (kva_nom * 10.0)
                if per_tot is not None and per_fer is not None:
                    fontes.append('R=untrmt(PER_TOT-PER_FER)')
                else:
                    fontes.append('R=eqtrmt(PER_TOT-PER_FER)')
            else:
                r_val = fallback['r']
                fontes.append('R=lit')

        return xhl_val, r_val, noloadloss, ', '.join(fontes)

    linhas = [
        "! ============================================================",
        "! Transformadores.dss – Transformadores de Distribuicao",
        "! ============================================================",
        "",
    ]
    contagem = 0
    cnt_full_real = 0
    # Quantos secundarios sairam em cada topologia. Sao decisoes de
    # modelagem, e nao leitura direta do cadastro: valem registro.
    cnt_bifasico = cnt_center_tap = cnt_mono = 0
    # Quem tem segundo enrolamento no secundario, pelo cadastro.
    seg_enrol = segundo_enrolamento_por_trafo(eqtrmt)
    cnt_misto = 0
    cnt_fallback = 0
    cnt_r_lit = 0
    cnt_xhl_lit = 0
    cnt_nl_lit = 0
    sem_potencia = []

    def _parse_fas_local(fas_raw):
        """NaN/None/vazio → 'ABC'; remove não-fases, mantém só A/B/C."""
        s = str(fas_raw).strip()
        if s.lower() in ('nan', 'none', ''):
            return 'ABC'
        result = ''.join(c for c in s.upper() if c in ('A', 'B', 'C'))
        return result if result else 'ABC'

    # --- SNIFFER MT: Descobre quais fases chegam por cima ---
    mt_bus_fases = {}
    if ssdmt is not None and not ssdmt.empty:
        for _, row in ssdmt.iterrows():
            for pac in ('PAC_1', 'PAC_2'):
                b = sanitizar_bus(row.get(pac, ''))
                fas = _parse_fas_local(row.get('FAS_CON', 'ABC'))
                if b not in mt_bus_fases: mt_bus_fases[b] = set()
                for f in fas: mt_bus_fases[b].add(f)

    # --- SNIFFER BT: Descobre quais fases o cabo da rua espera por baixo ---
    bt_bus_fases = {}
    if ssdbt is not None and not ssdbt.empty:
        for _, row in ssdbt.iterrows():
            for pac in ('PAC_1', 'PAC_2'):
                b = sanitizar_bus_bt(row.get(pac, ''))
                fas = _parse_fas_local(row.get('FAS_CON', 'ABC'))
                if b not in bt_bus_fases: bt_bus_fases[b] = set()
                for f in fas: bt_bus_fases[b].add(f)

    if untrmt is not None and not untrmt.empty:
        for _, row in untrmt.iterrows():
            cod     = str(row['COD_ID']).strip()
            bus_mt  = sanitizar_bus(row.get('PAC_1', ''))
            bus_bt  = sanitizar_bus_bt(row.get('PAC_2', ''))
            kva, _orig_kva = kva_trafo(row.get('POT_NOM'), TRAFO_KVA_PADRAO)
            if _orig_kva != 'cadastro':
                sem_potencia.append(cod)
            tip     = str(row.get('TIP_TRAFO', 'T')).upper().strip()
            tem_seg = seg_enrol.get(sanitizar_bus(cod))

            # BDGD V11 separa fases em FAS_CON_P (primário) e FAS_CON_S (secundário).
            # Fallback para FAS_CON genérico (versões antigas) ou default ABC/ABCN.
            fas_p = str(row.get('FAS_CON_P', row.get('FAS_CON', 'ABC')) or 'ABC').upper().strip()
            fas_s = str(row.get('FAS_CON_S', row.get('FAS_CON', 'ABCN')) or 'ABCN').upper().strip()

            # TEN_LIN_SE é a tensão nominal do secundário em kV (ex: 0.38 trifásico, 0.44 MRT).
            ten_lin_se_raw = row.get('TEN_LIN_SE', None)
            ten_lin_se = float(ten_lin_se_raw) if pd.notna(ten_lin_se_raw) and float(ten_lin_se_raw or 0) > 0 else TENSAO_BT_KV

            # TIP_TRAFO='MT' na BDGD é monofásico (MRT): entra no modelo como
            # monofásico, e não é pulado.

            fas_mt = fas_p.replace('N', '')
            if not fas_mt: fas_mt = 'ABC'

            # --- Ajuste Primário Baseado na Rede MT ---
            if len(fas_mt) == 1:
                fases_reais_mt = mt_bus_fases.get(bus_mt, set())
                if fas_mt[0] not in fases_reais_mt and len(fases_reais_mt) > 0:
                    fas_mt = list(fases_reais_mt)[0]

            # --- PARÂMETROS ELÉTRICOS DO TRAFO ---
            # Regra: usar parâmetros reais do próprio UNTRMT; fallback literatura só onde faltar campo.
            xhl_val, r_val, noloadloss, origem_param = _resolver_parametros_trafo(row, kva)
            origem_l = origem_param.lower()
            tem_lit = 'lit' in origem_l
            tem_real = ('untrmt' in origem_l) or ('eqtrmt' in origem_l)
            if not tem_lit and tem_real:
                cnt_full_real += 1
            elif tem_lit and tem_real:
                cnt_misto += 1
            else:
                cnt_fallback += 1
            if 'r=lit' in origem_l:
                cnt_r_lit += 1
            if 'xhl=lit' in origem_l:
                cnt_xhl_lit += 1
            if 'noload=lit' in origem_l:
                cnt_nl_lit += 1

            rs_half = r_val / 2.0

            # --- MODELAGEM DE TOPOLOGIA ---
            # O roteamento é feito pelas fases do PRIMÁRIO (fas_p / fas_mt).
            # MRTs têm FAS_CON_P='B' (1 fase) e FAS_CON_S='BN' com TEN_LIN_SE=0.44 kV.
            # O sniffer SSDBT detecta os cabos 'BCN' no secundário → center-tap 3-enrolamentos.

            if len(fas_mt) >= 3:
                # TRIFÁSICO
                linhas += [
                    f"! PARAM_SRC: {origem_param}",
                    f"New Transformer.TR_{cod} phases=3 windings=2",
                    f"~ xhl={xhl_val:.2f}",
                    f"~ %Rs=[{rs_half:.3f} {rs_half:.3f}] %imag=0.5 %noloadloss={noloadloss:.3f}",
                    f"~ wdg=1 bus={bus_mt}.1.2.3 kv={config.TENSAO_MT_KV:.4f} kva={kva:.1f} conn=delta",
                    # `ten_lin_se`, e não a constante: o ramo trifásico era o
                    # único que ignorava o cadastro, e é o mais comum. Num
                    # alimentador com secundários de 220 V isso energizava a
                    # baixa a 380.
                    f"~ wdg=2 bus={bus_bt}.1.2.3.0 kv={ten_lin_se:.4f} kva={kva:.1f} conn=wye", ""
                ]

            elif len(fas_mt) == 2 and not eh_center_tap(tip, ten_lin_se, tem_seg):
                # BIFÁSICO: duas fases no primário E secundário de um
                # enrolamento só. O center-tap sai daqui de propósito — há base
                # em que o primário é bifásico e o secundário é center-tap
                # (149.576 transformadores), e roteá-lo por aqui punha as pernas
                # em `ten/√3` = 147 V onde o certo é `ten/2` = 127. Quem manda
                # no secundário é o secundário.
                p_a = {'A':'1', 'B':'2', 'C':'3'}.get(fas_mt[0], '1')
                p_b = {'A':'1', 'B':'2', 'C':'3'}.get(fas_mt[1], '2')

                # O secundário de baixa precisa de referência de terra, o
                # cadastro declarando o neutro ou não. `FAS_CON_S` bifásico vem
                # sem o `N` em 14.630 trafos da RGE, e ler isso ao pé da letra
                # dava um enrolamento em delta flutuante: medimos 48 V e 222 V
                # nos dois nós do mesmo barramento — números sem significado,
                # postos ali pelo que quer que estivesse pendurado na barra.
                # E 99,6% das 45.539 unidades ligadas a esses trafos são
                # fase-neutro, então é a tensão contra o neutro que importa.
                #
                # Dois enrolamentos de fase-neutro, e não um só entre as fases:
                # com `phases=1` o OpenDSS consome os dois primeiros nós e
                # ignora o `.0`, de modo que um neutro escrito não existiria; e
                # com `phases=2` o primário em delta fica degenerado — dois
                # enrolamentos opostos entre o mesmo par de nós —, o que medimos
                # dar 127 V num nó e 74 V no outro.
                #
                # A concessão está no ângulo: saindo de uma unidade só, as duas
                # pernas ficam a 180° e não a 120°, então entre `p_a` e `p_b` há
                # 254 V em vez de 220. Não incomoda porque quase nada se liga
                # entre elas — as cargas são fase-neutro, e é a fase-neutro que
                # sai certa. É a mesma estrutura do center-tap logo abaixo, com
                # a perna em `ten_lin_se/√3` em vez de metade, porque aqui o
                # catálogo diz que não há center-tap: `TEN_TER` é 0 em 14.629
                # dos 14.630, e `LIG_FAS_S` traz as duas fases.
                cnt_bifasico += 1
                kv_perna = ten_lin_se / math.sqrt(3)

                linhas += [
                    f"! PARAM_SRC: {origem_param}",
                    f"New Transformer.TR_{cod} phases=1 windings=3",
                    f"~ xhl={xhl_val:.2f} xht={xhl_val:.2f} xlt={(xhl_val*0.8):.2f}",
                    f"~ %Rs=[{rs_half:.3f} {r_val:.3f} {r_val:.3f}] %imag=0.5 %noloadloss={noloadloss:.3f}",
                    f"~ wdg=1 bus={bus_mt}.{p_a}.{p_b} kv={config.TENSAO_MT_KV:.4f} kva={kva:.1f} conn=delta",
                    f"~ wdg=2 bus={bus_bt}.{p_a}.0 kv={kv_perna:.4f} kva={(kva/2):.1f} conn=wye",
                    f"~ wdg=3 bus={bus_bt}.0.{p_b} kv={kv_perna:.4f} kva={(kva/2):.1f} conn=wye", ""
                ]

            else:
                # MONOFÁSICO (TIP_TRAFO='MT'/MRT ou monofásico simples)
                p_mt = {'A':'1', 'B':'2', 'C':'3'}.get(fas_mt[0], '1')
                kv_mt_wye = config.TENSAO_MT_KV / math.sqrt(3)

                fases_reais_bt = list(bt_bus_fases.get(bus_bt, set()))

                # Fallback: se o sniffer SSDBT não achou fases, usa FAS_CON_S (strip N).
                if len(fases_reais_bt) < 2:
                    fas_s_clean = fas_s.replace('N', '')
                    if len(fas_s_clean) >= 2:
                        fases_reais_bt = sorted(list(fas_s_clean))

                # Center-tap: dois enrolamentos de baixa em série com o ponto
                # do meio aterrado. `eh_center_tap` é o mesmo juiz que as cargas
                # consultam — se os dois discordarem, a carga sai declarada numa
                # tensão que o transformador não produz, e o caso resolve sem
                # reclamar de nada.
                #
                # O sniffer some com duas fases no secundário é o outro sinal, e
                # entra por fora da função porque depende da rede, não do
                # cadastro do equipamento.
                is_mrt_center_tap = (eh_center_tap(tip, ten_lin_se, tem_seg)
                                     or (tem_seg is None
                                         and len(fases_reais_bt) >= 2))
                if is_mrt_center_tap:
                    cnt_center_tap += 1
                else:
                    cnt_mono += 1

                if is_mrt_center_tap:
                    # MRT Center-Tap (440/220V): wdg=2 em fase1-neutro, wdg=3 em neutro-fase2.
                    # Default da perna 1 = fase MT primaria (fas_mt[0]). Garante que ramais
                    # BDGD downstream com FAS_CON='CN' (fase C -> no 3) aterrissem em no
                    # energizado quando o sniffer SSDBT nao detecta fases BT.
                    fases_reais_bt = sorted(fases_reais_bt)
                    p_mt_node = {'A':'1', 'B':'2', 'C':'3'}.get(fas_mt[0], '1')
                    if fases_reais_bt:
                        p_bt1 = {'A':'1', 'B':'2', 'C':'3'}.get(fases_reais_bt[0], p_mt_node)
                    else:
                        p_bt1 = p_mt_node
                    if len(fases_reais_bt) > 1:
                        p_bt2 = {'A':'1', 'B':'2', 'C':'3'}.get(fases_reais_bt[1], '2')
                    else:
                        p_bt2 = next(n for n in ('1', '2', '3') if n != p_bt1)

                    # A perna é metade do secundário — é o que "center-tap"
                    # quer dizer. Um 440/220 dá 220 e um 230/115 dá 115; o 0,220
                    # fixo que estava aqui era o primeiro caso escrito como se
                    # fosse o único, e punha 220 V na perna de um secundário de
                    # 230.
                    kv_mrt_leg = ten_lin_se / 2.0

                    if p_bt1 == p_bt2:
                        # Sniffer BT só detectou 1 fase → centro-tap degenerado (dois enrolamentos
                        # no mesmo par de nós com polaridade invertida). Degradar para trafo 2-enrolamentos
                        # simples para que os buses BT fiquem energizados corretamente.
                        linhas += [
                            f"! PARAM_SRC: {origem_param}",
                            f"New Transformer.TR_{cod} phases=1 windings=2",
                            f"~ xhl={xhl_val:.2f}",
                            f"~ %Rs=[{rs_half:.3f} {rs_half:.3f}] %imag=0.5 %noloadloss={noloadloss:.3f}",
                            f"~ wdg=1 bus={bus_mt}.{p_mt}.0 kv={kv_mt_wye:.4f} kva={kva:.1f} conn=wye",
                            f"~ wdg=2 bus={bus_bt}.{p_bt1}.0 kv={kv_mrt_leg:.4f} kva={kva:.1f} conn=wye", ""
                        ]
                    else:
                        linhas += [
                            f"! PARAM_SRC: {origem_param}",
                            f"New Transformer.TR_{cod} phases=1 windings=3",
                            f"~ xhl={xhl_val:.2f} xht={xhl_val:.2f} xlt={(xhl_val*0.8):.2f}",
                            f"~ %Rs=[{rs_half:.3f} {r_val:.3f} {r_val:.3f}] %imag=0.5 %noloadloss={noloadloss:.3f}",
                            f"~ wdg=1 bus={bus_mt}.{p_mt}.0 kv={kv_mt_wye:.4f} kva={kva:.1f} conn=wye",
                            f"~ wdg=2 bus={bus_bt}.{p_bt1}.0 kv={kv_mrt_leg:.4f} kva={(kva/2):.1f} conn=wye",
                            f"~ wdg=3 bus={bus_bt}.0.{p_bt2} kv={kv_mrt_leg:.4f} kva={(kva/2):.1f} conn=wye", ""
                        ]
                else:
                    # Monofásico simples (ex: trafo rural 254V ou 220V puros).
                    p_bt1 = {'A':'1', 'B':'2', 'C':'3'}.get(fases_reais_bt[0], '1') if fases_reais_bt else p_mt

                    # Enrolamento ÚNICO ligado entre fase e neutro: a tensão
                    # declarada é a dele, e não se divide por nada.
                    #
                    # `TEN_LIN_SE` é a tensão entre os condutores do secundário.
                    # Num secundário trifásico a quatro fios isso é a tensão de
                    # linha, e a fase sai sobre √3 — é o que o ramo trifásico
                    # faz, e o OpenDSS divide sozinho. Aqui os condutores são a
                    # fase e o neutro, então a tensão declarada JÁ É a
                    # fase-neutro.
                    #
                    # São 80.683 transformadores numa base medida: 0,22 kV
                    # ligados `AN`, sem segundo enrolamento — 220 V contra o
                    # neutro, que é uma fase de uma rede 380/220. Dividir por √3
                    # os punha a 127 V, uma tensão que aquela rede não tem.
                    #
                    # O center-tap é caso à parte, tratado acima: lá são dois
                    # enrolamentos em série e a perna é metade.
                    kv_sec_ln = ten_lin_se

                    linhas += [
                        f"! PARAM_SRC: {origem_param}",
                        f"New Transformer.TR_{cod} phases=1 windings=2",
                        f"~ xhl={xhl_val:.2f}",
                        f"~ %Rs=[{rs_half:.3f} {rs_half:.3f}] %imag=0.5 %noloadloss={noloadloss:.3f}",
                        f"~ wdg=1 bus={bus_mt}.{p_mt}.0 kv={kv_mt_wye:.4f} kva={kva:.1f} conn=wye",
                        f"~ wdg=2 bus={bus_bt}.{p_bt1}.0 kv={kv_sec_ln:.4f} kva={kva:.1f} conn=wye", ""
                    ]

            contagem += 1
            if cadastro is not None:
                cadastro.append({
                    'elemento': ('tr_%s' % cod).lower(),
                    'cod_id': cod,
                    'pot_nom_kva': kva,
                    'fases': len(fas_mt),
                    'tip_trafo': tip,
                    'mrt': str(row.get('MRT', '') or '').strip(),
                    'ten_lin_se_kv': ten_lin_se,
                    'tap': _texto(row.get('TAP')),
                    'fas_con_p': fas_p,
                    'fas_con_s': fas_s,
                    'per_fer_w': _texto(row.get('PER_FER')),
                    'per_tot_w': _texto(row.get('PER_TOT')),
                    'banc': _texto(row.get('BANC')),
                    'mun': _primeiro(row.get('MUN')),
                    'bus_primario': bus_mt,
                    'bus_secundario': bus_bt,
                })

    if nos_secundario is not None:
        # Lê o que acabou de ser escrito: `~ wdg=2 bus=BT_x.1.2.3.0 …` e o
        # `wdg=3` do center-tap. O nó 0 é o neutro e não entra — quem liga um
        # jumper quer as fases.
        import re as _re
        _rx = _re.compile(r'wdg=[23]\s+bus=([^.\s]+)((?:\.\d+)+)', _re.I)
        # A mesma linha traz `kva=` logo depois, e ele nao confunde a
        # busca: o padrao exige o igual colado no `kv`, e `kva=` nao tem.
        _rx_kv = _re.compile(r'kv=([0-9.]+)', _re.I)
        for _l in linhas:
            _m = _rx.search(_l)
            if not _m:
                continue
            _barra = _m.group(1).lower()
            if kv_secundario is not None:
                _mkv = _rx_kv.search(_l)
                if _mkv:
                    # A tensão de PERNA, como o emissor a escreveu. Num
                    # center-tap são duas iguais, e guardar a última não
                    # muda nada; num trifásico é a de fase-neutro.
                    kv_secundario[_barra] = float(_mkv.group(1))
            _nos = [int(x) for x in _m.group(2).split('.') if x and x != '0']
            _atual = nos_secundario.setdefault(_barra, [])
            for _n in _nos:
                if _n not in _atual:
                    _atual.append(_n)
        for _barra in nos_secundario:
            nos_secundario[_barra].sort()

    with open(caminho_saida, 'w', encoding='utf-8', newline='\n') as f: f.write('\n'.join(linhas))
    _ajustes.registrar(
        'potencia', 'Transformador sem potência no cadastro',
        quantos=len(sem_potencia), unidade='transformadores', efeito='simulacao',
        detalhe='`POT_NOM` ausente, zero ou negativo. Entraram com %.0f kVA, '
                'o tamanho mais comum de transformador de poste.' % TRAFO_KVA_PADRAO,
        porque='`kva=0` não é "sem dado" para o OpenDSS: as impedâncias em pu '
               'ficam sem base, o Newton sai em NaN em duas iterações e o '
               'alimentador inteiro perde a solução — medido no SMD01, por '
               'um transformador de 877.',
        exemplos=['TR_%s' % c for c in sem_potencia[:4]])
    _ajustes.registrar(
        'tensao', 'Secundário bifásico modelado como duas pernas fase-neutro',
        quantos=cnt_bifasico, unidade='transformadores', efeito='simulacao',
        detalhe='O cadastro declara duas fases sem neutro (`FAS_CON_S` do tipo '
                'CA/AB/BC) e o catálogo confirma que não há terciário. Lido ao '
                'pé da letra isso dá um enrolamento em delta FLUTUANTE, sem '
                'referência de terra — medimos 48 V num nó e 222 V no outro.',
        porque='99,6% das unidades ligadas a esses transformadores são '
               'fase-neutro, e é a tensão contra o neutro que importa. A '
               'concessão é o ângulo: saindo de uma unidade só, as pernas ficam '
               'a 180° e não a 120°, então entre as fases há 254 V em vez de '
               '220. Quase nada se liga entre elas.')
    _ajustes.registrar(
        'tensao', 'Secundário modelado como center-tap',
        quantos=cnt_center_tap, unidade='transformadores', efeito='simulacao',
        detalhe='Dois enrolamentos de baixa em série com o ponto do meio '
                'aterrado. Cada perna é METADE da tensão de linha declarada — '
                'um 440/220 dá 220 e um 230/115 dá 115.',
        porque='Reconhecido pelo `TIP_TRAFO` e pela tensão, no mesmo lugar que '
               'a carga consulta. Enquanto eram dois testes escritos duas '
               'vezes, discordaram, e as cargas de 103 mil MRT saíam declaradas '
               'a 254 V em vez de 220.')
    print(f"  [OK] {os.path.basename(caminho_saida)} ({contagem} transformadores blindados)")
    print(
        "  [DIAG-TR] Fonte de parâmetros -> "
        f"real:{cnt_full_real} | misto:{cnt_misto} | fallback_literatura:{cnt_fallback}"
    )
    print(
        "  [DIAG-TR] Uso de fallback por campo -> "
        f"R:{cnt_r_lit}/{contagem} | XHL:{cnt_xhl_lit}/{contagem} | NoLoad:{cnt_nl_lit}/{contagem}"
    )

def gerar_capacitores(uncrmt, caminho_saida, cadastro=None, buses_mt=None):
    """Cria Capacitores.dss com bancos de capacitores MT (UNCRMT).

    POT_NOM já decodificado pelo Extrator via tabela TPOTRTV (PRODIST Módulo
    10). Lê preferencialmente POT_NOM_KVAR; se ausente, cai em POT_NOM com
    aviso quando o valor parece ser ainda o código (≤ 28).

    `buses_mt` é o conjunto de barras que a rede de média de fato tem. Um banco
    cujo `PAC_1` não esteja nele fica de fora — ver o comentário no ponto do
    descarte.
    """
    linhas = [
        "! ============================================================",
        "! Capacitores.dss – Bancos de Capacitores MT (UNCRMT)",
        "! POT_NOM já decodificado pelo Extrator via tabela TPOTRTV (PRODIST Mod. 10).",
        "! ============================================================",
        "",
    ]

    contagem = 0
    cnt_rd = cnt_se_off = cnt_se_fixo = cnt_se_cc = cnt_orfao = 0
    # Quantos bancos foram reconhecidos como de subestação pela potência, por o
    # `DESCR` não trazer prefixo. Sai no diagnóstico porque é um julgamento do
    # conversor, e não um dado do cadastro: quem discordar precisa poder ver.
    cnt_se_por_potencia = 0
    capcontrols = []

    if uncrmt is None or len(uncrmt) == 0:
        linhas.append("! Nenhum banco de capacitores encontrado.")
    else:
        for _, row in uncrmt.iterrows():
            cod  = str(row['COD_ID']).strip()
            bus  = sanitizar_bus(row.get('PAC_1', ''))
            fas  = str(row.get('FAS_CON', 'ABC')).upper().strip()
            nph, sfx, _, _ = fases_info(fas)

            kvar_decod = row.get('POT_NOM_KVAR') if 'POT_NOM_KVAR' in uncrmt.columns else None
            if kvar_decod is not None and pd.notna(kvar_decod):
                kvar = float(kvar_decod)
            else:
                kvar = _num(row.get('POT_NOM'), 0)
                if 0 < kvar <= 28:
                    print(f"  [WARN] CAP_{cod} POT_NOM={kvar} parece código TPOTRTV não decodificado — re-extrair com Extrator atualizado.")
                    continue
            if kvar <= 0:
                continue

            # Um banco cujo PAC_1 não é barra desta rede não pertence ao modelo
            # deste alimentador. É o caso típico do banco de subestação, que
            # fica no barramento da SE, a montante da cabeceira: o SSDMT do
            # alimentador não o alcança.
            #
            # Emiti-lo cria uma ilha de uma barra só, e o que acontece ali
            # depende do modo. Ligado, ele injeta reativo em coisa nenhuma e
            # ninguém percebe. Desligado — que é o padrão —, a barra fica sem
            # nenhum caminho para a terra, a matriz de admitância fica singular
            # e o alimentador INTEIRO deixa de convergir. Foi assim que a
            # correção do critério de banco de SE derrubou dois alimentadores
            # que passavam: a mudança certa expôs um elemento que nunca deveria
            # ter sido escrito.
            if buses_mt and bus not in buses_mt:
                cnt_orfao += 1
                continue

            # O prefixo `SE-` em `DESCR` é convenção de uma distribuidora. Nas
            # outras duas testadas ele não existe: numa o `DESCR` é `CAP`
            # seguido de código, na outra está vazio nos 18 registros. Sem outro
            # sinal, os 1.028 bancos de uma delas entravam todos fixos ligados —
            # inclusive 185 acima de 1,8 MVAr, que é exatamente o que a opção
            # `CAP_SE_MODO` existe para não deixar acontecer.
            #
            # Quando o prefixo existe, ele manda: na base que o usa, banco de
            # rede vai de 100 a 1.800 kVAr e banco de subestação de 900 a 7.200,
            # e as faixas se cruzam — nenhum critério de potência os separaria
            # ali sem errar. Faltando o prefixo, a potência decide, com o corte
            # no vão que os dados mostram: 1.800 é o maior banco de rede
            # observado, 2.400 o menor de subestação sem prefixo.
            descr = str(row.get('DESCR', '') or '').strip().upper()
            tem_prefixo = descr.startswith(('SE-', 'SE ', 'RD-', 'RD '))
            if tem_prefixo:
                is_se = descr.startswith(('SE-', 'SE '))
            else:
                is_se = kvar >= CAP_SE_KVAR_MINIMO
            if is_se:
                cnt_se_por_potencia += 0 if tem_prefixo else 1

            if is_se:
                if CAP_SE_MODO == 'off':
                    linhas += [
                        f"! CAP SE desligado por padrao (CAP_SE_MODO='off'). DESCR={descr} POT={kvar:.0f}kVAr",
                        f"New Capacitor.CAP_{cod} bus1={bus}{sfx} phases={nph}",
                        f"~ kv={config.TENSAO_MT_KV:.3f} kvar={kvar:.1f} states=[0]",
                        "",
                    ]
                    cnt_se_off += 1
                elif CAP_SE_MODO == 'capcontrol':
                    linhas += [
                        f"! CAP SE com CapControl (CAP_SE_MODO='capcontrol'). DESCR={descr}",
                        f"New Capacitor.CAP_{cod} bus1={bus}{sfx} phases={nph}",
                        f"~ kv={config.TENSAO_MT_KV:.3f} kvar={kvar:.1f} states=[0]",
                        "",
                    ]
                    capcontrols.append(
                        f"New CapControl.CC_CAP_{cod} element={CAP_SE_CAPCONTROL_ELEMENT} terminal=1 "
                        f"capacitor=CAP_{cod} type=kvar "
                        f"ONsetting={CAP_SE_CAPCONTROL_ON_KVAR:.1f} "
                        f"OFFsetting={CAP_SE_CAPCONTROL_OFF_KVAR:.1f} "
                        f"delay=30 delayoff=30"
                    )
                    cnt_se_cc += 1
                else:  # 'fixo' (comportamento legado)
                    linhas += [
                        f"! CAP SE fixo ligado (CAP_SE_MODO='fixo'). DESCR={descr}",
                        f"New Capacitor.CAP_{cod} bus1={bus}{sfx} phases={nph}",
                        f"~ kv={config.TENSAO_MT_KV:.3f} kvar={kvar:.1f}",
                        "",
                    ]
                    cnt_se_fixo += 1
            else:
                # Banco de rede (RD-*): fixo, sempre ligado (modelagem padrao).
                linhas += [
                    f"New Capacitor.CAP_{cod} bus1={bus}{sfx} phases={nph}",
                    f"~ kv={config.TENSAO_MT_KV:.3f} kvar={kvar:.1f}",
                    "",
                ]
                cnt_rd += 1
            contagem += 1
            if cadastro is not None:
                cadastro.append({
                    'elemento': ('cap_%s' % cod).lower(), 'cod_id': cod,
                    'familia': 'capacitor', 'bus1': bus,
                    'fases': nph, 'fas_con': fas,
                    'pot_kvar': kvar,
                    'tip_unid': _texto(row.get('TIP_UNID')),
                    'banc': _texto(row.get('BANC')),
                    'mun': _primeiro(row.get('MUN')),
                })

    if capcontrols:
        linhas += ["! --- CapControls para bancos de SE ---"] + capcontrols + [""]

    with open(caminho_saida, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(linhas))
    print(
        f"  [OK] {os.path.basename(caminho_saida)} ({contagem} capacitores | "
        f"RD:{cnt_rd} SE-off:{cnt_se_off} SE-fixo:{cnt_se_fixo} SE-cc:{cnt_se_cc} "
        f"| modo_SE='{CAP_SE_MODO}')"
    )
    if cnt_orfao:
        print('  [AVISO] %d banco(s) descartado(s): PAC_1 nao e barra desta '
              'rede de media (tipicamente banco de subestacao, a montante da '
              'cabeceira).' % cnt_orfao)
        _ajustes.registrar(
            'topologia', 'Bancos de capacitor fora da rede foram descartados',
            quantos=cnt_orfao, unidade='bancos', efeito='simulacao',
            detalhe='O `PAC_1` destes bancos não é barra da rede de média deste '
                    'alimentador — tipicamente o banco da subestação, a '
                    'montante da cabeceira.',
            porque='Escrito, ele cria uma ilha de uma barra só. Ligado, injeta '
                   'reativo em coisa nenhuma; desligado, que é o padrão, a '
                   'barra fica sem caminho para a terra e o alimentador INTEIRO '
                   'deixa de convergir.')
    if cnt_se_por_potencia:
        print('  [AVISO] %d banco(s) reconhecido(s) como de subestacao pela '
              'potencia (>= %.0f kVAr): o DESCR desta base nao traz prefixo '
              'SE-/RD-.' % (cnt_se_por_potencia, CAP_SE_KVAR_MINIMO))
        _ajustes.registrar(
            'classe', 'Banco de capacitor reconhecido como de subestação '
                      'pela potência',
            quantos=cnt_se_por_potencia, unidade='bancos', efeito='simulacao',
            detalhe='O `DESCR` desta base não traz o prefixo SE-/RD- que distingue '
                    'banco de rede de banco de subestação. Decidiu a potência, '
                    'com corte em %.0f kVAr.' % CAP_SE_KVAR_MINIMO,
            porque='Banco de subestação é manobrado por automatismo. Tratado como '
                   'de rede, entra fixo LIGADO e sobrecompensa a madrugada '
                   'inteira — um banco de 7,2 MVAr fixo num alimentador em '
                   'vazio.')

#: Quanto duas tensões de secundário podem diferir e ainda serem "a mesma".
#: Dois transformadores de 380 V não saem idênticos do cadastro; 160 V de
#: diferença, que é o que 220 V contra 380 V dá, não é arredondamento.
TOLERANCIA_TENSAO_SECUNDARIO = 0.02


def secundarios_na_ilha(barra, nos_secundario, ligacoes_bt):
    """Os secundários de transformador presentes neste circuito de baixa.

    Anda o mesmo grafo que `_nos_sem_alimentacao` percorre: as ligações que o
    emissor de linhas BT registrou, que é o circuito que o OpenDSS vai
    resolver. Inclui a própria `barra`, se ela for um secundário.

    Barras chegam em minúsculas, como o emissor de linhas BT as registrou.
    """
    secundarios = nos_secundario or {}
    achados = [barra] if barra in secundarios else []
    if not ligacoes_bt:
        return achados
    vistos = {barra}
    fila = [barra]
    while fila:
        b = fila.pop()
        for v in ligacoes_bt.get(b, ()):
            if v in vistos:
                continue
            vistos.add(v)
            fila.append(v)
            if v in secundarios:
                achados.append(v)
    return sorted(achados)


def secundario_incompativel(barra_solta, alvo, nos_secundario, ligacoes_bt,
                            kv_secundario):
    """O secundário do circuito-alvo com tensão diferente da do solto, ou None.

    Guarda contra o paralelo que **queima**, e não contra todo paralelo. A
    diferença importa:

    - dois secundários de 380 V ligados pela baixa dividem carga. Não é o
      arranjo do cadastro, mas não produz corrente de circulação apreciável, e
      recusar o jumper aí deixaria o transformador solto sem circuito — que é
      justamente o defeito que o jumper existe para curar. Medido: 47 dos 131
      jumpers já emitidos são desse tipo, e recusá-los todos seria trocar um
      problema por outro maior;
    - um secundário de 380 V ligado ao circuito de um center-tap de 220 V por
      perna é outra coisa. Medido num alimentador de distribuidora: os 160 V de
      diferença empurraram **589 A por um cabo de 125 A**, 1361% de
      carregamento.

    O estrago não parava na corrente, e é o que torna este guarda difícil de
    dispensar. A corrente circulante distorce as tensões **na compilação**, que
    é quando o `CalcVoltageBases` roda — e ele atribuiu base errada a dezesseis
    barras por causa disso. O caso reprovava em `verificar` com 0,581 a 1,494
    pu: nenhum dos dois números descreve defeito da rede. Recusando os jumpers,
    o mesmo alimentador fecha em 0,938 a 1,086 pu.

    Sem tensão conhecida de um dos lados, não se recusa: um guarda que age no
    escuro erra mais do que acerta, e o paralelo de mesma tensão não faz mal
    que justifique o palpite.
    """
    kvs = kv_secundario or {}
    kv_solto = kvs.get(barra_solta)
    if not kv_solto:
        return None
    for outro in secundarios_na_ilha(alvo, nos_secundario, ligacoes_bt):
        kv_outro = kvs.get(outro)
        if not kv_outro:
            continue
        if abs(kv_outro - kv_solto) > TOLERANCIA_TENSAO_SECUNDARIO * kv_solto:
            return outro
    return None


def agrupar_por_proximidade(mapa, tolerancia_m):
    """Junta os pontos que o arredondamento separou, mas o mapa nao separa.

    `mapa` e `{(x, y): {pacs}}`, com a coordenada ja arredondada a cinco casas
    (~1,1 m). Arredondar agrupa, mas nao mede: dois pontos a um metro um do
    outro caem em baldes VIZINHOS sempre que a fronteira do arredondamento
    passa entre eles, e ai nunca se encontram.

    Medido num alimentador da Copel de 11.800 cargas: a rede vinha partida em
    dois lobos, e o par mais proximo entre eles estava a **1,0 metro** — mesmo
    poste, desenhado com imprecisao de um metro, com as latitudes caindo em
    -22,73440 e -22,73439. Um passo do arredondamento separava 8.183 cargas da
    subestacao.

    Isto so importa onde a topologia depende da geometria. Numa base que
    encadeia `PAC_1`/`PAC_2` — a Celesc, por exemplo — os PACs coincidentes ja
    estao unidos, e esta funcao nao acrescenta ligacao nenhuma: medido em tres
    alimentadores, zero emendas novas. Na base que nao encadeia, leva o mesmo
    alimentador de 417 componentes para dois.

    Devolve um mapa novo, com um ponto por grupo.
    """
    if not mapa or tolerancia_m <= 0:
        return mapa

    chaves = list(mapa)
    lat_media = sum(k[1] for k in chaves) / len(chaves)
    k_lon = math.cos(math.radians(lat_media)) * 111320.0
    k_lat = 110540.0
    celula = tolerancia_m / 111320.0

    pai = {}

    def acha(c):
        """A raiz do grupo a que este ponto pertence, achatando o caminho."""
        pai.setdefault(c, c)
        while pai[c] != c:
            pai[c] = pai[pai[c]]
            c = pai[c]
        return c

    grade = {}
    for c in chaves:
        grade.setdefault((int(c[0] / celula), int(c[1] / celula)), []).append(c)

    for (gx, gy), aqui in grade.items():
        vizinhos = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                vizinhos.extend(grade.get((gx + dx, gy + dy), ()))
        for a in aqui:
            for b in vizinhos:
                if a is b or acha(a) == acha(b):
                    continue
                if math.hypot((b[0] - a[0]) * k_lon,
                              (b[1] - a[1]) * k_lat) <= tolerancia_m:
                    pai[acha(a)] = acha(b)

    juntos = {}
    for c in chaves:
        juntos.setdefault(acha(c), set()).update(mapa[c])
    return juntos


def ganho_da_costura(ssdmt, unsemt=None, unremt=None, bus_fonte=None,
                     tolerancia_m=TOLERANCIA_COSTURA_COORDENADA_M):
    """Quanto da media tensao a costura por coordenada recuperaria.

    Devolve `(antes, depois)`: a fracao da media que chega a fonte seguindo so
    os PACs, e a mesma fracao contando tambem as emendas que a costura criaria.

    O que decide ligar a costura e a DIFERENCA, e nao o nivel. O alimentador de
    referencia do pacote tem 0,70 da media encadeada por PAC e converte bem
    assim — os 30% restantes sao natureza do dado dele, e a costura nao os
    recupera. Ja o alimentador que saia partido em dois lobos vai de 0,08 para
    0,998. Medido em seis alimentadores de tres distribuidoras: o ganho e zero
    em todos os que fecham e quase um no que nao fecha, sem nada no meio.

    Olhar o nivel, e nao o ganho, ligaria a costura no alimentador de
    referencia — que nao precisa dela — e mudaria um caso que hoje esta certo.
    """
    from bdgdcase.modelo.topologia import fracao_mt_ligada

    antes = fracao_mt_ligada(ssdmt, unsemt, unremt, bus_fonte)
    if ssdmt is None or getattr(ssdmt, 'empty', True):
        return antes, antes
    if 'geometry' not in ssdmt.columns:
        return antes, antes
    # As mesmas guardas de `fracao_mt_ligada`, e pela mesma razao: isto roda em
    # toda conversao, e faltar coluna aqui nao pode custar o caso inteiro.
    if 'PAC_1' not in ssdmt.columns or 'PAC_2' not in ssdmt.columns:
        return antes, antes

    mapa = {}
    for geom, p1, p2 in zip(ssdmt.geometry,
                            ssdmt['PAC_1'].astype(str).str.strip(),
                            ssdmt['PAC_2'].astype(str).str.strip()):
        pontas = _geom_line_endpoints_xy(geom) if geom is not None else None
        if not pontas:
            continue
        (x1, y1), (x2, y2) = pontas
        mapa.setdefault((round(x1, 5), round(y1, 5)), set()).add(p1)
        mapa.setdefault((round(x2, 5), round(y2, 5)), set()).add(p2)

    emendas = []
    for pacs in agrupar_por_proximidade(mapa, tolerancia_m).values():
        juntos = sorted(pacs)
        for outro in juntos[1:]:
            emendas.append((juntos[0], outro))
    if not emendas:
        return antes, antes

    tudo = pd.concat(
        [d[['PAC_1', 'PAC_2']].astype(str)
         for d in (ssdmt, unsemt, unremt) if d is not None
         and not getattr(d, 'empty', True)
         and 'PAC_1' in d.columns and 'PAC_2' in d.columns]
        + [pd.DataFrame(emendas, columns=['PAC_1', 'PAC_2'])],
        ignore_index=True)
    return antes, fracao_mt_ligada(tudo, bus_fonte=bus_fonte)


def gerar_jumpers_topologicos(
    ssdmt,
    ssdbt,
    unsemt,
    unremt,
    untrmt,
    caminho_saida,
    incluir_mt=True,
    incluir_bt=True,
    nos_secundario=None,
    kv_secundario=None,
    nos_bt_por_barra=None,
    ligacoes_bt=None,
    arquivos_emitidos=None,
):
    """Identifica PACs na mesma coordenada e cria jumpers, IGNORANDO PACs já unidos por equipamentos.

    ``arquivos_emitidos``: os `.dss` já escritos (linhas de média, chaves e
    reguladores; transformadores; linhas de baixa). Com eles, **um jumper só
    liga o que o circuito emitido ainda não liga**. A costura existe para
    unir ilhas; entre dois pontos já ligados ela é, no melhor caso, um laço
    redundante e, no pior, um curto sobre o equipamento que está entre eles.
    Medido no UVA01: 513 jumpers de média fechavam 511 laços, dois deles
    contornando reguladores por um terceiro PAC do mesmo poste — a guarda de
    pares proibidos só via o par direto.
    """
    linhas = [
        "! ============================================================",
        "! Jumpers.dss – as ligacoes que o cadastro nao fecha e a conversao fecha por coordenada",
        "! ============================================================",
        "",
    ]

    # 1. CRIAR A "LISTA NEGRA" DE EQUIPAMENTOS
    # Se dois PACs já estão ligados por uma chave ou regulador, não podemos criar jumper!
    pares_proibidos = set()

    def registrar_proibidos(df, is_bt=False):
        """Marca os pares de PAC que NÃO podem virar o mesmo nó.

        Duas pontas de um mesmo trecho caem no mesmo lugar do mapa quando o
        trecho tem comprimento zero no cadastro. Fundi-las curto-circuitaria o
        trecho. Este registro é o que impede a costura por coordenada de fazer
        isso.
        """
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                p1 = sanitizar_bus_bt(row.get('PAC_1', '')) if is_bt else sanitizar_bus(row.get('PAC_1', ''))
                p2 = sanitizar_bus_bt(row.get('PAC_2', '')) if is_bt else sanitizar_bus(row.get('PAC_2', ''))
                if is_bt and (not _bus_bt_valido(p1) or not _bus_bt_valido(p2)):
                    continue
                if (not is_bt) and (not _bus_mt_valido(p1) or not _bus_mt_valido(p2)):
                    continue
                if p1 and p2:
                    pares_proibidos.add(f"{p1}_{p2}")
                    pares_proibidos.add(f"{p2}_{p1}")

    registrar_proibidos(unsemt)
    registrar_proibidos(unremt)
    registrar_proibidos(untrmt)

    # O circuito como foi escrito, para saber o que JA esta ligado. Sem os
    # arquivos (testes unitarios), a uniao fica vazia e a costura faz o que
    # sempre fez.
    # Duas unioes, porque a pergunta e diferente em cada nivel. Na media,
    # "ja ligado" so vale pelo circuito de MEDIA (trechos, chaves fechadas,
    # reguladores): duas regioes de media unidas so por transformador ->
    # baixa -> transformador nao estao ligadas — a baixa compartilhada entre
    # transformadores vizinhos e comum no cadastro, e por ela o UVA01 deu
    # 5.771 barras de media "ligadas" que nenhum trecho alcancava. Na baixa
    # vale o circuito inteiro: dois secundarios ja unidos pela media nao
    # devem ganhar um jumper que os ponha em paralelo.
    _so_mt = [c for c in (arquivos_emitidos or ()) if 'linhas_mt' in os.path.basename(str(c)).lower()]
    uniao_mt = uniao_dos_emitidos(_so_mt)
    uniao_bt = uniao_dos_emitidos(arquivos_emitidos)
    redundantes = {'MT': 0, 'BT': 0}

    def _ja_ligados(nivel, a, b, nos):
        """Os dois PACs já estão ligados em TODOS os nós que o jumper uniria?

        Se sim, o jumper não sai. Basta um nó sem caminho para ele sair: é
        o caso do trecho cadastrado com uma fase no meio de um tronco
        trifásico, que a costura por coordenada corrige há muito tempo.
        """
        if arquivos_emitidos is None:
            return False
        u = uniao_mt if nivel == 'MT' else uniao_bt
        if all(u.ligadas('%s.%d' % (a.lower(), k), '%s.%d' % (b.lower(), k)) for k in nos):
            redundantes[nivel] += 1
            return True
        return False

    def _unir(a, b, nos):
        """Passa a contar o jumper recém-emitido como ligação, nos dois níveis."""
        for k in nos:
            for u in (uniao_mt, uniao_bt):
                u.unir('%s.%d' % (a.lower(), k), '%s.%d' % (b.lower(), k))

    def _nos_do_jumper(b1):
        """Os nós que um `bus1=...` de jumper leva (sem lista: 1, 2 e 3)."""
        resto = b1.partition('.')[2]
        nos = [int(x) for x in resto.split('.') if x.isdigit() and int(x) != 0]
        return nos or [1, 2, 3]

    # 2. MAPEAR COORDENADAS DOS CABOS
    mapa_coords_mt = {}
    mapa_coords_bt = {}

    def registrar_pacs(df, mapa, is_bt=False):
        """Agrupa os PACs que caem na mesma coordenada.

        É o insumo da costura topológica: pontas que o cadastro deixou com
        códigos diferentes, mas que estão no mesmo poste, são candidatas a ser
        o mesmo nó. Candidatas — quem decide é `processar_mapa`, que respeita
        os pares proibidos.
        """
        if df is None or 'geometry' not in df.columns: return
        for _, row in df.iterrows():
            geom = row.get('geometry')
            pac1 = sanitizar_bus_bt(row.get('PAC_1', '')) if is_bt else sanitizar_bus(row.get('PAC_1', ''))
            pac2 = sanitizar_bus_bt(row.get('PAC_2', '')) if is_bt else sanitizar_bus(row.get('PAC_2', ''))

            if geom and not geom.is_empty and getattr(geom, 'geom_type', '') in ('LineString', 'MultiLineString'):
                ends = _geom_line_endpoints_xy(geom)
                if not ends:
                    continue
                (x1, y1), (x2, y2) = ends
                c1 = (round(x1, 5), round(y1, 5))
                c2 = (round(x2, 5), round(y2, 5))

                if is_bt:
                    if not _bus_bt_valido(pac1):
                        pac1 = ''
                    if not _bus_bt_valido(pac2):
                        pac2 = ''
                else:
                    if not _bus_mt_valido(pac1):
                        pac1 = ''
                    if not _bus_mt_valido(pac2):
                        pac2 = ''

                if pac1 and pac1.lower() not in ('nan', 'none', ''):
                    mapa.setdefault(c1, set()).add(pac1)
                if pac2 and pac2.lower() not in ('nan', 'none', ''):
                    mapa.setdefault(c2, set()).add(pac2)

    def _nos_sem_alimentacao(barra, alimentados):
        """Nós que ficariam pendurados se a energia entrasse só por estes.

        Anda o circuito de baixa a partir de `barra`, pelas ligações que o
        emissor de linhas BT registrou, e junta os nós que os condutores
        declaram. O que estiver fora de `alimentados` não tem de onde vir.

        A resposta vem do grafo escrito em disco, e não de uma leitura do
        cadastro: é o mesmo circuito que o OpenDSS vai resolver.
        """
        # `barra` chega em minúsculas, como o emissor de linhas BT a
        # registrou. Passar a caixa original faz a busca não achar chave
        # nenhuma e este guarda responder "nada solto" sempre — aconteceu, e
        # os 7 jumpers do 2_CVO_3 saíram como se nada tivesse sido conferido.
        if not ligacoes_bt:
            return set()
        vistos = {barra}
        fila = [barra]
        while fila:
            b = fila.pop()
            for v in ligacoes_bt.get(b, ()):
                if v not in vistos:
                    vistos.add(v)
                    fila.append(v)
        usados = set()
        for b in vistos:
            usados |= set((nos_bt_por_barra or {}).get(b, ()))
        return usados - set(alimentados)

    def religar_secundarios(df, mapa):
        """Liga o secundário solto ao nó de baixa do próprio poste.

        Só entram os secundários que nenhum condutor de baixa toca. A ligação é
        por proximidade e não por coincidência exata: medido, quando o cadastro
        separa o transformador do condutor que sai dele, separa por 1,6 a 2,0 m
        — precisão de desenho, não distância real.

        **O jumper leva a lista de nós que o emissor do transformador usou.**
        Sem ela o OpenDSS liga 1, 2 e 3, e num center-tap — que usa dois — nasce
        um nó flutuante sem caminho para a terra: o passo do meio-dia deixava de
        convergir e a tensão ia a 9,2e95 pu. Os nós vêm de `nos_secundario`,
        preenchido pelo próprio `gerar_transformadores` a partir da saída dele,
        e não de uma segunda leitura da regra de center-tap.

        Devolve `[(cod, bus, distância_m, nós)]` do que foi religado.
        """
        if df is None or 'geometry' not in df.columns or not mapa:
            return []
        import math as _math
        pontos = list(mapa.keys())
        ja_ligados = set()
        for _pacs in mapa.values():
            ja_ligados |= _pacs
        feitos = []
        for _, row in df.iterrows():
            geom = row.get('geometry')
            if geom is None or geom.is_empty:
                continue
            if getattr(geom, 'geom_type', '') != 'Point':
                continue
            pac2 = sanitizar_bus_bt(row.get('PAC_2', ''))
            if not _bus_bt_valido(pac2) or pac2.lower() in ('nan', 'none', ''):
                continue
            if pac2 in ja_ligados:
                continue                     # já está ligado; nada a fazer
            nos = list((nos_secundario or {}).get(pac2.lower(), []))
            if not nos:
                # Sem saber os nós, não se emite: um jumper de três fases num
                # secundário de dois cria o flutuante que derruba o dia.
                sem_nos.append((str(row.get('COD_ID', '')).strip(), pac2))
                continue
            x, y = geom.x, geom.y
            melhor, dist = None, TOLERANCIA_RELIGACAO_TRAFO_M
            for c in pontos:
                d = _math.hypot(
                    (c[0] - x) * 111320.0 * _math.cos(_math.radians(y)),
                    (c[1] - y) * 110540.0)
                if d <= dist:
                    melhor, dist = c, d
            if melhor is None:
                continue
            alvo = sorted(mapa[melhor])[0]
            # O poste mais proximo pode ser o de OUTRO transformador. Ligar ali
            # nao religa: poe dois transformadores em paralelo pelo secundario,
            # e eles nem precisam ter a mesma tensao. Medido: um center-tap de
            # 127 V por perna recebendo o secundario de 220 V de um trifasico
            # ao lado — a tensao ia a 1,704 pu e ninguem reclamava.
            if alvo.lower() in (nos_secundario or {}):
                paralelos.append((str(row.get('COD_ID', '')).strip(),
                                  pac2, alvo))
                continue
            # E o poste pode ser comum e mesmo assim pertencer ao circuito de
            # outro transformador, um salto adiante. Aqui so se recusa quando
            # as tensoes diferem, que e o caso que queima — ver
            # `secundario_incompativel`.
            conflito = secundario_incompativel(
                pac2.lower(), alvo.lower(), nos_secundario, ligacoes_bt,
                kv_secundario)
            if conflito is not None:
                incompativeis.append((str(row.get('COD_ID', '')).strip(),
                                      pac2, conflito))
                continue
            # Os nos do OUTRO lado, como o emissor de linhas BT os escreveu.
            # A ligacao e POSICIONAL: a primeira perna do secundario com o
            # primeiro condutor do circuito, a segunda com o segundo. Um
            # center-tap emitido em `.1.3` sobre um circuito em `.1.2` casa
            # 1-1 e 3-2 — casar por identidade deixaria o no 2 sem alimentacao,
            # flutuando, e o passo do meio-dia explodia por causa disso.
            nos_alvo = list((nos_bt_por_barra or {}).get(alvo.lower(), []))
            if not nos_alvo:
                sem_nos.append((str(row.get('COD_ID', '')).strip(), pac2))
                continue
            n = min(len(nos), len(nos_alvo))
            # E o circuito la embaixo, ele cabe no que este transformador
            # entrega? Um secundario de uma perna sobre um ramal que o
            # cadastro declara com tres condutores energiza um no e deixa os
            # outros pendurados, sem caminho para a terra. Medido: e assim que
            # `bt_bt265891` vai a 3,42 pu, e assim que quatro passos do
            # 881160007 deixam de convergir. Nao aparece como erro — aparece
            # como um dia inteiro de tensao errada.
            alimentados = set(nos_alvo[:n])
            soltos = _nos_sem_alimentacao(alvo.lower(), alimentados)
            if soltos:
                pendurados.append((str(row.get('COD_ID', '')).strip(), pac2,
                                   sorted(soltos)))
                continue
            de = '.'.join(str(x) for x in nos[:n])
            para = '.'.join(str(x) for x in nos_alvo[:n])
            cod_j = ('%s_%s' % (pac2, alvo)).replace('-', '_').replace('.', '_')
            linhas.append('New Line.JUMP_TR_%s bus1=%s.%s bus2=%s.%s phases=%d'
                          % (cod_j, pac2, de, alvo, para, n))
            linhas.append('~ switch=y enabled=true')
            linhas.append('')
            feitos.append((str(row.get('COD_ID', '')).strip(), pac2,
                           round(dist, 1), list(nos[:n])))
        return feitos

    registrar_pacs(ssdmt, mapa_coords_mt, is_bt=False)
    registrar_pacs(ssdbt, mapa_coords_bt, is_bt=True)

    # Na baixa, so entra na costura barra que o caso TEM: a que uma linha de
    # baixa toca ou um transformador cria. A SSDBT traz trechos que o emissor
    # descarta (`FAS_CON=N`, placeholders), e o PAC deles chegava aqui com
    # coordenada e tudo — o jumper nascia para uma barra que nada mais cria.
    # Medido no UVA01: `JUMP_BT_BT_772865_BT_774910`, com a barra fantasma
    # recebendo base de MEDIA (19,9 kV) e 166 V lidos como 0,008 pu.
    _barras_bt = set(nos_bt_por_barra or ()) | set(nos_secundario or ())
    if _barras_bt:
        for _c in list(mapa_coords_bt):
            mapa_coords_bt[_c] = {p for p in mapa_coords_bt[_c] if p.lower() in _barras_bt}
            if len(mapa_coords_bt[_c]) < 2:
                del mapa_coords_bt[_c]

    sem_nos = []
    pendurados = []
    paralelos = []
    incompativeis = []
    religados = religar_secundarios(untrmt, mapa_coords_bt)

    # 3. CRIAR JUMPERS COM SEGURANÇA
    contagem = 0
    def _com_nos(nivel, a, b):
        """`(bus1, bus2)` do jumper, com a lista de nós na baixa.

        Sem lista o OpenDSS liga 1, 2 e 3. Numa baixa em que as duas barras
        só têm o nó 3, isso cria dois nós que nada energiza — e, pior, nós
        que uma das barras nem tinha. Ficam os nós que as DUAS barras têm;
        se não há nenhum em comum, não há o que ligar, e o jumper não sai.
        Na média a rede é trifásica e a lista fica como estava.
        """
        if nivel != 'BT' or not nos_bt_por_barra:
            return a, b
        na = set((nos_bt_por_barra or {}).get(a.lower(), ()))
        nb = set((nos_bt_por_barra or {}).get(b.lower(), ()))
        if not na or not nb:
            return a, b
        comum = sorted(na & nb)
        if not comum:
            return None, None
        sfx = '.' + '.'.join(str(n) for n in comum)
        return a + sfx, b + sfx

    def processar_mapa(mapa, nivel):
        """Costura os PACs coincidentes, e conta quantos jumpers isso deu.

        Cada grupo de PACs no mesmo ponto vira um jumper para o primeiro deles.
        Jumper é remendo declarado, não rede: vai para o registro de ajustes, e
        o interruptor `config.HABILITAR_JUMPERS_TOPOLOGICOS` desliga todos de
        uma vez. Quem quiser ver o alimentador como a BDGD o descreve, com
        ilhas e tudo, desliga e olha.
        """
        nonlocal contagem
        for coord, pacs in mapa.items():
            if len(pacs) > 1:
                lista_pacs = list(pacs)
                pac_base = lista_pacs[0]

                for pac_sec in lista_pacs[1:]:
                    if f"{pac_base}_{pac_sec}" in pares_proibidos:
                        # O par direto é proibido (ligaria dois secundários em
                        # paralelo): tenta o ramal órfão em outro nó do mesmo poste.
                        for outro_pac in lista_pacs:
                            if outro_pac != pac_sec and f"{outro_pac}_{pac_sec}" not in pares_proibidos:
                                b1, b2 = _com_nos(nivel, outro_pac, pac_sec)
                                if b1 is None:
                                    continue
                                if _ja_ligados(nivel, outro_pac, pac_sec, _nos_do_jumper(b1)):
                                    continue
                                _unir(outro_pac, pac_sec, _nos_do_jumper(b1))
                                cod_jumper = f"{outro_pac}_{pac_sec}".replace('-', '_').replace('.', '_')
                                linhas.append(f"New Line.JUMP_{nivel}_{cod_jumper} bus1={b1} bus2={b2}")
                                linhas.append(f"~ switch=y enabled=true")
                                linhas.append("")
                                contagem += 1
                                pares_proibidos.add(f"{outro_pac}_{pac_sec}")
                                pares_proibidos.add(f"{pac_sec}_{outro_pac}")
                                break
                    else:
                        # Ligação Segura
                        b1, b2 = _com_nos(nivel, pac_base, pac_sec)
                        if b1 is None:
                            continue
                        if _ja_ligados(nivel, pac_base, pac_sec, _nos_do_jumper(b1)):
                            continue
                        _unir(pac_base, pac_sec, _nos_do_jumper(b1))
                        cod_jumper = f"{pac_base}_{pac_sec}".replace('-', '_').replace('.', '_')
                        linhas.append(f"New Line.JUMP_{nivel}_{cod_jumper} bus1={b1} bus2={b2}")
                        linhas.append(f"~ switch=y enabled=true")
                        linhas.append("")
                        contagem += 1
                        pares_proibidos.add(f"{pac_base}_{pac_sec}")
                        pares_proibidos.add(f"{pac_sec}_{pac_base}")

    # A costura mede distancia, e nao compara coordenada arredondada: ver
    # `agrupar_por_proximidade`, e o alimentador que um metro partia em dois.
    if incluir_mt:
        processar_mapa(agrupar_por_proximidade(
            mapa_coords_mt, TOLERANCIA_COSTURA_COORDENADA_M), "MT")
    if incluir_bt:
        processar_mapa(agrupar_por_proximidade(
            mapa_coords_bt, TOLERANCIA_COSTURA_COORDENADA_M), "BT")

    if redundantes['MT'] or redundantes['BT']:
        print('  [DIAG-JUMP] costura dispensada entre pontos ja ligados: MT %d | BT %d'
              % (redundantes['MT'], redundantes['BT']))

    if religados:
        _ajustes.registrar(
            'topologia', 'Secundário de transformador religado ao seu circuito',
            quantos=len(religados), unidade='transformadores',
            efeito='simulacao',
            detalhe='Estes tinham o secundário sem nenhum condutor de baixa '
                    'ligado. Cada um foi unido ao nó de baixa do próprio '
                    'poste, a no máximo %.0f m — o mais distante ficou a '
                    '%.1f m —, pelos mesmos nós que o transformador usa.'
                    % (TOLERANCIA_RELIGACAO_TRAFO_M,
                       max(d for _c, _b, d, _n in religados)),
            porque='O `PAC_2` do transformador não é ponta de condutor nenhum, '
                   'então nunca entrava na costura por coordenada — e o '
                   'cadastro ainda o separa do condutor que sai dele por uns '
                   'dois metros, que é precisão de desenho e não distância. '
                   'Sem religar, o transformador não alcança as unidades que a '
                   'BDGD diz que ele alimenta: elas ficam sem tensão o dia '
                   'inteiro, e o alimentador aparece com um pedaço morto que '
                   'não existe na rua.',
            exemplos=['TR %s → %s (%.1f m, nós %s)'
                      % (c, b, d, '.'.join(str(x) for x in n))
                      for c, b, d, n in religados[:4]])

    if sem_nos:
        _ajustes.registrar(
            'nao_corrigido', 'Secundário solto que não pôde ser religado',
            quantos=len(sem_nos), unidade='transformadores',
            efeito='nao_corrigido',
            detalhe='Têm o secundário sem condutor de baixa ligado, como os '
                    'que foram religados, mas o emissor não registrou os nós '
                    'que usou neles — então não há como emitir o jumper.',
            porque='Um jumper sem lista de nós faz o OpenDSS ligar as três '
                   'fases. Num secundário que usa duas, a terceira fica '
                   'flutuando sem caminho para a terra, e o passo de maior '
                   'geração deixa de convergir. Ficam como o cadastro os '
                   'publicou, e ditos aqui.',
            exemplos=['TR %s → %s' % (c, b) for c, b in sem_nos[:4]])

    if pendurados:
        _ajustes.registrar(
            'nao_corrigido',
            'Secundário solto sobre circuito de mais fases que o transformador',
            quantos=len(pendurados), unidade='transformadores',
            efeito='nao_corrigido',
            detalhe='Estes também têm o secundário sem condutor de baixa '
                    'ligado, mas o circuito do poste declara condutores que '
                    'este transformador não alimenta — religar energizaria '
                    'uns e deixaria os outros pendurados.',
            porque='Um nó energizado ao lado de outro sem caminho para a '
                   'terra não dá erro: dá número. Medido, a tensão vai a '
                   '3,4 pu e o dia inteiro sai errado sem ninguém reclamar; '
                   'em outro alimentador quatro passos deixaram de convergir. '
                   'A inconsistência está no cadastro — um transformador de '
                   'uma perna com ramal de três condutores — e não há na base '
                   'o dado que diga qual dos dois está certo. Ficam como '
                   'foram publicados, e ditos aqui.',
            exemplos=['TR %s → %s (nós sem alimentação: %s)'
                      % (c, b, ', '.join(str(x) for x in ns))
                      for c, b, ns in pendurados[:4]])

    if paralelos:
        _ajustes.registrar(
            'nao_corrigido',
            'Secundário solto cujo poste mais próximo é de outro transformador',
            quantos=len(paralelos), unidade='transformadores',
            efeito='nao_corrigido',
            detalhe='O ponto de baixa mais próximo destes não é o circuito '
                    'deles: é o secundário de outro transformador, no mesmo '
                    'poste ou a poucos metros.',
            porque='Ligar ali não religa o transformador ao circuito dele — '
                   'põe dois transformadores em paralelo pelo secundário, e '
                   'eles nem precisam ter a mesma tensão. Medido, um '
                   'center-tap de 127 V por perna recebendo o secundário de '
                   '220 V do trifásico ao lado leva a rede a 1,7 pu sem dar '
                   'erro nenhum. Ficam como o cadastro os publicou.',
            exemplos=['TR %s (%s): o poste mais próximo é %s, secundário '
                      'de outro transformador' % (c, b, a)
                      for c, b, a in paralelos[:4]])

    if incompativeis:
        _ajustes.registrar(
            'nao_corrigido',
            'Secundário solto cujo poste mais próximo já é de outra tensão',
            quantos=len(incompativeis), unidade='transformadores',
            efeito='nao_corrigido',
            detalhe='O poste mais próximo destes é um poste comum, mas o '
                    'circuito de baixa a que ele pertence já é alimentado '
                    'por outro transformador, de tensão de secundário '
                    'diferente.',
            porque='O jumper poria os dois em paralelo pela baixa, e a '
                   'diferença de tensão vira corrente de circulação. '
                   'Medido, 380 V contra 220 V por perna empurrou 589 A por '
                   'um cabo de 125 A — 1361% de carregamento — e distorceu '
                   'as tensões na compilação a ponto de o CalcVoltageBases '
                   'atribuir base errada a dezesseis barras. O caso '
                   'reprovava em `verificar` por números que não descreviam '
                   'defeito nenhum da rede. Ficam como o cadastro os '
                   'publicou.',
            exemplos=['TR %s (%s): o circuito do poste mais próximo já é o '
                      'de %s, com outra tensão' % (c, b, a)
                      for c, b, a in incompativeis[:4]])

    with open(caminho_saida, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(linhas))
    religados_n = len(religados)
    print(f"  [OK] {os.path.basename(caminho_saida)} ({contagem} jumpers "
          f"protegidos contra curto entre equipamentos + {religados_n} "
          f"secundarios de transformador religados)")
    # Quem chama precisa saber: um `Redirect` para arquivo vazio e ruido no
    # Master, e muda o caso byte a byte sem mudar nada eletrico.
    #
    # A religacao ENTRA na conta, e essa e a razao de a soma existir. Ela
    # estava de fora: o arquivo saia com os jumpers de religacao escritos,
    # `contagem` continuava zero, e o Master comentava o `Redirect` — o
    # conserto era escrito em disco e nunca carregado. Era o defeito de
    # sempre aqui: emissor e Master discordando sem ninguem reclamar.
    return contagem + religados_n
