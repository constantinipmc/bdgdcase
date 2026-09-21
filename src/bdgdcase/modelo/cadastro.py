# -*- coding: utf-8 -*-
"""Ler a ficha da BDGD sem acreditar nela.

Esta é a etapa que decide se o caso presta: traduzir o que está gravado no
cadastro para o que a rede fisicamente é. As duas coisas divergem com
frequência, e nunca de um jeito que levante exceção.

Quatro famílias de tradução moram aqui.

**Identidade.** `sanitizar_bus`, `_sanitizar_id` e vizinhas põem um `COD_ID` no
formato que o OpenDSS aceita como nome de barra, de forma estável — o mesmo
código tem de dar sempre o mesmo nome, ou o caso deixa de se ligar a si mesmo.

**Ligação e tensão.** `fases_info`, `eh_center_tap`, `kv_bt_para_fases`,
`segundo_enrolamento_por_trafo`. Aqui mora a leitura de `TEN_LIN_SE` — tensão
*entre os condutores do secundário*, que é a de linha num trifásico a quatro
fios, a própria de fase num enrolamento fase-neutro, e a metade num center-tap.
Ler o campo como se fosse sempre a mesma coisa põe 80 mil transformadores em
127 V quando são 220 V, e o erro é **invisível em pu**: 220/219 e 127/127 dão
os dois 1,00. Só em volts ele aparece.

**Potência.** `pot_inst_kw`, `corrigir_car_inst`, `corrigir_car_inst_pip`.
Vários campos de potência da BDGD não trazem potência: trazem a média do mês de
ponta, que é a energia dividida por 720 h. A identidade
`campo × 720 == max(ENE_01..12)` é o que denuncia. A correção é sempre **por
unidade**, nunca por tabela: as tabelas vêm misturadas, e corrigir a tabela
inteira já pôs 1.018 kW num ramal residencial.

**Catálogo de equipamento.** As tabelas DDA do regulador — TPOTAPRT, TRELTP,
TRELTC — e a tradução dos percentuais da BDGD para a convenção do OpenDSS.
"""
from __future__ import annotations

import geopandas as gpd
import math
import os
import pandas as pd

from bdgdcase.gd_tipos import COLS_ENE as _COLS_ENE_GD
from bdgdcase.gd_tipos import classificar_tipo_gd as _classificar_gd
from bdgdcase.gd_tipos import fator_capacidade as _fator_capacidade
from bdgdcase.gd_tipos import fator_capacidade_row as _fator_capacidade_row
from bdgdcase.modelo.registro import _diag_inc
from bdgdcase.modelo.config import (
    FATOR_POTENCIA, IMPEDANCIAS_RAMAL, MVASC1_PADRAO, MVASC3_PADRAO,
    MVASC_DERIVADO_FATOR_MONOFASICO, MVASC_DERIVADO_FATOR_SOBRECORRENTE,
    MVASC_OVERRIDE_POR_ALIMENTADOR, REG_BDGD_DECIMAL_MAX,
    REG_BDGD_EQRE_DECIMO, REG_CTPRIM_MAX,
    REG_CTPRIM_MIN, REG_LOADLOSS_MAX_PCT, REG_NOLOAD_MAX_PCT,
    REG_PTRATIO_MAX, REG_PTRATIO_MIN,
    TENSAO_BT_KV,)


# FUNÇÕES AUXILIARES
# ==============================================================================

def _num(valor, padrao):
    """Um número do cadastro, ou o padrão. Nunca `NaN`, nunca exceção.

    `float(row.get('X', 75) or 75)` parece proteger e não protege: `NaN` é
    verdadeiro, então o `or` não dispara, e o `.dss` recebe literalmente
    `kva=nan`. O OpenDSS aceita, e o alimentador resolve com um transformador
    de potência indefinida.

    Aceita também vírgula decimal, que aparece quando a tabela passa por CSV
    gravado em locale brasileiro.
    """
    if valor is None:
        return padrao
    if isinstance(valor, str):
        valor = valor.strip().replace(',', '.')
        if not valor or valor.lower() in ('nan', 'none', 'null', '-'):
            return padrao
    try:
        x = float(valor)
    except (TypeError, ValueError):
        return padrao
    return x if math.isfinite(x) else padrao

def sanitizar_bus(nome):
    """Sanitiza nome de barramento para uso no OpenDSS.

    Tambem normaliza IDs numericos com sufixo .0 (artefato de float no GDB):
    '12345.0' -> '12345', evitando divergencia entre buses do SSDBT e PN_CON.
    """
    s = str(nome).strip().replace('-', '_').replace(' ', '_').replace('/', '_')
    if s.endswith('.0'):
        base = s[:-2]
        if base.lstrip('-').isdigit():
            s = base
    return s

def _sanitizar_id(val):
    """Limpa IDs para garantir que o cabo do SSDMT bata exatamente com o SEGCON"""
    s = str(val).strip()
    if s.endswith('.0'):
        return s[:-2]
    return s

def sanitizar_bus_bt(nome):
    """Garante que TODOS os barramentos de BT tenham o prefixo 'BT_' para evitar curto com a MT."""
    s = sanitizar_bus(nome)
    if not s or s.lower() in ('nan', 'none'): return s
    if s.upper().startswith('BT_'):
        return s
    return 'BT_' + s

def _bus_bt_valido(bus):
    """Filtra placeholders BT inválidos usados em dados órfãos (ex.: BT_0, BT_D_P2_*)."""
    b = str(bus or '').strip()
    if not b:
        return False
    bu = b.upper()
    if bu in ('BT_0', 'BT_', 'BT_NONE', 'BT_NAN'):
        return False
    if bu.startswith('BT_D_P2_'):
        return False
    return True

def _bus_mt_valido(bus):
    """Filtra placeholders MT inválidos (ex.: 0/nulos)."""
    b = str(bus or '').strip()
    if not b:
        return False
    bu = b.upper()
    if bu in ('0', 'NONE', 'NAN', 'NULL', 'FONTE_DESCONHECIDA'):
        return False
    return True

def fases_info(fas_con):
    """Retorna (nphases, sufixo_bus_MT, sufixo_bus_BT, conn_trafo).

    - sufixo_bus_MT : sufixo para barramentos MT  (sem neutro explicito)
    - sufixo_bus_BT : sufixo para barramentos BT  (com neutro quando aplicavel)
    """
    f = str(fas_con).upper().strip()
    mapping = {
        'ABCN': (3, '.1.2.3', '.1.2.3.0', 'wye'),
        'ABC' : (3, '.1.2.3', '.1.2.3',   'delta'),
        'ABN' : (2, '.1.2',   '.1.2.0',   'wye'),
        'BCN' : (2, '.2.3',   '.2.3.0',   'wye'),
        'ACN' : (2, '.1.3',   '.1.3.0',   'wye'),
        'CAN' : (2, '.1.3',   '.1.3.0',   'wye'),  # C+A+N – mesma topologia de ACN
        'AB'  : (2, '.1.2',   '.1.2',     'delta'),
        'BC'  : (2, '.2.3',   '.2.3',     'delta'),
        'AC'  : (2, '.1.3',   '.1.3',     'delta'),
        'AN'  : (1, '.1',     '.1.0',     'wye'),
        'BN'  : (1, '.2',     '.2.0',     'wye'),
        'CN'  : (1, '.3',     '.3.0',     'wye'),
        'A'   : (1, '.1',     '.1',       'wye'),
        'B'   : (1, '.2',     '.2',       'wye'),
        'C'   : (1, '.3',     '.3',       'wye'),
        'N'   : (1, '.0',     '.0',       'wye'),  # neutro isolado – raro
    }
    if f in mapping:
        return mapping[f]
    # Virar trifásico em silêncio é o pior desfecho: um trecho de uma fase
    # rotulado de um jeito que esta tabela não conhece passa a carregar três, e
    # o alimentador fica com mais rede do que tem sem que nada denuncie. O
    # padrão continua trifásico — é o que não deixa o caso ficar sem trecho —,
    # mas agora sai no diagnóstico.
    _diag_inc('fas_con_desconhecida_%s' % (f or 'vazio'))
    return (3, '.1.2.3', '.1.2.3.0', 'wye')

