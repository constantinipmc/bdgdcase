# Dados de teste

Uma fatia da BDGD pública da ANEEL, reduzida ao que cabe num repositório:
o alimentador **URB13** com a topologia intacta — todos os transformadores,
segmentos e unidades consumidoras — e os catálogos de equipamento reduzidos
ao que ele de fato cita. De 69 MB para 2.4 MB.

Não é um alimentador inventado. Uma fixture sintética teria de acertar de
cabeça o contrato de dezenas de colunas da BDGD, e passaria a testar a ideia
que o autor faz da base em vez da base.

## O que saiu

- `CRVCRG_URB13.csv` — sem `DESCR`.
- `CTMT_URB13.csv` — sem `DESCR`.
- `EQCR_URB13.csv` — sem `DESCR`.
- `EQRE_URB13.csv` — sem `DESCR`.
- `EQSE` — o conversor não lê esta tabela.
- `EQTRMT` — nenhuma das 214534 linhas é citada por `UNTRMT.TIP_UNID` neste alimentador, de modo que o catálogo nunca é consultado e todo parâmetro de transformador vem do próprio `UNTRMT`.
- `PONNOT_URB13.gpkg` — sem `DESCR`.
- `RAMLIG_URB13.csv` — sem `DESCR`.
- `SEGCON_URB13.csv` — sem `DESCR`.
- `SSDBT_URB13.gpkg` — sem `DESCR`.
- `SSDMT_URB13.gpkg` — sem `DESCR`.
- `TTEN_URB13.csv` — sem `DESCR`.
- `UCBT_tab_URB13.csv` — sem `CEP`, `CLAS_SUB`, `CNAE`, `GRU_TAR`, `DESCR`, `CLASSE_CONSUMO`, `TIP_EDIFICACAO`.
- `UCBT_tab_URB13_por_poste.gpkg` — sem `CEP`, `CLAS_SUB`, `CNAE`, `GRU_TAR`, `DESCR`, `CLASSE_CONSUMO`, `TIP_EDIFICACAO`, `LISTA_CNAE`, `LISTA_CLAS_SUB`, `LISTA_GRU_TAR`, `LISTA_TIP_EDIFICACAO`, `LISTA_CLASSE_CONSUMO`, `LISTA_CEP`.
- `UCMT_tab_URB13.csv` — sem `CEP`, `CLAS_SUB`, `CNAE`, `GRU_TAR`, `DESCR`, `CLASSE_CONSUMO`, `TIP_EDIFICACAO`.
- `UCMT_tab_URB13_por_poste.gpkg` — sem `CEP`, `CLAS_SUB`, `CNAE`, `GRU_TAR`, `DESCR`, `CLASSE_CONSUMO`, `TIP_EDIFICACAO`, `LISTA_CNAE`, `LISTA_CLAS_SUB`, `LISTA_GRU_TAR`, `LISTA_TIP_EDIFICACAO`, `LISTA_CLASSE_CONSUMO`, `LISTA_CEP`.
- `UGBT_tab_URB13.csv` — sem `CEP`, `CNAE`, `DESCR`.
- `UGMT_tab_URB13.csv` — sem `CEP`, `CNAE`, `DESCR`.
- `UNREMT_URB13.gpkg` — sem `DESCR`.
- `UNSEMT_URB13.gpkg` — sem `DESCR`.
- `UNTRMT_URB13.gpkg` — sem `DESCR`.

Que a redução não alterou o resultado não é promessa: o caso gerado a partir
desta pasta sai **byte a byte igual** ao gerado a partir do alimentador
completo (`python scripts/fazer_fixture.py --alim URB13 --conferir`). A que
`tests/test_conversao.py` compara byte a byte com `src/bdgdcase/exemplo/` a
cada execução é a fixture de referência, indicada em `tests/dados/LEIAME.md`.

## Procedência e limites

Fonte: Base de Dados Geográfica da Distribuidora (BDGD), Agência Nacional de
Energia Elétrica, dado aberto em <https://dadosabertos.aneel.gov.br/>.
Distribuidora 396 — RGE Sul (RS), ano-base 2024.

A BDGD ser aberta não torna tudo nela publicável em qualquer forma. As colunas
retiradas acima incluíam, na `DESCR` das unidades consumidoras, o número da
conta do cliente no formato da distribuidora; nos `.gpkg` agregados por poste,
um ponto geográfico exato chegava a carregar dezesseis contas concatenadas, com
CEP e CNAE de cada uma. Num alimentador de 87 unidades, isso é
reidentificável. Nada disso é lido pelo conversor.

O que permanece é o mínimo para que a fixture seja um modelo elétrico de
verdade: topologia, coordenadas de poste, e o consumo mensal por unidade sob o
identificador pseudônimo da própria BDGD. Sem CEP, sem CNAE e sem número de
conta, não há vínculo direto com pessoa — mas quem for reutilizar esta pasta
noutro contexto faz bem em reavaliar isso por conta própria.

## Regeneração

Esta pasta é derivada, não editada à mão:

    bdgdcase extrair --gdb <BDGD.gdb> --saida Output --alim URB13
    python scripts/fazer_fixture.py --alim URB13

Para a BDGD completa, veja a seção *Dados* do README.
