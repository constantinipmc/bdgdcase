# -*- coding: utf-8 -*-
"""As unidades consumidoras somadas por poste.

A rede de baixa tensão da BDGD chega até o poste (`PN_CON`), e as
unidades consumidoras penduram nele. A conversão precisa das duas
coisas: o total por poste, que vira carga, e a identidade de cada UC,
que vira ficha e permite auditar o total. Daí as colunas `LISTA_*`,
posicionais — a n-ésima entrada de cada uma fala da mesma UC.
"""
from __future__ import annotations

import logging
import os

import geopandas as gpd
import pandas as pd

# O mesmo logger do motor: a extração inteira conta a sua
# história num arquivo só (logs/extrator.log).
log = logging.getLogger("ExtratoBDGD")


# Threshold mínimo de UCs residenciais no mesmo PN_CON para classificar como prédio.
PREDIO_UC_THRESHOLD = 4


def classificar_edificacao(df):
    """Gera dois campos por UC: TIP_EDIFICACAO (binário) e CLASSE_CONSUMO (multi).

    CLASSE_CONSUMO ∈ {'residencial', 'comercial', 'industrial', 'rural',
    'publico', 'outro'} — enriquece o banco preservando a classe de consumo da
    PRODIST M10 (TCLASUBCLA) sem perder o detalhe ao binarizar TIP_EDIFICACAO.

    Regra de prédio (TIP_EDIFICACAO='predio'): CLAS_SUB == CO6, OU qualquer UC
    no mesmo PAC tem CO6, OU >= PREDIO_UC_THRESHOLD UCs residenciais
    compartilham o mesmo PAC. Demais UCs → 'residencial'.

    Cluster por PAC (Ponto de Acoplamento Comum): em prédios o PAC é único para
    todas as UCs; casas têm PACs distintos. Geminadas podem compartilhar PAC
    mas raramente atingem o threshold de 4.

    Base documental: PRODIST Módulo 10, Anexo II (TCLASUBCLA). CO6 =
    "Administração condominial: iluminação e instalações de uso comum de
    prédio".
    """
    if 'CLAS_SUB' not in df.columns or 'PAC' not in df.columns:
        return df

    df = df.copy()
    clas = df['CLAS_SUB'].astype(str).str.upper().str.strip()
    pac = df['PAC'].astype(str).str.strip()

    is_co6 = (clas == 'CO6')
    is_res = clas.str.startswith('RE')

    classe = pd.Series('outro', index=df.index)
    classe[is_res] = 'residencial'
    classe[clas.str.startswith('CO')] = 'comercial'
    classe[clas == 'IN'] = 'industrial'
    classe[clas.str.startswith('RU')] = 'rural'
    classe[clas.str.startswith('PP') | clas.str.startswith('SP')] = 'publico'
    df['CLASSE_CONSUMO'] = classe

    tem_co6_por_pac = is_co6.groupby(pac).transform('any')
    qtd_res_por_pac = is_res.groupby(pac).transform('sum')
    eh_predio = is_co6 | tem_co6_por_pac | (is_res & (qtd_res_por_pac >= PREDIO_UC_THRESHOLD))

    tip = pd.Series('residencial', index=df.index)
    tip[eh_predio] = 'predio'
    df['TIP_EDIFICACAO'] = tip
    return df


def _ler_tabela(caminho):
    """Lê um arquivo CSV ou GPKG como DataFrame sem geometria."""
    if caminho.lower().endswith('.gpkg'):
        return gpd.read_file(caminho).drop(columns='geometry', errors='ignore')
    return pd.read_csv(caminho, low_memory=False)

def _normalizar_id(series):
    """Põe uma coluna de identificador no mesmo formato dos dois lados do join.

    O mesmo `COD_ID` sai como texto de um arquivo e como número de outro, e aí
    `'123'` não casa com `123.0`. Sem esta normalização o cruzamento entre UC e
    poste falha em silêncio: ninguém dá erro, só não sobra nada.
    """
    return series.astype(str).str.strip().str.replace(r'\.0$', '', regex=True)

