# -*- coding: utf-8 -*-
"""Transformador com a baixa tensão solta, e como ele se religa ao seu circuito.

Relato real, no alimentador 881160007: `tr_548578792`, `tr_548577746` e
`tr_547355113` apareciam no mapa com o secundário sem nada ligado. Não era
transformador vazio — o cadastro dizia que eles alimentavam 59, 83 e 62 unidades
consumidoras, e essas cargas estavam no caso, num circuito de baixa que existia.
O que faltava era o elo entre o secundário e esse circuito.

## Por que faltava

A costura por coordenada só recebia candidatos das tabelas de **condutor**
(`SSDMT`, `SSDBT`). O `PAC_2` do transformador é um nó de baixa como outro
qualquer, mas não é ponta de condutor nenhum — então nunca entrava no mapa, e
nunca podia ser pareado.

## Por que a coincidência exata não bastava

Medido: o transformador fica de **1,6 a 2,0 m** da ponta de condutor mais
próxima. O arredondamento a cinco casas decimais (~1 m) os punha em baldes
diferentes. Não é distância real — é precisão de desenho no cadastro, e os dois
estão no mesmo poste.

Medido nas bases: 65 transformadores soltos têm circuito próprio identificável
pelo campo `UNI_TR_MT`, e a distância até a barra mais próxima dele é **zero na
mediana**, com 64 dos 65 abaixo de 50 m.
"""
import inspect



def test_o_secundario_do_trafo_entra_na_costura():
    """A tabela de transformadores passa a alimentar o mapa de coordenadas.

    Sem esta chamada o `PAC_2` do trafo nunca é candidato, e é esse o buraco
    que deixava a baixa inteira solta.
    """
    from bdgdcase.modelo.rede import gerar_jumpers_topologicos

    fonte = inspect.getsource(gerar_jumpers_topologicos)
    assert 'religar_secundarios(untrmt' in fonte
    # e as duas de sempre continuam lá — já se perderam num recorte
    assert 'registrar_pacs(ssdmt, mapa_coords_mt' in fonte
    assert 'registrar_pacs(ssdbt, mapa_coords_bt' in fonte


def test_a_juncao_do_trafo_e_por_proximidade_e_nao_exata():
    """Coincidência exata não pega nada: o cadastro separa os dois por metros.

    Medido: o transformador fica de 1,6 a 2,0 m da ponta de condutor mais
    próxima. Abaixo de 2 m a tolerância não pega o caso relatado; muito acima
    de 15 m ela começa a alcançar o poste seguinte, que é outra coisa.
    """
    from bdgdcase.modelo import config

    valor = config.TOLERANCIA_RELIGACAO_TRAFO_M
    assert 2.0 < valor <= 15.0, '%s m' % valor


def test_o_primario_e_o_secundario_continuam_proibidos():
    """Unir os dois lados do transformador seria pô-lo em curto.

    A junção nova é do secundário com o nó de baixa do mesmo poste — nunca com
    o próprio primário, e é `registrar_proibidos(untrmt)` que garante isso.
    """
    from bdgdcase.modelo.rede import gerar_jumpers_topologicos

    fonte = inspect.getsource(gerar_jumpers_topologicos)
    assert 'registrar_proibidos(untrmt)' in fonte


def test_a_religacao_acontece_sozinha_sem_interruptor():
    """Não é escolha de modelagem: é defeito de cadastro, e se conserta sempre.

    Um transformador que não alcança as unidades que a BDGD diz que ele
    alimenta deixa essa carga sem tensão o dia inteiro. Não há premissa a
    escolher aí — há um elo faltando, e o dado para fechá-lo está na base.
    """
    from bdgdcase.modelo import config

    assert not hasattr(config, 'RELIGAR_SECUNDARIO_DO_TRAFO')


def test_os_dois_emissores_devolvem_os_nos_que_usaram():
    """A resposta vem de quem escreveu, e não de uma segunda leitura da regra.

    São cinco pontos de emissão de secundário — trifásico, center-tap, MRT com
    uma perna, MRT com duas — e reconstruir a regra fora dali é exatamente como
    emissor e réplica divergem. Foi assim que 103 mil MRT de uma base saíram
    declarados a 254 V.
    """
    import inspect

    from bdgdcase.modelo.rede import gerar_linhas_bt, gerar_transformadores

    assert 'nos_secundario' in inspect.signature(
        gerar_transformadores).parameters
    assert 'nos_por_barra' in inspect.signature(gerar_linhas_bt).parameters
    # e a resposta sai da própria saída, não de um recálculo
    assert 'wdg=[23]' in inspect.getsource(gerar_transformadores)


