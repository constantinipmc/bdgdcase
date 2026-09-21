# -*- coding: utf-8 -*-
"""O diagnóstico de topologia, e o que ele tem o direito de afirmar.

Este arquivo existe por causa de um diagnóstico que gritava em toda conversão.
Ele cruzava `PONNOT.COD_ID` com os `PAC` dos cabos e anunciava "0 postes com
cabos conectados" e milhares de "órfãos" — em **todas** as bases, inclusive a de
referência. Os dois nunca casam, e não por defeito de nenhuma delas: na BDGD o
identificador do poste e o ponto de acoplamento do cabo são espaços distintos.

Um alarme que dispara sempre não é diagnóstico. Foi lido como erro de extração,
e custou uma investigação inteira.

O que se guarda aqui, então, não é só o número certo: é a disciplina de só
comparar o que é do mesmo espaço de identificador, e de não trocar um alarme
falso por outro — o que a primeira tentativa de reescrever este diagnóstico
fez, duas vezes seguidas.
"""
import pandas as pd
import pytest

from bdgdcase.modelo.topologia import diagnostico_topologia


def _ponnot(ids):
    return pd.DataFrame({'COD_ID': ids})


def test_o_que_e_do_mesmo_espaco_e_comparado(capsys):
    """`UCBT.PN_CON` referencia `PONNOT.COD_ID`. Esse cruzamento vale."""
    diagnostico_topologia(
        _ponnot(['100', '200', '300']), None, None, None,
        ucbt_por_poste=pd.DataFrame({'COD_PONNOT': ['100', '200']}))
    saida = capsys.readouterr().out
    assert '2 (100%)' in saida
    assert 'ATENCAO' not in saida


def test_a_lista_por_poste_nao_e_confundida_com_um_codigo(capsys):
    """No agregado, `PN_CON` é uma lista separada por `;`.

    Um poste com cinco unidades traz `'100;100;100;100;100'`. Comparar isso com
    `COD_ID` fazia só os postes de uma unidade casarem — 14% onde a medição
    correta dá 100%. Era o alarme falso substituindo o alarme falso.
    """
    diagnostico_topologia(
        _ponnot(['100', '200']), None, None, None,
        ucbt_por_poste=pd.DataFrame({'PN_CON': ['100;100;100', '200;200']}))
    assert '2 (100%)' in capsys.readouterr().out


def test_o_ponto_zero_do_float_nao_conta_como_divergencia(capsys):
    """`661095653` lido do `.gpkg` com nulos na coluna volta `661095653.0`."""
    diagnostico_topologia(
        _ponnot([661095653.0, 661095654.0]), None, None, None,
        ucbt_por_poste=pd.DataFrame({'COD_PONNOT': ['661095653', '661095654']}))
    assert '2 (100%)' in capsys.readouterr().out


def test_a_divergencia_de_verdade_e_dita(capsys):
    """Quando as unidades realmente não acham o poste, aí sim há o que avisar."""
    diagnostico_topologia(
        _ponnot(['100', '200']), None, None, None,
        ucbt_por_poste=pd.DataFrame({'COD_PONNOT': ['100', '999', '998', '997']}))
    saida = capsys.readouterr().out
    assert '1 (25%)' in saida
    assert 'ATENCAO' in saida
    assert 'nao acha o poste' in saida


def test_o_poste_e_o_pac_nao_sao_mais_cruzados(capsys):
    """A regressão que se quer impedir: voltar a comparar espaços diferentes.

    Aqui o PONNOT e a rede não têm um único identificador em comum — que é a
    situação real de todas as bases medidas. O diagnóstico tem de sair sem
    alarme nenhum.
    """
    ssdmt = pd.DataFrame({'PAC_1': ['A1', 'A2'], 'PAC_2': ['A2', 'A3']})
    diagnostico_topologia(_ponnot(['100', '200', '300']), ssdmt, None, None)
    saida = capsys.readouterr().out
    assert 'ATENCAO' not in saida
    assert 'rfão' not in saida and 'rfao' not in saida
    assert 'Postes no PONNOT                    : 3' in saida
    assert 'Nos eletricos distintos na rede     : 3' in saida


