# -*- coding: utf-8 -*-
"""A tensão da rede de baixa vem do cadastro, e não de uma constante.

O conversor foi construído com a base de uma distribuidora, cuja baixa é toda
380/220 e cujo MRT é todo 440/220. Duas constantes e dois limiares escritos a
partir dela atravessavam o código, e acertavam sempre — ali. Noutra base, onde
metade da baixa é 220/127, erravam por √3 sem levantar exceção nenhuma: o caso
compila, converge, e as barras de baixa aparecem a 1,76 pu.

Estes testes fixam as regras que substituíram os limiares. Todas dizem a mesma
coisa por ângulos diferentes: `TEN_LIN_SE` é tensão de LINHA, e quem precisa da
fase-neutro tem de perguntar ao cadastro do transformador como chegar nela.
"""
import math

import pandas as pd
import pytest

from bdgdcase.modelo.cadastro import eh_center_tap, kv_bt_para_fases
from bdgdcase.modelo.config import TENSAO_BT_KV
from bdgdcase.modelo.master import _bases_extras, bases_do_caso, gerar_master
from bdgdcase.modelo.topologia import _kv_bt_por_trafo


# ═════════════════════════════════════════════════════════════════════════════
# Quem é center-tap
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize('tip, ten, esperado', [
    ('MT', 0.44, True),     # o MRT clássico, 440/220
    ('MT', 0.23, True),     # MRT de 230/115: o tipo manda, não a tensão
    ('T',  0.44, True),     # 0,44 sem um tipo que a explique ainda é center-tap
    ('T',  0.38, False),    # a rede trifásica comum
    ('T',  0.22, False),    # 220/127 trifásico — LINHA de 220, não center-tap
    ('M',  0.22, False),    # monofásico simples: uma fase e o neutro
    ('B',  0.22, False),    # bifásico: duas fases, e o catálogo nega terciário
    ('',   None, False),    # cadastro mudo não inventa center-tap
])
def test_center_tap_e_decidido_pelo_cadastro(tip, ten, esperado):
    assert eh_center_tap(tip, ten) is esperado


def test_center_tap_aguenta_lixo_no_campo():
    """Uma tensão ilegível não pode derrubar o alimentador inteiro."""
    assert eh_center_tap('T', 'sem valor') is False
    assert eh_center_tap(None, float('nan')) is False


# ═════════════════════════════════════════════════════════════════════════════
# A tensão que a carga declara
# ═════════════════════════════════════════════════════════════════════════════

def test_monofasico_de_220_127_ve_127_e_nao_220():
    """O erro que motivou tudo isto, no menor caso que o exibe.

    São 80.693 transformadores só numa distribuidora. O limiar antigo perguntava
    se a tensão passava de 0,30 kV para decidir se dividia por √3; 0,22 não
    passava, então a carga saía declarada a 220 V fase-neutro.
    """
    assert kv_bt_para_fases(1, (0.22, 0.22 / math.sqrt(3))) == pytest.approx(0.127, abs=1e-3)
    assert kv_bt_para_fases(3, (0.22, 0.22 / math.sqrt(3))) == pytest.approx(0.22)


def test_center_tap_de_440_ve_a_metade_e_nao_a_linha_sobre_raiz_de_tres():
    """220 V na perna, não 254.

    A regra geral divide por √3; o center-tap não a segue, e como as duas contas
    dão números plausíveis, discordar aqui não produz erro nenhum — só uma rede
    inteira modelada 15% acima.
    """
    assert kv_bt_para_fases(1, (0.44, 0.22)) == pytest.approx(0.22)


def test_sem_cadastro_cai_na_constante():
    """O último recurso continua existindo, e continua sendo o último."""
    assert kv_bt_para_fases(3) == pytest.approx(TENSAO_BT_KV)
    assert kv_bt_para_fases(1) == pytest.approx(TENSAO_BT_KV / math.sqrt(3), abs=1e-4)


def test_so_a_tensao_de_linha_ainda_e_aceita():
    """Quem chamar de fora com um número só recebe a leitura em estrela."""
    assert kv_bt_para_fases(1, 0.38) == pytest.approx(0.2194, abs=1e-3)
    assert kv_bt_para_fases(2, 0.38) == pytest.approx(0.38)