def regulador_fases_bdgd(fas_con):
    """Fases de um banco UNREMT para reguladores monofásicos independentes (OpenDSS).

    Retorna lista de (rótulo, nó DSS 1–3). Banco trifásico real → três pares;
    monofásico → um par.
    """
    f = str(fas_con).upper().strip()
    seq = {
        'ABCN': [('A', 1), ('B', 2), ('C', 3)],
        'ABC': [('A', 1), ('B', 2), ('C', 3)],
        'ABN': [('A', 1), ('B', 2)],
        'BCN': [('B', 2), ('C', 3)],
        'ACN': [('A', 1), ('C', 3)],
        'CAN': [('A', 1), ('C', 3)],
        'AB': [('A', 1), ('B', 2)],
        'BC': [('B', 2), ('C', 3)],
        'AC': [('A', 1), ('C', 3)],
        'AN': [('A', 1)],
        'BN': [('B', 2)],
        'CN': [('C', 3)],
        'A': [('A', 1)],
        'B': [('B', 2)],
        'C': [('C', 3)],
    }
    if f in seq:
        return list(seq[f])
    _diag_inc('regulador_fas_con_desconhecida_%s' % (f or 'vazio'))
    return [('A', 1), ('B', 2), ('C', 3)]

def tipo_inst_categoria(tip_inst):
    """'RD_SUBT_*' -> 'SUB'; demais -> 'AER'."""
    return 'SUB' if 'SUBT' in str(tip_inst).upper() else 'AER'

def _chave_bdgd_fechada(valor_p_n_ope):
    """Interpreta P_N_OPE da UNSEMT: True=fechada, False=aberta."""
    v = str(valor_p_n_ope or '').strip().upper()
    # Convenção usual do BDGD: F=Fechada, A=Aberta.
    if v in ('F', 'FECHADA', 'CLOSED', '1', 'S', 'SIM', 'Y', 'YES', 'NF'):
        return True
    if v in ('A', 'ABERTA', 'OPEN', '0', 'N', 'NAO', 'NÃO', 'NO'):
        return False
    # Fallback: mantém fechado para não desconectar a rede por valor inesperado.
    return True

def nome_linecode(nivel, nphases, cat, prefixo=''):
    """Ex: 'LT_MT_3F_AER', 'LT_BT_1F_SUB', 'LT_RL_1F_AER'."""
    return f"LT_{prefixo or nivel}_{nphases}F_{cat}"

def _linecode_ramal(nph, cat):
    """Nome do LineCode genérico de um ramal, garantidamente emitido.

    `IMPEDANCIAS_RAMAL` só tem a variante aérea, e `tipo_inst_categoria` pode
    devolver `'SUB'`. Sem esta degradação o trecho sairia citando
    `LT_RL_1F_SUB`, que nunca foi escrito, e o OpenDSS recusaria o arquivo
    inteiro — o alimentador não sai, e a mensagem fala de um nome ausente de
    todo o cadastro.
    """
    if (nph, cat) in IMPEDANCIAS_RAMAL:
        return nome_linecode('RL', nph, cat)
    _diag_inc('ramal_sem_variante_%s' % cat)
    return nome_linecode('RL', nph, 'AER')

def kvar_from_kw(kw, classe='DEFAULT'):
    """A potência reativa que acompanha `kw`, pelo fator de potência da classe.

    A BDGD não publica reativo por unidade consumidora. O fator de potência por
    classe é premissa, mora em `config.FATOR_POTENCIA`, e é o que faz a carga
    do caso ter Q — sem ele a rede inteira ficaria com fator unitário e a queda
    de tensão sairia otimista.
    """
    fp = FATOR_POTENCIA.get(classe, FATOR_POTENCIA['DEFAULT'])
    if isinstance(fp, dict):
        fp = FATOR_POTENCIA['DEFAULT']
    if fp <= 0 or fp >= 1:
        return 0.0
    return round(kw * math.tan(math.acos(fp)), 4)

def segundo_enrolamento_por_trafo(eqtrmt):
    """`{COD_ID do trafo: tem segundo enrolamento no secundário}`.

    `EQTRMT.LIG_FAS_T` nomeia a fase de um SEGUNDO enrolamento de baixa. Quando
    ele está preenchido, o secundário são dois enrolamentos em série com o
    ponto do meio aterrado — um center-tap —, e a tensão declarada é a de ponta
    a ponta: cada perna é metade.

    **É o discriminante, e não o `TIP_TRAFO` nem a tensão.** Medido nas quatro
    bases, e a separação é limpa:

    | base | tipo | quantos | com `LIG_FAS_T` |
    |---|---|---|---|
    | uma | MT | 103.281 | 100% |
    | outra | MT | 221 | 100% |
    | outra | M e MT | 301.317 | 100% |
    | outra | M | 80.693 | **0,01%** |

    A última linha é o ponto: naquela base o `TIP_TRAFO='M'` é enrolamento
    ÚNICO, e noutra o mesmo rótulo é center-tap. Decidir pelo tipo erra numa
    das duas, e decidir pela tensão erra em ambas — 0,44 é center-tap numa base
    e 0,254 é center-tap noutra, com valores que não se parecem.
    """
    fora = {}
    if eqtrmt is None or getattr(eqtrmt, 'empty', True):
        return fora
    cols = getattr(eqtrmt, 'columns', [])
    if 'UNI_TR_MT' not in cols or 'LIG_FAS_T' not in cols:
        return fora
    for _, row in eqtrmt.iterrows():
        cod = sanitizar_bus(str(row.get('UNI_TR_MT', '')).strip())
        if not cod:
            continue
        lig = str(row.get('LIG_FAS_T', '') or '').strip().upper()
        fora[cod] = lig not in ('', '0', 'NAN', 'NONE')
    return fora

def eh_center_tap(tip_trafo, ten_lin_se, tem_segundo=None):
    """O secundário são dois enrolamentos em série, com o meio aterrado?

    A distinção decide se a tensão fase-neutro é metade da declarada ou a
    própria, e 220 contra 440 V é diferença que aparece no resultado. Vale a
    pena existir como função porque a resposta tem de ser a mesma em todos os
    pontos que a perguntam — o emissor do transformador e quem declara a carga
    logo abaixo dele. Foi por eles discordarem que as cargas dos 103 mil MRT de
    uma das bases saíam declaradas a 254 V.

    `tem_segundo` é a resposta do cadastro, vinda de
    `segundo_enrolamento_por_trafo`, e **manda quando existe**. O resto é
    heurística para a extração que não trouxe o `EQTRMT`: `TIP_TRAFO='MT'`
    acerta em todas as bases medidas, e 0,44 kV é o center-tap clássico.

    A heurística sozinha erra em dois lugares conhecidos, e por isso o cadastro
    vem primeiro: numa base o `TIP_TRAFO='M'` é center-tap de 254/127 e a
    heurística o perde; noutra o mesmo rótulo é enrolamento único de 220 V e a
    heurística acertaria por acaso.
    """
    if tem_segundo is not None:
        return bool(tem_segundo)
    tip = str(tip_trafo or '').upper().strip()
    try:
        ten = float(ten_lin_se)
    except (TypeError, ValueError):
        ten = 0.0
    return tip == 'MT' or abs(ten - 0.44) < 0.05

