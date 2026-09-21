"""O recorte de um alimentador da geodatabase da ANEEL.

Lê o `.gdb` da distribuidora, filtra pelo alimentador (`CTMT`) e grava em
`Output/{ALIMENTADOR}/` a rede física e comercial — cabos, transformadores,
postes, unidades consumidoras e geradoras, catálogos —, mais as agregações por
poste.

**Sem interface.** Nem um `import tkinter` mora aqui, e é deliberado: até esta
modularização o trabalho vivia dentro de uma classe Tk de 1.622 linhas, e a
única forma de extrair sem abrir janela era instanciar uma invisível com
`root=None`. Quem quiser mostrar o andamento passa uma função `relatar` ao
construtor de :class:`Extrator`.
"""
import geopandas as gpd
import pyogrio
import pandas as pd
import os
import concurrent.futures
import multiprocessing
import warnings
import logging

from bdgdcase.caminhos import LOGS_DIR, garantir_dirs

warnings.filterwarnings("ignore", category=UserWarning)

# ---------------------------------------------------------------------------
# CONFIGURAÇÃO DE LOG  →  logs/extrator.log (bdgdcase.caminhos.LOGS_DIR)
# ---------------------------------------------------------------------------
# Multi-processo: o processo principal trunca o log (mode='w'); os processos
# filhos do ProcessPoolExecutor (re-importam o módulo no spawn) fazem append.
# Sem esse cuidado, cada child clobberia o log dos demais.
garantir_dirs()
_log_path = str(LOGS_DIR / "extrator.log")
_LOG_MODE = "w" if multiprocessing.current_process().name == 'MainProcess' else "a"
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [PID %(process)d] [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_path, mode=_LOG_MODE, encoding="utf-8"),
        logging.StreamHandler(),          # também imprime no cmd
    ]
)
log = logging.getLogger("ExtratoBDGD")

os.environ["OGR_ORGANIZE_POLYGONS"] = "SKIP"
os.environ["GDAL_CACHEMAX"] = "1024"

# Os módulos de assunto: quem é o quê (camadas), o que os códigos da norma
# querem dizer (catalogos), e como as unidades consumidoras somam por poste
# (agregacao). O motor abaixo é só a ordem em que essas coisas acontecem.
# Estão depois das variáveis de ambiente do GDAL de propósito: elas precisam
# valer antes de qualquer módulo abrir uma geodatabase.
from bdgdcase.extracao import agregacao, camadas, catalogos  # noqa: E402
from bdgdcase.extracao.agregacao import classificar_edificacao  # noqa: E402
from bdgdcase.extracao.camadas import (  # noqa: E402
    is_cabo, is_catalogo, is_ponto_rede, is_trafo, is_uc)
from bdgdcase.extracao.catalogos import aplicar_tpotrtv  # noqa: E402