def test_o_jumper_casa_os_lados_posicionalmente():
    """`.1.3` do secundário no `.1.2` do circuito: primeira perna, primeiro fio.

    Casar por identidade deixaria o nó 2 do circuito sem alimentação, flutuando
    — e foi isso que fez o passo do meio-dia ir a 9,2e95 pu.
    """
    import inspect

    from bdgdcase.modelo.rede import gerar_jumpers_topologicos

    fonte = inspect.getsource(gerar_jumpers_topologicos)
    assert 'nos_alvo' in fonte
    assert 'min(len(nos), len(nos_alvo))' in fonte


def test_sem_saber_os_nos_nao_se_emite_jumper():
    """Melhor a falta declarada que o remendo que derruba o dia."""
    import inspect

    from bdgdcase.modelo.rede import gerar_jumpers_topologicos

    fonte = inspect.getsource(gerar_jumpers_topologicos)
    assert 'sem_nos.append(' in fonte
    assert 'não pôde ser religado' in fonte


# ── O jumper que religa, e a camada em que ele entra ─────────────────────────
#
# Segundo relato, sobre os mesmos transformadores: clicar neles não realçava o
# circuito de baixa, e o painel não listava as unidades. A pergunta era se o
# transformador estava mesmo desligado das cargas.
#
# Não estava: medido no caso resolvido, os três carregavam 13,2, 6,4 e 6,6 kW às
# 18h. O problema era do mapa — o jumper se chama `JUMP_TR_…`, e a classificação
# olhava só o primeiro pedaço do nome. `jump` não estava na tabela, então caía no
# padrão `'mt'`: a travessia da baixa não atravessa um trecho de média, e o
# realce parava no transformador.

def test_o_jumper_do_trafo_conta_como_baixa():
    """`JUMP_TR_` une o secundário ao condutor de baixa do mesmo poste.

    O que passa por ele é a corrente daquele circuito, então ele é baixa. Se
    voltar a ser classificado como média, o realce para no transformador e o
    painel diz "0 unidades" para um transformador que alimenta oitenta.
    """
    from bdgdcase.interface.geometria import tipo_do_trecho

    assert tipo_do_trecho('JUMP_TR_BT_38802597_BT_547654685') == 'bt'


def test_o_jumper_segue_o_nivel_que_ele_une():
    """Cada jumper entra na camada do que ele costura, e não numa só."""
    from bdgdcase.interface.geometria import tipo_do_trecho

    assert tipo_do_trecho('JUMP_MT_a_b') == 'mt'
    assert tipo_do_trecho('JUMP_BT_a_b') == 'bt'


def test_os_prefixos_de_sempre_continuam_valendo():
    """A exceção do jumper não pode ter mexido no resto."""
    from bdgdcase.interface.geometria import tipo_do_trecho

    assert tipo_do_trecho('MT_5355187') == 'mt'
    assert tipo_do_trecho('BT_8008643') == 'bt'
    assert tipo_do_trecho('RL_10058970') == 'bt'      # ramal de ligação é baixa
    assert tipo_do_trecho('SW_6175301') == 'chaves'


def test_nome_desconhecido_cai_na_media():
    """O padrão continua sendo média — é onde um nome novo faz menos estrago.

    Um trecho de média classificado como baixa entraria no realce de todo
    transformador vizinho; ao contrário, ele só deixa de entrar num realce.
    """
    from bdgdcase.interface.geometria import tipo_do_trecho

    assert tipo_do_trecho('XYZ_1') == 'mt'
    assert tipo_do_trecho('') == 'mt'


def test_a_classificacao_dos_trechos_usa_a_funcao():
    """Duas cópias da regra viram duas regras na primeira vez que se mexe numa."""
    import inspect

    from bdgdcase.interface.geometria import _Geometria

    fonte = inspect.getsource(_Geometria._classificar_trechos)
    assert 'tipo_do_trecho(nome)' in fonte


