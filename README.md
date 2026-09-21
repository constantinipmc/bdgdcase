# bdgdcase

**Da BDGD a um caso de simulação OpenDSS.**

A Base de Dados Geográfica da Distribuidora é pública, nacional e de submissão
obrigatória ao regulador. Em princípio, qualquer pessoa fora da distribuidora (pesquisador, prefeitura, associação de consumidores) pode modelar a rede de que
depende. Na prática, a BDGD é um **cadastro de ativos, não um modelo elétrico**:
ela não traz a estrutura de nós, ramos e condições de contorno que um fluxo de
potência exige, e o caminho entre uma coisa e outra quase nunca é documentado.

Este pacote faz essa travessia e **declara as decisões que ela obriga a tomar**.
Decisão não declarada vira hipótese invisível dentro de resultado publicado.

```bash
pip install git+https://github.com/constantinipmc/bdgdcase.git

bdgdcase listar    --gdb BDGD_2024.gdb                          # que alimentadores existem?
bdgdcase extrair   --gdb BDGD_2024.gdb --saida Output --alim TRO05
bdgdcase converter Output/TRO05 --dias DU
```

Saem onze arquivos `.dss` prontos para o OpenDSS, mais as coordenadas dos
barramentos, cinco cadastros auxiliares e o registro do que foi ajustado. Há um
caso completo em
[`src/bdgdcase/exemplo/TRO05_DU/`](src/bdgdcase/exemplo/TRO05_DU) para ver no GitHub sem instalar nada, e
como ele viaja dentro do pacote, `bdgdcase exemplo` roda a coisa inteira logo
depois da instalação, sem baixar a BDGD.

O percurso completo, do download da ANEEL até a curva de carga simulada, está em
**[`docs/GUIA.md`](docs/GUIA.md)**: cinco comandos, com os tempos medidos e as
pegadinhas do caminho. Quem nunca usou Python nem abriu um terminal começa por
**[`docs/PRIMEIROS_PASSOS.md`](docs/PRIMEIROS_PASSOS.md)**, que parte do
computador limpo.



## O que sai

| Arquivo | Vem de | O que é |
|---|---|---|
| `Master.dss` | CTMT, TTEN, UNTRS | circuito, fonte, tensões de base, medidor de cabeceira |
| `LineCodes.dss` | SEGCON | impedâncias por tipo de condutor e número de fases |
| `Linhas_MT.dss` | SSDMT, UNSEMT, UNREMT | segmentos de média, chaves e reguladores |
| `Linhas_BT.dss` | SSDBT, RAMLIG | segmentos de baixa e ramais de ligação |
| `Transformadores.dss` | UNTRMT, EQTRMT | transformadores de distribuição |
| `CurvasDeCarga.dss` | CRVCRG | *loadshapes* de 96 pontos por classe, mais a curva fotovoltaica |
| `Cargas_BT.dss` | UCBT | cargas de baixa, agregadas por poste |
| `Cargas_MT.dss` | UCMT, PIP | cargas de média, por ponto |
| `GD.dss` | UGBT, UGMT | geração distribuída |
| `Capacitores.dss` | UNCRMT | bancos de capacitores |
| `Jumpers.dss` | UNTRMT, SSDBT | as ligações que o cadastro não fecha e a conversão fecha por coordenada |
| `BusCoords.csv` | PONNOT | coordenadas dos barramentos |
| `Cadastro_Cargas.csv` | UCBT, UCMT, PIP | cada unidade consumidora: identificador da BDGD, classe, CNAE, enquadramento tarifário, fases pedidas e ligadas, consumo dos doze meses |
| `Cadastro_Rede.csv` | SSDMT, SSDBT, RAMLIG, UNSEMT | cada trecho e chave: comprimento, instalação, condutor, posição normal de operação |
| `Cadastro_Condutores.csv` | SEGCON | bitola, material, isolação e as duas ampacidades de cada tipo de cabo |
| `Cadastro_Trafos.csv` | UNTRMT | placa, tape, perdas e tensões de cada transformador |
| `Cadastro_GD.csv` | UGBT, UGMT | código CEG, fonte deduzida, potência e fator de capacidade |
| `Ajustes.json` | a própria conversão | o que foi mudado entre o cadastro cru e este caso, e por quê |

O `Jumpers.dss` sai **sempre**, mesmo vazio, e o `Master.dss` só o redireciona
quando há o que redirecionar — um `Redirect` para arquivo vazio muda o caso byte
a byte sem mudar nada elétrico. No caso de referência ele tem só o cabeçalho: o
cadastro do TRO05 fecha sozinho.

Os cinco `Cadastro_*.csv` são a resposta a uma limitação real: **um caso OpenDSS
descreve um circuito, não um cadastro.** `Load.ucbt_com_13610584_2` diz kW, kV e
a curva; não diz que unidade é aquela nem de que bitola é o cabo que a alimenta.
Eles ficam ao lado dos `.dss` e não dentro deles porque o OpenDSS descarta
comentários: nada disso voltaria pela API de quem lê o caso compilado.

> **Atenção.** Quando a extração inclui CEP e CNAE, esses arquivos contêm dado
> identificável, e o aviso está na primeira linha de cada um. O número da conta
> do cliente (`DESCR`) **nunca** entra em nenhum deles. Revise a pasta antes de
> publicá-la.

**O ciclo fecha dentro do pacote.** Escrever o caso e resolvê-lo vêm juntos:

```bash
bdgdcase verificar OpenDSS/TRO05_DU            # o caso compila e converge?
bdgdcase rodar     OpenDSS/TRO05_DU --csv dia.csv   # o dia em 96 passos
```

`verificar` é uma pergunta estrutural: o modelo fecha? `rodar` resolve o dia em
passos de 15 minutos e devolve potência no ponto de entrega, perdas e faixa de
tensão. Para qualquer outra grandeza, [`docs/RESULTADOS.md`](docs/RESULTADOS.md)
mostra como chegar nela a partir do circuito já resolvido.

