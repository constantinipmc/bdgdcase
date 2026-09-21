# -*- coding: utf-8 -*-
"""Quanto cada carga consome, a cada 15 minutos.

O OpenDSS quer duas coisas de cada carga: um valor de potência e uma curva
`LoadShape` de 96 pontos que a module ao longo do dia. A BDGD não entrega
nenhuma das duas prontas.

**A curva** sai de `CRVCRG`, o catálogo de curvas típicas da distribuidora,
casado com o `TIP_CC` da unidade consumidora. Nem toda base publica esse
catálogo; quando falta, entra `dados/curvas_referencia.csv` — 61 códigos por
três tipos de dia, medidos numa base que os publica. Curva de referência é
declarada como tal no registro de ajustes: é premissa emprestada, não dado.

**O valor** sai da energia, não da potência instalada. `calcular_demanda_kw`
parte de `ENE_01..12`: potência instalada é o que caberia no ramal, energia é o
que de fato passou por ele. O fator de carga da própria curva fecha a conta,
para que a integral da curva devolva a energia de onde ela saiu.

A iluminação pública tem curva à parte (`_curva_ip_solar`), derivada do nascer
e do pôr do sol no centróide do alimentador: é a única carga do caso cujo
horário não vem de hábito humano, e sim de efeméride.
"""
from __future__ import annotations

import math
import os
import pandas as pd

from bdgdcase import ajustes as _ajustes
from bdgdcase.caminhos import DADOS_DIR
from bdgdcase.curva_pv import gerar_curva_pv_beta
from bdgdcase.modelo import config
from bdgdcase.modelo.registro import _diag_inc
from bdgdcase.modelo.config import (
    APLICAR_CORRECAO_CRVCRG_K, CORRECAO_CRVCRG_K_JANELAS, DATA_SIMULACAO,
    DEFAULT_TIP_CC_CURVA, FC_MACROCOPICO, LATITUDE, LONGITUDE,
    POT_COLS_CRVCRG, PV_BETA_ALPHA, PV_BETA_BETA, PV_BETA_SEED,
    PV_DERATING, PV_HORA_FIM, PV_HORA_INICIO, PV_MC_DIAS,
    PV_SIMULTANEIDADE, PV_SMOOTH_WINDOW, TIMEZONE_HORAS,)
from bdgdcase.modelo.cadastro import (
    _tip_cc_para_prefixo_carga, parse_lista, sanitizar_cod_loadshape,)


# Curva (flat) de cada fonte não-solar. A energia certa vem do escalonamento
# kw = POT_INST × FC, não da curva — ver bdgdcase.gd_tipos.
_CURVA_POR_TIPO_GD = {
    'CGH':  'Curva_CGH',    # hidro a fio d'água: base
    'EOL':  'Curva_EOL',    # eólica: base (aproximação; sem série de vento)
    'UTE':  'Curva_UTE',    # térmica/biomassa: base
    'NSOL': 'Curva_NSOL',   # não-solar de fonte desconhecida (detectada pelo FC)
}

def _expandir_curva_24_para_96(arr24):
    """Repete cada patamar horário 4× (1 h → quatro intervalos de 15 min)."""
    out = []
    for v in arr24:
        out.extend([float(v)] * 4)
    return out

#: Onde procurar a tipologia de cada unidade do poste, na ordem de preferência.
#: Os dois primeiros são a tipologia de curva propriamente dita; os demais são
#: o enquadramento do consumidor, que a serve por aproximação.
FONTES_TIPOLOGIA = ('LISTA_TIP_CC', 'TIP_CC', 'LISTA_CLAS_SUB',
                    'LISTA_GRU_TAR', 'LISTA_CLAS_TAR')

def _lista_tip_cc_linha(row):
    """Tipologia de cada UC do poste, do melhor campo que a linha oferecer.

    A base pode simplesmente não preencher `TIP_CC` — a da Coopera o deixa em
    branco (um espaço, não vazio) nas 29 mil unidades. Sem os degraus seguintes
    toda carga dela cai no padrão residencial, calada: o caso converge, o mapa
    abre, e o alimentador inteiro fica com a curva errada.

    `CLAS_SUB` e `GRU_TAR` não são tipologia de curva, e sim classe de consumo
    e grupo tarifário; servem por aproximação, e é por isso que vêm depois.
    """
    for campo in FONTES_TIPOLOGIA:
        lt = parse_lista(row.get(campo, ''), str)
        if lt:
            if campo not in ('LISTA_TIP_CC', 'TIP_CC'):
                _diag_inc('tipologia_por_%s' % campo.replace('LISTA_', '').lower())
            return lt
    return []

