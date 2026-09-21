# -*- coding: utf-8 -*-
"""Fase viva é a que tem caminho até a fonte — e o que o IAL02 ensinou.

O caso foi recusado por "tensão fora do plausível": 16 nós em 0,555 pu, sem
geração. A leitura imediata era fase solta logo acima do limiar de 0,5 pu, e
o remédio imediato seria subir o limiar. Antes disso, a pergunta certa —
*esse nó tem caminho até a fonte?* — foi feita nó a nó, e a resposta foi
**sim**: era o terceiro condutor de um secundário trifásico, e estava em
0,555 pu porque uma residência sem energia faturada trazia `CAR_INST=9500`
(watts, gravados como kW) e virava 1.634 kW numa fase de 75 kVA.

Dois achados, duas guardas: a máscara topológica (`nos_conectados`) no lugar
do limiar, e o teto de carga instalada de baixa (`car_inst_bt_kw`).
"""
import os

import pytest

from bdgdcase.modelo.curvas import CAR_INST_BT_MAX_KW, car_inst_bt_kw, calcular_demanda_kw


# ── car_inst_bt_kw ───────────────────────────────────────────────────────────

def test_carga_instalada_normal_passa_como_esta():
    assert car_inst_bt_kw(10.0) == (10.0, 'kw')
    assert car_inst_bt_kw(CAR_INST_BT_MAX_KW) == (CAR_INST_BT_MAX_KW, 'kw')


def test_watts_gravados_como_kw_viram_kw():
    """9.500 numa residência é 9,5 kW — o caso do IAL02."""
    assert car_inst_bt_kw(9500.0) == (9.5, 'watts')
    assert car_inst_bt_kw(2900.0) == (2.9, 'watts')


def test_o_que_nem_dividido_por_mil_cabe_na_baixa_vale_zero():
    assert car_inst_bt_kw(5.0e6) == (0.0, 'implausivel')
    assert car_inst_bt_kw('lixo') == (0.0, 'implausivel')


def test_zero_e_vazio_continuam_zero():
    assert car_inst_bt_kw(0) == (0.0, 'kw')
    assert car_inst_bt_kw(None) == (0.0, 'kw')


def test_o_recuo_da_demanda_usa_o_teto_e_conta():
    """Sem energia faturada, `CAR_INST=9500` entra como 9,5 kW, e é contado."""
    row = {'LISTA_CAR_INST': '10;9500', 'LISTA_ENE_01': '0;0'}
    avisos = {}
    kw = calcular_demanda_kw(row, idx_uc=1, fator_demanda=0.2, aplicar_fc_macro=False,
                             teto_car_inst_kw=CAR_INST_BT_MAX_KW, avisos=avisos)
    assert kw == pytest.approx(9.5 * 0.2)
    assert avisos == {'car_inst_watts': 1}


def test_sem_o_teto_o_recuo_faz_o_que_sempre_fez():
    """Unidade de média (`gerar_cargas_mt`) não passa pelo teto: milhares de
    kW instalados são normais lá."""
    row = {'LISTA_CAR_INST': '9500', 'LISTA_ENE_01': '0'}
    assert calcular_demanda_kw(row, idx_uc=0, fator_demanda=0.2,
                               aplicar_fc_macro=False) == pytest.approx(9500 * 0.2)


def test_com_energia_faturada_o_car_inst_nao_entra():
    row = {'LISTA_CAR_INST': '9500', **{'LISTA_ENE_%02d' % m: '730' for m in range(1, 13)}}
    avisos = {}
    kw = calcular_demanda_kw(row, idx_uc=0, fator_demanda=0.2, aplicar_fc_macro=False,
                             fator_carga_curva=1.0, teto_car_inst_kw=CAR_INST_BT_MAX_KW,
                             avisos=avisos)
    assert kw == pytest.approx(1.0)
    assert avisos == {}


# ── nos_conectados ───────────────────────────────────────────────────────────

