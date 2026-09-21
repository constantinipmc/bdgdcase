# -*- coding: utf-8 -*-
"""Uma fonte num nó que a rede não energiza abre o balanço do alimentador.

Dois relatos reais, o mesmo defeito.

No IBA08 (Celesc), a SSDBT traz um toco `PAC_1=BT-0 → PAC_2=BT-D-P2-9261182`,
`FAS_CON=N`, com geometria e com `UNI_TR_MT`. O emissor de linhas descarta o
toco; o fallback espacial da geração não — o placeholder era o ponto mais
perto do poste da usina, e o transformador do toco era o do cadastro. Uma
usina de 40 kW nasceu sozinha numa barra que nada cria, a 0,0 pu.

No 893840018 (Copel), o center-tap sai em `.3.0`/`.0.1`, a rede de baixa em
`.1.3`, e a usina foi para `.2.0` — um nó que só existia porque ela o criou.

Nos dois, o OpenDSS aceita o circuito, "converge" em duas iterações e entrega
o dia com o balanço aberto: 21 de 24 passos num, 50 de 96 no outro. Tirando
UMA usina, o mesmo caso fecha o dia inteiro com os reguladores ligados.

A guarda é a única fonte que não mente: as barras e os nós que os emissores
de linhas e de transformadores de fato escreveram.
"""
import pandas as pd

from bdgdcase.modelo.cadastro import _bus_bt_valido
from bdgdcase.modelo.topologia import (
    _construir_gdf_endpoints_bt, _construir_mapa_bt, nos_vivos,)


# ── nos_vivos ────────────────────────────────────────────────────────────────

def test_o_no_pedido_que_existe_fica_como_esta():
    assert nos_vivos(['1', '3'], [1, 3]) == (['1', '3'], False)


def test_o_no_que_a_rede_nao_cria_e_trocado_pelo_que_ela_cria():
    """O caso do 893840018: `.2` pedido, `.1.3` na rede — vai para `.1`."""
    assert nos_vivos(['2'], [1, 3]) == (['1'], True)


def test_o_que_sobra_da_intersecao_e_mantido():
    """Pediu três fases numa barra de duas: ficam as duas que existem."""
    assert nos_vivos(['1', '2', '3'], [1, 3]) == (['1', '3'], True)


def test_sem_saber_o_que_existe_nada_muda():
    """Quem não sabe não opina — teste unitário chama o emissor sem o mapa."""
    assert nos_vivos(['2'], None) == (['2'], False)
    assert nos_vivos(['2'], []) == (['2'], False)


def test_o_neutro_da_lista_real_nao_conta_como_no():
    assert nos_vivos(['2'], ['1', '0']) == (['1'], True)


def test_a_contagem_de_fases_e_preservada_quando_nada_do_pedido_existe():
    assert nos_vivos(['2', '3'], [1]) == (['1'], True)
    assert nos_vivos(['3'], [1, 2]) == (['1'], True)


def test_a_contagem_de_fases_e_completada_com_os_nos_reais():
    """Pediu as duas pernas `.1.2` num center-tap emitido em `.1.3`: sai em
    `.1.3`, ainda a dois fios. Cortar para `.1` mudaria a tensão declarada e
    poria todo o kW numa perna — o desequilíbrio que já foi medido e corrigido."""
    assert nos_vivos(['1', '2'], [1, 3]) == (['1', '3'], True)
    assert nos_vivos(['2', '3'], [1, 3]) == (['1', '3'], True)


# ── o placeholder não entra nos mapas de resolução ───────────────────────────

def _ssdbt_com_toco():
    from shapely.geometry import LineString
    import geopandas as gpd
    return gpd.GeoDataFrame([
        {'PAC_1': 'BT-1', 'PAC_2': 'BT-2', 'FAS_CON': 'ABCN', 'UNI_TR_MT': 'T1',
         'geometry': LineString([(0, 0), (1, 0)])},
        {'PAC_1': 'BT-0', 'PAC_2': 'BT-D-P2-9261182', 'FAS_CON': 'N', 'UNI_TR_MT': 'T1',
         'geometry': LineString([(5, 0), (6, 0)])},
    ], crs='EPSG:4674')


