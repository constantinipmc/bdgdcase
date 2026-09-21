# -*- coding: utf-8 -*-
"""A ordem em que o caso é construído.

Este módulo não sabe converter nada — ele sabe a ordem. `main` chama os
emissores de `modelo/` na sequência em que um depende do outro, e
`processar_lote` roda `main` uma vez por alimentador e por tipo de dia.

## Por que a ordem não é negociável

Curvas antes de cargas, porque a carga cita o nome do `LoadShape`. Rede antes
de cargas, porque a carga pendura numa barra que tem de existir. Tudo antes do
`Master.dss`, porque as bases de tensão saem do que o caso de fato criou — não
de uma lista escrita à mão.

## O que `processar_lote` reatribui, e por quê

Ele escreve em `config` o alimentador, as pastas, a tensão de MT e o tipo de
dia antes de cada passada. É por isso que esses nomes são lidos como
`config.NOME` em toda parte, e não importados soltos: uma cópia congelada faria
o segundo alimentador do lote sair com a tensão do primeiro, sem erro nenhum na
tela. `_TENSAO_MT_KV_ORIGINAL` guarda o valor de fábrica para que cada passada
comece do mesmo lugar, e não da que veio antes.

O registro de ajustes é limpo a cada passada, pelo mesmo motivo: ele descreve
**este** alimentador neste dia.
"""
from __future__ import annotations

import geopandas as gpd
import os
import pandas as pd

from bdgdcase import ajustes as _ajustes
from bdgdcase.modelo import config
from bdgdcase.modelo.registro import _DIAG, _log_kv, _log_secao
from bdgdcase.modelo.config import (
    AUTO_JUMPERS_APENAS_BT, AUTO_PRIORIZAR_PAC_INI_COM_JUMPERS,
    DEBUG_REGCONTROL_TRACE, FORCAR_CHAVES_FECHADAS,
    COSTURA_GEOMETRICA_APENAS_MT, GANHO_MINIMO_COSTURA,
    HABILITAR_JUMPERS_TOPOLOGICOS, MODOS_REVERSO,
    REGULADOR_REVERSO_PADRAO, REG_BANK_MONOFAIS_INDEPENDENTES,
    REG_CONTROL_PT_PHASE, REG_KVA_MULTIPLICADOR, REG_KVA_PADRAO, REG_MODE,
    REG_REV_BAND, REG_REV_DELAY_S, REG_REV_THRESHOLD_KW, REG_TAP_DELAY_S,)
from bdgdcase.modelo.cadastro import (
    CONSUMO_MES_PICO_KWH_POR_KW, CONSUMO_MES_PICO_KWH_POR_KW_MT,
    carregar_gpkg_ou_csv, corrigir_car_inst, juntar_eqre, obter_dados_ctmt,
    obter_mvasc_fonte,)
from bdgdcase.modelo.cadastro_csv import escrever_cadastro
from bdgdcase.modelo.cargas import gerar_cargas_bt, gerar_cargas_mt
from bdgdcase.modelo.coordenadas import gerar_buscoords
from bdgdcase.modelo.curvas import (
    _centroide_alimentador, gerar_curvas_de_carga_dss,)
from bdgdcase.modelo.geracao import gerar_gd
from bdgdcase.modelo.master import gerar_master
from bdgdcase.modelo.rede import (
    ganho_da_costura, gerar_capacitores, gerar_jumpers_topologicos,
    gerar_linecodes,
    gerar_linhas_bt, gerar_linhas_mt, gerar_transformadores,)
from bdgdcase.modelo.topologia import (
    _auditar_conectividade_dss, _buses_da_rede_mt, detectar_bus_fonte,
    diagnostico_topologia, nos_energizados_bt,)


# ==============================================================================
# FLUXO PRINCIPAL
# ==============================================================================

