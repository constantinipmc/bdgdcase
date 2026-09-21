# -*- coding: utf-8 -*-
"""O caso gerado resolve de verdade no OpenDSS.

Todo o resto da suíte prova que a conversão é estável: os mesmos bytes saem
sempre. Estabilidade não é utilidade — um conversor pode escrever, com perfeita
reprodutibilidade, um modelo que não fecha. Este arquivo é o que fecha o ciclo.

O `importorskip` do topo não é mais sobre extra opcional — o OpenDSS é
dependência do pacote. Ele fica como rede: numa instalação incompleta, este
arquivo pula em vez de encher a saída de erros que apontariam para o conversor
quando o problema é o ambiente.
"""
import math

import pytest

pytest.importorskip('opendssdirect',
                    reason='o OpenDSS nao carregou (instalacao incompleta)')

from bdgdcase.solucao import (PASSOS_DIA, para_csv,  # noqa: E402
                              rodar_dia, verificar)
from test_conversao import ESPERADO, falta  # noqa: E402

pytestmark = [pytest.mark.solver, pytest.mark.dados,
              pytest.mark.skipif(falta, reason='caso de referência ausente')]


# ── Nível 1: o caso fecha ────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def check():
    return verificar(ESPERADO)


def test_o_caso_converge(check):
    assert check['convergiu'] is True


def test_o_circuito_nao_esta_vazio(check):
    assert check['barras'] > 0
    assert check['cargas'] > 0
    assert check['linhas'] > 0
    assert check['transformadores'] > 0


def test_fase_desconectada_nao_conta_como_subtensao():
    """Regressão: o limiar de fase ativa é 0,5 pu, não um valor simbólico.

    Uma rede real tem laterais monofásicas e bifásicas. O barramento declara
    três nós, e o nó da fase ausente fica flutuando, acoplado só
    capacitivamente — no IBA09 essas fases assentam entre 0,05 e 0,50 pu.

    Como a tensão de uma barra é a *pior* fase, um limiar frouxo faz uma barra
    com duas fases em 0,97 pu ser reportada em 0,06. Chegou a dar 689 barras
    "abaixo de 0,90 pu" onde havia 53, e o mapa saía vermelho numa rede sã.
    """
    from bdgdcase.solucao import PU_FASE_ATIVA
    assert PU_FASE_ATIVA == 0.5

    # E o mapa não pode usar outro: dia e mapa divergindo seria pior que
    # qualquer um dos dois errar sozinho.
    geo = pytest.importorskip('bdgdcase.interface.geometria')
    assert geo.PU_MORTO == PU_FASE_ATIVA


def test_o_minimo_do_dia_ignora_fase_flutuante(dia):
    """Nenhum ponto em operação fica abaixo de meia tensão nominal."""
    for v in dia['v_min_pu']:
        assert v > 0.5, 'fase flutuante entrou na estatística'


def test_as_tensoes_sao_fisicamente_plausiveis(check):
    assert 0.5 < check['v_min_pu'] <= check['v_med_pu'] <= check['v_max_pu'] < 1.5


def test_alimentador_sem_geracao_distribuida_roda(tmp_path):
    """Zero GD não pode derrubar o inventário — e derrubava.

    A coleção `PVsystems` do OpenDSSDirect é **falsa quando está vazia**, e o
    inventário escolhia entre as duas grafias que circulam com um `a or b`.
    Num alimentador sem nenhuma GD o primeiro nome, que existe, era descartado
    por ser falso, e o segundo levantava AttributeError na cara de quem abriu o
    mapa. O alimentador de exemplo tem quatro geradores e nunca exercitou esse
    caminho; um alimentador urbano sem GD é comum.

    A escolha passou a ser por existência do atributo, não por verdade dele.
    """
    for p in ESPERADO.iterdir():
        (tmp_path / p.name).write_bytes(p.read_bytes())
    (tmp_path / 'GD.dss').write_text('! sem geração distribuída\n',
                                     encoding='utf-8')
    r = rodar_dia(tmp_path, passos=4, detalhado=True)
    assert r['inventario']['gd'] == []
    assert r['inventario']['cargas']          # o resto do inventário continua
    assert len(r['p_kw']) == 4


