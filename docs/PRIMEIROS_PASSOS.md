# Primeiros passos (Windows, do zero)

Este guia supõe que você nunca usou Python e nunca abriu um terminal. Ele vai do
computador limpo até uma curva de carga simulada.

Se você já programa, vá direto ao [GUIA.md](GUIA.md), que é a mesma coisa em um
quarto do tamanho.

**Quanto tempo levar em conta:** meia hora para as partes 1 e 2, e o resto do
tempo é download da BDGD, que depende da sua internet.

---

## Parte 1 — Instalar o Python (uma vez só)

O `bdgdcase` é um programa em Python, então o Python precisa existir na máquina.

1. Vá em <https://www.python.org/downloads/> e clique no botão amarelo
   **Download Python**.
2. Abra o arquivo baixado.
3. **Na primeira tela, marque a caixinha `Add python.exe to PATH`**, embaixo.
   Ela vem desmarcada.

   > Esta caixinha é o passo que mais dá errado. Sem ela, o Windows não encontra
   > o Python depois, e todos os comandos deste guia respondem
   > `'python' não é reconhecido`. Se você já instalou sem marcar, rode o
   > instalador de novo, escolha *Modify* e marque.

4. Clique em **Install Now** e espere.

### Conferir

Abra o menu Iniciar, digite `powershell` e abra o **Windows PowerShell**. Vai
aparecer uma janela preta ou azul com um cursor piscando. É o terminal — você vai
digitar comandos aí e apertar Enter.

Digite:

```
python --version
```

Deve responder algo como `Python 3.13.2`. Se responder que não reconhece o
comando, volte ao passo 3.

---

## Parte 2 — Instalar o bdgdcase

Na mesma janela do PowerShell, digite (ou copie e cole com o botão direito):

```
pip install "bdgdcase @ https://github.com/constantinipmc/bdgdcase/archive/refs/heads/main.zip"
```

Vai baixar e instalar várias coisas. Leva alguns minutos e termina com uma linha
começando em `Successfully installed`.

> **Por que esse endereço comprido?** Ele baixa o programa direto do GitHub, em
> `.zip`, sem precisar instalar mais nada. Vem tudo junto, inclusive o OpenDSS,
> que é o motor que resolve o circuito.

### Conferir — e este é o passo bom

```
bdgdcase exemplo
```

O programa traz um alimentador de verdade já pronto por dentro. Este comando o
resolve na hora, sem você ter baixado nada. Deve sair assim:

```
Caso de exemplo: ...\site-packages\bdgdcase\exemplo\TRO05_DU
  (alimentador TRO05 da BDGD Celesc, ja convertido)

    convergiu   147 barras, 73 cargas, 131 linhas, 11 trafos
    tensao      0.9790 a 1.0493 pu (media 1.0164)

  TRO05  (96 passos de 15 min)
    pico        84.4 kW as 18.50 h
    energia     682.4 kWh importados, 104.2 exportados
    perdas      49.2 kWh (7.21% da energia importada)
```

Se você viu isso, **está tudo funcionando** — instalação, conversor e simulador.
O resto do guia é aplicar isso aos seus próprios dados.

---

## Parte 3 — Baixar a BDGD

A BDGD é a base de dados que toda distribuidora de energia é obrigada a entregar
ao regulador, e é pública.

1. Vá em <https://dadosabertos.aneel.gov.br/>.
2. Busque por **BDGD** ou *Base de Dados Geográfica da Distribuidora*.
3. Escolha a distribuidora da sua região e o ano mais recente.
4. Baixe o arquivo `.zip`.

> **Espaço em disco.** O `.zip` tem alguns gigabytes e, descompactado, ocupa
> bem mais. Deixe uns 30 GB livres.

5. Descompacte (botão direito → *Extrair tudo*). Guarde numa pasta fácil de
   achar, por exemplo `C:\BDGD`.

### A pegadinha da pasta dupla

Ao descompactar, você provavelmente vai ver uma pasta com o mesmo nome **dentro
de outra igual**:

```
C:\BDGD\
└── Celesc-Dis_5697_2024-12-31_V11.gdb\      ← pasta comum
    └── Celesc-Dis_5697_2024-12-31_V11.gdb\  ← esta é a base de verdade
        ├── a00000001.gdbtable
        └── ... (mais de 200 arquivos)
```

A pasta certa é a **de dentro**, a que tem os arquivos `a00000001.gdbtable`.

Não precisa decorar: a janela do programa desce sozinha e avisa quando faz isso.

---

## Parte 4 — Usar o programa

No PowerShell, digite:

```
bdgdcase gui
```

Abre uma janela com cinco blocos numerados. Siga na ordem.

### 1. Base da BDGD

Clique em **Procurar…** e escolha a pasta `.gdb` que você descompactou. Se você
escolher a de fora, o programa desce sozinho e escreve *"A geodatabase estava
numa subpasta; ajustei o caminho"*.

Clique em **Varrer alimentadores**. Leva cerca de 20 segundos e o registro, lá
embaixo, mostra quantos foram encontrados — numa distribuidora estadual, algumas
centenas.

### 2. Alimentadores