def kv_bt_para_fases(nph, kv=None):
    """kV de referência de carga ou gerador BT, L-N no monofásico e L-L no resto.

    `kv` é o par (linha, fase-neutro) do secundário do transformador que
    alimenta o ponto, vindo de `_kv_bt_por_trafo`, e é o que deve mandar: a
    rede de baixa não tem uma tensão só. Numa distribuidora do Sul convivem
    380/220 e 220/127, e há base em que a segunda é a maioria — 102 mil dos 214
    mil transformadores.

    Vem o par pronto, e não só a tensão de linha, porque a fase-neutro nem
    sempre é a de linha sobre √3: num center-tap 440/220 é a metade. Quem sabe
    disso é o cadastro do transformador, não esta função.

    **No multifásico o valor devolvido é a fase-neutro × √3, e não a tensão de
    linha.** Os dois coincidem na estrela — 220 × √3 = 380 —, e é por isso que
    a diferença passou despercebida. No center-tap não coincidem: as pernas
    estão a 180°, então linha = 2 × fase-neutro, e não √3 ×. O OpenDSS divide
    por √3 de qualquer jeito (`kV` de elemento multifásico em estrela é sempre
    lida como linha-linha), de modo que declarar os 440 V reais punha o nominal
    da carga em 254 V onde a perna tem 220 — 15,5% acima, num modelo ZIP em que
    metade da parcela é impedância constante.

    Sem o par, cai na constante, que é o último recurso e não o padrão. Ignorar
    o cadastro aqui coloca a carga de 220 V numa rede declarada a 380: o caso
    resolve, ninguém reclama, e as barras de baixa aparecem a 1,76 pu.
    """
    linha, fase_neutro = TENSAO_BT_KV, TENSAO_BT_KV / math.sqrt(3)
    if isinstance(kv, (tuple, list)) and len(kv) == 2:
        linha, fase_neutro = kv
    elif kv is not None:
        # Só a tensão de linha: aceito por conveniência de quem chama de fora,
        # e aí não há como saber de center-tap — assume estrela.
        try:
            if float(kv) > 0:
                linha = float(kv)
                fase_neutro = linha / math.sqrt(3)
        except (TypeError, ValueError):
            pass
    if nph == 1:
        return round(fase_neutro, 4)
    # `linha` não serve aqui: ver o parágrafo do multifásico, acima. Na estrela
    # isto devolve o mesmo número de sempre; no center-tap, 0,381 em vez de
    # 0,440.
    return round(fase_neutro * math.sqrt(3), 4)

def parse_lista(valor, tipo=str):
    """Divide string separada por ';' em lista, ignorando valores vazios/nulos."""
    if pd.isna(valor) or str(valor).strip() in ('', 'nan', 'None', '0'):
        return []
    return [tipo(v.strip()) for v in str(valor).split(';')
            if v.strip() not in ('', 'nan', 'None')]

def parse_lista_posicional(valor, n):
    """As `n` posições de uma `LISTA_` do poste, com os vazios preservados.

    `parse_lista` descarta o vazio, e para montar o modelo isso é o certo — uma
    unidade sem potência não vira carga. Aqui é o contrário: a posição É a
    identidade da unidade, e um campo ausente na terceira não pode fazer a
    quarta responder pela terceira.

    `n` vem de fora, e não do próprio texto, porque um poste de UMA unidade com
    o campo vazio junta em `''`, indistinguível de lista ausente. Quem sabe
    quantas unidades há é o `QTD_UC` do poste.
    """
    s = '' if valor is None or (isinstance(valor, float) and pd.isna(valor)) \
        else str(valor)
    if s.strip() in ('nan', 'None'):
        s = ''
    partes = [p.strip() for p in s.split(';')] if s else []
    partes = ['' if p in ('nan', 'None') else p for p in partes]
    if len(partes) < n:
        partes += [''] * (n - len(partes))
    return partes[:n]

def _clas_tar_para_tipo(clas):
    """Mapeia classificação tarifária para tipo de curva: RES, COM ou IND.

    Suporta os formatos do BDGD Celesc V11:
      TIP_CC BT: RES-TipoX -> RES | COM-TipoX -> COM | IND-TipoX -> IND
      TIP_CC MT: MT-TipoX  -> COM (consumidores MT são tipicamente comerciais/industriais)
      CLAS_TAR:  B1/B2 -> RES | B3 -> COM | A* -> IND
    """
    c = str(clas).upper().strip()
    if c in ('', 'NAN', 'NONE', '0'):
        return 'RES'
    # TIP_CC BT format
    if c.startswith('RES'):
        return 'RES'
    if c.startswith('COM'):
        return 'COM'
    if c.startswith('IND'):
        return 'IND'
    # TIP_CC MT format (MT-Tipo3, MT-Tipo8, etc.) → tratar como COM
    if c.startswith('MT-'):
        return 'COM'
    # Rural / iluminação (prefixos BDGD)
    if c.startswith('RUR'):
        return 'RES'
    if c.startswith('SP') or c.startswith('IP'):
        return 'RES'  # legado: mesmo grupo RES para CLAS_TAR; use _tip_cc_para_prefixo_carga para IP
    # CLAS_SUB (PRODIST M10, tabela TCLASUBCLA): dois caracteres, sem o terceiro
    # que os rótulos de TIP_CC trazem. É o resgate para a base que não preenche
    # TIP_CC — a Coopera o deixa em branco nas 29 mil unidades, e sem isto toda
    # carga dela viraria residencial.
    if c.startswith('RE'):
        return 'RES'
    if c.startswith('CO'):
        return 'COM'
    if c.startswith('IN'):
        return 'IND'
    if c.startswith('RU'):
        return 'RES'
    if c.startswith('PP'):
        return 'COM'
    # CLAS_TAR / GRU_TAR
    if c.startswith('B1') or c.startswith('B2') or 'RESI' in c:
        return 'RES'
    if c.startswith('B3') or 'SERV' in c or 'TERC' in c:
        return 'COM'
    if c.startswith('A') or 'INDU' in c:
        return 'IND'
    # Um aviso por carga é ruído; o total por alimentador é informação.
    _diag_inc('classe_nao_reconhecida')
    return 'RES'   # padrao conservador

def sanitizar_cod_loadshape(cod_id):
    """Nome de elemento OpenDSS para Loadshape (TIP_CC / COD_ID da CRVCRG)."""
    s = str(cod_id).strip()
    if s.endswith('.0') and s[:-2].lstrip('-').isdigit():
        s = s[:-2]
    s = s.replace('-', '_').replace(' ', '_').replace('/', '_')
    return s

#: Horas num mês comercial de 30 dias. Aparece aqui porque é o divisor que
#: denuncia o `POT_INST` derivado — ver `pot_inst_kw`.
HORAS_MES = 720.0

#: kWh injetados no mês de maior geração, por kW instalado. Medido em 121.092
#: unidades de duas distribuidoras cujo `POT_INST` é potência de verdade:
#: mediana 102,6.
#:
#: **Não é o que se usa para reconstruir** — ver `RENDIMENTO_ANO_KWH_POR_KW`.
#: Fica porque é a constante que dá sentido à identidade `POT_INST × 720`: 720
#: horas seriam o mês inteiro a plena carga, e 102,6 é o que uma instalação de
#: verdade injeta no melhor mês.
RENDIMENTO_MES_PICO_KWH_POR_KW = 102.6

#: kWh injetados no ANO por kW instalado: mediana 725, nas mesmas duas bases.
#:
#: É por este que a reconstrução passa, e a razão é o mês zerado. Um quinto das
#: unidades tem três ou mais meses em zero — leitura bimestral ou trimestral,
#: que fatura num mês a energia de dois ou três. O máximo mensal daquelas
#: unidades não é energia de um mês, é acumulado, e reconstruir a partir dele
#: infla a potência na mesma proporção.
#:
#: Medimos um caso: doze meses de `[0, 12286, 22114, 0, 9354, …]`, um zero a
#: cada três. O máximo de 22.114 dava 215 kW de potência instalada — num
#: transformador de 75 kVA, e a rede de baixa dele ia a 1,50 pu. A soma do ano,
#: 103.280 kWh, dá 142. A soma não se importa com o mês em que a leitura caiu.
#:
#: A dispersão continua grande (p25 em 412, p75 em 931) e não é ruído: as
#: colunas `ENE_` são energia INJETADA, não gerada, e quanto a unidade injeta
#: depende de quanto ela consome no próprio local. O que se faz com este número
#: é uma ESTIMATIVA, e está declarado como tal em todo lugar que a usa.
RENDIMENTO_ANO_KWH_POR_KW = 725.0

