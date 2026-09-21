# -*- coding: utf-8 -*-
"""Das tabelas de um alimentador ao caso OpenDSS.

Segunda das três etapas do pacote — `extracao` → `modelo` → `interface`. Lê o
que a extração deixou em `Output/{ALIMENTADOR}/` e escreve em
`OpenDSS/{ALIMENTADOR}_{DIA}/` os arquivos `.dss` que o solver compila, mais os
`Cadastro_*.csv` que explicam de onde veio cada elemento.

## O que mora aqui

`config`
    As premissas. Toda decisão que se ajusta sem mexer em lógica, com a
    procedência anotada ao lado — norma, medição ou arbítrio declarado.
`registro`
    O que a conversão conta enquanto trabalha: o relato no terminal e o placar
    consultado no fim. Não confundir com `bdgdcase.ajustes`, que é o registro
    dos ajustes de cadastro que o usuário lê.
`cadastro`
    Ler a ficha da BDGD sem acreditar nela: identidade de barra, ligação e
    tensão, potência, e os catálogos DDA do regulador.
`curvas`
    Quanto cada carga consome a cada 15 minutos — a curva e o valor.
`topologia`
    Quem se liga a quem, e por onde entra a energia.
`coordenadas`
    Onde cada barra fica no mapa. Não muda o fluxo; muda quem enxerga o erro.
`cadastro_csv`
    A ficha de cada elemento gerado, ao lado do caso — o que o torna auditável.
`rede`
    A rede física escrita em `.dss`: trechos, transformadores, capacitores.
`cargas`
    As cargas de baixa e média tensão, e a iluminação pública.
`geracao`
    A geração distribuída — e ela não é só fotovoltaica.
`master`
    O `Master.dss`, que amarra o caso e declara as bases de tensão.
`pipeline`
    A ordem em que tudo acontece, e o laço por alimentador e tipo de dia.

## A ordem de leitura

`config` e `registro` não dependem de ninguém. `cadastro` depende deles;
`curvas`, `topologia` e `coordenadas` dependem de `cadastro`. Os emissores
(`rede`, `cargas`, `geracao`, `coordenadas`, `cadastro_csv`, `master`) dependem
de todos esses, e `pipeline` só sabe a ordem em que chamá-los.

A dependência nunca volta. Módulo que precisasse de quem depende dele é sinal
de que a divisão está errada — e a resposta é mudar a divisão, não o código.

## Escrever o caso não roda o caso

Este pacote não importa o solver, e há teste que exige que continue assim. A
conversão produz texto; quem resolve o fluxo de potência é `bdgdcase.solucao`.
A separação não é sobre instalação — é sobre responsabilidade: escrever um caso
e resolvê-lo são duas perguntas, e o erro de uma não deve aparecer disfarçado de
erro da outra.
"""
from __future__ import annotations

from bdgdcase.modelo import (cadastro, cadastro_csv, cargas, config,
                            coordenadas, curvas, geracao, master,
                            pipeline, rede, registro, topologia)

__all__ = ['cadastro', 'cadastro_csv', 'cargas', 'config', 'coordenadas',
           'curvas', 'geracao', 'master', 'pipeline', 'rede', 'registro',
           'topologia']
