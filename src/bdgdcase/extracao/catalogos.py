# -*- coding: utf-8 -*-
"""Os catálogos codificados da norma, e a decodificação deles.

A BDGD grava código, não grandeza. A potência de um banco de
capacitores vem como `6`, que na tabela DDA TPOTRTV quer dizer
300 kVAr. Quem lê o campo sem decodificar põe 6 kVAr na rede e nunca
desconfia — o número é plausível, e o fluxo converge. É o tipo de erro
silencioso que este módulo existe para não deixar acontecer.
"""
from __future__ import annotations

import logging
import os

import pandas as pd

# O mesmo logger do motor: a extração inteira conta a sua
# história num arquivo só (logs/extrator.log).
log = logging.getLogger("ExtratoBDGD")



# DDA TPOTRTV — Tipo de Potência Reativa (PRODIST Módulo 10, p.166/180).
# Mapeia o código POT_NOM gravado nas tabelas UNCRMT/UNCRBT para a
# potência reativa real do banco em kVAr.
# IMPORTANTE: o BDGD grava o CÓDIGO, não o valor — sem essa decodificação,
# os capacitores ficam com valor numérico igual ao código (ex.: 6 kVAr em
# vez de 300 kVAr), distorcendo o Q da rede no OpenDSS.
TPOTRTV_KVAR = {
    0: 0,
    1: 45,    2: 75,    3: 100,   4: 150,   5: 200,
    6: 300,   7: 400,   8: 450,   9: 500,   10: 600,
    11: 900,  12: 1200, 13: 1512, 14: 1800, 15: 2016,
    16: 2400, 17: 3000, 18: 3600, 19: 4800, 20: 5400,
    21: 6000, 22: 7200, 23: 8400, 24: 9000, 25: 10500,
    26: 14000, 27: 15000, 28: 30000,
}



def aplicar_tpotrtv(df):
    """Decodifica POT_NOM → kVAr para UNCRMT/UNCRBT.
    Cria coluna 'POT_NOM_KVAR' e preserva o código original em 'POT_NOM_COD'.
    Sobrescreve POT_NOM com o valor em kVAr para o consumidor a jusante.
    """
    if 'POT_NOM' not in df.columns:
        return df
    cod = pd.to_numeric(df['POT_NOM'], errors='coerce').round().astype('Int64')
    kvar = cod.map(TPOTRTV_KVAR).astype('float64')
    df = df.copy()
    df['POT_NOM_COD']  = cod
    df['POT_NOM_KVAR'] = kvar

    # Onde a decodificação deu certo entra o kVAr; onde não deu, fica o valor
    # original. O `POT_NOM` desta tabela é um código do TPOTRTV nas bases
    # vistas, mas nada garante que seja em todas — e um valor fora do domínio
    # saía do `.map` como `NaN` e ia por cima do original, que se perdia ali. O
    # conversor então lia `NaN` e caía no padrão, sem saber que a base tinha
    # dito outra coisa. Preservado, o valor chega até ele, que já distingue
    # código de kVAr pelo tamanho e avisa quando desconfia.
    #
    # A coluna é substituída inteira, e não em parte. Uma atribuição parcial
    # (`df.loc[mask, 'POT_NOM'] = ...`) tem de caber no dtype que a coluna já
    # tem — e há base em que `POT_NOM` vem como texto, onde escrever um float
    # levanta `TypeError` e derruba a extração da camada inteira. Trocar a
    # coluna leva o dtype junto, que é o que se quer: o consumidor a jusante
    # espera kVAr numérico.
    original = pd.to_numeric(df['POT_NOM'], errors='coerce')
    fora = kvar.isna() & original.notna()
    df['POT_NOM'] = kvar.where(kvar.notna(), original).astype('float64')
    if int(fora.sum()):
        log.warning('[TPOTRTV] %d valor(es) de POT_NOM fora do dominio do '
                    'catalogo; preservados como estavam.', int(fora.sum()))
    return df


