# -*- coding: utf-8 -*-
"""O cadastro incompleto, e o que o conversor faz com ele.

Nenhum dos defeitos aqui levanta exceção. Todos produzem um caso `.dss` que
compila, e alguns que até resolvem — só que errado, ou não resolvem por um
motivo que a mensagem do OpenDSS não deixa achar. São os que mais custam a
encontrar, e por isso os que mais merecem teste.
"""
import pandas as pd
import pytest

from bdgdcase.modelo.cadastro import _linecode_ramal, _num
from bdgdcase.modelo.config import CAP_SE_KVAR_MINIMO, IMPEDANCIAS_RAMAL
from bdgdcase.modelo.rede import gerar_capacitores, gerar_linecodes
from bdgdcase.modelo.topologia import _buses_da_rede_mt


# ═════════════════════════════════════════════════════════════════════════════
# O número que não é número
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize('valor, esperado', [
    (75.0, 75.0),
    (0, 0.0),                      # zero é valor, não ausência
    (None, 9.0),
    (float('nan'), 9.0),           # o caso que o `or` não pegava
    (float('inf'), 9.0),
    ('', 9.0),
    ('  ', 9.0),
    ('nan', 9.0),
    ('-', 9.0),
    ('12.5', 12.5),
    ('12,5', 12.5),                # CSV gravado em locale brasileiro
    ('não é número', 9.0),
    ([], 9.0),
])
def test_num_devolve_numero_ou_o_padrao(valor, esperado):
    assert _num(valor, 9.0) == esperado


def test_o_nan_e_verdadeiro_e_por_isso_o_or_nao_protegia():
    """A razão de `_num` existir, escrita como teste.

    `float(row.get('POT_NOM', 75) or 75)` parece defensivo. `NaN` é verdadeiro,
    então o `or` não dispara, e o `.dss` recebe `kva=nan` — que o OpenDSS aceita.
    """
    nan = float('nan')
    assert bool(nan) is True                 # a armadilha
    assert (nan or 75) != 75                 # o `or` não salva
    assert _num(nan, 75) == 75               # isto salva


# ═════════════════════════════════════════════════════════════════════════════
# LineCode citado é LineCode que existe
# ═════════════════════════════════════════════════════════════════════════════

def test_ramal_subterraneo_cai_no_aereo_em_vez_de_citar_o_que_nao_existe():
    """`IMPEDANCIAS_RAMAL` só tem a variante aérea, de propósito.

    Nas três bases medidas os 5,6 milhões de ramais são aéreos, e definir a
    variante subterrânea encheria todo caso gerado de LineCodes que ninguém
    cita. Quem tiver um ramal enterrado não fica sem trecho.
    """
    assert (1, 'SUB') not in IMPEDANCIAS_RAMAL
    assert _linecode_ramal(1, 'SUB') == 'LT_RL_1F_AER'
    assert _linecode_ramal(1, 'AER') == 'LT_RL_1F_AER'
    assert _linecode_ramal(3, 'AER') == 'LT_RL_3F_AER'


def test_condutor_ausente_do_segcon_ganha_generico(tmp_path, capsys):
    """Um `TIP_CND` sem linha no SEGCON derrubava a compilação inteira.

    O OpenDSS recusa o arquivo citando um nome que não está em lugar nenhum do
    cadastro, e quem for procurar não acha — porque o problema é a ausência.
    """
    ssdmt = pd.DataFrame({'TIP_CND': ['CABO_A', 'CABO_FANTASMA']})
    segcon = pd.DataFrame({'COD_ID': ['CABO_A'], 'R1': [0.3], 'X1': [0.2],
                           'R0': [0.5], 'X0': [0.6], 'CNOM': [200.0]})
    saida = tmp_path / 'LineCodes.dss'
    gerar_linecodes(segcon, ssdmt, None, None, saida)

    texto = saida.read_text(encoding='utf-8')
    assert 'New LineCode.CND_CABO_A ' in texto
    assert 'New LineCode.CND_CABO_FANTASMA ' in texto
    assert 'ausente do SEGCON' in texto
    assert 'nao estao no SEGCON' in capsys.readouterr().out


