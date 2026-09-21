# -*- coding: utf-8 -*-
"""RGE Sul — `DIST=396`, Rio Grande do Sul.

A base que mais custou, e a que mais ensinou. Nada nela levantava exceção: a
extração terminava, a conversão terminava, o caso compilava e resolvia. As
barras de baixa é que apareciam a 1,76 pu.

## O que esta base tem

| | medido em 214.534 transformadores |
|---|---|
| `TEN_LIN_SE` | 0,38 kV em 105.247, **0,22 kV em 102.388**, 0,23 kV em 6.839 |
| `TIP_TRAFO` | `T` 112.296, `M` 80.693, `MT` 6.915, **`B` 14.630** |
| `FAS_CON_S` | `ABCN`, `AN`/`BN`/`CN`, e **`CA`/`AB`/`BC` nos bifásicos** |
| `UNCRMT.DESCR` | `CAP…` e códigos numéricos — **nenhum prefixo `SE-`** |
| `UNSEMT` | chaves `BY-` com PACs auxiliares de sufixo `_1`/`_2` |
| `CRVCRG` | 74 códigos × 3 tipos de dia |

**Quase metade da rede de baixa é 220/127**, e não 380/220. É o fato central
desta base, e o que quebrava tudo em silêncio.

## As decisões

**`TEN_LIN_SE` é tensão de linha.** A prova está aqui dentro: os ~7.000
transformadores trifásicos `ABCN` declarados a 0,22 kV medem **128 V
fase-neutro** no caso resolvido. Logo 0,22 é a tensão entre fases de um sistema
220/127, e um enrolamento fase-neutro carrega 127 V. Os 80.693 monofásicos
desta base declaravam carga a 220 V. O limiar que causava isso foi removido do
conversor inteiro.

**`TIP_TRAFO='M'` é monofásico comum, não center-tap.** O `EQTRMT` confirma:
`TEN_TER='0'` nos 80.693, e `LIG_FAS_S='AN'`. Uma fase e o neutro, 127 V.

**`TIP_TRAFO='MT'` é center-tap de verdade.** Aqui o `EQTRMT` traz `TEN_TER`
preenchido e `LIG_FAS_T` numa fase *diferente* de `LIG_FAS_S` — os dois
enrolamentos em série com o ponto do meio aterrado. A 0,23 kV, cada perna é 115
V. O teste por `≈0,44` da base de origem os perderia; o `TIP_TRAFO` os pega.

**`TIP_TRAFO='B'` — o bifásico, e a concessão que ele obrigou.** O catálogo diz
que não há terciário (`TEN_TER='0'` em 14.629 de 14.630) e que o secundário tem
duas fases sem neutro (`LIG_FAS_S='CA'`). Lido ao pé da letra, isso dá um
enrolamento em delta flutuante — e o resultado foi medido: **48 V num nó e
222 V no outro**, números postos ali pelo que estivesse pendurado na barra.

O que decidiu foi a carga: **99,6% das 45.539 unidades ligadas a esses
transformadores são fase-neutro** (`AN`/`BN`/`CN`). É a tensão contra o neutro
que importa, então o secundário é modelado como dois enrolamentos fase-neutro
de `TEN_LIN_SE/√3`.

A concessão é o ângulo: saindo de uma unidade só, as duas pernas ficam a 180° e
não a 120°, de modo que entre as fases há 254 V em vez de 220. Não incomoda
porque quase nada se liga entre elas. Fica registrado porque é a única
simplificação consciente de todo o conjunto.

**O banco de subestação sem prefixo.** `DESCR` aqui é `CAP` seguido de código,
ou o código puro. Sem outro sinal, os 1.028 bancos entravam **todos fixos
ligados** — inclusive 185 acima de 1,8 MVAr, que é precisamente o que a opção
`CAP_SE_MODO` existe para evitar. Faltando prefixo, decide a potência, com
corte em 2.400 kVAr — o vão que os dados mostram entre o maior banco de rede e
o menor de subestação.

**Os nós de chave `_1`/`_2`.** As chaves `BY-` desta base ligam um PAC real a um
PAC auxiliar (`MT_URB13_1164749_1`) que **nenhum cabo atravessa** — e que por
isso não tem geometria. O `.dss` saía certo e o mapa não: 100% das barras de
média sem coordenada num alimentador vinham só do `UNSEMT`, e um único nó
desses — o do barramento de manobra — apagava **35 trechos** do desenho de uma
vez. É o "gap" que se vê no mapa. Hoje o nó herda a coordenada do próprio
dispositivo, que o cadastro traz como ponto.

**`POT_INST` da geração não é potência instalada.** O achado de maior efeito
sobre o resultado, e o mais bem escondido. Nesta base `POT_INST × 720` é
**exatamente** o maior dos doze valores mensais de energia — em 96,4% das
unidades de baixa, com erro mediano de 0,0 kWh, e em parte das de média. O
campo carrega a potência **média do mês de maior geração**, e não a instalada:
uma unidade de 5 kW aparece com 0,7, que é o inverso do fator de capacidade.

A comparação com as duas bases anteriores (a Copel entrou depois desta
medição) não deixa dúvida. `max(ENE)/POT_INST` dá
103,8 kWh/kW numa e 93,3 na outra — rendimento físico —, e **720,0 exato**
aqui, que é identidade aritmética.

A tabela de média é **mista**: 112,5; 75; 300 kW convivem com 7,890278 e
5,126389. Por isso a detecção é por linha, pela identidade, e não por
distribuidora — e ela não deu nenhum falso positivo nas 124.778 unidades das
outras duas bases.

A volta é **estimativa**: sabendo o maior mês, a instalada sai dividindo pelo
rendimento de pico medido nas bases que trazem os dois números (102,6 kWh/kW).
Cerca de 1% das unidades injetou tão pouco no ano que a reconstrução sai abaixo
de 0,5 kW; ficam assim, porque inventar um piso seria inventar dado.

**E o `POT_INST` errado desclassificava a fonte.** O elo que faltava, e o de
consequência mais visível. `fator_capacidade = ΣENE / (potência × 8760)`: uma
potência sete vezes menor dá um fator sete vezes maior, e o classificador usa
"acima de 25% não pode ser solar". O fator mediano saía em **60%** aqui, contra
7 a 9% na base sadia, e **446 de 463** unidades de um alimentador viravam
não-solares — modeladas com curva achatada, gerando as 24 horas do dia.

É essa geração noturna que levanta a rede de baixa numa madrugada sem carga.
Corrigida a potência, o fator cai para 8,3% e a classificação se alinha com as
outras bases.

**A reconstrução usa a energia do ANO, e não a do mês de pico.** Um quinto das
unidades tem três ou mais meses zerados — leitura bimestral, que fatura num mês
a energia de dois ou três. O máximo mensal daquelas unidades não é energia de
um mês. Num caso medido, `[0, 12286, 22114, 0, 9354, …]`: o máximo dava 215 kW
de potência instalada e a soma do ano dá 142.

**O que sobra é do cadastro, e fica visível.** Esse mesmo caso é uma usina de
~142 kW registrada atrás de um transformador de **75 kVA** — e a energia não
deixa dúvida de que ela é grande: 103.280 kWh no ano não saem de 30,71 kW
declarados, que dariam 3.363 kWh por kWp. A rede de baixa dele aparece a 1,30
pu, e a causa é a inconsistência do registro, não o modelo. O conversor conta
quantos transformadores estão nessa situação e diz quais.

**`CAR_INST` das unidades tem o mesmo defeito do `POT_INST`.** `CAR_INST × 720`
é o maior mês de energia em **92,0%** das unidades de baixa e **54,6%** das de
média. A mediana da carga instalada declarada é 0,37 kW por unidade, contra
9,00 e 10,47 nas outras duas bases.

Pesa menos que o da geração, porque a potência da carga no `.dss` vem do
histórico de energia e o `CAR_INST` só entra quando não há histórico — 1,4% das
unidades. Nas outras 98,6% o efeito é sobre o que se lê no cadastro e no painel
do mapa, que é onde o número absurdo aparece.

A correção é **unidade a unidade**, porque a tabela é mista: num poste medido
convivem `2,09981528` — o artefato — e `41,7`, que é carga real. E a referência
é **por nível de tensão**: 29,5 kWh por kW na baixa e 88,7 na média. As duas
lições saíram de erro: um fator de tabela pôs 1.018 kW num ramal residencial, e
a referência da baixa aplicada à média deu sete cargas de 1.448 kW.

**A iluminação pública vem dez vezes maior.** `CAR_INST` do ponto é 1,069 kW
aqui, contra 0,103 nas outras duas — uma luminária de 1 kW não existe. E como a
carga de IP é a **soma** dos pontos do transformador, o erro entra
multiplicado: num alimentador medido, 1.324 kW de iluminação onde há 132.

Quem denuncia é a energia. `max(ENE)/CAR_INST` são as horas que o ponto ficaria
aceso: 353,9 na base sadia — 11,6 h por noite — e **29,6** aqui, que daria uma
hora. Dividido por dez, o `POT_LAMP` desta base vira 103,5 W, 302 W e 401 W:
lâmpada mais reator nas potências normalizadas.

**`A2`/`A3` no `TIP_CC`.** Convenção desta base para consumidor industrial de
média; entrou no mapeamento de classe.
"""
from __future__ import annotations

