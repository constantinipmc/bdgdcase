# -*- coding: utf-8 -*-
"""A potência da geração distribuída, e o campo que não traz o que promete.

`POT_INST` deveria ser potência instalada, e em duas das três bases medidas é.
Na terceira, `POT_INST × 720` é **exatamente** o maior dos doze valores mensais
de energia — o campo carrega a potência média do mês de maior geração. Uma
unidade de 5 kW aparece com 0,7.

O que este arquivo guarda, mais que a conta, é o discriminante: a identidade
`× 720` é sobre o dado, e por isso funciona numa tabela **mista** — a de média
de uma das bases tem valores limpos (112,5; 75; 300 kW) ao lado do artefato
(7,890278; 5,126389). Perguntar de que distribuidora se trata erraria nas duas
metades.
"""
import pytest

from bdgdcase.modelo.cadastro import (
    HORAS_MES,
    RENDIMENTO_ANO_KWH_POR_KW,
    RENDIMENTO_MES_PICO_KWH_POR_KW,
    pot_inst_kw)


def _linha(pot, energias=()):
    """Uma linha de UGBT/UGMT com os doze meses preenchidos por posição."""
    row = {'POT_INST': pot}
    for i in range(12):
        row['ENE_%02d' % (i + 1)] = energias[i] if i < len(energias) else 0
    return row


# ═════════════════════════════════════════════════════════════════════════════
# O campo que traz potência de verdade não é tocado
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize('pot, energias', [
    (5.0,   [400, 420, 380, 350, 300, 250, 260, 300, 350, 400, 430, 450]),
    (75.0,  [6000, 6200] + [5000] * 10),
    (112.5, [11000] * 12),
    (3.0,   []),                      # sem energia declarada: nada a conferir
])
def test_a_potencia_declarada_e_respeitada(pot, energias):
    kw, origem = pot_inst_kw(_linha(pot, energias))
    assert kw == pot
    assert origem == 'cadastro'


def test_a_unidade_sem_potencia_passa_como_esta():
    for v in (0, 0.0, -1, None, ''):
        kw, origem = pot_inst_kw(_linha(v))
        assert origem == 'cadastro'


# ═════════════════════════════════════════════════════════════════════════════
# O campo derivado é reconhecido e reconstruído
# ═════════════════════════════════════════════════════════════════════════════

def test_a_identidade_de_720_denuncia_o_campo_derivado():
    """O caso real, com os números medidos na base.

    `POT_INST=0.454167` e `max(ENE)=327`: 0.454167 × 720 = 327,0 exato. A
    detecção é pelo pico, porque foi dele que a distribuidora derivou o campo;
    a reconstrução é pelo ano.
    """
    energias = [0] * 10 + [327, 76]
    kw, origem = pot_inst_kw(_linha(327.0 / HORAS_MES, energias))
    assert origem == 'estimada'
    assert kw == pytest.approx(sum(energias) / RENDIMENTO_ANO_KWH_POR_KW, abs=1e-4)


def test_a_reconstrucao_multiplica_por_cerca_de_sete():
    """É o inverso do fator de capacidade, e é a ordem do erro que se corrigiu.

    Numa unidade cujos doze meses estão todos preenchidos, o mês de pico e o ano
    dão praticamente o mesmo — é nas leituras bimestrais que eles divergem.
    """
    declarado = 0.7
    pico = declarado * HORAS_MES
    energias = [pico] + [pico * 0.8] * 11
    kw, _ = pot_inst_kw(_linha(declarado, energias))
    assert kw == pytest.approx(sum(energias) / RENDIMENTO_ANO_KWH_POR_KW, abs=1e-4)
    # O multiplicador depende da forma do ano, e a faixa é larga de propósito:
    # nesta linha sintética, achatada em 0,8 do pico o ano inteiro, dá 9,7. Na
    # população real, com estação e meses zerados, a mediana medida é 6,75 — a
    # declarada de 0,71 kW vira 4,79, contra os 5,00 kW reais da base sadia.
    assert 5.0 < kw / declarado < 11.0, 'a ordem do desvio e o inverso do FC'


def test_a_deteccao_e_por_linha_e_nao_por_tabela():
    """A tabela mista, que é a razão de o discriminante ser sobre o dado."""
    limpa = _linha(112.5, [11000] * 12)
    derivada = _linha(7.890278, [7.890278 * HORAS_MES])
    assert pot_inst_kw(limpa)[1] == 'cadastro'
    assert pot_inst_kw(derivada)[1] == 'estimada'