def _curva_ip_solar(h_nasc, h_por, npts=96):
    """Curva de Iluminacao Publica derivada do nascer/por-do-sol.

    IP=1.0 entre o por e o nascer; rampa linear de 30 min nas transicoes.
    Substitui o vetor 24h hardcoded da curva GEN_IP (que assumia 06h-18h
    fixos) — ajusta automaticamente quando muda LATITUDE/LONGITUDE/DATA.
    """
    out = []
    rampa = 0.5  # h
    for k in range(npts):
        t = k * (24.0 / npts)
        if t < h_nasc - rampa or t > h_por + rampa:
            out.append(1.0)
        elif t < h_nasc:
            out.append(max(0.0, (h_nasc - t) / rampa))
        elif t > h_por:
            out.append(min(1.0, (t - h_por) / rampa))
        else:
            out.append(0.0)
    return out

def _vetor_k_96(janelas):
    """Expande janelas (h_ini, h_fim, K) para vetor 96 pts (15 min)."""
    out = [1.0] * 96
    for h_ini, h_fim, k in janelas:
        i = max(0, int(h_ini * 4))
        j = min(96, int(h_fim * 4))
        for x in range(i, j):
            out[x] = k
    return out

def _aplicar_correcao_k(pu, classe_pref):
    """Aplica K_c(t) preservando a média (energia diária) da curva original."""
    if not APLICAR_CORRECAO_CRVCRG_K:
        return pu
    janelas = CORRECAO_CRVCRG_K_JANELAS.get(classe_pref)
    if not janelas:
        return pu
    K = _vetor_k_96(janelas)
    out = [p * k for p, k in zip(pu, K)]
    media_orig = sum(pu) / len(pu) if pu else 0.0
    media_new = sum(out) / len(out) if out else 0.0
    if media_new > 0 and media_orig > 0:
        ratio = media_orig / media_new
        out = [v * ratio for v in out]
    return out

def _centroide_alimentador(ssdmt, ssdbt, ponnot):
    """Retorna (lat, lon) em graus (EPSG:4674) do centroide do alimentador.

    Prioriza PONNOT (postes, geometria Point) > SSDMT (rede MT) > SSDBT
    (rede BT). Fallback: constantes globais LATITUDE/LONGITUDE.
    """
    for fonte in (ponnot, ssdmt, ssdbt):
        if fonte is None or len(fonte) == 0:
            continue
        try:
            geom = fonte.geometry.dropna()
            if geom.empty:
                continue
            cx = float(geom.centroid.x.mean())
            cy = float(geom.centroid.y.mean())
            if math.isfinite(cx) and math.isfinite(cy):
                return cy, cx
        except Exception:
            continue
    return LATITUDE, LONGITUDE

#: Onde o catálogo de referência mora dentro do pacote.
CURVAS_REFERENCIA = str(DADOS_DIR / 'curvas_referencia.csv')

def carregar_curvas_de_referencia(caminho=None):
    """Catálogo de curvas embarcado no pacote, ou `(None, '')` se não houver.

    Existe porque nem toda BDGD traz as suas. Numa das distribuidoras testadas
    a camada `CRVCRG` vem com os 101 campos e zero feições, e sem isto o
    alimentador inteiro sai com quatro curvas desenhadas à mão — 24 patamares
    esticados para 96, que ninguém mediu.

    Devolve também a linha de procedência lida do cabeçalho do arquivo, porque
    quem recebe a pasta pronta não tem outro jeito de saber de onde as curvas
    do caso dele vieram.
    """
    caminho = caminho or CURVAS_REFERENCIA
    if not os.path.exists(caminho):
        return None, ''
    try:
        origem = ''
        with open(caminho, encoding='utf-8') as f:
            for linha in f:
                if not linha.startswith('#'):
                    break
                if 'Origem:' in linha:
                    origem = linha.split('Origem:', 1)[1].strip()
        df = pd.read_csv(caminho, comment='#')
    except Exception as exc:                       # pragma: no cover
        print('  [AVISO] catálogo de curvas de referência ilegível (%s).' % exc)
        return None, ''
    if df.empty or 'COD_ID' not in df.columns:     # pragma: no cover
        return None, ''
    return df, origem