class Extrator:
    """Recorta um alimentador da geodatabase e grava as tabelas dele.

    **Não tem interface.** Roda igual chamada da linha de comando, de uma janela
    ou de um processo filho — e é por isso que existe como classe própria: até
    aqui o trabalho morava dentro de um `ExtratorBDGDApp`, uma janela Tk, e a
    única forma de extrair sem abrir janela era instanciar uma invisível com
    `root=None`. O motor e a janela eram o mesmo objeto.

    A classe guarda estado entre as etapas do recorte (a união espacial dos
    cabos, os códigos de transformador, o conjunto de PACs), e é isso que a
    mantém classe em vez de virar um punhado de funções soltas.

    `relatar` recebe as mensagens de andamento. O padrão manda para o log; uma
    janela passa a sua própria função e mostra na barra de status — sem que o
    motor saiba que existe uma janela.
    """

    def __init__(self, *, limpar_colunas=True, relatar=None):
        """Prepara um extrator, sem interface e sem tocar em disco.

        `limpar_colunas` descarta as colunas que a conversão não lê — corta o
        tamanho do recorte, e desliga-se quando se está investigando a base.

        `relatar` é uma função que recebe o andamento em texto. Sem ela, o
        rastro fica só no log — que é o normal, porque a extração costuma rodar
        em processo filho, onde não há ninguém ouvindo.
        """
        self.limpar_colunas_flag = bool(limpar_colunas)
        self._relatar = relatar

        # Cache da análise de carga MT, preenchido por `analisar_carga_mt`:
        #   pct_mt_by_alim    {ctmt: % de energia em média tensão, 0 a 100}
        #   ene_total_by_alim {ctmt: kWh/mês total, MT + BT}
        self.pct_mt_by_alim = {}
        self.ene_total_by_alim = {}
        self.alimentadores_todos = []

    # ── Andamento ────────────────────────────────────────────────────────────

    def _get_clean_data(self):
        """A limpeza de colunas está ligada?

        Existe como método porque o pipeline a consulta em vários pontos, e
        durante a transição da janela ela vinha ora de um `BooleanVar` do Tk,
        ora de um bool. Hoje é sempre bool.
        """
        return self.limpar_colunas_flag

    def _ui_status(self, msg):
        """Conta o andamento a quem estiver ouvindo, e sempre ao log.

        O log fica de qualquer jeito: a extração de um alimentador leva minutos
        e roda em processo filho, e quando alguma coisa dá errado o arquivo de
        log é o único lugar onde a história ficou.
        """
        log.info("[STATUS] %s", msg)
        if self._relatar is not None:
            try:
                self._relatar(msg)
            except Exception:      # pragma: no cover - ouvinte não pode derrubar
                log.exception("[STATUS] o ouvinte de andamento falhou")

    def _ui_warn(self, title, msg):
        """Um aviso: o recorte segue, com menos informação do que devia."""
        log.warning("[%s] %s", title, msg)
        self._ui_status('%s: %s' % (title, msg))

    def _ui_error(self, title, msg):
        """Um erro: esta parte do recorte não saiu."""
        log.error("[%s] %s", title, msg)
        self._ui_status('%s: %s' % (title, msg))


    def obter_coluna_alimentador(self, fields_upper):
        """Ver :func:`bdgdcase.extracao.camadas.obter_coluna_alimentador`."""
        return camadas.obter_coluna_alimentador(fields_upper)

    # =====================================================================
    # ANÁLISE DE CARGA MT POR ALIMENTADOR (proxy: ENE_MED das UCs)
    # =====================================================================
    # Objetivo: permitir filtrar alimentadores cuja proporção de carga MT
    # excede um limiar definido pelo usuário. Alimentadores muito MT têm
    # curva de carga ditada por processos industriais, e não pelo perfil
    # residencial/comercial típico de um alimentador de distribuição.
    #
    # Métrica: pct_mt = sum(ENE_MED em UCMT_*) / sum(ENE_MED em UCMT_* + UCBT_*)
    # ENE_MED = consumo médio mensal (kWh) já registrado no BDGD.
    # Fallback: se ENE_MED estiver ausente, usa-se a média de ENE_01..ENE_12.
    # =====================================================================


    def analisar_carga_mt(self, gdb):
        """Quanto da energia de cada alimentador está em média tensão.

        Devolve `{alimentador: % em MT}`, e guarda também o total em
        `ene_total_by_alim`. Serve para escolher o que estudar: um alimentador
        quase todo MT é uma rede industrial, com pouca baixa para modelar, e
        um quase todo BT é o oposto.

        Varre a geodatabase inteira, e por isso leva minutos — é operação de
        escolha, feita uma vez por base, e não parte do recorte.
        """
        sum_mt, sum_bt = {}, {}
        meses_cols = {f'ENE_{i:02d}' for i in range(1, 13)}

        try:
            layers = [layer[0] for layer in pyogrio.list_layers(gdb)]
            for layer in layers:
                layer_upper = layer.upper()
                if not (layer_upper.startswith('UCMT_') or layer_upper.startswith('UCBT_')):
                    continue
                try:
                    info = pyogrio.read_info(gdb, layer=layer)
                    fields_upper = {f.upper(): f for f in info['fields']}
                    col_ctmt_up = self.obter_coluna_alimentador(fields_upper)
                    if not col_ctmt_up:
                        log.warning("[MT%%] %s sem coluna de alimentador — ignorado", layer)
                        continue
                    col_ctmt = [f for f in info['fields'] if f.upper() == col_ctmt_up][0]

                    if 'ENE_MED' in fields_upper:
                        col_ene = [f for f in info['fields'] if f.upper() == 'ENE_MED'][0]
                        df = pyogrio.read_dataframe(
                            gdb, layer=layer,
                            columns=[col_ctmt, col_ene],
                            read_geometry=False)
                        df[col_ene] = pd.to_numeric(df[col_ene], errors='coerce').fillna(0)
                        df[col_ctmt] = df[col_ctmt].astype(str).str.strip()
                        sums = df.groupby(col_ctmt)[col_ene].sum().to_dict()
                    else:
                        cols_ene = [f for f in info['fields'] if f.upper() in meses_cols]
                        if not cols_ene:
                            log.warning("[MT%%] %s sem ENE_MED nem ENE_01..ENE_12 — pulado", layer)
                            continue
                        df = pyogrio.read_dataframe(
                            gdb, layer=layer,
                            columns=[col_ctmt] + cols_ene,
                            read_geometry=False)
                        for c in cols_ene:
                            df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
                        df[col_ctmt] = df[col_ctmt].astype(str).str.strip()
                        df['_ENE_MED_CALC'] = df[cols_ene].mean(axis=1)
                        sums = df.groupby(col_ctmt)['_ENE_MED_CALC'].sum().to_dict()

                    target = sum_mt if layer_upper.startswith('UCMT_') else sum_bt
                    for k, v in sums.items():
                        if not k or k in ('0', 'None', 'nan'):
                            continue
                        target[k] = target.get(k, 0.0) + float(v)
                    log.info("[MT%%] %s: %d alimentadores agregados", layer, len(sums))
                except Exception:
                    log.exception("[MT%%] Falha ao processar %s", layer)

            pct_mt, ene_total = {}, {}
            for c in set(sum_mt) | set(sum_bt):
                mt = sum_mt.get(c, 0.0)
                bt = sum_bt.get(c, 0.0)
                total = mt + bt
                ene_total[c] = total
                pct_mt[c] = (mt / total * 100.0) if total > 0 else 0.0

            self.pct_mt_by_alim = pct_mt
            self.ene_total_by_alim = ene_total

            n_com_dados = len(pct_mt)
            n_so_mt = sum(1 for v in pct_mt.values() if v >= 99.999)
            log.info("[MT%%] Concluído: %d alimentadores com dados | %d 100%% MT",
                     n_com_dados, n_so_mt)
            self._ui_status('%d alimentador(es) com dados de energia, '
                            '%d inteiramente em média tensão.'
                            % (n_com_dados, n_so_mt))
            return pct_mt
        except Exception as exc:
            # Não levanta: é uma análise auxiliar, e falhar nela não pode
            # impedir quem a pediu de extrair o alimentador assim mesmo.
            log.exception("[MT%%] Erro crítico em analisar_carga_mt")
            self._ui_error('Carga MT', 'falha no cálculo: %s' % exc)
            return {}


    def limpar_colunas(self, df, layer_upper=''):
        """Ver :func:`bdgdcase.extracao.camadas.limpar_colunas`.

        O método existe para amarrar a flag do extrator ao parâmetro da
        função — a decisão de filtrar é do extrator, o critério é da camada.
        """
        return camadas.limpar_colunas(df, layer_upper,
                                      ativo=self._get_clean_data())

    def _resgatar_trafos_sem_ctmt(self, gdb, out_dir, alim_filter, pac_mt,
                                  trans_codes, pac_set_mt, caixa_mt=None):
        """Traz os transformadores que a base deixou sem alimentador atribuido.

        O recorte filtra `UNTRMT` por `CTMT`, e isso basta em base que preenche
        o campo. Medido: a Celesc deixa **6** transformadores sem `CTMT` em
        212.301. Uma outra deixa **152.940 em 518.989 — 29,5%**, e esses caem
        fora do recorte em silencio.

        O estrago aparece depois, na conversao: os circuitos de baixa que eles
        alimentam vem no recorte (porque o `SSDBT` traz o `CTMT` preenchido),
        mas sem transformador nenhum. Medido num alimentador: **185 ilhas de
        baixa, 5.694 barras, 4.249 pontos de carga** sem caminho ate a
        subestacao. O caso resolve assim mesmo, mostrando so o pedaco que
        sobrou.

        **O crivo e a conectividade, nao a distancia.** Entra o transformador
        cujo `CTMT` esta em branco E cujo primario (`PAC_1`) e um no da media
        DESTE alimentador — o cadastro mesmo dizendo onde ele esta ligado.
        Medido no alimentador acima, contra os 2.816 transformadores da area:

        - 199 sem `CTMT`, e **199 deles** com o primario na media daqui;
        - 2.072 de OUTRO `CTMT`, e **nenhum**.

        Nao ha palpite: o criterio recupera todos os orfaos e nao rouba um
        transformador de vizinho sequer.

        **A caixa de busca vem da MEDIA, e nao dos transformadores ja achados.**
        Foi o que faltou na primeira versao. A caixa dos transformadores que o
        filtro `CTMT` encontrou e menor que o alimentador, justamente porque
        falta nela o que se quer achar: num alimentador medido, a media ia
        0,12 grau mais a oeste, 0,17 a leste e 0,15 ao norte — 12 a 18 km de
        rede fora da caixa. O resgate trazia 1.023 transformadores e nenhum
        deles salvava uma ilha de baixa; com a caixa da media sao 1.920, e
        **719** deles sao o transformador que faltava a um circuito orfao.

        A caixa e so limite de leitura, e nao criterio: quem decide continua
        sendo `PAC_1` na media deste alimentador. Ampliar a caixa nao afrouxa
        nada — dos 2.179 sem `CTMT` que ela passa a alcancar, 259 sao recusados
        por nao terem o primario aqui.
        """
        import geopandas as gpd
        import pandas as pd

        if not pac_mt:
            return

        for layer in [c for c, *_ in pyogrio.list_layers(gdb)]:
            try:
                info = pyogrio.read_info(gdb, layer=layer)
                campos = {f.upper(): f for f in info['fields']}
                if not is_trafo(layer.upper(), campos):
                    continue
                if 'CTMT' not in campos or 'PAC_1' not in campos:
                    continue

                col_ctmt, col_p1 = campos['CTMT'], campos['PAC_1']
                arquivo = os.path.join(
                    out_dir, '%s_%s.gpkg' % (layer, alim_filter.replace('/', '_')))
                if not os.path.isfile(arquivo):
                    continue
                ja = gpd.read_file(arquivo)

                # A caixa limita a leitura: sem ela, a consulta varreria os
                # 152 mil sem CTMT da base inteira. Vem da MEDIA, que e a
                # extensao real do alimentador — ver o docstring.
                cx = caixa_mt if caixa_mt is not None else ja.total_bounds
                margem = 0.01
                soltos = pyogrio.read_dataframe(
                    gdb, layer=layer,
                    where="%s IS NULL OR %s = ''" % (col_ctmt, col_ctmt),
                    bbox=(cx[0] - margem, cx[1] - margem,
                          cx[2] + margem, cx[3] + margem))
                if not len(soltos):
                    continue

                meus = soltos[soltos[col_p1].astype(str).str.strip().isin(pac_mt)]
                if not len(meus):
                    continue

                meus = self.limpar_colunas(meus, layer.upper())
                juntos = gpd.GeoDataFrame(
                    pd.concat([ja, meus[[c for c in meus.columns if c in ja.columns]]],
                              ignore_index=True),
                    crs=ja.crs)
                juntos.to_file(arquivo, driver='GPKG')

                if 'COD_ID' in campos:
                    trans_codes.update(
                        meus[campos['COD_ID']].dropna().astype(str).str.strip().tolist())
                pac_set_mt.update(
                    meus[col_p1].dropna().astype(str).str.strip().tolist())
                if campos.get('PAC_2') in meus.columns:
                    pac_set_mt.update(
                        meus[campos['PAC_2']].dropna().astype(str).str.strip().tolist())

                self._ui_status(
                    '%s: %d transformador(es) sem CTMT resgatados pelo primario '
                    'na media deste alimentador.' % (layer, len(meus)))
                log.info('[RESGATE-TR] %s: %d trafos sem CTMT com primario na '
                         'media de %s', layer, len(meus), alim_filter)
            except Exception:
                # Resgate e melhoria, nao requisito: falhar aqui nao pode custar
                # o recorte que ja saiu.
                log.exception('[RESGATE-TR] falhou em %s', layer)

    def extrair_ancoras(self, gdb, out_dir, alim_filter, mun_filter):
        """Fase 1: recorta a rede física, e com ela ancora todo o resto.

        Grava os cabos e os transformadores do alimentador, e devolve os três
        crivos que as fases seguintes usam para saber o que é do alimentador e
        o que é do vizinho:

        - `segcon_union`: a geometria de todos os cabos, unida. Serve de âncora
          espacial — postes e unidades consumidoras se filtram por proximidade
          a ela, porque na BDGD nem toda camada traz a coluna do alimentador;
        - `pac_set_mt`: os pontos de conexão da média tensão, que amarram
          chaves, capacitores e reguladores à rede que já existe;
        - `trans_codes`: os `COD_ID` dos transformadores, que é como as
          unidades consumidoras de baixa se ligam ao alimentador certo.

        A ordem é obrigatória: sem a rede, não há por onde filtrar o resto.
        """
        self._ui_status("Fase 1: Desenhando rede principal (Cabos MT/BT, Trafos)...")

        layers = [layer[0] for layer in pyogrio.list_layers(gdb)]
        segcon_union = None
        trans_codes = set()
        pac_set_mt = set()
        # So os PACs da MEDIA. `pac_set_mt` acima acumula os de todos os
        # cabos (media, baixa e ramal) e serve a outro proposito; o
        # resgate de transformador sem alimentador precisa do crivo
        # estrito — ver `_resgatar_trafos_sem_ctmt`.
        pac_mt_estrito = set()
        # A caixa da media, preenchida ao passar por SSDMT. E a extensao real do
        # alimentador, e o resgate de transformador a usa como limite de leitura.
        caixa_mt = None
        camadas_concluidas = []

        for layer in layers:
            layer_upper = layer.upper()

            try:
                info = pyogrio.read_info(gdb, layer=layer)
                fields_upper = {f.upper(): f for f in info['fields']}

                e_cabo = is_cabo(layer_upper, fields_upper)
                e_trafo = is_trafo(layer_upper, fields_upper)

                if not e_cabo and not e_trafo: continue

                col_ctmt_upper = self.obter_coluna_alimentador(fields_upper)
                if not col_ctmt_upper: continue

                col_ctmt = [f for f in info['fields'] if f.upper() == col_ctmt_upper][0]

                # B) Pré-filtra a leitura via SQL WHERE quando o driver suporta
                # (mesma técnica usada em process_single_layer p/ layers grandes).
                # Em SSDMT/SSDBT/RAMLIG/UNTRMT que cobrem a concessionária inteira
                # isso reduz I/O drasticamente: lê só as linhas do alimentador
                # em vez da layer toda. Em caso de exceção do driver, fallback
                # para leitura completa (preserva o comportamento original).
                where_clause = f"{col_ctmt} = '{alim_filter}'"
                try:
                    gdf = pyogrio.read_dataframe(gdb, layer=layer, where=where_clause)
                    log.info("[ANCHOR] %s — pré-filtrado por WHERE (%d reg.)", layer, len(gdf))
                except Exception as ex_where:
                    log.warning("[ANCHOR] WHERE falhou em %s (%s) — leitura completa",
                                layer, ex_where)
                    gdf = pyogrio.read_dataframe(gdb, layer=layer)

                # Máscara mantida como salvaguarda: trivial após o WHERE
                # (todos True) e ainda filtra corretamente no fallback de
                # leitura completa, inclusive CTMTs com whitespace.
                mask = gdf[col_ctmt].astype(str).str.strip() == alim_filter
                filtered_gdf = gdf[mask]
                if filtered_gdf.empty: continue

                if mun_filter and 'MUN' in fields_upper:
                    col_mun = [f for f in info['fields'] if f.upper() == 'MUN'][0]
                    filtered_gdf = filtered_gdf[filtered_gdf[col_mun].astype(str).str.strip() == mun_filter]

                if e_cabo:
                    # CORREÇÃO 1: Pega PAC de TODOS os cabos (MT, BT e Ramais) para garantir o resgate exato dos postes!
                    if 'PAC_1' in fields_upper:
                        col_p1 = [f for f in info['fields'] if f.upper() == 'PAC_1'][0]
                        pac_set_mt.update(filtered_gdf[col_p1].dropna().astype(str).str.strip().tolist())
                    if 'PAC_2' in fields_upper:
                        col_p2 = [f for f in info['fields'] if f.upper() == 'PAC_2'][0]
                        pac_set_mt.update(filtered_gdf[col_p2].dropna().astype(str).str.strip().tolist())

                    if layer_upper.startswith('SSDMT'):
                        for _c in ('PAC_1', 'PAC_2'):
                            if _c in fields_upper:
                                _col = [f for f in info['fields'] if f.upper() == _c][0]
                                pac_mt_estrito.update(
                                    filtered_gdf[_col].dropna().astype(str).str.strip().tolist())
                        # A extensao do alimentador e a da media, e e ela que o
                        # resgate usa para limitar a leitura.
                        try:
                            if getattr(filtered_gdf, 'geometry', None) is not None:
                                caixa_mt = filtered_gdf.total_bounds
                        except Exception:
                            pass

                    # CORREÇÃO 2: Buffer adaptativo e correção do aviso DeprecationWarning (union_all)
                    if hasattr(filtered_gdf, 'geometry') and filtered_gdf.geometry is not None and not filtered_gdf.geometry.is_empty.all():
                        is_geo = True
                        if filtered_gdf.crs is not None:
                            is_geo = filtered_gdf.crs.is_geographic

                        raio_buffer = 0.00005 if is_geo else 5.0
                        buf = filtered_gdf.geometry.buffer(raio_buffer)

                        try:
                            geom_unida = buf.union_all() # Novo padrão da biblioteca (remove o aviso do console)
                        except AttributeError:
                            geom_unida = buf.unary_union # Mantém compatibilidade com versões antigas

                        segcon_union = geom_unida if segcon_union is None else segcon_union.union(geom_unida)

                if e_trafo and 'COD_ID' in fields_upper:
                    col_cod = [f for f in info['fields'] if f.upper() == 'COD_ID'][0]
                    trans_codes.update(filtered_gdf[col_cod].dropna().astype(str).str.strip().tolist())
                    if 'PAC_1' in fields_upper:
                        col_p1 = [f for f in info['fields'] if f.upper() == 'PAC_1'][0]
                        pac_set_mt.update(filtered_gdf[col_p1].dropna().astype(str).str.strip().tolist())

                filtered_gdf = self.limpar_colunas(filtered_gdf, layer_upper)

                nome_arquivo = f"{layer}_{alim_filter.replace('/', '_')}.gpkg"
                caminho_saida = os.path.join(out_dir, nome_arquivo)
                if hasattr(filtered_gdf, 'geometry') and filtered_gdf.geometry is not None and not filtered_gdf.geometry.is_empty.all():
                    filtered_gdf.to_file(caminho_saida, driver="GPKG")
                else:
                    filtered_gdf.drop(columns='geometry', errors='ignore').to_csv(
                        caminho_saida.replace('.gpkg', '.csv'), index=False
                    )

                camadas_concluidas.append(layer)

            except Exception as e:
                print(f"Erro na âncora {layer}: {e}")

        pac_set_mt.discard('None')
        pac_set_mt.discard('nan')
        pac_set_mt.discard('0')
        pac_mt_estrito -= {'None', 'nan', '0', ''}

        self._resgatar_trafos_sem_ctmt(gdb, out_dir, alim_filter, pac_mt_estrito,
                                       trans_codes, pac_set_mt, caixa_mt)

        return segcon_union, trans_codes, pac_set_mt, camadas_concluidas

    def process_single_layer(self, layer, gdb, out_dir, alim_filter, mun_filter, segcon_union, trans_codes, pac_set_mt):
        """Fase 2: recorta UMA camada e grava o pedaço dela que é do alimentador.

        É chamada em paralelo, uma thread por camada, e por isso não guarda
        estado nenhum no extrator: devolve uma frase dizendo como foi, e quem
        chamou junta as frases. Cada camada escolhe sozinha por onde filtrar —
        pela coluna do alimentador quando existe, pelos `COD_ID` dos
        transformadores quando é unidade consumidora de baixa, pelos pontos de
        conexão quando é equipamento de média, e pela geometria dos cabos
        quando não sobrou nenhum dos três.

        Camada grande (mais de 50 mil feições) é lida com `WHERE` no próprio
        driver, e não em memória: as camadas de UC de uma distribuidora inteira
        têm milhões de linhas, e ler tudo para depois filtrar estoura a memória
        antes de chegar ao filtro.
        """
        # Limite acima do qual usamos WHERE na leitura para evitar MemoryError
        THRESHOLD_LARGE = 50_000

        try:
            info = pyogrio.read_info(gdb, layer=layer)
            fields_upper = {f.upper(): f for f in info['fields']}
            layer_upper = layer.upper()
            feature_count = info.get('features') or 0

            col_ctmt_upper = self.obter_coluna_alimentador(fields_upper)
            col_mun_upper = 'MUN' if 'MUN' in fields_upper else None

            # --- OTIMIZAÇÃO: FILTRO BBOX PARA PREVENIR MEMORY ERROR ---
            read_kwargs = {}
            geom_type = info.get("geometry_type")
            is_spatial = geom_type and geom_type != "Unknown"

            # Se a camada tem geometria e temos a área do alimentador desenhada
            if is_spatial and segcon_union is not None:
                bounds = segcon_union.bounds
                # Verifica grosseiramente se é coordenada geográfica (lat/lon) pelo X do Brasil
                is_geo = -80 < bounds[0] < -20
                margem = 0.005 if is_geo else 500.0  # Margem de segurança
                read_kwargs["bbox"] = (bounds[0]-margem, bounds[1]-margem, bounds[2]+margem, bounds[3]+margem)

            # Para layers grandes com campo CTMT: filtra na leitura via SQL WHERE e/ou BBOX
            leu_pre_filtrado = False
            if col_ctmt_upper and feature_count > THRESHOLD_LARGE:
                col_ctmt_real = [f for f in info['fields'] if f.upper() == col_ctmt_upper][0]
                where_clause = f"{col_ctmt_real} = '{alim_filter}'"
                try:
                    gdf = pyogrio.read_dataframe(gdb, layer=layer, where=where_clause, **read_kwargs)
                    leu_pre_filtrado = True
                    log.info("[LARGE] %s (%d features) — lido com WHERE '%s' e BBOX: %d reg.",
                             layer, feature_count, where_clause, len(gdf))
                except Exception as ex_where:
                    log.warning("[LARGE] WHERE falhou em %s (%s) — lendo apenas com BBOX (pode ser lento)", layer, ex_where)
                    gdf = pyogrio.read_dataframe(gdb, layer=layer, **read_kwargs)
            else:
                gdf = pyogrio.read_dataframe(gdb, layer=layer, **read_kwargs)

            if gdf.empty: return f"Vazia: {layer}"

            # Se já lemos pré-filtrado, todos os registros pertencem ao alimentador
            if leu_pre_filtrado:
                mask_ctmt = pd.Series(True, index=gdf.index)
            else:
                mask_ctmt = pd.Series(False, index=gdf.index)
                if col_ctmt_upper:
                    col_ctmt = [f for f in info['fields'] if f.upper() == col_ctmt_upper][0]
                    mask_ctmt = gdf[col_ctmt].astype(str).str.strip() == alim_filter

            gdf_ctmt = gdf[mask_ctmt]

            gdf_ponto_extra = gpd.GeoDataFrame()
            if is_ponto_rede(layer_upper) and 'COD_ID' in fields_upper:
                col_cod = [f for f in info['fields'] if f.upper() == 'COD_ID'][0]
                gdf_restante = gdf[~mask_ctmt]

                log.debug("[PONNOT] %s | total no GDB: %d | via CTMT: %d | restantes p/ filtro: %d",
                          layer, len(gdf), len(gdf_ctmt), len(gdf_restante))

                # Passo 1: postes referenciados diretamente pelos cabos (PAC_1/PAC_2)
                if pac_set_mt:
                    mask_pac = gdf_restante[col_cod].astype(str).str.strip().isin(pac_set_mt)
                    gdf_ponto_extra = gdf_restante[mask_pac]
                    log.debug("[PONNOT] %s | via PAC (pac_set_mt=%d): %d postes",
                              layer, len(pac_set_mt), len(gdf_ponto_extra))
                    amostra_pac = list(pac_set_mt)[:5]
                    amostra_ids = gdf_restante[col_cod].astype(str).str.strip().unique()[:5].tolist()
                    log.debug("[PONNOT] %s | amostra pac_set_mt: %s", layer, amostra_pac)
                    log.debug("[PONNOT] %s | amostra COD_ID no GDB: %s", layer, amostra_ids)
                else:
                    log.warning("[PONNOT] %s | pac_set_mt VAZIO — nenhum PAC de cabo coletado!", layer)

                # Passo 2: postes "folha" (ex.: pontos de conexão de UCMT) que não são
                # PAC de nenhum cabo mas estão fisicamente sobre a rede do alimentador.
                # O refinamento final é feito em filtrar_ponnot_por_distancia() após extrair as UCs.
                if segcon_union is not None:
                    ja_capturados = gdf_ponto_extra[col_cod].astype(str).str.strip() if not gdf_ponto_extra.empty else pd.Series([], dtype=str)
                    gdf_restante2 = gdf_restante[~gdf_restante[col_cod].astype(str).str.strip().isin(ja_capturados)]
                    if not gdf_restante2.empty:
                        mask_spatial = gdf_restante2.geometry.intersects(segcon_union)
                        gdf_espacial = gdf_restante2[mask_spatial]
                        if not gdf_espacial.empty:
                            gdf_ponto_extra = pd.concat([gdf_ponto_extra, gdf_espacial]).drop_duplicates()
                        log.debug("[PONNOT] %s | via espacial (buffer cabos): %d postes adicionais",
                                  layer, len(gdf_espacial) if not gdf_espacial.empty else 0)
                else:
                    log.warning("[PONNOT] %s | segcon_union é None — filtro espacial ignorado", layer)

                log.info("[PONNOT] %s | TOTAL capturado: %d postes", layer, len(gdf_ponto_extra))

            gdf_uc_extra = gpd.GeoDataFrame()
            if is_uc(layer_upper, fields_upper):
                gdf_restante = gdf[~mask_ctmt]
                campos_disponiveis = list(fields_upper.keys())
                log.debug("[UC] %s | feature_count GDB: %d | carregados: %d | via CTMT: %d | restantes: %d",
                          layer, feature_count, len(gdf), len(gdf_ctmt), len(gdf_restante))
                log.debug("[UC] %s | campos: %s", layer, campos_disponiveis)
                log.debug("[UC] %s | trans_codes disponíveis: %d", layer, len(trans_codes))
                amostra_trans = list(trans_codes)[:5]
                log.debug("[UC] %s | amostra trans_codes: %s", layer, amostra_trans)

                if trans_codes and 'UNI_TR_MT' in fields_upper:
                    col_tr = [f for f in info['fields'] if f.upper() == 'UNI_TR_MT'][0]
                    amostra_col = gdf_restante[col_tr].astype(str).str.strip().unique()[:5].tolist()
                    log.debug("[UC] %s | UNI_TR_MT amostra no GDB: %s", layer, amostra_col)
                    mask_tr = gdf_restante[col_tr].astype(str).str.strip().isin(trans_codes)
                    gdf_uc_extra = gdf_restante[mask_tr]
                    log.info("[UC] %s | via UNI_TR_MT: %d registros", layer, len(gdf_uc_extra))
                elif trans_codes and 'UNI_TR_S' in fields_upper:
                    col_tr = [f for f in info['fields'] if f.upper() == 'UNI_TR_S'][0]
                    amostra_col = gdf_restante[col_tr].astype(str).str.strip().unique()[:5].tolist()
                    log.debug("[UC] %s | UNI_TR_S amostra no GDB: %s", layer, amostra_col)
                    mask_tr = gdf_restante[col_tr].astype(str).str.strip().isin(trans_codes)
                    gdf_uc_extra = gdf_restante[mask_tr]
                    log.info("[UC] %s | via UNI_TR_S: %d registros", layer, len(gdf_uc_extra))
                else:
                    log.warning("[UC] %s | sem UNI_TR_MT nem UNI_TR_S — apenas CTMT (%d reg.)",
                                layer, len(gdf_ctmt))

            # --- NOVA LÓGICA: Tratamento para Tabelas de Catálogo ---
            is_cat = is_catalogo(layer_upper)

            if is_cat:
                if layer_upper == 'CTMT':
                    # Se for a tabela de circuitos, filtra pelo código do alimentador no COD_ID
                    col_cod = [f for f in info['fields'] if f.upper() == 'COD_ID'][0]
                    gdf_cat = gdf[gdf[col_cod].astype(str).str.strip() == alim_filter]
                    dfs_para_unir = [gdf_cat] if not gdf_cat.empty else []
                else:
                    # Se for equipamento/cabo (SEGCON, EQTRMT, etc), traz a tabela inteira (Catálogo global)
                    dfs_para_unir = [gdf]
            else:
                # Lógica normal para as redes físicas
                dfs_para_unir = [df for df in [gdf_ctmt, gdf_ponto_extra, gdf_uc_extra] if not df.empty]

            if not dfs_para_unir: return f"Ignorado (Não pertence à rede): {layer}"
            # --------------------------------------------------------

            filtered_gdf = pd.concat(dfs_para_unir).drop_duplicates()

            if mun_filter and col_mun_upper and not filtered_gdf.empty:
                antes_mun = len(filtered_gdf)
                col_mun = [f for f in info['fields'] if f.upper() == col_mun_upper][0]
                filtered_gdf = filtered_gdf[filtered_gdf[col_mun].astype(str).str.strip() == mun_filter]
                log.debug("[MUN] %s | filtro município: %d → %d", layer, antes_mun, len(filtered_gdf))

            if filtered_gdf.empty: return f"Ignorado (Não pertence à rede): {layer}"

            # Decodifica POT_NOM (TPOTRTV) para bancos de capacitores MT/BT
            if layer_upper.startswith('UNCRMT') or layer_upper.startswith('UNCRBT'):
                filtered_gdf = aplicar_tpotrtv(filtered_gdf)

            # Classifica tipo de edificação (casa/predio/...) por UC antes da limpeza
            if is_uc(layer_upper, fields_upper):
                filtered_gdf = classificar_edificacao(filtered_gdf)
                if 'TIP_EDIFICACAO' in filtered_gdf.columns:
                    dist = filtered_gdf['TIP_EDIFICACAO'].value_counts().to_dict()
                    log.info("[TIP_EDIF] %s | %s", layer, dist)

            # APLICA A LIMPEZA DE COLUNAS
            filtered_gdf = self.limpar_colunas(filtered_gdf, layer_upper)

            nome_arquivo = f"{layer}_{alim_filter.replace('/', '_')}.gpkg"
            caminho_saida = os.path.join(out_dir, nome_arquivo)

            tem_geometria = (hasattr(filtered_gdf, 'geometry') and
                             filtered_gdf.geometry is not None and
                             not filtered_gdf.geometry.is_empty.all() and
                             not filtered_gdf.geometry.isna().all())

            if tem_geometria:
                filtered_gdf.to_file(caminho_saida, driver="GPKG")
                log.info("[SALVO] %s → %s (GPKG, %d reg.)", layer, nome_arquivo, len(filtered_gdf))
            else:
                filtered_gdf.drop(columns='geometry', errors='ignore').to_csv(
                    caminho_saida.replace('.gpkg', '.csv'), index=False
                )
                log.info("[SALVO] %s → %s (CSV, %d reg.)", layer, nome_arquivo.replace('.gpkg','.csv'), len(filtered_gdf))

            return f"Extraído com Sucesso: {layer} ({len(filtered_gdf)} registros)"

        except Exception as e:
            log.exception("[ERRO] process_single_layer em %s", layer)
            return f"Erro em {layer}: {e}"

    def agregar_ucs_aos_postes(self, out_dir):
        """Ver :func:`bdgdcase.extracao.agregacao.agregar_ucs_aos_postes`.

        O que o método acrescenta é o caminho de volta do relato: aviso e erro
        da agregação chegam a quem estiver ouvindo o andamento.
        """
        return agregacao.agregar_ucs_aos_postes(
            out_dir, avisar=self._ui_warn, errar=self._ui_error)

    def filtrar_ponnot_por_distancia(self, out_dir, segcon_union):
        """Remove postes de outros alimentadores capturados pelo buffer espacial.

        Estratégia: calcula o convex hull de todos os cabos do alimentador (já
        embutido em segcon_union) e expande por uma margem de segurança. Postes
        além dessa margem são descartados.
        """
        if segcon_union is None:
            log.warning("[HULL] segcon_union é None — filtro de distância ignorado")
            return

        ponto_files = []
        for f in os.listdir(out_dir):
            f_upper = f.upper()
            if ('PONNOT' in f_upper or 'PONFAS' in f_upper) and '_POR_POSTE' not in f_upper and f.lower().endswith('.gpkg'):
                ponto_files.append(os.path.join(out_dir, f))

        if not ponto_files:
            log.warning("[HULL] Nenhum arquivo PONNOT/PONFAS encontrado para filtrar")
            return

        # Convex hull dos cabos do alimentador + margem generosa para não cortar postes legítimos
        hull = segcon_union.convex_hull
        bounds = hull.bounds
        log.info("[HULL] Convex hull do alimentador — bounds: minx=%.6f miny=%.6f maxx=%.6f maxy=%.6f",
                 bounds[0], bounds[1], bounds[2], bounds[3])

        try:
            gdf_ref = gpd.read_file(ponto_files[0])
            is_geo = gdf_ref.crs.is_geographic if gdf_ref.crs is not None else True
        except Exception:
            is_geo = True
        margem = 0.002 if is_geo else 200.0   # ~220 m em graus ou 200 m em metros
        area_alimentador = hull.buffer(margem)
        log.info("[HULL] Margem aplicada: %.5f (%s) | CRS geográfico: %s",
                 margem, "graus" if is_geo else "metros", is_geo)

        for pf in ponto_files:
            try:
                gdf = gpd.read_file(pf)
                if gdf.empty:
                    log.debug("[HULL] %s vazio — ignorado", os.path.basename(pf))
                    continue
                mask = gdf.geometry.within(area_alimentador)
                gdf_filtrado = gdf[mask]
                removidos = len(gdf) - len(gdf_filtrado)
                log.info("[HULL] %s | antes: %d | após: %d | removidos: %d",
                         os.path.basename(pf), len(gdf), len(gdf_filtrado), removidos)
                if removidos > 0:
                    log.debug("[HULL] %s | postes removidos (amostra COD_ID): %s",
                              os.path.basename(pf),
                              gdf[~mask]['COD_ID'].astype(str).unique()[:8].tolist() if 'COD_ID' in gdf.columns else "N/A")
                    gdf_filtrado.to_file(pf, driver="GPKG")
                else:
                    log.info("[HULL] %s | nenhum poste removido — todos dentro do hull", os.path.basename(pf))
            except Exception:
                log.exception("[HULL] Filtro de distância falhou em %s", os.path.basename(pf))

    def garantir_tten_em_saida(self, out_dir, alim_filter):
        """Ver :func:`bdgdcase.extracao.catalogos.garantir_tten_em_saida`."""
        return catalogos.garantir_tten_em_saida(out_dir, alim_filter)

    def _executar_pipeline_alim(self, gdb, base_out, alim_filter, mun_filter,
                                threads_internas, prefixo_ui=None):
        """Executa as fases do pipeline para UM alimentador.

        - Fase 1: extrair_ancoras (cabos + trafos do alim → segcon_union, PACs, trans_codes)
        - Fase 2: process_single_layer paralelo (postes, UCs, catálogos) com `threads_internas` threads
        - Fase 2.5: filtrar_ponnot_por_distancia (recorte pelo convex hull do alim)
        - Fase 2.6: garantir_tten_em_saida
        - Fase 3:   agregar_ucs_aos_postes

        `prefixo_ui` é um rótulo para as mensagens de andamento — tipicamente
        "3/12" quando se extrai um lote. Elas vão para `relatar`, e o motor não
        sabe se do outro lado há uma janela, um terminal ou nada.

        Retorna (ok_bool, err_str_or_None). NÃO levanta — captura tudo, porque
        quem extrai um lote precisa saber quais falharam e seguir com os
        outros.
        """
        out_dir = os.path.join(base_out, alim_filter)
        os.makedirs(out_dir, exist_ok=True)

        def _set_status(msg):
            """Conta o andamento já prefixado com o alimentador desta passada.

            O lote roda vários alimentadores em paralelo, e sem o prefixo as
            mensagens dos processos se misturam numa lista em que nada se acha.
            """
            self._ui_status('[%s] %s' % (alim_filter, msg))

        try:
            _set_status(f"{prefixo_ui or alim_filter} — Fase 1: Desenhando rede principal...")

            segcon_union, trans_codes, pac_set_mt, concluidos = self.extrair_ancoras(
                gdb, out_dir, alim_filter, mun_filter)

            _set_status(f"{prefixo_ui or alim_filter} — Âncoras: "
                        f"{len(trans_codes)} trafos, {len(pac_set_mt)} nós MT.")

            layers = [layer[0] for layer in pyogrio.list_layers(gdb)
                      if layer[0] not in concluidos]
            total_layers = len(layers)

            if total_layers == 0:
                log.warning("%s: apenas cabos e trafos encontrados", alim_filter)
                return True, None

            _set_status(f"{prefixo_ui or alim_filter} — Fase 2: "
                        f"Resgatando postes e UCs ({total_layers} layers)...")

            camadas_processadas = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=threads_internas) as executor:
                futuros = {
                    executor.submit(self.process_single_layer, layer, gdb, out_dir,
                                    alim_filter, mun_filter, segcon_union,
                                    trans_codes, pac_set_mt): layer
                    for layer in layers
                }
                for futuro in concurrent.futures.as_completed(futuros):
                    resultado = futuro.result()
                    camadas_processadas += 1
                    _set_status('%d/%d camadas — %s'
                                % (camadas_processadas, total_layers, resultado))

            _set_status(f"{prefixo_ui or alim_filter} — Fase 2.5: "
                        f"Filtrando postes externos pelo hull...")
            self.filtrar_ponnot_por_distancia(out_dir, segcon_union)

            _set_status(f"{prefixo_ui or alim_filter} — Fase 2.6: "
                        f"Garantindo catálogo TTEN...")
            self.garantir_tten_em_saida(out_dir, alim_filter)

            _set_status(f"{prefixo_ui or alim_filter} — Fase 3: "
                        f"Agregando cargas das UCs nos postes...")
            self.agregar_ucs_aos_postes(out_dir)

            log.info("%s: extração concluída", alim_filter)
            return True, None
        except Exception as e:
            log.exception("%s: falha na extração", alim_filter)
            return False, str(e)




