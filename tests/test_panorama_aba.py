# -*- coding: utf-8 -*-
"""A aba que deixa escolher o alimentador clicando no mapa.

`test_panorama.py` cobre a leitura da base — o que se lê, o que se costura, o
que se guarda. Aqui é a outra ponta: o que acontece quando alguém clica, e o
que se vê.

O que estes testes fixam é a ligação, que é onde a coisa quebra em silêncio: o
clique tem de virar seleção, a seleção tem de chegar ao `Listbox` da aba de
preparar — senão o `Executar` extrai outro alimentador, ou nenhum — e os dois
mapas da janela têm de ter cada um o seu fundo, que foi a razão de a navegação
ter sido extraída para `interface.navegacao`.

E fixam a legibilidade sobre satélite, que foi um defeito relatado: ao trocar o
fundo claro pelo híbrido, os alimentadores pareciam sumir *por baixo* do mapa.
Não era ordem de desenho — o traço estava por cima e invisível assim mesmo,
fino demais e em cor média sobre uma imagem escura. Os dois testes do fim
guardam as duas metades disso: o traço fica por cima, e ganha contorno quando o
fundo é escuro.

Roda sem servidor gráfico? Não. Num runner Linux sem X estes testes pulam.
"""
import os

import pytest

tk = pytest.importorskip('tkinter', reason='este Python não tem tkinter')
np = pytest.importorskip('numpy')
pytest.importorskip('matplotlib')
pytest.importorskip('pyogrio')

from bdgdcase.extracao.panorama import Panorama, construir

RAIZ = os.path.dirname(os.path.abspath(__file__))
SSDMT = os.path.join(RAIZ, 'dados', 'URB13', 'SSDMT_URB13.gpkg')


@pytest.fixture(scope='module')
def raiz():
    """Uma raiz Tk só para o módulo — ver a explicação em `test_gui.py`."""
    try:
        r = tk.Tk()
    except tk.TclError as exc:            # sem display (CI Linux headless)
        pytest.skip('sem servidor gráfico: %s' % exc)
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def janela(raiz):
    from bdgdcase.interface.janela import _Janela
    top = tk.Toplevel(raiz)
    top.withdraw()
    j = _Janela(top, aba='preparar')
    yield j
    top.destroy()


def _duas_ruas():
    """Dois alimentadores lado a lado, sem depender de arquivo nenhum.

    Um a oeste, outro a leste, cada um um traço contínuo de três vértices. É o
    mínimo para perguntar se o clique acerta o certo e se o Ctrl acumula.
    """
    coords = np.array([
        [-51.00, -27.00], [-51.00, -27.01], [-51.00, -27.02],
        [-50.50, -27.00], [-50.50, -27.01], [-50.50, -27.02],
    ])
    info = {'ALIM_O': {'nome': 'Oeste', 'kv': 13.8, 'km': 12.5,
                       'mwh_ano': 4200.0, 'n_trechos': 2},
            'ALIM_L': {'nome': 'Leste', 'kv': 23.1, 'km': 3.0,
                       'mwh_ano': 900.0, 'n_trechos': 2}}
    return Panorama(['ALIM_O', 'ALIM_L'], coords, np.array([0, 3, 6]),
                    np.array([0, 1], dtype=np.int32), info)


def _montar(j, pan):
    """Põe o panorama na aba como a varredura faria, e enquadra na mão.

    Na janela de verdade o enquadramento vem de um `after` de 60 ms; numa
    janela oculta esse laço não roda, e sem enquadrar o eixo fica no 0..1 do
    matplotlib — onde nenhum clique acerta coisa alguma.
    """
    j.alimentadores = list(pan.alimentadores)
    j._ui_repovoar()
    j._ui_pan_desenhar(pan)
    j.vista_pan.reenquadrar()
    return j