## Escolher o alimentador pelo mapa

```bash
bdgdcase gui      # varra a base e veja a aba "Alimentadores no mapa"
```

`bdgdcase listar` responde *quais* alimentadores a base tem — oitocentos
códigos numa distribuidora estadual. O código é a chave que a extração exige, e
não diz **onde** o alimentador passa: descobrir isso extraindo custa minutos por
tentativa.

A aba desenha todos de uma vez sobre o mapa base, cada um com a sua cor. Clicar
num deles o realça e mostra código, nome, tensão, quilômetros de rede e energia
do ano — e o marca na lista da aba de preparar, de onde o **Executar** segue
como sempre seguiu. Ctrl+clique acumula; clique no vazio desmarca; digitar o
código na busca aproxima a vista nele.

O traçado sai de `SSDMT`, a média tensão, porque `CTMT` é catálogo e não tem
geometria nenhuma. A BDGD publica essa rede em vãos de poste a poste — cerca de
1,3 milhão numa base estadual, que não se desenha. O mapa **costura** os vãos
que se tocam num traço contínuo por ramo e descarta os vértices que não mudam o
desenho. Medido na fixture: 1.611 vãos viram 183 traços de 484 vértices, com
0,02% do comprimento perdido.

O traço fica **inteiro**; o que não fica é a posição exata de cada poste. É um
mapa de **localização**, para escolher — a rede de verdade está na aba do mapa,
depois de converter o caso.

Sobre satélite ou híbrido, os traços ganham um contorno escuro por baixo: sem
ele, uma linha fina em cor média desaparece na imagem, e o mapa parece ter
perdido os alimentadores ao trocar o fundo.

Ler a geometria da base inteira leva alguns minutos, contra os vinte segundos da
varredura. Paga-se uma vez: o resultado fica em cache no temporário do sistema,
e a segunda abertura da mesma base é imediata. Para pagar antes, sem janela:

```bash
bdgdcase panorama --gdb BDGD_2024.gdb
```

Quem já sabe o código que quer pode desmarcar "Desenhar também os alimentadores
no mapa" e ficar só com a lista.

## Ver o alimentador no mapa

```bash
bdgdcase mapa OpenDSS/TRO05_DU
```

As séries dizem *como foi* o dia; o mapa diz **onde**. Uma tensão mínima de
0,93 pu pode ser um ramal ruim ou meio bairro afundando, e a diferença muda o
que se faz a respeito.

Cada barra no seu lugar geográfico, colorida pela **faixa de tensão do PRODIST
Módulo 8**: verde adequada, amarela precária, vermelha crítica. Os cabos ficam
na cor do seu nível, média e baixa, porque identificam e não medem: a medida
está nas barras, e uma segunda escala de cor ali competiria com ela. O
carregamento de cada trecho aparece no painel, com a ampacidade ao lado, e a
contagem dos que passam dela fica na barra de status. Uma barra deslizante de 96 posições percorre o
dia de 15 em 15 minutos, com animação: dá para ver a ponta da noite chegar pelo
alimentador. Clicando numa barra, saem as tensões fase a fase, o desequilíbrio e
a corrente de cada trecho ligado a ela.

A navegação é de mapa, não de gráfico: **roda amplia** em torno do cursor,
**arrastar move**, **clicar seleciona**. Sem escolher ferramenta antes: a barra
do matplotlib continua ali para o zoom por retângulo e para voltar ao
enquadramento inicial.

As **camadas** são a legenda no canto superior esquerdo do mapa, cada uma com o
seu símbolo ao lado do nome: cabos MT em vermelho, cabos BT em azul,
transformadores, chaves, **capacitores**, **reguladores de tensão**, GD, e as
**barras de média e de baixa em camadas separadas**. A caixa liga e desliga; **clicando no símbolo, troca-se a cor**.
Num alimentador de dezesseis mil barras, marcador demais vira mancha: então
chaves vêm desligadas, e quem precisa liga.

Ao lado de cada camada vai **quanto há dela** — cabo em quilômetros, o resto em
unidades —, e uma linha cinza fecha a legenda com as barras **sem tensão** no
instante em que o mapa está. A quantidade fica na legenda e não num painel à
parte porque é olhando a legenda que se pergunta quanto daquilo existe ali:
depois do enquadramento, um alimentador de 3 km e um de 127 km se parecem, e a
diferença só aparece se estiver escrita.

Separar as barras por nível resolve uma sobreposição real: num trecho urbano, o
poste de baixa e o primário do transformador ficam a metros um do outro e no
mapa viram o mesmo ponto. Desligando a média, vê-se a baixa por baixo dela: e
o clique acompanha, nunca selecionando uma barra de camada desligada. As de
média são desenhadas maiores, que é como se distinguem quando as duas estão
ligadas: a cor pertence à faixa de tensão, não ao nível.

Capacitor e regulador entram no fluxo de potência como qualquer outro
equipamento: o banco injeta reativo, e o regulador comuta tape ao longo do dia,
porque a simulação roda em `ControlMode=Time` e não congelada num instante. A
ficha do regulador traz **as tensões de entrada e de saída fase a fase no
instante mostrado**, o ganho que ele entregou, **o tape em que cada fase está
naquele instante**: com a curva do tape ao longo do dia, que é o único estado
do caso que muda por decisão do próprio elemento: e **em que modo ele opera
quando o fluxo inverte**. A do capacitor traz a potência reativa e se ele entrou
no caso ligado: o conversor deixa os bancos de subestação desligados por
padrão, já que ligados em vazio distorcem a madrugada.

> No OpenDSS um regulador é um `Transformer` com um `RegControl`, e um banco
> trifásico são **três** desses. O inventário os separa perguntando ao
> `RegControl` de quem ele cuida (não pelo nome do elemento), e os agrupa por
> par de barras: dois reguladores contam como dois equipamentos, e não seis
> transformadores de distribuição.