from .base import Achado, Distribuidora


def _conferir_bifasico(tabelas):
    """O bifásico é a única simplificação consciente. Merece ser contado.

    Quem abrir um caso desta base tem de saber quantos transformadores foram
    modelados com as duas pernas a 180° em vez de 120°, e não descobrir isso
    lendo o `.dss`.
    """
    untrmt = tabelas.get('untrmt')
    if untrmt is None or not len(untrmt) or 'TIP_TRAFO' not in untrmt.columns:
        return []
    n = int((untrmt['TIP_TRAFO'].astype(str).str.strip().str.upper() == 'B').sum())
    if not n:
        return []
    return [Achado('aviso',
                   '%d transformador(es) bifasico(s): secundario modelado como '
                   'duas pernas fase-neutro. As pernas ficam a 180 graus e nao '
                   'a 120, entao entre as duas fases ha 254 V em vez de 220. As '
                   'cargas sao fase-neutro e saem certas.' % n)]


def _conferir_nos_de_manobra(tabelas):
    """Os PACs auxiliares de chave, que somem do mapa se ninguém os localizar."""
    unsemt = tabelas.get('unsemt')
    ssdmt = tabelas.get('ssdmt')
    if unsemt is None or not len(unsemt) or ssdmt is None or not len(ssdmt):
        return []
    if 'PAC_1' not in unsemt.columns or 'PAC_1' not in ssdmt.columns:
        return []
    rede = set(ssdmt['PAC_1'].astype(str)) | set(ssdmt['PAC_2'].astype(str))
    so_chave = set()
    for pac in ('PAC_1', 'PAC_2'):
        so_chave |= {v for v in unsemt[pac].astype(str) if v not in rede}
    if not so_chave:
        return []
    tem_geom = ('geometry' in getattr(unsemt, 'columns', [])
                and unsemt.geometry.notna().any())
    if tem_geom:
        return []
    return [Achado('erro',
                   '%d no(s) de manobra fora da rede de cabos e o UNSEMT sem '
                   'geometria: essas barras ficarao sem coordenada e o mapa vai '
                   'mostrar buracos na media.' % len(so_chave))]


