# Do download da ANEEL ao resultado

Um percurso completo, do arquivo público da BDGD até a curva de carga simulada
de um alimentador. Cinco comandos.

Os tempos e os números deste guia foram medidos de ponta a ponta na BDGD da
Celesc de 2024, num notebook comum, para o alimentador **TRO05** — o mesmo que
está em [`../src/bdgdcase/exemplo/`](../src/bdgdcase/exemplo/TRO05_DU). Rodando os passos abaixo você chega
exatamente nos mesmos valores.

> **Prefere clicar?** `bdgdcase gui` abre uma janela que faz esta mesma cadeia
> inteira: escolher a base, varrer, selecionar o alimentador numa lista e marcar
> até onde ir. Vale ler os passos abaixo de qualquer forma — a janela executa as
> mesmas etapas, e as ressalvas sobre o que os números significam valem igual.

| passo | comando | tempo |
|---|---|---|
| 1 | *(download e descompactação da BDGD)* | depende da sua conexão |
| 2 | `bdgdcase listar` | ~20 s |
| 3 | `bdgdcase extrair` | ~3 min por alimentador |
| 4 | `bdgdcase converter` | ~5 s |
| 5 | `bdgdcase verificar` | instantâneo |
| 6 | `bdgdcase rodar` | instantâneo |

---

## 0. Instalar

```bash
pip install "git+https://github.com/constantinipmc/bdgdcase.git"
```

Vem tudo junto: a leitura da BDGD, a conversão, o OpenDSS que resolve e o mapa
que desenha. Um comando, e os cinco passos abaixo funcionam.

Confira:

```bash
bdgdcase --version
```

---

## 1. Baixar a BDGD

A base é pública, por distribuidora e por ano, no portal de dados abertos da
ANEEL:

> <https://dadosabertos.aneel.gov.br/> → *Base de Dados Geográfica da
> Distribuidora (BDGD)*

Escolha a distribuidora e o ano, e baixe o `.zip`. São vários gigabytes.

**Uma pegadinha que custa tempo.** Ao descompactar, o arquivo costuma produzir
uma pasta com o mesmo nome **duas vezes aninhada**:

```
Celesc-Dis_5697_2024-12-31_V11_20250926-1429.gdb/     ← pasta comum
└── Celesc-Dis_5697_2024-12-31_V11_20250926-1429.gdb/ ← a geodatabase de verdade
    ├── a00000001.gdbtable
    └── ... (209 arquivos)
```

O caminho a passar em `--gdb` é o **de dentro** — aquele que contém os
`a00000001.gdbtable`. Apontar para o de fora dá `Permission denied`, que é uma
mensagem enganosa: não é permissão, é que ali não há geodatabase nenhuma.

---

## 2. Descobrir o alimentador

Uma distribuidora estadual tem centenas de alimentadores, e você precisa do
código de um deles.

```bash
bdgdcase listar --gdb ".../Celesc-....gdb/Celesc-....gdb"
```

```
840 alimentador(es):
  ACA01  ACA02  ACA03  ACA04  ACA05  AGA01  AGA02  AGA03  AGA04  AGA05  AGA06
  AGA07  AGA08  AGA09  ALL01  ALL02  ALL04  ARI01  ARI02  ARI04  ARI05  ATA01
  ...

307 municipios; use --municipio para ve-los.
```

O prefixo de três letras costuma ser a subestação, e o número, o alimentador
dentro dela. Para restringir por cidade:

```bash
bdgdcase listar --gdb ".../Celesc-....gdb" --municipio
```

Os municípios saem como **códigos IBGE de 7 dígitos** (`4204202`, …), que é como
a BDGD os guarda — e é isso que `--municipio` espera no passo seguinte.

---

## 3. Extrair as tabelas do alimentador

```bash
bdgdcase extrair \
    --gdb ".../Celesc-....gdb/Celesc-....gdb" \
    --saida Output \
    --alim TRO05
```

