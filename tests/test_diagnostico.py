# -*- coding: utf-8 -*-
"""Duas armadilhas que fizeram um relato de erro apontar para o lugar errado.

As duas vêm de uma execução real da janela. Nenhuma delas levanta exceção na
hora em que acontece — as duas cobram o preço depois, e longe.

**A descrição de erro herdada.** O OpenDSS guarda a última descrição de erro num
buffer que nem sempre é substituído. Depois de uma violação de acesso o motor
fica corrompido para o resto do processo, e todo comando seguinte falha
repetindo a mensagem ANTIGA. O usuário recebeu "falhou ao abrir o caso X: erro
no comando `Set MaxIter=300`" — comando que a abertura de caso não emite, e que
mandou procurar defeito num caso que estava perfeito.

**A pasta de trabalho dentro da pasta de saída.** É lá que `Output/` e `OpenDSS/`
são criadas. Escolher a própria `OpenDSS/` gera `OpenDSS/OpenDSS/`, e como o
seletor do sistema reabre onde parou, o engano se aprofunda: na máquina de onde
veio o relato havia `OpenDSS/OpenDSS/OpenDSS/`.
"""
import os

import pytest


# ── A descrição herdada de outro comando ─────────────────────────────────────

#: O texto exato que o OpenDSS devolveu na execução que motivou esta correção.
ERRO_REAL = (
    '(#303) Error 303 Reported From OpenDSS Intrinsic Function:\n'
    'ProcessCommand: Exception Raised While Processing DSS Command:\n'
    'Set MaxIter=300\n'
    '\n'
    'Error Description:\n'
    'Access violation'
)


def test_reconhece_a_descricao_que_sobrou_de_outro_comando():
    """`Set MaxIter=300` não é emitido ao abrir um caso — só depois dele."""
    from bdgdcase.solucao import _erro_e_de_outro_comando
    assert _erro_e_de_outro_comando(ERRO_REAL)


@pytest.mark.parametrize('texto', [
    'ProcessCommand: Exception Raised While Processing DSS Command:\n'
    'Compile "C:/casos/TRO05_DU/Master.dss"',
    'ProcessCommand: Exception Raised While Processing DSS Command:\nClear',
])
def test_erro_do_proprio_compilar_nao_e_acusado_de_herdado(texto):
    """Erro que é mesmo da abertura do caso não pode ganhar a ressalva.

    Se ganhasse, toda falha legítima de compilação passaria a dizer "procure em
    outro lugar" — e o aviso deixaria de valer justamente por aparecer sempre.
    """
    from bdgdcase.solucao import _erro_e_de_outro_comando
    assert not _erro_e_de_outro_comando(texto)


def test_erro_sem_a_marca_de_comando_passa_batido():
    from bdgdcase.solucao import _erro_e_de_outro_comando
    assert not _erro_e_de_outro_comando('(#8888) There is no active circuit')
    assert not _erro_e_de_outro_comando('')


def test_motor_corrompido_e_trocado_e_o_caso_bom_abre(tmp_path, monkeypatch):
    """Uma violação de acesso deixou de condenar o resto da sessão.

    O motor não volta depois dela: todo comando falha repetindo a descrição do
    erro ORIGINAL. Antes, a saída era fechar e reabrir o programa — pior na
    janela, que fica aberta e passa a recusar todo caso novo. Hoje o motor é
    trocado por um contexto limpo e o caso abre.

    E abre **avisando**: o motor só chega a esse estado depois de algo morrer
    no meio, e quem estiver lendo os números do que rodou antes precisa saber.
    """
    import warnings

    from bdgdcase import solucao

    caso = tmp_path / 'ALIM_DU'
    caso.mkdir()
    (caso / 'Master.dss').write_text('New Circuit.x' + chr(10),
                                     encoding='utf-8')

    class _Texto:
        def Command(self, _cmd):
            raise RuntimeError(ERRO_REAL)

    class _MotorMorto:
        Text = _Texto()

    monkeypatch.setattr(solucao, '_MOTOR', None, raising=False)
    monkeypatch.setattr(solucao, '_dss', lambda: _MotorMorto())
    with warnings.catch_warnings(record=True) as avisos:
        warnings.simplefilter('always')
        solucao._compilar(str(caso))
    monkeypatch.setattr(solucao, '_MOTOR', None, raising=False)

    assert avisos, 'trocar o motor em silencio esconde a falha anterior'
    texto = str(avisos[0].message)
    assert 'corrompido' in texto and 'ANTES' in texto


