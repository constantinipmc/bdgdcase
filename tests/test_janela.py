# -*- coding: utf-8 -*-
"""Uma janela só: extrair e simular sem trocar de programa.

Eram dois programas. `bdgdcase gui` extraía e convertia; `bdgdcase mapa` abria o
caso. Ver no mapa o que se acabou de extrair passava por um botão que lançava um
segundo processo — uma fronteira do código, não do trabalho: quem extrai um
alimentador quer olhar para ele em seguida.

Estes testes fixam a fusão. Não o desenho — isso é `test_mapa.py` — mas as
propriedades que a tornam uma janela e não duas coladas: as quatro abas existem,
os dois comandos abrem a mesma coisa, o caso recém-criado entra na aba do mapa
em vez de num processo novo, e as duas threads não disputam a mesma fila.

Roda sem servidor gráfico? Não. Num runner Linux sem X estes testes pulam.
"""
import inspect

import pytest

tk = pytest.importorskip('tkinter', reason='este Python não tem tkinter')


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


def _abas(j):
    return [j.abas.tab(i, 'text').strip() for i in range(len(j.abas.tabs()))]


def test_as_abas_existem_na_ordem_do_trabalho(janela):
    """Escolher, olhar, resumir, auditar — a cadeia inteira num lugar só.

    A ordem é o trabalho: os alimentadores no mapa vêm ANTES do mapa do caso,
    porque escolher o alimentador precede olhar para ele.
    """
    assert _abas(janela) == ['Preparar', 'Alimentadores no mapa', 'Mapa',
                             'Resumo do alimentador', 'Ajustes do cadastro']


def test_a_janela_comeca_onde_lhe_pedirem(raiz):
    """`bdgdcase gui` e `bdgdcase mapa` diferem só nisto."""
    from bdgdcase.interface.janela import _Janela
    for aba, esperada in (('preparar', 'Preparar'), ('mapa', 'Mapa')):
        top = tk.Toplevel(raiz)
        top.withdraw()
        try:
            j = _Janela(top, aba=aba)
            atual = j.abas.tab(j.abas.select(), 'text').strip()
            assert atual == esperada, aba
        finally:
            top.destroy()


def test_os_dois_comandos_abrem_a_mesma_janela():
    """A prova de que a fusão é real, e não duas janelas com a mesma cara.

    Sem isto, nada impediria alguém de reintroduzir uma segunda janela e manter
    os testes verdes — cada um passaria na sua.
    """
    from bdgdcase.interface import janela as J
    from bdgdcase.interface import preparar as P

    assert callable(J.abrir_janela)
    assert 'aba' in inspect.signature(J.abrir_janela).parameters
    # `abrir_mapa` e `abrir` são atalhos para ela, e não janelas próprias
    for fonte in (inspect.getsource(J.abrir_mapa), inspect.getsource(P.abrir)):
        assert 'abrir_janela' in fonte
        assert 'Tk()' not in fonte


def test_ver_no_mapa_nao_abre_outro_processo(janela, monkeypatch, tmp_path):
    """O botão leva o caso para a aba do mapa, na mesma janela.

    Antes ele chamava `subprocess.Popen`, porque eram dois programas com duas
    raízes Tk. Uma raiz só é o que permitiu trocar isso por trocar de aba — e é
    o que este teste guarda: se o `Popen` voltar, é porque a fusão foi desfeita.
    """
    import subprocess

    def nao_pode(*a, **k):
        raise AssertionError('abriu outro processo: %r' % (a,))

    monkeypatch.setattr(subprocess, 'Popen', nao_pode)
    monkeypatch.setattr(type(janela), '_simular', lambda self: None)

    caso = tmp_path / 'TRO05_DU'
    caso.mkdir()
    janela.ultimo_caso = str(caso)
    janela._ui_mapa()

    assert janela.v_pasta.get() == str(caso)
    assert janela.abas.tab(janela.abas.select(), 'text').strip() == 'Mapa'


def test_ver_no_mapa_sem_caso_nao_faz_nada(janela):
    """Botão apertado antes de haver caso não pode trocar de aba nem explodir."""
    janela.ultimo_caso = None
    janela._ui_mapa()
    assert janela.abas.tab(janela.abas.select(), 'text').strip() == 'Preparar'


