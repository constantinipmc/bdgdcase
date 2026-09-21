# -*- coding: utf-8 -*-
"""Carga instalada: da unidade consumidora e do ponto de iluminação.

Dois campos, dois defeitos diferentes, na mesma base:

- `CAR_INST` das unidades traz `max(ENE)/720` — a potência média do mês de maior
  consumo — em vez da carga instalada. Mediana de 0,37 kW por unidade, contra
  9,00 e 10,47 nas outras duas bases;
- `CAR_INST` do ponto de iluminação vem **dez vezes maior**: 1,069 kW por
  luminária, contra 0,103. Como a carga de IP é a soma dos pontos do
  transformador, o erro aparece multiplicado.

O que estes testes guardam, mais que as contas, são as **duas armadilhas** que a
primeira versão da correção caiu, ambas capazes de derrubar um alimentador que
funcionava:

1. corrigir pela tabela em vez de por unidade. A tabela é MISTA: num poste
   medido convivem `2,09981528` — o artefato — e `41,7`, que é carga real. O
   fator de tabela pôs 1.018 kW num ramal residencial;
2. usar a referência da baixa na média. São 29,5 kWh/kW numa e 88,7 na outra,
   e a confusão custou sete cargas de 1.448 kW.
"""
import pandas as pd
import pytest

from bdgdcase.modelo.cadastro import (
    CONSUMO_MES_PICO_KWH_POR_KW,
    CONSUMO_MES_PICO_KWH_POR_KW_MT,
    HORAS_IP_MES,
    HORAS_MES,
    corrigir_car_inst,
    corrigir_car_inst_pip)


def _poste(cars, energias_por_uc):
    """Um poste com `len(cars)` unidades e os doze meses de cada uma."""
    linha = {'QTD_UC': len(cars),
             'LISTA_CAR_INST': ';'.join(str(c) for c in cars)}
    for m in range(12):
        linha['LISTA_ENE_%02d' % (m + 1)] = ';'.join(
            str(e[m] if m < len(e) else 0) for e in energias_por_uc)
    linha['SOMA_CAR_INST'] = sum(float(c or 0) for c in cars)
    return linha


def _tabela(postes):
    return pd.DataFrame(postes)


def _lista(df, i=0):
    return [float(x) for x in df['LISTA_CAR_INST'].iloc[i].split(';') if x]


# ═════════════════════════════════════════════════════════════════════════════
# A unidade consumidora
# ═════════════════════════════════════════════════════════════════════════════

def test_a_carga_instalada_de_verdade_nao_e_tocada():
    """Um poste de base sadia atravessa sem mudar nada."""
    df, n = corrigir_car_inst(_tabela([
        _poste([9.0, 12.0], [[300] * 12, [400] * 12])]))
    assert n == 0
    assert _lista(df) == [9.0, 12.0]


def test_a_identidade_de_720_e_reposta_na_escala():
    pico = 288.0
    derivado = pico / HORAS_MES          # 0,4 kW declarado
    df, n = corrigir_car_inst(_tabela([
        _poste([derivado], [[0] * 10 + [pico, 100]])]))
    assert n == 1
    assert _lista(df)[0] == pytest.approx(pico / CONSUMO_MES_PICO_KWH_POR_KW,
                                          rel=1e-4)
    assert 8 < _lista(df)[0] < 11, 'a unidade de 0,4 kW declarado tem ~10 kW'


def test_o_poste_misto_e_corrigido_unidade_a_unidade():
    """A armadilha nº 1, com os números que a produziram.

    `2,09981528` é o artefato — 2,09981528 × 720 = 1511,87 — e `41,7` é carga
    instalada de verdade, numa unidade sem histórico. Corrigir pela tabela
    multiplicava os 41,7 por 24 e punha 1.018 kW num ramal residencial.
    """
    artefato = 2.09981528
    pico = artefato * HORAS_MES
    df, n = corrigir_car_inst(_tabela([
        _poste([artefato, 41.7], [[pico] * 12, [0] * 12])]))
    assert n == 1, 'só a unidade derivada deve ser corrigida'
    corrigida, intacta = _lista(df)
    assert intacta == 41.7, 'a carga real tem de sobreviver intacta'
    assert 40 < corrigida < 60


def test_a_soma_e_refeita_da_lista_corrigida():
    """Escalar a soma seria errado num poste misto."""
    artefato = 1.0
    pico = artefato * HORAS_MES
    df, _ = corrigir_car_inst(_tabela([
        _poste([artefato, 30.0], [[pico] * 12, [0] * 12])]))
    assert df['SOMA_CAR_INST'].iloc[0] == pytest.approx(sum(_lista(df)), rel=1e-6)


def test_a_media_tem_referencia_propria():
    """A armadilha nº 2. Três vezes de diferença, e um caso a 0,58 pu."""
    assert CONSUMO_MES_PICO_KWH_POR_KW_MT > 2.5 * CONSUMO_MES_PICO_KWH_POR_KW
    pico = 7200.0
    tab = _tabela([_poste([pico / HORAS_MES], [[pico] * 12])])
    bt, _ = corrigir_car_inst(tab)
    mt, _ = corrigir_car_inst(tab, CONSUMO_MES_PICO_KWH_POR_KW_MT)
    assert _lista(bt)[0] == pytest.approx(pico / CONSUMO_MES_PICO_KWH_POR_KW, rel=1e-4)
    assert _lista(mt)[0] == pytest.approx(pico / CONSUMO_MES_PICO_KWH_POR_KW_MT, rel=1e-4)
    assert _lista(mt)[0] < _lista(bt)[0] / 2.5