Isso lê a geodatabase inteira filtrando pelo alimentador e escreve
`Output/TRO05/` com **20 arquivos, cerca de 61 MB**: as tabelas de rede em
`.gpkg` (segmentos, transformadores, postes) e as de cadastro em `.csv`
(unidades consumidoras, geração, curvas, catálogos).

Vários alimentadores de uma vez:

```bash
bdgdcase extrair --gdb ".../Celesc-....gdb" --saida Output --alim TRO05 IBA09 ACA01
```

O grosso dos 61 MB são dois catálogos de equipamento de toda a distribuidora,
que vêm junto porque a conversão pode precisar deles. Se o espaço apertar,
[`../scripts/fazer_fixture.py`](../scripts/fazer_fixture.py) reduz os catálogos
ao que o alimentador de fato cita — é o que gera as fixtures de
[`../tests/dados/`](../tests/dados).

---

## 4. Gerar o caso OpenDSS

```bash
bdgdcase converter Output/TRO05 --dias DU
```

Saem **18 arquivos** em `OpenDSS/TRO05_DU/`: onze `.dss`, o `BusCoords.csv`, os
cinco `Cadastro_*.csv` e o `Ajustes.json` com o registro do que a conversão
mudou. O `--dias` escolhe o tipo de dia, e a BDGD traz uma curva de carga
distinta para cada um: `DU` (dia útil), `SA` (sábado), `DO` (domingo). Os três
de uma vez:

```bash
bdgdcase converter Output/TRO05 --dias DU SA DO
```

Cada um gera sua pasta — `TRO05_DU`, `TRO05_SA`, `TRO05_DO`.

### Onde os arquivos vão parar

A saída fica em `OpenDSS/`, ao lado da pasta-mãe de `Output/`. Para ver como o
pacote está resolvendo os diretórios:

```bash
bdgdcase caminhos
```

O diretório base é o de trabalho corrente, e pode ser trocado pela variável de
ambiente `BDGD_BASE_DIR` ou por um `bdgdcase.json` na raiz:

```json
{ "output_dir": "dados/tabelas", "opendss_dir": "casos" }
```

---

## 5. O caso fecha?

```bash
bdgdcase verificar OpenDSS/TRO05_DU
```

```
  OpenDSS/TRO05_DU
    convergiu   147 barras, 73 cargas, 131 linhas, 11 trafos
    tensao      0.9790 a 1.0493 pu (media 1.0164)
    nominal     P=-39.6 kW  Q=41.3 kVAr  perdas=4.59 kW (instantaneo, sem curva)
```

Quatro perguntas encadeadas: o Master compila, a solução converge, o circuito
não está vazio, e as tensões caem numa faixa fisicamente plausível. É um teste
estrutural — pega modelo quebrado (impedância trocada, fase pendurada, tensão de
base errada), não julga qualidade de tensão.

**A linha `nominal` não descreve a operação.** O modo instantâneo do OpenDSS não
percorre as curvas de carga: cada elemento entra com o valor de placa. Se P sai
negativo, é a geração nominal superando a carga nominal — não um horário de
exportação. O dia de verdade é o passo seguinte.

Se falhar, a mensagem diz em qual das quatro perguntas, o que já reduz muito
onde procurar.

---

## 6. Rodar o dia

```bash
bdgdcase rodar OpenDSS/TRO05_DU --csv tro05_du.csv
```

```
  TRO05  (96 passos de 15 min)
    pico        84.4 kW as 18.50 h
    energia     682.4 kWh importados, 104.2 exportados
    perdas      49.2 kWh (7.21% da energia importada)
    tensao      0.9866 a 1.0476 pu no dia
```

96 passos de 15 minutos, com os controles em modo temporal para que reguladores
e capacitores atuem ao longo do dia como atuariam de verdade. O `--csv` grava
uma linha por passo:

```
horas,p_kw,q_kvar,s_kva,perdas_kw,...,v_min_pu,v_med_pu,v_max_pu,convergiu
0.0,39.80111474088244,12.248213332530488,41.643096240026,1.5113405471369807,...
0.25,39.621226063628725,12.195711817503089,41.45572266311078,1.5120191180819975,...
```

