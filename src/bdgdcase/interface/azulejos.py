from __future__ import annotations

import math
import os
import tempfile
import threading
import urllib.request

from bdgdcase import __version__ as _VERSAO

__all__ = ['FUNDOS', 'buscar', 'credito', 'lonlat_para_tile', 'merc', 'merc_inv']

#: Raio da esfera de Web Mercator (EPSG:3857), em metros.
RAIO = 6378137.0

#: Um mosaico de até 12×10 azulejos. O teto é o que fixa o zoom possível e, com
#: ele, a resolução: com 24 azulejos o alimentador de exemplo fecha em zoom 17;
#: com 120, em zoom 19 — quatro vezes mais detalhe linear. O custo é a primeira
#: busca; depois tudo sai do cache.
MAX_AZULEJOS = 120

#: Até onde se tenta ampliar. É um teto de tentativa, e não de resultado: o
#: provedor pode não ter imagem nesse nível *nesta área*, e aí :func:`buscar`
#: desce sozinha até onde ele tem. Ver `_zoom_servido`.
ZOOM_MAX = 21

#: Piso da descida. Abaixo disso não se procura mais: se nem em zoom 12 há
#: imagem, o problema não é o nível.
ZOOM_MIN_UTIL = 12

LADO = 256


_AGENTE = 'bdgdcase/%s (+https://github.com/constantinipmc/bdgdcase)' % _VERSAO

FUNDOS = {
    'nenhum': {
        'rotulo': 'sem fundo',
        'camadas': (),
        'credito': '',
    },
    'satelite': {
        'rotulo': 'satélite',
        'camadas': ('https://server.arcgisonline.com/ArcGIS/rest/services/'
                    'World_Imagery/MapServer/tile/{z}/{y}/{x}',),
        'credito': 'Imagem: Esri, Maxar, Earthstar Geographics',
    },
    'hibrido': {
        # Satélite com os rótulos por cima: é o que responde "que rua é essa"
        # sem perder o que está no chão.
        'rotulo': 'híbrido',
        'camadas': ('https://server.arcgisonline.com/ArcGIS/rest/services/'
                    'World_Imagery/MapServer/tile/{z}/{y}/{x}',
                    'https://server.arcgisonline.com/ArcGIS/rest/services/'
                    'Reference/World_Boundaries_and_Places/MapServer/tile/'
                    '{z}/{y}/{x}'),
        'credito': 'Imagem: Esri, Maxar · Referência: Esri',
    },
    'claro': {
        'rotulo': 'claro',
        'camadas': ('https://server.arcgisonline.com/ArcGIS/rest/services/'
                    'Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',),
        'credito': 'Esri, HERE, Garmin, © OpenStreetMap contributors',
    },
    'ruas': {
        'rotulo': 'ruas',
        'camadas': ('https://tile.openstreetmap.org/{z}/{x}/{y}.png',),
        'credito': '© OpenStreetMap contributors',
    },
}

_cache_memoria = {}
_trava = threading.Lock()


def credito(fundo):
    """A atribuição exigida pelo provedor daquele fundo.

    Aparece no canto do mapa. Não é enfeite: os provedores de mapa base pedem
    crédito visível como condição de uso, e um mapa exportado sem ele é um mapa
    que não se pode publicar.
    """
    return FUNDOS.get(fundo, FUNDOS['nenhum'])['credito']


# ── Projeção ─────────────────────────────────────────────────────────────────

def merc(lon, lat):
    """Graus decimais → Web Mercator (metros)."""
    lat = max(-85.05, min(85.05, float(lat)))
    return (RAIO * math.radians(float(lon)),
            RAIO * math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0)))


def merc_inv(x, y):
    """Web Mercator (metros) → graus decimais."""
    return (math.degrees(float(x) / RAIO),
            math.degrees(2.0 * math.atan(math.exp(float(y) / RAIO)) - math.pi / 2))


def lonlat_para_tile(lon, lat, z):
    """(lon, lat) em graus → coordenada de azulejo fracionária no zoom `z`."""
    n = 2.0 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(max(-85.05, min(85.05, lat)))
    y = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n
    return x, y


def tile_para_lonlat(x, y, z):
    """Canto noroeste do azulejo (x, y) → (lon, lat) em graus."""
    n = 2.0 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


def escolher_zoom(oeste, sul, leste, norte, max_azulejos=MAX_AZULEJOS):
    """O maior zoom cujo recorte ainda cabe no orçamento de azulejos.

    Zoom alto é o que dá detalhe; o teto existe porque cada nível dobra o lado
    do mosaico, e a conta estoura depressa.
    """
    for z in range(ZOOM_MAX, 0, -1):
        x0, y0 = lonlat_para_tile(oeste, norte, z)
        x1, y1 = lonlat_para_tile(leste, sul, z)
        n = (math.floor(x1) - math.floor(x0) + 1) * \
            (math.floor(y1) - math.floor(y0) + 1)
        if n <= max_azulejos:
            return z
    return 1