def test_a_religacao_conta_para_o_master_redirecionar():
    """Escrever o conserto em disco nao basta: o Master precisa carrega-lo.

    `gerar_jumpers_topologicos` devolve quantos jumpers escreveu, e o
    `pipeline` usa esse numero para decidir se o Master redireciona o
    `Jumpers.dss` — um `Redirect` para arquivo vazio muda o caso byte a byte
    sem mudar nada eletrico. A religacao ficou de fora dessa conta: os 15
    jumpers de um alimentador saiam escritos, `contagem` continuava zero, e o
    Master comentava o `Redirect`. O conserto existia no disco e nunca entrava
    no circuito — emissor e Master discordando, que e o defeito de sempre
    aqui, e sem ninguem reclamar.
    """
    import inspect

    from bdgdcase.modelo.rede import gerar_jumpers_topologicos

    fonte = inspect.getsource(gerar_jumpers_topologicos)
    assert 'return contagem + religados_n' in fonte


def test_nao_se_religa_criando_no_pendurado():
    """Energizar um nó e deixar o vizinho solto é pior que deixar tudo apagado.

    Um secundário de uma perna sobre um ramal que o cadastro declara com três
    condutores alimenta um nó e deixa os outros sem caminho para a terra. Não
    dá erro — dá número: medido, `bt_bt265891` foi a 3,42 pu e quatro passos
    do 881160007 deixaram de convergir, com o dia inteiro saindo errado sem
    ninguém reclamar. O guarda anda o circuito de baixa a partir do alvo, pelas
    ligações que o próprio emissor registrou, e desiste se sobrar nó.
    """
    import inspect

    from bdgdcase.modelo.rede import gerar_jumpers_topologicos, gerar_linhas_bt

    assert 'ligacoes_por_barra' in inspect.signature(
        gerar_linhas_bt).parameters
    fonte = inspect.getsource(gerar_jumpers_topologicos)
    assert '_nos_sem_alimentacao(alvo.lower(), alimentados)' in fonte
    assert 'pendurados.append(' in fonte


def test_nao_se_religa_em_secundario_de_outro_trafo():
    """O poste mais próximo pode ser o do vizinho, e aí não é religar.

    Ligar o secundário solto ao secundário de outro transformador põe os dois
    em paralelo, e eles nem precisam ter a mesma tensão: medido, um center-tap
    de 127 V por perna recebendo o secundário de 220 V do trifásico ao lado
    levou a rede a 1,704 pu — sem erro, sem aviso, só número errado.
    """
    import inspect

    from bdgdcase.modelo.rede import gerar_jumpers_topologicos

    fonte = inspect.getsource(gerar_jumpers_topologicos)
    assert 'if alvo.lower() in (nos_secundario or {}):' in fonte
    assert 'paralelos.append(' in fonte


def test_o_poste_comum_pode_pertencer_ao_circuito_de_outro_trafo():
    """O guarda de cima olha o poste; o defeito estava um salto adiante.

    Relato real: o poste mais próximo do secundário solto era um poste comum —
    e passava. Só que ele pertencia ao circuito de um center-tap de 220 V por
    perna, e o secundário solto era de um trifásico de 380 V.
    """
    from bdgdcase.modelo.rede import secundarios_na_ilha

    ligacoes = {'poste_a': {'poste_b'}, 'poste_b': {'poste_a', 'bt_do_outro'},
                'bt_do_outro': {'poste_b'}}
    assert secundarios_na_ilha('poste_a', {'bt_do_outro': [1, 2]},
                               ligacoes) == ['bt_do_outro']


def test_a_ilha_inclui_a_propria_barra_quando_ela_e_secundario():
    """O caso que o guarda antigo já pegava continua sendo visto."""
    from bdgdcase.modelo.rede import secundarios_na_ilha

    assert secundarios_na_ilha('bt_x', {'bt_x': [1, 2]}, {}) == ['bt_x']


def test_a_ilha_nao_atravessa_para_outro_circuito():
    """Dois circuitos separados não se enxergam: é o que faz o guarda ser útil.

    Sem isto, um transformador do outro lado do alimentador vetaria o jumper de
    um circuito com que ele não tem ligação nenhuma.
    """
    from bdgdcase.modelo.rede import secundarios_na_ilha

    ligacoes = {'poste_a': {'poste_b'}, 'poste_b': {'poste_a'},
                'bt_longe': {'poste_z'}, 'poste_z': {'bt_longe'}}
    assert secundarios_na_ilha('poste_a', {'bt_longe': [1, 2]}, ligacoes) == []


