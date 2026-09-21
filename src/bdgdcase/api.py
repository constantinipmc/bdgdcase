from __future__ import annotations

import os
from pathlib import Path

__all__ = ['listar', 'panorama', 'extrair', 'converter', 'DIAS']

#: Tipos de dia que a BDGD distingue, cada um com sua curva de carga própria.
DIAS = ('DU', 'SA', 'DO')

#: Nomes que a coluna do alimentador assume nas camadas da BDGD. A ordem é de
#: preferência: `CTMT` é o nome canônico, os outros aparecem em bases mais
#: antigas ou em recortes de distribuidora.
COLUNAS_ALIMENTADOR = ('CTMT', 'COD_CTMT', 'COD_ALIM', 'ALIM', 'CIRCUITO')


def listar(gdb):
    """Descobre quais alimentadores e municípios existem num `.gdb` da BDGD.

    Um `.gdb` de distribuidora traz centenas de alimentadores, e o código de
    cada um é a chave que :func:`extrair` exige. Sem isto, quem baixa a base da
    ANEEL fica sem saber o que pedir.

    A varredura lê **só a coluna procurada**, sem geometria — é o que a torna
    viável numa base de dezenas de gigabytes: cerca de 20 segundos para os 840
    alimentadores de uma distribuidora estadual inteira.

    Os municípios saem como **códigos IBGE de 7 dígitos**, que é como a BDGD os
    guarda, e é isso que ``extrair(..., municipio=...)`` espera.

    Devolve ``(alimentadores, municipios)``, ambos ordenados.
    """
    import pyogrio

    gdb = str(gdb)
    alimentadores, municipios = set(), set()

    for camada, *_ in pyogrio.list_layers(gdb):
        try:
            campos = {c.upper(): c for c in pyogrio.read_info(gdb, layer=camada)['fields']}
        except Exception:
            # Camada ilegível não impede a varredura: outra provavelmente
            # carrega as mesmas colunas.
            continue

        col_alim = next((campos[c] for c in COLUNAS_ALIMENTADOR if c in campos), None)
        col_mun = campos.get('MUN')

        for coluna, destino in ((col_alim, alimentadores), (col_mun, municipios)):
            if coluna is None or destino:
                continue
            try:
                serie = pyogrio.read_dataframe(gdb, layer=camada, columns=[coluna],
                                               read_geometry=False)[coluna]
            except Exception:
                continue
            destino.update(v for v in serie.dropna().astype(str).str.strip()
                           if v and v not in ('0', 'None'))

        if alimentadores and municipios:
            break

    return sorted(alimentadores), sorted(municipios)


def panorama(gdb, progresso=None):
    """Onde fica cada alimentador da base — o traçado de todos, para escolher.

    :func:`listar` responde *quais* alimentadores existem; esta responde *onde*.
    Devolve um :class:`bdgdcase.extracao.panorama.Panorama`, com o traçado
    grosseiro de cada alimentador em latitude e longitude, mais nome, tensão,
    quilômetros de rede e energia do ano.

    **Custa muito mais que `listar`**, e pela razão óbvia: lê a geometria da
    média tensão da base inteira, e não uma coluna. Alguns minutos numa base
    estadual, contra vinte segundos. Em troca, o resultado fica em cache no
    temporário do sistema, e a segunda vez na mesma base é instantânea.

    `progresso` é chamado como ``progresso(fracao, texto)``, com `fracao` de 0
    a 1 — quem espera minutos precisa ver que alguma coisa anda.
    """
    from bdgdcase.extracao.panorama import construir
    return construir(str(gdb), progresso=progresso)


def extrair(gdb, saida, alimentadores, *, municipio=None, limpar_colunas=True,
            threads=4):
    """Extrai do `.gdb` da BDGD as tabelas de cada alimentador.

    Escreve ``saida/{ALIMENTADOR}/`` com as tabelas em `.csv` e `.gpkg` que a
    conversão consome.

    Devolve uma lista de ``(alimentador, ok, erro)``.
    """
    from bdgdcase.extracao import extrair_alimentador

    gdb, saida = str(gdb), str(saida)
    os.makedirs(saida, exist_ok=True)
    if isinstance(alimentadores, str):
        alimentadores = [alimentadores]

    return [
        extrair_alimentador({
            'gdb': gdb,
            'base_out': saida,
            'alim_filter': alim,
            'mun_filter': municipio,
            'threads_internas': threads,
            'opt_clean': limpar_colunas,
        })
        for alim in alimentadores
    ]


def converter(pastas, dias=('DU',), regulador_reverso='neutro'):
    """Converte pastas de alimentador extraído em casos OpenDSS.

    ``pastas``: caminhos ``.../Output/{ALIMENTADOR}``. O destino é derivado
    como ``OpenDSS/{ALIMENTADOR}_{DIA}`` ao lado da pasta-mãe de ``Output`` —
    herança do projeto de origem, preservada para não quebrar quem já usa esse
    arranjo.

    ``dias``: quais tipos de dia gerar, entre os de :data:`DIAS`.

    ``regulador_reverso``: comportamento dos reguladores de tensão quando o
    fluxo inverte — ``'neutro'``, ``'cogeracao'``, ``'bidirecional'`` ou
    ``'direto'``. A escolha muda o resultado, e o painel do mapa mostra qual
    foi usada em cada caso.

    Devolve a lista de mensagens de erro; vazia significa que tudo passou.
    """
    dias = tuple(str(d).upper() for d in dias)
    invalidos = set(dias) - set(DIAS)
    if invalidos:
        raise ValueError('tipo de dia inválido: %s (use %s)'
                         % (', '.join(sorted(invalidos)), ', '.join(DIAS)))

    if isinstance(pastas, (str, Path)):
        pastas = [pastas]
    pastas = [str(p) for p in pastas]

    from bdgdcase.modelo.pipeline import processar_lote
    return processar_lote(pastas, dias=dias,
                          regulador_reverso=regulador_reverso)
