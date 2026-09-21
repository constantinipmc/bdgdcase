# -*- coding: utf-8 -*-
"""Tudo que se ajusta sem mexer em lógica.

Uma premissa de conversão mora aqui, em cima, com o comentário que diz de onde
ela veio — norma, medição ou arbítrio declarado. É o arquivo que alguém abre
para saber o que o caso assume antes de acreditar no resultado dele.

## Oito destas não são constantes

`ALIMENTADOR`, `PASTA_OUTPUT`, `PASTA_OPENDSS`, `TENSAO_MT_KV`,
`TIP_DIA_CURVA_CRVCRG` e os três `REG_CONTROL_REV*` são reatribuídos em tempo
de execução, por `pipeline.processar_lote`, a cada alimentador e cada tipo de
dia. Quem precisar delas tem de ler **`config.NOME`**, nunca importar o nome
solto: `from ... import TENSAO_MT_KV` congela o valor do momento da importação,
e a reatribuição deixa de chegar — sem erro, sem aviso, com o caso saindo com a
tensão do alimentador anterior.

`tests/test_config.py` fixa quais são as oito.
"""
from __future__ import annotations

from bdgdcase.caminhos import (OPENDSS_DIR as _OPENDSS_DIR_CFG,
                               OUTPUT_DIR as _OUTPUT_DIR_CFG)

#: Os nomes deste módulo que mudam durante a execução — ver o docstring acima.
MUTAVEIS = (
    'ALIMENTADOR', 'PASTA_OPENDSS', 'PASTA_OUTPUT', 'TENSAO_MT_KV',
    'TIP_DIA_CURVA_CRVCRG', 'REG_CONTROL_COGEN', 'REG_CONTROL_REVERSIBLE',
    'REG_CONTROL_REV_NEUTRAL',
)


# ==============================================================================
# Premissas da conversão
# ==============================================================================
# Defaults derivados de bdgdcase.caminhos — sobrescritos por processar_lote()
# (PASTA_OUTPUT aponta para Output/{ALIM}; PASTA_OPENDSS para a pasta OpenDSS/).
PASTA_OUTPUT  = str(_OUTPUT_DIR_CFG)
PASTA_OPENDSS = str(_OPENDSS_DIR_CFG)
ALIMENTADOR   = "ALIM01"      # Codigo CTMT; processar_lote() sobrescreve

TENSAO_MT_KV   = 23.1         # kV linha-linha do alimentador MT
TENSAO_BT_KV   = 0.380        # kV linha-linha BT (220/127 V)
FREQUENCIA     = 60            # Hz

# Tratamento de bancos de capacitores da SUBESTAÇÃO (UNCRMT com DESCR=SE-*).
# Diferentemente dos bancos de rede (DESCR=RD-*, tipicamente 100–600 kVAr, fixos),
# os bancos de SE (tipicamente 2 400 – 7 200 kVAr) são MANOBRADOS por automatismo
# conforme o reativo do alimentador. Mantê-los fixos ligados causa sobrecompensação
# brutal em vazio (um banco de 7 200 kVAr fixo, madrugada adentro).
#   'off'        : emite com states=[0] (desligado). Assume que a compensação SE é
#                  contabilizada externamente (SCADA ou balanço da própria SE).
#   'fixo'       : emite sempre ligado (comportamento legado, pré-fix).
#   'capcontrol' : emite states=[0] + CapControl tipo kvar monitorando Vsource.source.
#
# Quando o DESCR nao traz prefixo que identifique o banco, o criterio passa a ser
# a potencia. Ver o comentario no ponto de uso para a evidencia do corte.
CAP_SE_KVAR_MINIMO = 2400.0
# Em 'capcontrol', os setpoints abaixo sao os da SE de referencia; ajustar
# para a subestacao em estudo.
CAP_SE_MODO                = 'off'
CAP_SE_CAPCONTROL_ON_KVAR  = 500.0        # liga se Q_fonte > X kVAr (indutivo)
CAP_SE_CAPCONTROL_OFF_KVAR = 0.0          # desliga se Q_fonte < Y kVAr
CAP_SE_CAPCONTROL_ELEMENT  = 'Vsource.source'