O comportamento em fluxo reverso é escolha de quem converte, e muda o resultado:
um regulador que vai a neutro deixa de influir na tensão, e outro que continua
regulando não. São quatro modos, em `bdgdcase converter --regulador-reverso`:

| modo | o que faz quando o fluxo inverte |
|---|---|
| `neutro` *(padrão)* | o tape vai à posição neutra e o regulador para de atuar |
| `cogeracao` | continua regulando a jusante, com os setpoints reversos |
| `bidirecional` | regula também no sentido reverso; pode oscilar |
| `direto` | ignora o sentido do fluxo |

O painel **lê o modo do próprio caso compilado**, e não de uma configuração que
só quem converteu conhecia: quem abre uma pasta pronta precisa poder saber com
que hipótese ela foi gerada.

Clicar no **secundário de um transformador realça o circuito de baixa que ele
alimenta**, com um halo por baixo do cabo: que continua visível qualquer que
seja a cor escolhida para a camada, e o painel conta quantas barras, trechos e unidades são, com
o carregamento nominal sobre os kVA da máquina. O caminhamento anda só por
trechos de baixa, de modo que para sozinho no primário do transformador
seguinte: o realce é exatamente a zona daquele equipamento.

Sobre **fundo de satélite, híbrido, claro ou de ruas**: os azulejos vêm da
Esri e do OpenStreetMap, sem biblioteca de mapas: é aritmética de Mercator e um
`urlopen`, e a costura sai do Pillow que já vem com o matplotlib. Sem internet,
o fundo simplesmente não aparece e o resto funciona igual.

Dá para **ampliar além do que o provedor tem**. Pedir um zoom sem cobertura não
dá erro: a Esri devolve uma imagem cinza escrita *"Map data not yet available"*,
e como imagem válida ela atravessa qualquer verificação e vai para a tela. O
mapa reconhece esse azulejo pela repetição: é o mesmo para qualquer
coordenada, e dois azulejos de satélite distintos nunca saem byte a byte iguais, e desce um nível até achar imagem de verdade. O resultado fica borrado, e é o
que se quer: borrado e verdadeiro é melhor que nítido e falso, e muito melhor
que um aviso cinza no lugar do bairro.

O reconhecimento é por comparação, e não por conhecer a imagem de antemão, de
modo que continua valendo se a Esri trocá-la ou se alguém acrescentar outro
provedor. O teto descoberto fica guardado por região, para não repetir a
sondagem a cada movimento do mapa.

O mapa **desenha em Web Mercator**, e não em graus. É a projeção dos azulejos:
plotar a rede nela faz cada poste assentar sobre a imagem em qualquer
ampliação, e o eixo fica em aspecto 1:1 sem correção. Em graus a colocação é
aproximada, e a aproximação aparece justamente no zoom, quando se quer olhar de
perto.

Clicar num ponto abre **o que existe ali**: o transformador e seus kVA, as
unidades consumidoras com classe, tensão, curva e demanda nominal de cada uma, a
geração instalada, e a corrente de cada trecho ligado. É a pergunta que o mapa
faz nascer: uma barra em subtensão atende cinco casas ou um supermercado?

E daí se desce. Cada unidade, transformador e gerador é um **link**: abrindo um
deles, o painel troca pela ficha do elemento, com uma trilha no topo para
voltar. A ficha da unidade traz o identificador da BDGD, o poste, a classe e a
subclasse, o CNAE, o enquadramento tarifário, o consumo de cada um dos doze
meses, e **as fases que ela pede contra as que de fato tem**: quando diferem, é
porque a unidade estava ligada a uma fase que não existe no ponto de derivação,
e o conversor ajustou. A do transformador traz placa, tape, perdas, o alcance do
circuito de baixa e cada unidade que ele atende. A da geração traz o código CEG
e a fonte, que a BDGD não informa e o conversor deduz do fator de capacidade.

Em todas elas, **a curva daquele dia** aparece embutida no painel, com o
instante atual marcado. Ela é a placa vezes a forma da curva da classe: que é o
que o cadastro afirma, e não o que o solver serviu: as cargas são de impedância
parcialmente constante, de modo que a potência acompanha a tensão. No
alimentador de exemplo a diferença do pico fica em torno de 0,6%, mas cresce
onde a tensão cai, que é justamente onde se olha. Para ver a potência servida de
verdade, abra com `bdgdcase mapa --medir-cargas`; custa cerca do dobro do tempo,
e o rótulo do gráfico passa a dizer *medida no fluxo de potência*. É opção de
abertura e não botão na janela porque trocá-la obriga a resolver o dia de novo.

Vale lembrar o que essa curva é: a forma vem da CRVCRG da BDGD, que é
**regulatória e por classe**: uma curva para toda a concessionária. Não é a
curva daquela casa, e o painel diz isso.

A aba **Resumo do alimentador** traz o inventário completo: barras, extensão,
transformadores e kVA, unidades por classe, GD instalada sobre a capacidade dos
trafos: e o dia em três painéis: potência no ponto de entrega, perdas e faixa
de tensão.

### As faixas de tensão

A cor das barras segue o **PRODIST Módulo 8, Anexo 8.A**, e a régua é a do nível
de cada barra: que não é a mesma:

| | crítica | precária | adequada | precária | crítica |
|---|---|---|---|---|---|
| **média**, Tabela 3 (2,3 kV < V < 69 kV) | < 0,90 | 0,90–0,93 | 0,93–1,05 | (n/a) | > 1,05 |
| **baixa**, Tabela 5 (380/220 V) | < 331 V | 331–350 V | 350–399 V | 399–403 V | > 403 V |
| *a mesma linha, em pu de 380 V* | < 0,871 | 0,871–0,921 | 0,921–1,050 | 1,050–1,061 | > 1,061 |

Três coisas nessa tabela costumam passar despercebidas, e as três mudam o
resultado:

