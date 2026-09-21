# -*- coding: utf-8 -*-
"""O que cada distribuidora tem de próprio, e como se confere.

Um arquivo por base, com o número medido ao lado de cada afirmação e a decisão
que ela induziu. A conversão **não** ramifica por distribuidora — o porquê está
em `base.py`, e vale a leitura antes de acrescentar a quinta.

Uso:

    from bdgdcase.distribuidoras import identificar

    perfil = identificar(ctmt)          # pelo campo DIST
    for achado in perfil.conferir(tabelas):
        print(achado)

Na linha de comando:

    bdgdcase distribuidoras            # o que está registrado
    bdgdcase distribuidoras --dist 396 # o dossiê de uma delas
"""
from __future__ import annotations

from .base import Achado, DESCONHECIDA, Distribuidora
from . import celesc, coopera, copel, rge

#: `DIST` da BDGD → perfil. O `DIST` é o identificador estável: o nome
#: comercial da distribuidora muda com fusão e reorganização societária, o
#: código não.
PERFIS = {
    celesc.PERFIL.dist: celesc.PERFIL,
    rge.PERFIL.dist: rge.PERFIL,
    coopera.PERFIL.dist: coopera.PERFIL,
    copel.PERFIL.dist: copel.PERFIL,
}

__all__ = ['Achado', 'Distribuidora', 'DESCONHECIDA', 'PERFIS',
           'identificar', 'perfil_por_dist']


def perfil_por_dist(dist):
    """Perfil de um código `DIST`, ou `DESCONHECIDA`."""
    if dist is None:
        return DESCONHECIDA
    chave = str(dist).strip()
    if chave.endswith('.0') and chave[:-2].isdigit():
        chave = chave[:-2]      # o `DIST` atravessa CSV e volta float
    return PERFIS.get(chave, DESCONHECIDA)


def identificar(ctmt):
    """Perfil da base a que este alimentador pertence, pelo `CTMT.DIST`.

    Aceita o DataFrame do CTMT, um dicionário, ou o próprio código. Nunca
    levanta: uma distribuidora não registrada devolve `DESCONHECIDA`, e a
    conversão segue igual — identificar é para conferir, não para converter.
    """
    if ctmt is None:
        return DESCONHECIDA
    if isinstance(ctmt, (str, int, float)):
        return perfil_por_dist(ctmt)
    try:
        if hasattr(ctmt, 'columns'):
            if 'DIST' not in ctmt.columns or not len(ctmt):
                return DESCONHECIDA
            return perfil_por_dist(ctmt['DIST'].iloc[0])
        if hasattr(ctmt, 'get'):
            return perfil_por_dist(ctmt.get('DIST'))
    except Exception:                        # pragma: no cover - defensivo
        return DESCONHECIDA
    return DESCONHECIDA