# Curto-circuito na fonte (OpenDSS: New Circuit ... MVAsc3/MVAsc1)
# Mantém compatibilidade com o comportamento histórico caso não haja dados suficientes.
MVASC3_PADRAO = 200.0
MVASC1_PADRAO = 150.0

# Override manual por alimentador (permite uso imediato de estudo externo sem mexer na lógica).
# Exemplo:
# "ALIM01": {"mvasc3": 320.0, "mvasc1": 240.0}
MVASC_OVERRIDE_POR_ALIMENTADOR = {}

# Estimativa BDGD quando não há estudo explícito:
# MVAsc3 ~= (Snom_total_UNTRS_MVA * fator)
# MVAsc1 ~= MVAsc3 * fator_monofasico
MVASC_DERIVADO_FATOR_SOBRECORRENTE = 15.0
MVASC_DERIVADO_FATOR_MONOFASICO = 0.75
# Fatores de Potência padrão por classe (quando não parametrizados nas UCs)
FATOR_POTENCIA = {
    'RES': 0.98,  # Residencial moderno (inversores e eletronicos)
    'COM': 0.96,  # Comercial misto
    'IND': 0.92,  # Industrial bruto (motores/fábricas)
    'IP':  0.99,  # Iluminacao publica LED
    'DEFAULT': 0.92,
}

# Fator de demanda: CAR_INST e "carga instalada" (nao e demanda real).
# Multiplique por FATOR_DEMANDA para obter demanda de pico realista.
# Valor tipico para alimentadores residenciais/mistos: 0.15 a 0.25
# (ex: trafo 45 kVA com 1421 kW instalado -> demanda real ~213 kW em pico)
FATOR_DEMANDA  = 0.2

# Fator de coincidência macroscópica: calibrado por comparação com medição
# agregada de alimentadores de uma distribuidora de Santa Catarina. Não é
# universal — ver a seção 3 do README.
FC_MACROCOPICO = 0.86

# Catálogo CRVCRG (BDGD): tipo de dia para escolher a curva (DU = dia útil típico)
TIP_DIA_CURVA_CRVCRG = 'DU'

# TIP_CC vazio ou inexistente no catálogo: fallback regulatório (classe B1 residencial)
DEFAULT_TIP_CC_CURVA = 'RES-Tipo1'

# Curva solar PV realista (Beta): usada para Loadshape.Curva_PV (96x15 min)
PV_BETA_ALPHA = 3.5
PV_BETA_BETA = 2.0
PV_BETA_SEED = 20260101  # semente fixa: mesma BDGD, mesmo .dss
# Diferença deliberada em relação ao projeto de origem, onde o padrão é None e a
# curva sai diferente a cada execução. Aqui a reprodutibilidade vem antes: sem
# semente fixa não há teste de regressão nem resultado que outra pessoa consiga
# reproduzir. Para o ruído de volta, passe `seed=None` a `gerar_curva_pv_beta`.
# Janela horaria padrao (fallback quando LATITUDE/LONGITUDE/DATA_SIMULACAO
# nao estao disponiveis). Em uso normal, e substituida pelo nascer/por-do-sol
# real calculado em `bdgdcase.solar.nascer_por_do_sol`.
PV_HORA_INICIO = 6.0
PV_HORA_FIM = 18.0
PV_SMOOTH_WINDOW = 3
PV_MC_DIAS = 60  # Quantidade de dias do Monte Carlo para a curva média

# Localizacao e data da simulacao — usadas para derivar nascer/por-do-sol
# automaticamente, ajustando IP (ON ao por / OFF ao nascer) e PV (envelope
# solar Beta).
# LATITUDE/LONGITUDE servem como FALLBACK: em runtime o valor efetivo e
# calculado a partir do centroide geografico do alimentador processado
# (PONNOT / SSDMT / SSDBT em EPSG:4674). Ver _centroide_alimentador().
# TIMEZONE_HORAS e DATA_SIMULACAO seguem globais (trocar para simular
# outra regiao/mes).
LATITUDE       = -27.02       # Santa Catarina (sul negativo) — fallback
LONGITUDE      = -51.15       # Oeste negativo — fallback
TIMEZONE_HORAS = -3           # UTC-3 (Brasil sem horario de verao)
DATA_SIMULACAO = "2024-12-15" # YYYY-MM-DD representativa do mes simulado

