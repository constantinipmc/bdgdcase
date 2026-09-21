# -*- coding: utf-8 -*-
"""Copel Distribuição — `DIST=2866`, Paraná.

A quarta base, e a que provou que a regra certa era outra. Ela **converteu e
convergiu de primeira** — 0,902 a 1,023 pu no primeiro alimentador tentado —, o
que já diz que a generalização feita para as três anteriores valeu. O que ela
corrigiu foi mais sutil: duas regras que acertavam nas outras por caminhos
errados.

É também a primeira base de safra diferente: `2025-12-31`, um ano à frente das
outras três. As 43 camadas continuam idênticas.

## O que esta base tem

| | medido em 518.989 transformadores |
|---|---|
| `TEN_LIN_SE` | **0,254 kV em 301.669** e 0,22 em 216.949 |
| `TIP_TRAFO` | `T` 217.320, `MT` 151.889, **`M` 149.753** |
| `FAS_CON_S` | `AN` em 301.642 e `ABCN` em 216.976 |
| `LIG_FAS_T` | preenchido em **100%** dos `M` e dos `MT` |
| `CTMT.TEN_NOM` | 13,8 kV e 34,5 kV — nenhum a 23,1 |
| `PIP.CAR_INST` | **a energia do mês, em kWh** |
| `PIP.POT_LAMP` | 70, 100, 150, 50, 60 W — correto |

Rede urbana em 220/127 e rural em **254/127 com center-tap** — um par que não
aparece em nenhuma das outras três.

## As duas regras que ela corrigiu

**O center-tap não se reconhece pelo `TIP_TRAFO` nem pela tensão.** Aqui
`TIP_TRAFO='M'` é center-tap em 149.576 transformadores; noutra base o mesmo
rótulo é enrolamento único. E a tensão do center-tap é 0,254 aqui e 0,44 lá —
números que não se parecem.

O que separa é `EQTRMT.LIG_FAS_T`, que nomeia a fase de um **segundo
enrolamento** de baixa. Medido nas quatro bases: preenchido em 100% dos
center-tap e em 0,01% dos monofásicos simples. Passou a ser o discriminante, e
o campo entrou na lista de colunas da extração — não estava lá.

O efeito aqui: os `M` iam pelo ramo bifásico e saíam com pernas de
`0,254/√3 = 147 V`, tensão que não existe em rede nenhuma. Com o center-tap
reconhecido, saem **127 V**, que é o padrão brasileiro do 254/127.

**E o `TEN_LIN_SE` não é sempre tensão de linha.** Esta base tornou visível o
que a anterior tinha feito escrever errado. `TEN_LIN_SE` é a tensão entre os
**condutores do secundário**: num secundário trifásico a quatro fios isso é a
tensão de linha e a fase sai sobre √3; num enrolamento único entre fase e
neutro, a tensão declarada **já é** a fase-neutro.

Foi por isso que 80.683 monofásicos de outra base, declarados `AN` a 0,22 sem
segundo enrolamento, passaram a ser modelados a 220 V — e não a 127, como
estavam desde que se removeu um limiar que, sem que se soubesse, codificava
essa distinção.

## A iluminação pública, e o método que ela obrigou a trocar

`PIP.CAR_INST` aqui é **exatamente** o maior mês de energia: a razão
`max(ENE)/CAR_INST` dá **1,00**. O campo é kWh, não kW.

A correção que havia — um fator de potência de dez, aferido pela energia —
acertava noutra base por coincidência, porque lá o desvio era mesmo de dez.
Aqui daria 32 W onde o `POT_LAMP` da própria base diz 100. A régua passou a ser
uma só e direta: se a razão está longe das ~354 horas que uma luminária fica
acesa, a potência vem de `max(ENE)/354`.

## O que já funcionava e continuou funcionando

Média em 13,8 kV lida do `CTMT.TEN_NOM` — sem constante; curvas de carga da
própria base (52 códigos); `TIP_CC` preenchido; nenhum `nan` no `.dss`.
"""
from __future__ import annotations

from .base import Achado, Distribuidora


def _conferir_center_tap_por_cadastro(tabelas):
    """O center-tap desta base só se reconhece pelo `LIG_FAS_T` do `EQTRMT`.

    Sem esse campo na extração, os 301.317 `M`/`MT` caem na heurística: os `MT`
    ainda acertam pelo `TIP_TRAFO`, e os `M` vão para o ramo bifásico e saem
    com pernas de 147 V. Vale um achado próprio porque a causa é uma coluna
    ausente, e ninguém adivinha isso olhando o `.dss`.
    """
    eq = tabelas.get('eqtrmt')
    untrmt = tabelas.get('untrmt')
    if untrmt is None or not len(untrmt) or 'TIP_TRAFO' not in untrmt.columns:
        return []
    tipos = untrmt['TIP_TRAFO'].astype(str).str.strip().str.upper()
    n_m = int((tipos == 'M').sum())
    if not n_m:
        return []
    tem_campo = (eq is not None and len(eq)
                 and 'LIG_FAS_T' in getattr(eq, 'columns', []))
    if tem_campo:
        return []
    return [Achado('erro',
                   '%d transformador(es) TIP_TRAFO=M e o EQTRMT sem LIG_FAS_T: '
                   'nesta base eles sao center-tap de 254/127, e sem esse campo '
                   'saem com pernas de 147 V. Reextraia o alimentador com o '
                   'extrator atualizado.' % n_m)]


def _conferir_tensao_da_media(tabelas):
    """13,8 e 34,5 kV. Nenhum alimentador desta base opera em 23,1."""
    ctmt = tabelas.get('ctmt')
    if ctmt is None or not len(ctmt) or 'TEN_NOM' not in ctmt.columns:
        return []
    cod = str(ctmt['TEN_NOM'].iloc[0]).strip()
    if cod in ('49', '72'):          # 13,8 kV e 34,5 kV
        return []
    return [Achado('aviso',
                   'TEN_NOM=%s no CTMT: fora dos dois valores vistos nesta base '
                   '(13,8 e 34,5 kV). Confira a tensao de media do caso.' % cod)]


PERFIL = Distribuidora(
    dist='2866',
    nome='Copel Distribuição (PR)',
    resumo=('Rural em 254/127 com center-tap e urbana em 220/127. Converteu de '
            'primeira, e corrigiu duas regras que acertavam nas outras bases '
            'por caminhos errados.'),
    tensoes_bt_kv=(0.254, 0.22, 13.8, 15.0),
    tipos_de_trafo=('T', 'M', 'MT'),
    tem_curvas_de_carga=True,
    tem_tipologia_de_carga=True,
    capacitor_tem_prefixo=False,
    ramal_pac1_e_poste=True,
    conferencias=(_conferir_center_tap_por_cadastro, _conferir_tensao_da_media),
)
