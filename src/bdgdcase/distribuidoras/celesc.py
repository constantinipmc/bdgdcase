# -*- coding: utf-8 -*-
"""Celesc Distribuição — `DIST=5697`, Santa Catarina.

É a base com que o conversor foi construído, e por isso a que menos aparece no
código: durante muito tempo o que era convenção dela passou por lei da física.
Este arquivo existe principalmente para separar as duas coisas.

## O que esta base tem

| | medido em 212.301 transformadores |
|---|---|
| `TEN_LIN_SE` | 0,38 kV em 108.868 e 0,44 kV em 103.279 |
| `TIP_TRAFO` | só `T` (108.972) e `MT` (103.279) |
| `FAS_CON_S` | `ABCN` no trifásico, `AN`/`BN`/`CN` no MRT |
| `TIP_CC` | preenchido |
| `CRVCRG` | 61 códigos × 3 tipos de dia |
| `UNCRMT.DESCR` | `RD-` (1.127) e `SE-` (297) |

Rede de baixa 380/220 e MRT 440/220 com center-tap. Duas tensões, e as duas
altas — o que explica os limiares que o conversor carregava.

## As decisões que esta base induziu, e que precisaram ser desfeitas

**`TENSAO_BT_KV = 0.380` como constante.** Correta aqui em 108.868 casos.
Noutra base metade dos transformadores é 220/127, e a constante os modelava 73%
acima. Hoje a tensão vem do `TEN_LIN_SE` de cada transformador.

**`if ten >= 0.30: ten /= √3`.** Este é o mais instrutivo. O limiar separa
0,38 de 0,22 — e funciona porque *nesta base* 0,22 nunca é tensão de linha.
Noutra é, em metade dos transformadores. A regra certa não tem limiar:
`TEN_LIN_SE` é tensão de **linha**, sempre, e quem quer a fase-neutro divide
por √3 — exceto no center-tap, onde a perna é metade.

**O center-tap identificado por `≈0,44`.** Aqui os 103.279 MRT são todos 440/220,
então o número serve. Noutra base o MRT é 230/115, e o teste por tensão o
perde; o `TIP_TRAFO='MT'` é o sinal confiável. Hoje `eh_center_tap` olha os
dois.

**O banco de subestação pelo prefixo `SE-` em `DESCR`.** Aqui o prefixo existe e
é confiável — e **tem de continuar mandando**, porque as faixas de potência se
cruzam: banco de rede vai de 100 a 1.800 kVAr e banco de subestação de 900 a
7.200. Nenhum critério de tamanho os separaria nesta base sem errar. Por isso a
regra é: prefixo quando houver, potência só quando não houver.

## Uma armadilha que só esta base esconde

O alimentador de referência do pacote (TRO05) tem os 11 transformadores em 0,38
kV e nenhum MRT. Isso o torna um bom golden — muda quando o comportamento muda
— e um mau detector: a discordância que fazia as cargas dos 103.279 MRT saírem
declaradas a 254 V em vez de 220 atravessou o repositório inteiro sem que ele
piscasse. Correção que toque em center-tap precisa ser medida noutro
alimentador desta mesma base, não só no golden.
"""
from __future__ import annotations

from .base import Achado, Distribuidora


def _conferir_mrt_e_center_tap(tabelas):
    """MRT desta base é 440/220. Um MRT com outra tensão merece um olhar.

    Não é erro: `eh_center_tap` decide pelo `TIP_TRAFO` também, então o caso
    sai certo. É um aviso porque contraria o que se documentou aqui, e o que
    contraria a documentação costuma ser o começo de uma descoberta.
    """
    untrmt = tabelas.get('untrmt')
    if untrmt is None or not len(untrmt):
        return []
    cols = getattr(untrmt, 'columns', [])
    if 'TIP_TRAFO' not in cols or 'TEN_LIN_SE' not in cols:
        return []
    mrt = untrmt[untrmt['TIP_TRAFO'].astype(str).str.strip().str.upper() == 'MT']
    if not len(mrt):
        return []
    fora = mrt[(mrt['TEN_LIN_SE'] - 0.44).abs() > 0.05]
    if not len(fora):
        return []
    return [Achado('aviso',
                   '%d MRT com TEN_LIN_SE fora de 0,44 kV: %s. Nesta base o MRT '
                   'e 440/220; a perna do center-tap sai como metade da tensao '
                   'declarada.'
                   % (len(fora), sorted({round(float(v), 3)
                                         for v in fora['TEN_LIN_SE'].dropna()})))]


PERFIL = Distribuidora(
    dist='5697',
    nome='Celesc Distribuição (SC)',
    resumo=('A base de origem do conversor: baixa 380/220 e MRT 440/220 com '
            'center-tap, cadastro completo. É dela que vieram as suposições '
            'que as outras bases desfizeram.'),
    tensoes_bt_kv=(0.38, 0.44, 0.22),
    tipos_de_trafo=('T', 'MT'),
    tem_curvas_de_carga=True,
    tem_tipologia_de_carga=True,
    capacitor_tem_prefixo=True,
    ramal_pac1_e_poste=True,
    conferencias=(_conferir_mrt_e_center_tap,),
)