def _clicar(j, codigo, ctrl=False):
    """Clica no meio do traço de um alimentador."""
    p = j.pan.partes_de(codigo)[0]
    x, y = j.pan_linhas[p].mean(axis=0)
    j._pan_clicou(float(x), float(y), ctrl)


def test_o_mapa_desenha_tracos_continuos(janela):
    """Um caminho por ramo — e não um por vão, que era o mapa tracejado."""
    j = _montar(janela, _duas_ruas())
    caminhos = j._pan_col.get_segments()
    assert len(caminhos) == 2, 'dois alimentadores, dois traços'
    assert len(caminhos[0]) == 3, 'o traço mantém os vértices do ramo'
    assert len(j._pan_cores) == 2
    assert j._pan_cores[0] != j._pan_cores[1], (
        'vizinhos na mesma cor pareceriam um alimentador só')


def test_clicar_escolhe_o_alimentador_daquele_traco(janela):
    """A pergunta que a aba existe para responder: qual é este?"""
    j = _montar(janela, _duas_ruas())
    _clicar(j, 'ALIM_L')
    assert j.pan_sel == ['ALIM_L']
    _clicar(j, 'ALIM_O')
    assert j.pan_sel == ['ALIM_O'], 'sem Ctrl, o clique troca a escolha'


def test_a_escolha_do_mapa_chega_na_lista_do_executar(janela):
    """Se isto falhar, o `Executar` extrai outro alimentador — em silêncio."""
    j = _montar(janela, _duas_ruas())
    _clicar(j, 'ALIM_L')
    assert [j.lst.get(i) for i in j.lst.curselection()] == ['ALIM_L']
    assert j._ui_selecionados() == ['ALIM_L']


def test_o_filtro_da_lista_nao_esconde_o_que_se_escolheu_no_mapa(janela):
    """Filtro aceso escondia justamente o alimentador recém-clicado.

    A lista mostra só o que casa com o filtro. Clicar no mapa num alimentador
    que o filtro exclui deixava a seleção sem linha visível — e o `Executar`
    parecia ter perdido a escolha.
    """
    j = _montar(janela, _duas_ruas())
    j.v_filtro.set('ALIM_O')
    assert j.lst.size() == 1

    _clicar(j, 'ALIM_L')
    assert j.v_filtro.get() == ''
    assert j._ui_selecionados() == ['ALIM_L']


def test_ctrl_clique_acumula_e_o_repetido_sai(janela):
    """Mesmo gesto do `Listbox` EXTENDED, para não haver duas regras."""
    j = _montar(janela, _duas_ruas())
    _clicar(j, 'ALIM_O')
    _clicar(j, 'ALIM_L', ctrl=True)
    assert sorted(j.pan_sel) == ['ALIM_L', 'ALIM_O']
    assert sorted(j._ui_selecionados()) == ['ALIM_L', 'ALIM_O']

    _clicar(j, 'ALIM_L', ctrl=True)
    assert j.pan_sel == ['ALIM_O'], 'Ctrl no já escolhido desmarca'


def test_clicar_no_vazio_desmarca(janela):
    """Sem isto não haveria como limpar a seleção feita no mapa."""
    j = _montar(janela, _duas_ruas())
    _clicar(j, 'ALIM_O')
    assert j.pan_sel

    o, s_, l, n = j._pan_caixa()
    j._pan_clicou(l + (l - o) * 5, n + (n - s_) * 5, False)
    assert j.pan_sel == []
    assert j._ui_selecionados() == []


def test_o_realce_pega_o_traco_inteiro_do_escolhido(janela):
    """Realçar meio alimentador seria pior que não realçar."""
    j = _montar(janela, _duas_ruas())
    _clicar(j, 'ALIM_O')
    assert len(j._pan_foco.get_segments()) == len(j.pan.partes_de('ALIM_O'))
    assert j._pan_col.get_alpha() < 1.0, 'o resto do mapa apaga'

    _clicar(j, 'ALIM_O')  # de novo, sem Ctrl: continua sendo ele
    assert j.pan_sel == ['ALIM_O']