def test_segcon_com_celula_vazia_nao_escreve_nan(tmp_path):
    """O `except ValueError` antigo pegava o texto e deixava passar o `NaN`."""
    ssdmt = pd.DataFrame({'TIP_CND': ['CABO_A']})
    segcon = pd.DataFrame({'COD_ID': ['CABO_A'], 'R1': [float('nan')],
                           'X1': [None], 'R0': [float('nan')],
                           'X0': [float('nan')], 'CNOM': [float('nan')]})
    saida = tmp_path / 'LineCodes.dss'
    gerar_linecodes(segcon, ssdmt, None, None, saida)
    texto = saida.read_text(encoding='utf-8')
    assert 'nan' not in texto.lower().replace('nphases', '')


# ═════════════════════════════════════════════════════════════════════════════
# O capacitor
# ═════════════════════════════════════════════════════════════════════════════

def _uncrmt(linhas):
    return pd.DataFrame(linhas)


def _emitir(tmp_path, linhas, buses=None):
    saida = tmp_path / 'Capacitores.dss'
    gerar_capacitores(_uncrmt(linhas), saida, buses_mt=buses)
    return saida.read_text(encoding='utf-8')


def test_o_prefixo_manda_quando_existe(tmp_path):
    """Na base que usa prefixo, banco de rede vai a 1.800 kVAr.

    As faixas de potência dela se cruzam — rede de 100 a 1.800, subestação de
    900 a 7.200 —, então nenhum critério de tamanho os separaria sem errar. O
    prefixo tem de vencer, inclusive para um banco de rede grande.
    """
    texto = _emitir(tmp_path, [
        {'COD_ID': 'g', 'PAC_1': 'B1', 'FAS_CON': 'ABC',
         'POT_NOM_KVAR': 3600.0, 'DESCR': 'RD-0293889'},
    ], buses={'B1'})
    assert 'states=[0]' not in texto, 'banco de rede não deve sair desligado'


def test_sem_prefixo_a_potencia_decide(tmp_path):
    """Numa base o DESCR é código puro; noutra está vazio nos 18 registros."""
    grande = _emitir(tmp_path, [
        {'COD_ID': 'g', 'PAC_1': 'B1', 'FAS_CON': 'ABC',
         'POT_NOM_KVAR': CAP_SE_KVAR_MINIMO, 'DESCR': '90100719'},
    ], buses={'B1'})
    assert 'states=[0]' in grande

    pequeno = _emitir(tmp_path, [
        {'COD_ID': 'p', 'PAC_1': 'B1', 'FAS_CON': 'ABC',
         'POT_NOM_KVAR': 600.0, 'DESCR': ''},
    ], buses={'B1'})
    assert 'states=[0]' not in pequeno


def test_banco_fora_da_rede_nao_vira_elemento(tmp_path, capsys):
    """A ilha de uma barra só, que derrubou dois alimentadores inteiros.

    O banco de subestação fica no barramento da SE, a montante da cabeceira: o
    SSDMT do alimentador não o alcança. Ligado, ele injetava reativo em coisa
    nenhuma e ninguém percebia. Desligado — que é o padrão —, a barra ficava sem
    caminho para a terra e a matriz de admitância, singular.
    """
    texto = _emitir(tmp_path, [
        {'COD_ID': 'dentro', 'PAC_1': 'B1', 'FAS_CON': 'ABC',
         'POT_NOM_KVAR': 600.0, 'DESCR': 'RD-1'},
        {'COD_ID': 'fora', 'PAC_1': 'BARRA_DA_SE', 'FAS_CON': 'ABC',
         'POT_NOM_KVAR': 3600.0, 'DESCR': '90100719'},
    ], buses={'B1'})
    assert 'CAP_dentro' in texto
    assert 'CAP_fora' not in texto
    assert 'nao e barra desta rede' in capsys.readouterr().out


def test_sem_a_lista_de_barras_nada_e_descartado(tmp_path):
    """Quem chamar sem `buses_mt` mantém o comportamento de antes."""
    texto = _emitir(tmp_path, [
        {'COD_ID': 'x', 'PAC_1': 'DESCONHECIDA', 'FAS_CON': 'ABC',
         'POT_NOM_KVAR': 600.0, 'DESCR': 'RD-1'},
    ])
    assert 'CAP_x' in texto


def test_as_barras_da_rede_saem_sanitizadas():
    """Têm de casar com o `sanitizar_bus` que o emissor usa, ou tudo é órfão."""
    ssdmt = pd.DataFrame({'PAC_1': ['MT-1', 'MT-2'], 'PAC_2': ['MT-2', 'MT/3']})
    assert _buses_da_rede_mt(ssdmt) == {'MT_1', 'MT_2', 'MT_3'}
    assert _buses_da_rede_mt(None) == set()