def test_as_duas_threads_nao_dividem_a_mesma_fila(janela):
    """Fila compartilhada faria um dreno comer a mensagem do outro.

    E o prejuízo não apareceria como erro: apareceria como barra de progresso
    que não anda, ou passo de simulação que não chega — sintoma sem exceção,
    que é o mais caro de investigar.
    """
    assert janela.fila is not janela.fila_prep

    janela.fila_prep.put(('progresso', 42.0))
    janela._ui_drenar()
    assert janela.prog['value'] == 42.0
    assert janela.fila.empty()


def test_os_controles_do_caso_somem_na_aba_de_preparar(janela):
    """Régua do tempo sobre caso nenhum convida a clicar e concluir que quebrou."""
    # `winfo_manager()` e não `winfo_ismapped()`: a janela do teste está oculta,
    # e nela nada está mapeado — o segundo daria falso mesmo com o widget lá.
    janela.abas.select(janela.quadro_preparar)
    janela._aba_trocou()
    for quadro in (janela._barra_caso, janela._barra_tempo, janela._rodape):
        assert quadro.winfo_manager() == '', quadro

    janela.abas.select(janela.quadro_mapa_aba)
    janela._aba_trocou()
    for quadro in (janela._barra_caso, janela._barra_tempo, janela._rodape):
        assert quadro.winfo_manager() == 'grid', quadro


def test_o_registro_da_preparacao_nao_e_o_painel_do_ponto(janela):
    """Dois `Text` com o mesmo nome de atributo é uma sobrescrever a outra.

    Foi a colisão real da fusão: `_Painel.txt` era o registro, `_Janela.txt` era
    o inspetor. Juntas na mesma classe, a segunda apagava a primeira, e o log da
    extração ia parar no painel do ponto.
    """
    assert janela.txt is not janela.txt_log
    janela._log('linha de teste')
    janela._ui_drenar()
    assert 'linha de teste' in janela.txt_log.get('1.0', 'end')
    assert 'linha de teste' not in janela.txt.get('1.0', 'end')


def test_falha_do_mapa_vai_para_o_registro_e_nao_so_para_o_rodape(raiz, tmp_path):
    """A primeira falha da sessão tem de deixar rastro onde se possa procurá-la.

    O rodapé do mapa é um rótulo de uma linha: a mensagem seguinte o apaga, e
    ele some junto com a barra ao voltar para a aba de preparar. O traceback ia
    para a saída padrão, que numa janela aberta por atalho não existe.

    Isso importava mais do que parece. Depois de uma violação de acesso o motor
    do OpenDSS passa a recusar **todo** caso, repetindo a descrição do erro
    original — e a única forma de achar a causa é a primeira falha. Dizer
    "procure no registro" só vale se ela estiver lá.
    """
    import time

    from bdgdcase.interface.janela import _Janela

    caso = tmp_path / 'QUEBRADO_DU'
    caso.mkdir()
    (caso / 'Master.dss').write_text('New Circuit.x' + chr(10) +
                                     'Redirect nao_existe.dss' + chr(10),
                                     encoding='utf-8')

    top = tk.Toplevel(raiz)
    top.withdraw()
    j = _Janela(top, aba='mapa')
    try:
        j.v_pasta.set(str(caso))
        j._simular()
        # o trabalho roda numa thread; o registro so enche quando o laco do Tk
        # drena a fila, e aqui o laco nao esta rodando.
        texto = ''
        for _ in range(200):
            # duas filas, dois drenos: o registro e da aba de preparar, o
            # rodape e da simulacao. Ver o cabecalho de `interface.preparar`.
            j._ui_drenar()
            j._drenar()
            texto = j.txt_log.get('1.0', 'end')
            if 'ERRO ao simular' in texto:
                break
            time.sleep(0.05)
        assert 'ERRO ao simular' in texto, texto[-400:]
        assert str(caso) in texto, 'o registro tem de nomear o caso'
        assert 'Preparar' in j.v_status.get(), (
            'o rodape deve apontar para onde o texto inteiro esta')
    finally:
        top.destroy()