Os números saem com toda a precisão que o OpenDSS devolve, sem arredondamento:
arredondar é decisão de quem apresenta, e o arquivo é matéria-prima.

**Antes de usar qualquer número, olhe se apareceu um aviso de passos não
convergidos.** Passo que não converge entra na série com o valor do anterior,
para não abrir buraco na curva, e o comando avisa quando isso acontece. Número
interpolado que não se anuncia é a pior espécie de número.

Que `104,2 kWh` sejam exportados não é cenário nem projeção: é a geração
distribuída que **já consta do cadastro** daquele alimentador.

---

## 7. Desenhar

```python
import matplotlib.pyplot as plt
from bdgdcase.solucao import rodar_dia

dia = rodar_dia('OpenDSS/TRO05_DU')

fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(8, 6))
ax1.plot(dia['horas'], dia['p_kw'])
ax1.axhline(0, lw=0.8, color='0.6')        # abaixo daqui, o alimentador exporta
ax1.set_ylabel('P no ponto de entrega [kW]')

ax2.fill_between(dia['horas'], dia['v_min_pu'], dia['v_max_pu'], alpha=0.3)
ax2.plot(dia['horas'], dia['v_med_pu'])
ax2.set_ylabel('tensão [pu]')
ax2.set_xlabel('hora')
ax2.set_xlim(0, 24)

fig.savefig('tro05_du.png', dpi=200, bbox_inches='tight')
```

`matplotlib` já vem com o pacote: é o mesmo que desenha o mapa.

---

## 8. Daqui em diante

Para qualquer grandeza além dessas — perfil de tensão ao longo do alimentador,
carregamento por transformador, corrente contra ampacidade, registradores do
medidor de cabeceira —, o circuito continua carregado no OpenDSS depois da
simulação, e [`RESULTADOS.md`](RESULTADOS.md) mostra como consultá-lo. As
receitas de lá são testadas e os valores impressos são os que elas produzem.

---

## Tudo junto

```bash
GDB=".../Celesc-Dis_5697_2024-12-31_V11_20250926-1429.gdb/Celesc-Dis_5697_2024-12-31_V11_20250926-1429.gdb"

bdgdcase listar    --gdb "$GDB" | head
bdgdcase extrair   --gdb "$GDB" --saida Output --alim TRO05
bdgdcase converter Output/TRO05 --dias DU
bdgdcase verificar OpenDSS/TRO05_DU
bdgdcase rodar     OpenDSS/TRO05_DU --csv tro05_du.csv
```

---

## Quando algo dá errado

**`Permission denied` ao ler o `.gdb`** — quase sempre é o aninhamento do passo 1.
Aponte para a pasta que contém os arquivos `a00000001.gdbtable`.

**`bdgdcase listar` não encontra alimentador nenhum** — a base pode usar outro
nome de coluna. O pacote procura, nesta ordem: `CTMT`, `COD_CTMT`, `COD_ALIM`,
`ALIM`, `CIRCUITO`. Bases muito antigas podem trazer algo fora dessa lista.

**A extração falha num alimentador e passa nos outros** — `extrair` devolve um
resultado por alimentador, com a mensagem de erro de cada um; os que passaram
ficam gravados. Não é preciso repetir tudo.

**`Master.dss não encontrado`** — o `verificar` e o `rodar` recebem a pasta do
**caso** (`OpenDSS/TRO05_DU`), não a das tabelas (`Output/TRO05`).

**`Resolver o caso exige o OpenDSS`** — a instalação está incompleta; o
OpenDSS vem com o pacote: `pip install --force-reinstall bdgdcase`.

**A curva sai com nível estranho** — leia a seção *As decisões que a travessia
obriga a tomar*, no [README](../README.md). O fator de coincidência agregada foi
calibrado contra medição de uma distribuidora específica de Santa Catarina, e em
outra região precisa ser reavaliado.