def test_o_placeholder_e_recusado_pelo_mesmo_crivo_do_emissor_de_linhas():
    assert not _bus_bt_valido('BT_D_P2_9261182')
    assert not _bus_bt_valido('BT_0')
    assert _bus_bt_valido('BT_1726361')


def test_o_toco_nao_entra_no_mapa_de_barras_validas():
    validas, _ = _construir_mapa_bt(_ssdbt_com_toco(), None)
    assert 'BT_1' in validas and 'BT_2' in validas
    assert 'BT_D_P2_9261182' not in validas
    assert 'BT_0' not in validas


def test_o_toco_nao_entra_nos_endpoints_do_fallback_espacial():
    """Era por aqui que a usina do IBA08 caía no placeholder."""
    pontos = _construir_gdf_endpoints_bt(_ssdbt_com_toco())
    nomes = set(pontos['bus'].astype(str))
    assert 'BT_1' in nomes and 'BT_2' in nomes
    assert not any(n.startswith('BT_D_P2_') for n in nomes), nomes
    assert 'BT_0' not in nomes


# ── o emissor de geração, com a verdade dos emissores ────────────────────────

def _ugbt(pac, fas_con='ABCN'):
    return pd.DataFrame([{
        'COD_ID': 'u1', 'PN_CON': pac, 'PAC': pac, 'UNI_TR_MT': 'T1',
        'POT_INST': 40.0, 'FAS_CON': fas_con, 'CEG_GD': 'GD.SC.000.000.000',
        'MUN': '0', 'DAT_CON': '01/01/1951',
        **{'ENE_%02d' % i: 4000 for i in range(1, 13)}
    }])


def _untrmt():
    return pd.DataFrame([{
        'COD_ID': 'T1', 'POT_NOM': 45.0, 'TIP_TRAFO': 'T', 'TEN_LIN_SE': 0.38,
        'FAS_CON_P': 'ABC', 'FAS_CON_S': 'ABCN', 'PAC_1': 'MT_1', 'PAC_2': 'BT-9',
    }])


def _gd(tmp_path, ugbt, **kw):
    from bdgdcase.modelo.geracao import gerar_gd
    saida = tmp_path / 'GD.dss'
    gerar_gd(ugbt, None, None, None, None, None, _untrmt(), str(saida), **kw)
    return saida.read_text(encoding='utf-8')


def test_a_usina_em_barra_que_a_rede_nao_cria_vai_ao_secundario_do_trafo(tmp_path):
    """`BT_77` tem nome bom, mas ninguém a escreveu: vai para o `PAC_2` do trafo."""
    texto = _gd(tmp_path, _ugbt('BT-77'),
                barras_existentes={'bt_9', 'bt_1'}, nos_existentes={'bt_9': [1, 2, 3]})
    assert 'bus1=BT_9.' in texto, texto
    assert 'BT_77' not in texto


def test_a_usina_sem_barra_nem_trafo_fica_de_fora(tmp_path):
    """Se nem o secundário do trafo existe, a usina não entra — e é contada."""
    texto = _gd(tmp_path, _ugbt('BT-77'),
                barras_existentes={'bt_1'}, nos_existentes={'bt_1': [1, 2, 3]})
    assert 'New PVSystem' not in texto, texto


def test_a_usina_em_no_que_a_rede_nao_energiza_e_grampeada(tmp_path):
    """Pediu `.2` numa barra onde só há `.1.3`: sai em `.1`, ainda monofásica."""
    texto = _gd(tmp_path, _ugbt('BT-9', 'BN'),
                barras_existentes={'bt_9'}, nos_existentes={'bt_9': [1, 3]})
    assert 'bus1=BT_9.1.0 phases=1' in texto, texto


def test_sem_o_mapa_o_emissor_faz_o_que_sempre_fez(tmp_path):
    """A guarda é opcional: quem chama sem ela (testes antigos) não muda."""
    texto = _gd(tmp_path, _ugbt('BT-9', 'BN'))
    assert 'bus1=BT_9.2.0 phases=1' in texto, texto