def main():
    """Converte UM alimentador, num tipo de dia, na ordem em que dá certo.

    Lê as tabelas de `config.PASTA_OUTPUT` e escreve o caso em
    `config.PASTA_OPENDSS`. A ordem das chamadas não é estilo: curvas antes de
    cargas, porque a carga cita o nome do `LoadShape`; rede antes de cargas,
    porque a carga pendura numa barra que precisa existir; o `Master.dss` por
    último, porque as bases de tensão saem do que o caso de fato criou.

    Quem escolhe alimentador e dia é `processar_lote`, que escreve em `config`
    antes de chamar esta função.
    """
    print("=" * 60)
    print(f"  bdgdcase converter – Alimentador {config.ALIMENTADOR}  |  Tipo de dia: {config.TIP_DIA_CURVA_CRVCRG}")
    print("=" * 60)
    _log_secao("Configuração da execução")
    _log_kv("Modo UNSEMT", "forçar todas fechadas" if FORCAR_CHAVES_FECHADAS else "usar status BDGD (P_N_OPE)")
    _log_kv("Jumpers topológicos", "habilitado" if HABILITAR_JUMPERS_TOPOLOGICOS else "desabilitado")
    _log_kv("Regulador kVA", f"{REG_KVA_PADRAO:.1f} x {REG_KVA_MULTIPLICADOR:.3f} = {(REG_KVA_PADRAO * REG_KVA_MULTIPLICADOR):.1f}")
    rm = str(REG_MODE).strip().lower()
    if rm != 'bdgd_electric':
        rm = 'stable'
    _log_kv("REG_MODE", f"{rm} (stable=leve | bdgd_electric=EQRE+TREL*+pisos)")
    if config.REG_CONTROL_REVERSIBLE:
        _log_kv("RegControl", f"reversible=yes (revThreshold={REG_REV_THRESHOLD_KW:g}kW, revDelay={REG_REV_DELAY_S:g}s)")
    elif config.REG_CONTROL_COGEN:
        _log_kv("RegControl", f"Cogen=yes (revBand={REG_REV_BAND:g}, TapDelay={REG_TAP_DELAY_S:g}s)")
    elif config.REG_CONTROL_REV_NEUTRAL:
        _log_kv("RegControl", f"revNeutral=yes (tap neutro em fluxo reverso, revThreshold={REG_REV_THRESHOLD_KW:g}kW, revDelay={REG_REV_DELAY_S:g}s)")
    else:
        _log_kv("RegControl", "sem modo reverso/cogen")
    _log_kv(
        "Modelo reguladores",
        f"3x1f independentes={'sim' if REG_BANK_MONOFAIS_INDEPENDENTES else 'não'} | PTPhase={str(REG_CONTROL_PT_PHASE).strip().lower() or 'min'}",
    )
    _log_kv("ControlTrace Master", "ligado" if DEBUG_REGCONTROL_TRACE else "desligado")

    # Pasta de saida – sufixo com o tipo de dia para diferenciar as simulacoes
    nome_pasta = f"{config.ALIMENTADOR}_{config.TIP_DIA_CURVA_CRVCRG}"
    pasta_alim = os.path.join(config.PASTA_OPENDSS, nome_pasta)
    os.makedirs(pasta_alim, exist_ok=True)
    _log_kv("Pasta de saída", pasta_alim)

    # ── Carrega dados ──────────────────────────────────────────────────────────
    # O registro comeca aqui, antes da carga: parte dos ajustes acontece ao ler
    # as tabelas, e um `limpar()` posterior apagaria justamente esses.
    _ajustes.limpar(alimentador=config.ALIMENTADOR, dia=config.TIP_DIA_CURVA_CRVCRG)
    _log_secao("[1/3] Carregando dados do BDGD")

    def gpkg(prefixo):
        """A primeira camada `.gpkg` da pasta cujo nome contenha `prefixo`.

        O nome do arquivo carrega o alimentador (`UCBT_tab_TRO05.gpkg`), então
        a busca é por trecho e não por nome exato. Devolve None quando não há —
        camada ausente é caminho previsto, não erro.
        """
        for f in os.listdir(config.PASTA_OUTPUT):
            if prefixo.upper() in f.upper() and f.lower().endswith('.gpkg'):
                return gpd.read_file(os.path.join(config.PASTA_OUTPUT, f))
        return None

    def csv_(prefixo):
        """O mesmo que `gpkg`, para as tabelas que saíram em `.csv`.

        A extração grava em `.gpkg` o que tem geometria e em `.csv` o que não
        tem, e a conversão não quer saber qual foi: pergunta pelos dois.
        """
        for f in os.listdir(config.PASTA_OUTPUT):
            if prefixo.upper() in f.upper() and f.lower().endswith('.csv'):
                return pd.read_csv(os.path.join(config.PASTA_OUTPUT, f), low_memory=False)
        return None

    def tabela(prefixo, primeiro=gpkg):
        """A tabela, no formato em que ela estiver.

        Cada camada era lida num formato só — umas em `.gpkg`, outras em `.csv`
        —, conforme o que a distribuidora de origem exportava. Uma base que
        traga a mesma tabela no outro formato perdia a camada inteira **em
        silêncio**: sem exceção, sem aviso, e um caso a menos daquilo. Perder o
        SSDBT assim dá um alimentador sem rede de baixa que compila e resolve.

        `primeiro` preserva a preferência que cada tabela já tinha, para a
        pasta que traga os dois formatos continuar dando o mesmo caso de antes.
        """
        segundo = csv_ if primeiro is gpkg else gpkg
        df = primeiro(prefixo)
        return df if df is not None else segundo(prefixo)

    # NOVO: Carrega a tabela do circuito
    ctmt           = tabela('CTMT', csv_)
    tten           = tabela('TTEN', csv_)

    ssdmt          = tabela('SSDMT')
    ssdbt          = tabela('SSDBT')
    ponnot         = tabela('PONNOT')
    untrmt         = tabela('UNTRMT')
    unsemt         = tabela('UNSEMT')
    unremt         = tabela('UNREMT')
    # O catálogo do regulador, e a junção que faltava — ver `juntar_eqre`. Sem
    # ela o modo `bdgd_electric` lia colunas que a linha de `UNREMT` não tem e
    # caía no padrão em todo regulador.
    eqre           = tabela('EQRE', primeiro=csv_)
    unremt         = juntar_eqre(unremt, eqre)
    untrs          = tabela('UNTRS')
    uncrmt         = tabela('UNCRMT')
    ucbt_por_poste = None
    ucmt_por_poste = None

    ramlig         = tabela('RAMLIG')
    ugmt           = tabela('UGMT')
    # Estas vinham do `.csv` primeiro, e continuam vindo: numa pasta que traga
    # os dois formatos, o caso gerado tem de ser o mesmo de antes.
    pip            = tabela('PIP', csv_)
    ugbt           = tabela('UGBT', csv_)
    segcon         = tabela('SEGCON', csv_)
    eqtrmt         = tabela('EQTRMT', csv_)
    crvcrg         = tabela('CRVCRG', csv_)

    # Catálogos DDA para reguladores (UNREMT): potência nominal, relações TP/TC
    tpotaprt = carregar_gpkg_ou_csv(config.PASTA_OUTPUT, 'TPOTAPRT')
    treltp = carregar_gpkg_ou_csv(config.PASTA_OUTPUT, 'TRELTP')
    treltc = carregar_gpkg_ou_csv(config.PASTA_OUTPUT, 'TRELTC')
    tcor = carregar_gpkg_ou_csv(config.PASTA_OUTPUT, 'TCOR')
    for nome_cat, df_cat in (
        ('TPOTAPRT', tpotaprt),
        ('TRELTP', treltp),
        ('TRELTC', treltc),
        ('TCOR', tcor),
    ):
        n = len(df_cat) if df_cat is not None else 0
        if n:
            print(f"  {nome_cat:20s}: {n:>5d} registros (catálogo regulador)")

    # As unidades consumidoras agregadas por poste. O `_TAB` era exigido no
    # nome — é o sufixo que uma distribuidora usa —, e o `.gpkg` também. Aqui
    # basta o nome da camada e a marca `POR_POSTE`, em qualquer dos formatos.
    for f in os.listdir(config.PASTA_OUTPUT):
        fu = f.upper()
        if 'POR_POSTE' not in fu:
            continue
        caminho = os.path.join(config.PASTA_OUTPUT, f)
        if f.lower().endswith('.gpkg'):
            dados = gpd.read_file(caminho)
        elif f.lower().endswith('.csv'):
            dados = pd.read_csv(caminho, low_memory=False)
        else:
            continue
        if fu.startswith('UCBT') or 'UCBT_TAB' in fu:
            ucbt_por_poste = dados
        elif fu.startswith('UCMT') or 'UCMT_TAB' in fu:
            ucmt_por_poste = dados

    # A escala da carga instalada das unidades, aferida uma vez pela energia.
    # Baixa e média são aferidas SEPARADAMENTE, e com referências diferentes:
    # a razão entre energia e carga instalada é 29,5 numa e 88,7 na outra.
    ucbt_por_poste, _n_car_bt = corrigir_car_inst(ucbt_por_poste)
    ucmt_por_poste, _n_car_mt = corrigir_car_inst(
        ucmt_por_poste, CONSUMO_MES_PICO_KWH_POR_KW_MT)
    if _n_car_bt or _n_car_mt:
        print('  [ATENCAO] CAR_INST reposto em %d unidade(s) de baixa e %d de '
              'media.' % (_n_car_bt, _n_car_mt))
        _ajustes.registrar(
            'potencia', 'Carga instalada das unidades reposta na escala',
            quantos=_n_car_bt + _n_car_mt, unidade='unidades consumidoras',
            efeito='cadastro',
            detalhe='Nestas unidades o cadastro traz `CAR_INST × 720` igual ao '
                    'maior mês de energia: o campo é a potência MÉDIA do mês '
                    'de maior consumo, e não a carga instalada. A instalada '
                    'foi estimada dividindo a energia do mês de pico por '
                    '%.1f kWh/kW na baixa e %.1f na média.'
                    % (CONSUMO_MES_PICO_KWH_POR_KW,
                       CONSUMO_MES_PICO_KWH_POR_KW_MT),
            porque='Sem repor, a carga instalada aparece cerca de 24 vezes '
                   'menor do que é — mediana de 0,37 kW por residência. A '
                   'potência da carga no fluxo vem do histórico de energia e '
                   'NÃO muda; o que muda é a ficha, e a carga das unidades sem '
                   'histórico.')
        print('            Nesta base CAR_INST x 720 e o maior mes de energia: '
              'o campo traz a potencia MEDIA')
        print('            do mes de pico, e nao a carga instalada. A instalada '
              'foi ESTIMADA por max(ENE)/%.1f.'
              % CONSUMO_MES_PICO_KWH_POR_KW)
        print('            A potencia da carga no .dss vem do historico de '
              'energia e nao muda; o que muda e o')
        print('            cadastro, e a carga das unidades SEM historico.')

    # Faltando o UCBT por poste o caso sai com ZERO cargas de baixa, compila e
    # resolve. É o modo de falha mais caro que esta função tem: o alimentador
    # parece pronto e não tem carga nenhuma.
    if ucbt_por_poste is None or len(ucbt_por_poste) == 0:
        print('  [ATENCAO] Nenhum arquivo *POR_POSTE* de UCBT em %s.'
              % config.PASTA_OUTPUT)
        print('            O caso vai sair SEM cargas de baixa tensao. '
              'Reextraia o alimentador antes de usar o resultado.')
        _ajustes.registrar(
            'completude', 'Sem o arquivo de unidades por poste',
            quantos=None, efeito='nao_corrigido',
            detalhe='Nenhum arquivo *POR_POSTE* de UCBT foi encontrado na pasta de '
                    'entrada.',
            porque='O caso sai SEM cargas de baixa tensão, compila e resolve. É o '
                   'modo de falha mais caro que a conversão tem: o alimentador '
                   'parece pronto e não tem carga nenhuma. Reextraia antes de '
                   'usar o resultado.')

    for nome, dado in [('CTMT', ctmt), ('SSDMT', ssdmt), ('SSDBT', ssdbt), ('PONNOT', ponnot),
                       ('UNTRMT', untrmt), ('UNSEMT', unsemt)]:
        n = len(dado) if dado is not None else 0
        print(f"  {nome:20s}: {n:>5d} registros")

    # ── Quem publicou esta base, e o que se esperava dela ──────────────────
    #
    # Identificar não muda a conversão: as regras são sobre o dado, e a mesma
    # travessia serve às três bases medidas. Serve para CONFERIR — comparar o
    # que veio com o que está documentado em `bdgdcase.distribuidoras`, e dizer
    # em voz alta o que diverge.
    #
    # É a peça que faltava quando a segunda base entrou. Ela converteu, resolveu
    # e mostrou a baixa a 1,76 pu, e nada no caminho disse "isto aqui não é o
    # que se esperava".
    from bdgdcase.distribuidoras import identificar

    _perfil = identificar(ctmt)
    _ajustes.contexto(distribuidora={'dist': _perfil.dist,
                                     'nome': _perfil.nome})
    print('  %-20s: %s' % ('Distribuidora', _perfil.nome))
    _achados = _perfil.conferir({
        'ctmt': ctmt, 'ssdmt': ssdmt, 'ssdbt': ssdbt, 'ponnot': ponnot,
        'untrmt': untrmt, 'unsemt': unsemt, 'unremt': unremt,
        'uncrmt': uncrmt, 'ramlig': ramlig, 'crvcrg': crvcrg,
        'segcon': segcon, 'eqtrmt': eqtrmt, 'ucbt': ucbt_por_poste,
        'ugbt': ugbt, 'ugmt': ugmt,
    })
    for _a in _achados:
        print('  %s' % _a)

    # Uma camada ausente não é erro — há alimentador sem regulador, sem
    # capacitor, sem geração. Mas tem de sair no log: a diferença entre "esta
    # rede não tem" e "a extração não trouxe" não aparece em lugar nenhum
    # depois, e as duas dão o mesmo `.dss`.
    ausentes = [nome for nome, dado in (
        ('RAMLIG', ramlig), ('SEGCON', segcon), ('EQTRMT', eqtrmt),
        ('CRVCRG', crvcrg), ('UNREMT', unremt), ('UNCRMT', uncrmt),
        ('PIP', pip), ('UGBT', ugbt), ('UGMT', ugmt), ('TTEN', tten),
    ) if dado is None or len(dado) == 0]
    if ausentes:
        print('  [INFO] Camadas ausentes ou vazias nesta extracao: %s'
              % ', '.join(ausentes))
        _ajustes.registrar(
            'completude', 'Camadas ausentes ou vazias nesta extração',
            quantos=len(ausentes), unidade='camadas', efeito='cadastro',
            detalhe='Estas tabelas não vieram, ou vieram sem linhas: %s.'
                    % ', '.join(ausentes),
            porque='Nem toda ausência é defeito — há alimentador sem regulador, '
                   'sem capacitor, sem geração. Fica registrado porque a '
                   'diferença entre "esta rede não tem" e "a extração não '
                   'trouxe" não aparece em lugar nenhum depois, e as duas dão o '
                   'mesmo arquivo.',
            exemplos=list(ausentes))

    diagnostico_topologia(ponnot, ssdmt, ssdbt, untrmt,
                          ucbt_por_poste=ucbt_por_poste)

    # ── Detecta bus fonte e Atualiza Tensão ────────────────────────────────────
    _log_secao("[2/3] Resolvendo dados da Fonte (Subestação)")
    if unsemt is None:
        unsemt = gpd.GeoDataFrame()

    bus_fonte = detectar_bus_fonte(ssdmt, unsemt, ctmt, unremt_df=unremt)
    bus_ctmt_oficial, _, _ = obter_dados_ctmt(ctmt, tten_df=tten)
    habilitar_jumpers_exec = bool(HABILITAR_JUMPERS_TOPOLOGICOS)
    jumpers_incluir_mt = True
    jumpers_incluir_bt = True

    # A topologia desta base esta nos PACs ou na geometria?
    #
    # A Celesc encadeia `PAC_1`/`PAC_2`: a media fecha sozinha, e a costura por
    # coordenada nao tem o que acrescentar. A Copel nao encadeia — a
    # continuidade dela esta nas pontas coincidentes dos trechos —, e sem a
    # costura o alimentador sai partido. Medido num deles: 0,268 da media
    # chegando a fonte, 8.183 cargas orfas.
    #
    # O gatilho antigo olhava outra coisa (a fonte topologica divergir do
    # PAC_INI) e so acertava por acaso. Este olha o sintoma.
    antes_mt, depois_mt = ganho_da_costura(ssdmt, unsemt, unremt, bus_fonte)
    if depois_mt - antes_mt >= GANHO_MINIMO_COSTURA:
        print("  [Auto-topologia] So %.1f%% da media tensao chega a fonte pelo "
              "encadeamento de PAC, e a costura por coordenada leva a %.1f%% — "
              "habilitando-a." % (100.0 * antes_mt, 100.0 * depois_mt))
        habilitar_jumpers_exec = True
        if COSTURA_GEOMETRICA_APENAS_MT:
            # Na baixa a costura encadeia circuitos de transformadores
            # diferentes: medido, 35 vaos de baixa entre um no e a media,
            # com as pontas a 0,650 pu. Ver o comentario em `config`.
            jumpers_incluir_bt = False
        _ajustes.registrar(
            'topologia',
            'Rede costurada pela coordenada, porque o PAC não a encadeia',
            quantos=1, unidade='alimentadores', efeito='simulacao',
            detalhe='Seguindo só `PAC_1`/`PAC_2` dos trechos, chaves e '
                    'reguladores, apenas %.1f%% da média tensão chega à fonte. '
                    'A continuidade desta base está na geometria: as pontas '
                    'dos trechos coincidem no mapa sem repetir o mesmo código '
                    'de PAC; costurando pela coordenada, %.1f%%.'
                    % (100.0 * antes_mt, 100.0 * depois_mt),
            porque='Sem a costura o alimentador sai partido, com a maior parte '
                   'das cargas sem caminho até a subestação — e o caso resolve '
                   'assim mesmo, mostrando só o pedaço vivo. A costura une o '
                   'que está no mesmo ponto do mapa, e cada emenda é uma linha '
                   'de chave em `Jumpers.dss`, visível e desligável.')
    if (
        AUTO_PRIORIZAR_PAC_INI_COM_JUMPERS
        and bus_ctmt_oficial
        and bus_fonte
        and str(bus_ctmt_oficial).lower() != str(bus_fonte).lower()
    ):
        print(
            f"  [Auto-fonte][Ajuste] Mantendo PAC_INI oficial ({bus_ctmt_oficial}) "
            f"em vez do fallback ({bus_fonte}) e habilitando Jumpers.dss."
        )
        bus_fonte = bus_ctmt_oficial
        habilitar_jumpers_exec = True
        if AUTO_JUMPERS_APENAS_BT:
            jumpers_incluir_mt = False
            jumpers_incluir_bt = True

    # Atualiza tensão MT com prioridade regulatória (TEN_NOM->TTEN), mantendo fallback seguro.
    _, tensao_lida, origem_tensao = obter_dados_ctmt(ctmt, tten_df=tten)
    if tensao_lida and 4.0 <= tensao_lida <= 200.0:
        config.TENSAO_MT_KV = tensao_lida
        print(f"  [Auto-tensão] Tensão MT atualizada para {config.TENSAO_MT_KV:.3f} kV (origem={origem_tensao})")
    else:
        print(f"  [Aviso] Sem tensão válida no BDGD para CTMT (origem={origem_tensao}). Mantendo padrão: {config.TENSAO_MT_KV:.3f} kV")

    mvasc3, mvasc1, origem_sc = obter_mvasc_fonte(config.ALIMENTADOR, untrs_df=untrs)
    print(f"  [Auto-curto] Fonte configurada: MVAsc3={mvasc3:.3f} MVAsc1={mvasc1:.3f} (origem={origem_sc})")

    # ── Gera arquivos DSS ──────────────────────────────────────────────────────
    _log_secao("[3/3] Gerando arquivos OpenDSS")

    def out(nome):
        """O caminho de um arquivo do caso, dentro da pasta deste alimentador."""
        return os.path.join(pasta_alim, nome)

    # O cadastro é colhido durante a geração, e escrito no fim. Cada lista
    # recebe uma linha por elemento REALMENTE emitido — é o que impede o
    # arquivo de descrever um caso diferente do que está nos `.dss`.
    cad_cargas, cad_trafos, cad_rede, cad_gd, cad_cond = [], [], [], [], []

    gerar_linecodes(segcon, ssdmt, ssdbt, ramlig, out('LineCodes.dss'),
                    cadastro=cad_cond)
    nos_secundario = {}
    kv_secundario = {}
    gerar_transformadores(untrmt, eqtrmt, ssdmt, ssdbt,
                          out('Transformadores.dss'), cadastro=cad_trafos,
                          nos_secundario=nos_secundario,
                          kv_secundario=kv_secundario)
    gerar_linhas_mt(
        ssdmt,
        unsemt,
        unremt,
        bus_fonte,
        out('Linhas_MT.dss'),
        tpotaprt=tpotaprt,
        treltp=treltp,
        treltc=treltc,
        tcor=tcor,
        cadastro=cad_rede,
    )
    nos_bt = {}
    ligacoes_bt = {}
    nos_por_ligacao_bt = {}
    gerar_linhas_bt(ssdbt, ramlig, out('Linhas_BT.dss'), untrmt=untrmt,
                    cadastro=cad_rede, nos_por_barra=nos_bt,
                    ligacoes_por_barra=ligacoes_bt,
                    nos_secundario=nos_secundario,
                    nos_por_ligacao=nos_por_ligacao_bt)
    gerar_capacitores(uncrmt, out('Capacitores.dss'), cadastro=cad_rede,
                      buses_mt=_buses_da_rede_mt(ssdmt))

    lat_auto, lon_auto = _centroide_alimentador(ssdmt, ssdbt, ponnot)
    ctx_curvas = gerar_curvas_de_carga_dss(
        crvcrg, out('CurvasDeCarga.dss'),
        tip_dia=config.TIP_DIA_CURVA_CRVCRG,
        latitude=lat_auto,
        longitude=lon_auto,
    )

    # As barras de baixa que EXISTEM no caso: as que as linhas de baixa
    # tocam e as que os transformadores criam. Carga ou usina resolvida
    # para fora deste conjunto ficaria numa barra morta — e uma usina numa
    # barra morta derruba a solução do alimentador inteiro.
    barras_bt = set(nos_bt) | set(nos_secundario)
    # E os NÓS que têm tensão em cada uma. Não basta uma linha tocar o nó:
    # o center-tap escreve `.1.3`, um ramal cadastrado `.1.2.3` sai dele, e
    # o nó 2 desse ramal está a 0 V. Vale o que tem caminho até um
    # secundário; barra sem caminho nenhum (ilha de baixa, que os jumpers
    # ainda podem religar) fica com o que as linhas escrevem.
    nos_bt_reais = {}
    for _mapa in (nos_bt, nos_secundario):
        for _b, _ns in _mapa.items():
            _atual = nos_bt_reais.setdefault(_b, [])
            for _n in _ns:
                if _n not in _atual:
                    _atual.append(_n)
    for _b in nos_bt_reais:
        nos_bt_reais[_b].sort()
    nos_bt_reais.update(nos_energizados_bt(nos_secundario, nos_por_ligacao_bt))
    gerar_cargas_bt(ucbt_por_poste, ssdbt, ramlig, untrmt, out('Cargas_BT.dss'),
                    ctx_curvas, cadastro=cad_cargas, eqtrmt=eqtrmt,
                    barras_existentes=barras_bt, nos_existentes=nos_bt_reais)
    gerar_cargas_mt(ucmt_por_poste, pip, untrmt, ssdmt, out('Cargas_MT.dss'),
                    ssdbt=ssdbt, ctx_curvas=ctx_curvas, cadastro=cad_cargas,
                    eqtrmt=eqtrmt)
    gerar_gd(ugbt, ugmt, ponnot, ssdmt, ssdbt, ramlig, untrmt,
             out('GD.dss'), cadastro=cad_gd, eqtrmt=eqtrmt,
             barras_existentes=barras_bt, nos_existentes=nos_bt_reais)
    gerar_buscoords(ssdmt, ssdbt, untrmt, out('BusCoords.csv'), ramlig=ramlig,
                    unsemt=unsemt, unremt=unremt)

    # Duas coisas saem deste arquivo, e a evidencia de cada uma e diferente.
    # A cura topologica geral e OPCIONAL: une dois PACs que caem na mesma
    # coordenada, e quem decide se isso e rede ou coincidencia de cadastro e
    # `config.HABILITAR_JUMPERS_TOPOLOGICOS`.
    # A religacao do secundario do transformador NAO tem interruptor. Um
    # transformador que nao alcanca as unidades que a BDGD diz que ele
    # alimenta nao e premissa a escolher: e um elo faltando, e o dado para
    # fecha-lo esta na base. Ela acontece sozinha, e o registro de ajustes
    # conta quantos foram e quais — que e como isso aparece no log da janela.
    # Por isso a chamada e SEMPRE feita, mesmo com o interruptor da cura geral
    # desligado. Sem nenhum jumper o arquivo sai vazio, `n_jumpers` da zero e
    # o Master nao o redireciona: o caso sai igual ao que sairia sem a chamada.
    n_jumpers = gerar_jumpers_topologicos(
        ssdmt,
        ssdbt,
        unsemt,
        unremt,
        untrmt,
        out('Jumpers.dss'),
        incluir_mt=jumpers_incluir_mt and habilitar_jumpers_exec,
        incluir_bt=jumpers_incluir_bt and habilitar_jumpers_exec,
        nos_secundario=nos_secundario,
        kv_secundario=kv_secundario,
        nos_bt_por_barra=nos_bt,
        ligacoes_bt=ligacoes_bt,
        arquivos_emitidos=(out('Linhas_MT.dss'), out('Transformadores.dss'),
                           out('Linhas_BT.dss')),
    )

    gerar_master(
        bus_fonte,
        out('Master.dss'),
        ssdmt=ssdmt,
        mvasc3=mvasc3,
        mvasc1=mvasc1,
        incluir_jumpers=bool(n_jumpers),
        untrmt=untrmt,
        eqtrmt=eqtrmt,
        kv_secundario=kv_secundario,
        nos_secundario=nos_secundario,
    )
    escrever_cadastro(pasta_alim, Cargas=cad_cargas, Trafos=cad_trafos,
                      Rede=cad_rede, GD=cad_gd, Condutores=cad_cond)
    # A auditoria le os `.dss` ja escritos, e por isso vem depois deles — mas
    # ANTES de salvar o registro. Ela estava depois, e o que descobria nao
    # chegava ao arquivo: um alimentador com 366 pontos de carga fora da parte
    # alimentada saia com o registro em silencio sobre isso.
    _auditar_conectividade_dss(pasta_alim, bus_fonte)

    # O registro do que foi ajustado viaja com o caso, e nao so no console: a
    # pasta tem de sair com a explicacao do que ha nela.
    if _ajustes.salvar(pasta_alim):
        print('  [OK] %s (%d ajuste(s) registrado(s))'
              % (_ajustes.NOME_ARQUIVO, len(_ajustes.listar())))

    # ── Cartão de auditoria (resumo) ─────────────────────────────────────────
    _log_secao("Resumo consolidado (auditoria rápida)")
    # Entradas (BDGD extraído)
    _log_kv("SSDMT (entrada)", len(ssdmt) if ssdmt is not None else 0)
    _log_kv("SSDBT (entrada)", len(ssdbt) if ssdbt is not None else 0)
    _log_kv("RAMLIG (entrada)", len(ramlig) if ramlig is not None else 0)
    _log_kv("UNTRMT (entrada)", len(untrmt) if untrmt is not None else 0)
    _log_kv("UNREMT (entrada)", len(unremt) if unremt is not None else 0)
    _log_kv("UNCRMT (entrada)", len(uncrmt) if uncrmt is not None else 0)
    _log_kv("UGBT (entrada)", len(ugbt) if ugbt is not None else 0)
    _log_kv("UGMT (entrada)", len(ugmt) if ugmt is not None else 0)

    # Saídas / descartes relevantes
    _log_kv("SSDBT modelados", _DIAG.get("ssdbt_modelados", "n/d"))
    _log_kv("SSDBT skip FAS=N", _DIAG.get("ssdbt_skip_fas_n", "n/d"))
    _log_kv("UGMT ok", _DIAG.get("ugmt_ok", "n/d"))
    _log_kv("UGMT skip total", _DIAG.get("ugmt_skip_total", "n/d"))
    _log_kv("UGMT skip POT<=0", _DIAG.get("ugmt_skip_pot", "n/d"))
    _log_kv("UGMT skip sem bus", _DIAG.get("ugmt_skip_bus", "n/d"))

    print(f"\n{'='*60}")
    print(f"  Concluido! Tipo de dia: {config.TIP_DIA_CURVA_CRVCRG}")
    print(f"  Arquivos em: {pasta_alim}")
    print("  Abra o Master.dss no OpenDSS, ou: bdgdcase verificar / rodar / mapa")
    print(f"  sobre a pasta {os.path.basename(str(pasta_alim))}.")
    print(f"{'='*60}")