1. **A baixa tem faixa precária acima da adequada, e a média não.** Uma barra a
   1,055 pu é precária em baixa e crítica em média. Num alimentador real de
   23,1 kV, isso reclassificou 122 leituras ao longo do dia.
2. **A norma dá os limites de baixa em volts, não em pu**: e uma tabela por
   tensão nominal (220/127, 440/220, 208/120…). Usa-se a de 380/220 por ser o
   sistema predominante; as diferenças entre elas ficam na terceira casa.
3. **O corte entre as tabelas é 2,3 kV**, não 1 kV: a Tabela 3 vale para
   "tensão nominal superior a 2,3 kV e inferior a 69 kV".

A faixa de uma barra é a **pior entre as suas fases**, e por isso olha as duas
pontas: a mais baixa acusa subtensão e a mais alta acusa sobretensão. Reduzir a
barra só à fase mais baixa (que é o natural, e era o que se fazia) deixa passar
uma fase acima do limite sempre que outra esteja dentro da faixa. Uma barra com
1,0513 / 1,0019 / 1,0019 pu sairia "adequada" com a primeira fase já precária, e
o desequilíbrio é justamente o que empurra uma fase para cima: o caso não é raro,
é o esperado. No painel, **cada fase é colorida pela sua própria faixa**.

A distinção entre os níveis importa em três janelas: **0,925 pu é adequada em
baixa e precária em média**, 0,885 é precária em baixa e crítica em média, e
1,055 é precária em baixa e crítica em média. Julgar o alimentador inteiro por
uma tabela só (o que é fácil de fazer sem perceber) pinta de amarelo um bairro
de baixa que a norma considera em ordem. O nível vem da tensão de base de cada
barramento.

São três cores e não um degradê porque a pergunta tem três respostas. Um
gradiente convida a ler diferença onde a norma não vê nenhuma, 1,00 e 1,04 pu
são a mesma coisa para ela: e apaga o degrau que interessa, que é a passagem de
uma faixa para a outra.

Uma ressalva que o painel repete a cada barra: isto classifica **um instante**.
O enquadramento da norma se apura sobre uma janela de leituras, com indicadores
próprios, e não sobre uma foto.

Barra sem tensão aparece em **cinza**, não em vermelho: trecho desligado no
cadastro não é subtensão, e confundir os dois inventaria um problema que não
existe.



## Instalação

```bash
pip install "bdgdcase @ https://github.com/constantinipmc/bdgdcase/archive/refs/heads/main.zip"
```

Esta forma não exige git instalado: o pip baixa o `.zip` do GitHub direto. A
partir de um clone, `pip install .`.

**Um comando, e está tudo lá** — extrair, converter, resolver e ver no mapa. Não
há extra a lembrar, nem subcomando que falhe depois da instalação por faltar uma
peça.

Confira a instalação sem baixar dado nenhum:

```bash
bdgdcase exemplo
```

O pacote leva dentro de si um alimentador real já convertido (153 KB), e este
comando o resolve na hora. Instalação que só dá para testar depois de baixar
gigabytes é instalação que ninguém confirma.

Requer Python 3.9 ou mais novo. As dependências são de dados geoespaciais
(`geopandas`, `pyogrio`, `shapely`, `pyproj`), `numpy` e `pandas` para o
cálculo, `OpenDSSDirect.py` para resolver e `matplotlib` para o mapa.

Tkinter não se instala por `pip`: vem junto com o Python no Windows e no macOS,
e no Debian/Ubuntu sai de `apt install python3-tk`. Sem ele, **a biblioteca
continua funcionando**: só `bdgdcase gui` é que recusa, com mensagem explícita.

## Se preferir uma janela

```bash
bdgdcase gui
```

Uma janela só, que percorre a cadeia inteira: escolher a base, varrer os
alimentadores, escolher — na lista ou clicando no mapa, ver acima —, e marcar
até onde ir: extrair, gerar o caso, verificar, rodar o dia. O trabalho corre numa thread, com registro e
barra de progresso, e ao fim dá para abrir a pasta ou salvar o CSV do dia.

Ela resolve sozinha a pegadinha do `.gdb` aninhado descrita em
[`docs/GUIA.md`](docs/GUIA.md), e avisa quando o faz.

A linha de comando continua sendo a interface primária, e é ela que o resto
desta documentação descreve: automatizar, versionar e reproduzir é mais fácil
com comandos do que com cliques.



## Uso

### Linha de comando

```bash
# 1. Do .gdb da BDGD para as tabelas de um alimentador
bdgdcase extrair --gdb BDGD_2024.gdb --saida Output --alim TRO05 IBA09

# 2. Das tabelas para o caso OpenDSS
bdgdcase converter Output/TRO05 --dias DU SA DO

# O que se sabe de cada distribuidora já vista
bdgdcase distribuidoras
bdgdcase distribuidoras --dist 396

# Onde fica cada alimentador da base (aquece o cache do mapa da janela)
bdgdcase panorama --gdb BDGD_2024.gdb

# Onde o pacote está procurando e escrevendo as coisas
bdgdcase caminhos
```

Se `bdgdcase` não estiver no `PATH` — comum logo depois de instalar —, todos os
comandos funcionam igual com `python -m bdgdcase`:

```bash
python -m bdgdcase gui
python -m bdgdcase converter Output/TRO05
```

`python gui` não funciona, e a mensagem de erro não ajuda: o Python procura um
**arquivo** chamado `gui` no diretório atual. `gui` é subcomando, não script.

### Python

```python
from bdgdcase.api import extrair, converter

extrair('BDGD_2024.gdb', 'Output', ['TRO05'])
erros = converter('Output/TRO05', dias=('DU',))
assert not erros
```

### Diretórios

O pacote resolve os caminhos na importação, nesta ordem:

1. a variável de ambiente `BDGD_BASE_DIR`;
2. um `bdgdcase.json` na raiz do diretório base;
3. o diretório de trabalho corrente.

