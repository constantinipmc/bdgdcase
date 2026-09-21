# -*- coding: utf-8 -*-
"""A ordem das propriedades de `Line` importa, e não é a ordem natural de ler.

Um trecho de rede escrito com `phases=1` e, na linha seguinte, o código de um
condutor trifásico não é um trecho de uma fase: ao receber o `LineCode` o
OpenDSS remonta o elemento com o número de fases DELE. O arquivo diz uma coisa e
o motor monta outra, sem aviso.

Ninguém percebe pelo resultado. As tensões dos nós que existem continuam certas
— o que aparece é um nó a mais na barra, morto, que o `CalcVoltageBases` conta
ao escolher a base e que puxa a régua para baixo. Uma barra de 117 V ficou com
base de 73 V e foi reprovada a 1,59 pu.

Eram 20.101 trechos assim, em todos os alimentadores convertidos e em todas as
distribuidoras testadas.
"""
import re
from pathlib import Path

import pytest

from test_conversao import ESPERADO, falta

pytestmark = pytest.mark.skipif(falta, reason='caso de referência ausente')

#: `New Line.<nome> ...` até o próximo `New ` ou o fim do arquivo.
BLOCO = re.compile(r'New Line\.(?P<nome>\S+)(?P<corpo>.*?)(?=\nNew |\Z)',
                   re.S | re.I)


def _blocos(caminho):
    return BLOCO.finditer(Path(caminho).read_text(encoding='utf-8'))


@pytest.mark.parametrize('nome', ['Linhas_MT.dss', 'Linhas_BT.dss'])
def test_o_linecode_vem_antes_do_phases(nome):
    """A guarda direta: no texto emitido, o código precede a contagem de fases.

    É uma verificação de forma, e de propósito. A consequência do erro é
    indireta demais para servir de alarme — quem o reintroduzir vai ver a suíte
    passar e um alimentador distante reprovar por sobretensão.
    """
    fora = []
    for m in _blocos(ESPERADO / nome):
        corpo = m.group('corpo').lower()
        if 'linecode=' not in corpo or 'phases=' not in corpo:
            continue
        if corpo.index('linecode=') > corpo.index('phases='):
            fora.append(m.group('nome'))
    assert not fora, ('%d trecho(s) com phases antes do LineCode: %s'
                      % (len(fora), ', '.join(fora[:5])))


def test_o_opendss_confirma_que_a_ordem_e_essa(tmp_path):
    """A razão de a regra existir, medida no próprio motor.

    Sem isto o teste acima seria uma preferência de estilo defendida por um
    comentário. Aqui as duas ordens são compiladas lado a lado e a diferença
    aparece: mesma linha monofásica, três fases numa e uma na outra.
    """
    dss = pytest.importorskip(
        'opendssdirect',
        reason='o OpenDSS nao carregou (instalacao incompleta)')

    # Compilado de um arquivo, e não por chamadas soltas: o `~` de
    # continuação é do interpretador de script, e é exatamente na continuação
    # que o caso real põe o LineCode.
    caso = tmp_path / 'ordem.dss'
    caso.write_text('\n'.join([
        'Clear',
        'New Circuit.ordem basekv=0.22 phases=3 bus1=fonte',
        'New LineCode.LC3 nphases=3 R1=0.5 X1=0.3 R0=1.5 X0=1.2 units=km',
        # a ordem que o conversor usava
        'New Line.DEPOIS bus1=fonte.3 bus2=b1.3 phases=1',
        '~ LineCode=LC3 length=0.1 units=km',
        # a ordem que ele passou a usar
        'New Line.ANTES bus1=fonte.3 bus2=b2.3',
        '~ LineCode=LC3 phases=1 length=0.1 units=km',
        # as barras só passam a existir depois de o circuito ser montado
        'Set VoltageBases=[0.22]',
        'CalcVoltageBases',
        'Solve',
        '',
    ]), encoding='utf-8')
    dss.Text.Command('Compile "%s"' % caso.as_posix())

    dss.Circuit.SetActiveElement('Line.DEPOIS')
    assert dss.CktElement.NumPhases() == 3, (
        'se isto falhar, o OpenDSS mudou de comportamento e a regra pode sair')

    dss.Circuit.SetActiveElement('Line.ANTES')
    assert dss.CktElement.NumPhases() == 1

    # E o rastro que o erro deixa na barra: o nó que ninguém alimenta.
    dss.Circuit.SetActiveBus('b1')
    assert dss.Bus.NumNodes() == 2
    dss.Circuit.SetActiveBus('b2')
    assert dss.Bus.NumNodes() == 1