def test_a_ressalva_entra_na_mensagem_quando_nem_o_motor_novo_salva(
        tmp_path, monkeypatch):
    """Detectar não basta: o recado tem de chegar a quem está lendo o erro.

    Quando nem o motor limpo abre o caso — ou quando esta instalação não
    oferece contextos —, o erro volta, e com a ressalva de que a descrição é
    herdada de outra falha.
    """
    from bdgdcase import solucao

    monkeypatch.setattr(solucao, '_trocar_motor', lambda: None)

    caso = tmp_path / 'ALIM_DU'
    caso.mkdir()
    (caso / 'Master.dss').write_text('New Circuit.x\n', encoding='utf-8')

    class _Texto:
        def Command(self, _cmd):
            raise RuntimeError(ERRO_REAL)

    class _FalsoDss:
        Text = _Texto()

    monkeypatch.setattr(solucao, '_dss', lambda: _FalsoDss())
    with pytest.raises(RuntimeError) as e:
        solucao._compilar(str(caso))

    msg = str(e.value)
    assert 'Set MaxIter=300' in msg, 'o texto original tem de continuar lá'
    assert 'falha ANTERIOR' in msg
    assert 'trocá-lo por um limpo' in msg, (
        'o recado tem de dizer que a troca do motor ja foi tentada')


def test_o_diretorio_de_trabalho_volta_mesmo_quando_falha(tmp_path, monkeypatch):
    """O `Compile` troca o cwd do processo; a falha não pode deixá-lo trocado."""
    from bdgdcase import solucao

    caso = tmp_path / 'ALIM_DU'
    caso.mkdir()
    (caso / 'Master.dss').write_text('New Circuit.x\n', encoding='utf-8')

    class _Texto:
        def Command(self, _cmd):
            os.chdir(str(caso))          # como o OpenDSS faz
            raise RuntimeError('estourou')

    class _FalsoDss:
        Text = _Texto()

    monkeypatch.setattr(solucao, '_dss', lambda: _FalsoDss())
    antes = os.getcwd()
    with pytest.raises(RuntimeError):
        solucao._compilar(str(caso))
    assert os.getcwd() == antes


# ── A pasta de trabalho dentro da pasta de saída ─────────────────────────────

def test_pasta_de_trabalho_correta_nao_e_acusada():
    from bdgdcase.interface.preparar import pasta_de_trabalho_suspeita
    assert pasta_de_trabalho_suspeita(os.path.abspath('.')) is None
    assert pasta_de_trabalho_suspeita('') is None
    assert pasta_de_trabalho_suspeita(None) is None


@pytest.mark.parametrize('sufixo', [
    ('OpenDSS',), ('Output',), ('opendss',),
    ('OpenDSS', 'OpenDSS'), ('OpenDSS', 'Output'),
    ('OpenDSS', 'OpenDSS', 'OpenDSS'),
])
def test_pasta_de_saida_e_devolvida_ao_pai_util(tmp_path, sufixo):
    """De qualquer profundidade, a sugestão é o primeiro ancestral que serve.

    Inclusive de `OpenDSS/OpenDSS/OpenDSS`, que é onde a máquina do relato
    chegou depois de três enganos seguidos.
    """
    from bdgdcase.interface.preparar import pasta_de_trabalho_suspeita
    base = tmp_path / 'trabalho'
    alvo = base.joinpath(*sufixo)
    alvo.mkdir(parents=True)
    assert pasta_de_trabalho_suspeita(str(alvo)) == str(base)


def test_uma_pasta_chamada_output_no_meio_do_caminho_nao_conta():
    """Só o fim do caminho decide — `.../Output/estudo` é escolha legítima.

    Quem organiza o trabalho dentro de uma pasta chamada `Output` tem esse
    direito; o que não serve é a pasta de trabalho SER a de saída.
    """
    from bdgdcase.interface.preparar import pasta_de_trabalho_suspeita
    caminho = os.path.abspath(os.path.join('C:', os.sep, 'x', 'Output', 'estudo'))
    assert pasta_de_trabalho_suspeita(caminho) is None