# ==============================================================================
# Lote: os tipos de dia (DU / SA / DO) são processados por pasta.
# ==============================================================================
_DIAS = ['DU', 'SA', 'DO']

_DIAS_DESC = {'DU': 'Dia Util', 'SA': 'Sabado', 'DO': 'Domingo'}

_TENSAO_MT_KV_ORIGINAL = config.TENSAO_MT_KV  # guarda o valor padrao para reset entre alimentadores

def processar_lote(pastas, dias=tuple(_DIAS),
                   regulador_reverso=REGULADOR_REVERSO_PADRAO):
    """Gera os casos OpenDSS de cada pasta de alimentador × tipos de dia.

    ``pastas``: lista de caminhos ``.../Output/{ALIM}`` (saída do Extrator).
    A pasta OpenDSS de destino é derivada como irmã da pasta-mãe de Output.

    ``regulador_reverso``: como os reguladores se comportam quando o fluxo
    inverte — ``'neutro'`` (padrão) manda o tape à posição neutra e o regulador
    para de atuar; ``'cogeracao'`` mantém a regulação a jusante com setpoints
    reversos; ``'bidirecional'`` tenta regular nos dois sentidos, ao custo de
    poder oscilar; ``'direto'`` ignora o sentido do fluxo. Ver
    :data:`MODOS_REVERSO`.

    Retorna a lista de mensagens de erro (vazia = tudo OK).
    """

    if regulador_reverso not in MODOS_REVERSO:
        raise ValueError('modo de fluxo reverso inválido: %r (use %s)'
                         % (regulador_reverso,
                            ', '.join(sorted(MODOS_REVERSO))))
    (config.REG_CONTROL_REVERSIBLE, config.REG_CONTROL_COGEN,
     config.REG_CONTROL_REV_NEUTRAL) = MODOS_REVERSO[regulador_reverso]

    total = len(pastas) * len(dias)
    atual = 0
    erros = []

    for pasta in pastas:
        config.ALIMENTADOR = os.path.basename(pasta)
        config.PASTA_OUTPUT = pasta
        config.PASTA_OPENDSS = os.path.join(os.path.dirname(os.path.dirname(pasta)), "OpenDSS")

        for tip_dia in dias:
            atual += 1
            config.TIP_DIA_CURVA_CRVCRG = tip_dia
            # Reseta tensao ao padrao antes de cada run (cada alimentador pode ter tensao diferente)
            config.TENSAO_MT_KV = _TENSAO_MT_KV_ORIGINAL

            print(f"\n{'#' * 60}")
            print(f"# [{atual}/{total}]  Alimentador: {config.ALIMENTADOR}  |  Tipo: {_DIAS_DESC.get(tip_dia, tip_dia)} ({tip_dia})")
            print(f"{'#' * 60}")

            try:
                main()
            except Exception as e:
                msg = f"{config.ALIMENTADOR}/{tip_dia}: {e}"
                erros.append(msg)
                print(f"  [ERRO] {e}")

    print(f"\n{'=' * 60}")
    print(f"  Processamento concluido: {total - len(erros)}/{total} com sucesso.")
    if erros:
        print("  Erros encontrados:")
        for msg in erros:
            print(f"    - {msg}")
    print(f"{'=' * 60}")
    return erros
