# -*- coding: utf-8 -*-
"""O mapa: a geometria, que é testável, e a janela, que dá para montar.

`_Geometria` é a parte que decide o que aparece e de que cor — quais barras têm
lugar no mapa, qual segmento liga o quê, qual tensão vale para uma barra de três
fases. Nada disso toca em widget, e é o que este arquivo cobre de verdade.

A janela em si recebe um teste de fumaça: monta, desenha, anda no tempo. Não
prova que está bonita, prova que não quebra — que é o que um teste pode fazer
por uma interface gráfica.
"""
import math

import pytest

pytest.importorskip('opendssdirect',
                    reason='o OpenDSS nao carregou (instalacao incompleta)')
np = pytest.importorskip('numpy')

from bdgdcase.interface.geometria import (  # noqa: E402
    PU_MORTO,
    V_LIMITES,
    _Geometria,
    _hhmm)
from bdgdcase.solucao import rodar_dia  # noqa: E402
from test_conversao import ESPERADO, falta  # noqa: E402

pytestmark = [pytest.mark.solver, pytest.mark.dados,
              pytest.mark.skipif(falta, reason='caso de referência ausente')]


def _secoes(blocos):
    """Títulos de seção do inspetor, para afirmar sobre estrutura."""
    return {b[1] for b in blocos if b[0] == 'secao'}


def _rotulos(blocos):
    return {b[1].strip() for b in blocos if b[0] == 'par'}


@pytest.fixture(scope='module')
def resultado():
    return rodar_dia(ESPERADO, detalhado=True)


@pytest.fixture(scope='module')
def geo(resultado):
    return _Geometria(resultado)


# ── A coleta detalhada ───────────────────────────────────────────────────────

def test_o_detalhado_e_opcional(resultado):
    """Sem `detalhado`, o resultado não carrega os 96 instantes da rede."""
    magro = rodar_dia(ESPERADO)
    assert 'v_no_pu' not in magro
    assert 'v_no_pu' in resultado


def test_as_series_por_no_tem_a_forma_certa(resultado):
    v = resultado['v_no_pu']
    assert v.shape == (resultado['passos'], len(resultado['nos']))
    assert v.dtype == np.float32          # 96 instantes de rede urbana em 4 bytes
    assert len(resultado['no_para_barra']) == len(resultado['nos'])


def test_as_correntes_acompanham_as_linhas(resultado):
    amp = resultado['amp_linha']
    assert amp.shape == (resultado['passos'], len(resultado['linhas']))
    assert (amp >= 0).all()
    assert len(resultado['norm_amps']) == len(resultado['linhas'])


def test_as_coordenadas_cobrem_quase_tudo(resultado):
    coord = resultado['coordenadas']
    com = sum(1 for b in resultado['barras'] if b.lower() in coord)
    assert com > 0.9 * len(resultado['barras'])


def test_o_agregado_do_dia_continua_igual(resultado):
    """Coletar detalhe não pode mudar o que a simulação conclui."""
    magro = rodar_dia(ESPERADO)
    assert resultado['p_kw'] == magro['p_kw']
    assert resultado['p_max_kw'] == magro['p_max_kw']


# ── A geometria ──────────────────────────────────────────────────────────────

def test_so_entram_barras_com_coordenada(geo, resultado):
    coord = {k.lower() for k in resultado['coordenadas']}
    assert all(b.lower() in coord for b in geo.barras)
    assert len(geo.xy) == len(geo.barras)


def test_segmentos_tem_as_duas_pontas_no_mapa(geo):
    assert len(geo.segmentos) > 0
    assert geo.segmentos.shape[1:] == (2, 2)
    assert len(geo.idx_linha) == len(geo.segmentos)


def test_a_tensao_da_barra_e_a_pior_fase(geo, resultado):
    """Média esconderia desequilíbrio, que é justamente o que se procura."""
    passo = 74
    for j, nome in enumerate(geo.barras):
        fases = [float(resultado['v_no_pu'][passo, k])
                 for k, ib in enumerate(resultado['no_para_barra'])
                 if resultado['barras'][ib] == nome]
        vivas = [v for v in fases if v > PU_MORTO]
        if vivas:
            assert geo.tensao(passo)[j] == pytest.approx(min(vivas), abs=1e-5)
        else:
            assert math.isnan(float(geo.tensao(passo)[j]))


def test_barra_sem_tensao_vira_nan(geo):
    """NaN é o que faz o matplotlib pintar de cinza em vez de vermelho.

    Barra morta não é barra em subtensão, e confundir as duas faria um trecho
    desligado no cadastro parecer um problema de tensão.
    """
    v = geo.tensao(74)
    assert np.isnan(v).any()
    assert (~np.isnan(v)).sum() > 0


def test_o_carregamento_e_percentual_da_ampacidade(geo, resultado):
    c = geo.carregamento(74)
    assert len(c) == len(geo.segmentos)
    assert (c >= 0).all()
    k = int(geo.idx_linha[0])
    esperado = 100 * resultado['amp_linha'][74, k] / resultado['norm_amps'][k]
    assert c[0] == pytest.approx(esperado, rel=1e-4)