```json
{ "input_dir": "input", "output_dir": "Output", "opendss_dir": "OpenDSS" }
```

A saída da conversão é `OpenDSS/{ALIMENTADOR}_{DIA}`, ao lado da pasta-mãe de
`Output`: arranjo herdado do projeto de origem, preservado para não quebrar
quem já o usa.



## Como o código está organizado

Três pastas, na ordem em que o trabalho acontece — e a fronteira entre elas é
uma dependência que anda num sentido só.

```
src/bdgdcase/
├─ extracao/     o .gdb da ANEEL  →  as tabelas de um alimentador
│  ├─ motor.py       a ordem em que o recorte acontece
│  ├─ camadas.py     quem é cabo, trafo, unidade consumidora, catálogo
│  ├─ catalogos.py   os códigos da norma, e o que eles querem dizer
│  └─ agregacao.py   as UCs somadas por poste, com as LISTA_ posicionais
│
├─ modelo/       as tabelas  →  o caso OpenDSS
│  ├─ config.py       as premissas, com a procedência de cada uma
│  ├─ registro.py     o relato no terminal e o placar da conversão
│  ├─ cadastro.py     ler a ficha da BDGD sem acreditar nela
│  ├─ curvas.py       quanto cada carga consome, a cada 15 minutos
│  ├─ topologia.py    quem se liga a quem, e por onde entra a energia
│  ├─ coordenadas.py  onde cada barra fica no mapa
│  ├─ rede.py         trechos, transformadores, capacitores, jumpers
│  ├─ cargas.py       BT, MT e iluminação pública
│  ├─ geracao.py      a geração distribuída, que não é só fotovoltaica
│  ├─ cadastro_csv.py a ficha de cada elemento, ao lado do caso
│  ├─ master.py       o Master.dss e as bases de tensão
│  └─ pipeline.py     a ordem, e o laço por alimentador e por dia
│
├─ interface/    como se conversa com o pacote
│  ├─ cli.py         a linha de comando — a interface primária
│  ├─ janela.py      a janela, e a composição das abas
│  ├─ preparar.py    a aba que percorre a cadeia inteira
│  ├─ geometria.py   ler um caso resolvido e responder perguntas sobre ele
│  ├─ mapa.py        o desenho, e a navegação sobre ele
│  ├─ inspetor.py    o painel do ponto, com a procedência de cada número
│  ├─ resumo.py      o alimentador inteiro, e não um ponto dele
│  ├─ registro.py    a aba dos ajustes do cadastro
│  ├─ base.py        os imports adiados, a fonte e o tema
│  └─ azulejos.py    o fundo do mapa
│
├─ api.py            a API pública: listar, extrair, converter
├─ solucao.py        roda o caso no OpenDSS
├─ ajustes.py        o registro do que a conversão ajustou
├─ caminhos.py       onde ficam as pastas
├─ distribuidoras/   o dossiê de cada base já vista
└─ dados/  exemplo/  o catálogo de curvas e o caso de referência
```

**A dependência nunca volta.** `extracao` não sabe o que é um `.dss`; `modelo`
não sabe o que é uma janela — e há teste que exige isso, num Python cego para
`tkinter`. É o que permite converter em lote num contêiner sem servidor gráfico,
que é onde a conversão em lote faz mais sentido rodar.

**`solucao.py` fica na raiz de propósito.** Escrever um caso e resolvê-lo são
duas perguntas, e a segunda não pertence a nenhuma das três pastas: `modelo/`
produz texto e não sabe o que é um fluxo de potência. Manter a fronteira é o que
faz um erro de conversão não chegar disfarçado de erro de convergência.
`ajustes.py`, `caminhos.py` e `distribuidoras/` ficam na raiz porque as três
pastas os usam.

**Tudo em português** — pastas, funções, comentários e commits. A BDGD é
brasileira, a norma que ela implementa é o PRODIST, e quem vai ler este código
lê os dois em português.

## Dados

A BDGD completa de uma distribuidora tem dezenas de gigabytes e **não está neste
repositório**. O download é público, por distribuidora e ano, no portal de dados
abertos da ANEEL:

> <https://dadosabertos.aneel.gov.br/> → *Base de Dados Geográfica da
> Distribuidora (BDGD)*

Baixe o `.gdb` da distribuidora que lhe interessa e aponte `--gdb` para ele.

Para rodar os testes sem baixar nada, o repositório traz em
[`tests/dados/`](tests/dados) fatias reais da BDGD pública — **uma por
distribuidora**, com a topologia intacta e os catálogos de equipamento reduzidos
ao que cada alimentador de fato cita. Nenhuma é inventada, e a redução é
verificada: o caso gerado a partir da fixture sai byte a byte igual ao gerado a
partir do alimentador completo.

Três distribuidoras, e não uma, porque o que separa uma base da outra não é o
esquema — as geodatabases têm as mesmas 43 camadas, os mesmos campos e o mesmo
CRS — e sim o conteúdo. Um conversor calibrado numa base tropeça na seguinte
**sem levantar exceção**: metade da baixa de uma delas é 220/127 em vez de
380/220, e outra traz a camada de curvas de carga com os 101 campos e zero
feições. Nos dois casos o `.dss` sai, compila e resolve.

Foram **quatro** as bases medidas — Celesc, Coopera, RGE e Copel, cada uma com
um dossiê em [`src/bdgdcase/distribuidoras/`](src/bdgdcase/distribuidoras) —
e três viraram fixture. Onde este documento diz "três bases medidas", a
medição é anterior à quarta, e o número é o que se mediu.


### Quando a sua base não traz curvas de carga

O conversor lê as curvas de carga diárias da camada `CRVCRG` da própria BDGD que
você baixou. **Nem toda base a traz preenchida.** Numa das distribuidoras
testadas a camada existe, com os 101 campos, e zero feições — e não há como
adivinhar isso pelo tamanho do arquivo.