def test_a_ficha_diz_o_que_se_sabe_do_escolhido(janela):
    """Código, nome, tensão, extensão e energia — é por isso que se escolhe."""
    j = _montar(janela, _duas_ruas())
    _clicar(j, 'ALIM_O')
    texto = j.v_pan_status.get()
    for pedaco in ('ALIM_O', 'Oeste', '13.8 kV', '12.5 km', '4200 MWh'):
        assert pedaco in texto, (pedaco, texto)


def test_buscar_pelo_codigo_escolhe_quando_sobra_um(janela):
    """Digitar o código inteiro escolhe; digitar o prefixo de vários, não.

    Saltar para um entre trinta candidatos seria adivinhar qual.
    """
    j = _montar(janela, _duas_ruas())
    j.v_pan_busca.set('ALIM')
    assert j.pan_sel == [], 'dois casam: nada a fazer'
    j.v_pan_busca.set('ALIM_L')
    assert j.pan_sel == ['ALIM_L']


# ── O que o fundo escuro exigiu ──────────────────────────────────────────────

def test_o_traco_fica_por_cima_do_mapa_de_fundo(janela):
    """O relato era "os trechos ficam por baixo do mapa". Não ficam.

    Este teste pinta um fundo opaco e conta os pixels do traço depois. Se
    alguém trocar o `zorder` do mosaico, ou o `imshow` mudar de comportamento,
    é aqui que aparece — e não na tela de quem estiver usando.
    """
    j = _montar(janela, _duas_ruas())
    vista = j.vista_pan
    j._pan_col.set_color('#ff0000')
    j._pan_col.set_linewidth(5.0)

    # O pixel EXATO onde o traço passa, e não a contagem de pixels vermelhos:
    # contar mudaria de valor só por causa da suavização das bordas, que antes
    # se mistura com o branco da figura e depois com o preto do mosaico. O
    # miolo do traço não tem essa ambiguidade.
    meio = j.pan_linhas[0].mean(axis=0)
    px, py = vista.ax.transData.transform(meio)

    def cor_do_traco():
        vista.canvas.draw()
        a = np.asarray(vista.canvas.buffer_rgba())
        return a[a.shape[0] - int(round(py)), int(round(px))]

    antes = cor_do_traco()
    assert antes[0] > 200 and antes[1] < 60, (
        'sem fundo, o traço tem de aparecer: %s' % antes)

    escuro = np.zeros((32, 32, 4), dtype=np.uint8)
    escuro[:, :, 3] = 255
    x0, x1 = vista.ax.get_xlim()
    y0, y1 = vista.ax.get_ylim()
    vista._fundo_alvo = 'alvo'
    assert vista.pintar_fundo('alvo', (escuro, (x0, x1, y0, y1)), '') is True

    assert vista._fundo_img.get_zorder() < j._pan_col.get_zorder()
    depois = cor_do_traco()
    assert depois[0] > 200 and depois[1] < 60, (
        'o mosaico tapou o traço: %s' % depois)


def test_o_contorno_so_aparece_onde_ele_serve(janela):
    """Sobre satélite o traço precisa de contorno; sobre o claro, não.

    Desenhar o contorno sempre dobraria o traço a renderizar num mapa que já
    tem centenas de milhares de vértices — custo sem retorno onde o fundo já
    dá contraste.
    """
    j = _montar(janela, _duas_ruas())
    assert j.v_pan_fundo.get() == 'claro'
    assert j._pan_contorno.get_visible() is False

    j.v_pan_fundo.set('hibrido')
    j._ui_pan_contornar()
    assert j._pan_contorno.get_visible() is True

    j.v_pan_fundo.set('ruas')
    j._ui_pan_contornar()
    assert j._pan_contorno.get_visible() is False