# ═════════════════════════════════════════════════════════════════════════════
# O par que sai do cadastro
# ═════════════════════════════════════════════════════════════════════════════

def _untrmt(linhas):
    return pd.DataFrame(linhas)


def test_o_par_por_trafo_sai_do_tip_trafo():
    m = _kv_bt_por_trafo(_untrmt([
        {'COD_ID': 'a', 'TIP_TRAFO': 'T',  'TEN_LIN_SE': 0.38},
        {'COD_ID': 'b', 'TIP_TRAFO': 'T',  'TEN_LIN_SE': 0.22},
        {'COD_ID': 'c', 'TIP_TRAFO': 'MT', 'TEN_LIN_SE': 0.44},
        {'COD_ID': 'd', 'TIP_TRAFO': 'MT', 'TEN_LIN_SE': 0.23},
    ]))
    assert m['a'][1] == pytest.approx(0.2194, abs=1e-3)   # 380/220
    assert m['b'][1] == pytest.approx(0.127, abs=1e-3)    # 220/127
    assert m['c'][1] == pytest.approx(0.22)               # perna de 440/220
    assert m['d'][1] == pytest.approx(0.115)              # perna de 230/115
    assert [v[0] for v in m.values()] == [0.38, 0.22, 0.44, 0.23]


def test_trafo_sem_tensao_utilizavel_fica_de_fora():
    """Fora do mapa a carga cai na constante, que é melhor que cair em `nan`."""
    m = _kv_bt_por_trafo(_untrmt([
        {'COD_ID': 'a', 'TIP_TRAFO': 'T', 'TEN_LIN_SE': float('nan')},
        {'COD_ID': 'b', 'TIP_TRAFO': 'T', 'TEN_LIN_SE': 0.0},
        {'COD_ID': 'c', 'TIP_TRAFO': 'T', 'TEN_LIN_SE': None},
    ]))
    assert m == {}


# ═════════════════════════════════════════════════════════════════════════════
# As bases do Set VoltageBases
# ═════════════════════════════════════════════════════════════════════════════

def test_a_rede_conhecida_nao_pede_base_nova():
    """A garantia de que isto não mexe nos casos já publicados."""
    assert _bases_extras(_untrmt([
        {'COD_ID': 'a', 'TIP_TRAFO': 'T',  'TEN_LIN_SE': 0.38},
        {'COD_ID': 'b', 'TIP_TRAFO': 'MT', 'TEN_LIN_SE': 0.44},
        {'COD_ID': 'c', 'TIP_TRAFO': 'T',  'TEN_LIN_SE': 0.22},
    ])) == []


def test_perna_de_115_pede_a_base_que_a_põe_em_1_pu():
    """0,115 × √3 = 0,199.

    Sem ela a barra de 117 V casava com 0,127 — que o OpenDSS lê como tensão de
    linha e divide por √3 — e aparecia a 1,59 pu. A tensão estava certa o tempo
    todo; era a régua que estava errada, e `verificar` reprovava o alimentador.
    """
    assert _bases_extras(_untrmt([
        {'COD_ID': 'a', 'TIP_TRAFO': 'MT', 'TEN_LIN_SE': 0.23},
    ])) == [pytest.approx(0.1992, abs=1e-3)]


def test_sem_transformadores_nao_ha_base_extra():
    assert _bases_extras(None) == []
    assert _bases_extras(pd.DataFrame()) == []


# ═════════════════════════════════════════════════════════════════════════════
# A lista deriva do que o emissor escreveu — e só disso
# ═════════════════════════════════════════════════════════════════════════════
#
# O OpenDSS toma a tensão fase-neutro do primeiro nó da barra a vazio,
# multiplica por √3 e casa com o valor mais próximo da lista. Sempre por √3.
# Logo a base que põe uma barra em 1,0 pu é kv × √3 para enrolamento de uma
# perna e o próprio kv para o trifásico, cujo `kv` escrito já é de linha.

def test_center_tap_de_127_pede_0_220_e_nao_0_254():
    """Duas pernas de 127 V: 0,127 × √3 = 0,220. Não é o 0,254 do cadastro."""
    assert bases_do_caso({'bt_a': 0.127}, {'bt_a': [1, 2]}) == [0.22]