Nesse caso o pacote usa um **catálogo de referência embarcado**, em
`src/bdgdcase/dados/curvas_referencia.csv`: 61 códigos de tipologia × 3 tipos de
dia × 96 patamares de 15 min, extraídos da `CRVCRG` de uma BDGD pública que a
traz completa. É dado aberto da ANEEL, redistribuído com atribuição, gerado por
`scripts/fazer_curvas_referencia.py` e nunca editado à mão.

**O que isso significa para o seu resultado.** A *forma* do dia — o horário do
pico, a profundidade da madrugada — passa a ser a de outra distribuidora. A
*energia* de cada carga continua vindo do cadastro da sua: o `kWh` mensal, a
classe, a quantidade de unidades por poste, tudo isso é seu. Um estudo de perfil
horário sobre um caso assim é estimativa, e deve ser lido como tal.

**Como saber em qual situação o seu caso está**, sem precisar confiar na memória:

| onde | o que aparece |
|---|---|
| console, durante a conversão | `[AVISO] CRVCRG ausente ou vazia nesta base` e a procedência |
| `CurvasDeCarga.dss`, cabeçalho | bloco `ATENÇÃO` dizendo que as curvas não são desta distribuidora |
| `Cadastro_Cargas.csv`, coluna `curva_origem` | `catalogo`, `referencia` ou `generico`, por carga |

`catalogo` é a sua própria base. `referencia` é o catálogo embarcado.
`generico` é o último recurso — quatro curvas desenhadas à mão, que aparecem só
se o catálogo embarcado também estiver ausente, e que ninguém mediu.

Para gerar o catálogo a partir de outra base:

```bash
python scripts/fazer_curvas_referencia.py CAMINHO/DA/BASE.gdb
python scripts/fazer_curvas_referencia.py CAMINHO/DA/BASE.gdb --conferir
```



### Quando `POT_INST` da geração não é potência instalada

O campo `POT_INST` de `UGBT`/`UGMT` deveria trazer a potência instalada da
unidade geradora, e em duas das três bases medidas traz. Na terceira, não:
`POT_INST × 720` é **exatamente** o maior dos doze valores mensais de energia,
em 96,4% das unidades de baixa e com erro mediano de 0,0 kWh. O campo carrega a
potência **média do mês de maior geração**.

O efeito é grande e silencioso: uma unidade de 5 kW entra no caso com 0,7 —
cerca de sete vezes menos, que é o inverso do fator de capacidade. Num estudo de
hospedagem ou de fluxo reverso, isso não é detalhe, é o resultado.

O conversor **detecta pela identidade `× 720`, linha a linha**, e não pela
distribuidora: a tabela de média de uma das bases é mista, com 112,5 e 300 kW ao
lado de 7,890278. O teste não deu nenhum falso positivo nas 124.778 unidades das
outras duas bases.

**A volta é estimativa**, e está declarada como tal. A potência instalada sai
dividindo a energia injetada no ano pelo rendimento anual medido nas bases que
trazem os dois números — 725 kWh por kW instalado, mediana de 121.092
unidades. Pelo ano, e não pelo maior mês: um quinto das unidades tem leitura
bimestral ou trimestral, e o máximo mensal delas é acumulado, não energia de
um mês. O valor recuperado não é o do cadastro original: é a melhor
reconstrução possível a partir do que a base publicou. Quem precisar do número
exato tem de pedi-lo à distribuidora.

A coluna `pot_origem` do `Cadastro_GD.csv` diz, por unidade, se a potência veio
do `cadastro` ou foi `estimada`, e a conversão avisa no console quantas foram.
Cerca de 1% das unidades injetou tão pouco energia no ano que a reconstrução sai
abaixo de 0,5 kW; ficam assim, porque inventar um piso seria inventar dado.


### O mesmo defeito na carga instalada, e na iluminação pública

O `POT_INST` da geração não está sozinho. Na mesma base:

- **`CAR_INST` das unidades consumidoras** segue a identidade `× 720` em 92,0%
  das de baixa e 54,6% das de média — mediana de 0,37 kW por unidade, contra
  9,00 e 10,47 nas outras duas bases. Pesa menos que o da geração porque a
  potência da carga no `.dss` vem do histórico de energia; o campo só entra
  quando não há histórico (1,4% das unidades). Nas demais, o efeito é sobre o
  que se lê no cadastro e no painel do mapa.

- **`CAR_INST` do ponto de iluminação pública** vem **dez vezes maior**: 1,069 kW
  por luminária, contra 0,103. Como a carga de IP é a soma dos pontos do
  transformador, o erro entra multiplicado — num alimentador medido, 1.324 kW
  de iluminação onde há 132.

Nos dois casos quem denuncia é a energia, e a correção é **por unidade**, nunca
pela tabela: elas são mistas, com o artefato ao lado de valores corretos. Um
fator aplicado à tabela inteira pôs 1.018 kW num ramal residencial e derrubou um
alimentador para 0,58 pu — está no histórico como lição.

A referência é medida e **diferente por nível de tensão**: 29,5 kWh no mês de
pico por kW instalado na baixa, 88,7 na média, e 354 horas acesas por mês na
iluminação. A conversão diz no console quantas unidades repôs.


## O que foi ajustado, e onde ver

A BDGD publicada não fecha um fluxo de potência como está. Entre ela e um caso
que converge há dezenas de decisões — um campo que traz outra grandeza e é
reposto na escala, um nó sem coordenada que herda a do vizinho, uma curva que a
base não trouxe e vem de outro lugar.

A conversão **registra o que faz**, em `Ajustes.json`, dentro da pasta do caso.
A pasta viaja com a explicação do que há nela, e a janela do mapa mostra isso
numa aba própria, agrupado por família e ordenado pelo que mais importa saber:

| efeito | o que quer dizer |
|---|---|
| **muda o resultado** | altera o fluxo de potência |
| **não corrigido** | o conversor encontrou e não consertou, de propósito |
| muda o desenho | altera o que o mapa consegue mostrar |
| muda a ficha | altera o que se lê no painel, não o que a rede faz |