def _agregar_ug_por_polo(ug_files, lookup):
    """Agrega unidades geradoras (UGBT/UGMT) por polo de conexão.

    Retorna DataFrame indexado por COD_PONNOT_GEO com colunas GD_*.
    """
    CAMPOS_POT = ['POT_INST', 'POT_NOM', 'CAR_INST', 'POT_GER']
    dfs = []
    for ug_file in ug_files:
        try:
            df = _ler_tabela(ug_file)
            col_pn = next((c for c in df.columns if c.upper() == 'PN_CON'), None)
            if not col_pn:
                log.warning("[GD] %s sem PN_CON — ignorado", os.path.basename(ug_file))
                continue
            df[col_pn] = _normalizar_id(df[col_pn])
            merged = df.merge(lookup[['COD_PONNOT_GEO']], left_on=col_pn,
                              right_on='COD_PONNOT_GEO', how='inner')
            if not merged.empty:
                dfs.append(merged)
                log.info("[GD] %s | %d unidades geradoras associadas a postes",
                         os.path.basename(ug_file), len(merged))
            else:
                log.warning("[GD] %s | nenhum PN_CON bateu com o lookup de postes", os.path.basename(ug_file))
        except Exception:
            log.exception("[GD] Falha ao processar %s", os.path.basename(ug_file))

    if not dfs:
        return None

    df_all = pd.concat(dfs, ignore_index=True)

    # Log completo dos campos disponíveis nas tabelas UG — útil para
    # identificar o nome real do campo de potência neste BDGD específico
    log.info("[GD] Todos os campos disponíveis nas tabelas UG: %s", list(df_all.columns))

    # Busca exata primeiro; se falhar, aceita qualquer coluna que contenha 'POT'
    col_pot = next((c for c in df_all.columns if c.upper() in CAMPOS_POT), None)
    if col_pot is None:
        col_pot = next((c for c in df_all.columns if 'POT' in c.upper()), None)
        if col_pot:
            log.info("[GD] Campo de potência detectado por heurística ('POT'): %s", col_pot)

    col_tip  = next((c for c in df_all.columns if c.upper() == 'TIP_GER'), None)
    col_fas  = next((c for c in df_all.columns if c.upper() == 'FAS_CON'), None)
    col_ceg  = next((c for c in df_all.columns if c.upper() == 'CEG_GD'), None)
    col_cod  = next((c for c in df_all.columns if c.upper() == 'COD_ID'), None)

    log.info("[GD] Campos mapeados → POT:%s | TIP:%s | FAS:%s | CEG:%s | COD:%s",
             col_pot, col_tip, col_fas, col_ceg, col_cod)
    if col_pot is None:
        log.warning("[GD] nenhum campo de potência encontrado nas tabelas UG; "
                    "a geração sai sem potência. Campos disponíveis: %s",
                    list(df_all.columns))

    agg = {}
    if col_pot:
        df_all[col_pot] = pd.to_numeric(df_all[col_pot], errors='coerce').fillna(0)
        agg['GD_SOMA_POT']   = pd.NamedAgg(column=col_pot, aggfunc='sum')
        agg['GD_LISTA_POT']  = pd.NamedAgg(column=col_pot,
                                            aggfunc=lambda x: ';'.join(x.astype(str)))
    if col_tip:
        agg['GD_LISTA_TIP']  = pd.NamedAgg(column=col_tip,
                                            aggfunc=lambda x: ';'.join(x.dropna().astype(str)))
    if col_fas:
        agg['GD_LISTA_FAS']  = pd.NamedAgg(column=col_fas,
                                            aggfunc=lambda x: ';'.join(x.dropna().astype(str)))
    if col_ceg:
        agg['GD_LISTA_CEG']  = pd.NamedAgg(column=col_ceg,
                                            aggfunc=lambda x: ';'.join(
                                                v for v in x.dropna().astype(str)
                                                if v not in ('', 'nan', 'None', '0')))
    ref_col = col_cod or 'COD_PONNOT_GEO'
    agg['GD_QTD'] = pd.NamedAgg(column=ref_col, aggfunc='count')

    result = df_all.groupby('COD_PONNOT_GEO').agg(**agg)
    log.info("[GD] Agregação GD: %d polos com geração", len(result))
    return result