_SUBSTITUTOS = set()

_ZOOM_TETO = {}
ZOOM_REFERENCIA = 12


def _digital(img):
    """Impressão digital de um azulejo, para reconhecer repetição."""
    import hashlib

    return hashlib.sha256(img.tobytes()).hexdigest()


def _substituto(modelo, z, cantos, tempo_limite):
    """O provedor está devolvendo o azulejo de "sem imagem" neste nível?

    `cantos` são duas coordenadas distintas do mosaico. Dois azulejos de
    satélite distintos não saem iguais; se saírem, é o substituto.
    """
    try:
        primeiro = _um_azulejo(modelo, z, cantos[0][0], cantos[0][1],
                               tempo_limite)
    except Exception:
        return False              # sem rede não é o mesmo que sem imagem

    d0 = _digital(primeiro)
    if d0 in _SUBSTITUTOS:
        return True
    if len(cantos) < 2:
        return False              # um azulejo só não dá para comparar

    try:
        segundo = _um_azulejo(modelo, z, cantos[1][0], cantos[1][1],
                              tempo_limite)
    except Exception:
        return False
    if _digital(segundo) != d0:
        return False

    _SUBSTITUTOS.add(d0)
    return True


def _zoom_servido(modelo, z, tx0, ty0, tx1, ty1, tempo_limite):
    """O maior zoom ≤ `z` em que o provedor tem imagem nesta área."""
    desloc = max(0, z - ZOOM_REFERENCIA)
    chave = (modelo, tx0 >> desloc, ty0 >> desloc)
    teto = _ZOOM_TETO.get(chave)
    if teto is not None and teto < z:
        # Já se sabe que acima daqui não há imagem; ir direto poupa a sondagem
        # e, com ela, duas idas à rede por movimento do mapa.
        desceu = z - teto
        z = teto
        tx0, ty0 = tx0 >> desceu, ty0 >> desceu
        tx1, ty1 = tx1 >> desceu, ty1 >> desceu

    partiu_de = z
    while z > ZOOM_MIN_UTIL:
        # Dois cantos opostos do recorte, que é o par mais distante que se tem
        # em mão — e distância ajuda: azulejos vizinhos de mar aberto podem até
        # coincidir, cantos opostos de um recorte não.
        cantos = [(tx0, ty0)] if (tx0, ty0) == (tx1, ty1) else [(tx0, ty0),
                                                                (tx1, ty1)]
        if not _substituto(modelo, z, cantos, tempo_limite):
            break
        z -= 1
        tx0, ty0, tx1, ty1 = tx0 >> 1, ty0 >> 1, tx1 >> 1, ty1 >> 1

    if z < partiu_de:
        # Só aqui se aprendeu alguma coisa: houve substituto acima de `z`.
        _ZOOM_TETO[chave] = z
    return z


def _pasta_cache():
    """A pasta de cache dos azulejos, criada se ainda não existir.

    Fica no temporário do sistema, e não dentro do projeto: são megabytes de
    imagem que não pertencem ao caso e que o sistema operacional sabe limpar
    sozinho.
    """
    p = os.path.join(tempfile.gettempdir(), 'bdgdcase_azulejos')
    os.makedirs(p, exist_ok=True)
    return p


def _um_azulejo(modelo, z, x, y, tempo_limite):
    """Um azulejo como imagem RGBA, de memória, de disco ou da rede."""
    chave = (modelo, z, x, y)
    with _trava:
        if chave in _cache_memoria:
            return _cache_memoria[chave]

    from PIL import Image        # vem junto com o matplotlib

    nome = '%s_%d_%d_%d.png' % (abs(hash(modelo)) % 10 ** 8, z, x, y)
    caminho = os.path.join(_pasta_cache(), nome)

    img = None
    if os.path.isfile(caminho):
        try:
            img = Image.open(caminho).convert('RGBA')
        except Exception:
            img = None            # cache corrompido não impede de buscar de novo

    if img is None:
        url = modelo.format(z=z, x=x, y=y)
        pedido = urllib.request.Request(url, headers={'User-Agent': _AGENTE})
        with urllib.request.urlopen(pedido, timeout=tempo_limite) as resposta:
            dados = resposta.read()
        with open(caminho, 'wb') as f:
            f.write(dados)
        import io as _io
        img = Image.open(_io.BytesIO(dados)).convert('RGBA')

    with _trava:
        _cache_memoria[chave] = img
    return img


