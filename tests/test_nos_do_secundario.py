# -*- coding: utf-8 -*-
"""O emissor e a réplica têm de concordar sobre os nós do secundário.

`gerar_transformadores` escreve o transformador; `_trafo_ext_map` **replica** o
mesmo raciocínio para dizer às cargas em que nós elas podem pendurar. Enquanto
os dois concordam, tudo funciona. Quando divergem, a carga vai para um nó que o
transformador não energiza — e uma carga de potência constante sobre um nó a 0 V
faz a solução divergir.

Foi assim que um alimentador explodiu **às 19:00**, a hora em que a curva da
iluminação pública acende: o emissor tinha posto o center-tap em dois nós e a
réplica dizia três. E o sintoma que chegou ao usuário foi um
`RuntimeWarning: overflow encountered in cast` do numpy — uma mensagem sobre o
tamanho de um inteiro, não sobre a rede.

O teste principal aqui não confere um número: confere que os dois **concordam**,
em cada topologia de secundário que as quatro bases medidas produzem.
"""
import re

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from bdgdcase.modelo.rede import gerar_transformadores
from bdgdcase.modelo.topologia import _trafo_ext_map


#: Uma linha de UNTRMT por topologia real, com os números das bases medidas.
TOPOLOGIAS = [
    # (nome, TIP_TRAFO, TEN_LIN_SE, FAS_CON_P, FAS_CON_S, LIG_FAS_T)
    ('trifasico 380/220',      'T',  0.38,  'ABC', 'ABCN', '0'),
    ('trifasico 220/127',      'T',  0.22,  'ABC', 'ABCN', '0'),
    ('monofasico 220 simples', 'M',  0.22,  'A',   'AN',   '0'),
    ('center-tap 440/220',     'MT', 0.44,  'B',   'BN',   'CN'),
    ('center-tap 230/115',     'MT', 0.23,  'C',   'CN',   'AN'),
    ('center-tap 254/127',     'M',  0.254, 'CA',  'AN',   'BN'),
    ('bifasico sem 2o enrol.', 'B',  0.22,  'CA',  'CA',   '0'),
]


def _cadastro(caso):
    _nome, tip, ten, fp, fs, lft = caso
    untrmt = gpd.GeoDataFrame(
        [{'COD_ID': 'T1', 'PAC_1': 'MT1', 'PAC_2': 'BT1', 'TIP_TRAFO': tip,
          'TEN_LIN_SE': ten, 'FAS_CON_P': fp, 'FAS_CON_S': fs,
          'POT_NOM': 75.0, 'geometry': Point(0, 0)}],
        geometry='geometry')
    eqtrmt = pd.DataFrame([{'UNI_TR_MT': 'T1', 'LIG_FAS_T': lft}])
    ssdmt = gpd.GeoDataFrame(
        [{'COD_ID': 'L1', 'PAC_1': 'MT0', 'PAC_2': 'MT1', 'FAS_CON': 'ABC',
          'COMP': 100.0, 'geometry': LineString([(0, 0), (1, 0)])}],
        geometry='geometry')
    return untrmt, eqtrmt, ssdmt


def _nos_escritos(texto):
    """Os nós que o `.dss` de fato energiza no secundário (wdg 2 e 3)."""
    nos = set()
    for m in re.finditer(r'wdg=[23] bus=\S*?((?:\.\d)+)', texto):
        for n in m.group(1).split('.'):
            if n and n != '0':
                nos.add(n)
    return nos


@pytest.mark.parametrize('caso', TOPOLOGIAS, ids=[c[0] for c in TOPOLOGIAS])
def test_o_emissor_e_a_replica_concordam(caso, tmp_path):
    """O invariante. Se este teste cai, alguma carga vai para um nó morto."""
    untrmt, eqtrmt, ssdmt = _cadastro(caso)
    saida = tmp_path / 'Transformadores.dss'
    gerar_transformadores(untrmt, eqtrmt, ssdmt, None, saida)

    escritos = _nos_escritos(saida.read_text(encoding='utf-8'))
    ext = _trafo_ext_map(untrmt, None, eqtrmt)
    assert 'T1' in ext, 'a réplica perdeu o transformador'
    _bus, _tip, _ten, avail, _ct = ext['T1']

    assert escritos, 'o emissor não escreveu nó nenhum'
    assert avail == escritos, (
        'réplica diz %s e o emissor escreveu %s' % (sorted(avail),
                                                    sorted(escritos)))


@pytest.mark.parametrize('caso', TOPOLOGIAS, ids=[c[0] for c in TOPOLOGIAS])
def test_a_replica_sabe_quem_e_center_tap(caso, tmp_path):
    """O quinto campo da tupla é a resposta já dada, e tem de bater com o `.dss`.

    A assinatura do center-tap **não** é ter três enrolamentos — o bifásico
    também tem, com duas pernas. É a perna valer METADE da tensão declarada;
    no bifásico ela vale a tensão sobre √3.
    """
    _nome, _tip, ten = caso[0], caso[1], caso[2]
    untrmt, eqtrmt, ssdmt = _cadastro(caso)
    saida = tmp_path / 'Transformadores.dss'
    gerar_transformadores(untrmt, eqtrmt, ssdmt, None, saida)
    kvs = {float(v) for v in re.findall(r'wdg=[23] bus=\S+ kv=([\d.]+)',
                                        saida.read_text(encoding='utf-8'))}
    e_metade = kvs == {round(ten / 2.0, 4)}
    assert _trafo_ext_map(untrmt, None, eqtrmt)['T1'][4] is e_metade


def test_o_center_tap_de_254_nao_vai_para_o_ramo_bifasico(tmp_path):
    """O caso que produziu a divergência, com os números da base.

    Primário de duas fases (`CA`) e secundário center-tap. Roteado pelo
    primário, saía com pernas de `0,254/√3` = 147 V e a réplica devolvia três
    nós; roteado pelo secundário, sai `0,127` = 127 V em dois nós.
    """
    untrmt, eqtrmt, ssdmt = _cadastro(
        ('x', 'M', 0.254, 'CA', 'AN', 'BN'))
    saida = tmp_path / 'Transformadores.dss'
    gerar_transformadores(untrmt, eqtrmt, ssdmt, None, saida)
    texto = saida.read_text(encoding='utf-8')
    assert 'windings=3' in texto
    kvs = {float(v) for v in re.findall(r'wdg=[23] bus=\S+ kv=([\d.]+)', texto)}
    assert kvs == {0.127}, 'a perna do 254/127 é 127 V, não 147'
    assert len(_trafo_ext_map(untrmt, None, eqtrmt)['T1'][3]) == 2


def test_sem_o_catalogo_a_heuristica_ainda_concorda(tmp_path):
    """Extração sem `EQTRMT`: os dois caem na mesma heurística, e concordam.

    Perder a evidência degrada o resultado — o `TIP_TRAFO='M'` daquela base
    deixa de ser reconhecido como center-tap —, mas não pode fazer o emissor e a
    réplica discordarem, que é o defeito caro.
    """
    for caso in TOPOLOGIAS:
        untrmt, _eq, ssdmt = _cadastro(caso)
        saida = tmp_path / 'T.dss'
        gerar_transformadores(untrmt, None, ssdmt, None, saida)
        escritos = _nos_escritos(saida.read_text(encoding='utf-8'))
        avail = _trafo_ext_map(untrmt, None, None)['T1'][3]
        assert avail == escritos, '%s: %s vs %s' % (caso[0], sorted(avail),
                                                    sorted(escritos))