def agregar_ucs_aos_postes(out_dir, avisar=None, errar=None):
    """Soma as unidades consumidoras e geradoras em cada poste do recorte.

    Lê o que já está em `out_dir` — pontos notáveis, UCs de baixa e média,
    unidades geradoras — e grava ao lado os arquivos `*_POR_POSTE`, com o total
    por poste e as colunas `LISTA_*`.

    As `LISTA_*` são **posicionais**: a n-ésima entrada de todas elas fala da
    mesma unidade consumidora. É o que permite à conversão emitir carga por UC
    e, depois, auditar o total contra o poste. Por isso um valor ausente vira
    string vazia em vez de sumir — perder a posição embaralharia as fichas.

    `avisar` e `errar` recebem `(título, mensagem)`. Sem eles, o rastro fica só
    no log: a agregação roda em processo filho, e é normal ninguém estar
    ouvindo.
    """
    avisar = avisar or (lambda t, m: log.warning("[%s] %s", t, m))
    errar = errar or (lambda t, m: log.error("[%s] %s", t, m))
    ponto_files = []   # PONNOT + PONFAS
    uc_files    = []   # UCBT, UCMT, UCAT
    ug_files    = []   # UGBT, UGMT (geração)

    for f in os.listdir(out_dir):
        f_upper = f.upper()
        ext_ok  = f.lower().endswith('.csv') or f.lower().endswith('.gpkg')
        if ('PONNOT' in f_upper or 'PONFAS' in f_upper) and '_POR_POSTE' not in f_upper and f.lower().endswith('.gpkg'):
            ponto_files.append(os.path.join(out_dir, f))
        elif any(p in f_upper for p in ['UCBT', 'UCMT', 'UCAT']) and '_POR_POSTE' not in f_upper and ext_ok:
            uc_files.append(os.path.join(out_dir, f))
        elif any(p in f_upper for p in ['UGBT', 'UGMT', 'UGAT']) and '_POR_POSTE' not in f_upper and ext_ok:
            ug_files.append(os.path.join(out_dir, f))

    log.info("[AGREGA] Pontos: %s", [os.path.basename(p) for p in ponto_files])
    log.info("[AGREGA] UC: %s",     [os.path.basename(u) for u in uc_files])
    log.info("[AGREGA] UG (GD): %s",[os.path.basename(g) for g in ug_files])

    if not ponto_files or not uc_files:
        log.warning("[AGREGA] Abortando: ponto_files=%d, uc_files=%d", len(ponto_files), len(uc_files))
        return

    arquivos_gerados = 0

    # Campos que precisam de discriminação individual além da soma
    CAMPOS_LISTA = ['CAR_INST', 'ENE_MED', 'FAS_CON', 'CEG_GD', 'CLAS_TAR', 'CNAE',
                    'CLAS_SUB', 'GRU_TAR', 'TIP_EDIFICACAO', 'CLASSE_CONSUMO',
                    # Enquadramento e localização, por unidade: é o que o
                    # painel mostra ao abrir uma UC, e sem estar em LISTA_
                    # só existiria o valor dominante do poste.
                    'TIP_CC', 'GRU_TEN', 'TEN_FORN', 'ARE_LOC', 'MUN', 'CEP',
                    'ENE_01', 'ENE_02', 'ENE_03', 'ENE_04', 'ENE_05', 'ENE_06',
                    'ENE_07', 'ENE_08', 'ENE_09', 'ENE_10', 'ENE_11', 'ENE_12']
    
    # Campos que serão somados matematicamente para formar a carga total do poste
    CAMPOS_SOMA  = ['CAR_INST', 'ENE_MED',
                    'ENE_01', 'ENE_02', 'ENE_03', 'ENE_04', 'ENE_05', 'ENE_06',
                    'ENE_07', 'ENE_08', 'ENE_09', 'ENE_10', 'ENE_11', 'ENE_12']

    try:
        # ── Lookup de postes (PONNOT + PONFAS) ──────────────────────────
        crs_ponto = None
        dfs_ponto = []
        for pf in ponto_files:
            try:
                gdf_p = gpd.read_file(pf)
                col_id = next((c for c in gdf_p.columns if c.upper() == 'COD_ID'), None)
                log.debug("[AGREGA] %s | %d reg. | COD_ID: %s", os.path.basename(pf), len(gdf_p), col_id)
                if not col_id:
                    continue
                if crs_ponto is None:
                    crs_ponto = gdf_p.crs
                sub = gdf_p[[col_id, 'geometry']].copy()
                sub[col_id] = _normalizar_id(sub[col_id])
                sub.rename(columns={col_id: 'COD_PONNOT_GEO'}, inplace=True)
                dfs_ponto.append(sub)
            except Exception:
                log.exception("[AGREGA] Falha ao ler %s", os.path.basename(pf))

        if not dfs_ponto:
            log.error("[AGREGA] Nenhum ponto válido — abortando")
            return

        gdf_lookup = (pd.concat(dfs_ponto)
                       .drop_duplicates(subset=['COD_PONNOT_GEO'])
                       .reset_index(drop=True))
        log.info("[AGREGA] Lookup: %d IDs únicos de postes", len(gdf_lookup))

        # ── Pré-agrega unidades geradoras por polo ───────────────────────
        ug_por_polo = _agregar_ug_por_polo(ug_files, gdf_lookup) if ug_files else None

        # ── Processa cada arquivo UC ──────────────────────────────────────
        for uc_file in uc_files:
            nome_base = os.path.splitext(os.path.basename(uc_file))[0]
            try:
                df_uc = _ler_tabela(uc_file)
            except Exception:
                log.exception("[AGREGA] Falha ao ler %s", nome_base)
                continue
            log.debug("[AGREGA] %s | %d reg.", nome_base, len(df_uc))

            col_pn = next((c for c in df_uc.columns if c.upper() == 'PN_CON'), None)
            if not col_pn:
                log.warning("[AGREGA] %s | sem PN_CON — ignorado. Cols: %s",
                            nome_base, list(df_uc.columns))
                continue

            df_uc[col_pn] = _normalizar_id(df_uc[col_pn])

            pn_set     = set(df_uc[col_pn].dropna().unique())
            lookup_set = set(gdf_lookup['COD_PONNOT_GEO'].unique())
            matches    = pn_set & lookup_set
            log.info("[AGREGA] %s | PN_CON: %d | lookup: %d | matches: %d",
                     nome_base, len(pn_set), len(lookup_set), len(matches))
            if not matches:
                log.warning("[AGREGA] %s | ZERO matches! PN_CON amostra: %s | lookup amostra: %s",
                            nome_base, list(pn_set)[:5], list(lookup_set)[:5])

            merged = df_uc.merge(gdf_lookup, left_on=col_pn,
                                 right_on='COD_PONNOT_GEO', how='inner')
            log.info("[AGREGA] %s | merged: %d linhas", nome_base, len(merged))
            if merged.empty:
                log.error("[AGREGA] %s | merge vazio", nome_base)
                log.warning("[UC] %s: sem casamento PN_CON x COD_ID", nome_base)
                continue

            # Converte campos numéricos
            cols_soma = [c for c in df_uc.columns if c.upper() in CAMPOS_SOMA]
            for c in cols_soma:
                merged[c] = pd.to_numeric(merged[c], errors='coerce').fillna(0)

            # Adiciona colunas LISTA_ (cópia string para preservar individualmente)
            for campo in CAMPOS_LISTA:
                col_real = next((c for c in df_uc.columns if c.upper() == campo), None)
                if col_real:
                    merged[f'LISTA_{campo}'] = merged[col_real].astype(str).str.strip()

            # Funções de agregação
            agg_funcs = {'geometry': 'first'}
            for c in df_uc.columns:
                if c in cols_soma:
                    agg_funcs[c] = 'sum'
                else:
                    agg_funcs[c] = lambda x: ';'.join([str(v) for v in x if pd.notna(v) and str(v) not in ('nan', 'None')])

            # ── As LISTA_ são POSICIONAIS ────────────────────────────────
            #
            # A i-ésima entrada de toda LISTA_ é a i-ésima UC do poste, e o
            # ausente vira campo vazio em vez de sumir. Antes cada coluna
            # descartava os seus nulos por conta própria, e as listas do
            # mesmo poste ficavam com comprimentos diferentes: medido em
            # AGA01, `LISTA_CNAE` divergia da contagem de UCs em 1741 dos
            # 1962 postes. O índice deixava de significar a mesma unidade
            # em cada lista, e ler "a CNAE da terceira UC" devolvia a de
            # outra pessoa.
            #
            # As listas elétricas — FAS_CON, CAR_INST, ENE_* — já vinham
            # completas, e por isso o caso `.dss` não muda: quem as consome
            # usa `parse_lista`, que continua descartando os vazios e
            # produzindo exatamente a mesma sequência de antes.
            def _posicional(vazios=('', 'nan', 'None')):
                """Devolve o agregador que preserva a posição de cada UC."""
                def juntar(x):
                    """Junta com `;`, deixando vazio o que faltou — sem pular."""
                    return ';'.join(
                        '' if (pd.isna(v) or str(v).strip() in vazios)
                        else str(v).strip()
                        for v in x)
                return juntar

            for campo in CAMPOS_LISTA:
                lista_col = f'LISTA_{campo}'
                if lista_col not in merged.columns:
                    continue
                # Em CEG_GD o zero é ausência de geração, e não um código.
                agg_funcs[lista_col] = _posicional(
                    ('', 'nan', 'None', '0', '0.0') if campo == 'CEG_GD'
                    else ('', 'nan', 'None'))

            qtd_uc  = merged.groupby('COD_PONNOT_GEO').size().rename('QTD_UC')
            grouped = merged.groupby('COD_PONNOT_GEO').agg(agg_funcs)
            grouped = grouped.join(qtd_uc).reset_index()

            # Tipo de edificação dominante no poste + contagens por classe
            col_tip = next((c for c in df_uc.columns if c.upper() == 'TIP_EDIFICACAO'), None)
            if col_tip:
                tip_dom = (merged.groupby('COD_PONNOT_GEO')[col_tip]
                           .agg(lambda x: x.mode().iloc[0] if len(x.mode()) else 'outro')
                           .rename('TIP_EDIF_DOMINANTE'))
                qtd_predio = (merged[merged[col_tip] == 'predio']
                              .groupby('COD_PONNOT_GEO').size().rename('QTD_PREDIO'))
                qtd_res = (merged[merged[col_tip] == 'residencial']
                           .groupby('COD_PONNOT_GEO').size().rename('QTD_RESIDENCIAL'))
                for s in (tip_dom, qtd_predio, qtd_res):
                    grouped = grouped.join(s, on='COD_PONNOT_GEO', how='left')
                grouped['QTD_PREDIO'] = grouped['QTD_PREDIO'].fillna(0).astype(int)
                grouped['QTD_RESIDENCIAL'] = grouped['QTD_RESIDENCIAL'].fillna(0).astype(int)

            # Contagem de UCs com geração registrada via CEG_GD
            col_ceg_uc = next((c for c in df_uc.columns if c.upper() == 'CEG_GD'), None)
            if col_ceg_uc:
                # fillna('') ANTES do astype(str): no dtype string do pandas 3 um
                # valor ausente vira '<NA>' (e não 'nan'), escapa do isin abaixo e
                # a máscara fica True para TODAS as UCs — QTD_GD_CEG saía igual a
                # QTD_UC em 100% dos postes, e a contagem de unidades com geração
                # por poste deixava de significar alguma coisa.
                mask_gd = (merged[col_ceg_uc].fillna('').astype(str).str.strip()
                           .isin(['', 'nan', 'None', '0', '0.0']) == False)
                qtd_gd_uc = (merged[mask_gd]
                             .groupby('COD_PONNOT_GEO').size()
                             .rename('QTD_GD_CEG'))
                grouped = grouped.join(qtd_gd_uc, on='COD_PONNOT_GEO', how='left')
                grouped['QTD_GD_CEG'] = grouped['QTD_GD_CEG'].fillna(0).astype(int)

            # Junta dados de geração (UGBT/UGMT) se existirem
            if ug_por_polo is not None:
                grouped = grouped.join(ug_por_polo, on='COD_PONNOT_GEO', how='left')
                n_com_gd = grouped['GD_QTD'].notna().sum()
                log.info("[AGREGA] %s | %d postes com geração GD associada", nome_base, n_com_gd)

            grouped.rename(columns={'COD_PONNOT_GEO': 'COD_PONNOT'}, inplace=True)

            # Renomeia colunas originais de UC para prefixo UC_
            rename_dict = {}
            for c in df_uc.columns:
                c_up = c.upper()
                if c_up == 'COD_ID':
                    rename_dict[c] = 'UC_COD_IDS'
                elif c in cols_soma:
                    rename_dict[c] = f'SOMA_{c}'
                # LISTA_ e GD_* ficam como estão
            for lista_col in [x for x in grouped.columns if x.startswith('LISTA_')]:
                rename_dict[lista_col] = lista_col   # mantém nome
            grouped.rename(columns=rename_dict, inplace=True)

            gdf_out = gpd.GeoDataFrame(grouped, geometry='geometry', crs=crs_ponto)
            out_name = f"{nome_base}_por_poste.gpkg"
            gdf_out.to_file(os.path.join(out_dir, out_name), driver="GPKG")
            log.info("[AGREGA] %s salvo (%d postes)", out_name, len(gdf_out))
            arquivos_gerados += 1

        if arquivos_gerados == 0:
            avisar("Aviso",
                          "Tabelas encontradas, mas conexões (PN_CON x COD_ID) não bateram.")

    except Exception:
        log.exception("[AGREGA] Erro crítico em agregar_ucs_aos_postes")
        errar("Erro",
                       "Falha ao agregar UCs. Veja logs/extrator.log para detalhes.")