def pot_inst_kw(row):
    """Potência instalada em kW, e como se chegou nela.

    Devolve `(kw, origem)`, com `origem` em `'cadastro'`, `'estimada'`,
    `'teto'` ou `'teto_sem_energia'` — ver as duas seções finais.

    O campo `POT_INST` deveria trazer potência instalada, e em duas das três
    bases medidas traz. Na terceira, não: ali `POT_INST × 720` é **exatamente**
    o maior dos doze valores mensais de energia — em 96,4% das unidades de
    baixa e em parte das de média, com erro mediano de 0,0 kWh. O campo carrega
    a potência MÉDIA do mês de maior geração, e não a instalada.

    O efeito é o que se vê: uma unidade de 5 kW aparece com 0,7. É por volta de
    sete vezes menos, que é o inverso do fator de capacidade — e num estudo de
    hospedagem ou de fluxo reverso isso não é detalhe, é o resultado.

    **A detecção é por linha, e sobre o dado.** A tabela de média de uma das
    bases é mista: valores limpos (112,5; 75; 300 kW) convivem com o artefato
    (7,890278; 5,126389). Perguntar de que distribuidora se trata erraria nas
    duas metades. A identidade `POT_INST × 720 == max(ENE_01..12)`, com
    tolerância de meio kWh, separa as duas — e não deu **nenhum** falso
    positivo nas 124.778 unidades das outras duas bases.

    **A volta é estimativa.** A instalada sai dividindo a energia do ano pelo
    rendimento anual medido nas bases que trazem os dois números
    (`RENDIMENTO_ANO_KWH_POR_KW`) — pelo ano e não pelo mês de pico, porque o
    mês zerado da leitura bimestral corrompe o máximo e não a soma. O valor
    recuperado não é o do cadastro original; é a melhor reconstrução possível
    a partir do que a base publicou. Quem precisar do número exato tem de
    pedi-lo à distribuidora.
    """
    bruto = _num(row.get('POT_INST'), 0.0)
    if bruto <= 0:
        return bruto, 'cadastro'

    energias = []
    for m in range(1, 13):
        v = _num(row.get('ENE_%02d' % m), 0.0)
        if v > 0:
            energias.append(v)
    if not energias:
        # Sem energia não há o que reconstruir — mas o crivo do teto continua
        # valendo, e uma "geração distribuída" de vários MW que não gerou nada
        # no ano é justamente o que não pode passar calada.
        if bruto > LIMITE_MINIGD_KW:
            _diag_inc('pot_inst_acima_do_teto_sem_energia')
            return bruto, 'teto_sem_energia'
        return bruto, 'cadastro'

    # A DETECÇÃO é pelo mês de pico, porque é dele que a identidade nasce: foi
    # `max(ENE)/720` que a distribuidora escreveu no campo.
    pico = max(energias)

    # A RECONSTRUÇÃO é pelo ano, que o mês zerado não corrompe. Ver o comentário
    # de `RENDIMENTO_ANO_KWH_POR_KW`.
    estimada = sum(energias) / RENDIMENTO_ANO_KWH_POR_KW
    if abs(bruto * HORAS_MES - pico) < 0.5:
        _diag_inc('pot_inst_estimada')
        return round(estimada, 4), 'estimada'

    # ACIMA DO TETO LEGAL. Outra assinatura do mesmo campo, e não a mesma: aqui
    # `POT_INST` não é a potência média do mês de pico, é a própria energia —
    # na unidade que motivou a regra, `POT_INST` = 8.661 é o `ENE_12` da linha,
    # dígito por dígito.
    #
    # A identidade `POT_INST == max(ENE)` **não** serve de crivo: medida nas
    # quatro bases, ela pegou duas unidades, e uma delas era uma usina de 5 kW
    # que gerou 5 kWh no ano — coincidência, não defeito. Tampouco serve a razão
    # entre a potência declarada e a que a energia sustenta: as maiores razões
    # são de usinas que mal geraram, com potência perfeitamente plausível.
    #
    # O que separa é o tamanho, contra a norma: acima de 5 MW não é geração
    # distribuída. Uma unidade em 3.355, e é a relatada.
    if bruto > LIMITE_MINIGD_KW:
        if estimada >= PISO_RECONSTRUCAO_KW:
            _diag_inc('pot_inst_acima_do_teto')
            return round(estimada, 4), 'teto'
        _diag_inc('pot_inst_acima_do_teto_sem_energia')
        return bruto, 'teto_sem_energia'

    return bruto, 'cadastro'

#: Teto da minigeração distribuída, em kW. Lei 14.300/2022, art. 1º: micro até
#: 75 kW, mini acima disso e **até 5 MW**. Acima do teto não é geração
#: distribuída — é central geradora, com outro regime de conexão, e não teria
#: por que estar numa tabela de unidade geradora de consumidor.
#:
#: Serve de crivo, e não de corte: o valor não é aparado para 5 MW, porque um
#: número inventado no lugar de outro número inventado não melhora nada. Ele
#: marca a linha como suspeita, e a potência é então reconstruída da energia —
#: ou mantida e declarada, quando não há energia com que reconstruir.
#:
#: Medido: 1 unidade em 3.355, nas quatro bases extraídas. A que motivou a
#: regra tinha `POT_INST` = 8.661 kW **idêntico** ao `ENE_12` da mesma linha, e
#: sozinha valia nove vezes a carga média do alimentador — bastava ela para o
#: fluxo inteiro ficar reverso.
LIMITE_MINIGD_KW = 5000.0

#: Piso de energia para reconstruir uma potência a partir dela, em kW já
#: reconstruídos. Abaixo disso a usina praticamente não gerou no ano, e a conta
#: devolveria alguns watts para uma instalação que existe: medido, há usinas de
#: 5 kW com 5 kWh no ano inteiro, que a reconstrução poria em 0,007 kW.
PISO_RECONSTRUCAO_KW = 1.0

#: kWh no mês de maior consumo por kW de carga instalada. Medido em 3.889.030
#: unidades de duas distribuidoras cujo `CAR_INST` é carga instalada de verdade:
#: mediana 29,5. A dispersão é enorme — p10 em 9,7 e p90 em 78,5 — porque quanto
#: uma instalação consome do que tem instalado varia com o hábito de quem mora
#: nela. Serve para pôr o campo na escala certa, não para adivinhar a instalação
#: de ninguém.
CONSUMO_MES_PICO_KWH_POR_KW = 29.5

#: O mesmo, para a unidade de MÉDIA: mediana 88,7 em 21.220 unidades. É três
#: vezes o da baixa, e tem de ser medido à parte — quem se liga em média usa uma
#: fração bem maior do que tem instalado. Aplicar o número da baixa à média
#: multiplica a carga por três: numa primeira tentativa desta correção, sete
#: unidades de média de um alimentador saltaram de 136 para 3.317 kW e o caso
#: desabou para 0,582 pu.
CONSUMO_MES_PICO_KWH_POR_KW_MT = 88.7

def corrigir_car_inst(por_poste, referencia=None):
    """Repõe a escala do `CAR_INST` nas unidades em que o campo é derivado.

    Mesmo achado do `POT_INST` da geração, no campo vizinho: numa das bases
    `CAR_INST × 720` é **exatamente** o maior dos doze valores mensais de
    energia — 92,0% das unidades de baixa e 54,6% das de média. O campo traz a
    potência média do mês de maior consumo, e não a carga instalada. A mediana
    dela é 0,37 kW por unidade de baixa, contra 9,00 e 10,47 nas outras duas
    bases.

    **Unidade a unidade, e não pela tabela.** Esta foi a lição cara: a tabela é
    MISTA. Num poste medido convivem `2,09981528` — o artefato — e `25,1` e
    `41,7`, que são carga instalada de verdade. Um fator aplicado à tabela
    inteira multiplicou os 41,7 por 24 e pôs uma carga de 1.018 kW num ramal
    residencial, derrubando o alimentador para 0,58 pu.

    **A conta.** Como o valor derivado **é** `max(ENE)/720`, dividir o maior mês
    pela referência dá a instalada direto. A referência é medida, e é diferente
    por nível de tensão: 29,5 kWh por kW na baixa e 88,7 na média, em 3,9
    milhões e 21 mil unidades de duas bases. Três vezes maior na média porque
    quem se liga ali usa fração bem maior do que tem instalado — aplicar o
    número da baixa à média foi o outro erro do caminho, e custou sete cargas
    de 1.448 kW.

    **O limite honesto.** Sem energia não há como saber se o campo daquela
    unidade é derivado ou real, e ela fica como veio. É justamente onde o
    `CAR_INST` mais importa — a potência da carga no `.dss` vem do histórico de
    energia, e o campo só entra quando não há histórico. Nas demais, o efeito é
    sobre o que se lê no cadastro e no painel, que é onde o número absurdo
    aparece.
    """
    if por_poste is None or not len(por_poste):
        return por_poste, 0
    cols = getattr(por_poste, 'columns', [])
    if 'LISTA_CAR_INST' not in cols:
        return por_poste, 0

    ref = referencia or CONSUMO_MES_PICO_KWH_POR_KW
    df = por_poste.copy()
    listas, somas, corrigidas = [], [], 0

    for _, row in df.iterrows():
        n = _qtd_uc(row)
        car = parse_lista_posicional(row.get('LISTA_CAR_INST', ''), n)
        meses = [parse_lista_posicional(row.get('LISTA_ENE_%02d' % m, ''), n)
                 for m in range(1, 13)]
        fora, soma = [], 0.0
        for i in range(n):
            bruto = _num(car[i], None) if i < len(car) else None
            if bruto is None or bruto <= 0:
                fora.append(car[i] if i < len(car) else '')
                continue
            pico = 0.0
            for col in meses:
                v = _num(col[i], 0.0) if i < len(col) else 0.0
                if v > pico:
                    pico = v
            if pico > 0 and abs(bruto * HORAS_MES - pico) < 0.5:
                bruto = pico / ref
                corrigidas += 1
            fora.append('%.6f' % bruto)
            soma += bruto
        listas.append(';'.join(fora))
        somas.append(soma)

    if not corrigidas:
        return por_poste, 0
    df['LISTA_CAR_INST'] = listas
    if 'SOMA_CAR_INST' in df.columns:
        # Recalculada da lista já corrigida, e não escalada: o poste pode ter
        # unidades corrigidas ao lado de unidades intactas.
        df['SOMA_CAR_INST'] = somas
    return df, corrigidas