def garantir_tten_em_saida(out_dir, alim_filter):
    """Garante catálogo TTEN na saída.

    Se a camada TTEN não existir no GDB (situação comum em alguns envios), cria
    TTEN_<ALIM>.csv com base na tabela normativa ANEEL (Módulo 10, Anexo II).
    """
    for f in os.listdir(out_dir):
        fu = f.upper()
        if 'TTEN' in fu and (f.lower().endswith('.csv') or f.lower().endswith('.gpkg')):
            log.info("[TTEN] Catálogo já existe na saída: %s", f)
            return

    # Tabela normativa ANEEL (recorte das tensões MT/AT mais comuns na distribuição).
    # Campos: COD_ID, TEN (V), DESCR
    tten_rows = [
        ('35', 5000, '5 kV'),
        ('36', 6000, '6 kV'),
        ('37', 6600, '6,6 kV'),
        ('38', 6930, '6,93 kV'),
        ('39', 7960, '7,96 kV'),
        ('40', 8670, '8,67 kV'),
        ('41', 11400, '11,4 kV'),
        ('42', 11900, '11,9 kV'),
        ('43', 12000, '12 kV'),
        ('44', 12600, '12,6 kV'),
        ('45', 12700, '12,7 kV'),
        ('46', 13200, '13,2 kV'),
        ('47', 13337, '13,337 kV'),
        ('48', 13530, '13,53 kV'),
        ('49', 13800, '13,8 kV'),
        ('50', 13860, '13,86 kV'),
        ('51', 14140, '14,14 kV'),
        ('52', 14190, '14,19 kV'),
        ('53', 14400, '14,4 kV'),
        ('54', 14835, '14,835 kV'),
        ('55', 15000, '15 kV'),
        ('56', 15200, '15,2 kV'),
        ('57', 19053, '19,053 kV'),
        ('58', 19919, '19,919 kV'),
        ('59', 21000, '21 kV'),
        ('60', 21500, '21,5 kV'),
        ('61', 22000, '22 kV'),
        ('62', 23000, '23 kV'),
        ('63', 23100, '23,1 kV'),
        ('64', 23827, '23,827 kV'),
        ('65', 24000, '24 kV'),
        ('66', 24200, '24,2 kV'),
        ('67', 25000, '25 kV'),
        ('68', 25800, '25,8 kV'),
        ('69', 27000, '27 kV'),
        ('70', 30000, '30 kV'),
        ('71', 33000, '33 kV'),
        ('72', 34500, '34,5 kV'),
        ('73', 36000, '36 kV'),
        ('74', 38000, '38 kV'),
        ('75', 40000, '40 kV'),
        ('76', 44000, '44 kV'),
        ('77', 45000, '45 kV'),
        ('78', 45400, '45,4 kV'),
        ('79', 48000, '48 kV'),
        ('80', 60000, '60 kV'),
        ('81', 66000, '66 kV'),
        ('82', 69000, '69 kV'),
        ('83', 72500, '72,5 kV'),
        ('84', 88000, '88 kV'),
        ('85', 88200, '88,2 kV'),
        ('86', 92000, '92 kV'),
        ('87', 100000, '100 kV'),
        ('88', 120000, '120 kV'),
        ('89', 121000, '121 kV'),
        ('90', 123000, '123 kV'),
        ('91', 131600, '131,6 kV'),
        ('92', 131630, '131,63 kV'),
        ('93', 131635, '131,635 kV'),
        ('94', 138000, '138 kV'),
        ('95', 145000, '145 kV'),
        ('96', 230000, '230 kV'),
        ('97', 345000, '345 kV'),
        ('98', 500000, '500 kV'),
        ('99', 750000, '750 kV'),
        ('100', 1000000, '1000 kV'),
    ]

    df_tten = pd.DataFrame(tten_rows, columns=['COD_ID', 'TEN', 'DESCR'])
    out_csv = os.path.join(out_dir, f"TTEN_{str(alim_filter).strip().replace('/', '_')}.csv")
    df_tten.to_csv(out_csv, index=False)
    log.warning("[TTEN] Camada não encontrada no GDB. Criado fallback normativo: %s (%d linhas)",
                os.path.basename(out_csv), len(df_tten))
