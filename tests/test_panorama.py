# -*- coding: utf-8 -*-
"""O mapa que se olha antes de escolher o alimentador.

`api.listar` devolve oitocentos códigos, e um código não diz onde o alimentador
passa. `extracao.panorama` lê a geometria da média tensão da base e devolve o
traçado de todos, para que a escolha seja um clique no mapa e não uma tentativa
de extração de minutos.

O que estes testes defendem:

- o traçado sai **contínuo**. A primeira versão amostrava um vão a cada dez, e o
  alimentador saía tracejado — que se lê como rede partida, e é a conclusão
  errada. Hoje `line_merge` costura e `simplify` enxuga, sem descartar trecho;
- a ficha mede a rede inteira, e não o desenho reduzido;
- uma base projetada volta em graus;
- o clique acerta o **traço**, e não só os vértices — depois de simplificar, um
  tronco reto pode ter meio quilômetro entre dois pontos;
- a leitura em blocos dá o mesmo que a de uma vez.

Sem Tkinter e sem matplotlib: nada aqui desenha. A outra ponta — o que acontece
quando alguém clica — está em `test_panorama_aba.py`.

A fixture é BDGD de verdade: `tests/dados/URB13/SSDMT_URB13.gpkg`, 1.611 vãos de
média tensão em EPSG:4674.
"""
import os

import pytest

np = pytest.importorskip('numpy')
pytest.importorskip('pyogrio')
shapely = pytest.importorskip('shapely')

from bdgdcase.extracao.panorama import (ORCAMENTO, Panorama, _chave, _costurar,
                                        _gravar_cache, _ler_cache, construir)

RAIZ = os.path.dirname(os.path.abspath(__file__))
SSDMT = os.path.join(RAIZ, 'dados', 'URB13', 'SSDMT_URB13.gpkg')

pytestmark = pytest.mark.skipif(
    not os.path.isfile(SSDMT),
    reason='exige a fixture de alimentador em tests/dados/')


@pytest.fixture(scope='module')
def pan():
    """O panorama da fixture, lido do arquivo e sem passar pelo cache."""
    return construir(SSDMT, cache=False)


def _cadeia(n, passo=0.0005, x=-51.0, y=-27.0):
    """`n` vãos em linha reta, como a BDGD publica um tronco: poste a poste."""
    ys = y - passo * np.arange(n + 1)
    pontos = np.stack([np.full(n + 1, x), ys], axis=1)
    return np.stack([pontos[:-1], pontos[1:]], axis=1)


# ── A costura, que é o coração do módulo ─────────────────────────────────────

def test_um_tronco_reto_vira_um_traco_so(pan):
    """Vinte vãos em linha viram UMA polilinha de dois pontos.

    É a diferença entre o mapa de hoje e o tracejado de antes: nada é
    descartado, e ainda assim sobram dois vértices no lugar de vinte e um.
    """
    pontas = _cadeia(20)
    indice = np.zeros(20, dtype=np.int32)
    coords, partes, dono = _costurar(pontas, indice, 1, 0.0001)

    assert len(dono) == 1, 'o tronco tem de sair inteiro, num traço só'
    assert len(coords) == 2, 'reta nao precisa de vertice no meio'
    assert list(partes) == [0, 2]


def test_a_costura_nao_perde_comprimento(pan):
    """Simplificar tira vértice, não rede.

    Medido na fixture: o traço costurado e simplificado tem de continuar com
    praticamente o mesmo comprimento do que entrou. Perder comprimento aqui
    seria perder trecho, e trecho sumido no mapa é rede que parece não existir.
    """
    import geopandas as gpd

    bruto = shapely.line_merge(shapely.multilinestrings(
        shapely.get_parts(gpd.read_file(SSDMT).geometry.values)))
    inteiro = shapely.length(bruto)

    saiu = sum(shapely.length(shapely.linestrings(linha))
               for linha in pan.linhas() if len(linha) >= 2)
    assert saiu == pytest.approx(inteiro, rel=0.002)


def test_simplificar_corta_vertice_de_sobra(pan):
    """O ganho que paga a costura: menos pontos para o mesmo desenho."""
    import geopandas as gpd

    origem = gpd.read_file(SSDMT)
    bruto = len(shapely.get_coordinates(origem.geometry.values))
    assert pan.n_vertices < bruto / 2, (
        'sem reducao, uma base estadual nao desenha: %d de %d'
        % (pan.n_vertices, bruto))
    assert pan.n_vertices > 0


