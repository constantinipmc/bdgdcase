# -*- coding: utf-8 -*-
"""A ficha de cada elemento do caso, ao lado do caso.

Para cada elemento gerado sai uma linha num `Cadastro_*.csv` dizendo de onde
ele veio: qual `COD_ID` da BDGD, em que poste, quais unidades consumidoras
foram somadas nele, que premissa entrou no lugar de um campo vazio.

Isso não é documentação — é o que torna o caso **auditável**. Um `.dss` sozinho
diz que a carga da barra X é 12,4 kW; a ficha diz que são sete UCs, nomeia as
sete, e mostra que três delas tiveram a potência corrigida pela regra do fator
720. Sem ela, discordar do número exige refazer a extração inteira.

As colunas `LISTA_*` são posicionais — a n-ésima entrada de cada uma fala da
mesma UC —, e é por isso que valor ausente vira campo vazio em vez de sumir da
lista.
"""
from __future__ import annotations

from bdgdcase.modelo.cadastro import _qtd_uc, parse_lista_posicional


# ═════════════════════════════════════════════════════════════════════════════
# CADASTRO — o que a BDGD sabe e o `.dss` não tem onde guardar
# ═════════════════════════════════════════════════════════════════════════════
#
# Um caso OpenDSS descreve um circuito, não um cadastro. `Load.UCBT_COM_...`
# carrega kW, kV e a curva; não carrega quem é aquela unidade, de que classe,
# quanto consumiu em cada mês, nem de que material é o poste onde ela está. Essa
# informação existe nas tabelas extraídas e morria aqui.
#
# Ela sai em arquivos ao lado dos `.dss`, e não em comentário `!` dentro deles,
# por duas razões: o OpenDSS descarta comentários, de modo que nada disso
# voltaria pela API de quem lê o caso compilado; e mexer no texto dos `.dss`
# quebraria a comparação byte a byte que sustenta o resto dos testes.
#
# Um arquivo por família, e não um só com a união das colunas: uma carga tem
# vinte e nove campos e um trecho de rede tem dezoito, quase todos diferentes.
# Junto, seria uma tabela de cinquenta colunas majoritariamente vazias.
#
# As linhas são coletadas DENTRO dos laços que emitem os `.dss`, com o mesmo
# índice e as mesmas listas. Assim o cadastro nunca afirma mais do que o
# conversor sabia: se houver desalinhamento no cadastro de origem, os dois erram
# junto, e o arquivo continua descrevendo o caso que de fato existe.

#: Aviso na primeira linha de cada arquivo. Sem data: o determinismo do caso é
#: verificado byte a byte, e um carimbo de tempo faria toda execução divergir.
AVISO_CADASTRO = (
    '# Cadastro do caso, extraido da BDGD. Contem dado identificavel quando a '
    'extracao inclui CEP/CNAE.\n'
    '# Revise antes de publicar esta pasta. O numero da conta do cliente '
    '(DESCR) nunca entra aqui.\n'
)

#: Ordem das colunas de cada arquivo. Explícita, e não derivada das chaves dos
#: dicionários, porque a ordem é parte do formato: um arquivo cuja coluna troca
#: de lugar entre execuções não é comparável.
_COLUNAS_CADASTRO = {
    'Cadastro_Cargas.csv': (
        'elemento', 'tipo', 'cod_id', 'poste', 'idx', 'bus', 'fases_ligadas',
        'tip_cc', 'classe', 'curva', 'curva_origem', 'fas_con', 'car_inst_kw',
        'kw_pico', 'kvar_pico', 'kv',
        'clas_sub', 'classe_consumo', 'tip_edificacao', 'gru_tar', 'gru_ten',
        'ten_forn', 'cnae', 'cep', 'are_loc', 'mun', 'uni_tr_mt', 'ceg_gd',
    ) + tuple('ene_%02d' % m for m in range(1, 13)),
    'Cadastro_Trafos.csv': (
        'elemento', 'cod_id', 'pot_nom_kva', 'fases', 'tip_trafo', 'mrt',
        'ten_lin_se_kv', 'tap', 'fas_con_p', 'fas_con_s',
        'per_fer_w', 'per_tot_w', 'banc', 'mun',
        'bus_primario', 'bus_secundario',
    ),
    'Cadastro_Rede.csv': (
        'elemento', 'cod_id', 'familia', 'bus1', 'bus2', 'comp_m',
        'fases', 'fas_con', 'tip_inst', 'tip_cnd', 'linecode',
        'uni_tr_mt', 'p_n_ope', 'ligada_no_caso',
        # capacitor e regulador dividem o arquivo com cabos e chaves: são
        # todos coisas que ficam no poste, e cada um preenche o que lhe cabe
        'pot_kvar', 'pot_kva', 'tip_unid', 'tip_regu', 'ten_reg',
        'rel_tp', 'rel_tc', 'cor_nom', 'banc', 'mun',
    ),
    'Cadastro_Condutores.csv': (
        'elemento', 'cod_id', 'cnom_a', 'cmax_a',
        'bit_fas', 'bit_neu', 'mat_fas', 'mat_neu', 'iso_fas',
        'r1_ohm_km', 'x1_ohm_km',
    ),
    'Cadastro_GD.csv': (
        'elemento', 'cod_id', 'ceg_gd', 'fonte', 'pot_inst_kw', 'pot_origem',
        'fc_pct',
        'fas_con', 'bus', 'kv', 'mun',
    ) + tuple('ene_%02d' % m for m in range(1, 13)),
}

