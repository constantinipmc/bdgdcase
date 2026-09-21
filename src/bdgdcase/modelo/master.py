# -*- coding: utf-8 -*-
"""O Master.dss: o que amarra o caso e o torna resolvível.

Arquivo pequeno e desproporcionalmente importante. Ele define a fonte, chama os
demais na ordem certa, e declara as bases de tensão.

**`Set VoltageBases` é onde casos silenciosamente erram.** O OpenDSS reporta
tensão em pu contra a base mais próxima da que ele calculou a vazio. Falta uma
base, e uma barra de 220 V passa a ser medida contra a de 127 V: o relatório
mostra 1,73 pu e ninguém sabe se é a rede ou o cadastro. **Sobra** uma base, e
o erro é o oposto e mais discreto: com 0,240 e 0,220 na lista, a 9% uma da
outra, um center-tap de 127 V que a vazio está a 133,5 V — fonte em 1,02 e
mais 2,7% de Ferranti num tronco longo de 34,5 kV — casa com 0,240 e passa a
valer 0,88 pu com a tensão correta. Medido: 13.545 barras de um alimentador,
todas "precárias" no mapa sem nada de errado na rede.

Por isso a lista é `bases_do_caso`: derivada do que o emissor de fato escreveu
para cada secundário, e nada além disso. A lista fixa só entra quando não há
de onde derivar.

**Os jumpers topológicos** entram aqui por decisão: eles não são rede, são
remendo declarado. Ficam desligáveis num interruptor só
(`config.HABILITAR_JUMPERS_TOPOLOGICOS`), e cada um deles vai para o registro
de ajustes — quem lê o caso tem de conseguir distinguir o que a distribuidora
cadastrou do que a conversão costurou.
"""
from __future__ import annotations

import math
import os

from bdgdcase import ajustes as _ajustes
from bdgdcase.modelo import config
from bdgdcase.modelo.config import (
    DEBUG_REGCONTROL_TRACE, FREQUENCIA, TENSAO_BT_KV,)
from bdgdcase.modelo.cadastro import (
    eh_center_tap, sanitizar_bus, segundo_enrolamento_por_trafo,)
from bdgdcase.modelo.topologia import _primeiro_cabo_mt_da_fonte


#: A lista de reserva, usada só quando não há secundário emitido de onde
#: derivar (`bases_do_caso`). São a rede de 380/220 e o MRT de 440/220 da
#: distribuidora com que o conversor foi construído, mais os valores
#: fase-neutro. Era a lista de sempre, e o comentário aqui dizia que uma base
#: sobrando não fazia mal; fazia — 0,240 foi escolhida por 13.545 barras de
#: 220 V num alimentador em que nenhum transformador é de 240.
BASES_TENSAO_FIXAS = (0.440, 0.380, 0.254, 0.240, 0.220, 0.127)

def bases_do_caso(kv_secundario, nos_secundario, tol=0.005):
    """As bases de linha que o `CalcVoltageBases` precisa — e só elas.

    O OpenDSS escolhe a base de cada barra assim: toma a tensão fase-neutro do
    PRIMEIRO nó da barra no solve a vazio, multiplica por √3 e casa com o valor
    mais próximo da lista, que ele lê como tensão de linha. Sempre por √3,
    tenha a barra um nó ou três. A base que faz uma barra valer 1,0 pu é,
    portanto, a tensão de perna emitida × √3 quando o enrolamento é de uma
    perna — center-tap, MRT, monofásico —, e o próprio kV quando o enrolamento
    é trifásico, porque aí o `kv` escrito já é de linha.

    Isso conserta um erro antigo escondido na lista fixa: o 0,440 posto lá
    para o MRT 440/220 é 254 V fase-neutro, e uma perna de 220 V medida contra
    ele daria 0,866 pu. Pela regra, a perna vai a 0,381 e fica em 1,0.

    A lista sai curta de propósito. Cada base a mais é uma que o solve a vazio
    pode escolher por engano quando a rede está 5% acima do nominal — e 5% é
    ordinário: fonte em 1,02 e Ferranti no tronco.

    Devolve as bases em kV de linha, em ordem decrescente, sem repetidas a
    menos de `tol` — 0,3811 (0,22 × √3) e 0,380 são a mesma base, e declarar
    as duas faria barras iguais serem medidas contra bases diferentes. Quando
    as duas aparecem, fica o 0,380: é o nominal de linha como o cadastro o
    escreveu, e o 0,3811 é só a equivalência que a regra do OpenDSS impõe à
    perna. A diferença é de 0,3%, mas escolher a derivada mudaria em 0,3% a
    tensão de toda barra trifásica de casos que já estavam certos.
    """
    if not kv_secundario:
        return []
    nominais, derivadas = [], []
    for barra, kv in kv_secundario.items():
        try:
            kv = float(kv)
        except (TypeError, ValueError):
            continue
        if kv != kv or kv <= 0:
            continue
        nos = [n for n in (nos_secundario or {}).get(barra, ()) if n]
        if len(nos) >= 3:
            nominais.append(kv)
        else:
            derivadas.append(kv * math.sqrt(3))

    achadas = []
    for base in sorted(nominais, reverse=True) + sorted(derivadas, reverse=True):
        base = round(base, 4)
        if not any(abs(j - base) <= tol for j in achadas):
            achadas.append(base)
    return sorted(achadas, reverse=True)