def test_um_caso_quebrado_e_recusado(tmp_path):
    """Tensão de base errada por um fator dez tem de fazer `verificar` falhar.

    Sem este teste, `verificar` poderia estar aprovando qualquer coisa, e os
    testes acima passariam do mesmo jeito.
    """
    for p in ESPERADO.iterdir():
        (tmp_path / p.name).write_bytes(p.read_bytes())
    master = tmp_path / 'Master.dss'
    master.write_text(
        master.read_text(encoding='utf-8').replace('basekv=13.800',
                                                   'basekv=1.3800'),
        encoding='utf-8')
    # A mensagem depende de como o modelo quebra. Com a base dez vezes baixa o
    # circuito não energiza: nenhum nó chega a meia tensão, e é essa a queixa.
    with pytest.raises(RuntimeError,
                       match='plausível|convergiu|nenhuma fase energizada'):
        verificar(tmp_path)


def test_caso_com_base_alta_demais_tambem_e_recusado(tmp_path):
    """O outro lado do erro de base: tensões pu absurdamente altas."""
    for p in ESPERADO.iterdir():
        (tmp_path / p.name).write_bytes(p.read_bytes())
    master = tmp_path / 'Master.dss'
    master.write_text(
        master.read_text(encoding='utf-8').replace('basekv=13.800',
                                                   'basekv=138.00'),
        encoding='utf-8')
    with pytest.raises(RuntimeError,
                       match='plausível|convergiu|nenhuma fase energizada'):
        verificar(tmp_path)


def test_pasta_sem_master_diz_o_que_fazer(tmp_path):
    with pytest.raises(FileNotFoundError, match='converter'):
        verificar(tmp_path)


# ── Nível 2: o dia ───────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def dia():
    return rodar_dia(ESPERADO)


def test_o_dia_tem_noventa_e_seis_passos(dia):
    assert dia['passos'] == PASSOS_DIA
    for chave in ('p_kw', 'q_kvar', 'perdas_kw', 'v_min_pu', 'convergiu'):
        assert len(dia[chave]) == PASSOS_DIA, chave
    assert dia['horas'][0] == 0.0
    assert dia['horas'][-1] == pytest.approx(23.75)


def test_todos_os_passos_convergem(dia):
    assert dia['passos_nao_convergidos'] == []
    assert all(dia['convergiu'])


def test_as_series_sao_numeros_finitos(dia):
    for chave in ('p_kw', 'q_kvar', 's_kva', 'perdas_kw', 'perdas_pct',
                  'v_min_pu', 'v_med_pu', 'v_max_pu'):
        assert all(math.isfinite(v) for v in dia[chave]), chave


def test_a_curva_de_carga_tem_forma_de_dia(dia):
    """Não é um patamar: há um pico, e ele cai na ponta da noite."""
    assert dia['p_max_kw'] > min(dia['p_kw'])
    assert 16.0 <= dia['hora_p_max'] <= 22.0


def test_as_perdas_sao_uma_fracao_pequena_e_positiva(dia):
    assert all(v >= 0 for v in dia['perdas_kw'])
    assert 0.0 < dia['perdas_pct_dia'] < 25.0


def test_a_potencia_aparente_fecha_com_as_componentes(dia):
    for p, q, s in zip(dia['p_kw'], dia['q_kvar'], dia['s_kva']):
        assert s == pytest.approx(math.hypot(p, q))


def test_a_energia_fecha_com_a_serie(dia):
    passo_h = dia['minutos_por_passo'] / 60.0
    importada = sum(p for p in dia['p_kw'] if p > 0) * passo_h
    assert dia['energia_importada_kwh'] == pytest.approx(importada)
    assert dia['perdas_kwh'] == pytest.approx(sum(dia['perdas_kw']) * passo_h)