def test_o_contorno_apaga_junto_com_o_traco_que_ele_contorna(janela):
    """Senão o mapa vira sombra: o escolhido colorido e o resto só preto."""
    j = _montar(janela, _duas_ruas())
    j.v_pan_fundo.set('hibrido')
    j._ui_pan_contornar()
    cheio = j._pan_contorno.get_alpha()

    _clicar(j, 'ALIM_O')
    assert j._pan_contorno.get_alpha() < cheio


# ── Os dois mapas da janela ──────────────────────────────────────────────────

def test_os_dois_mapas_tem_cada_um_o_seu_fundo(janela):
    """A razão de a navegação ter saído de `mapa.py`.

    Antes, o mosaico de azulejos era estado da janela — um só. Com dois mapas,
    o que uma vista pediu não pode ser pintado na outra, e é a própria vista
    que vai na mensagem da fila para garantir isso.
    """
    j = _montar(janela, _duas_ruas())
    vista = j.vista_pan
    assert getattr(j, 'vista', None) is not vista

    # Um mosaico que chegou tarde — a vista já mudou de alvo — é recusado.
    vista._fundo_alvo = ('claro', 0, 0, 1, 1)
    assert vista.pintar_fundo(('claro', 9, 9, 9, 9), None, '') is False
    # E o que chega no alvo certo, mas vazio, avisa em vez de pintar.
    assert vista.pintar_fundo(('claro', 0, 0, 1, 1), None, '') is None


def test_a_fila_entrega_o_fundo_a_vista_certa(janela):
    """`_drenar` tem de aceitar a mensagem de quatro partes sem explodir."""
    j = _montar(janela, _duas_ruas())
    j.vista_pan._fundo_alvo = ('claro', 0, 0, 1, 1)
    j.fila.put(('fundo', (j.vista_pan, ('claro', 0, 0, 1, 1), None, '')))
    j._drenar()
    assert 'Sem mapa de fundo' in j.v_status.get()


def test_a_regua_do_tempo_some_na_aba_de_escolher(janela):
    """A barra do caso e a régua do tempo falam de um caso já carregado.

    Nesta aba ainda não há caso nenhum — deixá-las à vista sugere que se pode
    arrastar o tempo de coisa alguma, que é o tipo de detalhe que faz alguém
    clicar e concluir que o programa quebrou.
    """
    j = janela
    # `winfo_ismapped` não serve aqui: numa janela oculta ele é falso sempre.
    # Quem responde é o gerenciador de geometria, que `grid_remove` esvazia.
    j.abas.select(j.quadro_panorama)
    j._aba_trocou()
    assert j._barra_tempo.winfo_manager() == ''
    assert j._barra_caso.winfo_manager() == ''

    j.abas.select(j.quadro_mapa_aba)
    j._aba_trocou()
    assert j._barra_tempo.winfo_manager() == 'grid'
    assert j._barra_caso.winfo_manager() == 'grid'


@pytest.mark.skipif(not os.path.isfile(SSDMT),
                    reason='exige a fixture de alimentador em tests/dados/')
def test_a_base_de_verdade_desenha_e_responde_ao_clique(janela):
    """Fim a fim, com BDGD de verdade: do `.gpkg` ao alimentador escolhido."""
    pan = construir(SSDMT, cache=False)
    j = _montar(janela, pan)
    assert len(j._pan_col.get_segments()) == len(pan)
    _clicar(j, 'URB13')
    assert j._ui_selecionados() == ['URB13']
    assert 'URB13' in j.v_pan_status.get()


def test_base_sem_media_tensao_avisa_em_vez_de_quebrar(janela):
    """Mapa vazio é um aviso no rodapé, não um traceback na varredura."""
    j = janela
    j._ui_pan_desenhar(Panorama([], np.zeros((0, 2)),
                                np.zeros(1, dtype=np.int64),
                                np.zeros(0, dtype=np.int32)))
    assert j.vista_pan is None
    assert 'SSDMT' in j.v_pan_status.get()