Cada linha diz **quantos** elementos atingiu, **o que** foi feito e **por quê**.
A categoria *não corrigido* existe porque é a mais honesta das quatro: é onde o
resultado depende de um dado que a base publicou errado e o conversor não tem
como resolver sem inventar.

Sem esse registro, o `.dss` tem a mesma aparência de autoridade quer o cadastro
esteja completo, quer tenha sido remendado em quinze lugares.


## Uma distribuidora de cada vez, e o que cada uma ensinou

O conversor **não** ramifica por distribuidora, e isso foi decidido depois de
tentar. As quatro geodatabases medidas têm as mesmas 43 camadas, os mesmos
campos e o mesmo CRS: o que muda é o conteúdo. Toda vez que uma correção foi
escrita como *regra sobre o dado*, serviu a todas de uma vez; toda vez que foi
escrita como *convenção de uma delas*, quebrou na seguinte.

O que existe por distribuidora é o **dossiê**, em
[`src/bdgdcase/distribuidoras/`](src/bdgdcase/distribuidoras): um arquivo por
base, com o número medido ao lado de cada afirmação e a decisão que ela induziu.
Cada perfil declara o que se espera encontrar, e a conversão confere o
alimentador contra isso — uma base que se afaste do documentado é dita em voz
alta, em vez de sair num `.dss` plausível e errado.

```bash
bdgdcase distribuidoras            # o que está registrado
bdgdcase distribuidoras --dist 396 # o dossiê de uma delas
```

Uma distribuidora que não esteja registrada **converte igual**. O que falta sem
perfil é a conferência, porque não há expectativa com que comparar.

## As decisões que a travessia obriga a tomar

Esta é a seção que justifica o pacote existir. Nenhum dos itens abaixo está na
BDGD; todos precisam ser decididos por quem converte, e a maioria das
ferramentas do gênero decide em silêncio.

### 1. A fonte da geração não consta da base

`TIP_GER` não existe nas tabelas de unidades geradoras. `TIP_SIST` vale
`RD_INTERLIG` em 100% dos registros. E o prefixo `GD.` do código CEG significa
*micro ou minigeração distribuída*: enquadramento regulatório, **não fonte**.
Uma CGH enquadrada como minigeração recebe `GD.…` e, sem mais nenhum sinal,
seria modelada como fotovoltaica.

A fonte é recuperada pelo fator de capacidade anual, que a base permite calcular
a partir da energia injetada mês a mês e da potência instalada:

```
FC = (ENE_01 + ENE_02 + ... + ENE_12) / (POT_INST * 8760)
```

A separação é inequívoca. Medido na BDGD Celesc 2024 (medianas): geração com
prefixo `GD.` fica em 8,2%, e o teto físico do fotovoltaico em Santa Catarina
ronda 20%; as hidráulicas ficam entre 43% e 47%, e a térmica em 61,9%. O
critério adotado é **FC > 25% ⇒ não é fotovoltaica**: conservador, com folga
para os dois lados.

Ressalva honesta: FC alto também pode ser erro de cadastro (potência instalada
subdimensionada, energia trocada de coluna). Nos dois casos o registro é
impróprio para representar um telhado solar, e é modelado como geração de base.

### 2. O cadastro real não converge sem tratamento

Fases mortas, ramais órfãos e unidades ligadas a fases que não existem no ponto
de derivação são comuns e travam o fluxo de potência. A autocorreção usa
**confirmação topológica** pela árvore do medidor de cabeceira: tensão baixa,
isoladamente, não caracteriza ilhamento. E carrega uma salvaguarda: se mais de
60% das unidades geradoras forem classificadas como ilhadas, o diagnóstico é que
está errado, e tudo é reabilitado.

### 3. A demanda vem da energia faturada, não da carga instalada

A BDGD traz as duas coisas, e a escolha entre elas muda o resultado por um fator
de quatro. `CAR_INST` é **carga instalada**: a soma das placas do que existe
ligado na unidade. Ela não é demanda, e transformá-la em demanda por um fator
plano é o caminho ingênuo.

O caminho usado aqui é o histórico de faturação, `ENE_01` a `ENE_12`:

```
kW_pico = (Σ ENE_mm / meses_válidos) / (730 h × fator_carga) × FC_MACROCOPICO
```

`fator_carga` é a **média em pu da curva CRVCRG oficial** da classe daquela
unidade: os 96 patamares da própria base, não um número escolhido. Dividir a
energia média mensal pelo fator de carga da curva é o que converte consumo em
potência de ponta de forma consistente com o formato do dia.

`CAR_INST × FATOR_DEMANDA` sobrou como **fallback**, para unidade sem histórico.
Medido: 0,7% das unidades no alimentador IBA09, e nenhuma no TRO05.

A diferença entre os dois caminhos, no IBA09: o histórico de energia dá cerca de
**6,1 MW** de ponta; a carga instalada com fator de demanda daria **24,7 MW**,
4,1 vezes mais. Um modelo construído pelo caminho ingênuo não erra um detalhe —
erra o alimentador.

O modelo de carga segue o PRODIST módulo 7 (REN 956/2021): 50% potência
constante e 50% impedância constante no ativo, 100% impedância constante no
reativo.

> **`FC_MACROCOPICO = 0.86` é calibrado, não universal.** Ele multiplica os dois
> caminhos, e veio de comparação com medição SCADA em alimentadores de uma
> distribuidora de Santa Catarina. Em outra distribuidora, outra região ou outro
> perfil de consumo, precisa ser reavaliado. Está em `modelo/config.py`, com
> o porquê ao lado. O mesmo vale para `APLICAR_CORRECAO_CRVCRG_K`, uma correção
> horária empírica que vem **desligada** justamente por não ser transferível.

### 4. Agregação por poste