def gerar_curvas_de_carga_dss(crv_df, caminho_saida, tip_dia=config.TIP_DIA_CURVA_CRVCRG,
                              latitude=None, longitude=None):
    """Gera CurvasDeCarga.dss a partir de CRVCRG_*.csv (PU por patamar + Curva_PV 96 pts).

    Retorna dict com fatores de carga médios por nome de Loadshape e o default
    sanitizado.
    """
    lat = LATITUDE if latitude is None else latitude
    lon = LONGITUDE if longitude is None else longitude
    origem_pos = "centroide alimentador" if (latitude is not None or longitude is not None) else "fallback global"

    if APLICAR_CORRECAO_CRVCRG_K:
        print("  [INFO] Correcao horaria CRVCRG K_c(t) ATIVA (calibracao do usuario).")
    else:
        print("  [INFO] Correcao CRVCRG K_c(t) DESATIVADA — usando curvas BDGD originais.")

    # Janela solar derivada de (lat, lon, DATA_SIMULACAO).
    # Usada tanto pela Curva_PV (Beta) quanto pela curva GEN_IP genérica.
    try:
        from bdgdcase.solar import nascer_por_do_sol
        from datetime import date as _date
        h_nasc_solar, h_por_solar = nascer_por_do_sol(
            lat, lon,
            _date.fromisoformat(DATA_SIMULACAO),
            TIMEZONE_HORAS,
        )
        print(f"  [SOLAR] Nascer={h_nasc_solar:.2f}h Por={h_por_solar:.2f}h "
              f"(lat={lat:.4f} lon={lon:.4f} data={DATA_SIMULACAO} origem={origem_pos})")
    except Exception as exc:
        print(f"  [AVISO] Falha no cálculo solar ({exc}); usando a janela fixa {PV_HORA_INICIO}-{PV_HORA_FIM}h.")
        h_nasc_solar = PV_HORA_INICIO
        h_por_solar = PV_HORA_FIM

    # O modo é decidido antes do cabeçalho porque o cabeçalho tem de dizer qual
    # é. Ele anunciava "Catálogo CRVCRG (BDGD)" mesmo quando as curvas eram as
    # genéricas desenhadas à mão, e quem abrisse o arquivo não teria como
    # desconfiar.
    origem_ref = ''
    if crv_df is None or crv_df.empty:
        crv_ref, origem_ref = carregar_curvas_de_referencia()
        if crv_ref is not None and not crv_ref.empty:
            modo, crv_df = 'referencia', crv_ref
        else:
            modo = 'generico'
    else:
        modo = 'catalogo'

    procedencia = {
        # O texto do modo `catalogo` é o histórico, palavra por palavra: ele já
        # dizia a verdade, e mudá-lo mexeria em todos os casos já publicados
        # para não corrigir nada. A mentira estava nos outros dois, que
        # anunciavam este mesmo cabeçalho sem terem lido CRVCRG nenhuma.
        'catalogo': 'Catálogo CRVCRG (BDGD)',
        'referencia': 'catálogo de referência do bdgdcase (%s)' % (origem_ref or 'origem não registrada'),
        'generico': 'curvas genéricas do bdgdcase (não medidas)',
    }[modo]

    linhas = [
        "! ============================================================",
        "! CurvasDeCarga.dss – %s + PV" % procedencia,
    ]
    if modo != 'catalogo':
        linhas += [
            "!",
            "! ATENÇÃO: esta base NÃO trouxe curvas de carga (camada CRVCRG",
            "! ausente ou vazia). As curvas abaixo NÃO são desta distribuidora.",
            "! Procedência: %s" % procedencia,
            "! A forma do dia vem daí; a energia de cada carga continua vindo do",
            "! cadastro desta base. Trate os resultados horários como estimativa.",
            "!",
        ]
    linhas += [
        "! Perfil PU: cada patamar / max(patamares); minterval=15 min, npts=96",
        f"! Janela solar: nascer={h_nasc_solar:.2f}h por={h_por_solar:.2f}h",
        "! ============================================================",
        "",
    ]
    fatores = {}
    default_san = sanitizar_cod_loadshape(DEFAULT_TIP_CC_CURVA)

    if modo == 'generico':
        print("  [AVISO] CRVCRG ausente — usando curvas genéricas 96 pts (legado 24 h expandido).")
        gen_specs = [
            ('GEN_RES', _FATOR_CARGA_CURVA['RES'], _expandir_curva_24_para_96(
                [0.4, 0.35, 0.35, 0.35, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.6, 0.6, 0.55, 0.55, 0.55, 0.6, 0.7, 0.9, 1.0, 0.95, 0.85, 0.7, 0.6, 0.5])),
            ('GEN_COM', _FATOR_CARGA_CURVA['COM'], _expandir_curva_24_para_96(
                [0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.3, 0.5, 0.8, 0.9, 1.0, 1.0, 0.9, 0.9, 1.0, 1.0, 0.9, 0.7, 0.5, 0.4, 0.3, 0.2, 0.2, 0.2])),
            ('GEN_IND', _FATOR_CARGA_CURVA['IND'], _expandir_curva_24_para_96(
                [0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.6, 0.8, 0.9, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.8, 0.6, 0.5, 0.4, 0.4, 0.4, 0.4])),
            # IP derivada do nascer/por-do-sol (ajusta sozinha quando muda lat/lon/data)
            ('GEN_IP', _FATOR_CARGA_CURVA['IP'], _curva_ip_solar(h_nasc_solar, h_por_solar)),
        ]
        for nome, fc_mean, pu in gen_specs:
            vmax = max(pu) if max(pu) > 0 else 1.0
            pu_n = [p / vmax for p in pu]
            # Correcao horaria opcional: K_c(t) por classe (do sufixo do nome).
            classe_pref = nome.split('_')[-1]  # GEN_RES -> RES
            pu_n = _aplicar_correcao_k(pu_n, classe_pref)
            fatores[nome] = float(sum(pu_n) / len(pu_n))
            s = ' '.join(f'{p:.6f}' for p in pu_n)
            linhas.append(f"New Loadshape.{nome} npts=96 minterval=15 mult=[{s}]")
        default_san = 'GEN_RES'
        print('  [AVISO] Sem CRVCRG e sem catálogo de referência: as curvas '
              'são genéricas e NÃO foram medidas.')
        _ajustes.registrar(
            'curvas', 'Curvas de carga genéricas, não medidas',
            quantos=len(fatores), unidade='curvas', efeito='simulacao',
            detalhe='Sem CRVCRG na base e sem o catálogo de referência do '
                    'pacote. As quatro curvas usadas foram desenhadas à mão, '
                    'com 24 patamares esticados para 96.',
            porque='É o último recurso, e o único caso em que a forma do dia não '
                   'vem de medição nenhuma. Reinstale o pacote completo para '
                   'ter o catálogo de referência.')
    else:
        curvas_pu, nome_bruto = {}, {}
        df = crv_df.copy()
        if 'TIP_DIA' in df.columns:
            df['_td'] = df['TIP_DIA'].astype(str).str.upper().str.strip()
            sub = df[df['_td'] == str(tip_dia).upper()]
            if sub.empty:
                sub = df
        else:
            sub = df
        for cod_raw, grp in sub.groupby(sub['COD_ID'].astype(str).str.strip()):
            row = grp.iloc[0]
            vals = []
            for col in POT_COLS_CRVCRG:
                v = row.get(col, 0)
                vals.append(float(v) if pd.notna(v) else 0.0)
            vmax = max(vals) if max(vals) > 0 else 1.0
            pu = [v / vmax for v in vals]
            # Correcao horaria opcional: K_c(t) por classe do COD_ID.
            pu = _aplicar_correcao_k(pu, _tip_cc_para_prefixo_carga(cod_raw))
            san = sanitizar_cod_loadshape(cod_raw)
            fatores[san] = float(sum(pu) / len(pu))
            curvas_pu[san] = pu
            nome_bruto[san] = cod_raw
            s = ' '.join(f'{p:.6f}' for p in pu)
            linhas.append(f"New Loadshape.{san} npts=96 minterval=15 mult=[{s}]")
        if modo == 'referencia':
            # As curvas de classe, agregadas do próprio catálogo. Sem elas o
            # catálogo de referência seria inútil justamente para quem precisa
            # dele: a base sem CRVCRG costuma ser também a que não preenche o
            # `TIP_CC`, e nenhuma carga casaria com código nenhum — todas
            # cairiam numa curva só.
            por_classe = {}
            for san, pu in curvas_pu.items():
                por_classe.setdefault(
                    _tip_cc_para_prefixo_carga(nome_bruto[san]), []).append(pu)
            for classe in ('RES', 'COM', 'IND', 'IP'):
                membros = por_classe.get(classe)
                if not membros:
                    continue
                media = [sum(c[i] for c in membros) / len(membros)
                         for i in range(96)]
                vmax = max(media) or 1.0
                media = [v / vmax for v in media]
                nome = 'GEN_%s' % classe
                fatores[nome] = float(sum(media) / len(media))
                linhas.append('New Loadshape.%s npts=96 minterval=15 mult=[%s]'
                              % (nome, ' '.join('%.6f' % p for p in media)))
            _ajustes.registrar(
                'curvas', 'Curvas de carga vieram do catálogo embarcado',
                quantos=len(fatores), unidade='curvas',
                efeito='simulacao',
                detalhe='A camada CRVCRG desta base veio vazia. As curvas são o '
                        'catálogo de referência do pacote. Procedência: %s'
                        % procedencia,
                porque='A FORMA do dia passa a ser a de outra distribuidora; a '
                       'ENERGIA de cada carga continua vindo do cadastro desta. '
                       'Um estudo de perfil horário sobre este caso é '
                       'estimativa, e deve ser lido como tal.')
            print('  [AVISO] CRVCRG ausente ou vazia nesta base — usando o '
                  'catálogo de referência do bdgdcase.')
            print('          Procedência: %s' % procedencia)
            print('          A forma do dia NÃO é desta distribuidora; a '
                  'energia de cada carga continua vindo do cadastro dela.')

        if default_san not in fatores:
            pref = [k for k in sorted(fatores.keys()) if k.startswith('RES_')]
            default_san = pref[0] if pref else next(iter(fatores.keys()))

    # Curva PV (96 pontos): módulo externo com lógica Beta + envelope de céu claro.
    # Fallback para semi-seno clássico se houver erro, para não quebrar o fluxo.
    try:
        pv96 = gerar_curva_pv_beta(
            npts=96,
            alpha=PV_BETA_ALPHA,
            beta_param=PV_BETA_BETA,
            hora_inicio=h_nasc_solar,
            hora_fim=h_por_solar,
            smooth_window=PV_SMOOTH_WINDOW,
            seed=PV_BETA_SEED,
            n_dias_mc=PV_MC_DIAS,
        )
        pv96 = [v * PV_DERATING * PV_SIMULTANEIDADE for v in pv96]
        print(
            "  [OK] Curva_PV Beta (Monte Carlo) gerada "
            f"(dias={PV_MC_DIAS}, alpha={PV_BETA_ALPHA}, beta={PV_BETA_BETA}, seed={PV_BETA_SEED}, "
            f"derating={PV_DERATING}, simult={PV_SIMULTANEIDADE})"
        )
    except Exception as exc:
        print(f"  [AVISO] Falha ao gerar Curva_PV Beta ({exc}); usando semi-seno legado.")
        pv96 = []
        for step in range(96):
            hora_dec = step * 0.25  # hora em formato decimal
            if hora_dec < 6.0 or hora_dec >= 18.0:
                pv96.append(0.0)
            else:
                frac = (hora_dec - 6.0) / 12.0  # 0..1
                pv96.append(round(math.sin(frac * math.pi), 6))
    pv_str = ' '.join(f'{p:.6f}' for p in pv96)
    linhas.extend(['', f"New Loadshape.Curva_PV npts=96 minterval=15 mult=[{pv_str}]", ''])

    # Curvas para GDs nao-solares (CGH/PCH/UHE, EOL, UTE/biomassa e NSOL — fonte
    # desconhecida detectada pelo fator de capacidade). Sao flat 1.0: essas fontes
    # operam como base load no horizonte diario de 24 h. A ENERGIA correta nao vem
    # da curva e sim do escalonamento kw = POT_INST x FC feito em gerar_gd()
    # (usar POT_INST com curva flat faria a unidade gerar 100% da nominal 24 h/dia).
    # Refinar com perfil real (hidrologico/vento) quando o dado existir.
    for _nome_gd in ('Curva_CGH', 'Curva_EOL', 'Curva_UTE', 'Curva_NSOL'):
        _mult_flat = ' '.join(['1.000000'] * 96)
        linhas.append(f"New Loadshape.{_nome_gd} npts=96 minterval=15 mult=[{_mult_flat}]")
    linhas.append('')

    with open(caminho_saida, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(linhas))

    print(f"  [OK] {os.path.basename(caminho_saida)} — modo={modo}, default Loadshape={default_san}, {len(fatores)} curvas de carga")
    return {'fatores': fatores, 'default_san': default_san, 'modo': modo,
            'procedencia': procedencia}