def test_trifasico_entra_com_o_proprio_kv():
    assert bases_do_caso({'bt_a': 0.22}, {'bt_a': [1, 2, 3]}) == [0.22]
    assert bases_do_caso({'bt_a': 0.38}, {'bt_a': [1, 2, 3]}) == [0.38]


def test_perna_de_220_do_mrt_pede_0_381_e_nao_0_440():
    """O 0,440 da lista fixa, posto lá para o MRT, é 254 V fase-neutro.

    Uma perna de 220 V medida contra ele vale 0,866 pu. O que a põe em 1,0 é
    0,22 × √3 = 0,381 — que é, a menos de 1 mV, o 0,380 da rede trifásica.
    """
    assert bases_do_caso({'bt_a': 0.22}, {'bt_a': [1, 2]}) == [pytest.approx(0.3811, abs=1e-3)]


def test_bases_iguais_a_menos_da_tolerancia_viram_uma_so():
    """0,3811 e 0,380 na mesma lista fariam barras iguais medirem contra bases
    diferentes: o OpenDSS escolheria a mais próxima barra a barra."""
    bases = bases_do_caso({'bt_a': 0.22, 'bt_b': 0.38},
                          {'bt_a': [1], 'bt_b': [1, 2, 3]})
    assert bases == [0.38], 'quando coincidem, fica o nominal de linha do trifasico'
    # e a ordem em que o emissor escreveu nao muda a escolha
    bases = bases_do_caso({'bt_b': 0.38, 'bt_a': 0.22},
                          {'bt_a': [1], 'bt_b': [1, 2, 3]})
    assert bases == [0.38]


def test_a_lista_e_curta_de_proposito():
    """Um caso só de center-tap 220/127 sai com UMA base de baixa.

    Cada base a mais é uma que o solve a vazio escolhe por engano quando a
    rede está 5% acima do nominal. Medido: com 0,240 e 0,220 na lista, um
    center-tap a vazio em 133,5 V (fonte em 1,02 e Ferranti no tronco) casou
    com 0,240 em 13.545 barras — 83% da baixa "precária" com a tensão certa.
    """
    kv = {'bt_%d' % i: 0.127 for i in range(50)}
    nos = {b: [1, 2] for b in kv}
    assert bases_do_caso(kv, nos) == [0.22]


def test_sem_secundario_emitido_nao_ha_de_onde_derivar():
    assert bases_do_caso(None, None) == []
    assert bases_do_caso({}, {}) == []
    assert bases_do_caso({'bt_a': 'x', 'bt_b': 0, 'bt_c': float('nan')}, {}) == []


def test_o_master_declara_so_as_bases_do_caso(tmp_path):
    """O que vai para o `Set VoltageBases` é a MT e o que os secundários pedem."""
    saida = tmp_path / 'Master.dss'
    gerar_master('F', str(saida), mvasc3=100, mvasc1=100,
                 kv_secundario={'bt_a': 0.127, 'bt_b': 0.22},
                 nos_secundario={'bt_a': [1, 2], 'bt_b': [1, 2, 3]})
    texto = saida.read_text(encoding='utf-8')
    linha = [l for l in texto.splitlines() if l.startswith('Set VoltageBases')][0]
    bases = [float(x) for x in linha.split('[')[1].rstrip(']').split()]
    assert bases[1:] == [0.22], linha
    assert 0.24 not in bases and 0.44 not in bases


def test_sem_secundarios_o_master_cai_na_lista_de_reserva(tmp_path):
    """Sem emissor de onde derivar, vale o que sempre valeu — e dito no arquivo."""
    saida = tmp_path / 'Master.dss'
    gerar_master('F', str(saida), mvasc3=100, mvasc1=100)
    texto = saida.read_text(encoding='utf-8')
    linha = [l for l in texto.splitlines() if l.startswith('Set VoltageBases')][0]
    assert '0.380' in linha and '0.440' in linha and '0.127' in linha
    assert 'reserva' in texto


# ═════════════════════════════════════════════════════════════════════════════
# O center-tap multifásico: linha não é fase-neutro × √3
# ═════════════════════════════════════════════════════════════════════════════