#: Horas por mês que uma luminária pública fica acesa. Medido: numa base a razão
#: `max(ENE)/CAR_INST` do PIP é 353,9 do p05 ao p95 — praticamente constante, o
#: que indica energia derivada da potência, e não medida. São ~11,6 h por noite,
#: e é o número contra o qual se afere a escala do campo.
HORAS_IP_MES = 354.0

def corrigir_car_inst_pip(pip):
    """Repõe a potência dos pontos de iluminação quando o campo não a traz.

    Devolve `(tabela, quantos, horas_observadas)`.

    `CAR_INST` do ponto de iluminação deveria ser a potência instalada em kW, e
    a carga de IP do transformador é a soma dela nos pontos que ele alimenta.
    Em duas das quatro bases medidas ele traz outra coisa:

    | base | `CAR_INST` p50 | `max(ENE)/CAR_INST` | o que é |
    |---|---|---|---|
    | uma | 0,103 | 353,9 | potência em kW ✔ |
    | outra | 0,070 | — | potência em kW ✔ |
    | outra | 1,069 | 29,6 | potência dez vezes maior |
    | outra | 32,04 | **1,00** | a energia do mês, em kWh |

    A última linha é o que derrubou a tentativa anterior. Ali `CAR_INST` é
    **exatamente** o maior mês de energia — a razão dá 1,00 —, e uma correção
    por potência de dez daria 32 W onde o `POT_LAMP` da própria base diz 100.

    **A régua é a energia, e é uma só.** Uma luminária pública fica acesa cerca
    de 354 horas por mês, e é isso que separa a potência da energia: se a razão
    `max(ENE)/CAR_INST` está perto de 354, o campo é potência e não se toca; se
    está longe, a potência vem de `max(ENE)/354`.

    Isso substitui o fator de potência de dez que havia aqui. Ele acertava numa
    base por coincidência — o desvio era mesmo de dez — e errava na outra, onde
    o desvio não é de escala e sim de grandeza.

    A aferição é por tabela, e a correção por ponto: a convenção é de quem
    publicou, mas a energia de cada ponto é dele.

    **Quantos pontos bastam depende de quanto eles concordam.** Havia aqui um
    piso de 20 pontos, e ele barrava exatamente os casos em que a resposta é
    mais clara: num alimentador de 5 pontos a razão dava 1,00 nos cinco —
    `CAR_INST` era a energia do mês, sem margem para dúvida —, e o piso deixava
    o valor cru passar. O resultado era iluminação pública a 148 kW em 5
    luminárias, 53% do pico daquele alimentador; a energia publicada diz 47 W
    por ponto.

    O que a amostra pequena não sustenta é uma mediana ruidosa, e não uma
    unânime. Por isso o piso passa a valer só quando a razão se dispersa:
    aferimos quando `p95/p05` cabe num fator de dois — a convenção é a mesma em
    todos os pontos, e mais pontos não acrescentariam nada — ou quando há os 20
    de antes. Medido nos 70 alimentadores extraídos, isso corrige quatro que
    escapavam e não muda nenhum dos demais.
    """
    vazio = (pip, 0, None)
    if pip is None or not len(pip) or 'CAR_INST' not in getattr(pip, 'columns', []):
        return vazio
    col_ene = [c for c in pip.columns if c.startswith('ENE_')]
    if not col_ene:
        return vazio
    car = pd.to_numeric(pip['CAR_INST'], errors='coerce')
    ene = pip[col_ene].apply(pd.to_numeric, errors='coerce').max(axis=1)
    ok = (car > 0) & (ene > 0)
    if not int(ok.sum()):
        return vazio
    razao = ene[ok] / car[ok]
    horas = float(razao.median())

    # Concordam entre si? Então o número de pontos não importa. Ver o parágrafo
    # sobre isto no docstring: o piso de 20 barrava amostras unânimes.
    p05, p95 = float(razao.quantile(0.05)), float(razao.quantile(0.95))
    coerente = p05 > 0 and p95 <= p05 * 2.0
    if not coerente and int(ok.sum()) < 20:
        return vazio

    # A faixa é larga de propósito. O que se quer separar é potência de energia,
    # e entre as duas há duas ordens de grandeza — não vale a pena discutir se a
    # luminária ficou 300 ou 400 horas acesa.
    if not (horas > 0) or 150.0 <= horas <= 700.0:
        return (pip, 0, horas)

    df = pip.copy()
    nova = ene / HORAS_IP_MES
    # Só onde há energia: sem ela não há de onde tirar, e o valor declarado
    # fica como veio.
    df.loc[ok, 'CAR_INST'] = nova[ok]
    return (df, int(ok.sum()), horas)

def _classificar_tipo_gd(row, pot=None):
    """Classifica a GD de uma linha de UGBT/UGMT: 'PV', 'CGH', 'EOL', 'UTE' ou 'NSOL'.

    Delega ao critério único de `bdgdcase.gd_tipos` — prefixo explícito do CEG
    quando existe, senão o fator de capacidade (FC > 25% não pode ser solar).
    Retorna (tipo, fc_medido).

    `pot` é a potência instalada **já corrigida** por `pot_inst_kw`, e passá-la
    não é detalhe: o fator de capacidade é `ΣENE / (potência × 8760)`, de modo
    que uma potência sete vezes menor dá um fator sete vezes maior. Numa base
    em que o `POT_INST` publicado é a média do mês de pico, o fator saía em 60%
    — contra 7 a 9% na base sadia — e **toda** a geração solar era classificada
    como não-solar: 446 de 463 unidades num alimentador, 80 de 88 noutro.

    A consequência não fica no rótulo. A não-solar é modelada com curva
    achatada, gerando as 24 horas do dia, e é essa geração noturna que levanta
    a rede de baixa a 1,13 pu numa madrugada sem carga. 60% dividido por 7,02
    dá 8,5%, que é exatamente a faixa da base sadia.
    """
    if pot is not None and pot > 0:
        fc = _fator_capacidade(pot, [row.get(c, 0) for c in _COLS_ENE_GD])
    else:
        fc = _fator_capacidade_row(row)
    return _classificar_gd(row.get('CEG_GD', ''), fc), fc

def _tip_cc_para_prefixo_carga(clas):
    """Prefixo para nome de Load no DSS (RES/COM/IND/IP) a partir do TIP_CC."""
    c = str(clas).upper().strip()
    if c in ('', 'NAN', 'NONE', '0'):
        return 'RES'
    if c.startswith('IND'):
        return 'IND'
    # AT-2-Tipo*, AT-3-Tipo* (subgrupos A2/A3 ANEEL — alta tensao industrial/grande comercial)
    if c.startswith('AT-') or c.startswith('AT '):
        return 'IND'
    if c.startswith('COM') or c.startswith('MT'):
        return 'COM'
    if c.startswith('SP') or c.startswith('IP'):
        return 'IP'
    if c.startswith('RES') or c.startswith('RUR'):
        return 'RES'
    # Subgrupos A2/A3 da alta tensão: a Celesc os escreve `AT-2-TipoN`, a RGE
    # `A2-TipoN`. Só o primeiro estava coberto.
    if c.startswith('A2') or c.startswith('A3'):
        return 'IND'
    _diag_inc('prefixo_por_clas_sub')
    return _clas_tar_para_tipo(clas)