def test_a_tolerancia_e_estreita():
    """Meio kWh. Foi o que deu zero falso positivo em 124.778 unidades."""
    pico = 500.0
    assert pot_inst_kw(_linha(pico / HORAS_MES, [pico]))[1] == 'estimada'
    # meio por cento fora já é outra coisa, e o cadastro manda
    assert pot_inst_kw(_linha(pico / HORAS_MES * 1.005, [pico]))[1] == 'cadastro'


def test_o_pico_e_o_maior_mes_e_nao_o_ultimo():
    """A unidade ligada em outubro tem meses zerados na frente."""
    energias = [0, 0, 0, 0, 0, 0, 0, 55, 259, 584, 706, 635]
    kw, origem = pot_inst_kw(_linha(706.0 / HORAS_MES, energias))
    assert origem == 'estimada'
    assert kw == pytest.approx(sum(energias) / RENDIMENTO_ANO_KWH_POR_KW, abs=1e-4)


def test_a_leitura_bimestral_nao_infla_a_potencia():
    """A razão de a reconstrução ser pelo ano, com os números do caso real.

    Um quinto das unidades tem três ou mais meses em zero: a leitura é bimestral
    ou trimestral, e fatura num mês a energia de dois ou três. O máximo mensal
    daquela unidade não é energia de um mês.

    Aqui, `max(ENE)=22114` daria 215 kW pelo mês de pico — num transformador de
    75 kVA, com a rede de baixa a 1,50 pu. A soma do ano dá 142.
    """
    energias = [0, 12286, 22114, 0, 9354, 12026, 0, 10275, 11645, 0, 9379, 16201]
    kw, origem = pot_inst_kw(_linha(22114.0 / HORAS_MES, energias))
    assert origem == 'estimada'
    assert 130 < kw < 155, 'pelo mes de pico daria 215'
    pelo_pico = max(energias) / RENDIMENTO_MES_PICO_KWH_POR_KW
    assert pelo_pico > 200 and kw < 0.75 * pelo_pico


def test_a_unidade_que_quase_nao_gerou_sai_pequena(caplog):
    """Um limite honesto da reconstrução, guardado para não ser esquecido.

    Se a base só publica a média do mês de pico, uma unidade que injetou 1 kWh no
    ano inteiro não tem como render número melhor. São ~1% dos casos na base
    medida, e continuam pequenos de propósito: inventar um piso seria inventar
    dado.
    """
    kw, origem = pot_inst_kw(_linha(1.1 / HORAS_MES, [1.1]))
    assert origem == 'estimada'
    assert kw < 0.5


# ═════════════════════════════════════════════════════════════════════════════
# A constante
# ═════════════════════════════════════════════════════════════════════════════

def test_o_rendimento_e_o_medido_e_nao_um_numero_bonito():
    """102,6 kWh/kW: mediana de 121.092 unidades de duas distribuidoras.

    Se alguém arredondar para 100 ou 120 sem medir de novo, este teste avisa —
    e o comentário da constante diz onde o número foi obtido.
    """
    assert RENDIMENTO_MES_PICO_KWH_POR_KW == pytest.approx(102.6)
    assert RENDIMENTO_ANO_KWH_POR_KW == pytest.approx(725.0)
    assert HORAS_MES == 720.0


# ═════════════════════════════════════════════════════════════════════════════
# A classificação da fonte, que depende da potência
# ═════════════════════════════════════════════════════════════════════════════

def test_a_potencia_errada_desclassifica_a_fonte():
    """O elo que fazia a geração solar virar não-solar, e gerar de madrugada.

    `fator_capacidade = ΣENE / (potência × 8760)`. Uma potência sete vezes menor
    dá um fator sete vezes maior, e o classificador usa "acima de 25% não pode
    ser solar". Medido: o fator mediano saía em 60% na base afetada, contra 7 a
    9% na sadia, e **446 de 463** unidades de um alimentador eram classificadas
    como não-solares.

    Não fica no rótulo: a não-solar recebe curva achatada e gera as 24 horas.
    É essa geração noturna que levanta a rede de baixa numa madrugada sem carga.
    """
    from bdgdcase.modelo.cadastro import _classificar_tipo_gd

    # uma unidade solar de ~10 kW: 1.100 kWh/mes no pico, 7.250 no ano
    energias = [1100, 900, 800, 600, 400, 350, 380, 500, 650, 800, 1000, 1100]
    declarado = max(energias) / HORAS_MES        # o que a base publica
    linha = _linha(declarado, energias)

    crua, fc_cru = _classificar_tipo_gd(linha)
    corrigida = pot_inst_kw(linha)[0]
    boa, fc_bom = _classificar_tipo_gd(linha, corrigida)

    assert fc_cru > 0.25, 'com a potencia crua o fator estoura o limite solar'
    assert crua != 'PV', 'e a unidade e classificada como nao-solar'
    assert fc_bom < 0.25
    assert boa == 'PV', 'com a potencia corrigida ela volta a ser solar'
    assert fc_cru / fc_bom == pytest.approx(corrigida / declarado, rel=1e-6)


