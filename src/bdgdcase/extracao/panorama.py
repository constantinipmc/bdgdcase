# -*- coding: utf-8 -*-
"""Onde fica cada alimentador da base — o mapa que se olha ANTES de escolher.

`api.listar` responde *quais* alimentadores existem: oitocentos códigos numa
base estadual, e um código não diz onde o alimentador passa. Quem procura o
alimentador de um bairro escolhia por tentativa — extrair, converter, olhar no
mapa, descobrir que era outro —, e cada tentativa dessas custa minutos.

Este módulo responde *onde*. Lê a geometria da média tensão da base inteira e
devolve, por alimentador, o traçado da rede em latitude e longitude, mais o que
a ficha precisa: nome, tensão, quilômetros de rede e energia do ano.

## Três decisões que valem ser ditas

**A geometria vem de `SSDMT`, e não de `CTMT`.** `CTMT` é catálogo — traz
`COD_ID`, `NOME`, `PAC_INI`, `TEN_NOM`, `DIST` e a energia mensal, e **nenhuma
geometria**. O desenho do alimentador só existe nos trechos de média tensão.

**O traçado é costurado e simplificado, não amostrado.** A BDGD publica a média
tensão em vãos de poste a poste: uma base estadual traz cerca de 1,3 milhão
deles, e isso não se desenha. A primeira versão disto jogava fora um vão a cada
dez — o mapa saía **tracejado**, e alimentador tracejado se lê como rede
partida, que é a conclusão errada.

O caminho certo custa quase o mesmo e não mente: `line_merge` costura os vãos
que se tocam num traço contínuo por ramo, e `simplify` (Douglas-Peucker) tira os
vértices que não mudam o desenho. Medido na fixture: 1.611 vãos viram 183 ramos
contínuos de 1.794 vértices, e simplificar a 11 m deixa 484 — **3,7 vezes menos
que o original, com 0,02% do comprimento perdido**. Nenhum pedaço some; o que
some é o vértice que ninguém veria.

**Guarda-se latitude e longitude, não a projeção.** Projetar é decisão de quem
desenha — a interface converte para Web Mercator na hora de pôr sobre os
azulejos. Assim o cache serve a qualquer uso posterior, e este módulo não
depende do matplotlib nem do Tk.

## Como o traçado é guardado

Não como segmentos soltos, e sim como polilinhas: `coords` traz todos os
vértices em sequência, `partes` diz onde cada polilinha começa e acaba, e
`indice` diz de que alimentador ela é. É essa forma que preserva a continuidade
do traço — e ela desenha mais rápido que segmentos soltos, porque são menos
caminhos para o matplotlib percorrer.

## O custo, e por que há cache

A leitura percorre a geometria da base inteira: alguns minutos numa base
estadual, contra os ~20 s de `api.listar`, que lê só uma coluna. É caro o
bastante para não se pagar duas vezes — o resultado vai para o temporário do
sistema, na mesma disciplina dos azulejos do mapa, e a segunda abertura da mesma
base é instantânea. A chave do cache inclui o tamanho e a data de cada arquivo
do `.gdb`: base trocada, cache descartado.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile

__all__ = ['Panorama', 'construir', 'ORCAMENTO', 'TOLERANCIA']

#: Quantos vértices o mapa inteiro pode ter. É o teto que decide se o desenho
#: responde ao arrasto: meio milhão de vértices em polilinhas desenham em cerca
#: de meio segundo e repintam num piscar; o 2,6 milhão que a base traz, não.
ORCAMENTO = 500_000

#: Tolerância do Douglas-Peucker, em graus (~11 m). Um vão de média tem de 30 a
#: 120 m, então isto não junta postes: tira o vértice de uma curva que, na
#: escala em que este mapa é olhado, não é curva nenhuma. Medido na fixture:
#: 3,7 vezes menos vértices, 0,02% do comprimento.
TOLERANCIA = 0.0001

#: Feições por leitura. A geometria da base não cabe na memória de uma vez; em
#: blocos, o pico é o de um bloco, e é o que também permite dizer o andamento.
BLOCO = 200_000

#: Versão do arquivo de cache. Mudou o que se guarda, muda aqui — e o cache
#: antigo é descartado em vez de lido torto. A 1 guardava segmentos amostrados;
#: a 2 guarda polilinhas costuradas.
FORMATO = 2

#: Onde a média tensão mora na BDGD. Prefixo, e não nome exato: as bases
#: publicam `SSDMT` com sufixos (`SSDMT_1`, `SSDMT_URB13` num recorte).
PREFIXO_MT = 'SSDMT'


class Panorama:
    """O traçado de todos os alimentadores de uma base, pronto para desenhar.

    Tudo em graus decimais (EPSG:4674, como a BDGD publica), e nada projetado:
    ver o cabeçalho do módulo.

    - `alimentadores`: os códigos, ordenados. É o índice de tudo o mais.
    - `coords`: `(M, 2)` — todos os vértices, em sequência, como `(lon, lat)`.
    - `partes`: `(P + 1,)` — a polilinha `p` são os vértices
      `coords[partes[p]:partes[p + 1]]`.
    - `indice`: `(P,)` — a que alimentador cada polilinha pertence, como
      posição em `alimentadores`.
    - `info`: por código, `{nome, kv, km, mwh_ano, n_trechos}`. `km` e
      `n_trechos` são da rede **inteira**, medidos antes de simplificar: é o
      tamanho do alimentador, e não o do desenho.
    """

    def __init__(self, alimentadores, coords, partes, indice, info=None):
        """Guarda os arrays já prontos. Quem os constrói é :func:`construir`."""
        import numpy as np

        self.alimentadores = list(alimentadores)
        self.coords = np.asarray(coords, dtype=float).reshape(-1, 2)
        self.partes = np.asarray(partes, dtype=np.int64).reshape(-1)
        self.indice = np.asarray(indice, dtype=np.int32)
        self.info = dict(info or {})
        self._vaos = None

    def __len__(self):
        """Quantas polilinhas o mapa tem."""
        return len(self.indice)

    @property
    def n_vertices(self):
        """Quantos vértices ao todo — é o que mede o custo de desenhar."""
        return len(self.coords)

    def linhas(self, quais=None):
        """As polilinhas como lista de arrays `(n, 2)`, que é o que se desenha.

        `quais` restringe a um subconjunto de polilinhas, pelo índice delas —
        é assim que o realce pega só as do alimentador escolhido.
        """
        import numpy as np

        alvo = range(len(self.indice)) if quais is None else np.asarray(quais)
        return [self.coords[self.partes[p]:self.partes[p + 1]] for p in alvo]

    def partes_de(self, alimentador):
        """Os índices das polilinhas de um alimentador."""
        import numpy as np

        i = self._posicao(alimentador)
        if i is None:
            return np.zeros(0, dtype=int)
        return np.flatnonzero(self.indice == i)

    def caixa(self, alimentador=None):
        """Retângulo `(oeste, sul, leste, norte)` de um alimentador ou de tudo.

        `None` quando não há o que enquadrar — base vazia, ou código que não
        existe nesta base.
        """
        import numpy as np

        pontos = self.coords
        if alimentador is not None:
            partes = self.partes_de(alimentador)
            if not len(partes):
                return None
            pontos = np.concatenate(self.linhas(partes))
        if not len(pontos):
            return None
        return (float(np.min(pontos[:, 0])), float(np.min(pontos[:, 1])),
                float(np.max(pontos[:, 0])), float(np.max(pontos[:, 1])))

    def _posicao(self, alimentador):
        """A posição de um código em `alimentadores`, ou None."""
        try:
            return self.alimentadores.index(str(alimentador))
        except ValueError:
            return None

    @property
    def vaos(self):
        """Os pares de vértices consecutivos, para a busca do clique.

        Calculado uma vez. Um par por vão de traço desenhado — e não os
        vértices soltos: depois de simplificar, um tronco reto pode ter meio
        quilômetro entre dois vértices, e medir a distância só até eles faria
        o clique no meio do tronco errar o alimentador que está debaixo do
        cursor.
        """
        import numpy as np

        if self._vaos is None:
            ini, fim = self.partes[:-1], self.partes[1:]
            # Cada polilinha de n vértices tem n-1 vãos; polilinha de um
            # vértice só não tem nenhum, e sai daqui sem virar vão degenerado.
            comeco = np.concatenate([np.arange(a, b - 1)
                                     for a, b in zip(ini, fim) if b - a >= 2]
                                    ) if len(ini) else np.zeros(0, dtype=int)
            dono = np.concatenate([np.full(max(b - a - 1, 0), d)
                                   for a, b, d in zip(ini, fim, self.indice)]
                                  ) if len(ini) else np.zeros(0, dtype=int)
            self._vaos = (comeco.astype(np.int64), dono.astype(np.int32))
        return self._vaos

    def mais_proximo(self, lon, lat, raio):
        """O alimentador mais perto deste ponto, ou `None` se nenhum está perto.

        O `raio`, em graus, é o que faz clicar no vazio **desmarcar** em vez de
        agarrar um alimentador a cinco quilômetros dali — quem clica longe de
        tudo não está escolhendo, está limpando a seleção.

        A distância é até o **traço**, e não até o vértice mais próximo: é a
        projeção do ponto sobre cada vão. A diferença de longitude entra
        corrigida pelo cosseno da latitude, senão o "mais próximo" se
        enviesaria no sentido leste-oeste, onde o grau vale menos.
        """
        import numpy as np

        comeco, dono = self.vaos
        if not len(comeco):
            return None
        k = float(np.cos(np.radians(lat)))
        escala = np.array([k, 1.0])
        a = self.coords[comeco] * escala
        b = self.coords[comeco + 1] * escala
        p = np.array([lon, lat]) * escala

        ab = b - a
        denom = np.einsum('ij,ij->i', ab, ab)
        t = np.where(denom > 0,
                     np.einsum('ij,ij->i', p - a, ab) / np.where(denom > 0,
                                                                 denom, 1.0),
                     0.0)
        perto = a + np.clip(t, 0.0, 1.0)[:, None] * ab
        d2 = np.einsum('ij,ij->i', perto - p, perto - p)

        j = int(np.argmin(d2))
        if d2[j] > float(raio) ** 2:
            return None
        return self.alimentadores[int(dono[j])]


# ── Construção ───────────────────────────────────────────────────────────────

def construir(gdb, progresso=None, tolerancia=TOLERANCIA, cache=True):
    """Lê a geometria da média tensão da base e devolve um :class:`Panorama`.

    `progresso` é chamado como `progresso(fracao, texto)`, com `fracao` entre
    0 e 1 — a leitura leva minutos numa base estadual, e barra de progresso
    parada se lê como programa travado.

    Com `cache=True` (o padrão), uma base já lida antes volta do disco em
    milissegundos.
    """
    gdb = str(gdb)
    chave = _chave(gdb, tolerancia)

    if cache:
        pronto = _ler_cache(chave)
        if pronto is not None:
            _dizer(progresso, 1.0, 'Mapa dos alimentadores lido do cache.')
            return pronto

    pan = _construir(gdb, progresso, tolerancia)
    if cache:
        _gravar_cache(chave, pan)
    return pan


def _dizer(progresso, fracao, texto):
    """Avisa quem pediu, sem deixar o ouvinte derrubar a leitura.

    O relato é auxiliar: uma barra de progresso que falha não pode custar os
    minutos de leitura que já foram pagos.
    """
    if progresso is None:
        return
    try:
        progresso(float(fracao), texto)
    except Exception:      # pragma: no cover - ouvinte não pode derrubar
        pass


def _camada_mt(gdb):
    """A camada de média tensão da base, e como ela nomeia o alimentador.

    Devolve `(camada, coluna_do_alimentador, coluna_comprimento, n_feicoes)`.
    """
    import pyogrio

    from bdgdcase.extracao.camadas import obter_coluna_alimentador

    nomes = [c for c, *_ in pyogrio.list_layers(gdb)]
    candidatas = [c for c in nomes if c.upper().startswith(PREFIXO_MT)]
    if not candidatas:
        raise RuntimeError(
            'Não achei a camada de média tensão (%s*) em %s. '
            'Camadas encontradas: %s'
            % (PREFIXO_MT, gdb, ', '.join(nomes) or '(nenhuma)'))

    camada = sorted(candidatas)[0]
    info = pyogrio.read_info(gdb, layer=camada)
    campos = {c.upper(): c for c in info['fields']}
    col_alim_up = obter_coluna_alimentador(campos)
    if col_alim_up is None:
        raise RuntimeError(
            'A camada %s não tem coluna de alimentador; sem ela não há como '
            'saber de quem é cada trecho.' % camada)
    return (camada, campos[col_alim_up], campos.get('COMP'),
            int(info['features']))


def _extremos(geoms):
    """Primeiro e último vértice de cada geometria, de uma vez só.

    Cada registro de `SSDMT` é um vão entre dois postes, e duas pontas bastam
    para descrevê-lo — a costura em :func:`_costurar` é que devolve o traço
    contínuo. Vetorizado porque são milhões: a mesma conta em laço de Python é
    a diferença entre segundos e minutos.
    """
    import numpy as np
    import shapely

    coords, idx = shapely.get_coordinates(geoms, return_index=True)
    if not len(coords):
        return np.zeros((0, 2, 2)), np.zeros(len(geoms), dtype=bool)
    alvo = np.arange(len(geoms))
    ini = np.searchsorted(idx, alvo, side='left')
    fim = np.searchsorted(idx, alvo, side='right') - 1
    # Geometria vazia não tem vértice, e `fim < ini` a denuncia. Ela sai aqui em
    # vez de virar um segmento de lugar nenhum no meio do mapa.
    viva = fim >= ini
    return np.stack([coords[ini[viva], :2], coords[fim[viva], :2]], axis=1), viva


def _construir(gdb, progresso, tolerancia):
    """A leitura de verdade: blocos, CRS, extremos, costura e metadados."""
    import numpy as np
    import pyogrio

    camada, col_alim, col_comp, n = _camada_mt(gdb)
    colunas = [col_alim] + ([col_comp] if col_comp else [])

    _dizer(progresso, 0.0, 'Lendo a geometria de %s (%s trechos)…'
           % (camada, _milhar(n)))

    codigos_por_bloco, segmentos, comprimentos = [], [], []
    for inicio in range(0, max(n, 1), BLOCO):
        gdf = pyogrio.read_dataframe(gdb, layer=camada, columns=colunas,
                                     skip_features=inicio, max_features=BLOCO)
        if not len(gdf):
            break

        # A única normalização de CRS do pacote. A BDGD publica em EPSG:4674,
        # mas um recorte de distribuidora pode vir projetado — e aí os metros
        # entrariam no lugar dos graus, sem erro nenhum: o alimentador iria
        # parar no golfo da Guiné, sobre um azulejo de oceano.
        if gdf.crs is not None and not gdf.crs.is_geographic:
            gdf = gdf.to_crs(4674)

        pontas, viva = _extremos(gdf.geometry.values)
        codigos = gdf[col_alim].to_numpy(dtype=object)[viva]
        codigos = np.array([str(c).strip() for c in codigos], dtype=object)
        # Código vazio é trecho órfão: existe na base e não pertence a
        # alimentador nenhum. Desenhá-lo daria uma cor a mais na legenda para
        # algo que ninguém pode escolher.
        util = np.array([bool(c) and c not in ('0', 'None', 'nan')
                         for c in codigos], dtype=bool)

        u, inv = np.unique(codigos[util], return_inverse=True)
        codigos_por_bloco.append((u, inv.astype(np.int32)))
        segmentos.append(pontas[util])
        if col_comp:
            comp = gdf[col_comp].to_numpy()[viva][util]
            comprimentos.append(np.nan_to_num(
                comp.astype(float, copy=False), nan=0.0))
        else:
            comprimentos.append(np.zeros(int(util.sum())))

        _dizer(progresso, min(0.75, 0.75 * (inicio + BLOCO) / max(n, 1)),
               'Lidos %s de %s trechos…'
               % (_milhar(min(inicio + BLOCO, n)), _milhar(n)))

    if not segmentos:
        return Panorama([], np.zeros((0, 2)), np.zeros(1, dtype=np.int64),
                        np.zeros(0, dtype=np.int32))

    # Os códigos são unificados só agora: guardar 1,3 milhão de strings de
    # Python durante a leitura custaria mais memória que a geometria toda.
    # Por bloco são algumas centenas de códigos distintos, e é isso que se
    # carrega.
    alimentadores = sorted({c for u, _ in codigos_por_bloco for c in u})
    posicao = {c: i for i, c in enumerate(alimentadores)}
    indice = np.concatenate([
        np.array([posicao[c] for c in u], dtype=np.int32)[inv]
        for u, inv in codigos_por_bloco])
    pontas = np.concatenate(segmentos)
    comp = np.concatenate(comprimentos)

    # Tamanho do alimentador de VERDADE, medido antes de simplificar: é o que
    # vai para a ficha, e ele não pode encolher junto com o desenho.
    km = {c: float(comp[indice == i].sum()) / 1000.0 for c, i in posicao.items()}
    n_trechos = {c: int((indice == i).sum()) for c, i in posicao.items()}

    _dizer(progresso, 0.78, 'Costurando %s vãos em traços contínuos…'
           % _milhar(len(indice)))
    coords, partes, dono = _costurar(pontas, indice, len(alimentadores),
                                     tolerancia, progresso)

    _dizer(progresso, 0.96, 'Lendo o cadastro dos alimentadores…')
    info = _metadados(gdb, alimentadores, km, n_trechos)

    _dizer(progresso, 1.0, '%d alimentadores, %s traços, %s vértices.'
           % (len(alimentadores), _milhar(len(dono)), _milhar(len(coords))))
    return Panorama(alimentadores, coords, partes, dono, info)


def _milhar(n):
    """Número com ponto de milhar, como se escreve em português."""
    return '{:,}'.format(int(n)).replace(',', '.')


def _costurar(pontas, indice, n_alim, tolerancia, progresso=None):
    """Junta os vãos de cada alimentador num traço contínuo, e o simplifica.

    É o coração do módulo, e o que separa este mapa da primeira versão dele.
    A BDGD publica a média tensão em vãos de poste a poste; desenhados como
    vêm, são milhões de traços minúsculos. Amostrar um a cada dez — o que se
    fazia antes — corta o custo e produz um alimentador **tracejado**, que se
    lê como rede partida.

    `line_merge` costura os vãos que compartilham ponta, devolvendo uma
    polilinha por ramo da rede. `simplify` então tira os vértices que não mudam
    o desenho na escala em que ele é olhado. Nada é descartado: o traço fica
    inteiro, com menos pontos.

    Se ainda assim passar do orçamento, a tolerância sobe na proporção do
    excesso e simplifica de novo — uma vez só, porque a segunda passada já
    resolve e ficar iterando custaria mais que desenhar.
    """
    import numpy as np
    import shapely

    # Um argsort, e não uma varredura por alimentador: `indice == i` dentro de
    # um laço de 840 voltas percorreria 1,3 milhão de linhas a cada volta.
    ordem = np.argsort(indice, kind='stable')
    inicios = np.searchsorted(indice[ordem], np.arange(n_alim + 1), side='left')

    unidos, dono_geom = [], []
    for i in range(n_alim):
        a, b = inicios[i], inicios[i + 1]
        if b <= a:
            continue
        meus = pontas[ordem[a:b]]
        plano = meus.reshape(-1, 2)
        quem = np.repeat(np.arange(len(meus)), 2)
        vaos = shapely.linestrings(plano, indices=quem)
        unido = shapely.line_merge(shapely.multilinestrings(vaos))
        for parte in shapely.get_parts(unido):
            unidos.append(parte)
            dono_geom.append(i)
        if progresso is not None and n_alim > 20 and i % max(n_alim // 20, 1) == 0:
            _dizer(progresso, 0.78 + 0.17 * i / n_alim,
                   'Costurando o traçado… (%d de %d alimentadores)'
                   % (i, n_alim))

    if not unidos:
        return (np.zeros((0, 2)), np.zeros(1, dtype=np.int64),
                np.zeros(0, dtype=np.int32))

    geoms = np.array(unidos, dtype=object)
    dono_geom = np.array(dono_geom, dtype=np.int32)

    simples = shapely.simplify(geoms, tolerancia)
    total = int(shapely.get_num_coordinates(simples).sum())
    if total > ORCAMENTO:
        # Base maior que o previsto: afrouxa na proporção do excesso. O
        # Douglas-Peucker responde de forma quase linear nessa faixa, e uma
        # passada basta — o alvo é caber, não acertar o número na mosca.
        simples = shapely.simplify(geoms, tolerancia * (total / ORCAMENTO))
        _dizer(progresso, 0.95,
               'Base grande: traçado afrouxado para caber no desenho.')

    coords, parte_de = shapely.get_coordinates(simples, return_index=True)
    # `partes` são os limites de cada polilinha dentro de `coords`; com eles a
    # interface fatia o array sem procurar nada.
    contagem = np.bincount(parte_de, minlength=len(simples))
    partes = np.concatenate([[0], np.cumsum(contagem)]).astype(np.int64)
    return coords, partes, dono_geom


def _metadados(gdb, alimentadores, km, n_trechos):
    """Nome, tensão, quilômetros e energia do ano, por alimentador.

    Sai de `CTMT`, que é catálogo e se lê inteiro em um segundo. Falhar aqui
    não pode custar o mapa: sem a ficha o mapa ainda responde onde cada
    alimentador fica, que é o que ele existe para fazer.
    """
    base = {c: {'nome': c, 'kv': None, 'km': round(km.get(c, 0.0), 1),
                'mwh_ano': None, 'n_trechos': n_trechos.get(c, 0)}
            for c in alimentadores}
    try:
        import pandas as pd
        import pyogrio

        nomes = [c for c, *_ in pyogrio.list_layers(gdb)]
        ctmt = next((c for c in nomes if c.upper().startswith('CTMT')), None)
        if ctmt is None:
            return base

        df = pyogrio.read_dataframe(gdb, layer=ctmt, read_geometry=False)
        col = {c.upper(): c for c in df.columns}
        if 'COD_ID' not in col:
            return base

        tten = next((c for c in nomes if c.upper().startswith('TTEN')), None)
        tten_df = (pyogrio.read_dataframe(gdb, layer=tten, read_geometry=False)
                   if tten else None)
        from bdgdcase.modelo.cadastro import _tten_cod_para_kv

        ene = [col[f'ENE_{m:02d}'] for m in range(1, 13)
               if f'ENE_{m:02d}' in col]
        for _, linha in df.iterrows():
            cod = str(linha[col['COD_ID']]).strip()
            alvo = base.get(cod)
            if alvo is None:
                continue
            if 'NOME' in col:
                nome = str(linha[col['NOME']]).strip()
                if nome and nome.lower() != 'nan':
                    alvo['nome'] = nome
            if 'TEN_NOM' in col:
                alvo['kv'] = _tten_cod_para_kv(linha[col['TEN_NOM']], tten_df)
            if ene:
                total = pd.to_numeric(pd.Series([linha[c] for c in ene]),
                                      errors='coerce').fillna(0).sum()
                alvo['mwh_ano'] = round(float(total) / 1000.0, 1)
    except Exception:
        # Ver o docstring: a ficha é acessório, o mapa não.
        pass
    return base


# ── Cache ────────────────────────────────────────────────────────────────────

def _pasta_cache():
    """A pasta de cache, criada se ainda não existir.

    No temporário do sistema, e não dentro do projeto, pela mesma razão dos
    azulejos: é derivado de uma base que mora em outro lugar, e o sistema
    operacional sabe limpá-lo sozinho.
    """
    p = os.path.join(tempfile.gettempdir(), 'bdgdcase_panorama')
    os.makedirs(p, exist_ok=True)
    return p


def _chave(gdb, tolerancia):
    """Impressão digital da base, para reconhecer que já se leu esta mesma.

    O caminho não basta: uma BDGD nova baixada por cima da antiga tem o mesmo
    nome de pasta, e o mapa antigo passaria por atual. Tamanho e data de cada
    arquivo denunciam a troca.
    """
    h = hashlib.sha256()
    h.update(('%s|formato=%d|tol=%.8f'
              % (os.path.abspath(gdb), FORMATO, tolerancia)).encode('utf-8',
                                                                    'replace'))
    try:
        for e in sorted(os.scandir(gdb), key=lambda e: e.name):
            if e.is_file():
                st = e.stat()
                h.update(('|%s:%d:%d' % (e.name, st.st_size, int(st.st_mtime)))
                         .encode('utf-8', 'replace'))
    except OSError:
        # Base ilegível não é caso de cache; a leitura adiante dará o erro de
        # verdade, com a mensagem do driver.
        pass
    return h.hexdigest()[:32]


def _ler_cache(chave):
    """O panorama já guardado desta base, ou `None`."""
    import numpy as np

    caminho = os.path.join(_pasta_cache(), '%s.npz' % chave)
    if not os.path.isfile(caminho):
        return None
    try:
        with np.load(caminho, allow_pickle=False) as z:
            return Panorama([str(c) for c in z['alimentadores']],
                            z['coords'], z['partes'], z['indice'],
                            json.loads(str(z['info'])))
    except Exception:
        # Cache corrompido ou de um formato que já não se lê: apaga e relê a
        # base. Ler pela metade seria pior que reler inteiro.
        try:
            os.remove(caminho)
        except OSError:
            pass
        return None


def _gravar_cache(chave, pan):
    """Guarda o panorama para a próxima abertura desta base."""
    import numpy as np

    caminho = os.path.join(_pasta_cache(), '%s.npz' % chave)
    try:
        np.savez_compressed(
            caminho,
            alimentadores=np.array(pan.alimentadores, dtype=np.str_),
            coords=pan.coords, partes=pan.partes, indice=pan.indice,
            info=np.array(json.dumps(pan.info)))
    except Exception:
        # Sem espaço, sem permissão: perde-se a velocidade da próxima vez, e
        # nada mais. Não vale derrubar um mapa que já está pronto.
        pass