def test_erro_comum_nao_troca_o_motor(monkeypatch):
    """Trocar o motor a cada erro esconderia defeito de caso.

    A troca só se justifica pela assinatura do motor envenenado: uma descrição
    que fala de um comando que a abertura do caso não emite. Um caso realmente
    quebrado tem de falhar na primeira tentativa, com o erro dele.
    """
    from bdgdcase import solucao

    tentativas = []

    class _CasoRuim:
        class Text:
            @staticmethod
            def Command(c):
                tentativas.append(c)
                raise RuntimeError('(#183) Y matrix build aborted')

    monkeypatch.setattr(solucao, '_MOTOR', None, raising=False)
    ruim = _CasoRuim()
    dss, erro = solucao._abrir_com_recuo(ruim, 'qualquer/Master.dss', '.')

    assert dss is ruim, 'nao se troca o motor por erro de caso'
    assert 'Y matrix' in str(erro)
    assert tentativas == ['Clear'], 'uma tentativa so'


# ── O erro de tensão manda procurar onde? ────────────────────────────────────

class _BarraFalsa:
    """O mínimo da API de barras do OpenDSS que `_explicar_tensao` consulta."""

    def __init__(self, dono):
        self._dono = dono

    def puVmagAngle(self):
        pus = self._dono.tensoes[self._dono.ativa]
        saida = []
        for pu in pus:
            saida += [pu, 0.0]
        return saida


class _DssFalso:
    """Um circuito de mentira, só com o que a explicação lê."""

    def __init__(self, tensoes, geracao, trafo=None):
        self.tensoes, self.geracao, self.trafo = tensoes, geracao, trafo
        self.ativa = next(iter(tensoes))
        self.Bus = _BarraFalsa(self)
        self.Circuit = self
        self.PVsystems = self
        self.Generators = None
        self._i = 0

    # Circuit
    def AllBusNames(self):
        return list(self.tensoes)

    def SetActiveBus(self, b):
        self.ativa = b.lower()

    # PVsystems, percorrido como uma coleção do OpenDSS
    def First(self):
        self._i = 0
        return 1 if self.geracao else 0

    def Next(self):
        self._i += 1
        return 1 if self._i < len(self.geracao) else 0

    def Pmpp(self):
        return list(self.geracao.values())[self._i]

    @property
    def CktElement(self):
        return self

    def BusNames(self):
        return [list(self.geracao)[self._i]]


def test_o_erro_de_tensao_diz_quantos_nos_sairam_da_faixa():
    """Dois nós em vinte mil é uma frase; "tensões fora do plausível" não é.

    Sem a proporção, um alimentador rejeitado por dois nós parece tão quebrado
    quanto um rejeitado por metade da rede.
    """
    from bdgdcase.solucao import _explicar_tensao

    dss = _DssFalso({'bt_a': [1.0], 'bt_b': [1.0], 'bt_ruim': [1.9]}, {})
    recado = _explicar_tensao(dss, 0.7, 1.5)
    assert '1 no(s) fora da faixa, de 3 energizados' in recado
    assert 'bt_ruim' in recado
    assert 'Nenhuma dessas barras tem geracao' in recado


def test_o_erro_aponta_a_barra_da_GERACAO_e_nao_a_pior():
    """Quem dispara a sobretensão é a barra da usina.

    A que aparece com o pu mais alto costuma ser a vizinha a jusante, que não
    tem geração nenhuma — apontar essa mandaria procurar no lugar errado, que
    é justamente o defeito que esta mensagem existe para corrigir.
    """
    from bdgdcase.solucao import _explicar_tensao

    dss = _DssFalso({'bt_usina': [1.80], 'bt_vizinha': [1.81]},
                    {'bt_usina': 75.0})
    dss._trafo = ('tr_x', 5.0)
    import bdgdcase.solucao as sol
    orig = sol._trafo_acima
    sol._trafo_acima = lambda d, b: ('tr_x', 5.0)
    try:
        recado = _explicar_tensao(dss, 0.7, 1.5)
    finally:
        sol._trafo_acima = orig

    assert 'bt_vizinha' in recado, 'o pior continua sendo dito'
    assert 'bt_usina' in recado and '75 kW' in recado
    assert '5 kVA (15.0x)' in recado
    assert 'registro de ajustes' in recado


def test_sem_no_fora_da_faixa_nao_ha_o_que_explicar():
    """A explicação só entra no erro, e erro sem nó fora não existe."""
    from bdgdcase.solucao import _explicar_tensao

    assert _explicar_tensao(_DssFalso({'bt_a': [1.0]}, {}), 0.7, 1.5) == ''