@pytest.mark.parametrize('kwargs', [
    dict(ponnot=None, ssdmt=None, ssdbt=None, untrmt=None),
    dict(ponnot=pd.DataFrame(), ssdmt=pd.DataFrame(), ssdbt=None, untrmt=None),
    dict(ponnot=_ponnot(['1']), ssdmt=None, ssdbt=None, untrmt=None,
         ucbt_por_poste=pd.DataFrame()),
    dict(ponnot=_ponnot(['1']), ssdmt=None, ssdbt=None, untrmt=None,
         ucbt_por_poste=pd.DataFrame({'OUTRA': [1]})),
])
def test_tabela_faltando_nao_derruba_nem_alarma(kwargs, capsys):
    """Diagnosticar nunca pode ser motivo de o caso não sair."""
    diagnostico_topologia(**kwargs)
    assert 'ATENCAO' not in capsys.readouterr().out


# ── Quando a topologia da base está na geometria, e não no PAC ───────────────

def test_a_costura_une_o_que_o_arredondamento_separou():
    """Um metro entre duas pontas partia um alimentador em dois lobos.

    A costura por coordenada agrupava por igualdade da coordenada **arredondada
    a cinco casas**. Arredondar agrupa, mas não mede: dois pontos colados caem
    em baldes vizinhos sempre que a fronteira do arredondamento passa entre
    eles. Medido num alimentador da Copel — latitudes em -22,73436 e -22,73439,
    3,0 m de distância real — e o resultado eram 8.183 cargas sem caminho até a
    subestação.
    """
    from bdgdcase.modelo.rede import agrupar_por_proximidade

    mapa = {(-52.62663, -22.73436): {'A'},
            (-52.62663, -22.73439): {'B'},
            (-52.60000, -22.70000): {'C'}}

    assert len(agrupar_por_proximidade(mapa, 0)) == 3, 'sem raio, nada se une'
    juntos = agrupar_por_proximidade(mapa, 5.0)
    assert sorted(sorted(v) for v in juntos.values()) == [['A', 'B'], ['C']]


def test_a_costura_nao_alcanca_o_poste_seguinte():
    """O raio tem de ficar muito abaixo do vão entre postes.

    Trinta metros é o vão de um ramal urbano. Se a costura chegasse lá, ela
    fundiria postes distintos — e o que era imprecisão de desenho viraria
    invenção de rede.
    """
    from bdgdcase.modelo.config import TOLERANCIA_COSTURA_COORDENADA_M
    from bdgdcase.modelo.rede import agrupar_por_proximidade

    assert 1.0 < TOLERANCIA_COSTURA_COORDENADA_M <= 8.0

    # ~27 m em latitude
    mapa = {(-52.6, -22.70000): {'A'}, (-52.6, -22.70024): {'B'}}
    assert len(agrupar_por_proximidade(
        mapa, TOLERANCIA_COSTURA_COORDENADA_M)) == 2


def test_a_costura_devolve_o_mapa_intacto_quando_nao_ha_o_que_unir():
    """Base cuja geometria não tem pontas coincidentes não ganha emenda."""
    from bdgdcase.modelo.rede import agrupar_por_proximidade

    mapa = {(-52.6, -22.7): {'A'}, (-52.5, -22.6): {'B'}, (-52.4, -22.5): {'C'}}
    assert len(agrupar_por_proximidade(mapa, 5.0)) == 3


def test_a_fracao_ligada_separa_a_base_que_encadeia_da_que_nao_encadeia():
    """É este número que decide ligar a costura, e ele tem de discriminar.

    A Celesc encadeia `PAC_1`/`PAC_2`: a média fecha sozinha e a fração dá 1,0.
    A Copel não encadeia — a continuidade dela está nas pontas coincidentes —,
    e a mesma conta dá 0,27. Ligar a costura onde ela não é necessária custaria
    caro: cada emenda vira um jumper, e jumper entre nós já ligados é um laço
    numa rede que é radial.
    """
    import pandas as pd

    from bdgdcase.modelo.topologia import fracao_mt_ligada

    encadeia = pd.DataFrame({'PAC_1': ['a', 'b', 'c'],
                             'PAC_2': ['b', 'c', 'd']})
    assert fracao_mt_ligada(encadeia, bus_fonte='a') == 1.0

    partida = pd.DataFrame({'PAC_1': ['a', 'c'], 'PAC_2': ['b', 'd']})
    assert fracao_mt_ligada(partida, bus_fonte='a') == 0.5