As unidades consumidoras de baixa tensão são agregadas no poste. No alimentador
IBA09, 11.928 unidades viram 3.168 barras: uma redução de 3,8×, com o desenho
elétrico preservado. É o que torna a simulação diária tratável.

### 5. O transformador que não alcança o próprio circuito

O `PAC_2` de um transformador nem sempre é ponta de condutor nenhum: o
secundário fica sem um único cabo de baixa ligado, e o transformador não alcança
as unidades que a própria BDGD diz que ele alimenta. Elas ficam sem tensão o dia
inteiro, e o alimentador aparece com um pedaço morto que não existe na rua.

A conversão liga esse secundário ao ponto de baixa do mesmo poste, a até 5 m —
medido: quando o cadastro separa o transformador do condutor que sai dele,
separa por 1,6 a 2,0 m, que é precisão de desenho e não distância real. A
ligação vai para o `Jumpers.dss` e **não tem interruptor**: não há premissa a
escolher aí, há um elo faltando e o dado para fechá-lo está na base.

Os nós vêm de quem os escreveu. Um secundário em derivação central é emitido em
`.1.3` e o circuito abaixo dele pode estar em `.1.2`; a ligação é **posicional**
— primeira perna com primeiro condutor. Casar por identidade deixaria um nó sem
alimentação, flutuando, e a solução vai a 9,2e95 pu em vez de acusar erro.

Em dois casos a conversão **não** religa, e diz no `Ajustes.json` quais e por
quê: quando o circuito do poste declara condutores que aquele transformador não
alimenta (energizar um e deixar os outros pendurados levou uma barra a 3,42 pu),
e quando o poste mais próximo é o secundário de outro transformador (ligar ali
põe os dois em paralelo, e um center-tap de 127 V por perna recebendo 220 V do
trifásico ao lado levou a rede a 1,704 pu). Nos dois, a inconsistência está no
cadastro e a base não diz qual lado está certo.

Medido nos 30 alimentadores extraídos das quatro bases: 122 religações
aplicadas, zero passos não convergidos no dia de 96 passos.

### 6. Uma verificação de fechamento

Vale a pena somar a energia faturada em baixa tensão, acrescentar as perdas
técnicas e comparar com o que a simulação entrega no ponto de entrega. No IBA09:
85,2 MWh/dia faturados + 4,7 de perdas = 89,9 esperados, contra 90,4 simulados —
**+0,6%**.

E, com o mesmo destaque, **o que essa verificação não demonstra**: ela não valida
a forma da curva ao longo do dia, não valida a repartição espacial da carga, e
não é independente em sentido estrito, já que simulação e expectativa partem do
mesmo cadastro.



## Reprodutibilidade

Mesma BDGD, mesmos bytes de saída. A curva fotovoltaica é sintetizada com ruído
estocástico, e aqui a semente é **fixa** (`PV_BETA_SEED`): sem isso não há teste
de regressão possível nem resultado que outra pessoa consiga reproduzir.

Esta é uma diferença deliberada em relação ao projeto de origem, onde a semente é
`None` porque lá o ruído é justamente o que se quer, ao amostrar cenários de
Monte Carlo. Para o comportamento antigo, passe `seed=None` a
`gerar_curva_pv_beta`.

Pelo mesmo motivo, os arquivos saem sempre com fim de linha **LF**, e não com o
da plataforma. O OpenDSS lê os dois; um teste de regressão, não: com fim de
linha da plataforma, ele passaria na máquina de quem gerou o caso e falharia em
todas as outras.



## O que este pacote **não** faz

- **Não faz Monte Carlo** nem varredura de cenários.
- **Não modela adoção de tecnologia**, nem de geração distribuída, nem de
  veículos elétricos. Onde a geração aparece, é a que já consta do cadastro. O
  que `rodar` simula é **o dia que a BDGD descreve**, sem sorteio nem projeção.
- **Não apura conformidade com a norma.** `rodar` e `verificar` devolvem pu,
  e é o mapa que colore cada barra pela faixa do PRODIST Módulo 8 — para
  orientar o olhar, num instante. O enquadramento de verdade se apura sobre
  uma janela de leituras, com indicadores próprios, e é do leitor. Enterrar
  esse julgamento dentro de uma biblioteca de conversão seria exatamente o
  tipo de decisão não declarada que este pacote existe para evitar.
- **Não valida a rede contra medição.** A verificação de fechamento energético
  da seção anterior é um teste de sanidade agregado, não uma validação.

Delimitar isso importa: o pacote é a travessia do cadastro ao modelo, e não uma
versão reduzida de uma ferramenta de planejamento.



## Testes

```bash
pip install -e ".[dev]"
pytest
```

A suíte roda inteira sem baixar a BDGD e sem Tkinter instalado. Além das peças
isoladas, ela converte a fixture e compara o resultado byte a byte com o caso de
[`src/bdgdcase/exemplo/`](src/bdgdcase/exemplo): o que faz do exemplo algo que **não pode**
ficar desatualizado sem o CI acusar.

Os testes que resolvem o caso no OpenDSS levam o marcador `solver`, e dá para
rodar só eles — ou só o resto:

```bash
pytest -m solver          # os que resolvem o caso
pytest -m "not solver"    # os que provam a conversão, sem tocar no motor
```

A separação existe para o diagnóstico ficar óbvio: se o motor quebrar numa
versão nova, o que falhou foi resolver o caso, não convertê-lo.



## Origem e citação

Este pacote é o recorte publicável de um projeto maior de mestrado, sobre
impacto de geração distribuída e veículos elétricos em redes de distribuição
(PPGES/UFSC, Campus Araranguá). O que ficou aqui é a parte que serve a qualquer
pessoa que precise de um modelo elétrico a partir da BDGD, independentemente do
estudo que venha depois.

Se for útil em trabalho acadêmico, veja `CITATION.cff`.

## Licença

MIT: veja [`LICENSE`](LICENSE).