def test_o_orcamento_e_respeitado_quando_a_base_e_grande():
    """Base maior que o previsto afrouxa a tolerância em vez de travar a tela.

    Mil troncos com vértices que uma tolerância fina preservaria: a segunda
    passada tem de trazer o total para baixo do teto.
    """
    ruido = []
    for _ in range(400):
        c = _cadeia(200, x=-51.0 + np.random.rand())
        c[:, :, 0] += np.random.normal(0, 0.002, c[:, :, 0].shape)
        ruido.append(c)
    pontas = np.concatenate(ruido)
    indice = np.repeat(np.arange(400, dtype=np.int32), 200)

    coords, _partes, dono = _costurar(pontas, indice, 400, 1e-9)
    assert len(coords) <= ORCAMENTO
    assert len(np.unique(dono)) == 400, 'nenhum alimentador pode sumir'


# ── O que a base traz ────────────────────────────────────────────────────────

def test_le_o_alimentador_da_fixture(pan):
    """Um `.gpkg` de um alimentador só tem de dar um alimentador só."""
    assert pan.alimentadores == ['URB13']
    assert len(pan) > 0


def test_o_tracado_e_o_da_geometria_de_origem(pan):
    """Os traços caem dentro da extensão do que se leu.

    É o teste que pega a troca de eixo e a projeção esquecida: lat no lugar de
    lon põe o alimentador no oceano, e nada mais no programa reclama.
    """
    import geopandas as gpd

    o, s, l, n = gpd.read_file(SSDMT).total_bounds
    assert pan.coords[:, 0].min() >= o - 1e-9
    assert pan.coords[:, 0].max() <= l + 1e-9
    assert pan.coords[:, 1].min() >= s - 1e-9
    assert pan.coords[:, 1].max() <= n + 1e-9


def test_cada_traco_tem_dono_e_limites(pan):
    """`indice` e `partes` têm de descrever exatamente `coords`."""
    assert pan.indice.shape == (len(pan),)
    assert pan.partes.shape == (len(pan) + 1,)
    assert pan.partes[0] == 0 and pan.partes[-1] == pan.n_vertices
    assert np.all(np.diff(pan.partes) >= 2), 'traço de um ponto só não é traço'
    assert pan.indice.min() >= 0
    assert pan.indice.max() < len(pan.alimentadores)
    assert len(pan.linhas()) == len(pan)


def test_a_ficha_mede_a_rede_inteira_e_nao_o_desenho(pan):
    """`km` e `n_trechos` são do alimentador, não do que sobrou de traço.

    Se a simplificação encolhesse a ficha junto com o desenho, o mapa mentiria
    sobre o tamanho do alimentador — e é pelo tamanho que se escolhe.
    """
    ficha = pan.info['URB13']
    assert ficha['n_trechos'] == 1611
    assert ficha['km'] == pytest.approx(190, abs=5)
    assert ficha['nome'] == 'URB13'


def test_a_leitura_em_blocos_da_o_mesmo_que_a_de_uma_vez(pan, monkeypatch):
    """Uma base estadual não cabe na memória, e é lida em fatias.

    Cada fatia unifica os códigos por conta própria e depois eles são
    remapeados para o índice global — se esse remapeamento errasse, os trechos
    de um alimentador sairiam com a cor e o nome de outro, e nada acusaria.
    A fixture cabe num bloco só; aqui o bloco encolhe para forçar o caminho.
    """
    monkeypatch.setattr('bdgdcase.extracao.panorama.BLOCO', 500)
    fatiado = construir(SSDMT, cache=False)

    assert fatiado.alimentadores == pan.alimentadores
    assert np.array_equal(fatiado.indice, pan.indice)
    assert np.allclose(fatiado.coords, pan.coords)
    assert fatiado.info == pan.info


def test_dois_alimentadores_nao_trocam_de_trecho(tmp_path):
    """Cada traço tem de sair com o dono que a base lhe deu.

    Com um alimentador só, um remapeamento errado passaria despercebido:
    qualquer índice cai no único nome que existe.
    """
    import geopandas as gpd

    origem = gpd.read_file(SSDMT)
    metade = len(origem) // 2
    origem.loc[origem.index[:metade], 'CTMT'] = 'OUTRO'
    juntos = tmp_path / 'dois.gpkg'
    origem.to_file(juntos, driver='GPKG', layer='SSDMT')

    pan = construir(str(juntos), cache=False)
    assert pan.alimentadores == ['OUTRO', 'URB13']
    assert pan.info['OUTRO']['n_trechos'] == metade
    assert pan.info['URB13']['n_trechos'] == len(origem) - metade

    for i, cod in enumerate(pan.alimentadores):
        meus = np.concatenate(pan.linhas(np.flatnonzero(pan.indice == i)))
        esperado = origem[origem['CTMT'] == cod].total_bounds
        assert meus[:, 1].max() <= esperado[3] + 1e-9
        assert meus[:, 1].min() >= esperado[1] - 1e-9