def _tip_cc_para_classe_fp(clas):
    """Chave em FATOR_POTENCIA para cálculo de kvar (RES/COM/IND/IP)."""
    p = _tip_cc_para_prefixo_carga(clas)
    return 'IP' if p == 'IP' else p

def _tten_cod_para_kv(ten_nom_cod, tten_df):
    """Converte código TEN_NOM (CTMT) para kV usando catálogo TTEN."""
    cod = _norm_cod_bdgd(ten_nom_cod)
    if not cod or tten_df is None or tten_df.empty:
        return None
    mapa = _catalog_cod_map(tten_df)
    row = mapa.get(cod)
    if row is None:
        return None
    ten_v = row.get('TEN', None)
    try:
        ten_v = float(ten_v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(ten_v) or ten_v <= 0:
        return None
    return ten_v / 1000.0

def obter_dados_ctmt(ctmt_df, tten_df=None):
    """Lê a tabela CTMT para extrair barramento fonte e tensão nominal em kV."""
    if ctmt_df is None or ctmt_df.empty:
        return None, None, 'indisponivel'

    # Pega o primeiro (e único) registro do alimentador
    row = ctmt_df.iloc[0]

    # Extrai o PAC_INI (Bus Fonte / Nó de saída da Subestação)
    pac_ini = str(row.get('PAC_INI', '')).strip()
    if pac_ini.endswith('.0'):
        pac_ini = pac_ini[:-2]

    bus_fonte = sanitizar_bus(pac_ini) if pac_ini and pac_ini.lower() != 'nan' else None

    # Prioridade 1: TEN_NOM (código DDA) -> TTEN.TEN (V) -> kV
    kv_tten = _tten_cod_para_kv(row.get('TEN_NOM', None), tten_df)
    if kv_tten:
        return bus_fonte, kv_tten, 'ten_nom_tten'

    # Prioridade 2: legado TEN_OPE numérico (algumas bases trazem em kV ou V)
    tensao = row.get('TEN_OPE', None)
    tensao_mt = float(tensao) if pd.notna(tensao) else None
    if tensao_mt and tensao_mt >= 1000:
        tensao_mt = tensao_mt / 1000.0
    if tensao_mt and 4.0 <= tensao_mt <= 200.0:
        return bus_fonte, tensao_mt, 'ten_ope_direto'

    return bus_fonte, None, 'fallback_padrao'

def _float_seguro(v):
    """Converte para float, devolvendo None no que não é número usável.

    `inf` e `nan` entram entre os inutilizáveis: os dois atravessam `float()`
    sem erro e só explodem lá adiante, dentro do solver, longe da célula de
    cadastro que os produziu.
    """
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x

def obter_mvasc_fonte(alimentador, untrs_df=None):
    """Resolve MVAsc3/MVAsc1 com override manual, derivação BDGD e fallback padrão."""
    al = str(alimentador).strip().upper()
    ov = MVASC_OVERRIDE_POR_ALIMENTADOR.get(al)
    if isinstance(ov, dict):
        m3 = _float_seguro(ov.get('mvasc3'))
        m1 = _float_seguro(ov.get('mvasc1'))
        if m3 and m3 > 0 and m1 and m1 > 0:
            return m3, m1, 'override_manual'

    # Derivação simples por potência instalada na(s) unidade(s) transformadora(s) de subestação.
    if untrs_df is not None and not untrs_df.empty and 'POT_NOM' in untrs_df.columns:
        pot = pd.to_numeric(untrs_df['POT_NOM'], errors='coerce').dropna()
        pot = pot[pot > 0]
        if not pot.empty:
            # O BDGD da UNTRS costuma trazer MVA; tolera kVA por detecção de escala.
            s_total = float(pot.sum())
            s_mva = s_total / 1000.0 if s_total > 10000 else s_total
            m3 = s_mva * MVASC_DERIVADO_FATOR_SOBRECORRENTE
            m1 = m3 * MVASC_DERIVADO_FATOR_MONOFASICO
            if m3 > 0 and m1 > 0:
                return m3, m1, 'derivado_untrs'

    return MVASC3_PADRAO, MVASC1_PADRAO, 'fallback_padrao'

def carregar_gpkg_ou_csv(pasta, prefixo):
    """Tenta carregar arquivo .gpkg ou .csv que contenha 'prefixo' no nome."""
    if not pasta or not os.path.isdir(pasta):
        return None
    for f in os.listdir(pasta):
        if prefixo.upper() in f.upper():
            caminho = os.path.join(pasta, f)
            if f.lower().endswith('.gpkg'):
                return gpd.read_file(caminho)
            elif f.lower().endswith('.csv'):
                return pd.read_csv(caminho, low_memory=False)
    return None

def _norm_cod_bdgd(val):
    """Põe um código de catálogo na forma em que ele casa dos dois lados.

    O mesmo código sai como texto de um arquivo e como número de outro, e `'6'`
    não casa com `6.0`. Sem normalizar, a busca no catálogo não acha, o
    equipamento fica com o valor padrão, e nada avisa.
    """
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ''
    s = str(val).strip()
    if s.lower() in ('nan', 'none', ''):
        return ''
    if s.endswith('.0') and s.replace('.0', '').isdigit():
        s = s[:-2]
    return s

def _catalog_cod_map(df):
    """Índice COD_ID (normalizado) -> linha do catálogo."""
    if df is None or df.empty or 'COD_ID' not in df.columns:
        return {}
    out = {}
    for _, row in df.iterrows():
        k = _norm_cod_bdgd(row.get('COD_ID'))
        if k:
            out[k] = row
    return out

def _eqre_r_xhl_para_percent_opendss(val, default, vmin, vmax):
    """R do UNREMT / catálogo EQRE → % de perdas no OpenDSS.

    Hoje só o R (`%LoadLoss`) passa por aqui. O XHL do regulador é lido ao pé
    da letra por `xhl_regulador`, e o comentário de `REG_XHL_CRIVEL_MIN` diz
    por que a regra do ×10 abaixo foi desfeita para ele.

    - |v| ≥ 1 → já em % (ex.: 2,5 = 2,5 %).
    - 0 < |v| ≤ REG_BDGD_DECIMAL_MAX → fração do % (0,04 → 4 %).
    - REG_BDGD_DECIMAL_MAX < |v| < 1 e REG_BDGD_EQRE_DECIMO → ×10 (0,261 → 2,61 %).
    - Caso contrário (|v| < 1, decimo desligado) → interpretação literal em %.
    """
    if val is None:
        return default
    try:
        v = float(val)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v):
        return default
    av = abs(v)
    if av < 1e-12:
        return default
    if av >= 1.0:
        out = av
    elif av <= REG_BDGD_DECIMAL_MAX:
        out = av * 100.0
    elif av < 1.0 and REG_BDGD_EQRE_DECIMO:
        out = av * 10.0
    else:
        out = av
    out = abs(out)
    if out < vmin:
        out = vmin
    if out > vmax:
        out = vmax
    return out

def _float_cell(row, names, default=None):
    """Lê o primeiro campo existente em `names` com valor numérico válido (case-insensitive)."""
    idx_upper = {str(c).upper(): c for c in row.index}
    for n in names:
        col = idx_upper.get(n.upper())
        if col is None:
            continue
        v = row[col]
        if pd.isna(v):
            continue
        try:
            x = float(v)
            if math.isfinite(x):
                return x
        except (TypeError, ValueError):
            continue
    return default

def _kva_catalogo_tpotaprt(row_cat):
    """kVA nominal a partir de uma linha TPOTAPRT (nomes de coluna variam no DDA)."""
    if row_cat is None:
        return None
    for col in ('POT_NOM', 'POT_KVA', 'KVA', 'S_NOM', 'SNOM'):
        v = _float_cell(row_cat, [col])
        if v is not None and v > 0:
            if v < 5.0:
                v = v * 1000.0
            return max(v, 1.0)
    return None