def test_center_tap_multifasico_declara_a_perna_vezes_raiz_3():
    """0,220 × √3 = 0,381, e não os 0,440 entre as pernas.

    As duas pernas estão a 180°, então linha = 2 × fase-neutro. O OpenDSS
    divide a `kV` de elemento multifásico em estrela por √3 de qualquer jeito,
    e declarar os 440 V reais punha o nominal em 254 V onde a perna tem 220 —
    15,5% acima, num modelo ZIP com metade de impedância constante.
    """
    assert kv_bt_para_fases(2, (0.44, 0.22)) == pytest.approx(0.3811, abs=1e-3)


def test_na_estrela_a_regra_devolve_o_mesmo_numero_de_sempre():
    """A garantia de que isto não mexe nas redes 380/220 e 220/127.

    Ali linha JÁ É fase-neutro × √3, e os dois caminhos coincidem — é por isso
    que a diferença passou despercebida por tanto tempo.
    """
    assert kv_bt_para_fases(3, (0.38, 0.38 / math.sqrt(3))) == pytest.approx(0.38)
    assert kv_bt_para_fases(2, (0.38, 0.38 / math.sqrt(3))) == pytest.approx(0.38)
    assert kv_bt_para_fases(3, (0.22, 0.22 / math.sqrt(3))) == pytest.approx(0.22)


def test_a_perna_sozinha_nao_muda():
    """Monofásico continua declarando a fase-neutro, que é o que ele vê."""
    assert kv_bt_para_fases(1, (0.44, 0.22)) == pytest.approx(0.22)
    assert kv_bt_para_fases(1, (0.38, 0.38 / math.sqrt(3))) == pytest.approx(0.2194, abs=1e-3)


def _gd_center_tap(fas_con, tmp_path):
    """Emite a GD de uma usina num center-tap 440/220 e devolve o GD.dss."""
    import pandas as pd

    from bdgdcase.modelo.geracao import gerar_gd

    ugbt = pd.DataFrame([{
        'COD_ID': 'u1', 'PAC': 'BT_1', 'UNI_TR_MT': 'T1', 'POT_INST': 75.0,
        'FAS_CON': fas_con, 'CEG_GD': 'GD.SC.000.000.000', 'MUN': '0',
        'DAT_CON': '01/01/1951',
        **{'ENE_%02d' % i: 8000 for i in range(1, 13)}
    }])
    untrmt = pd.DataFrame([{
        'COD_ID': 'T1', 'POT_NOM': 25.0, 'TIP_TRAFO': 'MT', 'TEN_LIN_SE': 0.44,
        'FAS_CON_P': 'A', 'FAS_CON_S': 'ABN', 'PAC_1': 'MT_1', 'PAC_2': 'BT_1',
    }])
    saida = tmp_path / 'GD.dss'
    gerar_gd(ugbt, None, None, None, None, None, untrmt, str(saida))
    return saida.read_text(encoding='utf-8')


def test_a_usina_de_center_tap_usa_as_duas_pernas_que_o_cadastro_pede(tmp_path):
    """`FAS_CON=ABN` num center-tap é atendimento a três fios: duas pernas.

    A carga do mesmo poste já saía assim; a usina saía numa perna só, com o
    comentário de que era "a física do trafo". Não era: todo o kW numa perna
    dobra a corrente nela e deixa a outra vazia. Medido num alimentador da
    Celesc, 116 usinas assim — a perna injetada em 320,5 V contra 218,9 na
    outra, e o caso em 1,461 pu; ligando as duas, 266 V nas duas e 1,215.
    """
    texto = _gd_center_tap('ABN', tmp_path)
    assert 'phases=2' in texto, texto
    assert '.1.2.0' in texto, texto
    # e a tensão declarada é a perna × √3, não os 440 entre pernas
    assert 'kv=0.3811' in texto or 'kv=0.3810' in texto, texto


def test_a_usina_de_uma_perna_continua_numa_perna(tmp_path):
    """`FAS_CON=AN` pede uma perna, e é uma perna que ela recebe — a 220 V."""
    texto = _gd_center_tap('AN', tmp_path)
    assert 'phases=1' in texto, texto
    assert 'kv=0.2200' in texto, texto