def test_sem_potencia_a_classificacao_usa_o_que_ha():
    """Quem chamar sem a potência corrigida mantém o comportamento antigo."""
    from bdgdcase.modelo.cadastro import _classificar_tipo_gd

    linha = _linha(10.0, [1100] + [800] * 11)
    assert _classificar_tipo_gd(linha)[0] == _classificar_tipo_gd(linha, None)[0]


# ── A segunda assinatura: POT_INST acima do teto legal ───────────────────────
#
# Relato real, no alimentador 805160019 (Copel): a unidade
# `gd_mt_d0840521…` entrava com 8.661 kW e invertia o fluxo do alimentador
# inteiro. Na linha da BDGD, `POT_INST` = 8661,0 é **dígito por dígito** o
# `ENE_12` da mesma linha.
#
# Duas hipóteses foram medidas e descartadas antes de chegar à regra que ficou,
# e os dois testes a seguir guardam justamente o que elas erravam.

#: A linha do relato, com os doze meses como a base os publica.
RELATADA = (8661.0, (4782, 5970, 5134, 4277, 6516, 7929,
                     6394, 8109, 6092, 7456, 8543, 8661))


def test_geracao_acima_do_teto_legal_e_reconstruida_da_energia():
    """8.661 kW numa unidade geradora de consumidor não é geração distribuída.

    A Lei 14.300/2022 limita a minigeração a 5 MW. Acima disso é central
    geradora, com outro regime de conexão — não teria por que estar numa tabela
    de unidade geradora.
    """
    from bdgdcase.modelo.cadastro import LIMITE_MINIGD_KW

    pot, origem = pot_inst_kw(_linha(*RELATADA))
    assert origem == 'teto'
    assert pot < LIMITE_MINIGD_KW
    # a reconstrução é a energia do ano sobre o rendimento anual
    assert pot == pytest.approx(sum(RELATADA[1]) / RENDIMENTO_ANO_KWH_POR_KW,
                                rel=1e-6)


def test_a_identidade_com_o_mes_de_pico_nao_serve_de_criterio():
    """`POT_INST == max(ENE)` era a hipótese óbvia, e ela é falsa.

    Medida nas quatro bases extraídas, a identidade pegou duas unidades em
    3.318 — e uma delas era uma usina de 5 kW que gerou 5 kWh no ano inteiro.
    Ali a igualdade é coincidência, não defeito: a potência está certa, e a
    usina é que mal funcionou.

    Este teste fixa que essa unidade **não** é tocada. Se alguém trocar o teto
    pela identidade, é aqui que se descobre.
    """
    pot, origem = pot_inst_kw(_linha(5.0, (0,) * 11 + (5.0,)))
    assert (pot, origem) == (5.0, 'cadastro')


def test_a_razao_entre_potencia_e_energia_tambem_nao_serve():
    """A outra hipótese descartada: potência muito acima do que a energia sustenta.

    As maiores razões medidas são de usinas com potência plausível que quase não
    geraram — 45 kW com 161 kWh no ano, 3 kW com 8 kWh. A relatada, com razão
    78, fica no meio dessas. Cortar por razão mexeria em dezenas de unidades
    sadias para acertar uma.
    """
    # 45 kW que gerou 161 kWh no ano: razão ~200, e está certa
    pot, origem = pot_inst_kw(_linha(45.0, (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 161)))
    assert (pot, origem) == (45.0, 'cadastro')


def test_acima_do_teto_sem_energia_fica_como_esta_e_e_declarada():
    """Sem energia não há de onde reconstruir — e inventar seria pior.

    Aparar no teto poria 5 MW, que é tão arbitrário quanto os 9 MW declarados.
    A unidade entra como está, com origem própria, para que quem registra os
    ajustes possa dizê-la em voz alta.
    """
    pot, origem = pot_inst_kw(_linha(9000.0))
    assert (pot, origem) == (9000.0, 'teto_sem_energia')


def test_o_teto_nao_atrapalha_a_regra_dos_720():
    """As duas assinaturas convivem, e a dos 720 continua tendo precedência."""
    pico = 5681.0
    pot, origem = pot_inst_kw(_linha(pico / HORAS_MES, (0,) * 7 + (pico,)))
    assert origem == 'estimada'


def test_usina_grande_e_legitima_passa_intacta():
    """5 MW é o teto, e não um alvo: uma central de 4,5 MW é minigeração legal."""
    pot, origem = pot_inst_kw(_linha(4500.0, (600000,) * 12))
    assert (pot, origem) == (4500.0, 'cadastro')
