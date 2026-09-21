# -*- coding: utf-8 -*-
"""Da geodatabase da ANEEL às tabelas de um alimentador.

Primeira das três etapas do pacote — `extracao` → `modelo` → `interface`. Lê o
`.gdb` que a distribuidora publica, recorta o alimentador pedido e grava em
`Output/{ALIMENTADOR}/` as tabelas que a conversão consome: rede física (cabos,
transformadores, postes, chaves), rede comercial (unidades consumidoras e
geradoras), catálogos de equipamento, e as agregações por poste.

## O que mora aqui

`motor`
    O trabalho. Uma classe sem interface nenhuma, que roda igual chamada da
    linha de comando, de uma janela ou de um processo filho.
`camadas`
    Quem é cabo, quem é transformador, quem é unidade consumidora, quem é
    catálogo. A BDGD tem 43 camadas e o nome delas não basta para classificar.
`catalogos`
    Os catálogos codificados da norma — potência de capacitor, tensão —, e a
    decodificação deles.
`agregacao`
    As unidades consumidoras somadas por poste, com as `LISTA_` posicionais que
    preservam a identidade de cada uma.

## Por que a extração é uma etapa separada

Ela é cara e é reaproveitável. Um `.gdb` de distribuidora tem dezenas de
gigabytes e centenas de alimentadores; extrair um deles leva minutos e produz
alguns megabytes que se convertem em segundos. Separar as duas coisas é o que
permite converter o mesmo alimentador dez vezes — mudando premissa, tipo de
dia, modo de regulador — sem tocar na geodatabase de novo.
"""
from __future__ import annotations

# A ordem importa: os três módulos de assunto não dependem de ninguém daqui,
# e o motor depende dos três. Importá-los primeiro deixa o pacote resolvido
# antes de o motor pedir por eles.
from bdgdcase.extracao import agregacao, camadas, catalogos
from bdgdcase.extracao.agregacao import (
    agregar_ucs_aos_postes,
    classificar_edificacao,
)
from bdgdcase.extracao.camadas import (
    is_cabo,
    is_catalogo,
    is_ponto_rede,
    is_trafo,
    is_uc,
    limpar_colunas,
    obter_coluna_alimentador,
)
from bdgdcase.extracao.catalogos import (
    TPOTRTV_KVAR,
    aplicar_tpotrtv,
    garantir_tten_em_saida,
)
from bdgdcase.extracao.motor import Extrator, extrair_alimentador

__all__ = [
    'Extrator', 'extrair_alimentador',
    'agregacao', 'camadas', 'catalogos',
    'agregar_ucs_aos_postes', 'classificar_edificacao',
    'is_cabo', 'is_catalogo', 'is_ponto_rede', 'is_trafo', 'is_uc',
    'limpar_colunas', 'obter_coluna_alimentador',
    'TPOTRTV_KVAR', 'aplicar_tpotrtv', 'garantir_tten_em_saida',
]