def test_a_chave_costura_o_que_o_trecho_nao_costura():
    """Chaves e reguladores contam: é por eles que a Celesc fecha o alimentador."""
    import pandas as pd

    from bdgdcase.modelo.topologia import fracao_mt_ligada

    trechos = pd.DataFrame({'PAC_1': ['a', 'c'], 'PAC_2': ['b', 'd']})
    chave = pd.DataFrame({'PAC_1': ['b'], 'PAC_2': ['c']})
    assert fracao_mt_ligada(trechos, bus_fonte='a') == 0.5
    assert fracao_mt_ligada(trechos, unsemt=chave, bus_fonte='a') == 1.0


def test_sem_media_tensao_nao_ha_o_que_consertar():
    """Devolve 1,0: um alimentador sem MT não pede costura."""
    import pandas as pd

    from bdgdcase.modelo.topologia import fracao_mt_ligada

    assert fracao_mt_ligada(None) == 1.0
    assert fracao_mt_ligada(pd.DataFrame()) == 1.0
    assert fracao_mt_ligada(pd.DataFrame({'X': [1]})) == 1.0


def test_o_gatilho_olha_a_conectividade_e_nao_o_pac_ini():
    """O gatilho antigo só acertava por acaso.

    Ele ligava a costura quando a fonte topológica divergia do `PAC_INI` — que
    não tem relação com o sintoma. Num alimentador em que os dois coincidissem,
    a rede saía partida e ninguém ligava a cura.
    """
    import inspect

    from bdgdcase.modelo import pipeline

    fonte = inspect.getsource(pipeline)
    assert 'ganho_da_costura(ssdmt' in fonte
    assert 'GANHO_MINIMO_COSTURA' in fonte
    assert 'habilitar_jumpers_exec = True' in fonte


def test_o_criterio_e_o_ganho_da_costura_e_nao_o_nivel():
    """Olhar o nível ligaria a costura no caso de referência do pacote.

    Ele tem 0,70 da média encadeada por PAC e converte certo assim: os 30%
    restantes são natureza do dado dele, e a costura não os recupera. Já o
    alimentador que saía partido vai de 0,08 para 0,998. É a diferença que
    separa os dois — o nível não separa.
    """
    pytest.importorskip('geopandas')
    import geopandas as gpd
    from shapely.geometry import LineString

    from bdgdcase.modelo.rede import ganho_da_costura

    # dois trechos que se tocam no mapa e NAO compartilham PAC: so a costura une
    partido = gpd.GeoDataFrame(
        {'PAC_1': ['a', 'c'], 'PAC_2': ['b', 'd']},
        geometry=[LineString([(-52.60000, -22.70000), (-52.60000, -22.70050)]),
                  LineString([(-52.60000, -22.70050), (-52.60000, -22.70100)])])
    antes, depois = ganho_da_costura(partido, bus_fonte='a')
    assert antes == 0.5
    assert depois == 1.0, 'a costura tem de unir o que se toca no mapa'

    # os mesmos trechos, agora encadeados pelo PAC: nao ha o que recuperar
    inteiro = gpd.GeoDataFrame(
        {'PAC_1': ['a', 'b'], 'PAC_2': ['b', 'c']},
        geometry=list(partido.geometry))
    antes, depois = ganho_da_costura(inteiro, bus_fonte='a')
    assert antes == 1.0 and depois == 1.0
    assert depois - antes == 0.0, 'sem ganho, a costura nao deve ser ligada'