#: campo do arquivo ← coluna `LISTA_` do poste, lida pela posição da unidade.
#:
#: `TIP_CC`, `FAS_CON` e `CAR_INST` não estão aqui de propósito: quem os emite
#: já os tem resolvidos em mão, pelas mesmas regras que decidiram a carga, e
#: relê-los da lista seria arriscar discordar do `.dss` por um detalhe de
#: fallback.
_LISTAS_UC = (
    ('cod_id', 'UC_COD_IDS'),
    ('clas_sub', 'LISTA_CLAS_SUB'),
    ('classe_consumo', 'LISTA_CLASSE_CONSUMO'),
    ('tip_edificacao', 'LISTA_TIP_EDIFICACAO'),
    ('gru_tar', 'LISTA_GRU_TAR'),
    ('gru_ten', 'LISTA_GRU_TEN'),
    ('ten_forn', 'LISTA_TEN_FORN'),
    ('cnae', 'LISTA_CNAE'),
    ('cep', 'LISTA_CEP'),
    ('are_loc', 'LISTA_ARE_LOC'),
    ('mun', 'LISTA_MUN'),
    ('ceg_gd', 'LISTA_CEG_GD'),
) + tuple(('ene_%02d' % m, 'LISTA_ENE_%02d' % m) for m in range(1, 13))

def _cadastro_regulador(row, cod, elemento, bus1, bus2, fases, fas_con, kva):
    """Uma linha de cadastro para um enrolamento de regulador."""
    return {
        'elemento': elemento, 'cod_id': cod, 'familia': 'regulador',
        'bus1': bus1, 'bus2': bus2, 'fases': fases, 'fas_con': fas_con,
        'pot_kva': kva,
        'tip_regu': _texto(row.get('TIP_REGU')),
        'ten_reg': _texto(row.get('TEN_REG')),
        'rel_tp': _texto(row.get('REL_TP')),
        'rel_tc': _texto(row.get('REL_TC')),
        'cor_nom': _texto(row.get('COR_NOM')),
        'banc': _texto(row.get('BANC')),
        'mun': _primeiro(row.get('MUN')),
    }

def _primeiro(valor):
    """O primeiro item de um campo de poste que veio concatenado por `;`.

    Colunas que não estão em `CAMPOS_LISTA` são agregadas mesmo assim, e um
    poste com cinco unidades no mesmo município guarda `4218707;4218707;…`.
    Escrever isso numa célula de município seria escrever ruído.
    """
    s = str(valor or '').strip()
    if s.lower() in ('nan', 'none'):
        return ''
    return s.split(';')[0].strip() if ';' in s else s

def _cadastro_da_uc(row, i, **fixos):
    """Uma linha de `Cadastro_Cargas.csv`: a unidade `i` do poste de `row`.

    `i` é o mesmo índice que nomeia a carga no `.dss` (`UCBT_RES_<poste>_<i>`),
    e é por ele que a janela do mapa reencontra esta linha.
    """
    n = _qtd_uc(row)
    linha = {'poste': str(row.get('COD_PONNOT', '') or '').strip(),
             'idx': i,
             'uni_tr_mt': _primeiro(row.get('UNI_TR_MT'))}
    for campo, coluna in _LISTAS_UC:
        # Coluna ausente não vira campo vazio: vira campo que não existe, e o
        # escritor deixa a célula em branco. É o que faz uma extração antiga
        # continuar abrindo, só com menos informação.
        if coluna in row:
            valores = parse_lista_posicional(row.get(coluna), n)
            linha[campo] = valores[i] if i < len(valores) else ''
    if not linha.get('mun'):
        linha['mun'] = _primeiro(row.get('MUN'))
    linha.update(fixos)
    return linha

def _texto(v):
    """Célula de CSV: número com casas fixas, texto sem surpresa, nada de None."""
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'sim' if v else 'nao'
    if isinstance(v, float):
        if v != v:                       # NaN
            return ''
        return '%.4f' % v
    s = str(v).strip()
    return '' if s.lower() in ('nan', 'none') else s

def escrever_cadastro(pasta, **familias):
    """Grava os `Cadastro_*.csv` da pasta do caso.

    `familias` mapeia o nome do arquivo (sem `Cadastro_`/`.csv`) à lista de
    dicionários coletada durante a geração. Família vazia não vira arquivo:
    um alimentador sem GD não precisa de um `Cadastro_GD.csv` com só o
    cabeçalho para alguém abrir e não entender.
    """
    import csv
    import os

    escritos = []
    for chave, linhas in sorted(familias.items()):
        nome = 'Cadastro_%s.csv' % chave
        colunas = _COLUNAS_CADASTRO[nome]
        if not linhas:
            continue
        caminho = os.path.join(pasta, nome)
        with open(caminho, 'w', encoding='utf-8', newline='') as f:
            f.write(AVISO_CADASTRO)
            w = csv.writer(f, lineterminator='\n')
            w.writerow(colunas)
            for linha in linhas:
                w.writerow([_texto(linha.get(c)) for c in colunas])
        escritos.append((nome, len(linhas)))

    if escritos:
        print('  [OK] cadastro: %s'
              % ', '.join('%s (%d)' % (n, q) for n, q in escritos))
    return escritos
