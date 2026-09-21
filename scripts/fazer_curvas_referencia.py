# -*- coding: utf-8 -*-
"""Extrai o catálogo de curvas de carga de referência de uma BDGD real.

O conversor lê as curvas da camada `CRVCRG` da própria base. Nem toda base a
traz: numa das distribuidoras testadas a camada existe com os 101 campos e
**zero feições**, e o caso saía com quatro curvas desenhadas à mão, de 24
patamares esticados para 96, que ninguém mediu.

Este script grava um catálogo de referência dentro do pacote, para servir a quem
baixou uma base sem curvas. É dado aberto da ANEEL, redistribuído com
atribuição — a mesma condição do caso de exemplo que o pacote já publica.

O arquivo gerado **não se edita à mão**. Se precisar mudar, mude aqui e rode de
novo: um catálogo de curvas editado a dedo é indistinguível de um inventado.

Uso:
    python scripts/fazer_curvas_referencia.py CAMINHO.gdb
    python scripts/fazer_curvas_referencia.py CAMINHO.gdb --conferir
"""
from __future__ import annotations

import argparse
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESTINO = os.path.join(RAIZ, 'src', 'bdgdcase', 'dados', 'curvas_referencia.csv')

#: Os 96 patamares de 15 min. Mesmos nomes da BDGD.
POT_COLS = ['POT_%02d' % i for i in range(1, 97)]
COLUNAS = ['COD_ID', 'TIP_DIA'] + POT_COLS


def _ler(gdb):
    import geopandas as gpd

    crv = gpd.read_file(gdb, layer='CRVCRG', ignore_geometry=True,
                        engine='pyogrio')
    if crv.empty:
        raise SystemExit('CRVCRG está vazia em %s — escolha outra base.' % gdb)
    faltam = [c for c in COLUNAS if c not in crv.columns]
    if faltam:
        raise SystemExit('faltam colunas em CRVCRG: %s' % ', '.join(faltam[:6]))
    return crv[COLUNAS].copy()


def _normalizar(crv):
    """Ordena e arredonda, para o arquivo ser estável entre execuções.

    Sem ordem fixa o `git diff` de uma reextração viria cheio de linhas que só
    trocaram de lugar, e ninguém conseguiria ver o que de fato mudou.
    """
    crv['COD_ID'] = crv['COD_ID'].astype(str).str.strip()
    crv['TIP_DIA'] = crv['TIP_DIA'].astype(str).str.strip().str.upper()
    crv = crv.drop_duplicates(subset=['COD_ID', 'TIP_DIA'])
    crv = crv.sort_values(['COD_ID', 'TIP_DIA']).reset_index(drop=True)
    for c in POT_COLS:
        crv[c] = crv[c].astype(float).round(6)
    return crv


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('gdb', help='geodatabase da BDGD (pasta .gdb)')
    ap.add_argument('--conferir', action='store_true',
                    help='só compara com o arquivo já gravado, não escreve')
    args = ap.parse_args()

    crv = _normalizar(_ler(args.gdb))
    origem = os.path.basename(os.path.normpath(args.gdb))

    linhas = [
        '# Catálogo de curvas de carga de referência do bdgdcase.',
        '# GERADO por scripts/fazer_curvas_referencia.py — não editar à mão.',
        '# Origem: camada CRVCRG de %s' % origem,
        '# Dado aberto da ANEEL (BDGD), redistribuído com atribuição.',
        '# %d códigos x %d tipos de dia; 96 patamares de 15 min.'
        % (crv['COD_ID'].nunique(), crv['TIP_DIA'].nunique()),
        ','.join(COLUNAS),
    ]
    for _, r in crv.iterrows():
        linhas.append(','.join(
            [str(r['COD_ID']), str(r['TIP_DIA'])]
            + ['%.6f' % r[c] for c in POT_COLS]))
    texto = '\n'.join(linhas) + '\n'

    if args.conferir:
        if not os.path.exists(DESTINO):
            print('ainda não existe: %s' % DESTINO)
            return 1
        atual = open(DESTINO, encoding='utf-8', newline='').read()
        igual = atual == texto
        print('%s: %s' % (os.path.relpath(DESTINO, RAIZ),
                          'igual' if igual else 'DIFERENTE do que esta base gera'))
        return 0 if igual else 1

    os.makedirs(os.path.dirname(DESTINO), exist_ok=True)
    with open(DESTINO, 'w', encoding='utf-8', newline='') as f:
        f.write(texto)
    print('%s gravado: %d linhas, %.0f KB, origem %s'
          % (os.path.relpath(DESTINO, RAIZ), len(crv),
             os.path.getsize(DESTINO) / 1024, origem))
    return 0


if __name__ == '__main__':
    sys.exit(main())
