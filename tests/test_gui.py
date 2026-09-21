# -*- coding: utf-8 -*-
"""A janela monta, e a lógica que não é desenho é testável sem ela.

Interface gráfica costuma virar a parte do código que ninguém testa, e por isso
é onde um `AttributeError` fica escondido até alguém clicar. O que dá para
verificar sem olho humano é bastante: que a janela monta inteira sem exceção,
que a resolução do caminho da geodatabase acerta os casos difíceis, e que o
filtro da lista faz o que promete.

Roda sem servidor gráfico? Não — `Tk()` exige um display. Num runner Linux sem
X, estes testes pulam; no Windows e no macOS rodam de verdade.
"""

import pytest

tk = pytest.importorskip('tkinter', reason='este Python não tem tkinter')

from bdgdcase.interface.preparar import resolver_gdb  # noqa: E402


@pytest.fixture(scope='module')
def raiz():
    """Uma única raiz Tk para o módulo inteiro.

    Criar e destruir vários `Tk()` no mesmo processo é frágil: a partir do
    segundo, o interpretador Tcl pode não reencontrar o `init.tcl` e falhar com
    uma mensagem que parece falta de servidor gráfico, mas não é. Uma raiz só, e
    cada teste ganha o seu `Toplevel`.
    """
    try:
        r = tk.Tk()
    except tk.TclError as exc:            # sem display (CI Linux headless)
        pytest.skip('sem servidor gráfico: %s' % exc)
    r.withdraw()                          # não pisca na tela de quem roda
    yield r
    r.destroy()


@pytest.fixture
def janela(raiz):
    """Uma janela descartável, filha da raiz do módulo."""
    j = tk.Toplevel(raiz)
    j.withdraw()
    yield j
    j.destroy()


# ── resolver_gdb: a pegadinha do zip da ANEEL ────────────────────────────────

def _fazer_gdb(pasta):
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / 'a00000001.gdbtable').write_bytes(b'\x00')
    return pasta


def test_gdb_direta_e_aceita(tmp_path):
    alvo = _fazer_gdb(tmp_path / 'BDGD.gdb')
    assert resolver_gdb(alvo) == str(alvo)


def test_gdb_aninhada_e_encontrada(tmp_path):
    """O caso real: o zip da ANEEL põe a .gdb dentro de outra de mesmo nome."""
    fora = tmp_path / 'Celesc-Dis_5697_2024-12-31_V11.gdb'
    dentro = _fazer_gdb(fora / 'Celesc-Dis_5697_2024-12-31_V11.gdb')
    assert resolver_gdb(fora) == str(dentro)


def test_pasta_sem_geodatabase_devolve_nada(tmp_path):
    (tmp_path / 'vazia').mkdir()
    assert resolver_gdb(tmp_path / 'vazia') is None
    assert resolver_gdb(tmp_path / 'nem_existe') is None


def test_nao_desce_dois_niveis(tmp_path):
    """Descer indefinidamente acharia geodatabase onde o usuário não apontou."""
    _fazer_gdb(tmp_path / 'a' / 'b' / 'c.gdb')
    assert resolver_gdb(tmp_path / 'a') is None


# ── A janela ─────────────────────────────────────────────────────────────────

@pytest.fixture
def painel(janela):
    """A aba de preparar, dentro da janela única.

    Depois da fusão ela deixou de ser um programa à parte: é um mixin de
    `_Janela`, e a única forma honesta de testá-la é pela janela inteira, que é
    como o usuário a encontra.
    """
    from bdgdcase.interface.janela import _Janela
    return _Janela(janela, aba='preparar')


def test_a_janela_monta_inteira(painel):
    assert painel.btn_ir['text'] == 'Executar'
    assert painel.lst.size() == 0
    assert painel.prog['value'] == 0


def test_o_registro_recebe_a_mensagem_de_boas_vindas(painel):
    painel._ui_drenar()
    assert 'Varrer alimentadores' in painel.txt_log.get('1.0', 'end')


def test_a_lista_e_povoada_pela_fila_da_thread(painel):
    painel.fila_prep.put(('alimentadores', (['ACA01', 'IBA09', 'TRO05'], ['4204202'])))
    painel._ui_drenar()
    assert painel.lst.size() == 3
    assert list(painel.cb_mun['values']) == ['', '4204202']


def test_o_filtro_reduz_a_lista(painel):
    painel.alimentadores = ['ACA01', 'IBA09', 'IBA10', 'TRO05']
    painel.v_filtro.set('iba')                       # sem diferenciar maiúsculas
    painel._ui_repovoar()
    assert [painel.lst.get(i) for i in range(painel.lst.size())] == ['IBA09',
                                                                    'IBA10']
    painel.v_filtro.set('')
    painel._ui_repovoar()
    assert painel.lst.size() == 4


def test_o_progresso_chega_pela_fila(painel):
    painel.fila_prep.put(('progresso', 42.0))
    painel._ui_drenar()
    assert painel.prog['value'] == pytest.approx(42.0)


def test_o_fim_destrava_os_botoes(painel):
    painel._ui_travar(True)
    assert str(painel.btn_ir['state']) == 'disabled'
    painel.fila_prep.put(('fim', None))
    painel._ui_drenar()
    assert str(painel.btn_ir['state']) == 'normal'
    # Sem dia rodado, não há CSV a salvar.
    assert str(painel.btn_csv['state']) == 'disabled'


def test_erro_na_thread_vira_linha_no_registro(painel):
    def explodir():
        raise RuntimeError('falha de propósito')

    painel._rodar_em_thread(explodir)
    for _ in range(200):                             # a thread é assíncrona
        painel._ui_drenar()
        if 'falha de propósito' in painel.txt_log.get('1.0', 'end'):
            break
        painel.root.update()
    texto = painel.txt_log.get('1.0', 'end')
    assert 'ERRO: falha de propósito' in texto
    # E, o mais importante: destravou. Erro que deixa a janela morta é pior
    # que erro nenhum.
    painel._ui_drenar()
    assert str(painel.btn_ir['state']) == 'normal'


def test_dias_padrao_e_apenas_dia_util(painel):
    marcados = [d for d, v in painel.v_dias.items() if v.get()]
    assert marcados == ['DU']


def test_todas_as_etapas_vem_marcadas(painel):
    assert all(v.get() for v in (painel.v_extrair, painel.v_converter,
                                 painel.v_verificar, painel.v_rodar))


def test_abrir_recusa_janela_desconhecida():
    from bdgdcase.interface.preparar import abrir
    with pytest.raises(ValueError, match='completa'):
        abrir('inexistente')