MASTER = (
    'Clear\n'
    'New Circuit.teste bus1=fonte.1.2.3 basekv=13.8 pu=1.0 phases=3\n'
    'New Line.MT_1 bus1=fonte.1.2.3 bus2=meio.1.2.3 length=1 units=km r1=0.3 x1=0.4 r0=0.9 x0=1.2\n'
    # lateral bifásica: o nó 3 de `ponta` não existe em linha nenhuma
    'New Line.MT_2 bus1=meio.1.2 bus2=ponta.1.2 phases=2 length=1 units=km r1=0.3 x1=0.4 r0=0.9 x0=1.2\n'
    # trafo monofásico em `ponta.1`: energiza só o nó 1 do secundário...
    'New Transformer.T1 phases=1 windings=2 buses=[ponta.1.0 bt.1.0] conns=[wye wye] kvs=[7.97 0.22] kvas=[25 25] xhl=3\n'
    # ...e a rede de baixa cadastrada a três condutores leva o 2 e o 3 soltos
    'New Line.BT_1 bus1=bt.1.2.3 bus2=bt_fim.1.2.3 phases=3 length=0.1 units=km r1=0.6 x1=0.5 r0=0.6 x0=0.5\n'
    'New Load.C1 bus1=bt_fim.1.0 phases=1 kv=0.22 kw=5 pf=0.95\n'
    # uma chave aberta: o que está atrás dela não tem caminho
    'New Line.CH_1 bus1=meio.1.2.3 bus2=atras.1.2.3 switch=yes\n'
    'Open Line.CH_1 1\n'
    'New Load.C2 bus1=atras.1.2.3 phases=3 kv=13.8 kw=30\n'
    'Set VoltageBases=[13.8 0.38]\nCalcVoltageBases\nSolve\n'
)


@pytest.fixture
def circuito(tmp_path):
    import opendssdirect as dss
    (tmp_path / 'Master.dss').write_text(MASTER, encoding='utf-8')
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        dss.Text.Command('clear')
        dss.Text.Command('redirect Master.dss')
        yield dss
    finally:
        os.chdir(cwd)


def _mascara(dss):
    from bdgdcase.solucao import nos_conectados
    nomes = [n.lower() for n in dss.Circuit.AllNodeNames()]
    return dict(zip(nomes, nos_conectados(dss)))


def test_o_condutor_ausente_na_lateral_nao_esta_conectado(circuito):
    m = _mascara(circuito)
    assert m['fonte.1'] and m['meio.3']
    assert m['ponta.1'] and m['ponta.2']
    assert 'ponta.3' not in m or not m['ponta.3']


def test_a_rede_de_baixa_a_tres_fios_sob_um_trafo_de_uma_fase_tem_uma_so_viva(circuito):
    """O caso que o limiar não separa: `bt_fim.2` e `.3` têm tensão por
    acoplamento com o condutor vivo, e não têm caminho até a fonte."""
    m = _mascara(circuito)
    assert m['bt.1'] and m['bt_fim.1']
    assert not m['bt.2'] and not m['bt.3']
    assert not m['bt_fim.2'] and not m['bt_fim.3']


def test_atras_da_chave_aberta_nao_ha_caminho(circuito):
    m = _mascara(circuito)
    assert not m['atras.1'] and not m['atras.2'] and not m['atras.3']


def test_a_estatistica_de_tensao_usa_a_mascara(circuito):
    """Com a máscara, o mínimo é o da rede viva; o nó solto — que pode estar
    em qualquer valor — não entra, esteja acima ou abaixo de `PU_FASE_ATIVA`."""
    from bdgdcase.solucao import _tensoes_pu, nos_conectados
    v_min, _, v_max = _tensoes_pu(circuito, nos_conectados(circuito))
    assert 0.9 < v_min <= v_max < 1.1


# ── transformador sem potência ───────────────────────────────────────────────

def test_pot_nom_zero_e_recuo_e_nao_valor():
    """SMD01: um `POT_NOM=0` em 877 transformadores e o alimentador inteiro
    saía em NaN — `kva=0` não tem impedância definível."""
    from bdgdcase.modelo.cadastro import kva_trafo
    assert kva_trafo(0, 75.0) == (75.0, 'padrao')
    assert kva_trafo(None, 75.0) == (75.0, 'padrao')
    assert kva_trafo('nan', 75.0) == (75.0, 'padrao')
    assert kva_trafo(-45, 75.0) == (75.0, 'padrao')
    assert kva_trafo(45, 75.0) == (45.0, 'cadastro')
    assert kva_trafo('112,5', 75.0) == (112.5, 'cadastro')


