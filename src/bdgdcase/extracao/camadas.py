# -*- coding: utf-8 -*-
"""Quem é o quê nas 43 camadas da geodatabase.

O nome da camada não basta para classificar. `SEGCON` tem nome de
segmento, mas é catálogo de condutor e não tem PAC; `UNREMT` tem nome
de unidade transformadora, mas é regulador, e os `COD_ID` dele não são
`UNI_TR_MT` — se entrassem em `trans_codes` poluiriam as UCs. Por isso
cada teste aqui olha também os campos da camada, e não só o nome: é o
que separa o que é rede do que é ficha.

Referência: PRODIST Módulo 10, dicionário de dados da BDGD (V10/V11).
"""
from __future__ import annotations

import logging

# O mesmo logger do motor: a extração inteira conta a sua
# história num arquivo só (logs/extrator.log).
log = logging.getLogger("ExtratoBDGD")



# =============================================================================
# MAPEAMENTO DO PADRÃO BDGD ANEEL (V10/V11)
# =============================================================================

def is_cabo(layer_upper, fields_upper):
    """A camada é trecho de rede — média, baixa ou ramal de ligação?

    O que decide não é só o nome: é ter os dois pontos de conexão (`PAC_1` e
    `PAC_2`). Um trecho liga duas coisas; catálogo não liga nada. É por isso
    que `SEGCON`, que tem cara de segmento, fica de fora — ele é a tabela de
    condutores, e não tem PAC.
    """
    has_pac = 'PAC_1' in fields_upper and 'PAC_2' in fields_upper
    is_named = ('SSDMT' in layer_upper or 'SSDBT' in layer_upper or 'RAMLIG' in layer_upper)
    return is_named and has_pac


def is_trafo(layer_upper, fields_upper):
    """A camada é unidade transformadora (MT, AT ou de subestação)?

    `UNREMT` fica deliberadamente de fora, apesar do nome parecido: ela é o
    regulador de tensão, e os `COD_ID` dela não são `UNI_TR_MT`. Se entrassem,
    poluiriam a lista de transformadores usada para casar as UCs.
    """
    return (('UNTRMT' in layer_upper or 'UNTRAT' in layer_upper or
             'UNTRS' in layer_upper) and 'COD_ID' in fields_upper)


def is_ponto_rede(layer_upper):
    """A camada é ponto notável ou ponto de fase — o poste e as fases dele?"""
    return 'PONNOT' in layer_upper or 'PONFAS' in layer_upper


def is_uc(layer_upper, fields_upper):
    """A camada é ficha de consumo — unidade consumidora ou iluminação pública?

    `PIP` entra aqui porque, do ponto de vista da conversão, o ponto de
    iluminação pública é carga como as outras: pendura num poste e consome.
    """
    return (('UCBT' in layer_upper or 'UCMT' in layer_upper or
             'UCAT' in layer_upper or 'PIP' in layer_upper) and 'COD_ID' in fields_upper)


def is_catalogo(layer_upper):
    """A camada é catálogo — tabela de referência, e não rede?

    Catálogo se copia inteiro para o recorte, sem filtrar por alimentador: são
    poucas linhas, e a conversão consulta qualquer uma delas. É aqui que moram
    os condutores (`SEGCON`), os transformadores de catálogo (`EQTRMT`), as
    curvas típicas (`CRVCRG`) e as tabelas do regulador.
    """
    return layer_upper in [
        'SEGCON', 'EQTRMT', 'EQSE', 'EQRE', 'EQCR', 'CTMT', 'CRVCRG',
        'TPOTAPRT', 'TRELTP', 'TRELTC', 'TREGU', 'TFASCON', 'TCOR', 'TTEN',
    ]


def obter_coluna_alimentador(fields_upper):
    """O nome da coluna de alimentador nesta camada, ou None.

    A norma manda `CTMT`, mas as bases publicadas divergem: há camada com
    `COD_CTMT`, e camada auxiliar da distribuidora com `ALIM` ou
    `CIRCUITO`. Sem achar a coluna não há como recortar o alimentador, e
    a camada inteira é descartada do recorte.
    """
    for col in ['CTMT', 'COD_CTMT', 'COD_ALIM', 'ALIM', 'CIRCUITO']:
        if col in fields_upper: return col
    return None






