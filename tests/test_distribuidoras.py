# -*- coding: utf-8 -*-
"""O conversor fora da distribuidora com que foi construído.

`test_conversao.py` prova estabilidade: os mesmos bytes saem sempre, para o
alimentador de referência. Estabilidade não é generalidade — um conversor pode
reproduzir com perfeição um modelo que só está certo numa base.

Aqui cada fixture é de uma distribuidora diferente, e o que se pergunta não é se
os bytes batem, e sim se o caso **fecha**: compila, converge, tem barras e
cargas, e as tensões caem em faixa plausível. É a pergunta que sobrevive à
diferença entre as bases, e a única que faz sentido fazer às três.

As três geodatabases têm o mesmo esquema — 43 camadas, mesmos campos, mesmo CRS.
Tudo que separa uma da outra é conteúdo e convenção de codificação, e é aí que
um conversor calibrado numa base tropeça sem levantar exceção.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip('opendssdirect',
                    reason='o OpenDSS nao carregou (instalacao incompleta)')

from bdgdcase.solucao import verificar  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
DADOS = RAIZ / 'tests' / 'dados'

#: (pasta da fixture, o que ela traz de diferente).
#: `TRO05` fica de fora: é o caso de referência, coberto byte a byte em
#: `test_conversao.py`, e repeti-lo aqui só custaria tempo.
FIXTURES = [
    pytest.param('URB13', id='bt-em-220-127'),
    pytest.param('2_CVO_3', id='sem-curvas-de-carga'),
]

pytestmark = pytest.mark.dados


def _converter(alim, base):
    """Converte a fixture num diretório-base isolado e devolve a saída.

    Em subprocesso porque `bdgdcase.caminhos` resolve os diretórios na
    importação — mesma razão de `test_conversao.py`.
    """
    entrada = base / 'Output' / alim
    entrada.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(DADOS / alim, entrada)

    r = subprocess.run(
        [sys.executable, '-c',
         'import sys\n'
         'from bdgdcase.api import converter\n'
         'sys.exit(1 if converter([sys.argv[1]], dias=("DU",)) else 0)\n',
         str(entrada)],
        capture_output=True, text=True,
        # Encoding fixado nos dois lados. O relato da conversão tem acento, e
        # este teste procura por ele; se o filho escrever numa codificação e o
        # pai decodificar noutra, a busca falha por mojibake e o erro aponta
        # para a conversão, que está certa. Acontece de verdade a quem tem
        # PYTHONIOENCODING no ambiente.
        encoding='utf-8', errors='replace',
        env=dict(os.environ, BDGD_BASE_DIR=str(base),
                 PYTHONIOENCODING='utf-8'))
    assert r.returncode == 0, (r.stdout or '')[-3000:] + (r.stderr or '')[-2000:]
    saida = base / 'OpenDSS' / ('%s_DU' % alim)
    assert saida.is_dir(), (r.stdout or '')[-2000:]
    return saida, (r.stdout or '')


@pytest.fixture(scope='module', params=FIXTURES)
def caso(request, tmp_path_factory):
    alim = request.param
    if not (DADOS / alim).is_dir():
        pytest.skip('fixture %s ausente (veja tests/dados/LEIAME.md)' % alim)
    base = tmp_path_factory.mktemp('dist_%s' % alim.replace('/', '_'))
    saida, log = _converter(alim, base)
    return alim, saida, log


def test_o_caso_fecha(caso):
    """Compila, converge, e tem rede e carga de verdade."""
    _, saida, _ = caso
    r = verificar(saida)
    assert r['convergiu']
    assert r['barras'] > 100
    assert r['cargas'] > 0
    assert r['transformadores'] > 0


def test_as_tensoes_sao_plausiveis(caso):
    """`verificar` já recusa fora de 0,70–1,50 pu; aqui a faixa é mais estreita.

    Uma rede real de distribuição não opera a 0,75 pu nem a 1,4. A faixa larga
    do `verificar` existe para não recusar um alimentador longo e carregado; um
    caso de teste que caia nela indica erro de modelo, não rede difícil.
    """
    _, saida, _ = caso
    r = verificar(saida)
    assert 0.85 <= r['v_min_pu'] <= 1.05, 'tensão mínima fora do razoável'
    assert 0.95 <= r['v_max_pu'] <= 1.10, 'tensão máxima fora do razoável'


def test_nenhum_nan_chegou_ao_dss(caso):
    """`NaN` num `.dss` o OpenDSS aceita calado — e o elemento fica indefinido."""
    _, saida, _ = caso
    ruins = []
    for arq in sorted(saida.glob('*.dss')):
        texto = arq.read_text(encoding='utf-8', errors='ignore').lower()
        # `nphases` contém 'nan'? não — mas 'nan' aparece em nomes de cabo,
        # então só interessa como valor de propriedade.
        for marca in ('=nan', '=[nan', ' nan '):
            if marca in texto:
                ruins.append('%s (%s)' % (arq.name, marca.strip()))
    assert not ruins, 'valores nan no caso gerado: %s' % ', '.join(ruins)


def test_toda_barra_com_tensao_tem_base_coerente(caso):
    """A base errada não muda a tensão; muda a régua, e reprova o correto.

    Uma perna de center-tap de 115 V que case com a entrada de 0,127 kV aparece
    a 1,59 pu. O caso está certo e o relatório, não — e é o relatório que o
    usuário lê.
    """
    import opendssdirect as dss
    import numpy as np

    _, saida, _ = caso
    dss.Text.Command('Compile "%s"' % (saida / 'Master.dss').as_posix())
    dss.Solution.Solve()
    fora = []
    for nome in dss.Circuit.AllBusNames():
        dss.Circuit.SetActiveBus(nome)
        pu = np.array(dss.Bus.puVmagAngle()[0::2])
        pu = pu[pu > 0.05]          # nós mortos não têm base a conferir
        if len(pu) and (pu.max() > 1.15 or pu.min() < 0.80):
            fora.append('%s (%.3f-%.3f pu)' % (nome, pu.min(), pu.max()))
    assert not fora, '%d barra(s) fora de faixa: %s' % (len(fora), fora[:5])


def test_a_procedencia_da_curva_e_declarada(caso):
    """Quem recebe a pasta pronta tem de poder saber de onde as curvas vieram."""
    alim, saida, log = caso
    cabecalho = (saida / 'CurvasDeCarga.dss').read_text(encoding='utf-8')[:2000]
    cadastro = (saida / 'Cadastro_Cargas.csv').read_text(encoding='utf-8')

    if alim == '2_CVO_3':
        # Esta base traz a camada CRVCRG com os 101 campos e zero feições.
        assert 'ATENÇÃO' in cabecalho
        assert 'NÃO são desta distribuidora' in cabecalho
        assert 'referencia' in cadastro
        assert 'catálogo de referência' in log
    else:
        assert 'catalogo' in cadastro
        assert 'ATENÇÃO' not in cabecalho
