# Extraindo resultados da simulação

`bdgdcase.solucao` devolve um punhado de séries agregadas — potência no ponto de
entrega, perdas, faixa de tensão. É o que a maioria das perguntas exige, e é
deliberadamente pouco: um pacote que tentasse antecipar toda grandeza que alguém
poderia querer viraria um pacote sobre outra coisa.

Este documento mostra as duas saídas. A primeira é ler o que já vem pronto. A
segunda é chegar em **qualquer** grandeza, indo direto ao circuito resolvido —
que continua carregado no OpenDSS depois que a simulação passa por ele.

> Tudo aqui resolve o caso no OpenDSS, que vem com o pacote:
> ```bash
> pip install bdgdcase
> ```
>
> Se a sua pergunta é *onde* e não *quanto*, talvez `bdgdcase mapa` já responda
> sem escrever código — veja a seção final.

---

## 1. O que já vem pronto

```python
from bdgdcase.solucao import rodar_dia, para_csv

dia = rodar_dia('OpenDSS/TRO05_DU')
para_csv(dia, 'tro05_du.csv')       # 96 linhas, uma por passo
```

### Séries — 96 pontos, um a cada 15 minutos

| chave | unidade | o que é |
|---|---|---|
| `horas` | h | 0,00 · 0,25 · … · 23,75 |
| `p_kw` | kW | ativa no ponto de entrega. **Negativa = exportação** |
| `q_kvar` | kVAr | reativa no mesmo ponto |
| `s_kva` | kVA | `hypot(p, q)` |
| `perdas_kw` · `perdas_kvar` | kW · kVAr | perdas do circuito inteiro |
| `perdas_pct` | % | perdas sobre `max(|P|, perdas, 1 kW)` |
| `v_min_pu` · `v_med_pu` · `v_max_pu` | pu | entre as **fases conectadas** (> 0,5 pu) |
| `convergiu` | bool | veja a ressalva adiante |

### Agregados — um por execução

| chave | o que é |
|---|---|
| `alimentador` · `pasta` · `passos` · `minutos_por_passo` | identificação |
| `energia_importada_kwh` · `energia_exportada_kwh` | integral dos passos de cada sinal |
| `perdas_kwh` · `perdas_pct_dia` | perdas do dia, e sua fração da energia **importada** |
| `p_max_kw` · `hora_p_max` | o pico e quando |
| `v_min_dia_pu` · `v_max_dia_pu` | envelope de tensão do dia |
| `passos_nao_convergidos` | índices dos passos repetidos do anterior |

**Fase conectada é acima de 0,5 pu, e o limiar não é arbitrário.** Uma rede de
distribuição real tem laterais monofásicas e bifásicas: o barramento declara três
nós, e o nó da fase ausente fica *flutuando*, acoplado só capacitivamente. No
alimentador IBA09 essas fases assentam entre 0,05 e 0,50 pu — bem acima de zero,
e bem abaixo de qualquer tensão de operação. Como a tensão de uma barra é a
**pior** fase, um limiar frouxo faz uma barra com duas fases em 0,97 pu ser
reportada em 0,06. Nenhum ponto em operação fica abaixo de meia tensão nominal;
o PRODIST já considera crítico em 0,87 pu na baixa. Abaixo da metade não é
subtensão, é ausência de conexão.

**Duas ressalvas que mudam a leitura dos números.**

`perdas_pct_dia` divide pela energia *importada*. Num alimentador que exporta em
parte do dia, esse denominador não é a energia servida, e a fração parece maior
do que a intuição sugere. Se o seu estudo precisa de outra base — energia
servida, energia aparente —, recalcule a partir das séries; elas estão todas
disponíveis.

`passos_nao_convergidos` é o único lugar onde o resultado admite ter chutado.
Passo que não converge entra com o valor do anterior, para não abrir buraco na
série. **Confira essa lista antes de publicar qualquer número**: se ela não está
vazia, parte da curva é interpolação, não simulação.

### Desenhar

