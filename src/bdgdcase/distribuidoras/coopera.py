# -*- coding: utf-8 -*-
"""Coopera — `DIST=5370`, Forquilhinha (SC).

Uma permissionária pequena: 16 alimentadores, 2.183 transformadores. O que ela
tem de próprio não é a rede, é o que **falta** no cadastro — e faltar não
levanta exceção.

## O que esta base tem

| | medido |
|---|---|
| `CRVCRG` | a camada existe, com os 101 campos, e **zero feições** |
| `TIP_CC` | `' '` — um espaço, não vazio nem nulo — em **100%** das UCs |
| `TIP_TRAFO` | `T` 1.961 e `MT` 221 |
| `TEN_LIN_SE` | 0,38 kV em 1.961 e 0,44 kV em 220 |
| `UNCRMT.DESCR` | vazio nos 18 registros |
| `RAMLIG` | **`PAC_1` é a unidade consumidora e `PAC_2` é o poste** |
| `UNSEMT` | nós `SE_CVO_*` encadeados dentro da subestação |

Rede de baixa igual à da base de origem — 380/220 com MRT 440/220 —, de modo
que nenhuma das correções de tensão a toca. Os problemas dela são outros.

## As decisões

**Sem curvas de carga.** Nada no tamanho do arquivo denuncia uma camada com
todos os campos e nenhuma linha. O alimentador saía com quatro curvas de 24
patamares desenhadas à mão e esticadas para 96, sob um cabeçalho que anunciava
"Catálogo CRVCRG (BDGD)". Hoje entra o catálogo de referência embarcado no
pacote, e a troca é declarada em quatro lugares — cabeçalho do
`CurvasDeCarga.dss`, console, coluna `curva_origem` do `Cadastro_Cargas.csv` e
README. A **forma** do dia passa a ser de outra base; a **energia** de cada
carga continua vindo do cadastro desta.

**`TIP_CC` é um espaço.** Não é vazio nem nulo, então qualquer teste por
ausência falhava e toda carga caía no padrão residencial: 731 de 731. A cadeia
de classe ganhou `.strip()` na frente e mais dois degraus — `CLAS_SUB` e
`GRU_TAR` —, que esta base preenche (`RE1`, `CO1`, `IN`, `RU1`; `B1`, `B3`,
`B2RU`). O resultado passou a 659 residenciais, 43 comerciais, 28 industriais e
1 de iluminação pública.

**Os PACs do ramal de ligação estão invertidos.** Aqui `PAC_1` é a unidade
(`UC30872`) e `PAC_2` é o poste (`BT278011`) — o contrário das duas bases
anteriores. A coordenada era herdada sempre de `PAC_1` para `PAC_2`, de modo que o
laço tentava dar ao poste a coordenada da UC, não conseguia, e saía sem herdar
nada: **0 de 664 ramais**, e metade da rede de baixa daquele alimentador sem
como ser desenhada.

A correção não pergunta de que distribuidora se trata: herda **em qualquer dos
dois sentidos**, do lado que tiver coordenada. Uma base que invente uma
terceira convenção também funciona.

**Os nós de manobra da subestação.** `SE_CVO_CS_16`, `SE_CVO_CS_17`, … são
chaves encadeadas entre si dentro da subestação, e nenhuma toca um cabo do
`SSDMT`. Nenhuma geometria passa por elas, e as barras ficavam sem coordenada —
o "gap" que aparece no mapa. As 13 chaves envolvidas **têm ponto próprio** no
cadastro, e é dele que a coordenada vem agora.

**`EQTRMT.TEN_PRI = '39'`** (7,96 kV) em 221 transformadores: MRT com primário
fase-neutro. É o esperado para os 221 `TIP_TRAFO='MT'` desta base.
"""
from __future__ import annotations

from .base import Achado, Distribuidora


def _conferir_tip_cc_em_branco(tabelas):
    """O espaço que não é vazio.

    Vale um achado próprio porque a consequência é muda e cara: sem tipologia,
    toda carga vira residencial, e o caso resolve sem reclamar de nada.
    """
    ucbt = tabelas.get('ucbt')
    if ucbt is None or not len(ucbt) or 'TIP_CC' not in ucbt.columns:
        return []
    vazio = ucbt['TIP_CC'].astype(str).str.strip().eq('').mean()
    if vazio < 0.5:
        return []
    tem_alternativa = any(c in ucbt.columns for c in ('CLAS_SUB', 'GRU_TAR'))
    if tem_alternativa:
        return [Achado('aviso',
                       'TIP_CC em branco em %.0f%% das unidades: a classe da '
                       'carga vem de CLAS_SUB/GRU_TAR.' % (100 * vazio))]
    return [Achado('erro',
                   'TIP_CC em branco em %.0f%% das unidades e sem CLAS_SUB nem '
                   'GRU_TAR na extracao: TODA carga vai sair residencial. '
                   'Reextraia com o extrator atualizado.' % (100 * vazio))]


def _conferir_ramal_invertido(tabelas):
    """A inversão dos PACs, dita em voz alta mesmo quando não atrapalha mais."""
    ram = tabelas.get('ramlig')
    ssdbt = tabelas.get('ssdbt')
    if ram is None or not len(ram) or ssdbt is None or not len(ssdbt):
        return []
    if 'PAC_1' not in ram.columns or 'PAC_1' not in ssdbt.columns:
        return []
    rede = set(ssdbt['PAC_1'].astype(str)) | set(ssdbt['PAC_2'].astype(str))
    p1 = ram['PAC_1'].astype(str).isin(rede).mean()
    p2 = ram['PAC_2'].astype(str).isin(rede).mean()
    if p1 >= p2:
        return []
    return [Achado('aviso',
                   'RAMLIG com os PACs invertidos: o poste esta em PAC_2 '
                   '(%.0f%% na rede) e a unidade em PAC_1 (%.0f%%). A '
                   'coordenada e herdada nos dois sentidos, entao o desenho '
                   'sai completo.' % (100 * p2, 100 * p1))]


PERFIL = Distribuidora(
    dist='5370',
    nome='Coopera (SC)',
    resumo=('Permissionária pequena. O que ela tem de próprio é o que falta: '
            'curvas de carga, tipologia da UC, e os PACs do ramal na ordem '
            'inversa. Nada disso levantava exceção.'),
    tensoes_bt_kv=(0.38, 0.44, 0.22),
    tipos_de_trafo=('T', 'MT'),
    tem_curvas_de_carga=False,
    tem_tipologia_de_carga=False,
    capacitor_tem_prefixo=False,
    ramal_pac1_e_poste=False,
    conferencias=(_conferir_tip_cc_em_branco, _conferir_ramal_invertido),
)
