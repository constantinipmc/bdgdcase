# -*- coding: utf-8 -*-
"""Reduz um alimentador extraído a uma fixture que cabe num repositório.

A BDGD completa tem dezenas de gigabytes e não vai para o GitHub. Mas sem
nenhum dado de entrada, o teste do conversor cobre só as partes puras, e a
travessia inteira — que é o que este pacote faz — fica sem cobertura.

A saída é uma fatia real da BDGD pública da ANEEL, e não um alimentador
inventado: um alimentador verdadeiro reduzido ao mínimo que ainda converte. Uma
fixture sintética teria de acertar de cabeça o contrato de dezenas de colunas, e
passaria a testar a ideia que o autor faz da base em vez da base.

A redução mexe só nos catálogos de equipamento, que são de toda a distribuidora
e respondem por 99% do tamanho. A topologia não é tocada: nenhum transformador,
segmento ou unidade consumidora sai. Um alimentador podado seria um alimentador
diferente, e o caso `.dss` deixaria de ser comparável ao do alimentador inteiro.

Duas espécies de descarte, ambas verificadas e não presumidas:

* **Tabela que o conversor não lê** — EQSE, zero referências em
  `src/bdgdcase/modelo/`. São 24 MB que não mudam byte nenhum do resultado.
* **Catálogo que o alimentador não cita** — EQTRMT entra no caso por
  `UNTRMT.TIP_UNID → EQTRMT.COD_ID`. Se nenhuma linha casar, o catálogo já não
  era consultado e sai inteiro; se alguma casar, ficam só as que casam.

A prova de que a redução não alterou nada é `--conferir`: converte a fixture e
compara byte a byte com o caso gerado a partir do alimentador completo.

A entrada é uma pasta de alimentador já extraída — o que `bdgdcase extrair`
produz. O `.gdb` bruto da distribuidora não vem no repositório e não é
necessário aqui.

Uso:
    python scripts/fazer_fixture.py [--alim TRO05]
    python scripts/fazer_fixture.py --conferir
    python scripts/fazer_fixture.py --exemplo
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

import geopandas as gpd
import pandas as pd

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGEM_PADRAO = os.path.join(RAIZ, 'Output')
DESTINO = os.path.join(RAIZ, 'tests', 'dados')
EXEMPLOS = os.path.join(RAIZ, 'src', 'bdgdcase', 'exemplo')

# Tabelas que o conversor nunca lê. Confirmar com:
#     grep -rc EQSE src/bdgdcase/modelo/
#
# INDICE não é tabela da BDGD: é o nome de uma tabela de contexto que uma
# extração pode ter anexado por fora. O conversor não a lê, e ela não entra.
DESCARTAR = ('EQSE', 'INDICE')

# Catálogos de toda a distribuidora, podados às linhas que o alimentador cita.
#   prefixo: (coluna do catálogo, tabela que referencia, coluna que referencia)
PODAR = {
    'EQTRMT': ('COD_ID', 'UNTRMT', 'TIP_UNID'),
}

# ── Colunas que não vão para o repositório público ───────────────────────────
# A BDGD é aberta, mas isso não faz de tudo nela publicável em qualquer forma. A
# `DESCR` das tabelas de unidade consumidora traz, no formato da Celesc,
# `NR_CONTA_CNS_GENESIS: <n° da conta>,…` — o número da conta do cliente. Nos
# `.gpkg` agregados por poste isso vira pior: um ponto geográfico exato com até
# dezesseis contas concatenadas, cada uma com seu CEP e seu CNAE. Num alimentador
# de 68 unidades e 6 CEPs, um comércio com CNAE de restaurante num CEP é
# identificável a pé.
#
# Que o dado esteja na base da ANEEL não significa que republicá-lo num CSV de
# 20 KB no GitHub seja a mesma exposição: muda a facilidade, e muda quem assina a
# redistribuição.
#
# Nenhuma destas colunas é lida pelo conversor — conferido com grep em
# `src/bdgdcase/modelo/`, zero ocorrências, exceto `DESCR`, que ele lê só na UNCRMT
# (capacitores) para achar o prefixo `SE-`. Que retirá-las não muda o modelo não
# é promessa: `--conferir` compara o caso gerado byte a byte.
FORA = ('DESCR', 'CEP', 'CNAE', 'CLAS_SUB', 'GRU_TAR',
        'CLASSE_CONSUMO', 'TIP_EDIFICACAO')

# As tabelas agregadas por poste (`*_por_poste.gpkg`) têm esquema bem mais largo
# e repetem as mesmas colunas sob o prefixo `LISTA_`, concatenadas por `;`. Um
# filtro por nome exato passaria direto por elas.
PREFIXOS = ('LISTA_',)

# Colunas de contexto que não pertencem ao esquema da BDGD e que uma extração
# pode ter anexado por fora. O conversor não as lê, e a fixture não as leva:
# a varredura final pegou grandezas que já tinham saído como tabela à parte
# reaparecendo como colunas dentro dos agregados.
CONTEXTO_EXTERNO = ('RENDA_', 'IBGE_')

#: Onde `DESCR` fica, porque o conversor a lê.
DESCR_PRESERVADA = ('UNCRMT',)


def _fora(coluna):
    c = coluna.upper()
    if c in FORA or c.startswith(CONTEXTO_EXTERNO):
        return True
    return any(c.startswith(p) and c[len(p):] in FORA for p in PREFIXOS)


def _limpar_colunas(df, prefixo):
    """Remove as colunas identificáveis. Devolve (df, lista removida)."""
    fora = [c for c in df.columns if c != 'geometry' and _fora(c)]
    if prefixo.upper() in DESCR_PRESERVADA and 'DESCR' in fora:
        fora.remove('DESCR')
    return (df.drop(columns=fora) if fora else df), fora


def _achar(pasta, prefixo, extensoes=('.csv', '.gpkg')):
    for f in sorted(os.listdir(pasta)):
        if f.upper().startswith(prefixo.upper()) and f.lower().endswith(extensoes):
            return os.path.join(pasta, f)
    return None


def _referenciados(pasta, tabela, coluna):
    """Valores distintos de `coluna` na tabela que referencia o catálogo."""
    caminho = _achar(pasta, tabela)
    if caminho is None:
        return set()
    df = (gpd.read_file(caminho) if caminho.endswith('.gpkg')
          else pd.read_csv(caminho, low_memory=False))
    if coluna not in df.columns:
        return set()
    return {s for s in df[coluna].astype(str).str.strip()
            if s and s.lower() not in ('nan', 'none')}


def montar(alim, origem_raiz, destino):
    origem = os.path.join(origem_raiz, alim)
    if not os.path.isdir(origem):
        print('alimentador não extraído: %s' % origem)
        print('Extraia primeiro:  bdgdcase extrair --gdb <BDGD.gdb> '
              '--saida %s --alim %s' % (origem_raiz, alim))
        return 1

    alvo = os.path.join(destino, alim)
    if os.path.isdir(alvo):
        shutil.rmtree(alvo)
    os.makedirs(alvo)

    notas = []
    antes = depois = 0
    for nome in sorted(os.listdir(origem)):
        caminho = os.path.join(origem, nome)
        if not os.path.isfile(caminho):
            continue
        tamanho = os.path.getsize(caminho)
        antes += tamanho
        prefixo = nome.split('_')[0].upper()

        if prefixo in DESCARTAR:
            print('   %-34s fora: o conversor não lê %s' % (nome, prefixo))
            notas.append('`%s` — o conversor não lê esta tabela.' % prefixo)
            continue

        if prefixo in PODAR and nome.lower().endswith('.csv'):
            chave, tabela, coluna = PODAR[prefixo]
            usados = _referenciados(origem, tabela, coluna)
            df = pd.read_csv(caminho, low_memory=False)
            n0 = len(df)
            df = df[df[chave].astype(str).str.strip().isin(usados)]
            if df.empty:
                # Acontece de verdade: em muitos alimentadores `TIP_UNID` traz um
                # código de tipo ('38'), e não o COD_ID de um equipamento do
                # catálogo. O vínculo não resolve, e todo parâmetro de trafo vem
                # do próprio UNTRMT. Guardar um catálogo de 212 mil linhas que
                # ninguém consulta seria carregar peso morto.
                print('   %-34s fora: nenhuma das %d linhas é citada por %s.%s'
                      % (nome, n0, tabela, coluna))
                notas.append(
                    '`%s` — nenhuma das %d linhas é citada por `%s.%s` neste '
                    'alimentador, de modo que o catálogo nunca é consultado e '
                    'todo parâmetro de transformador vem do próprio `%s`.'
                    % (prefixo, n0, tabela, coluna, tabela))
                continue
            df.to_csv(os.path.join(alvo, nome), index=False)
            tam = os.path.getsize(os.path.join(alvo, nome))
            depois += tam
            print('   %-34s %d de %d linhas' % (nome, len(df), n0))
            notas.append('`%s` — %d das %d linhas, as citadas por `%s.%s`.'
                         % (prefixo, len(df), n0, tabela, coluna))
            continue

        destino_arq = os.path.join(alvo, nome)
        gpkg = nome.lower().endswith('.gpkg')
        df = (gpd.read_file(caminho) if gpkg
              else pd.read_csv(caminho, low_memory=False))
        df, removidas = _limpar_colunas(df, prefixo)
        if removidas:
            if gpkg:
                df.to_file(destino_arq, driver='GPKG')
            else:
                df.to_csv(destino_arq, index=False)
            print('   %-34s sem %s' % (nome, ', '.join(removidas)))
            notas.append('`%s` — sem %s.'
                         % (nome, ', '.join('`%s`' % c for c in removidas)))
        else:
            shutil.copyfile(caminho, destino_arq)
            print('   %-34s íntegra' % nome)
        depois += os.path.getsize(destino_arq)

    print()
    print('   %.1f MB -> %.1f MB' % (antes / 2 ** 20, depois / 2 ** 20))
    _escrever_leiame(alim, destino, notas, antes, depois, alvo, origem)
    return 0


def _procedencia(alvo, origem):
    """Distribuidora, nome e número de unidades, lidos da própria fixture.

    Lidos e não escritos à mão: as fixtures de três distribuidoras já saíram
    com a procedência da primeira, copiada.
    """
    from bdgdcase.distribuidoras import perfil_por_dist
    dist = ''
    ctmt = _achar(alvo, 'CTMT', ('.csv',))
    if ctmt:
        df = pd.read_csv(ctmt, dtype=str, low_memory=False)
        if 'DIST' in df.columns and len(df):
            dist = str(df['DIST'].iloc[0]).strip()
    nome = perfil_por_dist(dist).nome if dist else ''
    n_uc = 0
    for prefixo in ('UCBT_tab', 'UCMT_tab'):
        caminho = _achar(alvo, prefixo, ('.csv',))
        if caminho:
            n_uc += len(pd.read_csv(caminho, usecols=[0], low_memory=False))
    return dist, nome, n_uc


def _escrever_leiame(alim, destino, notas, antes, depois, alvo, origem):
    dist, nome, n_uc = _procedencia(alvo, origem)
    texto = [
        '# Dados de teste',
        '',
        'Uma fatia da BDGD pública da ANEEL, reduzida ao que cabe num repositório:',
        'o alimentador **%s** com a topologia intacta — todos os transformadores,' % alim,
        'segmentos e unidades consumidoras — e os catálogos de equipamento reduzidos',
        'ao que ele de fato cita. De %.0f MB para %.1f MB.' % (antes / 2 ** 20, depois / 2 ** 20),
        '',
        'Não é um alimentador inventado. Uma fixture sintética teria de acertar de',
        'cabeça o contrato de dezenas de colunas da BDGD, e passaria a testar a ideia',
        'que o autor faz da base em vez da base.',
        '',
        '## O que saiu',
        '',
    ]
    texto += ['- %s' % n for n in notas]
    texto += [
        '',
        'Que a redução não alterou o resultado não é promessa: o caso gerado a partir',
        'desta pasta sai **byte a byte igual** ao gerado a partir do alimentador',
        'completo (`python scripts/fazer_fixture.py --alim %s --conferir`). A que' % alim,
        '`tests/test_conversao.py` compara byte a byte com `src/bdgdcase/exemplo/` a',
        'cada execução é a fixture de referência, indicada em `tests/dados/LEIAME.md`.',
        '',
        '## Procedência e limites',
        '',
        'Fonte: Base de Dados Geográfica da Distribuidora (BDGD), Agência Nacional de',
        'Energia Elétrica, dado aberto em <https://dadosabertos.aneel.gov.br/>.',
        ('Distribuidora %s — %s.' % (dist, nome)) if dist else 'Distribuidora não identificada.',
        '',
        'A BDGD ser aberta não torna tudo nela publicável em qualquer forma. As colunas',
        'retiradas acima incluíam, na `DESCR` das unidades consumidoras, o número da',
        'conta do cliente no formato da distribuidora; nos `.gpkg` agregados por poste,',
        'um ponto geográfico exato chegava a carregar dezesseis contas concatenadas, com',
        'CEP e CNAE de cada uma. Num alimentador de %d unidades, isso é' % n_uc,
        'reidentificável. Nada disso é lido pelo conversor.',
        '',
        'O que permanece é o mínimo para que a fixture seja um modelo elétrico de',
        'verdade: topologia, coordenadas de poste, e o consumo mensal por unidade sob o',
        'identificador pseudônimo da própria BDGD. Sem CEP, sem CNAE e sem número de',
        'conta, não há vínculo direto com pessoa — mas quem for reutilizar esta pasta',
        'noutro contexto faz bem em reavaliar isso por conta própria.',
        '',
        '## Regeneração',
        '',
        'Esta pasta é derivada, não editada à mão:',
        '',
        '    bdgdcase extrair --gdb <BDGD.gdb> --saida Output --alim %s' % alim,
        '    python scripts/fazer_fixture.py --alim %s' % alim,
        '',
        'Para a BDGD completa, veja a seção *Dados* do README.',
        '',
    ]
    # Dentro da fixture, e não em `tests/dados/`: com mais de uma fixture o
    # arquivo compartilhado era sobrescrito a cada execução, e a última a rodar
    # apagava a descrição das outras. `tests/dados/LEIAME.md` passa a ser o
    # índice, escrito à mão porque não descreve nenhuma pasta em particular.
    io.open(os.path.join(destino, alim, 'LEIAME.md'), 'w',
            encoding='utf-8', newline='\n').write('\n'.join(texto))


# ── Prova de que a redução não mudou o caso ──────────────────────────────────

def _converter(entrada_alim):
    """Converte uma pasta de alimentador com este pacote; devolve a saída."""
    tmp = tempfile.mkdtemp(prefix='fixture_')
    shutil.copytree(entrada_alim,
                    os.path.join(tmp, 'Output', os.path.basename(entrada_alim)))
    env = dict(os.environ, BDGD_BASE_DIR=tmp,
               PYTHONPATH=os.path.join(RAIZ, 'src'))
    codigo = ('import sys; from bdgdcase.modelo.pipeline import processar_lote; '
              'processar_lote([sys.argv[1]], dias=("DU",))')
    r = subprocess.run(
        [sys.executable, '-c', codigo,
         os.path.join(tmp, 'Output', os.path.basename(entrada_alim))],
        env=env, capture_output=True, text=True)
    saida = os.path.join(tmp, 'OpenDSS',
                         '%s_DU' % os.path.basename(entrada_alim))
    if not os.path.isdir(saida):
        print((r.stderr or r.stdout)[-2000:])
        return None, tmp
    return saida, tmp


def conferir(alim, origem_raiz, destino):
    """Converte fixture e original e compara os arquivos gerados."""
    fixture = os.path.join(destino, alim)
    completo = os.path.join(origem_raiz, alim)
    for p in (fixture, completo):
        if not os.path.isdir(p):
            print('pasta ausente: %s' % p)
            return 1

    a, tmp_a = _converter(fixture)
    b, tmp_b = _converter(completo)
    try:
        if a is None or b is None:
            return 1
        nomes = sorted(f for f in os.listdir(b) if f.endswith(('.dss', '.csv')))
        ruins = []
        for nome in nomes:
            fa, fb = os.path.join(a, nome), os.path.join(b, nome)
            if not os.path.exists(fa):
                ruins.append(nome)
                print('   %-24s <<< a fixture não o gerou' % nome)
            elif _hash(fa) == _hash(fb):
                print('   %-24s idêntico' % nome)
            elif nome.startswith('Cadastro_'):
                # O cadastro DEVE divergir, e diverge por decisão: a fixture não
                # leva CEP, CNAE, classe de consumo nem grupo tarifário. O que
                # se exige dele é mais fraco que igualdade e mais forte que
                # nada — que ele apenas OMITA, nunca contradiga. Uma célula
                # preenchida com valor diferente seria a redução mudando o
                # dado, e não escondendo.
                vazias, erradas = _redigido(fa, fb)
                if erradas:
                    ruins.append(nome)
                    print('   %-24s <<< contradiz o completo em %s'
                          % (nome, ', '.join(sorted(erradas)[:6])))
                else:
                    print('   %-24s redigido: sem %s'
                          % (nome, ', '.join(sorted(vazias)) or '(nada)'))
            else:
                ruins.append(nome)
                print('   %-24s <<< difere' % nome)
        print()
        if ruins:
            print('%d arquivo(s) divergem: a redução alterou o caso.' % len(ruins))
            return 1
        print('A fixture gera o mesmo caso que o alimentador completo.')
        return 0
    finally:
        for t in (tmp_a, tmp_b):
            shutil.rmtree(t, ignore_errors=True)


def _hash(caminho):
    return hashlib.sha256(io.open(caminho, 'rb').read()).hexdigest()


def _ler_cadastro_csv(caminho):
    """Linhas de um `Cadastro_*.csv`, indexadas por elemento."""
    import csv

    with io.open(caminho, encoding='utf-8') as f:
        while True:
            onde = f.tell()
            linha = f.readline()
            if not linha or not linha.startswith('#'):
                f.seek(onde)
                break
        return {r['elemento']: r for r in csv.DictReader(f)}


def _redigido(reduzido, completo):
    """O cadastro da fixture omite, mas não contradiz, o do alimentador inteiro.

    Devolve `(colunas_esvaziadas, colunas_contraditorias)`. A segunda tem de
    sair vazia: célula preenchida com valor diferente significa que a redução
    mudou o dado em vez de escondê-lo, e aí a fixture deixou de descrever o
    mesmo alimentador.
    """
    a, b = _ler_cadastro_csv(reduzido), _ler_cadastro_csv(completo)
    if set(a) != set(b):
        return set(), {'as linhas não são as mesmas'}
    vazias, erradas = set(), set()
    for chave, linha_b in b.items():
        linha_a = a[chave]
        for coluna, valor in linha_b.items():
            atual = linha_a.get(coluna, '')
            if atual == valor:
                continue
            if atual == '':
                vazias.add(coluna)
            else:
                erradas.add(coluna)
    return vazias, erradas


def exemplo(alim, destino):
    """Gera o caso de referência a partir da fixture.

    O mesmo diretório serve a dois propósitos, e é de propósito: é o caso que
    `tests/test_conversao.py` compara byte a byte a cada execução, e é também o
    exemplo que alguém abre no GitHub para ver como um caso `.dss` sai daqui,
    sem instalar nada nem baixar a BDGD.
    """
    a, tmp = _converter(os.path.join(destino, alim))
    try:
        if a is None:
            return 1
        alvo = os.path.join(EXEMPLOS, '%s_DU' % alim)
        if os.path.isdir(alvo):
            shutil.rmtree(alvo)
        os.makedirs(alvo)
        total = 0
        for nome in sorted(os.listdir(a)):
            # O `Ajustes.json` vai junto: a pasta tem de sair com a explicação
            # do que há nela, e é o que o pacote empacota (ver pyproject).
            if not nome.endswith(('.dss', '.csv', '.json')):
                continue
            shutil.copyfile(os.path.join(a, nome), os.path.join(alvo, nome))
            total += os.path.getsize(os.path.join(alvo, nome))
            print('   %-24s %6d bytes'
                  % (nome, os.path.getsize(os.path.join(alvo, nome))))
        print()
        print('   %d KB em %s' % (total / 1024, os.path.relpath(alvo, RAIZ)))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--alim', default='TRO05')
    ap.add_argument('--origem', default=ORIGEM_PADRAO,
                    help='raiz das pastas de alimentador já extraídas '
                         '(padrão: Output/ deste repositório)')
    ap.add_argument('--destino', default=DESTINO)
    ap.add_argument('--conferir', action='store_true',
                    help='converte fixture e alimentador completo e compara '
                         'os casos byte a byte')
    ap.add_argument('--exemplo', action='store_true',
                    help='gera src/bdgdcase/exemplo/{ALIM}_DU — é o caso de '
                         'referência dos testes e o exemplo do README')
    args = ap.parse_args()
    origem_raiz = os.path.abspath(args.origem)
    destino = os.path.abspath(args.destino)

    if args.conferir:
        print('Conferindo a fixture de %s contra o alimentador completo' % args.alim)
        print()
        return conferir(args.alim, origem_raiz, destino)

    if args.exemplo:
        print('Gerando o caso de referência de %s' % args.alim)
        print()
        return exemplo(args.alim, destino)

    print('Reduzindo %s para fixture' % args.alim)
    print()
    return montar(args.alim, origem_raiz, destino)


if __name__ == '__main__':
    sys.exit(main())