def _azulejo_do_pai(modelo, z, x, y, tempo_limite, subidas=3):
    """O quadrante correspondente de um azulejo de zoom menor, ampliado.

    Azulejo que não vem deixa um furo, e furo sobre figura clara aparece como
    um quadrado branco no meio do bairro — que se lê como dado, e não como
    falha de rede. O pai cobre o furo com a mesma imagem em resolução menor:
    borrado, mas contínuo e verdadeiro. E costuma já estar em cache, porque é
    o azulejo que se via antes de ampliar.
    """
    from PIL import Image

    for salto in range(1, subidas + 1):
        if z - salto < 1:
            break
        try:
            pai = _um_azulejo(modelo, z - salto, x >> salto, y >> salto,
                              tempo_limite)
        except Exception:
            continue
        fatia = LADO >> salto            # lado do quadrante, em pixels
        px = (x - ((x >> salto) << salto)) * fatia
        py = (y - ((y >> salto) << salto)) * fatia
        return pai.crop((px, py, px + fatia, py + fatia)).resize(
            (LADO, LADO), Image.BILINEAR)
    return None


def buscar(oeste, sul, leste, norte, fundo='hibrido', tempo_limite=6.0,
           max_azulejos=MAX_AZULEJOS, trabalhadores=24):
    """Mosaico para a janela pedida, em graus.

    Devolve ``(imagem_rgba, (x_oeste, x_leste, y_sul, y_norte))``, com a
    extensão em **Web Mercator (metros)** — é assim que ela entra no `imshow`
    de um eixo que também esteja em Mercator, e é o que faz a imagem coincidir
    com a rede em qualquer ampliação.

    ``None`` quando não há fundo a desenhar: sem internet, servidor fora, ou
    ``fundo='nenhum'``.
    """
    conf = FUNDOS.get(fundo)
    if not conf or not conf['camadas']:
        return None

    from concurrent.futures import ThreadPoolExecutor

    from PIL import Image

    z = escolher_zoom(oeste, sul, leste, norte, max_azulejos)

    def _recorte(nivel):
        """Os índices dos azulejos que cobrem a área pedida, neste zoom."""
        a, b = lonlat_para_tile(oeste, norte, nivel)
        c, d = lonlat_para_tile(leste, sul, nivel)
        return (int(math.floor(a)), int(math.floor(b)),
                int(math.floor(c)), int(math.floor(d)))

    # Desce até onde o provedor de fato tem imagem. Sem isto, ampliar demais
    # trocava o bairro pelo aviso cinza dele.
    tx0, ty0, tx1, ty1 = _recorte(z)
    servido = _zoom_servido(conf['camadas'][0], z, tx0, ty0, tx1, ty1,
                            tempo_limite)
    if servido != z:
        z = servido
        tx0, ty0, tx1, ty1 = _recorte(z)
    largura, altura = (tx1 - tx0 + 1) * LADO, (ty1 - ty0 + 1) * LADO

    coords = [(tx, ty) for tx in range(tx0, tx1 + 1)
              for ty in range(ty0, ty1 + 1)]

    mosaico = Image.new('RGBA', (largura, altura))
    for modelo in conf['camadas']:
        camada = Image.new('RGBA', (largura, altura))
        algum = False

        # Em paralelo, e com folga de trabalhadores: a espera é de rede, não
        # de CPU. Medido num mosaico de 120 azulejos: 17 s com 8 trabalhadores,
        # 6,7 s com 24. Depois disso tudo sai do cache em milissegundos.
        def _pegar(tc):
            """Baixa um azulejo, devolvendo o erro em vez de propagá-lo.

            Roda em paralelo com dezenas de irmãos, e um azulejo que não vem
            não pode derrubar o mapa inteiro: falta um quadradinho, o resto
            desenha.
            """
            try:
                return tc, _um_azulejo(modelo, z, tc[0], tc[1], tempo_limite)
            except Exception:
                # Erro na tela seria pior que buraco; buraco coberto pelo pai é
                # melhor que os dois.
                return tc, _azulejo_do_pai(modelo, z, tc[0], tc[1],
                                           tempo_limite)

        with ThreadPoolExecutor(max_workers=trabalhadores) as pool:
            for (tx, ty), peca in pool.map(_pegar, coords):
                if peca is None:
                    continue
                camada.paste(peca, ((tx - tx0) * LADO, (ty - ty0) * LADO))
                algum = True

        if not algum and modelo is conf['camadas'][0]:
            return None            # nem a camada de base veio: sem internet
        mosaico = Image.alpha_composite(mosaico, camada)

    import numpy as np
    lon_o, lat_n = tile_para_lonlat(tx0, ty0, z)
    lon_l, lat_s = tile_para_lonlat(tx1 + 1, ty1 + 1, z)
    x_o, y_n = merc(lon_o, lat_n)
    x_l, y_s = merc(lon_l, lat_s)
    return np.asarray(mosaico), (x_o, x_l, y_s, y_n)