def test_o_transformador_sem_potencia_sai_com_o_padrao_e_e_registrado(tmp_path):
    import pandas as pd
    from bdgdcase import ajustes
    from bdgdcase.modelo.config import TRAFO_KVA_PADRAO
    from bdgdcase.modelo.rede import gerar_transformadores
    untrmt = pd.DataFrame([{
        'COD_ID': 'T0', 'PAC_1': 'MT_1', 'PAC_2': 'BT-1', 'POT_NOM': 0.0, 'TIP_TRAFO': 'T',
        'FAS_CON_P': 'ABC', 'FAS_CON_S': 'ABCN', 'TEN_LIN_SE': 0.38, 'PER_FER': 85.0, 'PER_TOT': 300.0,
    }])
    ajustes.limpar(alimentador='teste', dia='DU')
    saida = tmp_path / 'Transformadores.dss'
    gerar_transformadores(untrmt, None, None, None, str(saida))
    texto = saida.read_text(encoding='utf-8')
    assert 'kva=%.1f' % TRAFO_KVA_PADRAO in texto, texto
    assert 'kva=0.0' not in texto
    achados = [a for a in ajustes.listar() if 'sem potência' in a['titulo']]
    assert achados and achados[0]['quantos'] == 1
    ajustes.limpar(alimentador='teste', dia='DU')


# ── malha: chave de contorno de regulador, e jumper só onde falta ligação ────

def test_a_chave_que_contorna_o_regulador_pelo_patio_abre():
    """UVA01: o contorno era um caminho de TRÊS chaves pela subestação, todas
    `P_N_OPE=F`; a guarda antiga só via chave no mesmo par de PACs."""
    from bdgdcase.modelo.topologia import chaves_que_bypassam_reguladores
    linhas = [('fonte', 'a')]
    chaves = [('s1', 'a', 'x', True), ('s2', 'x', 'y', True), ('s3', 'y', 'b', True)]
    regs = [('r1', 'a', 'b')]
    abrir, sem_saida = chaves_que_bypassam_reguladores(linhas, chaves, regs)
    assert abrir == {'s2': 'r1'}          # a do meio: não toca os terminais
    assert sem_saida == []


def test_sem_contorno_nada_abre():
    from bdgdcase.modelo.topologia import chaves_que_bypassam_reguladores
    abrir, sem = chaves_que_bypassam_reguladores([('fonte', 'a'), ('b', 'c')],
                                                 [('s1', 'c', 'd', True)], [('r1', 'a', 'b')])
    assert abrir == {} and sem == []


def test_chave_ja_aberta_nao_e_contorno():
    from bdgdcase.modelo.topologia import chaves_que_bypassam_reguladores
    abrir, sem = chaves_que_bypassam_reguladores([], [('s1', 'a', 'b', False)], [('r1', 'a', 'b')])
    assert abrir == {} and sem == []


def test_contorno_so_por_trechos_e_dito_e_nao_inventado():
    from bdgdcase.modelo.topologia import chaves_que_bypassam_reguladores
    abrir, sem = chaves_que_bypassam_reguladores([('a', 'x'), ('x', 'b')], [], [('r1', 'a', 'b')])
    assert abrir == {} and sem == ['r1']


def test_dois_contornos_no_mesmo_patio_abrem_dois():
    from bdgdcase.modelo.topologia import chaves_que_bypassam_reguladores
    chaves = [('s1', 'a', 'x', True), ('s2', 'x', 'b', True), ('s3', 'a', 'y', True), ('s4', 'y', 'b', True)]
    abrir, sem = chaves_que_bypassam_reguladores([], chaves, [('r1', 'a', 'b')])
    assert len(abrir) == 2 and sem == []