def test_a_ilha_termina_em_circuito_com_ciclo():
    """Rede de baixa em anel existe, e um `while` ingênuo não voltaria dela."""
    from bdgdcase.modelo.rede import secundarios_na_ilha

    anel = {'a': {'b', 'c'}, 'b': {'a', 'c'}, 'c': {'a', 'b'}}
    assert secundarios_na_ilha('a', {'fora': [1]}, anel) == []


def test_so_se_recusa_o_jumper_quando_as_tensoes_diferem():
    """Recusar todo paralelo seria trocar um problema por outro maior.

    Medido: 47 dos 131 jumpers já emitidos ligam circuitos cujo transformador
    tem a MESMA tensão de secundário. Aí não há corrente de circulação que
    importe, e recusar deixaria o transformador solto sem circuito — que é o
    defeito que o jumper existe para curar.

    O que queima é a diferença: 380 V contra 220 V por perna empurrou 589 A por
    um cabo de 125 A.
    """
    from bdgdcase.modelo.rede import secundario_incompativel

    ligacoes = {'poste_a': {'bt_vizinho'}, 'bt_vizinho': {'poste_a'}}
    nos = {'bt_solto': [1, 2], 'bt_vizinho': [1, 2]}

    iguais = {'bt_solto': 0.38, 'bt_vizinho': 0.38}
    assert secundario_incompativel('bt_solto', 'poste_a', nos, ligacoes,
                                   iguais) is None

    diferentes = {'bt_solto': 0.38, 'bt_vizinho': 0.22}
    assert secundario_incompativel('bt_solto', 'poste_a', nos, ligacoes,
                                   diferentes) == 'bt_vizinho'


def test_sem_saber_a_tensao_nao_se_recusa():
    """Guarda que age no escuro erra mais do que acerta.

    O paralelo de mesma tensão não faz mal que justifique o palpite, então a
    ausência do dado deixa o jumper passar em vez de barrá-lo por precaução.
    """
    from bdgdcase.modelo.rede import secundario_incompativel

    ligacoes = {'poste_a': {'bt_vizinho'}, 'bt_vizinho': {'poste_a'}}
    nos = {'bt_solto': [1, 2], 'bt_vizinho': [1, 2]}

    assert secundario_incompativel('bt_solto', 'poste_a', nos, ligacoes,
                                   {'bt_vizinho': 0.22}) is None
    assert secundario_incompativel('bt_solto', 'poste_a', nos, ligacoes,
                                   {'bt_solto': 0.38}) is None


def test_diferenca_de_arredondamento_nao_conta_como_outra_tensao():
    """Dois secundários de 380 V não saem idênticos do cadastro."""
    from bdgdcase.modelo.rede import secundario_incompativel

    ligacoes = {'poste_a': {'bt_vizinho'}, 'bt_vizinho': {'poste_a'}}
    nos = {'bt_solto': [1, 2], 'bt_vizinho': [1, 2]}
    quase = {'bt_solto': 0.38, 'bt_vizinho': 0.3805}
    assert secundario_incompativel('bt_solto', 'poste_a', nos, ligacoes,
                                   quase) is None


def test_o_emissor_consulta_a_ilha_e_o_kv_do_secundario():
    """A ligação entre o guarda e quem o usa — some num recorte sem avisar."""
    import inspect

    from bdgdcase.modelo.rede import (gerar_jumpers_topologicos,
                                      gerar_transformadores)

    fonte = inspect.getsource(gerar_jumpers_topologicos)
    assert 'secundario_incompativel(' in fonte
    assert 'incompativeis.append(' in fonte
    # e o kv tem de ser LIDO DA SAIDA do emissor, como os nós já são
    assert 'kv_secundario[_barra]' in inspect.getsource(gerar_transformadores)


def test_o_que_nao_foi_religado_aparece_no_registro():
    """Transparência: o que se deixou de consertar é dito, e por quê.

    São três motivos distintos, e cada um tem entrada própria no registro de
    ajustes — que é o que a janela mostra. Um único "não deu" genérico
    esconderia justamente a informação que faz o leitor decidir se confia no
    caso.
    """
    import inspect

    from bdgdcase.modelo.rede import gerar_jumpers_topologicos

    fonte = inspect.getsource(gerar_jumpers_topologicos)
    assert fonte.count("'nao_corrigido',") >= 3
    assert 'não pôde ser religado' in fonte
    assert 'mais fases que o transformador' in fonte
    assert 'é de outro transformador' in fonte