def test_as_tensoes_do_dia_envolvem_as_de_cada_passo(dia):
    assert dia['v_min_dia_pu'] == pytest.approx(min(dia['v_min_pu']))
    assert dia['v_max_dia_pu'] == pytest.approx(max(dia['v_max_pu']))


def test_o_dia_e_reprodutivel():
    """Mesmo caso, mesmos números — o OpenDSS não introduz aleatoriedade."""
    a, b = rodar_dia(ESPERADO), rodar_dia(ESPERADO)
    assert a['p_kw'] == b['p_kw']
    assert a['perdas_kw'] == b['perdas_kw']


def test_csv_sai_com_uma_linha_por_passo(dia, tmp_path):
    import csv
    caminho = para_csv(dia, tmp_path / 'sub' / 'dia.csv')
    with open(caminho, encoding='utf-8') as f:
        linhas = list(csv.reader(f))
    assert linhas[0][0] == 'horas'
    assert len(linhas) == PASSOS_DIA + 1
    assert float(linhas[1][0]) == 0.0


def test_medir_cargas_nao_muda_o_que_a_simulacao_conclui():
    """Colher potência por carga é observar, não interferir.

    `Powers()` lê o estado que o `Solve` já produziu, e por isso os agregados
    do dia têm de sair idênticos. Se um dia não saírem, a coleta passou a mexer
    na solução, e é aqui que se descobre.
    """
    sem = rodar_dia(ESPERADO, passos=12, detalhado=True)
    com = rodar_dia(ESPERADO, passos=12, detalhado=True, medir_cargas=True)
    assert sem['p_kw'] == com['p_kw']
    assert sem['p_max_kw'] == com['p_max_kw']
    assert sem['perdas_kwh'] == com['perdas_kwh']
    assert sem['pq_carga'] is None and com['pq_carga'] is not None
    assert com['pq_carga'].shape == (12, len(com['nomes_carga']), 2)


def test_a_potencia_medida_bate_com_o_total_do_alimentador():
    """A soma das cargas medidas tem de caber no que entra pelo alimentador.

    Não é igualdade: a diferença são as perdas e a geração distribuída. Mas a
    soma não pode ser maior que a entrada mais a geração, nem negativa — e um
    erro de índice ou de terminal apareceria como uma dessas duas coisas.
    """
    r = rodar_dia(ESPERADO, passos=8, detalhado=True, medir_cargas=True)
    for k in range(8):
        somado = float(r['pq_carga'][k, :, 0].sum())
        assert somado > 0
        assert somado <= r['p_kw'][k] + sum(
            g['kva'] for g in r['inventario']['gd']) + 1e-6


# ── Reguladores de tensão ────────────────────────────────────────────────────

MODOS_REGCONTROL = {
    'neutro': 'reversible=yes revNeutral=yes revThreshold=1 revDelay=120',
    'cogeracao': 'Cogen=yes revThreshold=1 revDelay=120 revVreg=120 revBand=3',
    'bidirecional': 'reversible=yes revThreshold=1 revDelay=120 '
                    'revVreg=120 revBand=3',
    'direto': '',
}