def _conferir_pot_inst_derivado(tabelas):
    """`POT_INST` da geração nesta base é a média do mês de pico, não a instalada.

    Sai como achado próprio porque é a correção de maior efeito sobre o
    resultado: sem ela, cada unidade entra no caso com cerca de um sétimo da
    potência real, e um estudo de hospedagem ou de fluxo reverso responde a
    pergunta errada sem dar nenhum sinal disso.
    """
    from bdgdcase.modelo.cadastro import pot_inst_kw

    n = 0
    for chave in ('ugbt', 'ugmt'):
        df = tabelas.get(chave)
        if df is None or not len(df) or 'POT_INST' not in getattr(df, 'columns', []):
            continue
        for _, row in df.iterrows():
            if pot_inst_kw(row)[1] == 'estimada':
                n += 1
    if not n:
        return []
    return [Achado('aviso',
                   '%d unidade(s) de geracao com POT_INST derivado (media do '
                   'mes de pico, nao a instalada). A potencia foi reconstruida '
                   'por estimativa -- ver a coluna pot_origem em '
                   'Cadastro_GD.csv.' % n)]


PERFIL = Distribuidora(
    dist='396',
    nome='RGE Sul (RS)',
    resumo=('Quase metade da baixa em 220/127, transformadores monofásicos e '
            'bifásicos, MRT em 230/115, capacitor sem prefixo e nós de chave '
            'sem cabo. Nada disso levantava exceção.'),
    tensoes_bt_kv=(0.38, 0.22, 0.23, 0.44),
    tipos_de_trafo=('T', 'M', 'B', 'MT'),
    tem_curvas_de_carga=True,
    tem_tipologia_de_carga=True,
    capacitor_tem_prefixo=False,
    ramal_pac1_e_poste=True,
    conferencias=(_conferir_bifasico, _conferir_nos_de_manobra,
                  _conferir_pot_inst_derivado),
)