def _resolver_loadshape_e_fator(tip_cc_txt, ctx_curvas):
    """Retorna (nome_loadshape_sanitizado, fator_carga_medio PU) para daily=."""
    fatores = ctx_curvas['fatores']
    default_san = ctx_curvas['default_san']
    modo = ctx_curvas.get('modo', 'catalogo')
    raw = str(tip_cc_txt).strip() if tip_cc_txt is not None else ''
    if raw in ('', 'nan', 'None', '0'):
        san = default_san
        return san, fatores.get(san, _FATOR_CARGA_CURVA['RES'])
    san = sanitizar_cod_loadshape(raw)
    if san in fatores:
        return san, fatores[san]
    # Evita fallback residencial para Iluminação Pública quando TIP_CC não casar
    # com o catálogo CRVCRG: prioriza curva IP dedicada (GEN_IP ou IP_*).
    pref = _tip_cc_para_prefixo_carga(raw)
    if pref == 'IP':
        if 'GEN_IP' in fatores:
            return 'GEN_IP', fatores.get('GEN_IP', _FATOR_CARGA_CURVA['IP'])
        ip_keys = [k for k in sorted(fatores.keys()) if k.startswith('IP_')]
        if ip_keys:
            ip_san = ip_keys[0]
            return ip_san, fatores.get(ip_san, _FATOR_CARGA_CURVA['IP'])
    if modo in ('generico', 'referencia'):
        # No modo de referência a carga chega quase sempre sem `TIP_CC` que case
        # — é o mesmo tipo de base que não traz curva —, então a rota por classe
        # é a rota principal, e não a exceção.
        gen = {'RES': 'GEN_RES', 'COM': 'GEN_COM', 'IND': 'GEN_IND', 'IP': 'GEN_IP'}.get(pref, 'GEN_RES')
        if gen in fatores:
            return gen, fatores[gen]
    return default_san, fatores.get(default_san, _FATOR_CARGA_CURVA['RES'])