def _caso_com_regulador(destino, cauda):
    """Copia o exemplo e enxerta um regulador entre duas barras de média."""
    import re
    for p in ESPERADO.iterdir():
        if p.is_file():
            (destino / p.name).write_bytes(p.read_bytes())
    mt = destino / 'Linhas_MT.dss'
    texto = mt.read_text(encoding='utf-8')
    # o primeiro trecho de média dá o par de barras
    # O `phases` migrou para a linha de continuação, atrás do `LineCode`, de
    # modo que `bus2` agora pode terminar a linha.
    m = re.search(r'New Line\.MT_\S+ bus1=(\S+?)(\.\S+)? bus2=(\S+?)(\.\S+)?(?:\s|$)',
                  texto)
    assert m, 'o exemplo devia ter um trecho de média'
    b1, b2 = m.group(1), m.group(3)
    mt.write_text(
        texto + (
            '\nNew Transformer.REG_T phases=3 windings=2 '
            'buses=[%s.1.2.3 %s.1.2.3] conns=[wye wye] '
            'kVs=[13.8000 13.8000] kVAs=[1000.0 1000.0] XHL=1.0 '
            '%%LoadLoss=0.02 %%noloadloss=0.001 %%imag=0.001 '
            'mintap=0.90000 maxtap=1.10000 numtaps=32\n'
            'New RegControl.RC_T transformer=REG_T winding=2 vreg=120.0 '
            'band=3.0 ptratio=111.14 ctprim=200 delay=30 TapDelay=15 '
            'maxtapchange=1 %s\n' % (b1, b2, cauda)),
        encoding='utf-8')
    return destino


@pytest.mark.parametrize('modo,cauda', sorted(MODOS_REGCONTROL.items()))
def test_o_painel_le_o_modo_de_fluxo_reverso_do_proprio_caso(modo, cauda,
                                                             tmp_path):
    """Quem abre uma pasta pronta não sabe com que opção ela foi gerada.

    A escolha muda o resultado — um regulador que vai a neutro no reverso
    deixa de influir na tensão, outro que continua regulando não —, então o
    painel lê o modo do caso compilado, e não de uma configuração que só quem
    converteu conhecia.
    """
    caso = _caso_com_regulador(tmp_path, cauda)
    r = rodar_dia(caso, passos=2, detalhado=True)
    regs = r['inventario']['reguladores']
    assert len(regs) == 1, [g['nome'] for g in regs]
    assert regs[0]['modo'] == modo
    assert regs[0]['modo_rotulo']
    assert regs[0]['modo_nota']


def test_o_tape_e_colhido_a_cada_passo(tmp_path):
    """O tape é o único estado que muda por decisão do próprio elemento.

    Antes só se lia onde ele parou no fim do dia, e era isso que o painel
    mostrava como se fosse o valor do instante mostrado.
    """
    caso = _caso_com_regulador(tmp_path, MODOS_REGCONTROL['neutro'])
    r = rodar_dia(caso, passos=8, detalhado=True)
    taps = r['tap_regulador']
    assert taps is not None
    assert taps.shape == (8, len(r['nomes_regcontrol']))
    assert 'rc_t' in [n.lower() for n in r['nomes_regcontrol']]
    # e o passo de tape sai do próprio transformador, não de um palpite
    g = r['inventario']['reguladores'][0]
    assert g['tap_passo'] == pytest.approx((1.10 - 0.90) / 32)


def test_sem_regulador_nao_ha_serie_de_tape():
    r = rodar_dia(ESPERADO, passos=2, detalhado=True)
    assert r['inventario']['reguladores'] == []
    assert r['tap_regulador'] is None
    assert r['nomes_regcontrol'] == []


def test_master_vazio_diz_o_que_houve(tmp_path):
    """Master de zero byte é caso diferente de Master ausente.

    O OpenDSS compila um Master vazio sem reclamar — não há sintaxe errada em
    nada — e simplesmente termina sem circuito. Quem abre a pasta vê os outros
    dez `.dss` no lugar e não desconfia do que está com zero byte.

    Acontece de verdade: o programa de mesa do OpenDSS, aberto sobre a pasta,
    pode truncar o Master ao salvar.
    """
    for p in ESPERADO.iterdir():
        if p.is_file():
            (tmp_path / p.name).write_bytes(p.read_bytes())
    (tmp_path / 'Master.dss').write_bytes(b'')

    with pytest.raises(RuntimeError, match='vazio'):
        rodar_dia(tmp_path, passos=2)