A lista enche com os códigos. Cada alimentador é um circuito que sai de uma
subestação e atende um pedaço de cidade.

- Digite na caixa **Filtrar** para reduzir a lista. Se você conhece a subestação,
  as três primeiras letras costumam ser dela.
- Clique num código para escolher. Segure **Ctrl** para escolher vários.

> **Comece por um só.** Cada alimentador leva cerca de 3 minutos e ocupa uns
> 57 MB. Depois que der certo com um, faça o resto.

### 3. Pasta de trabalho

Onde os resultados vão ficar. O padrão serve; se quiser, aponte para algo como
`C:\meus-casos`.

### 4. O que fazer

As quatro caixinhas já vêm marcadas, e é o que você quer da primeira vez:

| caixinha | o que faz |
|---|---|
| **Extrair da BDGD** | separa os dados daquele alimentador (~3 min) |
| **Gerar o caso OpenDSS** | transforma os dados no modelo elétrico (~5 s) |
| **Verificar se resolve** | confere que o modelo fecha |
| **Rodar o dia (96 passos)** | simula as 24 h, de 15 em 15 minutos |

**dias:** `DU` é dia útil, `SA` sábado, `DO` domingo. A base traz uma curva de
consumo diferente para cada um. Deixe só `DU` na primeira vez.

### 5. Executar

Clique em **Executar**. A barra anda e o registro conta o que está acontecendo.
A janela continua respondendo — pode minimizar e ir fazer outra coisa.

No fim, algo assim:

```
-- TRO05 --------------------------------
Extraindo da BDGD... (alguns minutos)
   20 arquivos, 57.2 MB em Output/TRO05
Gerando o caso OpenDSS (DU)...
   caso gerado.
Verificacao de TRO05_DU:
   converge - 147 barras, 73 cargas, 131 linhas, 11 trafos
   tensao 0.9790 a 1.0493 pu
Rodando o dia de TRO05_DU...
   pico 84.4 kW as 18.50 h
   682.4 kWh importados, 104.2 exportados
   perdas 49.2 kWh (7.21%)
```

---

## Parte 5 — Os resultados

Clique em **Abrir pasta**. Dentro da pasta de trabalho você encontra:

| pasta | o que tem |
|---|---|
| `Output\TRO05\` | os dados brutos daquele alimentador |
| `OpenDSS\TRO05_DU\` | **o modelo elétrico** — 11 arquivos `.dss`, mais as coordenadas, cinco cadastros e o registro dos ajustes |

Os `.dss` são texto puro e abrem no Bloco de Notas. `Master.dss` é o principal:
ele descreve o circuito e chama os outros. Esses arquivos abrem também no
programa OpenDSS, se você o usar.

Clique em **Salvar CSV do dia** para guardar a simulação numa planilha: 96
linhas, uma a cada 15 minutos, com potência, perdas e tensão. Abre no Excel.

### Lendo os números

**Potência negativa quer dizer exportação** — naquele momento o alimentador
estava devolvendo energia para o sistema, em vez de consumir. É o efeito da
geração solar já instalada, que consta do cadastro.

**Se aparecer um aviso de passos que não convergiram**, leve a sério: aqueles
pontos da curva foram repetidos do instante anterior, não calculados. O programa
avisa justamente para você não usá-los sem saber.

**O nível da curva é uma estimativa.** Os fatores que convertem consumo faturado
em potência instantânea foram ajustados contra medições de uma distribuidora de
Santa Catarina. Em outra região, a forma da curva continua informativa, mas os
valores absolutos merecem desconfiança. O [README](../README.md) explica isso na
seção *As decisões que a travessia obriga a tomar*.

---

## Se algo der errado

**`'python' não é reconhecido` ou `'bdgdcase' não é reconhecido`**
A caixinha *Add python.exe to PATH* não foi marcada. Rode o instalador do Python
de novo, escolha *Modify* e marque. Depois feche e reabra o PowerShell.

**A janela não abre e aparece "Interface gráfica indisponível"**
Falta o Tkinter. No Windows ele vem com o Python, então isso costuma significar
que o Python foi instalado pela Microsoft Store — desinstale e use o do
python.org.

**"Não encontrei uma geodatabase aqui dentro"**
Você apontou para a pasta errada. Procure a que tem os arquivos
`a00000001.gdbtable`.

**A extração demora demais**
Três minutos por alimentador é o normal, e o programa lê a base inteira para
achar o pedaço que interessa. Se passar de dez minutos, veja se o disco não está
cheio.

**Erro em vermelho no registro**
A mensagem é a última linha. Se não fizer sentido, abra uma *issue* no GitHub
colando o registro inteiro.

---

## Onde ir depois

- **[GUIA.md](GUIA.md)** — os mesmos passos por linha de comando, que é o
  caminho para automatizar e repetir.
- **[RESULTADOS.md](RESULTADOS.md)** — como tirar outros números da simulação:
  perfil de tensão ao longo do alimentador, carregamento por transformador,
  corrente nos cabos.
- **[README](../README.md)** — o que o programa decide por você ao converter o
  cadastro num modelo, e por quê. Vale ler antes de publicar qualquer resultado.