# =============================================================================
# O PONTO DE ENTRADA
# =============================================================================
def extrair_alimentador(payload):
    """Recorta UM alimentador. É o que `bdgdcase.api.extrair` chama.

    Recebe um dicionário em vez de argumentos nomeados porque precisa ser
    **pickleável**: quando se extrai um lote, cada alimentador roda num processo
    filho de um `ProcessPoolExecutor`, e o que atravessa a fronteira do
    processo tem de ser uma função de módulo com um argumento simples.

    Nunca levanta. Devolve `(alimentador, deu_certo, erro)` — quem extrai doze
    alimentadores precisa saber quais falharam e ficar com os outros onze, e
    não perder o lote inteiro no primeiro tropeço.
    """
    alim_filter = payload.get('alim_filter', '<sem-alim>')
    try:
        motor = Extrator(limpar_colunas=payload['opt_clean'],
                         relatar=payload.get('relatar'))
        ok, err = motor._executar_pipeline_alim(
            gdb=payload['gdb'],
            base_out=payload['base_out'],
            alim_filter=alim_filter,
            mun_filter=payload['mun_filter'],
            threads_internas=payload['threads_internas'],
            prefixo_ui=payload.get('prefixo_ui'),
        )
        return (alim_filter, ok, err)
    except Exception as e:
        log.exception("[WORKER] Erro fatal em %s", alim_filter)
        return (alim_filter, False, str(e))