def test_base_projetada_sai_em_graus(tmp_path):
    """Uma base em UTM tem de voltar em lon/lat, ou vai parar no golfo da Guiné.

    É a única normalização de CRS do pacote, e ela não existia: metros entrando
    onde se esperam graus não levantam erro nenhum — o alimentador só aparece
    em outro continente, sobre um azulejo de oceano.
    """
    import geopandas as gpd

    projetado = tmp_path / 'utm.gpkg'
    # A camada tem de se chamar SSDMT: é o nome que diz onde mora a média
    # tensão, e sem ele o `.gpkg` herdaria o nome do arquivo.
    gpd.read_file(SSDMT).to_crs(31982).to_file(projetado, driver='GPKG',
                                               layer='SSDMT')

    pan = construir(str(projetado), cache=False)
    assert pan.alimentadores == ['URB13']
    assert -75.0 < pan.coords[:, 0].mean() < -30.0
    assert -35.0 < pan.coords[:, 1].mean() < 6.0


# ── O clique ─────────────────────────────────────────────────────────────────

def test_o_clique_acerta_o_traco_e_nao_so_o_vertice():
    """No meio de um tronco reto não há vértice nenhum — e ainda assim acerta.

    Depois de simplificar, um tronco de meio quilômetro tem dois pontos: um em
    cada ponta. Medir a distância só até os vértices faria o clique bem em cima
    do traço não encontrar nada, que é o defeito mais irritante possível num
    mapa de escolher.
    """
    pan = Panorama(['TRONCO'],
                   np.array([[-51.0, -27.0], [-51.0, -27.5]]),
                   np.array([0, 2]), np.array([0], dtype=np.int32))

    # Bem no meio, longe das duas pontas.
    assert pan.mais_proximo(-51.0, -27.25, raio=0.001) == 'TRONCO'
    # Ao lado do traço, dentro e fora da tolerância.
    assert pan.mais_proximo(-50.999, -27.25, raio=0.01) == 'TRONCO'
    assert pan.mais_proximo(-50.9, -27.25, raio=0.01) is None


def test_clique_longe_desmarca(pan):
    """O raio é o que separa escolher de limpar a seleção."""
    _o, _s, leste, norte = pan.caixa()
    assert pan.mais_proximo(leste + 0.05, norte + 0.05, raio=0.5) == 'URB13'
    assert pan.mais_proximo(leste + 0.05, norte + 0.05, raio=0.01) is None
    assert pan.mais_proximo(0.0, 0.0, raio=0.5) is None


def test_a_caixa_enquadra_o_alimentador(pan):
    """A caixa de um alimentador é a caixa de tudo, quando ele é o único."""
    assert pan.caixa('URB13') == pan.caixa()
    assert pan.caixa('NAO_EXISTE') is None


# ── O cache ──────────────────────────────────────────────────────────────────

def test_o_cache_devolve_o_que_guardou(pan, monkeypatch, tmp_path):
    """Ida e volta pelo disco preserva traçado, dono e ficha."""
    monkeypatch.setattr('bdgdcase.extracao.panorama._pasta_cache',
                        lambda: str(tmp_path))
    chave = 'teste'
    assert _ler_cache(chave) is None
    _gravar_cache(chave, pan)

    volta = _ler_cache(chave)
    assert volta.alimentadores == pan.alimentadores
    assert np.array_equal(volta.indice, pan.indice)
    assert np.array_equal(volta.partes, pan.partes)
    assert np.allclose(volta.coords, pan.coords)
    assert volta.info == pan.info


def test_a_chave_do_cache_muda_quando_a_base_muda(tmp_path):
    """Baixar uma BDGD nova por cima da antiga não pode devolver o mapa velho.

    O caminho continua o mesmo; o que denuncia a troca é o tamanho e a data
    dos arquivos.
    """
    base = tmp_path / 'BDGD.gdb'
    base.mkdir()
    (base / 'a00000001.gdbtable').write_bytes(b'x' * 10)
    antes = _chave(str(base), 0.0001)

    (base / 'a00000001.gdbtable').write_bytes(b'x' * 20)
    assert _chave(str(base), 0.0001) != antes


def test_panorama_vazio_nao_explode():
    """Base sem média tensão dá mapa vazio, e não traceback."""
    vazio = Panorama([], np.zeros((0, 2)), np.zeros(1, dtype=np.int64),
                     np.zeros(0, dtype=np.int32))
    assert len(vazio) == 0
    assert vazio.n_vertices == 0
    assert vazio.caixa() is None
    assert vazio.linhas() == []
    assert vazio.mais_proximo(-50.0, -27.0, raio=1.0) is None
