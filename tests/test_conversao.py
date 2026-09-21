# -*- coding: utf-8 -*-
"""A travessia inteira: tabelas da BDGD entram, um caso OpenDSS sai.

Os outros arquivos de teste cobrem peças isoladas. Este cobre o que o pacote é:
converte a fixture de `tests/dados/` e compara o resultado, byte a byte, com o
caso guardado em `src/bdgdcase/exemplo/`. Qualquer mudança de comportamento
aparece aqui — uma fase que passa a ser modelada de outro jeito, um kVA que
muda, uma linha de `.dss` a mais.

O caso de referência é o mesmo que `bdgdcase exemplo` roda, o que tem uma
consequência boa: se ele ficar desatualizado, o teste avisa, em vez de o usuário
descobrir sozinho que o exemplo não corresponde mais ao código.
"""
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
DADOS = RAIZ / 'tests' / 'dados'
ESPERADO = RAIZ / 'src' / 'bdgdcase' / 'exemplo' / 'TRO05_DU'
ALIM = 'TRO05'

pytestmark = pytest.mark.dados

falta = not (DADOS / ALIM).is_dir() or not ESPERADO.is_dir()
MOTIVO_FALTA = 'fixture ou caso de referência ausentes (veja tests/dados/LEIAME.md)'
sem_fixture = pytest.mark.skipif(falta, reason=MOTIVO_FALTA)


def _sha(caminho):
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def _converter(base, dias=('DU',)):
    """Converte num diretório-base isolado e devolve a pasta de saída.

    Em subprocesso porque `bdgdcase.caminhos` resolve os diretórios na
    importação: mudar `BDGD_BASE_DIR` no processo atual não teria efeito.
    """
    entrada = base / 'Output' / ALIM
    entrada.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(DADOS / ALIM, entrada)

    env = dict(os.environ, BDGD_BASE_DIR=str(base))
    r = subprocess.run(
        [sys.executable, '-c',
         'import sys\n'
         'from bdgdcase.api import converter\n'
         'erros = converter([sys.argv[1]], dias=tuple(sys.argv[2:]))\n'
         'sys.exit(1 if erros else 0)\n',
         str(entrada), *dias],
        capture_output=True, text=True, env=env)
    assert r.returncode == 0, (r.stdout or '') + (r.stderr or '')
    return base / 'OpenDSS' / ('%s_DU' % ALIM)


@pytest.fixture(scope='module')
def caso(tmp_path_factory):
    if falta:
        pytest.skip('fixture ausente')
    return _converter(tmp_path_factory.mktemp('caso'))


@sem_fixture
def test_gera_os_arquivos_que_o_master_referencia(caso):
    gerados = {p.name for p in caso.iterdir()}
    assert gerados >= {p.name for p in ESPERADO.iterdir()}


@sem_fixture
@pytest.mark.parametrize('nome', sorted(p.name for p in ESPERADO.iterdir())
                         if ESPERADO.is_dir() else [])
def test_arquivo_bate_byte_a_byte(caso, nome):
    gerado = caso / nome
    assert gerado.is_file(), '%s não foi gerado' % nome
    if _sha(gerado) == _sha(ESPERADO / nome):
        return
    # Falhou: mostra a primeira linha divergente, que diz mais que o hash.
    a = gerado.read_text(encoding='utf-8').splitlines()
    b = (ESPERADO / nome).read_text(encoding='utf-8').splitlines()
    for i, (x, y) in enumerate(zip(a, b), 1):
        if x != y:
            pytest.fail('%s difere na linha %d\n  gerado:   %s\n  esperado: %s'
                        % (nome, i, x[:160], y[:160]))
    pytest.fail('%s tem %d linhas, o esperado tem %d' % (nome, len(a), len(b)))


@sem_fixture
def test_master_e_um_circuito_completo(caso):
    """O Master precisa fechar: fonte, tensão de base e os redirects."""
    texto = (caso / 'Master.dss').read_text(encoding='utf-8')
    baixo = texto.lower()
    assert 'new circuit.' in baixo
    assert 'set voltagebases' in baixo
    for arquivo in ('LineCodes.dss', 'Linhas_MT.dss', 'Transformadores.dss',
                    'Cargas_BT.dss'):
        assert arquivo.lower() in baixo, 'Master não redireciona %s' % arquivo


@sem_fixture
def test_conversao_e_deterministica(tmp_path_factory):
    """Mesma entrada, mesmos bytes — inclusive na curva fotovoltaica.

    É a diferença deliberada em relação ao projeto de origem, onde a curva PV
    sai diferente a cada execução. Sem isto, nenhum dos testes acima seria
    possível.
    """
    a = _converter(tmp_path_factory.mktemp('det_a'))
    b = _converter(tmp_path_factory.mktemp('det_b'))
    for p in sorted(a.iterdir()):
        if p.suffix in ('.dss', '.csv'):
            assert _sha(p) == _sha(b / p.name), '%s muda entre execuções' % p.name


@sem_fixture
def test_tipo_de_dia_muda_a_pasta_de_saida(tmp_path_factory):
    base = tmp_path_factory.mktemp('sabado')
    _converter(base, dias=('DU', 'SA'))
    assert (base / 'OpenDSS' / ('%s_SA' % ALIM)).is_dir()


# ── Fluxo reverso nos reguladores de tensão ──────────────────────────────────

def test_o_modo_de_fluxo_reverso_e_escolhivel(tmp_path):
    """Os quatro modos escrevem `RegControl` diferentes, e o padrão não muda nada.

    A escolha não é cosmética: um regulador que vai a neutro no reverso deixa
    de influir na tensão, e outro que continua regulando não. Quem monta um
    estudo com geração distribuída precisa escolher — e até aqui a escolha
    existia só como constante de módulo, sem jeito de mexer nela sem editar o
    código do pacote.
    """
    import os
    import subprocess
    import sys

    from bdgdcase.modelo.config import MODOS_REVERSO

    if falta:
        pytest.skip(MOTIVO_FALTA)
    fixture = RAIZ / 'tests' / 'dados' / 'TRO05'
    if not fixture.is_dir():
        pytest.skip('fixture ausente')

    def _converter(modo):
        base = tmp_path / modo
        alvo = base / 'Output' / 'TRO05'
        alvo.parent.mkdir(parents=True)
        shutil.copytree(str(fixture), str(alvo))
        r = subprocess.run(
            [sys.executable, '-c',
             'import sys; from bdgdcase.modelo.pipeline import processar_lote; '
             'processar_lote([sys.argv[1]], dias=("DU",), '
             'regulador_reverso=sys.argv[2])', str(alvo), modo],
            env=dict(os.environ, BDGD_BASE_DIR=str(base),
                     PYTHONPATH=str(RAIZ / 'src')),
            capture_output=True, text=True)
        saida = base / 'OpenDSS' / 'TRO05_DU'
        assert saida.is_dir(), (r.stderr or r.stdout)[-1500:]
        return saida

    # o padrão reproduz o caso de referência byte a byte
    padrao = _converter('neutro')
    for p in ESPERADO.iterdir():
        if p.is_file():
            assert _sha(padrao / p.name) == _sha(p), p.name

    assert set(MODOS_REVERSO) == {'neutro', 'cogeracao', 'bidirecional',
                                  'direto'}


def test_modo_de_fluxo_reverso_invalido_e_recusado():
    from bdgdcase.api import converter
    with pytest.raises(ValueError, match='fluxo reverso'):
        converter(['qualquer'], regulador_reverso='ao_contrario')