def limpar_colunas(df, layer_upper='', ativo=True):
    """Descarta as colunas que a conversão não lê.

    Um `.gdb` de distribuidora traz dezenas de colunas por camada, e o
    recorte guarda todas em disco. Filtrar corta o tamanho do recorte sem
    perder nada que a conversão use — mas continua opcional
    (`ativo=False`), porque quem está investigando a base quer as colunas
    todas. `CRVCRG` nunca é filtrada: as 96 colunas de patamar são a
    tabela inteira.
    """
    if not ativo or layer_upper == 'CRVCRG':
        return df

    # Catálogos DDA usados na modelagem de reguladores / equipamentos: manter todas as colunas.
    if layer_upper in (
        'TPOTAPRT', 'TRELTP', 'TRELTC', 'TCOR', 'TREGU', 'TFASCON', 'TTEN',
    ):
        return df
    
    # ── O que fica de cada tabela ────────────────────────────────────────
    #
    # Uma lista só, aplicada a quase toda camada: a BDGD repete os mesmos
    # nomes de coluna entre tabelas, e uma lista por tabela seria a mesma
    # coisa escrita quinze vezes.
    #
    # O critério não é mais "o que o conversor lê". O conversor precisa de
    # umas trinta colunas; o resto está aqui porque **descreve o
    # equipamento** e é o que uma pessoa quer ver ao clicar num poste: de
    # que material ele é, que bitola tem o cabo, em que grupo tarifário está
    # a unidade. Coluna que não é extraída não volta — e reextrair custa
    # horas, enquanto carregar uma coluna a mais custa bytes.
    #
    # Algumas entradas não existem na BDGD V11 que serviu de referência
    # (`CLAS_TAR`, `ENE_MED`, `R0`, `TIP_GER`, `COD_ALIM`…). Ficam de
    # propósito: a base varia entre versões e entre distribuidoras, e
    # entrada sobrando não custa nada — o filtro é `nome in lista`, e nome
    # que não existe simplesmente não casa. Removê-las tornaria o extrator
    # pior para quem tem outra vintage da base, em troca de uma lista mais
    # curta.
    colunas_manter = [
        # Identificação e topologia
        'COD_ID', 'CTMT', 'COD_CTMT', 'COD_ALIM', 'ALIM', 'CIRCUITO', 'MUN',
        'PAC', 'PAC_1', 'PAC_2', 'PN_CON', 'UNI_TR_MT', 'UNI_TR_S',
        # `UN_RE` é para o regulador o que `UNI_TR_MT` é para o transformador: o
        # elo entre o catálogo (`EQRE`) e a unidade (`UNREMT`). Faltava, e sem
        # ele a `EQRE` saía com R, XHL, POT_NOM e as perdas de cada regulador
        # sem dizer de quem eram — o conversor lia a linha de `UNREMT`, não
        # achava as colunas, e escrevia os padrões em todo regulador de toda
        # distribuidora.
        'UN_RE',
        'FAS_CON', 'TIP_FAS', 'COMP', 'TIP_INST', 'COD_COND', 'TYP_COND',
        'NOME', 'BRR', 'ARE_LOC', 'SIT_ATIV', 'DAT_CON', 'TIP_SIST',

        # Condutor (SEGCON): elétrico e físico. `CNOM` é a corrente nominal
        # que o conversor usa como ampacidade; `CMAX` é a máxima do
        # catálogo, e as duas divergem — ver o painel, que mostra ambas.
        'TIP_CND', 'R1', 'X1', 'R0', 'X0', 'CNOM', 'CMAX',
        'BIT_FAS_1', 'BIT_FAS_2', 'BIT_FAS_3', 'BIT_NEU',
        'MAT_FAS_1', 'MAT_FAS_2', 'MAT_FAS_3', 'MAT_NEU',
        'ISO_FAS_1', 'ISO_FAS_2', 'ISO_FAS_3', 'ISO_NEU',

        # Transformador (UNTRMT/EQTRMT): ligação, perdas, impedâncias
        'TEN_LIN_SE', 'TEN_LIN_SEC', 'FAS_CON_P', 'FAS_CON_S', 'FAS_CON_T',
        'LIG', 'PER_FER', 'PER_TOT', 'R', 'XHL', 'XHT', 'XLT',
        'CLAS_TEN', 'TEN_PRI', 'TEN_SEC', 'TEN_TER',
        # `LIG_FAS_T` nomeia a fase de um SEGUNDO enrolamento de baixa, e é
        # o que distingue o center-tap do enrolamento único — a diferença
        # entre a perna valer metade da tensão declarada ou valer ela
        # inteira. Medido nas quatro bases, separa 100% dos center-tap de
        # 99,99% dos monofásicos simples, onde nem o `TIP_TRAFO` nem a
        # tensão conseguem: numa base `TIP_TRAFO='M'` é center-tap de
        # 254/127 e noutra o mesmo rótulo é enrolamento único de 220 V.
        'LIG_FAS_T',
        'POT_NOM', 'POT_NOM_COD', 'POT_NOM_KVAR', 'TIP_TRAFO', 'TIP_UNID',
        'EST_OPER', 'COR_NOM', 'TAP', 'BANC', 'MRT',

        # Chave (UNSEMT), fonte (CTMT) e curva de carga (CRVCRG)
        'P_N_OPE', 'CAP_ELO', 'TLCD',
        'PAC_INI', 'TEN_OPE', 'TEN_NOM', 'TIP_CC',

        # Regulador de tensão MT (UNREMT) — cadastro completo BDGD / DDA
        'DIST', 'TIP_REGU', 'TEN_REG', 'LIG_FAS_P', 'LIG_FAS_S',
        'REL_TP', 'REL_TC', 'SITCONT', 'DAT_IMO', 'DESCR',
        'ODI', 'TI', 'CM', 'TUC', 'A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'IDUC',

        # Poste (PONNOT): material, altura, esforço, estrutura. Nada disso é
        # elétrico, e é o primeiro que se pergunta olhando um ponto no mapa.
        'MAT', 'ESF', 'ALT', 'ESTR', 'TIP_PN', 'POS',

        # Unidade consumidora: carga, classe e enquadramento tarifário
        'CAR_INST', 'ENE_MED', 'ENE_TEF', 'CLAS_TAR',
        'CLAS_SUB', 'GRU_TAR', 'GRU_TEN', 'TEN_FORN',
        'TIP_EDIFICACAO', 'CLASSE_CONSUMO', 'CNAE', 'CEP',

        # Consumo mensal (kWh) dos doze meses do ano-base
        'ENE_01', 'ENE_02', 'ENE_03', 'ENE_04', 'ENE_05', 'ENE_06',
        'ENE_07', 'ENE_08', 'ENE_09', 'ENE_10', 'ENE_11', 'ENE_12',

        # Demanda medida e contratada (kW), onde a BDGD as traz
        'DEM_CONT',
        'DEM_01', 'DEM_02', 'DEM_03', 'DEM_04', 'DEM_05', 'DEM_06',
        'DEM_07', 'DEM_08', 'DEM_09', 'DEM_10', 'DEM_11', 'DEM_12',

        # Iluminação pública (PIP): a lâmpada, que é a carga de verdade
        'UC_ID', 'TIPO_LAMP', 'POT_LAMP', 'CONTROLE',

        # Geração distribuída (UGBT / UGMT e campo CEG nas UCs)
        # TIP_GER nem sempre vem preenchido; quando ausente, `gd_tipos.py`
        # infere a fonte pelo prefixo do CEG_GD (CGH./PCH./UHE.→hidro,
        # EOL.→eólica, UTE./CGB.→térmica, UFV.→solar) e, para o prefixo `GD.`,
        # que não diz a fonte, pelo fator de capacidade.
        'POT_INST', 'TIP_GER', 'CEG_GD',
    ]

    colunas_filtradas = [col for col in df.columns if col.upper() in colunas_manter or col.lower() == 'geometry']
    return df[colunas_filtradas].copy()