# ── O ramal casa posicionalmente, como o jumper ──────────────────────────────

def _emitir_ramal(tmp_path, fas_con, nos_secundario):
    """Emite um ramal só, pendurado no secundário de um center-tap."""
    import pandas as pd

    from bdgdcase.modelo.rede import gerar_linhas_bt

    ramlig = pd.DataFrame([{
        'COD_ID': 'R1', 'PAC_1': 'BT_POSTE', 'PAC_2': 'BT_CLIENTE',
        'FAS_CON': fas_con, 'COMP': 12.0, 'TIP_INST': 'AER', 'TIP_CND': '',
    }])
    saida = tmp_path / 'Linhas_BT.dss'
    gerar_linhas_bt(None, ramlig, str(saida),
                    nos_secundario=nos_secundario)
    for linha in saida.read_text(encoding='utf-8').splitlines():
        if linha.startswith('New Line.RL_R1'):
            return linha
    raise AssertionError('o ramal nao foi emitido')


def test_o_ramal_casa_com_os_nos_que_a_barra_tem(tmp_path):
    """O `FAS_CON` nomeia nós que o circuito acima nem sempre tem.

    Medido: um center-tap com o secundário em `.3.0` e `.0.1` — nós 1 e 3 —
    recebendo um ramal declarado `BCN`, que pede os nós 2 e 3. O nó 2 não
    existe ali: fica pendurado, sem caminho para a terra, a 27,9 V. A tensão
    dos nós reais continua certa, mas o `CalcVoltageBases` casa a base da barra
    pelo conjunto dos nós, e o nó morto a puxa para baixo — uma barra de 130 V
    ficou com base de 73,3 V e apareceu a 1,773 pu.

    A ligação passa a ser posicional, como já era nos jumpers.
    """
    linha = _emitir_ramal(tmp_path, 'BCN', {'bt_poste': [1, 3]})
    assert 'bus1=BT_POSTE.1.3' in linha, linha
    assert 'bus2=BT_CLIENTE.1.3' in linha, linha


def test_o_ramal_que_ja_casa_nao_e_tocado(tmp_path):
    """Remapear o que já está certo seria mexer sem motivo."""
    linha = _emitir_ramal(tmp_path, 'ACN', {'bt_poste': [1, 3]})
    assert 'bus1=BT_POSTE.1.3' in linha, linha


def test_o_ramal_nao_e_remapeado_quando_pede_mais_condutores(tmp_path):
    """Casar exigiria escolher qual condutor descartar, e o cadastro não diz.

    Fica como foi publicado, e a contagem vai para o registro de ajustes —
    inventar aqui seria pior que registrar a dívida.
    """
    linha = _emitir_ramal(tmp_path, 'ABCN', {'bt_poste': [1]})
    assert 'bus1=BT_POSTE.1.2.3' in linha, linha


def test_sem_saber_os_nos_da_barra_o_ramal_sai_como_o_cadastro_pede(tmp_path):
    """Sem o mapa, não há com o que casar — e não se adivinha."""
    linha = _emitir_ramal(tmp_path, 'BCN', {})
    assert 'bus1=BT_POSTE.2.3' in linha, linha


def test_as_fases_e_o_condutor_nao_mudam_no_remapeamento(tmp_path):
    """O remapeamento renomeia nó; ele não pode mexer no conteúdo elétrico.

    É essa propriedade que torna a correção segura de aplicar em todas as
    bases — e ela foi medida: o remapeamento disparou em 109 ramais de um
    alimentador da Celesc e o `verificar` saiu idêntico.
    """
    import re

    casado = _emitir_ramal(tmp_path, 'BCN', {'bt_poste': [1, 3]})
    solto = _emitir_ramal(tmp_path, 'BCN', {})
    # a linha seguinte (LineCode/phases) tem de ser a mesma nos dois
    assert re.search(r'phases=', casado) is None, (
        'as fases moram na continuacao, nao nesta linha')
    assert casado.replace('.1.3', '.2.3') == solto