# Derating PV agregado: placa CC * inversor * sujeira * temperatura.
# Valor tipico 0.75-0.85; default 0.80 reflete instalacao residencial
# media brasileira sem manutencao recente.
PV_DERATING       = 0.80
# Fator de simultaneidade entre multiplas GDs no mesmo alimentador
# (variacao de nuvens entre PVs espalhadas reduz a soma agregada vs N
# copias da mesma curva sintetica). 0.92 e razoavel para alimentador
# urbano com 60+ GDs distribuidas.
PV_SIMULTANEIDADE = 0.92

# ============================================================================
# CORRECAO HORARIA OPCIONAL DA CURVA CRVCRG
# ----------------------------------------------------------------------------
# Motivacao:
#   As curvas CRVCRG da BDGD sao MEDIAS estatisticas regulatorias: uma
#   curva por classe para toda a concessionaria. Uma populacao com
#   habitos diferentes da media regional produz residuo sistematico
#   concentrado em faixas do dia -- tipicamente a madrugada e a ponta.
#   Isso NAO se corrige com fator de escala constante, que mexeria no
#   dia inteiro por igual.
#
# Modelo:
#   curva_corrigida_c(t) = curva_CRVCRG_c(t) * K_c(t)
#   - c em {RES, COM, IND, IP}
#   - K_c(t): 96 pontos (15 min)
#   - mean_t(K_c(t)) = 1.0 apos renormalizacao -> preserva energia diaria
#
# Os fatores abaixo sao um EXEMPLO DE FORMA, e nao um padrao a adotar:
# vieram de calibracao contra medicao de um caso particular, e so valem
# para ele.
#
# IMPORTANTE: sem recalibrar contra medicao propria, aplicar isto
# introduz vies artificial em vez de remove-lo. Por isso vem DESLIGADO.
# ============================================================================
APLICAR_CORRECAO_CRVCRG_K = False   # False = curvas BDGD originais (legado)

CORRECAO_CRVCRG_K_JANELAS = {
    'RES': [(0.0, 6.0, 0.92), (6.0, 17.0, 1.00), (17.0, 20.0, 0.94), (20.0, 24.0, 1.00)],
    'COM': [(0.0, 6.0, 0.95), (6.0, 17.0, 1.00), (17.0, 20.0, 0.97), (20.0, 24.0, 1.00)],
    'IND': [(0.0, 24.0, 1.00)],
    'IP':  [(0.0, 24.0, 1.00)],
}

# Colunas POT_01..POT_96 na CRVCRG
POT_COLS_CRVCRG = [f'POT_{i:02d}' for i in range(1, 97)]

# Modelo de Carga PRODIST 7 ANEEL (RN 956/2021, item 35)
# P ativo:  50% potencia constante + 50% impedancia constante
# Q reativo: 100% impedancia constante
# ZIPV = [Z_P, I_P, P_P, Z_Q, I_Q, P_Q, Vcutoff]
MODELO_CARGA_PRODIST = "model=8"
ZIPV_PRODIST = "ZIPV=[0.5, 0, 0.5, 1, 0, 0, 0.5]"

# Bus da subestacao (None = auto-detectar pelo link SE-xxx no UNSEMT)
BUS_FONTE_MANUAL = None        # Ex: '724566'

# Modo de modelagem das chaves UNSEMT:
# True  -> força todas fechadas (enabled=yes), exceto bypass de regulador
# False -> respeita o status operacional do BDGD (P_N_OPE), exceto bypass de regulador
FORCAR_CHAVES_FECHADAS = False
# Valida automaticamente o PAC_INI contra a conectividade da malha MT.
# Quando o PAC_INI cai fora da componente principal, tenta fallback topológico.
VALIDAR_FONTE_POR_TOPOLOGIA_MT = True
# Filtro espacial para PAC=0 na MT: evita "estrela" irreal conectando a fonte
# em múltiplos locais distantes do alimentador.
FILTRO_ESPACIAL_PAC_ZERO_MT = True
# Distância máxima (km) para aceitar que PAC=0 pertence à própria cabeceira.
PAC_ZERO_MT_MAX_DIST_FONTE_KM = 0.6
# Limite para ancorar PAC=0 ao PAC MT mais próximo.
PAC_ZERO_MT_MAX_DIST_NEAREST_KM = 0.8