def test_tabela_sem_as_colunas_de_pac_nao_derruba_a_conversao():
    """Isto roda em toda conversao: faltar coluna nao pode custar o caso.

    `fracao_mt_ligada` sempre teve essa guarda; `ganho_da_costura` ia direto a
    `ssdmt['PAC_1']` e levantaria `KeyError` — e o `pipeline` a chama antes de
    escrever qualquer arquivo, de modo que uma base com a coluna sob outro nome
    trocaria uma conversao que funciona por uma que aborta.
    """
    pytest.importorskip('geopandas')
    import geopandas as gpd
    from shapely.geometry import LineString

    from bdgdcase.modelo.rede import ganho_da_costura

    geom = [LineString([(-52.6, -22.7), (-52.6, -22.7005)])]
    for colunas in ({}, {'PAC_1': ['a']}, {'PAC_2': ['b']}):
        df = gpd.GeoDataFrame(dict(colunas), geometry=geom)
        antes, depois = ganho_da_costura(df, bus_fonte='a')
        assert antes == depois, colunas
        assert depois - antes < 0.05, 'sem PAC nao se liga costura nenhuma'


def test_trechos_distantes_nao_ganham_costura():
    """O ganho tem de ser zero quando as pontas não se tocam de verdade."""
    pytest.importorskip('geopandas')
    import geopandas as gpd
    from shapely.geometry import LineString

    from bdgdcase.modelo.rede import ganho_da_costura

    longe = gpd.GeoDataFrame(
        {'PAC_1': ['a', 'c'], 'PAC_2': ['b', 'd']},
        geometry=[LineString([(-52.60000, -22.70000), (-52.60000, -22.70050)]),
                  LineString([(-52.50000, -22.60000), (-52.50000, -22.60050)])])
    antes, depois = ganho_da_costura(longe, bus_fonte='a')
    assert depois - antes == 0.0


def test_a_costura_geometrica_nao_encadeia_circuitos_de_baixa():
    """Na baixa, costurar por coordenada junta circuitos de trafos diferentes.

    Isso não religa nada: encadeia. Medido num alimentador da Copel — o caminho
    de um nó de baixa até a média passou a ter **35 vãos** e seis emendas, e as
    pontas dessa corrente caíram a **0,650 pu**. Circuito secundário real tem
    poucos vãos do transformador.

    Restringindo a costura à média, o mesmo alimentador fecha em 0,941 a 1,043,
    e os outros dois que ela toca ficam iguais. Religar secundário solto
    continua existindo por outro caminho — `religar_secundarios`, que anda do
    transformador ao poste dele e recusa o paralelo de tensões diferentes.
    """
    import inspect

    from bdgdcase.modelo import pipeline
    from bdgdcase.modelo.config import COSTURA_GEOMETRICA_APENAS_MT

    assert COSTURA_GEOMETRICA_APENAS_MT is True
    fonte = inspect.getsource(pipeline)
    assert 'COSTURA_GEOMETRICA_APENAS_MT' in fonte
    assert 'jumpers_incluir_bt = False' in fonte


def test_o_gatilho_antigo_continua_costurando_a_baixa():
    """A restrição vale para o gatilho NOVO, e não muda o que já existia.

    O caminho do `PAC_INI` divergente costura média e baixa desde antes, e não
    há medição que justifique mexer nele — mexer mudaria casos que hoje estão
    certos, que é exatamente o que não se quer.
    """
    import inspect

    from bdgdcase.modelo import pipeline

    fonte = inspect.getsource(pipeline)
    # a restricao mora DENTRO do bloco do gatilho novo, depois do ganho.
    # `rindex` e nao `index`: o nome do gatilho antigo aparece antes na lista
    # de imports, e comparar com ela nao diria nada sobre a ordem do codigo.
    antes = fonte.index('ganho_da_costura(ssdmt')
    restricao = fonte.rindex('COSTURA_GEOMETRICA_APENAS_MT')
    pac_ini = fonte.rindex('AUTO_PRIORIZAR_PAC_INI_COM_JUMPERS')
    assert antes < restricao < pac_ini, (
        'a restricao tem de ficar no gatilho novo, antes do bloco do PAC_INI')