def _ptratio_de_treltp(row_tp, kv_ln_volts):
    """ptratio do RegControl = V_primário_PT / V_secundário_PT (típico 120 V)."""
    if row_tp is None:
        return None
    # Tensões explícitas no catálogo (V)
    vp = _float_cell(row_tp, ('TEN_PRIM', 'TENS_PRIM', 'U_PRIM', 'TENSAO_PRIM', 'VN_PRIM'))
    vs = _float_cell(row_tp, ('TEN_SEC', 'TENS_SEC', 'U_SEC', 'TENSAO_SEC', 'VN_SEC'))
    if vp and vs and vs > 0:
        pr = vp / vs
        if REG_PTRATIO_MIN <= pr <= REG_PTRATIO_MAX:
            return pr
    # Razão direta (primário/secundário em V ou kV coerentes)
    rel = _float_cell(row_tp, ('REL', 'RAZAO', 'RELACAO', 'KTP', 'RTP'))
    if rel and rel > 1.0:
        if REG_PTRATIO_MIN <= rel <= REG_PTRATIO_MAX:
            return rel
    # Relação nominal em relação ao neutro (kV LN) — alguns cadastros
    kn = _float_cell(row_tp, ('KV_LN', 'KVN', 'TEN_NOM'))
    if kn and kn > 0:
        if kn < 50:
            pr = (kn * 1000.0) / 120.0
            if REG_PTRATIO_MIN <= pr <= REG_PTRATIO_MAX:
                return pr
    return None

def _ctprim_de_trel_tc(row_tc, row_cor):
    """Corrente nominal primária do TC (A) para parâmetro ctprim do RegControl."""
    if row_tc is not None:
        ip = _float_cell(row_tc, ('COR_PRIM', 'I_PRIM', 'I_NOM', 'COR_NOM', 'INOM', 'AMP_PRIM'))
        if ip and ip > 0:
            return min(max(ip, REG_CTPRIM_MIN), REG_CTPRIM_MAX)
        rel = _float_cell(row_tc, ('REL', 'RAZAO', 'RELACAO', 'KTC'))
        if rel and rel > 0:
            return min(max(rel, REG_CTPRIM_MIN), REG_CTPRIM_MAX)
    if row_cor is not None:
        i = _float_cell(row_cor, ('COR_NOM', 'I_NOM', 'INOM', 'AMP', 'CORR'))
        if i and i > 0:
            return min(max(i, REG_CTPRIM_MIN), REG_CTPRIM_MAX)
    return None

def _parametros_trafo_regulador(row_unremt, kva):
    """R/X e perdas a partir do cadastro UNREMT (e fallback seguro)."""
    # Ao pe da letra, e nao pela regra do x10 — ver `xhl_regulador`, que traz a
    # medicao nas quatro bases e o que o x10 custava.
    xhl = xhl_regulador(_float_cell(row_unremt, ('XHL',)), 1.0)

    r_raw = _float_cell(row_unremt, ('R',))
    per_fer = _float_cell(row_unremt, ('PER_FER',))
    per_tot = _float_cell(row_unremt, ('PER_TOT',))

    pct_load = None
    pct_noload = None
    if per_tot is not None and per_fer is not None and kva > 0:
        p_cu = float(per_tot) - float(per_fer)
        if p_cu > 0:
            pct_load = min(p_cu / (kva * 10.0), REG_LOADLOSS_MAX_PCT)
        if per_fer > 0:
            pct_noload = min(float(per_fer) / (kva * 10.0), REG_NOLOAD_MAX_PCT)
    if pct_load is None and r_raw is not None and r_raw > 0:
        # R no mesmo formato que XHL no catálogo EQRE (ver _eqre_r_xhl_para_percent_opendss)
        pct_load = _eqre_r_xhl_para_percent_opendss(
            r_raw, 0.001, 1e-6, REG_LOADLOSS_MAX_PCT
        )
    if pct_load is None:
        pct_load = 0.001
    else:
        pct_load = max(min(float(pct_load), REG_LOADLOSS_MAX_PCT), 1e-6)

    if pct_noload is None:
        pct_noload = 0.001
    else:
        pct_noload = max(min(float(pct_noload), REG_NOLOAD_MAX_PCT), 1e-6)

    pct_imag = max(min(pct_noload, REG_NOLOAD_MAX_PCT), 0.001)

    return xhl, pct_load, pct_noload, pct_imag

#: Faixa em que a potência de um regulador de distribuição é crível, em kVA
#: por fase, já convertida para a de PASSAGEM (ver `juntar_eqre`). Fora dela o
#: número não descreve regulador nenhum, e vale mais o padrão do que o
#: cadastro: um valor absurdo aqui vira impedância absurda, porque `%XHL` e
#: `%LoadLoss` são percentuais **sobre esta base**.
REG_KVA_PASSAGEM_MIN = 50.0
REG_KVA_PASSAGEM_MAX = 50000.0

#: `TPOTAPRT`: o domínio da ANEEL para potência aparente nominal, código → kVA.
#: `POT_NOM` da `EQRE` é um código desta tabela, e não kVA — foi lido como kVA
#: numa primeira versão, e um regulador de 1.500 kVA virou um de 48. A tabela
#: não viaja no `.gdb` (nenhuma das quatro bases medidas a traz), e por isso
#: fica aqui, transcrita de `bdgd2opendss` (P. Radatz, EPRI), que a copia do
#: Dicionário de Dados da BDGD. É monotônica e passa pelos tamanhos padrão
#: (5, 10, 15, 25, 37,5, 45, 75, 112,5 … 1.000, 1.500 … 7.000 kVA), o que a
#: própria forma confirma.
TPOTAPRT_KVA = {
    0: 0.0, 1: 3.0, 2: 5.0, 3: 10.0, 4: 15.0, 5: 20.0, 6: 22.5, 7: 25.0,
    8: 30.0, 9: 35.0, 10: 37.5, 11: 38.1, 12: 40.0, 13: 45.0, 14: 50.0,
    15: 60.0, 16: 75.0, 17: 76.2, 18: 88.0, 19: 100.0, 20: 112.5, 21: 114.3,
    22: 120.0, 23: 138.0, 24: 150.0, 25: 167.0, 26: 175.0, 27: 180.0,
    28: 200.0, 29: 207.0, 30: 225.0, 31: 250.0, 32: 276.0, 33: 288.0,
    34: 300.0, 35: 332.0, 36: 333.0, 37: 400.0, 38: 414.0, 39: 432.0,
    40: 500.0, 41: 509.0, 42: 667.0, 43: 750.0, 44: 833.0, 45: 1000.0,
    46: 1250.0, 47: 1300.0, 48: 1500.0, 49: 1750.0, 50: 2000.0, 51: 2250.0,
    52: 2300.0, 53: 2400.0, 54: 2500.0, 55: 2750.0, 56: 2900.0, 57: 3000.0,
    58: 3125.0, 59: 3300.0, 60: 3750.0, 61: 4000.0, 62: 4200.0, 63: 4500.0,
    64: 5000.0, 65: 6250.0, 66: 6500.0, 67: 7000.0, 68: 7500.0, 69: 7800.0,
    70: 8000.0, 71: 9000.0, 72: 9375.0, 73: 9600.0, 74: 10000.0, 75: 12000.0,
    76: 12500.0, 77: 13300.0, 78: 15000.0, 79: 16000.0, 80: 18000.0,
    81: 18750.0, 82: 20000.0, 83: 25000.0, 84: 26000.0, 85: 26600.0,
    86: 28000.0, 87: 30000.0, 88: 32000.0, 89: 33000.0, 90: 33300.0,
    91: 40000.0, 92: 45000.0, 93: 50000.0, 94: 60000.0, 95: 67000.0,
    96: 75000.0, 97: 80000.0, 98: 83000.0, 99: 85000.0, 100: 90000.0,
    101: 100000.0, 102: 200000.0, 103: 14550000.0, 104: 17320000.0,
    105: 19100000.0, 106: 41550000.0,
}


def kva_de_codigo_tpotaprt(valor):
    """Código `TPOTAPRT` → kVA, ou `NaN` se não for código.

    Inteiro entre 0 e 106 é código; qualquer outra coisa — fração, negativo,
    texto, vazio — não descreve equipamento nenhum e cai no padrão.
    """
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return float('nan')
    if not math.isfinite(v) or v < 0 or v != int(v):
        return float('nan')
    return TPOTAPRT_KVA.get(int(v), float('nan'))