```python
import matplotlib.pyplot as plt

fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(8, 6))

ax1.plot(dia['horas'], dia['p_kw'])
ax1.axhline(0, lw=0.8, color='0.6')          # abaixo daqui, o alimentador exporta
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

## 2. Chegando em qualquer outra grandeza

Depois de `rodar_dia`, **o circuito continua carregado no OpenDSS**, parado no
último passo. Tudo o que o OpenDSS sabe sobre ele está a uma chamada de
distância.

O padrão é sempre o mesmo:

```python
import opendssdirect as dss
from bdgdcase.solucao import rodar_dia

rodar_dia('OpenDSS/TRO05_DU')      # circuito resolvido, parado às 23h45
# ... agora consulte `dss` à vontade
```

Para parar num instante específico — o pico, por exemplo — resolva você mesmo,
passo a passo. São quatro linhas:

```python
import opendssdirect as dss
from bdgdcase.solucao import _compilar

dss = _compilar('OpenDSS/TRO05_DU')
dss.Text.Command('Set Mode=Daily StepSize=15m Number=1')
dss.Text.Command('Set ControlMode=Time')
for _ in range(75):                # 75 passos de 15 min = 18h45
    dss.Solution.Solve()
```

> `_compilar` tem underscore: não é API estável. Ele faz duas coisas que valem
> ser copiadas se você preferir chamar o OpenDSS direto — compila com caminho
> absoluto e **restaura o diretório de trabalho**, que o `Compile` do OpenDSS
> troca por conta própria para resolver os `Redirect` do Master.

A partir daqui, quatro receitas. Todas rodam sobre o caso que vem no pacote e os
valores mostrados são os que elas de fato produzem lá.

### Perfil de tensão ao longo do alimentador

A pergunta clássica: a tensão cai com a distância? Separando média de baixa,
porque as duas têm faixas de referência diferentes.

```python
mt, bt = [], []
for barra in dss.Circuit.AllBusNames():
    dss.Circuit.SetActiveBus(barra)
    vivos = [v for v in dss.Bus.puVmagAngle()[::2] if v > 0.5]
    if not vivos:
        continue
    ponto = (dss.Bus.Distance(), min(vivos))     # km desde a fonte, pu
    (mt if dss.Bus.kVBase() > 1.0 else bt).append(ponto)