def test_o_clique_acha_a_posicao_certa(geo):
    for j in (0, len(geo.barras) // 2, len(geo.barras) - 1):
        x, y = geo.xy[j]
        achada = geo.barra_mais_proxima(x, y)
        assert (geo.xy[achada] == geo.xy[j]).all()
        # deslocamento bem menor que o espaçamento entre barras
        assert geo.barra_mais_proxima(x + 1e-7, y - 1e-7) == achada


def test_clicar_de_novo_percorre_as_barras_empilhadas(geo):
    """Um terço das posições tem mais de uma barra; sem isto, ficam presas."""
    empilhada = next((j for j in range(len(geo.barras))
                      if geo.empilhadas(j) > 1), None)
    if empilhada is None:
        pytest.skip('este caso não tem barras no mesmo ponto')
    x, y = geo.xy[empilhada]
    n = geo.empilhadas(empilhada)

    vistas, atual = [], None
    for _ in range(n):
        atual = geo.barra_mais_proxima(x, y, atual)
        vistas.append(atual)
    assert len(set(vistas)) == n              # passou por todas
    assert geo.barra_mais_proxima(x, y, atual) == vistas[0]   # e volta ao começo


def test_o_detalhe_avisa_quando_ha_barras_empilhadas(geo):
    j = next((j for j in range(len(geo.barras)) if geo.empilhadas(j) > 1), None)
    if j is None:
        pytest.skip('este caso não tem barras no mesmo ponto')
    assert 'barras neste ponto' in geo.detalhe(j, 74)


def test_o_inventario_acompanha_o_circuito(resultado):
    inv = resultado['inventario']
    assert len(inv['cargas']) > 0 and len(inv['trafos']) > 0
    assert all(c['bus'] and c['kw'] >= 0 for c in inv['cargas'])
    assert sum(t['kva'] for t in inv['trafos']) > 0
    assert len(inv['tensoes_base']) > 0


def test_a_classe_da_carga_nao_confunde_numero_com_classe(resultado):
    """`ucmt_2542911_0` não tem classe no nome; ler o 2º pedaço às cegas erraria."""
    classes = {c['classe'] for c in resultado['inventario']['cargas']}
    assert classes <= {'RES', 'COM', 'IND', 'RUR', 'PP', 'SP', 'CPR', 'MT',
                       'IP', '—'}
    assert not any(c.isdigit() for c in classes)


def test_os_elementos_estao_indexados_por_barra(geo, resultado):
    total = sum(len(v['cargas']) for v in geo.por_barra.values())
    assert total == len(resultado['inventario']['cargas'])


def test_o_painel_do_ponto_mostra_as_unidades(geo):
    alvo = next((j for j, b in enumerate(geo.barras)
                 if len(geo.por_barra.get(b, {'cargas': []})['cargas']) > 2), None)
    if alvo is None:
        pytest.skip('nenhum ponto com várias unidades')
    secoes = _secoes(geo.blocos(alvo, 74))
    assert 'Unidades consumidoras' in secoes
    assert 'demanda nominal' in _rotulos(geo.blocos(alvo, 74))


def test_ponto_sem_unidade_diz_isso(geo):
    alvo = next((j for j, b in enumerate(geo.barras) if b not in geo.por_barra),
                None)
    if alvo is None:
        pytest.skip('todo ponto tem elemento')
    assert 'Nenhum elemento' in geo.detalhe(alvo, 74)


# ── Camadas e realce ─────────────────────────────────────────────────────────

def test_os_trechos_sao_classificados_por_tipo(geo):
    from bdgdcase.interface.mapa import CAMADAS_ROTULO
    tipos = set(geo.tipo_trecho)
    assert tipos <= set(CAMADAS_ROTULO)
    assert sum(len(v) for v in geo.indice_por_tipo.values()) == len(geo.segmentos)
    assert len(geo.indice_por_tipo['mt']) > 0
    assert len(geo.indice_por_tipo['bt']) > 0


def test_os_marcadores_tem_posicao(geo):
    assert len(geo.trafo_xy) == len(geo.trafos) > 0
    assert len(geo.gd_xy) == len(geo.gd)
    assert geo.trafo_xy.shape[1] == 2


def test_o_circuito_bt_de_um_trafo_para_no_proximo(geo):
    """O caminhamento anda só por BT, então não atravessa outro trafo.

    A prova é aritmética: se os circuitos se sobrepusessem, a soma dos trechos
    passaria do total de trechos BT; se deixassem órfãos, ficaria abaixo.
    """
    total = sum(len(geo.circuito_bt(t['nome'])[1]) for t in geo.trafos)
    assert total == len(geo.indice_por_tipo['bt'])

    vistos = set()
    for t in geo.trafos:
        _, trechos = geo.circuito_bt(t['nome'])
        assert not (set(trechos) & vistos), 'circuitos se sobrepõem'
        vistos.update(trechos)


def test_o_circuito_bt_comeca_no_secundario(geo):
    alvo = max(geo.trafos, key=lambda t: len(geo.circuito_bt(t['nome'])[1]))
    pontos, trechos = geo.circuito_bt(alvo['nome'])
    assert len(trechos) > 0
    assert geo.indice[alvo['buses'][1]] in pontos


def test_trafo_inexistente_nao_realca_nada(geo):
    assert geo.circuito_bt('nao_existe') == ([], [])


def test_o_painel_conta_o_circuito_realcado(geo):
    alvo = max(geo.trafos, key=lambda t: len(geo.circuito_bt(t['nome'])[1]))
    j = geo.indice[alvo['buses'][1]]
    texto = geo.detalhe(j, 74)
    assert 'circuito BT em destaque' in texto
    assert 'carregamento nominal' in texto


def test_o_detalhe_diz_fase_a_fase(geo):
    v = geo.tensao(74)
    j = int(np.nanargmin(v))
    blocos = geo.blocos(j, 74)
    assert geo.barras[j] in geo.detalhe(j, 74)
    assert {'Tensão', 'Trechos ligados'} <= _secoes(blocos)
    rotulos = _rotulos(blocos)
    assert any(r.startswith('fase ') for r in rotulos)
    assert 'pior fase' in rotulos
    # corrente e percentual da ampacidade, no mesmo valor
    assert any(' A · ' in b[2] for b in blocos if b[0] == 'par')


def test_os_indices_dizem_o_mesmo_que_a_varredura(geo):
    """Os pré-índices são otimização, e otimização só vale se não mudar nada.

    O painel é remontado a cada passo, inclusive nos 120 ms da animação, e
    antes disto duas varreduras lineares rodavam ali dentro — os 43 mil nós e
    as 15 mil linhas, quatro vezes por segundo. A troca por índice tem de dar
    exatamente a mesma resposta, para toda barra do circuito, incluindo as que
    não têm nó nem trecho nenhum.
    """
    r = geo.r
    for nome in r['barras']:
        nos = [k for k, ib in enumerate(r['no_para_barra'])
               if r['barras'][ib] == nome]
        assert list(geo.nos_da_barra.get(nome, ())) == nos, nome

        trechos = [(k, b1, b2) for k, (b1, b2) in enumerate(r['linhas'])
                   if nome in (b1, b2)]
        assert list(geo.trechos_da_barra.get(nome, ())) == trechos, nome


def test_o_laco_proprio_conta_uma_vez_so(geo):
    """Trecho com bus1 == bus2 existe no cadastro e não pode duplicar.

    O exemplo tem `MT_5053837` saindo e chegando na mesma barra. Indexando por
    ponta sem cuidado, ele entraria duas vezes na lista daquela barra e o painel
    mostraria o mesmo trecho repetido.
    """
    proprios = [(k, b1) for k, (b1, b2) in enumerate(geo.r['linhas']) if b1 == b2]
    if not proprios:
        pytest.skip('este caso não tem trecho com as duas pontas na mesma barra')
    for k, b in proprios:
        assert [t[0] for t in geo.trechos_da_barra[b]].count(k) == 1


def test_o_trecho_mostra_a_ampacidade_junto_da_corrente(geo):
    """Sem a ampacidade, o percentual mente sobre o que está errado.

    O exemplo tem um trecho a 1956 % da ampacidade. Lido sozinho, "1956 %" é um
    condutor em brasa; escrito por extenso — "176.0 A de 9 A" — fica claro que o
    implausível é o cadastro do condutor, e não a corrente calculada. O número
    sozinho engana, e o painel não pode ser o lugar onde ele engana.

    Este teste existe porque a versão com ampacidade já esteve escrita e morta:
    ficou sombreada por uma segunda definição do método, e ninguém notou.
    """
    import re

    j = max(range(len(geo.barras)),
            key=lambda k: len(geo._blocos_trechos(geo.barras[k], 74)))
    valores = [b[2] for b in geo._blocos_trechos(geo.barras[j], 74)
               if b[0] == 'par']
    assert valores
    for v in valores:
        assert re.match(r'^[\d.]+ A de \d+ A · \d+%$', v), v


def test_o_detalhe_de_barra_morta_nao_inventa_numero(geo):
    v = geo.tensao(74)
    mortas = np.where(np.isnan(v))[0]
    if not len(mortas):
        pytest.skip('este caso não tem barra sem tensão')
    blocos = geo.blocos(int(mortas[0]), 74)
    assert 'Sem fase conectada' in geo.detalhe(int(mortas[0]), 74)
    # e nenhuma linha de tensão foi inventada
    assert not any(r.startswith('fase ') for r in _rotulos(blocos))


# ── Mapa de fundo ────────────────────────────────────────────────────────────

def test_mercator_ida_e_volta():
    from bdgdcase.interface.azulejos import lonlat_para_tile, tile_para_lonlat
    lon, lat, z = -49.012, -28.467, 17
    x, y = lonlat_para_tile(lon, lat, z)
    lon2, lat2 = tile_para_lonlat(x, y, z)
    assert lon2 == pytest.approx(lon, abs=1e-9)
    assert lat2 == pytest.approx(lat, abs=1e-9)


def test_o_zoom_respeita_o_orcamento_de_azulejos():
    from bdgdcase.interface.azulejos import escolher_zoom, lonlat_para_tile
    import math
    o, s_, l, n = -49.0165, -28.4685, -49.0060, -28.4645
    z = escolher_zoom(o, s_, l, n, max_azulejos=16)
    x0, y0 = lonlat_para_tile(o, n, z)
    x1, y1 = lonlat_para_tile(l, s_, z)
    quantos = ((math.floor(x1) - math.floor(x0) + 1)
               * (math.floor(y1) - math.floor(y0) + 1))
    assert quantos <= 16
    # e é o maior que cabe: um nível acima estouraria
    x0, y0 = lonlat_para_tile(o, n, z + 1)
    x1, y1 = lonlat_para_tile(l, s_, z + 1)
    assert ((math.floor(x1) - math.floor(x0) + 1)
            * (math.floor(y1) - math.floor(y0) + 1)) > 16


def test_sem_rede_o_fundo_apenas_nao_aparece():
    """Servidor fora não pode virar erro na tela — o mapa vale sem o fundo."""
    from bdgdcase.interface import azulejos
    azulejos.FUNDOS['_teste'] = {
        'rotulo': 'teste', 'credito': '',
        'camadas': ('https://este-host-nao-existe.invalid/{z}/{x}/{y}.png',)}
    try:
        assert azulejos.buscar(-49.02, -28.47, -49.00, -28.46,
                               fundo='_teste', tempo_limite=2) is None
    finally:
        del azulejos.FUNDOS['_teste']


def test_fundo_nenhum_nao_vai_a_rede():
    from bdgdcase.interface.azulejos import buscar
    assert buscar(-49.02, -28.47, -49.00, -28.46, fundo='nenhum') is None


def test_o_pai_cobre_o_azulejo_que_faltou():
    """Azulejo que não veio é coberto pelo de zoom menor, no quadrante certo.

    Sem isso o furo fica branco, e um quadrado branco no meio do bairro se lê
    como dado — como se ali não houvesse nada — em vez de falha de rede.
    """
    from PIL import Image

    from bdgdcase.interface import azulejos

    modelo = 'https://exemplo.invalid/{z}/{x}/{y}.png'
    quadrantes = {(0, 0): (255, 0, 0), (1, 0): (0, 255, 0),
                  (0, 1): (0, 0, 255), (1, 1): (255, 255, 0)}
    pai = Image.new('RGBA', (azulejos.LADO, azulejos.LADO))
    meio = azulejos.LADO // 2
    for (qx, qy), cor in quadrantes.items():
        pai.paste(Image.new('RGBA', (meio, meio), cor + (255,)),
                  (qx * meio, qy * meio))

    z, px, py = 19, 300000, 400000
    azulejos._cache_memoria[(modelo, z - 1, px, py)] = pai
    try:
        for (qx, qy), cor in quadrantes.items():
            filho = azulejos._azulejo_do_pai(
                modelo, z, px * 2 + qx, py * 2 + qy, 1.0)
            assert filho is not None
            assert filho.size == (azulejos.LADO, azulejos.LADO)
            assert filho.getpixel((azulejos.LADO // 2,
                                   azulejos.LADO // 2))[:3] == cor
    finally:
        azulejos._cache_memoria.pop((modelo, z - 1, px, py), None)


def test_sem_pai_nenhum_o_buraco_continua_buraco():
    """Não havendo nem o pai, não se inventa imagem: devolve None."""
    from bdgdcase.interface.azulejos import _azulejo_do_pai
    assert _azulejo_do_pai('https://nao-existe.invalid/{z}/{x}/{y}.png',
                           19, 1, 1, 0.5) is None


def test_mercator_e_o_sistema_do_mapa():
    """O mapa desenha em metros de Mercator, e o painel mostra grau.

    É o que faz a rede assentar sobre o azulejo em qualquer ampliação: em graus
    a colocação é aproximada, e a aproximação aparece no zoom, com o poste
    andando para fora da rua.
    """
    from bdgdcase.interface.azulejos import merc, merc_inv
    lon, lat = -49.0117, -28.4665
    x, y = merc(lon, lat)
    assert abs(x) > 1e6 and abs(y) > 1e6          # metros, não graus
    lon2, lat2 = merc_inv(x, y)
    assert lon2 == pytest.approx(lon, abs=1e-9)
    assert lat2 == pytest.approx(lat, abs=1e-9)


def test_a_geometria_guarda_mercator_e_lonlat(geo):
    from bdgdcase.interface.azulejos import merc
    assert geo.xy.shape == geo.lonlat.shape
    x, y = merc(*geo.lonlat[0])
    assert geo.xy[0][0] == pytest.approx(x)
    assert geo.xy[0][1] == pytest.approx(y)


def test_a_extensao_do_fundo_vem_em_mercator():
    """`imshow` recebe metros, como o eixo — misturar unidade desalinha tudo."""
    from bdgdcase.interface.azulejos import buscar
    r = buscar(-49.02, -28.47, -49.00, -28.46, fundo='claro')
    if r is None:
        pytest.skip('sem rede')
    _img, (xo, xl, ys, yn) = r
    assert abs(xo) > 1e6 and abs(yn) > 1e6
    assert xo < xl and ys < yn


def test_todo_fundo_declara_credito():
    """Atribuição é obrigação de quem publica a imagem, não cortesia."""
    from bdgdcase.interface.azulejos import FUNDOS, credito
    for chave, conf in FUNDOS.items():
        assert conf['rotulo']
        if conf['camadas']:
            assert credito(chave), chave


#: O contrato do inspetor: tipo de bloco → quantos elementos a tupla tem.
FORMA_DO_BLOCO = {'secao': 3, 'par': 4, 'link': 5, 'grafico': 5,
                  'nota': 2, 'vazio': 1}


def _conferir_contrato(blocos):
    """Todo bloco é de um tipo conhecido e tem o tamanho daquele tipo."""
    for b in blocos:
        assert b[0] in FORMA_DO_BLOCO, b
        assert len(b) == FORMA_DO_BLOCO[b[0]], b


def test_o_inspetor_e_estrutura_e_nao_texto(geo):
    """`blocos()` é o contrato; `detalhe()` é uma renderização dele.

    Afirmar sobre a string formatada amarra o teste à tipografia, e foi o que
    quebrou quatro testes quando o painel deixou de ser um despejo monoespaçado.
    """
    j = int(np.nanargmin(geo.tensao(74)))
    blocos = geo.blocos(j, 74)
    _conferir_contrato(blocos)

    # Cor só onde o número diagnostica: se toda linha tivesse estilo, o estilo
    # deixaria de destacar coisa alguma.
    pares = [b for b in blocos if b[0] in ('par', 'link')]
    com_estilo = [b for b in pares if b[3]]
    assert 0 < len(com_estilo) < len(pares)
    assert all(b[3] in ('bom', 'atencao', 'ruim') for b in com_estilo)


def test_todo_link_aponta_para_algo_que_existe(geo):
    """Link que abre uma tela vazia é pior que rótulo sem link."""
    vistos = 0
    for j in range(len(geo.barras)):
        for b in geo.blocos(j, 74):
            if b[0] != 'link':
                continue
            vistos += 1
            tipo, nome = b[4]
            if tipo == 'uc':
                assert nome in geo.carga_por_nome, nome
            elif tipo == 'trafo':
                assert nome in geo.trafo_por_nome, nome
            elif tipo == 'gd':
                assert nome in geo.gd_por_nome, nome
            elif tipo == 'barra':
                assert nome in geo.indice or nome in geo.por_barra, nome
            else:
                raise AssertionError('alvo desconhecido: %r' % (b[4],))
    assert vistos > 0, 'nenhum link no painel — a navegação não existe'


def test_toda_tela_de_detalhe_respeita_o_contrato(geo):
    """As fichas produzem os mesmos tipos de bloco que o ponto."""
    alvos = ([('uc', n) for n in list(geo.carga_por_nome)[:8]]
             + [('trafo', n) for n in list(geo.trafo_por_nome)[:4]]
             + [('gd', n) for n in list(geo.gd_por_nome)[:4]])
    assert alvos
    for foco in alvos:
        blocos = geo.blocos_de(foco, 74)
        assert blocos, foco
        _conferir_contrato(blocos)
        # e o texto puro não pode engolir nenhum tipo em silêncio
        assert any(b[0] == 'secao' for b in blocos), foco


def test_a_ficha_da_unidade_traz_o_cadastro(geo):
    """É o pedido central: clicar numa UC e ver quem ela é."""
    nome = next((n for n, c in geo.carga_por_nome.items()
                 if (c.get('cadastro') or {}).get('cod_id')), None)
    if nome is None:
        pytest.skip('o caso não tem cadastro por unidade')
    blocos = geo.blocos_de(('uc', nome), 74)
    rotulos = {b[1].strip() for b in blocos if b[0] in ('par', 'link')}
    assert {'identificador BDGD', 'demanda nominal', 'fases ligadas'} <= rotulos
    assert any(b[0] == 'grafico' for b in blocos), 'faltou a curva do dia'


def test_a_curva_da_unidade_tem_um_ponto_por_passo(geo):
    nome = next(iter(geo.carga_por_nome))
    serie, nota = geo.curva_da_carga(nome)
    if serie is None:
        pytest.skip('sem curva para esta carga')
    assert len(serie) == geo.v_barra.shape[0]
    assert all(v >= 0 for v in serie)
    assert 'nominal' in nota or 'medida' in nota


def test_a_ficha_do_trafo_soma_o_circuito_de_baixa(geo):
    nome = max(geo.trafo_por_nome,
               key=lambda n: len(geo.circuito_bt(n)[1]))
    blocos = geo.blocos_de(('trafo', nome), 74)
    rotulos = {b[1].strip() for b in blocos if b[0] in ('par', 'link')}
    assert {'alcance', 'carregamento nominal'} <= rotulos
    graficos = [b for b in blocos if b[0] == 'grafico']
    assert graficos, 'faltou a curva do circuito'
    # a soma tem de bater com a soma das curvas das unidades atendidas
    pontos, _ = geo.circuito_bt(nome)
    cargas = [c for p in pontos
              for c in geo.por_barra.get(geo.barras[p], {'cargas': []})['cargas']]
    esperado = 0.0
    for c in cargas:
        s, _ = geo.curva_da_carga(c['nome'])
        if s:
            esperado += s[40]
    assert graficos[0][2][40] == pytest.approx(esperado, rel=1e-6)


def test_o_detalhe_em_texto_nao_engole_bloco_nenhum(geo):
    """`detalhe()` tem de renderizar todo tipo, e não cair num `else` mudo.

    O `else` que devolvia linha em branco deixaria um tipo novo sumir sem
    erro: os testes que procuram substring continuariam verdes enquanto o
    conteúdo desaparecia da tela.
    """
    nome = next(iter(geo.carga_por_nome))
    blocos = geo.blocos_de(('uc', nome), 74)
    texto = geo.detalhe_de(('uc', nome), 74)
    assert nome in texto
    for b in blocos:
        if b[0] == 'grafico':
            assert b[1] in texto, b
        elif b[0] in ('par', 'link', 'secao'):
            assert b[1].strip() in texto, b


def test_a_faixa_de_tensao_classifica_pelos_cortes_usuais():
    from bdgdcase.interface.geometria import _faixa
    assert _faixa(0.99) == 'bom'
    assert _faixa(0.90) == 'atencao'
    assert _faixa(0.80) == 'ruim'
    assert _faixa(1.08) == 'ruim'
    assert _faixa(float('nan')) == ''


def test_a_paleta_separa_os_dois_niveis_de_tensao():
    """MT e BT nascem em cores diferentes — é o que a legenda declara."""
    from bdgdcase.interface.mapa import CAMADAS_ROTULO, CORES_PADRAO, FORMA_CAMADA
    assert CORES_PADRAO['mt'] != CORES_PADRAO['bt']
    assert len(set(CORES_PADRAO.values())) == len(CORES_PADRAO)
    # toda camada tem forma na legenda; as de barra não têm cor própria,
    # porque são pintadas pela faixa do PRODIST
    from bdgdcase.interface.mapa import CAMADAS_BARRA
    assert set(FORMA_CAMADA) == set(CAMADAS_ROTULO)
    assert set(CORES_PADRAO) == set(CAMADAS_ROTULO) - set(CAMADAS_BARRA)
    assert set(CAMADAS_BARRA) <= set(CAMADAS_ROTULO)


def test_relogio():
    assert _hhmm(0.0) == '00:00'
    assert _hhmm(18.5) == '18:30'
    assert _hhmm(23.75) == '23:45'


def test_a_escala_de_cor_e_fixa():
    """Escala automática faria alimentador saudável parecer doente."""
    assert V_LIMITES == (0.90, 1.05)


# ── A janela ─────────────────────────────────────────────────────────────────

tk = pytest.importorskip('tkinter')
pytest.importorskip('matplotlib',
                    reason='o matplotlib nao carregou (instalacao incompleta)')


@pytest.fixture(scope='module')
def raiz():
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip('sem servidor gráfico: %s' % exc)
    r.withdraw()
    yield r
    r.destroy()


def test_a_figura_acompanha_o_widget(raiz, resultado):
    """A figura tem de crescer com a janela, e não ficar no figsize inicial.

    Regressão de um `bind('<Configure>', ...)` sem o `'+'`: o Tk **substitui**
    a ligação existente, e a existente era a do `FigureCanvasTkAgg`, que é quem
    redimensiona a figura. Com ela apagada, a figura ficava presa em 700×600 px
    por maior que fosse a janela — o mapa pequeno num canto, com o resto vazio.

    O sintoma só aparecia abrindo pelo terminal e simulando depois; passando a
    pasta ao construtor, a ordem dos eventos escondia o defeito.
    """
    from bdgdcase.interface.janela import _Janela

    janela = tk.Toplevel(raiz)
    janela.geometry('1100x760')
    janela.update_idletasks()
    try:
        j = _Janela(janela)
        j.resultado = resultado
        j._construir_mapa()
        alvo = j._canvas.get_tk_widget()
        alvo.update_idletasks()

        # Força um redimensionamento e deixa o Tk processá-lo.
        janela.geometry('1300x820')
        for _ in range(40):
            janela.update()
        fig = j._ax.get_figure()
        larg_fig, alt_fig = fig.get_size_inches() * fig.dpi
        assert larg_fig == pytest.approx(alvo.winfo_width(), abs=4), (
            'a figura não acompanhou o widget: %d px contra %d'
            % (larg_fig, alvo.winfo_width()))
        assert alt_fig == pytest.approx(alvo.winfo_height(), abs=4)
    finally:
        janela.destroy()


def test_a_janela_monta_e_anda_no_tempo(raiz, resultado):
    from bdgdcase.interface.janela import _Janela

    janela = tk.Toplevel(raiz)
    janela.withdraw()
    try:
        j = _Janela(janela)
        j.resultado = resultado
        j._construir_mapa()
        assert j.geo is not None

        j._ir(0)
        assert j.v_hora.get() == '00:00'
        j._ir(74)
        assert j.v_hora.get() == '18:30'
        assert 'pu' in j.v_status.get()

        # não passa das pontas
        j._ir(-5)
        assert j.passo == 0
        j._ir(10_000)
        assert j.passo == resultado['passos'] - 1

        # a vista preenche a moldura: sem faixa branca em cima e embaixo.
        # Numa janela oculta o `after` do enquadramento não chega a disparar,
        # então aqui ele é chamado na mão.
        j._reenquadrar()
        cx = j._ax.get_window_extent()

        # E a moldura medida é a inteira, não a já encolhida: medir pela caixa
        # desenhada faz o enquadramento convergir para o tamanho reduzido, e o
        # mapa nunca recupera a largura perdida.
        pos = j._ax.get_position(original=True)
        fig = j._ax.get_figure()
        larg_fig, alt_fig = fig.get_size_inches() * fig.dpi
        assert cx.width == pytest.approx(pos.width * larg_fig, rel=0.02)
        assert cx.height == pytest.approx(pos.height * alt_fig, rel=0.02)
        x0, x1 = j._ax.get_xlim()
        y0, y1 = j._ax.get_ylim()
        proporcao = (y1 - y0) / (x1 - x0) * j._ax.get_aspect()
        assert proporcao == pytest.approx(cx.height / cx.width, abs=0.02)

        # roda do mouse amplia em torno do cursor
        class _Evento:
            pass
        e = _Evento()
        e.inaxes, e.button = j._ax, 'up'
        e.xdata, e.ydata = (x0 + x1) / 2, (y0 + y1) / 2
        j.vista._rodou(e)
        assert (j._ax.get_xlim()[1] - j._ax.get_xlim()[0]) < (x1 - x0)
        e.button = 'down'
        j.vista._rodou(e)
        assert j._ax.get_xlim()[1] - j._ax.get_xlim()[0] == pytest.approx(
            x1 - x0, rel=1e-6)

        # arrastar desloca; um clique parado não
        p = _Evento()
        p.inaxes, p.button, p.x, p.y = j._ax, 1, 500, 400
        p.xdata, p.ydata = e.xdata, e.ydata
        j.vista._apertou(p)
        mv = _Evento()
        mv.inaxes, mv.x, mv.y = j._ax, 560, 400
        mv.xdata, mv.ydata = e.xdata, e.ydata
        antes = j._ax.get_xlim()
        j.vista._moveu(mv)
        assert j._ax.get_xlim() != antes

        # camadas ligam e desligam sem remontar nada
        j.v_camada['chaves'].set(False)
        j._aplicar_camadas()
        assert j._marcadores['chaves'].get_visible() is False
        j.v_camada['chaves'].set(True)
        j._aplicar_camadas()
        assert j._marcadores['chaves'].get_visible() is True
        j.v_camada['bt'].set(False)
        j._aplicar_camadas()
        assert j._colecoes['bt'].get_visible() is False

        # clicar no secundário de um trafo realça o circuito BT dele
        alvo = max(j.geo.trafos,
                   key=lambda t: len(j.geo.circuito_bt(t['nome'])[1]))
        j._selecionar(*j.geo.xy[j.geo.indice[alvo['buses'][1]]])
        assert len(j._realce.get_segments()) == len(
            j.geo.circuito_bt(alvo['nome'])[1]) > 0

        # a legenda cobre todas as camadas, e fica sobre o mapa
        from bdgdcase.interface.mapa import CAMADAS_ROTULO
        assert set(j._amostras) == set(CAMADAS_ROTULO)
        assert j._legenda.winfo_manager() == 'place'
        assert j._legenda.master is j._canvas.get_tk_widget()

        # trocar a cor de uma camada chega aos artistas, que é o ponto de
        # deixar trocar: sem isto o seletor mexeria só no símbolo da legenda
        from matplotlib.colors import to_hex
        j.cores['mt'] = '#00ff00'
        j.cores['trafos'] = '#ff00ff'
        j._desenhar_amostra('mt')
        j._redesenhar()
        assert to_hex(j._colecoes['mt'].get_color()[0]) == '#00ff00'
        assert to_hex(j._marcadores['trafos'].get_markerfacecolor()) == '#ff00ff'

        # a aba de resumo monta e traz os números do alimentador
        texto = j._texto_resumo()
        assert resultado['alimentador'] in texto
        for pedaco in ('transformadores', 'unidades consumidoras',
                       'Geração distribuída', 'fator de carga'):
            assert pedaco in texto
    finally:
        janela.destroy()


def test_a_navegacao_em_profundidade(raiz, resultado):
    """Clicar num poste, entrar numa unidade, ver a curva, e voltar.

    É o pedido que originou tudo isto, e o único teste que o exercita pela
    janela — o resto afirma sobre `_Geometria`, que não sabe o que é um clique.
    """
    from bdgdcase.interface.janela import _Janela

    janela = tk.Toplevel(raiz)
    janela.withdraw()
    try:
        j = _Janela(janela)
        j.resultado = resultado
        j._construir_mapa()

        # um ponto que tenha unidade consumidora
        alvo = next(i for i, b in enumerate(j.geo.barras)
                    if j.geo.por_barra.get(b, {'cargas': []})['cargas'])
        j._selecionar(*j.geo.xy[alvo])
        assert j._foco is None and j._trilha == []
        texto_ponto = j.txt.get('1.0', 'end')

        # a unidade é um link no painel do ponto
        links = [b for b in j.geo.blocos(alvo, j.passo)
                 if b[0] == 'link' and b[4][0] == 'uc']
        assert links, 'o ponto não ofereceu nenhuma unidade para abrir'

        j._abrir(links[0][4])
        assert j._foco == links[0][4]
        assert j._trilha == [(None, 'Ponto')]
        ficha = j.txt.get('1.0', 'end')
        assert ficha != texto_ponto
        assert 'voltar' in ficha
        assert 'DEMANDA' in ficha.upper() or 'demanda' in ficha
        # a curva entrou como figura embutida, não como texto
        assert j._graficos, 'a curva do dia não foi desenhada'

        j._voltar()
        assert j._foco is None
        assert j.txt.get('1.0', 'end') == texto_ponto
        assert j._graficos == []

        # andar no tempo não desfaz a navegação, e não acumula figuras: cada
        # repintura destrói as anteriores, senão 96 passos deixariam 96 telas
        # de matplotlib vivas dentro do painel.
        j._abrir(links[0][4])
        quantos = len(j._graficos)
        assert quantos
        for passo in (10, 50, 90):
            j._ir(passo)
            assert j._foco == links[0][4]
            assert len(j._graficos) == quantos
        j._voltar()

        # e do transformador dá para descer para as unidades que ele atende
        nome_tr = max(j.geo.trafo_por_nome,
                      key=lambda n: len(j.geo.circuito_bt(n)[1]))
        j._abrir(('trafo', nome_tr))
        assert j._graficos
        descidas = [b for b in j.geo.blocos_de(('trafo', nome_tr), j.passo)
                    if b[0] == 'link' and b[4][0] == 'uc']
        assert descidas
        j._abrir(descidas[0][4])
        assert len(j._trilha) == 2
        j._voltar()
        assert j._foco == ('trafo', nome_tr)
    finally:
        janela.destroy()


def test_clicar_no_mapa_recomeca_a_navegacao(raiz, resultado):
    """Selecionar outra barra tem de zerar o foco — senão o painel mostraria a
    ficha de um elemento enquanto o mapa destaca outro ponto."""
    from bdgdcase.interface.janela import _Janela

    janela = tk.Toplevel(raiz)
    janela.withdraw()
    try:
        j = _Janela(janela)
        j.resultado = resultado
        j._construir_mapa()
        alvo = next(i for i, b in enumerate(j.geo.barras)
                    if j.geo.por_barra.get(b, {'cargas': []})['cargas'])
        j._selecionar(*j.geo.xy[alvo])
        links = [b for b in j.geo.blocos(alvo, j.passo)
                 if b[0] == 'link' and b[4][0] == 'uc']
        j._abrir(links[0][4])
        assert j._foco is not None

        outro = next(i for i in range(len(j.geo.barras)) if i != alvo)
        j._selecionar(*j.geo.xy[outro])
        assert j._foco is None
        assert j._trilha == []
    finally:
        janela.destroy()


# ── As faixas do PRODIST ─────────────────────────────────────────────────────

def test_o_corte_entre_media_e_baixa_e_o_da_norma():
    """O Anexo 8.A separa em 2,3 kV, e não em 1 kV.

    A Tabela 3 vale para "tensão nominal superior a 2,3 kV e inferior a 69 kV";
    as de baixa, para "igual ou inferior a 2,3 kV".
    """
    from bdgdcase.interface.geometria import nivel_de
    assert nivel_de(13.8) == 'mt'
    assert nivel_de(23.1) == 'mt'
    assert nivel_de(2.31) == 'mt'
    assert nivel_de(2.3) == 'bt'          # "igual ou inferior a 2,3 kV"
    assert nivel_de(0.38) == 'bt'
    assert nivel_de(0.44) == 'bt'
    assert nivel_de(0.0) == 'bt'          # base desconhecida: a régua permissiva
    assert nivel_de(None) == 'bt'


def test_a_mesma_tensao_muda_de_faixa_entre_media_e_baixa():
    """É o motivo de existirem duas tabelas, e o que faltava no mapa.

    Há duas janelas em que os dois níveis discordam, e são elas que este teste
    fixa:

    * entre 0,92 e 0,93 pu — **adequada** em baixa, **precária** em média;
    * entre 0,87 e 0,90 pu — **precária** em baixa, **crítica** em média.

    Julgar a rede inteira pela tabela da média, que era o que se fazia, pinta
    de amarelo um bairro de baixa que a norma considera em ordem, e de vermelho
    um que ela considera apenas precário.
    """
    from bdgdcase.interface.geometria import _faixa
    assert _faixa(0.925, 'mt') == 'atencao'
    assert _faixa(0.925, 'bt') == 'bom'
    assert _faixa(0.885, 'mt') == 'ruim'
    assert _faixa(0.885, 'bt') == 'atencao'
    # e há uma terceira janela, no alto: a norma dá faixa precária acima da
    # adequada só em baixa tensão
    assert _faixa(1.055, 'mt') == 'ruim'
    assert _faixa(1.055, 'bt') == 'atencao'
    # fora dessas janelas os dois concordam
    for pu in (0.80, 0.95, 1.00, 1.20):
        assert _faixa(pu, 'mt') == _faixa(pu, 'bt'), pu


@pytest.mark.parametrize('pu,esperado', [
    # Tabela 3 do Anexo 8.A — acima de 2,3 kV e abaixo de 69 kV. Aqui a norma
    # dá os limites já em fração da tensão de referência.
    (0.899, 'ruim'), (0.90, 'atencao'), (0.929, 'atencao'),
    (0.93, 'bom'), (1.00, 'bom'), (1.05, 'bom'),
    # e em média NÃO há faixa precária acima da adequada
    (1.0501, 'ruim'), (1.06, 'ruim'), (1.20, 'ruim'),
])
def test_os_cortes_do_prodist_em_media_tensao(pu, esperado):
    from bdgdcase.interface.geometria import _faixa
    assert _faixa(pu, 'mt') == esperado


@pytest.mark.parametrize('volts,esperado', [
    # Tabela 5 do Anexo 8.A — sistema 380/220 V. A norma dá os limites em
    # VOLTS, e é assim que o teste os escreve: adequada de 350 a 399, precária
    # de 331 a 350 ou de 399 a 403, crítica fora disso. Testar em volts é
    # testar contra a norma; testar em pu arredondado é testar contra a minha
    # conversão dela.
    (330, 'ruim'), (331, 'atencao'), (349, 'atencao'),
    (350, 'bom'), (380, 'bom'), (399, 'bom'),
    (400, 'atencao'), (403, 'atencao'), (404, 'ruim'), (450, 'ruim'),
])
def test_os_cortes_do_prodist_em_baixa_tensao(volts, esperado):
    from bdgdcase.interface.geometria import _faixa
    assert _faixa(volts / 380.0, 'bt') == esperado


def test_a_sobretensao_precaria_existe_em_baixa_e_nao_em_media():
    """A assimetria é da norma, e é o que faz as duas tabelas divergirem no alto.

    Em baixa, 400 V num sistema 380/220 são precários — a norma admite até
    403. Em média, qualquer coisa acima de 1,05 TR já é crítica.
    """
    from bdgdcase.interface.geometria import PRODIST, _faixa
    critica, precaria, adequada, alta = PRODIST['bt']
    assert alta > adequada, 'baixa tensão tem faixa precária superior'
    assert _faixa((adequada + alta) / 2, 'bt') == 'atencao'

    assert PRODIST['mt'][3] == PRODIST['mt'][2], 'média não tem faixa superior'
    assert _faixa((adequada + alta) / 2, 'mt') == 'ruim'


def test_barra_sem_tensao_nao_tem_faixa():
    from bdgdcase.interface.geometria import _faixa
    assert _faixa(float('nan'), 'mt') == ''
    assert _faixa(None, 'bt') == ''


def test_a_geometria_classifica_barra_a_barra(geo):
    """O vetor de faixas do mapa tem de concordar com a classificação de cada
    barra pela SUA tabela — é o que garante que a cor não é global."""
    from bdgdcase.interface.geometria import _faixa
    for passo in (0, 40, 74):
        f = geo.faixa(passo)
        v = geo.tensao(passo)
        assert len(f) == len(geo.barras)
        nomes = {'bom': 0.0, 'atencao': 1.0, 'ruim': 2.0}
        for i in range(len(v)):
            if np.isnan(v[i]):
                assert np.isnan(f[i])
            else:
                assert f[i] == nomes[_faixa(float(v[i]), geo.nivel[i])]


def test_as_contagens_por_faixa_fecham_com_o_total(geo):
    for passo in (0, 50):
        a, p, c, mortas = geo.contar_faixas(passo)
        assert a + p + c + mortas == len(geo.barras)
        assert mortas == int(np.isnan(geo.tensao(passo)).sum())


def test_a_classificacao_pega_as_tres_faixas_dos_dois_lados(geo):
    """O alimentador de exemplo é saudável, e um teste que só o percorre nunca
    veria amarelo nem vermelho. Aqui as tensões são forçadas."""
    import copy

    from bdgdcase.interface.geometria import _Geometria

    r = copy.copy(geo.r)
    r['v_no_pu'] = geo.r['v_no_pu'].copy()
    g = _Geometria(r)

    # uma barra de cada nível, e um valor de cada faixa em cada uma
    casos = {'mt': [(0.95, 0), (0.91, 1), (0.85, 2), (1.06, 2), (1.10, 2)],
             # em baixa, 1,055 pu cai na faixa precária SUPERIOR
             'bt': [(0.95, 0), (0.89, 1), (1.055, 1), (0.85, 2), (1.10, 2)]}
    for nivel, valores in casos.items():
        j = next((i for i in range(len(g.barras)) if g.nivel[i] == nivel
                  and not np.isnan(g.tensao(0)[i])), None)
        assert j is not None, nivel
        for pu, faixa in valores:
            # As duas pontas: "todas as fases em `pu`" é mínimo e máximo iguais
            g.v_barra[0, j] = pu
            g.v_barra_alta[0, j] = pu
            assert g.faixa(0)[j] == faixa, (nivel, pu)


def test_as_cores_e_os_nomes_das_faixas_andam_juntos():
    from bdgdcase.interface.geometria import CORES_FAIXA, FAIXAS, PRODIST
    assert len(CORES_FAIXA) == len(FAIXAS) == 3
    assert FAIXAS[0].startswith('adequ') and FAIXAS[2].startswith('crít')
    assert len(set(CORES_FAIXA)) == 3
    # e as duas tabelas existem, com a de baixa mais permissiva embaixo
    assert PRODIST['bt'][0] < PRODIST['mt'][0]
    assert PRODIST['bt'][1] < PRODIST['mt'][1]
    assert PRODIST['bt'][2] == pytest.approx(1.05)
    assert PRODIST['mt'][2] == 1.05


def test_simular_pelo_botao_chega_ao_fim(raiz):
    """O caminho que todo usuário percorre: apertar Simular e esperar.

    Os outros testes montam o mapa chamando `_construir_mapa` direto, e por
    isso nunca passavam pela thread de trabalho. Ela tem regra própria — o Tk
    só deixa ler suas variáveis na thread que criou o interpretador —, e ler
    uma lá dentro derruba a simulação antes de começar, com "main thread is
    not in main loop". Foi o que aconteceu ao ligar a medição por carga.
    """
    import time

    from bdgdcase.interface.janela import _Janela

    janela = tk.Toplevel(raiz)
    janela.withdraw()
    try:
        j = _Janela(janela)
        j.v_pasta.set(str(ESPERADO))
        j._simular()
        limite = time.time() + 120
        while j.geo is None and time.time() < limite:
            janela.update()
            j._drenar()
            time.sleep(0.02)
        assert not j.v_status.get().startswith('ERRO'), j.v_status.get()
        assert j.geo is not None, 'a simulação não terminou: %s' % j.v_status.get()
        assert j.resultado['pq_carga'] is None      # sem medir, por padrão

        # e com a medição marcada, o resultado traz as séries por carga
        j.geo = None
        j.v_medir.set(True)
        j._simular()
        limite = time.time() + 120
        while j.geo is None and time.time() < limite:
            janela.update()
            j._drenar()
            time.sleep(0.02)
        assert not j.v_status.get().startswith('ERRO'), j.v_status.get()
        assert j.resultado['pq_carga'] is not None
    finally:
        # Espera a thread soltar o botão antes de destruir: destruir a janela
        # com o trabalho em voo deixa variáveis do Tk órfãs, e o Python
        # reclama delas na coleta de lixo, num ponto sem relação com a causa.
        limite = time.time() + 30
        while (str(j.btn_sim['state']) == 'disabled'
               and time.time() < limite):
            janela.update()
            j._drenar()
            time.sleep(0.02)
        janela.destroy()


def test_as_barras_sao_duas_camadas_que_cobrem_o_circuito(geo):
    """Média e baixa em artistas separados, sem sobrar nem repetir barra."""
    from bdgdcase.interface.mapa import CAMADAS_BARRA

    total = 0
    for nivel in CAMADAS_BARRA.values():
        m = geo.barras_do_nivel(nivel)
        assert m.dtype == bool
        total += int(m.sum())
    assert total == len(geo.barras)
    assert not (geo.barras_do_nivel('mt') & geo.barras_do_nivel('bt')).any()


def test_o_clique_nao_alcanca_barra_de_camada_desligada(geo):
    """Selecionar o que não se vê é pior que não selecionar nada."""
    j_mt = next(i for i in range(len(geo.barras)) if geo.nivel[i] == 'mt')
    x, y = geo.xy[j_mt]

    # com os dois níveis à mostra, o clique acha a barra de média
    assert geo.nivel[geo.barra_mais_proxima(x, y, niveis=['mt', 'bt'])] == 'mt'
    # com a média desligada, cai na barra de baixa mais próxima
    achada = geo.barra_mais_proxima(x, y, niveis=['bt'])
    assert achada is not None and geo.nivel[achada] == 'bt'
    # e sem nível nenhum ligado não há o que selecionar
    assert geo.barra_mais_proxima(x, y, niveis=[]) is None


def test_ligar_e_desligar_barras_por_nivel(raiz, resultado):
    """A camada some do mapa, e a outra continua lá."""
    from bdgdcase.interface.janela import _Janela
    from bdgdcase.interface.mapa import CAMADAS_BARRA

    janela = tk.Toplevel(raiz)
    janela.withdraw()
    try:
        j = _Janela(janela)
        j.resultado = resultado
        j._construir_mapa()
        assert set(j._pontos) == set(CAMADAS_BARRA)
        assert all(a.get_visible() for a in j._pontos.values())

        j.v_camada['barras_mt'].set(False)
        j._aplicar_camadas()
        assert j._pontos['barras_mt'].get_visible() is False
        assert j._pontos['barras_bt'].get_visible() is True

        # e cada artista carrega só as barras do seu nível
        for camada, nivel in CAMADAS_BARRA.items():
            n = int(j.geo.barras_do_nivel(nivel).sum())
            assert len(j._pontos[camada].get_offsets()) == n

        # andar no tempo continua repintando os dois
        j._ir(60)
        for camada, nivel in CAMADAS_BARRA.items():
            arr = j._pontos[camada].get_array()
            assert len(arr) == int(j.geo.barras_do_nivel(nivel).sum())
    finally:
        janela.destroy()


# ── Capacitores e reguladores ────────────────────────────────────────────────

def test_regulador_nao_e_transformador_de_distribuicao(resultado):
    """Um regulador é `Transformer` no OpenDSS, e não pode contar como trafo.

    Sem separá-los, um regulador trifásico entrava três vezes na lista de
    transformadores: o resumo somava seis máquinas onde há duas, e o mapa as
    desenhava com o quadrado de trafo, com "carregamento nominal 0 %" e um
    circuito de baixa vazio — porque regulador não atende ninguém.

    A separação pergunta ao `RegControl` de quem ele cuida, e não ao nome do
    elemento: assim continua valendo para caso gerado por outra ferramenta.
    """
    inv = resultado['inventario']
    nomes_trafo = {t['nome'].lower() for t in inv['trafos']}
    for g in inv.get('reguladores', ()):
        for enrolamento in g.get('enrolamentos', []):
            assert enrolamento.lower() not in nomes_trafo
    assert inv['n_reguladores'] == len(inv.get('reguladores', ()))
    assert inv['n_capacitores'] == len(inv.get('capacitores', ()))


def test_o_regulador_trifasico_conta_como_um_equipamento():
    """Três enrolamentos entre o mesmo par de barras são um banco só."""
    from bdgdcase.solucao import _agrupar_reguladores

    fases = [{'nome': 'reg_1_%s' % s, 'buses': ['a', 'b'], 'kva': 333.3,
              'controle': 'rc_1_%s' % s, 'vreg': 120.0, 'banda': 3.0,
              'tap': 0, 'tap_atual': 1.0, 'barra_monitorada': '',
              'cadastro': {'cod_id': '1'}} for s in 'abc']
    outro = dict(fases[0], nome='reg_2_a', buses=['c', 'd'])

    bancos = _agrupar_reguladores(fases + [outro])
    assert len(bancos) == 2
    banco = bancos[0]
    assert banco['fases'] == 3
    assert banco['kva'] == pytest.approx(1000.0, abs=0.1)
    assert banco['enrolamentos'] == ['reg_1_a', 'reg_1_b', 'reg_1_c']
    assert banco['cadastro']['cod_id'] == '1'


def test_as_camadas_novas_tem_cor_forma_e_marcador(geo):
    from bdgdcase.interface.mapa import CAMADAS_ROTULO, CORES_PADRAO, FORMA_CAMADA
    for camada in ('capacitores', 'reguladores'):
        assert camada in CAMADAS_ROTULO
        assert camada in CORES_PADRAO
        assert camada in FORMA_CAMADA
    # e a geometria sabe posicioná-los, mesmo quando o caso não tem nenhum
    assert geo.cap_xy.shape[1:] == (2,)
    assert geo.reg_xy.shape[1:] == (2,)
    assert len(geo.cap_xy) == len(geo.capacitores)
    assert len(geo.reg_xy) == len(geo.reguladores)


def test_a_ficha_do_capacitor_e_do_regulador(geo):
    """Sintéticos: o alimentador de exemplo não tem nenhum dos dois."""
    geo.capacitor_por_nome['cap_x'] = {
        'nome': 'cap_x', 'bus': geo.barras[0], 'kvar': 600.0, 'kv': 13.8,
        'passos': 1, 'ligado': False, 'cadastro': {'cod_id': '9', 'banc': '1'}}
    geo.regulador_por_nome['reg_x'] = {
        'nome': 'reg_x', 'buses': [geo.barras[0], geo.barras[1]],
        'kva': 1000.0, 'fases': 3, 'enrolamentos': ['reg_x_a', 'reg_x_b'],
        'controle': 'rc_x', 'vreg': 120.0, 'banda': 3.0, 'tap': -1,
        'tap_atual': 0.99, 'barra_monitorada': '',
        'cadastro': {'cod_id': '7', 'tip_regu': 'DF'}}
    try:
        cap = geo.blocos_de(('capacitor', 'cap_x'), 0)
        _conferir_contrato(cap)
        rotulos = {b[1].strip() for b in cap if b[0] in ('par', 'link')}
        assert {'potência reativa', 'barra'} <= rotulos
        # banco desligado tem de aparecer como tal, e não como 600 kVAr ativos
        assert any(b[0] == 'par' and b[3] == 'atencao' for b in cap)
        assert 'desligado' in geo.detalhe_de(('capacitor', 'cap_x'), 0)

        reg = geo.blocos_de(('regulador', 'reg_x'), 0)
        _conferir_contrato(reg)
        rotulos = {b[1].strip() for b in reg if b[0] in ('par', 'link')}
        assert {'tensão de referência', 'banda morta', 'montante',
                'jusante'} <= rotulos
        assert 'Controle' in _secoes(reg)
    finally:
        del geo.capacitor_por_nome['cap_x']
        del geo.regulador_por_nome['reg_x']


def test_a_faixa_da_barra_vem_do_pior_desvio_e_nao_da_fase_mais_baixa(geo):
    """Uma fase sozinha acima do limite tem de pintar a barra.

    A tensão da barra era só o mínimo entre as fases vivas, o que serve para
    subtensão e é cego para sobretensão: uma barra com as fases em 1,0513,
    1,0019 e 1,0019 pu virava 1,0019 e saía adequada, com a primeira já
    precária. E o desequilíbrio é justamente o que empurra uma fase para cima,
    de modo que o caso não é raro — é o esperado.
    """
    import copy

    from bdgdcase.interface.geometria import _Geometria

    r = copy.copy(geo.r)
    r['v_no_pu'] = geo.r['v_no_pu'].copy()
    g = _Geometria(r)

    for nivel, esperado in (('bt', 1), ('mt', 2)):
        j = next(i for i in range(len(g.barras)) if g.nivel[i] == nivel
                 and not np.isnan(g.tensao(0)[i]))
        g.v_barra[0, j] = 1.0019       # a mais baixa, adequada nos dois níveis
        g.v_barra_alta[0, j] = 1.0513  # a mais alta, já fora da adequada
        assert g.faixa(0)[j] == esperado, nivel

    # e o contrário continua valendo: a mais baixa fora e a mais alta dentro
    j = next(i for i in range(len(g.barras)) if g.nivel[i] == 'mt'
             and not np.isnan(g.tensao(0)[i]))
    g.v_barra[0, j] = 0.91
    g.v_barra_alta[0, j] = 1.00
    assert g.faixa(0)[j] == 1


def test_o_painel_colore_cada_fase_pela_sua_faixa(geo):
    """A fase fora do limite se anuncia sozinha, sem o leitor comparar de cabeça."""
    from bdgdcase.interface.geometria import _faixa
    j = int(np.nanargmin(geo.tensao(74)))
    nivel = geo.nivel[j]
    fases = {b[1]: b for b in geo.blocos(j, 74)
             if b[0] == 'par' and b[1].startswith('fase ')}
    assert fases
    for rotulo, bloco in fases.items():
        pu = float(bloco[2].split()[0])
        assert bloco[3] == _faixa(pu, nivel), rotulo


def test_a_pior_fase_e_a_de_pior_faixa_e_nao_a_mais_baixa():
    """O rótulo diz "pior", e pior é a que está em pior faixa.

    Com 1,0513 / 1,0019 / 1,0019, a mais baixa está adequada; a pior é a de
    cima. Ler "pior fase 1,0019 — adequada" ao lado de uma fase precária seria
    o painel desmentindo a própria cor da barra.
    """
    from bdgdcase.interface.geometria import _faixa
    fases = [1.0513, 1.0019, 1.0019]
    severidade = {'': 0, 'bom': 0, 'atencao': 1, 'ruim': 2}
    for nivel in ('bt', 'mt'):
        pior = max(fases, key=lambda x: (severidade[_faixa(x, nivel)],
                                         abs(x - 1.0)))
        assert pior == 1.0513, nivel
        assert _faixa(pior, nivel) != 'bom'


# ── Ampliar além do que o provedor tem ───────────────────────────────────────

def _azulejo_falso(cor):
    from PIL import Image
    return Image.new('RGBA', (8, 8), cor)


def test_o_azulejo_substituto_e_reconhecido_pela_repeticao(monkeypatch):
    """O provedor não dá erro: devolve uma imagem cinza dizendo que não tem.

    Como imagem válida, ela atravessa qualquer verificação de rede e vai para
    a tela — era o que aparecia ao aproximar demais. O que a denuncia é a
    repetição: é o MESMO azulejo para qualquer coordenada, e dois azulejos de
    satélite distintos nunca saem byte a byte iguais.
    """
    from bdgdcase.interface import azulejos as az

    az._SUBSTITUTOS.clear()
    monkeypatch.setattr(az, '_um_azulejo',
                        lambda *a, **k: _azulejo_falso((120, 120, 120, 255)))
    assert az._substituto('m', 19, [(1, 1), (9, 9)], 1.0) is True
    assert len(az._SUBSTITUTOS) == 1

    # e uma vez aprendido, um azulejo só já basta para reconhecer
    assert az._substituto('m', 19, [(5, 5)], 1.0) is True
    az._SUBSTITUTOS.clear()


def test_azulejos_diferentes_nao_sao_confundidos_com_substituto(monkeypatch):
    """A imagem de verdade muda com a coordenada, e não pode ser descartada."""
    from bdgdcase.interface import azulejos as az

    az._SUBSTITUTOS.clear()
    monkeypatch.setattr(
        az, '_um_azulejo',
        lambda modelo, z, x, y, t: _azulejo_falso((x * 7 % 255, y, 30, 255)))
    assert az._substituto('m', 19, [(1, 1), (9, 9)], 1.0) is False
    assert not az._SUBSTITUTOS


def test_a_busca_desce_ate_o_zoom_que_o_provedor_serve(monkeypatch):
    """Sem imagem no nível pedido, desce — e a imagem sai borrada, não cinza.

    Borrado e verdadeiro é melhor que nítido e falso, e muito melhor que o
    aviso do provedor: a mesma área com menos pixels ainda é a área certa, e o
    `imshow` a estica sobre a extensão em metros.
    """
    from bdgdcase.interface import azulejos as az

    az._SUBSTITUTOS.clear()
    az._ZOOM_TETO.clear()
    servido = 18

    def fingir(modelo, z, x, y, t):
        if z > servido:
            return _azulejo_falso((120, 120, 120, 255))   # sempre igual
        return _azulejo_falso((x % 255, y % 255, z, 255))

    monkeypatch.setattr(az, '_um_azulejo', fingir)
    assert az._zoom_servido('m', 21, 100, 200, 101, 201, 1.0) == servido

    # e o teto fica registrado, para não repetir a sondagem a cada movimento
    assert servido in az._ZOOM_TETO.values()
    az._SUBSTITUTOS.clear()
    az._ZOOM_TETO.clear()


def test_o_teto_nao_e_aprendido_quando_o_nivel_pedido_funciona(monkeypatch):
    """Ter pedido zoom 16 com sucesso não diz nada sobre o 17.

    Gravar isso como teto passaria a limitar uma área que tem imagem de sobra
    — o defeito ao contrário, e mais difícil de notar, porque o mapa fica
    apenas menos nítido do que podia.
    """
    from bdgdcase.interface import azulejos as az

    az._SUBSTITUTOS.clear()
    az._ZOOM_TETO.clear()
    monkeypatch.setattr(
        az, '_um_azulejo',
        lambda modelo, z, x, y, t: _azulejo_falso((x % 255, y % 255, z, 255)))
    assert az._zoom_servido('m', 16, 100, 200, 101, 201, 1.0) == 16
    assert az._ZOOM_TETO == {}


def test_a_chave_do_teto_nao_depende_do_zoom(monkeypatch):
    """A mesma área tem de ter a mesma chave em qualquer ampliação.

    Com a chave em coordenadas do zoom corrente, cada nível viraria uma região
    diferente e o teto aprendido num deles nunca seria consultado no outro.
    """
    from bdgdcase.interface import azulejos as az

    az._SUBSTITUTOS.clear()
    az._ZOOM_TETO.clear()

    def fingir(modelo, z, x, y, t):
        if z > 18:
            return _azulejo_falso((120, 120, 120, 255))
        return _azulejo_falso((x % 255, y % 255, z, 255))

    monkeypatch.setattr(az, '_um_azulejo', fingir)
    # a mesma área, pedida em dois níveis diferentes
    az._zoom_servido('m', 21, 800, 1600, 801, 1601, 1.0)
    chaves = set(az._ZOOM_TETO)
    az._zoom_servido('m', 20, 400, 800, 400, 800, 1.0)
    assert set(az._ZOOM_TETO) == chaves, 'a área virou duas regiões'
    az._SUBSTITUTOS.clear()
    az._ZOOM_TETO.clear()


def test_a_barra_de_baixo_ficou_so_com_o_fundo(raiz, resultado):
    """Saíram a caixa de medir carga e o seletor de "colorir por".

    Este teste existe para que a remoção seja decisão e não descuido: se
    alguém reintroduzir um dos dois sem pensar, é aqui que aparece.

    A medição continua alcançável — é escolha de quem abre o caso, e não de
    quem já está olhando para ele, porque trocá-la obriga a resolver o dia de
    novo. Fica em `bdgdcase mapa --medir-cargas`.
    """
    from bdgdcase.interface.janela import _Janela

    janela = tk.Toplevel(raiz)
    janela.withdraw()
    try:
        j = _Janela(janela)
        assert not hasattr(j, 'v_modo'), 'o modo de cor saiu junto com o botão'
        assert hasattr(j, 'v_medir'), 'a opção continua, só sem caixa na janela'

        textos = []

        def varrer(w):
            for f in w.winfo_children():
                try:
                    textos.append(str(f.cget('text')))
                except Exception:
                    pass
                varrer(f)

        varrer(janela)
        juntos = ' | '.join(textos).lower()
        assert 'colorir por' not in juntos
        assert 'carga nos trechos' not in juntos
        assert 'medir carga' not in juntos
        # e o que ficou continua lá
        assert 'fundo:' in juntos and 'simular' in juntos
    finally:
        janela.destroy()


def test_abrir_mapa_ainda_liga_a_medicao(raiz):
    """Sem a caixa, o caminho da linha de comando é o único — e tem de valer."""
    import inspect

    from bdgdcase.interface.janela import abrir_mapa
    assert 'medir_cargas' in inspect.signature(abrir_mapa).parameters


# ── Barras empilhadas na mesma coordenada ────────────────────────────────────
#
# Relato real, em ISL07 às 18h30: a barra `bt_964284` estava precária e o mapa
# a pintava de verde. A cor dela estava certa — o que se via era outra barra,
# na mesma coordenada, desenhada por cima.

def test_a_ordem_de_pintura_poe_o_pior_por_ultimo():
    """Num ponto empilhado, quem decide a cor é o último desenhado."""
    from bdgdcase.interface.mapa import ordem_por_gravidade

    f = [0.0, 2.0, 1.0, 0.0]
    ordem = list(ordem_por_gravidade(f))
    assert [f[i] for i in ordem] == [0.0, 0.0, 1.0, 2.0]


def test_barra_sem_fase_energizada_vai_para_o_fundo():
    """Ausência de tensão não pode esconder problema de tensão.

    NaN é barra sem nenhuma fase viva — cinza no mapa. Se ficasse por cima,
    apagaria a barra crítica que divide a coordenada com ela.
    """
    import math

    from bdgdcase.interface.mapa import ordem_por_gravidade

    f = [2.0, float('nan'), 0.0]
    ordem = list(ordem_por_gravidade(f))
    assert math.isnan(f[ordem[0]])
    assert f[ordem[-1]] == 2.0


def test_a_ordem_e_estavel_entre_iguais():
    """Faixas iguais mantêm a ordem original.

    Sem estabilidade, o desenho embaralharia pontos de mesma faixa a cada passo
    — invisível na cor, mas visível como cintilação sobre imagem de satélite.
    """
    from bdgdcase.interface.mapa import ordem_por_gravidade

    assert list(ordem_por_gravidade([1.0, 1.0, 1.0, 1.0])) == [0, 1, 2, 3]


def test_nenhum_ponto_empilhado_esconde_uma_faixa_pior(geo, resultado):
    """A propriedade que o conserto garante, medida no caso de referência.

    Para cada coordenada com mais de uma barra, a que fica por cima tem de ser
    a pior da pilha. É a condição que faltava, e ela vale em qualquer passo.
    """
    import numpy as np
    from collections import defaultdict

    from bdgdcase.interface.mapa import CAMADAS_BARRA, ordem_por_gravidade

    escondidos = []
    n = resultado['passos']
    for passo in (0, n // 2, n - 1):
        faixa = geo.faixa(passo)
        for _camada, nivel in CAMADAS_BARRA.items():
            m = geo.barras_do_nivel(nivel)
            if not m.any():
                continue
            f = faixa[m]
            ordem = ordem_por_gravidade(f)
            xy, fo = geo.xy[m][ordem], f[ordem]
            grupos = defaultdict(list)
            for k in range(len(xy)):
                grupos[(round(float(xy[k, 0]), 4),
                        round(float(xy[k, 1]), 4))].append(k)
            for pilha in grupos.values():
                if len(pilha) < 2:
                    continue
                visivel = fo[pilha[-1]]
                piores = [fo[k] for k in pilha if fo[k] == fo[k]]
                if not piores:
                    continue
                if visivel != visivel or max(piores) > visivel:
                    escondidos.append((passo, nivel, max(piores), visivel))
    assert not escondidos, (
        '%d pontos escondem uma faixa pior que a desenhada: %s'
        % (len(escondidos), escondidos[:5]))


# ── Buracos no tronco, e a barra de baixa desenhada como se fosse de média ───
#
# Três relatos do mesmo alimentador (805160019): pontos de média sem cabo de um
# lado, e pontos claros cobrindo os ramais vivos ao redor.

def test_a_camada_de_chaves_vem_ligada():
    """A chave é equipamento SOBRE o condutor — o cabo existe de todo jeito.

    Desligada, a camada tirava do desenho trechos de tronco de até 164 m, e o
    mapa mostrava dois postes de média sem nada entre eles. Isso se lê como
    rede partida, que é a conclusão errada.
    """
    from bdgdcase.interface.mapa import CAMADAS_PADRAO
    assert CAMADAS_PADRAO['chaves'] is True


def test_o_vao_do_regulador_e_desenhado(geo):
    """O regulador é `Transformer`, não entra em `segmentos`, e o tronco passa por ele.

    Onde as duas barras dividem a coordenada o vão mede zero e ninguém nota;
    onde não dividem, ele mede — 88 m no alimentador que motivou a correção.
    """
    vaos = geo.vaos_de_regulador()
    assert vaos.ndim == 3 and vaos.shape[1:] == (2, 2)
    # o caso de referência pode não ter regulador; o contrato é a forma
    for a, b in vaos:
        assert len(a) == 2 and len(b) == 2


def test_um_banco_de_tres_reguladores_da_um_vao_so(geo):
    """`REG_x_A`, `_B` e `_C` são o mesmo trecho visto três vezes."""
    vaos = geo.vaos_de_regulador()
    pares = {(tuple(a), tuple(b)) for a, b in vaos}
    assert len(pares) == len(vaos)


def test_barra_de_baixa_nunca_entra_na_camada_de_media(geo):
    """O nome manda quando ele diz `BT_`.

    A base de tensão vem do `CalcVoltageBases`, que a escolhe pela tensão
    calculada — e numa barra sem fase energizada não há tensão calculada, então
    o OpenDSS atribui a da fonte. Barras de baixa iam parar na camada de média:
    desenhadas no dobro do tamanho e por cima da baixa, em cinza, tapando os
    ramais vivos que dividiam a coordenada com elas.

    Medido antes do conserto: 112 barras num alimentador, 35 noutro, 7 no de
    referência.
    """
    maus = [b for b, n in zip(geo.barras, geo.nivel)
            if str(b).lower().startswith('bt_') and n == 'mt']
    assert not maus, '%d barras de baixa na camada de média: %s' % (
        len(maus), maus[:5])


def test_trocar_de_caso_nao_leva_a_selecao_do_anterior(raiz, tmp_path):
    """O estado de navegação é feito de ÍNDICES, e cada caso tem os seus.

    Antes de as duas janelas virarem uma, cada caso nascia num processo próprio
    e morria com ele. Agora a mesma janela simula um caso atrás do outro: a
    barra 5.000 do alimentador anterior não existe num caso com 4.525, e o
    painel abria com `IndexError` antes de desenhar qualquer coisa.
    """
    import inspect

    from bdgdcase.interface import mapa

    fonte = inspect.getsource(mapa._AbaMapa._construir_mapa)
    # a geometria nova e o estado zerado andam juntos
    assert 'self.geo = _Geometria(self.resultado)' in fonte
    for campo in ('_selecionada', '_foco', '_trilha', '_graficos'):
        assert 'self.%s = ' % campo in fonte, campo


# ── As quantidades ao lado da legenda ────────────────────────────────────────

def test_o_resumo_cobre_toda_camada_da_legenda(geo):
    """Camada sem número na legenda fica com o rótulo pela metade."""
    from bdgdcase.interface.mapa import CAMADAS_ROTULO

    resumo = geo.resumo_das_camadas(0)
    faltando = [c for c in CAMADAS_ROTULO if c not in resumo]
    assert not faltando, faltando
    assert 'barras_sem_tensao' in resumo


def test_o_km_por_camada_soma_o_total_do_caso(geo, resultado):
    """A soma das camadas tem de dar o comprimento que o caso declara.

    O número vem do `length` de cada linha, e não da distância entre as
    coordenadas do desenho: a segunda erra sempre que o cadastro põe as duas
    pontas no mesmo poste, e nesta base isso é comum — 33% dos trechos do caso
    de referência.
    """
    resumo = geo.resumo_das_camadas(0)
    soma = sum(float(resumo[c].split()[0])
               for c in ('mt', 'bt'))
    soma += float(resumo['chaves'].split('·')[1].split()[0])
    assert soma == pytest.approx(resultado['inventario']['km_linhas'], abs=0.15)


def test_a_contagem_de_barras_bate_com_as_camadas(geo):
    """O que a legenda diz é o que o mapa desenha, e não outra contagem."""
    from bdgdcase.interface.mapa import CAMADAS_BARRA

    resumo = geo.resumo_das_camadas(0)
    for camada, nivel in CAMADAS_BARRA.items():
        assert int(resumo[camada]) == int(geo.barras_do_nivel(nivel).sum())


def test_a_contagem_de_sem_tensao_muda_com_a_hora(geo, resultado):
    """É a única quantidade que depende do instante, e por isso é recalculada.

    De madrugada a geração está parada e a rede toda alimentada; ao meio-dia,
    não necessariamente. Fixar o número na montagem da legenda deixaria a
    contagem mentir a partir do segundo passo.
    """
    import numpy as np

    n = resultado['passos']
    for passo in (0, n // 2, n - 1):
        resumo = geo.resumo_das_camadas(passo)
        assert int(resumo['barras_sem_tensao']) == int(
            np.isnan(geo.faixa(passo)).sum())


def test_a_legenda_do_sem_tensao_usa_a_cor_do_mapa():
    """A bolinha cinza da legenda e a do mapa são a mesma cor, por construção.

    Duas constantes iguais viram duas constantes diferentes na primeira vez que
    alguém mexe numa delas, e aí a legenda passa a nomear uma cor que o mapa
    não usa.
    """
    import inspect

    from bdgdcase.interface import mapa
    from bdgdcase.interface.geometria import CINZA_SEM_TENSAO

    fonte = inspect.getsource(mapa._AbaMapa._construir_mapa)
    assert 'bad=CINZA_SEM_TENSAO' in fonte
    legenda = inspect.getsource(mapa._AbaMapa._montar_legenda)
    assert 'CINZA_SEM_TENSAO' in legenda
    assert CINZA_SEM_TENSAO.startswith('#')