# Transformador de distribuição sem potência no cadastro (`POT_NOM` ausente,
# zero ou negativo). Zero não é "sem dado": `kva=0` no OpenDSS deixa as
# impedâncias em pu sem base, o Newton sai em NaN em duas iterações e o
# alimentador INTEIRO perde a solução — medido no SMD01 (Celesc), por UM
# transformador de 877. Setenta e cinco kVA é o tamanho mais comum de
# transformador trifásico de poste, e já era o recuo para o campo ausente.
TRAFO_KVA_PADRAO = 75.0

# Reguladores: kVA base e multiplicador (para testes de sensibilidade).
# Exemplo: REG_KVA_MULTIPLICADOR=10 aplica 10 MVA em vez de 1 MVA.
REG_KVA_PADRAO = 1000.0
REG_KVA_MULTIPLICADOR = 1.0

# Modo do modelo Transformer.REG_* + RegControl:
# Conexão: wye aterrada (conns=[wye wye]), sem neutro flutuante — conforme IEEE 13 / IEEE 8500.
# REG_BANK_MONOFAIS_INDEPENDENTES = True (padrao): 3 trafos 1ph + 3 RegControl (tap por fase).
# REG_BANK_MONOFAIS_INDEPENDENTES = False: 1 trafo nph + 1 RegControl (tap unico, PTPhase seleciona fase).
REG_CONTROL_PT_PHASE = "min"  # legado se REG_BANK_MONOFAIS_INDEPENDENTES = False
REG_BANK_MONOFAIS_INDEPENDENTES = True
#   'stable' — parâmetros leves (XHL=1 % …) para depuração rápida.
#   'bdgd_electric' — R/XHL/PER, TRELTP/TRELTC/TCOR, TEN_REG do BDGD/EQRE + pisos numéricos.
REG_MODE = 'bdgd_electric'
DEBUG_REGCONTROL_TRACE = False

# Regulador: faixas numéricas e escala BDGD → OpenDSS.
# O DDA costuma gravar R/X em % (ex.: 2,5) ou em fração (ex.: 0,025 = 2,5 %).
# Se frações forem lidas como % literal (0,025 %), o trafo fica quase ideal e a rede
# não converge, sobretudo com fluxo reverso + RegControl.
# Até este limite, |v| é tratado como fração do % (0,04 → 4 %).
REG_BDGD_DECIMAL_MAX = 0.15
# Entre REG_BDGD_DECIMAL_MAX e 1,0, o R do catálogo EQRE é lido como décimos
# do percentual (0,261 → 2,61 %). Vale só para o R (perdas): o XHL do regulador
# é lido ao pé da letra, por `cadastro.xhl_regulador` — ver o porquê lá.
REG_BDGD_EQRE_DECIMO = True
REG_LOADLOSS_MAX_PCT = 5.0
# Religação do secundário do transformador ao circuito de baixa do próprio
# poste. Sem interruptor: acontece sempre que há o que religar, porque o
# transformador que não alcança as unidades que a BDGD diz que ele alimenta é
# defeito de cadastro, não escolha de modelagem.

# Até que distância o secundário e a ponta de um condutor de baixa são o mesmo
# poste, em metros. Medido: quando o cadastro separa os dois, separa por 1,6 a
# 2,0 m — precisão de desenho, não distância real. Cinco metros cobre isso com
# folga e continua longe do poste seguinte, que num ramal urbano fica a trinta
# ou mais.
TOLERANCIA_RELIGACAO_TRAFO_M = 5.0