print('MT: %d barras, mínima %.4f pu' % (len(mt), min(v for _, v in mt)))
print('BT: %d barras, mínima %.4f pu' % (len(bt), min(v for _, v in bt)))
# MT: 50 barras, mínima 1.0196 pu
# BT: 68 barras, mínima 0.9884 pu
```

`Bus.Distance()` só é preenchido porque o `Master.dss` declara um
`EnergyMeter` na cabeceira — é ele que estabelece a árvore a partir da qual o
OpenDSS mede distância elétrica. Um `plt.scatter(*zip(*mt))` dá o perfil.

O filtro `> 0.5 pu` não é decorativo, e o valor importa: é a fronteira entre uma
fase conectada e uma fase que apenas existe no cadastro. Veja a ressalva sobre
fases flutuantes na seção 1.

### Transformador mais carregado

```python
carregamento = []
i = dss.Transformers.First()
while i:
    p = dss.CktElement.Powers()
    # Powers() traz [P, Q] por terminal e fase; o primeiro terminal é o primário.
    kva = sum((p[k] ** 2 + p[k + 1] ** 2) ** 0.5
              for k in range(0, len(p) // 2, 2))
    carregamento.append((100 * kva / dss.Transformers.kVA(),
                         dss.Transformers.Name(), dss.Transformers.kVA()))
    i = dss.Transformers.Next()

carregamento.sort(reverse=True)
for pct, nome, kva in carregamento[:3]:
    print('%-14s %5.1f%% de %3.0f kVA' % (nome, pct, kva))
# tr_7449457      52.5% de  45 kVA
```

Repetir isso dentro do laço de passos dá a curva de carregamento de cada
transformador ao longo do dia.

### Linhas perto da ampacidade

```python
apertadas = []
i = dss.Lines.First()
while i:
    corrente = max(dss.CktElement.CurrentsMagAng()[::2] or [0.0])
    norm = dss.Lines.NormAmps()
    if norm > 0:
        apertadas.append((100 * corrente / norm, dss.Lines.Name(), norm))
    i = dss.Lines.Next()

apertadas.sort(reverse=True)
for pct, nome, norm in apertadas[:3]:
    print('%-14s %5.1f%% de %3.0f A' % (nome, pct, norm))
```

As ampacidades vêm da tabela SEGCON da BDGD, via `LineCodes.dss`. São o que a
distribuidora cadastrou — se o cadastro estiver defasado, o resultado herda a
defasagem, e nenhuma simulação avisa.

### Registradores do medidor de cabeceira

O `EnergyMeter` acumula energia ao longo do dia e é uma conferência independente
das séries agregadas:

```python
dss.Meters.First()
registros = dict(zip(dss.Meters.RegisterNames(), dss.Meters.RegisterValues()))
print('kWh   %.1f' % registros['kWh'])
print('Max kW %.1f' % registros['Max kW'])
# Max kW  84.4  — o mesmo `p_max_kw` que rodar_dia devolve
```

Que os dois caminhos cheguem ao mesmo pico é um teste de sanidade barato, e vale
repetir sempre que se mexer no laço de simulação.

### Onde procurar o resto

O que existe segue o mesmo formato dos exemplos acima: uma coleção
(`dss.Loads`, `dss.Capacitors`, `dss.RegControls`, `dss.PVSystems`, `dss.Meters`,
`dss.Monitors`) percorrida com `First()`/`Next()`, e `dss.CktElement.*` para o
elemento ativo. A referência é a documentação do
[OpenDSSDirect.py](https://dss-extensions.org/OpenDSSDirect.py/) e o manual do
OpenDSS.

Para séries de alta resolução em pontos específicos, vale conhecer os
`Monitor` do próprio OpenDSS: declarados no `.dss`, eles gravam tensão ou
corrente de um elemento a cada passo, sem laço em Python.

---

## 3. O mapa, quando a pergunta é "onde"

```bash
bdgdcase mapa OpenDSS/TRO05_DU
```

A janela resolve o dia uma vez, guarda os 96 instantes da rede inteira e deixa
percorrê-los. Em Python, os mesmos dados saem de:

```python
r = rodar_dia('OpenDSS/TRO05_DU', detalhado=True)

r['v_no_pu']      # (96, n_nós)    tensão em pu, por nó e passo
r['amp_linha']    # (96, n_linhas) corrente do trecho, em ampères
r['norm_amps']    # ampacidade nominal de cada trecho
r['coordenadas']  # {barra: (lon, lat)}
r['no_para_barra'], r['fase_do_no'], r['barras'], r['nos'], r['linhas']
r['inventario']   # cargas, trafos e GD, cada um com o seu 'cadastro'
r['curvas']       # {nome_do_loadshape: [96 multiplicadores]}
```

São `float32` de propósito: um alimentador urbano de 16 mil barras ocupa cerca
de 22 MB assim, e o dobro disso começaria a incomodar. `detalhado` é opcional
porque quem só quer a curva do dia não deveria pagar por isso.

Com esses vetores dá para responder coisas que a janela não mostra — por
exemplo, em que instante cada barra atinge sua pior tensão:

```python
import numpy as np
pior_passo = np.nanargmin(r['v_no_pu'], axis=0)   # um índice por nó
```

### O cadastro que veio junto

Cada carga, transformador e gerador do inventário traz um sub-dicionário
`'cadastro'` com o que os `Cadastro_*.csv` da pasta guardaram — o que a BDGD
sabe e o caso `.dss` não tem onde escrever:

```python
uc = r['inventario']['cargas'][0]
uc['kw']                    # do circuito compilado
uc['cadastro']['cod_id']    # o identificador da unidade na BDGD
uc['cadastro']['ene_07']    # o que ela consumiu em julho, em kWh
uc['cadastro']['fas_con'], uc['cadastro']['fases_ligadas']
```

Os dois últimos merecem atenção: `fas_con` é a fase que o cadastro declara e
`fases_ligadas` é a que sobrou depois da auto-cura. Quando diferem, a unidade
estava ligada a uma fase que não existe no ponto de derivação — é achado de
cadastro, e some se ninguém for olhar.

A separação é de propósito: o que veio do circuito fica no nível de cima, o que
veio do cadastro fica dentro de `'cadastro'`. As duas coisas têm procedências
diferentes e não devem se confundir na leitura.

### A curva de uma unidade

```python
uc = r['inventario']['cargas'][0]
curva = r['curvas'][uc['curva'].lower()]     # 96 multiplicadores
potencia = [uc['kw'] * m for m in curva]     # kW ao longo do dia
```

Isto é a curva **nominal**: a placa vezes a forma da classe. Não é o que o
solver serviu — as cargas são `model=8` com `ZIPV`, metade da parte ativa em
impedância constante, de modo que a potência acompanha a tensão. No alimentador
de exemplo a diferença do pico tem mediana de +0,6% e máximo de +4%, e cresce
onde a tensão cai.

Para a servida, peça a medição:

```python
r = rodar_dia('OpenDSS/TRO05_DU', detalhado=True, medir_cargas=True)
j = r['nomes_carga'].index(uc['nome'])
r['pq_carga'][:, j, 0]    # P em kW, por passo
r['pq_carga'][:, j, 1]    # Q em kvar
```

Vem desligado porque custa: são cerca de um milhão de travessias a mais pela API
do OpenDSS num alimentador urbano, e o tempo praticamente dobra. A memória não é
o problema — doze mil cargas em 96 passos dão 9 MB em `float32`.

E vale a ressalva de sempre: a forma da curva vem da CRVCRG, que é regulatória e
**por classe** — uma curva para toda a concessionária. Ela descreve o hábito
médio de um grupo, não o daquela unidade.

## 4. O que os números significam — e o que não significam

Um resultado de simulação carrega todas as decisões tomadas para construir o
modelo. As três que mais afetam o que você vai ler:

**A demanda é derivada da energia faturada.** Não da carga instalada: o pico de
cada unidade sai de `ENE_01..ENE_12` divididos pelo fator de carga da curva
CRVCRG da sua classe. `CAR_INST × fator de demanda` é só o fallback para unidade
sem histórico — 0,7% delas no IBA09. Sobre o resultado dos dois caminhos incide
`FC_MACROCOPICO`, um fator de coincidência agregada que **foi calibrado contra
medição de uma distribuidora específica em Santa Catarina**. Em outra
distribuidora, outra região ou outro perfil de consumo, ele precisa ser revisto —
e enquanto não for, o nível absoluto da curva é uma estimativa, ainda que a forma
seja informativa.

**As curvas são regulatórias.** As curvas CRVCRG da BDGD são médias estatísticas
por classe, uma para toda a concessionária. Elas descrevem bem o comportamento
agregado e mal o de um alimentador específico com população atípica.

**Não há conformidade apurada aqui.** `rodar` devolve tensão em pu. O mapa
colore cada barra pela faixa do PRODIST Módulo 8 para orientar o olhar num
instante, mas o enquadramento em adequada, precária ou crítica depende de
janela de apuração e de indicadores próprios, e é do leitor. É de propósito:
enterrar esse julgamento dentro de uma biblioteca de conversão é exatamente o
tipo de decisão não declarada que este pacote existe para evitar.

E o que o pacote não faz, de novo: ele simula **o dia que o cadastro descreve** —
as cargas e a geração que já constam da BDGD, sob as curvas da própria base. Não
há sorteio, cenário, projeção nem modelo de adoção de tecnologia. Um estudo que
precise disso constrói o cenário por cima: as ferramentas acima permitem editar
o circuito entre passos, e o que se faz com essa liberdade é decisão de quem
pesquisa, não deste pacote.