# Fator de carga (mean) de curvas legadas 24 h — usado só se CRVCRG ausente / fallback.
# Com CRVCRG, o fator vem da média PU da curva oficial (96 patamares).
# kW_pico = ENE_media / (730h × fator_carga) × FC_MACROCOPICO
_FATOR_CARGA_CURVA = {
    'RES': sum([0.4, 0.35, 0.35, 0.35, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.6, 0.6, 0.55, 0.55, 0.55, 0.6, 0.7, 0.9, 1.0, 0.95, 0.85, 0.7, 0.6, 0.5]) / 24,
    'COM': sum([0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.3, 0.5, 0.8, 0.9, 1.0, 1.0, 0.9, 0.9, 1.0, 1.0, 0.9, 0.7, 0.5, 0.4, 0.3, 0.2, 0.2, 0.2]) / 24,
    'IND': sum([0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.6, 0.8, 0.9, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.8, 0.6, 0.5, 0.4, 0.4, 0.4, 0.4]) / 24,
    'IP':  sum([1.0, 1.0, 1.0, 1.0, 1.0, 0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.2, 0.8, 1.0, 1.0, 1.0, 1.0, 1.0]) / 24,
}

#: Teto de carga instalada, em kW, para uma unidade de BAIXA tensão. Acima
#: disto o `CAR_INST` não é kW: é watt registrado como kW, ou lixo.
#:
#: A régua é folgada de propósito. O PRODIST liga em baixa quem tem até 75 kW
#: de demanda; a carga INSTALADA de uma unidade assim pode passar disso, e
#: 300 kW ainda cabe numa oficina ou num prédio ligado em baixa. Mil, não.
#:
#: Medido no IAL02 (Celesc): a mediana do `CAR_INST` é 10 kW, e 113 unidades
#: trazem centenas ou milhares — 2.900, 9.500 — que são os watts de uma casa.
#: Enquanto a unidade tem energia faturada, o número não importa: a demanda
#: sai da energia. Uma delas não tinha, e o recuo `CAR_INST × 0,2` fez dela
#: uma carga residencial monofásica de 1.634 kW num transformador de 75 kVA:
#: 1.389 A numa fase, 0,18 pu no fim da rua, e o caso recusado por tensão
#: fora do plausível — com a investigação apontando para fase solta.
CAR_INST_BT_MAX_KW = 300.0