# Raio da costura por coordenada, em metros. A costura une os PACs que estao no
# MESMO ponto do mapa; o que ela precisa e de um teste de DISTANCIA, e nao de
# igualdade de coordenada arredondada — dois pontos colados podem cair em
# baldes vizinhos do arredondamento e nunca se encontrar.
#
# Medido num alimentador da Copel: o vao que partia o alimentador em dois lobos
# tinha 3,0 m entre as pontas dos trechos, com as latitudes caindo em -22,73436
# e -22,73439 — um passo do arredondamento. Sem raio, 417 componentes de media;
# com raio, duas.
#
# Cinco e nao tres porque a coordenada chega aqui ja arredondada a cinco casas,
# o que soma ate 0,8 m de ruido: o corte efetivo sobre a distancia real fica em
# torno de quatro metros. E o mesmo numero de TOLERANCIA_RELIGACAO_TRAFO_M, e
# pela mesma razao — e imprecisao de desenho, nao distancia. Continua uma ordem
# de grandeza abaixo do vao entre postes, que num ramal urbano e de trinta.
TOLERANCIA_COSTURA_COORDENADA_M = 5.0

# Quanto a costura por coordenada precisa RECUPERAR da media tensao para valer
# a pena liga-la. O criterio e o ganho, e nao o nivel: o alimentador de
# referencia tem 0,70 da media encadeada por PAC e converte bem assim: os 30%
# restantes sao natureza do dado dele, e a costura nao os recupera (ganho
# +0,000). Ja o alimentador que saia partido vai de 0,08 para 0,998 — ganho
# +0,91. Medido em seis alimentadores de tres distribuidoras: o ganho e zero em
# todos os sadios e quase um no quebrado, sem nada no meio.
GANHO_MINIMO_COSTURA = 0.05

# A costura geometrica age so na MEDIA tensao. Medido: na baixa ela encadeia
# circuitos de transformadores DIFERENTES num so. Num alimentador da Copel o
# caminho de um no de baixa ate a media passou a ter 35 vaos e seis emendas —
# circuito secundario real tem poucos vaos —, e as pontas dessa corrente caiam
# a 0,650 pu. Restringindo a media, o mesmo alimentador fecha em 0,941.
#
# Religar secundario solto continua existindo, e por outro caminho:
# `religar_secundarios`, que anda do transformador ao poste dele e recusa o
# paralelo de tensoes diferentes. O que se recusa aqui e ligar dois circuitos
# que JA tem transformador cada um — isso nao religa nada, encadeia.
COSTURA_GEOMETRICA_APENAS_MT = True

# Cura topológica opcional: cria Jumpers.dss e inclui no Master.
HABILITAR_JUMPERS_TOPOLOGICOS = False
# Se a fonte topológica divergir do PAC_INI, prioriza PAC_INI e ativa jumpers
# automaticamente para evitar "trecho morto" logo após a subestação.
AUTO_PRIORIZAR_PAC_INI_COM_JUMPERS = True
# No modo automático de priorização do PAC_INI, evita jumpers MT para não criar
# atalhos elétricos indevidos na cabeceira (mantém apenas cura BT).
AUTO_JUMPERS_APENAS_BT = False
REG_NOLOAD_MAX_PCT = 3.0
REG_PTRATIO_MIN = 40.0
REG_PTRATIO_MAX = 800.0
REG_CTPRIM_MIN = 25.0
REG_CTPRIM_MAX = 2500.0
REG_VREG_MIN = 114.0   # ~0,95 × 120 V (base RegControl)
REG_VREG_MAX = 126.0   # ~1,05 × 120 V
# Fluxo reverso (GD) — tres modos disponiveis (apenas um True por vez):
#   REG_CONTROL_REV_NEUTRAL = True  → reversible=yes revNeutral=yes: ao detectar fluxo
#       reverso o tap vai para a posicao neutra (1,0 p.u.) e o regulador para de atuar,
#       nao influenciando os resultados. Fluxo direto (carga) regula normalmente.
#   REG_CONTROL_COGEN = True        → Cogen=yes: mantem regulacao para jusante com
#       setpoints reversos (revVreg/revBand) — regulador ainda influencia a tensao.
#       Para voltar a este modo: REG_CONTROL_REV_NEUTRAL = False, REG_CONTROL_COGEN = True.
#   REG_CONTROL_REVERSIBLE = True   → reversible=yes sem revNeutral: tenta regular em
#       direcao reversa, pode causar hunting (oscilacao forward/reverse).
REG_CONTROL_REVERSIBLE  = False
REG_CONTROL_COGEN       = False
REG_CONTROL_REV_NEUTRAL = True   # tap neutro (1,0 p.u.) em fluxo reverso