def test_a_unidade_sem_energia_fica_como_veio():
    """O limite honesto: sem energia não há como saber, e não se inventa."""
    df, n = corrigir_car_inst(_tabela([_poste([0.4], [[0] * 12])]))
    assert n == 0
    assert _lista(df) == [0.4]


@pytest.mark.parametrize('tabela', [
    None,
    pd.DataFrame(),
    pd.DataFrame({'OUTRA': [1]}),
])
def test_tabela_sem_o_campo_nao_derruba(tabela):
    df, n = corrigir_car_inst(tabela)
    assert n == 0


# ═════════════════════════════════════════════════════════════════════════════
# O ponto de iluminação pública
# ═════════════════════════════════════════════════════════════════════════════

def _pip(cars, picos):
    linha = {'CAR_INST': cars}
    for m in range(1, 13):
        linha['ENE_%02d' % m] = picos if m == 6 else [0] * len(cars)
    return pd.DataFrame(linha)


def _car(df):
    return list(pd.to_numeric(df['CAR_INST'], errors='coerce'))


def test_a_luminaria_na_escala_certa_nao_e_tocada():
    """0,103 kW acesa 354 h por mês: 36,5 kWh. É o caso são, e não se mexe."""
    n = 40
    df, quantos, horas = corrigir_car_inst_pip(
        _pip([0.103] * n, [0.103 * HORAS_IP_MES] * n))
    assert quantos == 0
    assert horas == pytest.approx(HORAS_IP_MES, rel=0.01)
    assert _car(df)[0] == 0.103


def test_a_potencia_dez_vezes_maior_e_reposta():
    """1,069 kW por ponto daria 30 h acesas por mês. Não existe."""
    n, real = 40, 0.089
    df, quantos, horas = corrigir_car_inst_pip(
        _pip([1.069] * n, [real * HORAS_IP_MES] * n))
    assert quantos == n
    assert horas < 100
    assert _car(df)[0] == pytest.approx(real, rel=1e-6)


def test_o_campo_que_traz_ENERGIA_e_reposto():
    """O caso que derrubou a versão anterior desta função.

    Numa base `CAR_INST` do PIP é **exatamente** o maior mês de energia — a
    razão dá 1,00 —, e o campo é kWh, não kW. Uma correção por potência de dez
    daria 32 W onde o `POT_LAMP` da própria base diz 100.
    """
    n, energia = 40, 32.04
    df, quantos, horas = corrigir_car_inst_pip(
        _pip([energia] * n, [energia] * n))
    assert horas == pytest.approx(1.0, rel=0.01)
    assert quantos == n
    esperado = energia / HORAS_IP_MES
    assert _car(df)[0] == pytest.approx(esperado, rel=1e-6)
    assert 0.07 < _car(df)[0] < 0.12, 'uma luminaria de ~90 W'


def test_a_faixa_de_tolerancia_e_larga():
    """Separar potência de energia, não discutir se foram 300 ou 400 horas."""
    n = 40
    for horas in (200, 354, 600):
        df, quantos, _h = corrigir_car_inst_pip(
            _pip([0.1] * n, [0.1 * horas] * n))
        assert quantos == 0, 'horas=%s deveria passar intacto' % horas


def test_o_ponto_sem_energia_fica_como_veio():
    """Sem energia não há de onde tirar, e não se inventa."""
    n = 40
    cars = [1.069] * n
    picos = [0.089 * HORAS_IP_MES] * (n - 5) + [0] * 5
    df, quantos, _h = corrigir_car_inst_pip(_pip(cars, picos))
    assert quantos == n - 5
    assert _car(df)[-1] == 1.069


def test_amostra_pequena_mas_unanime_autoriza():
    """Cinco pontos que dizem a MESMA coisa bastam; o piso de 20 barrava-os.

    Medido num alimentador da Copel de 5 pontos: a razão `max(ENE)/CAR_INST`
    dava 1,00 nos cinco — o campo é a energia do mês, sem margem para dúvida —
    e o piso deixava o valor cru passar. Saíam 172 kW de iluminação pública
    num alimentador cujo pico inteiro é 190 kW, contra os 0,49 kW que a energia
    publicada sustenta.
    """
    df, quantos, horas = corrigir_car_inst_pip(_pip([1.05] * 5, [0.105 * 354] * 5))
    assert quantos == 5
    assert horas == pytest.approx(35.4, abs=0.1)
    assert _car(df)[0] == pytest.approx(0.105, abs=1e-3)


def test_amostra_pequena_e_dispersa_continua_nao_autorizando():
    """O que a amostra pequena não sustenta é uma mediana RUIDOSA.

    É para isto que o piso existe, e é por isto que ele continua: com cinco
    pontos discordando entre si por mais de um fator de dois, a mediana não
    diz de que convenção se trata.
    """
    cars = [1.0, 1.0, 1.0, 1.0, 1.0]
    picos = [2.0, 8.0, 30.0, 120.0, 350.0]        # razões de 2 a 350
    df, quantos, horas = corrigir_car_inst_pip(_pip(cars, picos))
    assert quantos == 0
    assert _car(df) == cars


def test_a_amostra_grande_continua_valendo_mesmo_dispersa():
    """Vinte pontos autorizam como sempre autorizaram, concordem ou não."""
    n = 40
    cars = [1.0] * n
    picos = [10.0, 40.0] * (n // 2)               # dispersa, mas n >= 20
    df, quantos, _h = corrigir_car_inst_pip(_pip(cars, picos))
    assert quantos == n


@pytest.mark.parametrize('tabela', [
    None,
    pd.DataFrame(),
    pd.DataFrame({'CAR_INST': [0.1] * 40}),      # sem colunas de energia
])
def test_pip_sem_o_que_aferir_fica_intacto(tabela):
    df, quantos, horas = corrigir_car_inst_pip(tabela)
    assert quantos == 0 and horas is None