#: Onde a reatância de um regulador de degraus é crível, em % sobre a potência
#: de passagem. Medido nas quatro bases, o `XHL` publicado em `EQRE` fica entre
#: 0,352 e 1,000 — que **já é** essa faixa, lida ao pé da letra.
#:
#: Isto desfaz, para o regulador, a regra do ×10 de
#: `_eqre_r_xhl_para_percent_opendss`: ela nasceu do receio de que 0,26 fosse
#: décimo de por cento e deixasse "o regulador quase ideal", e nunca chegou a
#: ver dado — o elo `UN_RE` não era extraído, e a função recebia sempre `None`.
#: Com o dado na mão, o ×10 põe a Celesc em 9,9%, que é impedância de
#: transformador e não de regulador: medido num alimentador dela, derruba a
#: tensão mínima de 0,843 para 0,753 pu e dobra os passos que não fecham o
#: balanço, de 22 para 45 em 96. A leitura literal dá 0,71% e reproduz a
#: tensão de hoje.
REG_XHL_CRIVEL_MIN = 0.10
REG_XHL_CRIVEL_MAX = 2.00


def xhl_regulador(valor, default=1.0):
    """`XHL` do catálogo `EQRE` → % sobre a potência de passagem, ao pé da letra.

    Aceita o valor publicado quando ele cai na faixa crível de um regulador de
    degraus (`REG_XHL_CRIVEL_MIN`..`MAX`); tenta ×10 e ×100 para o caso de uma
    base publicar em décimos ou em fração; e devolve `default` quando nenhuma
    leitura cabe — que é o "muito fora da realidade" do enunciado. Impedância
    errada aqui não dá erro: dá uma queda de tensão que parece rede ruim.
    """
    try:
        v = abs(float(valor))
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v) or v <= 0:
        return default
    for candidato in (v, v * 10.0, v * 100.0):
        if REG_XHL_CRIVEL_MIN <= candidato <= REG_XHL_CRIVEL_MAX:
            return candidato
    return default


def juntar_eqre(unremt, eqre):
    """Traz para cada regulador os dados elétricos que o catálogo `EQRE` tem.

    `EQRE` é para o regulador o que `EQTRMT` é para o transformador: o
    equipamento, com `POT_NOM`, `TEN_REG`, `R`, `XHL`, `PER_FER`, `PER_TOT`,
    `REL_TP` e `REL_TC`. A ligação é `EQRE.UN_RE` → `UNREMT.COD_ID`.

    Sem isto o conversor escrevia **o mesmo regulador em toda a base** — 333,3
    kVA, `XHL=1%`, `%LoadLoss=0,02%` —, números que não saíram de cadastro
    nenhum, enquanto a BDGD medida trazia 43 a 61 kVA, `XHL` de 0,352 a 0,592
    e `R` de 0,069 a 0,097. O modo `bdgd_electric` já existia e já sabia ler
    essas colunas; faltava alguém pô-las na linha.

    **`POT_NOM` da `EQRE` é um código, não kVA.** Os valores das quatro bases
    — 33, 43, 48, 51, 55, 61, 62, 65, 67 — são inteiros num intervalo estreito,
    e são posições da tabela `TPOTAPRT` da ANEEL: 288, 750, 1.500, 2.250,
    2.750, 4.000, 4.200, 6.250 e 7.000 kVA. Uma primeira versão os leu como kVA
    e ainda inventou uma "potência de passagem" dividindo por uma faixa de
    regulação: um regulador de 1.500 kVA virou um de 480. O erro foi achado
    comparando com `bdgd2opendss`, que passa o campo por `convert_tpotaprt`.
    O que fica é o kVA decodificado, que é a potência de passagem do banco.

    `TEN_REG` **não** entra na potência. Medido nas quatro bases, vale 1,000 em
    duas delas, 1,035 numa e 1,100 na outra, constante dentro de cada uma — é
    o valor de tensão que o regulador sustenta, e é assim que ele entra aqui
    também: `vreg = TEN_REG × 120`, na base de 120 V do `RegControl`, em
    `rede.py`.

    Um regulador pode ter várias linhas em `EQRE` — o banco tem uma por fase, e
    há reserva. Fica a **mediana** de cada grandeza: é o valor do banco, e não
    se deixa uma linha destoante mandar sozinha.

    Devolve uma cópia de `unremt` com as colunas acrescentadas, e o próprio
    `unremt` intacto quando não há o que juntar — base sem `EQRE`, sem `UN_RE`,
    ou sem nenhuma linha que case. O recuo é o comportamento de antes, e não
    uma falha.
    """
    if unremt is None or getattr(unremt, 'empty', True):
        return unremt
    if eqre is None or getattr(eqre, 'empty', True):
        return unremt
    if 'COD_ID' not in unremt.columns:
        return unremt
    col_un = next((c for c in eqre.columns if str(c).upper() == 'UN_RE'), None)
    if col_un is None:
        return unremt

    campos = [c for c in ('POT_NOM', 'TEN_REG', 'R', 'XHL', 'PER_FER',
                          'PER_TOT', 'REL_TP', 'REL_TC', 'COR_NOM')
              if c in eqre.columns]
    if not campos:
        return unremt

    tab = eqre[[col_un] + campos].copy()
    tab[col_un] = tab[col_un].astype(str).str.strip()
    for c in campos:
        tab[c] = pd.to_numeric(tab[c], errors='coerce')
    agrupado = tab.groupby(col_un).median(numeric_only=True)
    if agrupado.empty:
        return unremt

    saida = unremt.copy()
    chave = saida['COD_ID'].astype(str).str.strip()
    achou = 0
    for c in campos:
        valores = chave.map(agrupado[c])
        if c == 'POT_NOM':
            valores = _kva_passagem(valores)
        # A coluna do catálogo só entra onde o cadastro da unidade não a traz:
        # `UNREMT` é a autoridade sobre si mesma, e `EQRE` preenche o que falta.
        if c in saida.columns:
            saida[c] = pd.to_numeric(saida[c], errors='coerce').fillna(valores)
        else:
            saida[c] = valores
        achou = max(achou, int(valores.notna().sum()))
    if not achou:
        return unremt
    return saida


def _kva_passagem(pot_nom):
    """Código `TPOTAPRT` da `EQRE` → kVA do banco. Ver `juntar_eqre`.

    Fora da faixa crível devolve `NaN`, e aí vale o padrão: percentual sobre
    base absurda é impedância absurda.
    """
    passagem = pot_nom.map(kva_de_codigo_tpotaprt)
    return passagem.where((passagem >= REG_KVA_PASSAGEM_MIN)
                          & (passagem <= REG_KVA_PASSAGEM_MAX))


def kva_trafo(pot_nom, padrao):
    """`(kva, origem)` de um transformador de distribuição.

    `origem` é `'cadastro'` quando `POT_NOM` é um número positivo, e
    `'padrao'` quando falta, é zero ou negativo. Zero passa a ser recuo, e
    não valor: um transformador de 0 kVA não tem impedância definível, e o
    OpenDSS responde a isso com NaN no circuito inteiro.
    """
    v = _num(pot_nom, 0.0)
    if v > 0:
        return float(v), 'cadastro'
    return float(padrao), 'padrao'


def _kva_e_cod_pot(row_unremt, map_tpot):
    """Resolve kVA: POT_NOM numérico direto ou código em TPOTAPRT."""
    raw = row_unremt.get('POT_NOM')
    if raw is not None and not (isinstance(raw, float) and pd.isna(raw)):
        try:
            v = float(raw)
            if math.isfinite(v) and v >= 1.0:
                return max(v, 1.0), 'POT_NOM_num'
        except (TypeError, ValueError):
            pass
    cod = _norm_cod_bdgd(raw)
    if cod and map_tpot:
        kva = _kva_catalogo_tpotaprt(map_tpot.get(cod))
        if kva:
            return kva, f'TPOTAPRT[{cod}]'
    return None, None

def _qtd_uc(row):
    """Quantas unidades o poste agrega — a autoridade sobre o tamanho das listas."""
    try:
        n = int(float(row.get('QTD_UC', 0) or 0))
    except (TypeError, ValueError):
        n = 0
    if n:
        return n
    # Extração antiga, sem QTD_UC: as fases nunca faltam, e servem de régua.
    return len(parse_lista(row.get('LISTA_FAS_CON', ''), str)) or 1