def test_sem_circuito_a_queixa_nomeia_a_pasta(tmp_path):
    """A sondagem tem de sobreviver ao caso que ela existe para detectar.

    Sem circuito, `Circuit.Name()` não devolve vazio: levanta a exceção do
    OpenDSS. A guarda morria antes de dar o recado, e quem chamava recebia
    "(#8888) There is no active circuit" — verdadeiro e inútil, sem uma palavra
    sobre qual pasta estava em questão.
    """
    from bdgdcase.solucao import _compilar

    # Um Master que compila sem erro nenhum e não cria circuito: só
    # comentário. É exatamente o que o arquivo truncado produzia.
    (tmp_path / 'Master.dss').write_text('! nenhum New Circuit aqui\n',
                                         encoding='utf-8')
    with pytest.raises(RuntimeError, match='circuito nenhum'):
        _compilar(tmp_path)


def test_master_malformado_vira_erro_do_pacote(tmp_path):
    """O erro do OpenDSS nomeia arquivo e linha, e isso é bom; o tipo não.

    Quem chama `rodar_dia` trata `RuntimeError`, e uma exceção do
    fornecedor escapando derruba a linha de comando com traceback em vez de
    uma linha dizendo qual alimentador falhou.
    """
    from bdgdcase.solucao import _compilar

    (tmp_path / 'Master.dss').write_text('Set tolerance=1e-6\n',
                                         encoding='utf-8')
    with pytest.raises(RuntimeError, match='recusou'):
        _compilar(tmp_path)


def test_a_solucao_que_explodiu_nao_conta_como_convergida():
    """`Converged()` não basta: o solver mente depois de divergir uma vez.

    Num caso medido, o passo das 19:00 — a hora em que a curva da iluminação
    pública acende — divergiu de fato e reportou `Converged=False`; os **vinte
    passos seguintes** reportaram `Converged=True` com as tensões em `inf`,
    porque o solver reparte do estado anterior e nunca mais sai dele.

    Sem esta guarda, esses passos entravam no resultado como bons, e o único
    sinal que chegava ao usuário era um `RuntimeWarning: overflow encountered in
    cast` do numpy — uma mensagem sobre o tamanho de um inteiro, não sobre a
    rede.
    """
    import numpy as np

    from bdgdcase.solucao import PU_ABSURDO, _solucao_finita

    class _Falso:
        def __init__(self, v):
            self._v = v
            self.Circuit = self

        def AllBusMagPu(self):
            return self._v

    assert _solucao_finita(_Falso([1.0, 0.98, 1.02]))
    assert _solucao_finita(_Falso([]))            # circuito vazio não é explosão
    assert not _solucao_finita(_Falso([1.0, float('inf')]))
    assert not _solucao_finita(_Falso([1.0, 1e131]))
    assert not _solucao_finita(_Falso([1.0, PU_ABSURDO + 1]))
    # o limiar é folgado: uma rede ruim de verdade ainda passa
    assert _solucao_finita(_Falso([1.0, 1.9]))


def test_o_dia_do_caso_de_referencia_nao_tem_passo_ruim(dia):
    """Nenhum dos 96 passos pode divergir no alimentador publicado."""
    assert dia['passos_nao_convergidos'] == []


# ── O dia que saía em zeros ──────────────────────────────────────────────────

def test_o_dia_comeca_de_um_instantaneo_e_nao_do_frio():
    """A partida a frio do modo diário levava a solução a um dia de zeros.

    O primeiro passo do modo diário parte de tensão plana. Num alimentador
    difícil isso bastava para o solver cair numa solução degenerada e
    **declará-la convergida**: medido num alimentador de 7.740 cargas, os 96
    passos fechavam com 0,0 kW na cabeceira, 0,9 A no tronco e todas as barras
    em 1,020 pu. Nenhum passo aparecia como não convergido, e o CSV saía cheio
    de zeros com cara de dado.

    Um `Snap` antes de entrar no dia resolve: o mesmo caso passa a fechar em
    3.020 kW, com o balanço batendo. É uma solução a mais em noventa e sete.
    """
    import inspect

    from bdgdcase.solucao import rodar_dia

    fonte = inspect.getsource(rodar_dia)
    assert 'Set Mode=Snap' in fonte, 'sumiu a semeadura da solução'
    assert fonte.index('Set Mode=Snap') < fonte.index('Set Mode=Daily'), (
        'o instantâneo tem de vir ANTES do modo diário, ou não semeia nada')