def car_inst_bt_kw(valor, teto=CAR_INST_BT_MAX_KW):
    """`CAR_INST` de uma unidade de baixa, em kW de verdade: `(kw, origem)`.

    `origem` é `'kw'` (veio como estava), `'watts'` (estava mil vezes maior
    do que uma unidade de baixa comporta, e dividido por mil cai numa carga
    normal) ou `'implausivel'` (nem assim; vale zero — a unidade fica sem
    carga, e é contada, em vez de derrubar a fase inteira).
    """
    try:
        v = float(valor or 0.0)
    except (TypeError, ValueError):
        return 0.0, 'implausivel'
    if not (v > 0):
        return 0.0, 'kw'
    if v <= teto:
        return v, 'kw'
    if v / 1000.0 <= teto:
        return v / 1000.0, 'watts'
    return 0.0, 'implausivel'


def calcular_demanda_kw(row, idx_uc=None, fator_demanda=0.20, tipo='RES',
                          fator_carga_curva=None, aplicar_fc_macro=True,
                          teto_car_inst_kw=None, avisos=None):
    """Calcula o kW de PICO da UC a partir do histórico de faturação (12 meses).

    Fórmula:
        kW_pico = ENE_media_mensal / (730h × fator_carga_curva) × FC_MACROCOPICO

    fator_carga_curva: média da curva PU oficial (CRVCRG) ou, se None, o fator
    legado por classe.

    Se não houver histórico ENE, usa fallback: CAR_INST × fator_demanda ×
    FC_MACROCOPICO. Com ``teto_car_inst_kw`` (unidade de baixa), o `CAR_INST`
    passa antes por :func:`car_inst_bt_kw`; ``avisos`` é um dict onde se
    conta o que foi reinterpretado (`'car_inst_watts'`) ou descartado
    (`'car_inst_implausivel'`).
    """
    if fator_carga_curva is not None and fator_carga_curva > 0:
        fator_carga = fator_carga_curva
    else:
        fator_carga = _FATOR_CARGA_CURVA.get(tipo, _FATOR_CARGA_CURVA['RES'])

    soma_energia = 0.0
    meses_validos = 0

    # 1. Histórico de faturação (ENE_01 a ENE_12)
    for mes in range(1, 13):
        if idx_uc is not None:
            campo = f'LISTA_ENE_{mes:02d}'
            lista_mes = parse_lista(row.get(campo, ''), float)
            if idx_uc < len(lista_mes):
                val = lista_mes[idx_uc]
                if val > 0:
                    soma_energia += val
                    meses_validos += 1
        else:
            campo = f'SOMA_ENE_{mes:02d}'
            val = float(row.get(campo, 0) or 0)
            if val > 0:
                soma_energia += val
                meses_validos += 1

    if meses_validos > 0:
        energia_media_mensal = soma_energia / meses_validos
        kw = energia_media_mensal / (730.0 * fator_carga)
        return kw * FC_MACROCOPICO if aplicar_fc_macro else kw

    # 2. Fallback: Carga Instalada × fator de demanda
    if idx_uc is not None:
        lista_car = parse_lista(row.get('LISTA_CAR_INST', ''), float)
        kw_inst = lista_car[idx_uc] if idx_uc < len(lista_car) else 0.0
    else:
        kw_inst = float(row.get('SOMA_CAR_INST', 0) or 0)

    if teto_car_inst_kw is not None:
        kw_inst, origem = car_inst_bt_kw(kw_inst, teto_car_inst_kw)
        if origem != 'kw' and avisos is not None:
            chave = 'car_inst_' + origem
            avisos[chave] = avisos.get(chave, 0) + 1

    kw = kw_inst * fator_demanda
    return kw * FC_MACROCOPICO if aplicar_fc_macro else kw

def _tem_consumo_real(row, idx_uc=None):
    """Retorna True se a linha possui histórico de consumo válido (ENE_01..12 com pelo menos 1 mês > 0)."""
    for mes in range(1, 13):
        if idx_uc is not None:
            val_lista = parse_lista(row.get(f'LISTA_ENE_{mes:02d}', ''), float)
            if idx_uc < len(val_lista) and val_lista[idx_uc] > 0:
                return True
        else:
            if float(row.get(f'SOMA_ENE_{mes:02d}', 0) or 0) > 0:
                return True
    return False