def test_a_uniao_dos_emitidos_e_por_no(tmp_path):
    """Dois PACs unidos por um trecho de UMA fase não estão unidos nas outras:
    o jumper que as traz é o que mantém a média trifásica (UVA01)."""
    from bdgdcase.modelo.topologia import uniao_dos_emitidos
    f = tmp_path / 'Linhas_MT.dss'
    f.write_text('New Line.MT_1 bus1=A.3 bus2=B.3 phases=1\n'
                 'New Line.SW_1 bus1=B.1.2.3 bus2=C.1.2.3 phases=3\n~ switch=y enabled=no\n'
                 'New Transformer.T1 phases=1 windings=2 buses=[C.1 D.1]\n', encoding='utf-8')
    u = uniao_dos_emitidos([str(f)])
    assert u.ligadas('a.3', 'b.3')
    assert not u.ligadas('a.1', 'b.1') and not u.ligadas('a.2', 'b.2')
    assert not u.ligadas('b.1', 'c.1')          # chave aberta não liga
    assert u.ligadas('c.1', 'd.1')              # transformador liga os enrolamentos


def _caso_jumper(tmp_path, ssdmt_extra_linha_1f):
    """Dois PACs de média no mesmo ponto; num caso já unidos por um trecho
    trifásico, no outro por um trecho de uma fase só."""
    import geopandas as gpd
    from shapely.geometry import LineString
    from bdgdcase import ajustes
    from bdgdcase.modelo.rede import gerar_jumpers_topologicos
    ssdmt = gpd.GeoDataFrame([
        {'COD_ID': 't1', 'PAC_1': '1', 'PAC_2': '2', 'FAS_CON': 'ABC', 'COMP': 100.0,
         'geometry': LineString([(0, 0), (0.001, 0)])},
        {'COD_ID': 't2', 'PAC_1': '2', 'PAC_2': '3', 'FAS_CON': 'ABC', 'COMP': 100.0,
         'geometry': LineString([(0.001, 0), (0.002, 0)])},
        {'COD_ID': 't3', 'PAC_1': '3', 'PAC_2': '4', 'FAS_CON': 'ABC', 'COMP': 100.0,
         'geometry': LineString([(0.002, 0), (0.003, 0)])},
        # PAC 5 cai exatamente onde está o PAC 3, e continua por outro trecho
        {'COD_ID': 't4', 'PAC_1': '5', 'PAC_2': '6', 'FAS_CON': 'ABC', 'COMP': 100.0,
         'geometry': LineString([(0.002, 0), (0.002, 0.001)])},
    ], crs='EPSG:4674')
    mt = tmp_path / 'Linhas_MT.dss'
    # 3 e 5 já se ligam por 3-4-5; no caso "1f" o trecho 4-5 tem só a fase C
    sfx = '.3' if ssdmt_extra_linha_1f else '.1.2.3'
    linhas_mt = [
        'New Line.MT_t1 bus1=1.1.2.3 bus2=2.1.2.3',
        'New Line.MT_t2 bus1=2.1.2.3 bus2=3.1.2.3',
        'New Line.MT_t3 bus1=3.1.2.3 bus2=4.1.2.3',
        'New Line.MT_t4 bus1=5.1.2.3 bus2=6.1.2.3',
        'New Line.MT_t5 bus1=4%s bus2=5%s' % (sfx, sfx),
    ]
    mt.write_text('\n'.join(linhas_mt) + '\n', encoding='utf-8')
    ajustes.limpar(alimentador='teste', dia='DU')
    saida = tmp_path / 'Jumpers.dss'
    n = gerar_jumpers_topologicos(ssdmt, None, None, None, None, str(saida),
                                  incluir_mt=True, incluir_bt=False,
                                  arquivos_emitidos=(str(mt),))
    return n, saida.read_text(encoding='utf-8')


def test_jumper_entre_pontos_ja_ligados_em_todas_as_fases_nao_sai(tmp_path):
    n, texto = _caso_jumper(tmp_path, ssdmt_extra_linha_1f=False)
    assert n == 0, texto


def test_jumper_sai_quando_falta_uma_fase_no_caminho(tmp_path):
    """3 e 5 estão no mesmo ponto e já ligados — mas só pela fase C. O
    jumper trifásico é o que traz as outras duas."""
    n, texto = _caso_jumper(tmp_path, ssdmt_extra_linha_1f=True)
    assert n == 1, texto
    assert 'JUMP_MT' in texto