def test_dia_inteiro_em_zero_com_carga_no_circuito_e_recusado():
    """Rede de segurança: se o dia de zeros voltar, não volta calado.

    A guarda por passo — finito e abaixo de 10 pu — não pega este caso: as
    tensões ficam num plano de 1,02 pu e cada passo se declara convergido. O
    que denuncia é o agregado, e é ele que esta conferência olha.
    """
    from bdgdcase.solucao import _conferir_dia_com_carga

    class _Cargas:
        def __init__(self, n): self._n = n
        def Count(self): return self._n

    class _Dss:
        def __init__(self, n): self.Loads = _Cargas(n)

    morto = {'p_kw': [0.0] * 96}
    with pytest.raises(RuntimeError, match='zero'):
        _conferir_dia_com_carga(morto, 'pasta/qualquer', _Dss(7740))

    # circuito sem carga nenhuma não é o caso desta guarda
    _conferir_dia_com_carga(morto, 'pasta/qualquer', _Dss(0))
    # e um dia de verdade passa
    vivo = {'p_kw': [0.0] * 95 + [3020.0]}
    _conferir_dia_com_carga(vivo, 'pasta/qualquer', _Dss(7740))


def test_o_dia_do_caso_de_referencia_tem_potencia(dia):
    """O agregado mais básico: um alimentador com carga move energia."""
    assert dia['p_max_kw'] > 1.0
    assert dia['energia_importada_kwh'] > 0.0


# ═════════════════════════════════════════════════════════════════════════════
# O recuo sem reguladores
# ═════════════════════════════════════════════════════════════════════════════

def test_caso_sem_regulador_nao_passa_pelo_recuo(dia):
    """O exemplo empacotado não tem regulador: o recuo nem tem de que falar."""
    assert 'reguladores_desligados' not in dia


def test_o_recuo_so_fica_com_o_dia_sem_reguladores_se_ele_fechar_melhor():
    """Regulador que não atrapalha continua ligado.

    O recuo age quando a guarda recusa mais de `FRACAO_RECUO_REGULADOR` dos
    passos, e só troca o dia se o dia sem reguladores recusar MENOS. Medido:
    38 → 0, 8 → 0 e 46 → 0 nos três que falhavam; num quarto, com nove
    reguladores no batente, 0 → 0, e o recuo nem dispara.
    """
    import inspect

    from bdgdcase import solucao

    fonte = inspect.getsource(solucao.rodar_dia)
    assert 'recusados > FRACAO_RECUO_REGULADOR' in fonte
    assert 'recusados_sem < recusados' in fonte
    assert 0.0 < solucao.FRACAO_RECUO_REGULADOR < 0.5


def test_o_regulador_e_desligado_antes_da_semente():
    """Desligado ANTES do instantâneo, o tape fica no neutro.

    Se herdasse a posição de plena carga da semente, o regulador desligado
    seria um transformador com relação fixa errada o dia inteiro.
    """
    import inspect

    from bdgdcase import solucao

    fonte = inspect.getsource(solucao.rodar_dia)
    i_off = fonte.index("batchedit regcontrol..* enabled=no")
    i_snap = fonte.index("Set Mode=Snap")
    assert i_off < i_snap