def test_a_usina_no_placeholder_e_recusada_mesmo_sem_o_mapa(tmp_path):
    """O crivo por nome vale sempre; `BT_0` era onde o PRA04 pendurava duas."""
    untrmt = _untrmt(); untrmt.loc[0, 'PAC_2'] = '0'
    from bdgdcase.modelo.geracao import gerar_gd
    saida = tmp_path / 'GD.dss'
    gerar_gd(_ugbt('BT-77'), None, None, None, None, None, untrmt, str(saida))
    texto = saida.read_text(encoding='utf-8')
    assert 'bus1=BT_0' not in texto, texto
    assert 'New PVSystem' not in texto, texto


# ── nos_energizados_bt: o nó tem de ter CAMINHO até um secundário ────────────

from bdgdcase.modelo.topologia import nos_energizados_bt


def _lig(*pares):
    return {frozenset((a, b)): set(nos) for a, b, nos in pares}


def test_o_ramal_a_tres_fios_saindo_de_um_center_tap_so_energiza_duas_pernas():
    """O caso do 893840018: secundário `.1.3`, ramal `.1.2.3`, nó 2 a 0 V."""
    e = nos_energizados_bt({'bt_sec': [1, 3]}, _lig(('bt_sec', 'bt_poste', (1, 2, 3))))
    assert e['bt_poste'] == [1, 3]


def test_a_linha_a_dois_fios_deixa_o_terceiro_no_para_tras():
    e = nos_energizados_bt({'bt_sec': [1, 2, 3]},
                           _lig(('bt_sec', 'bt_a', (1, 2)), ('bt_a', 'bt_b', (1, 2, 3))))
    assert e['bt_a'] == [1, 2]
    assert e['bt_b'] == [1, 2]      # o 3 não voltou a existir rio abaixo


def test_dois_caminhos_somam():
    e = nos_energizados_bt({'bt_s1': [1], 'bt_s2': [3]},
                           _lig(('bt_s1', 'bt_x', (1, 2, 3)), ('bt_s2', 'bt_x', (1, 2, 3))))
    assert e['bt_x'] == [1, 3]


def test_a_ilha_sem_transformador_nao_entra():
    e = nos_energizados_bt({'bt_sec': [1, 2, 3]},
                           _lig(('bt_sec', 'bt_a', (1, 2, 3)), ('bt_ilha1', 'bt_ilha2', (1, 2, 3))))
    assert 'bt_ilha1' not in e and 'bt_ilha2' not in e


def test_uma_linha_que_nao_conduz_nenhum_no_do_secundario_nao_leva_nada():
    e = nos_energizados_bt({'bt_sec': [1]}, _lig(('bt_sec', 'bt_a', (2, 3))))
    assert 'bt_a' not in e


def test_um_laco_termina():
    e = nos_energizados_bt({'bt_sec': [1, 2, 3]},
                           _lig(('bt_sec', 'bt_a', (1, 2, 3)), ('bt_a', 'bt_b', (1, 2, 3)),
                                ('bt_b', 'bt_sec', (1, 2, 3))))
    assert e['bt_b'] == [1, 2, 3]


def test_as_linhas_de_baixa_devolvem_os_nos_por_ligacao(tmp_path):
    """`gerar_linhas_bt` preenche o mapa que a propagação consome."""
    import geopandas as gpd
    from shapely.geometry import LineString
    from bdgdcase.modelo.rede import gerar_linhas_bt
    ssdbt = gpd.GeoDataFrame([
        {'COD_ID': 's1', 'PAC_1': 'BT-1', 'PAC_2': 'BT-2', 'FAS_CON': 'ACN', 'COMP': 10.0,
         'TIP_CND': 'x', 'UNI_TR_MT': 'T1', 'geometry': LineString([(0, 0), (1, 0)])},
    ], crs='EPSG:4674')
    nos_lig = {}
    gerar_linhas_bt(ssdbt, None, str(tmp_path / 'Linhas_BT.dss'), nos_por_ligacao=nos_lig)
    assert nos_lig == {frozenset(('bt_1', 'bt_2')): {1, 3}}, nos_lig
