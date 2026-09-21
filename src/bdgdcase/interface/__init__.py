# -*- coding: utf-8 -*-
"""Como se conversa com o bdgdcase.

Terceira das três etapas do pacote — `extracao` → `modelo` → `interface`. É a
única camada que sabe o que é uma janela, um clique ou um argumento de linha de
comando; as outras duas fazem o trabalho e não têm opinião sobre quem pediu.

## O que mora aqui

`cli`
    A linha de comando, que é a interface primária e a que a documentação
    descreve. Ponto de entrada do executável `bdgdcase`.
`janela`
    A janela. Monta as abas e compõe as partes; não desenha nem calcula.
`panorama`
    A aba que desenha todos os alimentadores da base, para escolher clicando.
`navegacao`
    Roda, arrasto e clique sobre o eixo do matplotlib, como num mapa.
`preparar`
    A aba que percorre a cadeia: escolher o `.gdb`, varrer, escolher o
    alimentador, extrair, converter, simular.
`geometria`
    Ler um caso já resolvido e responder perguntas sobre ele. Não desenha —
    é o que faz mapa, inspetor e resumo concordarem entre si.
`mapa`
    O desenho do alimentador e a navegação sobre ele.
`inspetor`
    O painel do ponto, com a procedência de cada número que mostra.
`resumo`
    O alimentador inteiro: faixas do PRODIST, pior momento do dia, balanço.
`registro`
    A aba dos ajustes do cadastro — o que foi mudado entre a BDGD e o caso.
`base`
    O mínimo que toda parte precisa: os imports adiados, a fonte e o tema.
`azulejos`
    O fundo do mapa — azulejos de mapa base, com cache em disco.

## Uma janela, dois pontos de partida

`bdgdcase gui` e `bdgdcase mapa` abrem a mesma janela e mudam só a aba inicial.
Antes eram dois programas, e ver no mapa o alimentador que se acabou de extrair
passava por um botão que lançava um segundo processo — uma fronteira do código,
não do trabalho.

## Importar a biblioteca não pode exigir Tk

É a regra que esta separação existe para manter, e `tests/test_importacao.py` a
verifica num Python cego para `tkinter`. Sem ela, `bdgdcase` fica inutilizável
em contêiner de CI, em servidor e em WSL sem X — que é exatamente onde a
conversão em lote faz mais sentido rodar. Por isso todo `import tkinter` deste
pacote mora dentro de função, e não no topo do módulo.

## A disciplina de thread

Trabalho pesado roda numa thread que **não toca em widget nenhum**: ela empurra
mensagens numa fila, e o laço do Tk drena a fila. Widget de Tk só pode ser
tocado pela thread que criou o interpretador, e ignorar isso não dá erro na
hora — dá travamento intermitente, do tipo que só aparece na máquina do
usuário.
"""
from __future__ import annotations

__all__ = []