def test_o_cli_avisa_quando_desligou_os_reguladores(capsys, monkeypatch):
    """O usuário tem de ver que a simulação desligou algo — e o que custou."""
    from bdgdcase.interface import cli
    import bdgdcase.solucao as S

    falso = {
        'alimentador': 'X', 'passos': 96, 'minutos_por_passo': 15,
        'p_max_kw': 4568.2, 'hora_p_max': 18.5,
        'energia_importada_kwh': 72198.1, 'energia_exportada_kwh': 0.0,
        'perdas_kwh': 7279.5, 'perdas_pct_dia': 10.08,
        'v_min_dia_pu': 0.7917, 'v_max_dia_pu': 1.0469,
        'passos_nao_convergidos': [],
        'reguladores_desligados': {
            'quantos': 6, 'passos_recusados_com': 38, 'passos_recusados_sem': 0,
            'v_min_com_pu': 0.837, 'v_min_sem_pu': 0.792,
            'p_max_com_kw': 5010.0, 'p_max_sem_kw': 4568.0},
    }
    monkeypatch.setattr(S, 'rodar_dia', lambda *a, **k: falso)

    class Args(object):
        pastas = ['qualquer']; medir_cargas = False; csv = None

    cli._cmd_rodar(Args())
    saida = capsys.readouterr().out
    assert 'DESLIGADOS' in saida
    assert '38 dos 96' in saida
    assert '0.837' in saida and '0.792' in saida


def test_o_regulador_desligado_continua_no_inventario():
    """`RegControls.First/Next` só percorre os habilitados; o mapa precisa
    dos desligados também, com o tape em zero e a marca. Medido: com
    `enabled=no`, `Count()` diz seis e o laço devolve zero."""
    import inspect

    from bdgdcase import solucao

    fonte = inspect.getsource(solucao._inventario)
    assert '_nomes_regcontrol(dss)' in fonte
    assert "'ativo'" in fonte
    fonte_col = inspect.getsource(solucao._coletor_detalhado)
    assert '_nomes_regcontrol(dss)' in fonte_col


def test_a_anotacao_de_execucao_entra_e_sai_do_registro(tmp_path):
    """O recuo grava no `Ajustes.json`; rodar de novo sem recuo apaga."""
    import json

    from bdgdcase import ajustes

    arq = tmp_path / ajustes.NOME_ARQUIVO
    arq.write_text(json.dumps({'alimentador': 'X', 'ajustes': [
        {'categoria': 'tensao', 'titulo': 'outro', 'quantos': 1, 'efeito': 'simulacao'}]}),
        encoding='utf-8')
    entrada = {'categoria': 'tensao', 'titulo': 'T', 'quantos': 6,
               'unidade': 'reguladores', 'efeito': 'simulacao',
               'detalhe': 'd', 'porque': 'p', 'exemplos': []}
    ajustes.anotar_execucao(tmp_path, 'T', entrada)
    d = ajustes.carregar(tmp_path)
    assert [a['titulo'] for a in d['ajustes']].count('T') == 1
    assert any(a['titulo'] == 'outro' for a in d['ajustes']), 'o resto fica'
    # de novo: nao duplica
    ajustes.anotar_execucao(tmp_path, 'T', entrada)
    assert [a['titulo'] for a in ajustes.carregar(tmp_path)['ajustes']].count('T') == 1
    # None remove
    ajustes.anotar_execucao(tmp_path, 'T', None)
    assert not any(a['titulo'] == 'T' for a in ajustes.carregar(tmp_path)['ajustes'])


def test_a_anotacao_nao_toca_o_arquivo_quando_nada_muda(tmp_path):
    """Sem nada a tirar nem a por, o arquivo fica byte a byte igual."""
    import json

    from bdgdcase import ajustes

    arq = tmp_path / ajustes.NOME_ARQUIVO
    arq.write_text(json.dumps({'alimentador': 'X', 'ajustes': []}), encoding='utf-8')
    antes = arq.read_bytes()
    ajustes.anotar_execucao(tmp_path, 'T', None)
    assert arq.read_bytes() == antes


def test_sem_registro_a_anotacao_nao_inventa_um(tmp_path):
    from bdgdcase import ajustes

    assert ajustes.anotar_execucao(tmp_path, 'T', {'titulo': 'T'}) is None
    assert not (tmp_path / ajustes.NOME_ARQUIVO).exists()