#: Os quatro modos, pelo nome, para `processar_lote(regulador_reverso=...)`.
#: A escolha muda o resultado e não apenas a forma: um regulador que vai a
#: neutro no reverso deixa de influir na tensão, e outro que continua regulando
#: não. Quem monta um estudo com geração distribuída precisa poder escolher, e
#: quem abre a pasta pronta precisa poder ver o que foi escolhido — por isso o
#: painel do mapa lê o modo do próprio caso.
#:                        (REVERSIBLE, COGEN, REV_NEUTRAL)
MODOS_REVERSO = {
    'neutro':       (False, False, True),
    'cogeracao':    (False, True,  False),
    'bidirecional': (True,  False, False),
    'direto':       (False, False, False),
}
REGULADOR_REVERSO_PADRAO = 'neutro'
# revThreshold baixo (1 kW) para detectar qualquer fluxo reverso real nos RegControls
# monofasicos. O limiar antigo (500 kW) nunca era atingido em alimentadores com GD
# residencial pequena (~10-50 kW por fase no regulador), fazendo o revNeutral nao disparar.
REG_REV_THRESHOLD_KW = 1.0
REG_REV_DELAY_S = 120.0
REG_REV_BAND = 4.0
REG_TAP_DELAY_S = 15.0
# Pisos adicionais no modo bdgd_electric (evita trafo “quase curto” após escalonar EQRE).
REG_BDGD_XHL_FLOOR_PCT = 0.8
REG_BDGD_LOADLOSS_FLOOR_PCT = 0.02

# Limites e passo de tap do regulador (padrao ANEEL: +/-16 passos x 0.625% = +/-10%)
REG_MINTAP       = 0.9       # Tap minimo (1 - 16 x 0.00625)
REG_MAXTAP       = 1.1       # Tap maximo (1 + 16 x 0.00625)

# Impedancias tipicas por categoria  (R1, X1, C1_nF/km, R0, X0)
# Premissas: valem so quando o SEGCON nao traz o condutor do trecho. O caso
# gerado diz quantos trechos cairam nelas.
IMPEDANCIAS_MT = {
    (3, 'AER'): (0.3505, 0.3703,  9.42, 0.5000, 1.2000),
    (2, 'AER'): (0.4500, 0.4000,  7.00, 0.6000, 1.2000),
    (1, 'AER'): (0.6156, 0.4500,  5.00, 0.6156, 0.4500),
    (3, 'SUB'): (0.2680, 0.0950, 200.0, 0.3000, 0.1500),
    (2, 'SUB'): (0.3500, 0.1000, 150.0, 0.4000, 0.1500),
    (1, 'SUB'): (0.4500, 0.1000, 100.0, 0.5000, 0.1500),
}
IMPEDANCIAS_BT = {
    (3, 'AER'): (0.8400, 0.0700, 50.0, 0.8400, 0.0700),
    (2, 'AER'): (1.2500, 0.0700, 30.0, 1.2500, 0.0700),
    (1, 'AER'): (1.5000, 0.0700, 20.0, 1.5000, 0.0700),
    (3, 'SUB'): (0.6700, 0.0750, 200., 0.6700, 0.0750),
    (2, 'SUB'): (0.9000, 0.0800, 100., 0.9000, 0.0800),
    (1, 'SUB'): (1.2000, 0.0800,  80., 1.2000, 0.0800),
}
IMPEDANCIAS_RAMAL = {
    (3, 'AER'): (1.8000, 0.0600, 20.0, 1.8000, 0.0600),
    (2, 'AER'): (2.0000, 0.0600, 15.0, 2.0000, 0.0600),
    (1, 'AER'): (2.5000, 0.0600, 10.0, 2.5000, 0.0600),
    # Só aéreo, de propósito: nas três bases medidas os 5,6 milhões de ramais
    # de ligação são todos aéreos, e definir a variante subterrânea encheria
    # todo caso gerado de três LineCodes que ninguém cita. Quem precisar dela
    # não fica sem trecho: `_linecode_ramal` degrada para o aéreo e avisa.
}

# ==============================================================================
