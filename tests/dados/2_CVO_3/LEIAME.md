# Dados de teste

Uma fatia da BDGD pública da ANEEL, reduzida ao que cabe num repositório:
o alimentador **2_CVO_3** com a topologia intacta — todos os transformadores,
segmentos e unidades consumidoras — e os catálogos de equipamento reduzidos
ao que ele de fato cita. De 2 MB para 1.7 MB.

Não é um alimentador inventado. Uma fixture sintética teria de acertar de
cabeça o contrato de dezenas de colunas da BDGD, e passaria a testar a ideia
que o autor faz da base em vez da base.

## O que saiu

- `CTMT_2_CVO_3.csv` — sem `DESCR`.
- `EQCR_2_CVO_3.csv` — sem `DESCR`.
- `EQRE_2_CVO_3.csv` — sem `DESCR`.
- `EQSE` — o conversor não lê esta tabela.
- `EQTRMT` — 1 das 2258 linhas, as citadas por `UNTRMT.TIP_UNID`.
- `PIP_2_CVO_3.csv` — sem `CLAS_SUB`, `GRU_TAR`, `DESCR`, `CLASSE_CONSUMO`, `TIP_EDIFICACAO`.
- `PONNOT_2_CVO_3.gpkg` — sem `DESCR`.
- `RAMLIG_2_CVO_3.csv` — sem `DESCR`.
- `SEGCON_2_CVO_3.csv` — sem `DESCR`.
- `SSDBT_2_CVO_3.gpkg` — sem `DESCR`.
- `SSDMT_2_CVO_3.gpkg` — sem `DESCR`.
- `TTEN_2_CVO_3.csv` — sem `DESCR`.
- `UCBT_tab_2_CVO_3.csv` — sem `CEP`, `CLAS_SUB`, `CNAE`, `GRU_TAR`, `DESCR`, `CLASSE_CONSUMO`, `TIP_EDIFICACAO`.
- `UCBT_tab_2_CVO_3_por_poste.gpkg` — sem `CEP`, `CLAS_SUB`, `CNAE`, `GRU_TAR`, `DESCR`, `CLASSE_CONSUMO`, `TIP_EDIFICACAO`, `LISTA_CNAE`, `LISTA_CLAS_SUB`, `LISTA_GRU_TAR`, `LISTA_TIP_EDIFICACAO`, `LISTA_CLASSE_CONSUMO`, `LISTA_CEP`.
- `UCMT_tab_2_CVO_3.csv` — sem `CEP`, `CLAS_SUB`, `CNAE`, `GRU_TAR`, `DESCR`, `CLASSE_CONSUMO`, `TIP_EDIFICACAO`.
- `UCMT_tab_2_CVO_3_por_poste.gpkg` — sem `CEP`, `CLAS_SUB`, `CNAE`, `GRU_TAR`, `DESCR`, `CLASSE_CONSUMO`, `TIP_EDIFICACAO`, `LISTA_CNAE`, `LISTA_CLAS_SUB`, `LISTA_GRU_TAR`, `LISTA_TIP_EDIFICACAO`, `LISTA_CLASSE_CONSUMO`, `LISTA_CEP`.
- `UGBT_tab_2_CVO_3.csv` — sem `CEP`, `CNAE`, `DESCR`.
- `UGMT_tab_2_CVO_3.csv` — sem `CEP`, `CNAE`, `DESCR`.
- `UNSEMT_2_CVO_3.gpkg` — sem `DESCR`.
- `UNTRMT_2_CVO_3.gpkg` — sem `DESCR`.

Que a redução não alterou o resultado não é promessa: o caso gerado a partir
desta pasta sai **byte a byte igual** ao gerado a partir do alimentador
completo (`python scripts/fazer_fixture.py --alim 2_CVO_3 --conferir`). A que
`tests/test_conversao.py` compara byte a byte com `src/bdgdcase/exemplo/` a
cada execução é a fixture de referência, indicada em `tests/dados/LEIAME.md`.

## Procedência e limites

Fonte: Base de Dados Geográfica da Distribuidora (BDGD), Agência Nacional de
Energia Elétrica, dado aberto em <https://dadosabertos.aneel.gov.br/>.
Distribuidora 5370 — Coopera (SC), ano-base 2024.

A BDGD ser aberta não torna tudo nela publicável em qualquer forma. As colunas
retiradas acima incluíam, na `DESCR` das unidades consumidoras, o número da
conta do cliente no formato da distribuidora; nos `.gpkg` agregados por poste,
um ponto geográfico exato chegava a carregar dezesseis contas concatenadas, com
CEP e CNAE de cada uma. Num alimentador de 752 unidades, isso é
reidentificável. Nada disso é lido pelo conversor.

O que permanece é o mínimo para que a fixture seja um modelo elétrico de
verdade: topologia, coordenadas de poste, e o consumo mensal por unidade sob o
identificador pseudônimo da própria BDGD. Sem CEP, sem CNAE e sem número de
conta, não há vínculo direto com pessoa — mas quem for reutilizar esta pasta
noutro contexto faz bem em reavaliar isso por conta própria.

## Regeneração

Esta pasta é derivada, não editada à mão:

    bdgdcase extrair --gdb <BDGD.gdb> --saida Output --alim 2_CVO_3
    python scripts/fazer_fixture.py --alim 2_CVO_3

Para a BDGD completa, veja a seção *Dados* do README.