def _bases_extras(untrmt, fixas=BASES_TENSAO_FIXAS, tol=0.005, eqtrmt=None):
    """Bases de tensão que este caso usa e a lista fixa não cobre.

    O `CalcVoltageBases` do OpenDSS casa cada barra com a base mais próxima da
    lista, dividida por √3. Uma base ausente não impede o caso de resolver: a
    barra pega a menos distante e a tensão em pu sai deslocada, o que dá um
    alarme falso de sobretensão em cima de uma tensão correta. Foi o que
    aconteceu com uma perna de center-tap de 115 V, que casou com 0,127 e
    apareceu a 1,59 pu valendo 117 V.

    A entrada é de linha, então a perna de um center-tap entra multiplicada por
    √3 — é o número que faz a barra dela cair em 1,0 pu.
    """
    achadas = []

    def juntar(v):
        """Acrescenta uma base de tensão, se ela já não estiver na lista.

        Com tolerância, e não por igualdade exata: 0,22 e 0,2199 são a mesma
        base, e declarar as duas faria o OpenDSS escolher a mais próxima barra
        a barra — duas barras iguais passariam a ser medidas contra bases
        diferentes.
        """
        if not v or v <= 0 or v != v:
            return
        for j in list(fixas) + achadas:
            if abs(j - v) <= tol:
                return
        achadas.append(round(v, 4))

    if untrmt is None or getattr(untrmt, 'empty', True):
        return []
    _seg_bases = segundo_enrolamento_por_trafo(eqtrmt)
    tem_ten = 'TEN_LIN_SE' in untrmt.columns
    for _, row in untrmt.iterrows():
        try:
            ten = float(row.get('TEN_LIN_SE') if tem_ten else 0)
        except (TypeError, ValueError):
            continue
        if ten != ten or ten <= 0:
            continue
        _cod = sanitizar_bus(str(row.get('COD_ID', '')).strip())
        if eh_center_tap(row.get('TIP_TRAFO'), ten, _seg_bases.get(_cod)):
            juntar(round(ten / 2.0 * math.sqrt(3), 4))
        else:
            juntar(round(ten, 4))
    return sorted(achadas, reverse=True)

def gerar_master(bus_fonte, caminho_saida, ssdmt=None, mvasc3=None,
                 mvasc1=None, incluir_jumpers=False, untrmt=None,
                 eqtrmt=None, kv_secundario=None, nos_secundario=None):
    """Cria o Master.dss que orquestra todos os arquivos.

    `kv_secundario` e `nos_secundario` são o que `gerar_transformadores`
    emitiu, e deles sai o `Set VoltageBases` — ver `bases_do_caso`. Sem eles,
    vale a lista de reserva mais o que `_bases_extras` acha no cadastro.
    """
    primeiro_cabo = _primeiro_cabo_mt_da_fonte(ssdmt, bus_fonte) if ssdmt is not None else None
    if primeiro_cabo:
        linha_medidor = f"New EnergyMeter.MED_CABECEIRA element=Line.MT_{primeiro_cabo} terminal=1"
    else:
        linha_medidor = "! EnergyMeter: nao foi possivel detectar o primeiro cabo MT da fonte."

    linha_trace = "Set ControlTrace=Yes" if DEBUG_REGCONTROL_TRACE else "! Set ControlTrace=Yes"

    linha_jumpers = "Redirect Jumpers.dss" if incluir_jumpers else "! Redirect Jumpers.dss"

    derivadas = bases_do_caso(kv_secundario, nos_secundario)
    if derivadas:
        lista = [config.TENSAO_MT_KV] + derivadas
        origem = 'derivadas dos secundarios emitidos'
        extras = []
    else:
        lista = [config.TENSAO_MT_KV, TENSAO_BT_KV] + list(BASES_TENSAO_FIXAS)
        origem = 'lista de reserva, sem secundario emitido de onde derivar'
        extras = _bases_extras(untrmt, eqtrmt=eqtrmt)
        lista += extras
    bases = ' '.join('%.3f' % b for b in lista)
    print('  [INFO] Set VoltageBases=[%s] (%s)' % (bases, origem))
    if extras:
        print('  [INFO] Set VoltageBases: %s acrescentada(s) a partir do '
              'cadastro dos transformadores.'
              % ', '.join('%.3f kV' % b for b in extras))
        _ajustes.registrar(
            'tensao', 'Bases de tensão acrescentadas ao caso',
            quantos=len(extras), unidade='bases', efeito='cadastro',
            detalhe='Acrescentadas a partir do cadastro dos transformadores: %s.'
                    % ', '.join('%.3f kV' % b for b in extras),
            porque='O OpenDSS casa cada barra com a base mais próxima da lista. '
                   'Uma base ausente não impede o caso de resolver: a barra '
                   'pega a menos distante e a tensão em pu sai deslocada. Uma '
                   'perna de center-tap de 115 V chegou a aparecer a 1,59 pu '
                   'com a tensão correta.',
            exemplos=['%.3f kV' % b for b in extras])

    conteudo = f"""! ==============================================================
! Master.dss – Alimentador {config.ALIMENTADOR}
! ==============================================================

Clear

! ----- Definicao do Circuito (Fonte) -----
New Circuit.{config.ALIMENTADOR}
~ bus1={bus_fonte}.1.2.3
~ basekv={config.TENSAO_MT_KV:.3f}
~ pu=1.02
~ phases=3
~ frequency={FREQUENCIA}
~ MVAsc3={float(mvasc3):.3f}
~ MVAsc1={float(mvasc1):.3f}

! ----- Elementos de Rede -----
Redirect LineCodes.dss
Redirect Transformadores.dss
Redirect Linhas_MT.dss
Redirect Linhas_BT.dss
Redirect Capacitores.dss
{linha_jumpers}

! ----- Curvas de carga (CRVCRG / TIP_CC) -----
Redirect CurvasDeCarga.dss

! ----- Cargas -----
Redirect Cargas_BT.dss
Redirect Cargas_MT.dss

! ----- Geracao Distribuida -----
Redirect GD.dss

! ----- Medidores -----
{linha_medidor}

! ----- Tensao Base -----
! So as bases que este caso tem ({origem}).
! Base sobrando e escolhida por engano quando a rede esta 5% acima do nominal a vazio.
Set VoltageBases=[{bases}]
CalcVoltageBases

! ----- Coordenadas -----
BusCoords BusCoords.csv

! ----- Configuracoes de solucao -----
! Redes de distribuicao + RegControl: tolerancia 1e-4 costuma exigir >100 it.; 1e-3 e tipico.
! Uma tolerancia mais apertada (1e-5) foi medida e nao adotada: cura um
! alimentador e faz outro deixar de convergir. O criterio do OpenDSS e a variacao
! de tensao entre iteracoes, e uma iteracao que empaca o satisfaz sem resolver;
! por isso `bdgdcase rodar` confere o balanco de potencia a cada passo e recusa o
! passo que nao fecha, em vez de publica-lo.
! MaxControlIter padrao do OpenDSS = 10 (EPRI) — com varios RegControl 1phi e maxtapchange=1, e pouco.
! 300 permite que 9 controles independentes (3 bancos x 3 fases) acomodem transicoes bruscas de GD
! sem precisar do retry com reset de taps (que distorce o pico de potencia medido).
Set MaxControlIter=300
Set MaxIter=300
Set Tolerance=0.001
Set Algorithm=Newton
{linha_trace}
"""
    with open(caminho_saida, 'w', encoding='utf-8', newline='\n') as f:
        f.write(conteudo)
    print(f"  [OK] {os.path.basename(caminho_saida)} (bus fonte: {bus_fonte})")
