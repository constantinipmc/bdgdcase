# Dados de teste

Fatias reais da BDGD pública da ANEEL, uma por distribuidora, reduzidas ao que
cabe num repositório. Cada pasta traz o seu próprio `LEIAME.md`, gerado junto
com ela, dizendo o que saiu e como regenerá-la.

| pasta | distribuidora | serve para |
|---|---|---|
| [`TRO05/`](TRO05) | a base com que o conversor foi construído | o caso de referência, comparado **byte a byte** |
| [`URB13/`](URB13) | uma base do Sul com metade da baixa em 220/127 | prova que o caso fecha fora da base de origem |
| [`2_CVO_3/`](2_CVO_3) | uma base pequena, sem curvas de carga | prova que o catálogo de referência embarcado serve |

Nenhuma é um alimentador inventado. Uma fixture sintética teria de acertar de
cabeça o contrato de dezenas de colunas da BDGD, e passaria a testar a ideia que
o autor faz da base em vez da base.

## Por que só uma delas é comparada byte a byte

`TRO05` é o caso de referência: qualquer mudança de comportamento do conversor
aparece nele, e é o que `tests/test_conversao.py` guarda. As outras duas são
verificadas por `tests/test_distribuidoras.py`, que converte e chama
`bdgdcase.solucao.verificar` — compila, converge, tem barras e cargas, tensões
em faixa plausível.

A diferença é deliberada. Um golden por distribuidora obrigaria a regerar três
referências a cada correção, e o custo de manter três não compra três vezes a
informação: o que a segunda e a terceira acrescentam é *funciona fora da base de
origem*, e é isso que `verificar` responde.

## Uma limitação a saber, na fixture da Coopera

A redução tira as colunas que reidentificam pessoa, e entre elas `CLAS_SUB`.
Nessa base o `TIP_CC` vem em branco em 100% das unidades, e `CLAS_SUB` é o
degrau seguinte da cadeia que decide a classe da carga — de modo que **na
fixture as 731 cargas saem todas residenciais**, enquanto no alimentador
completo saem 659 residenciais, 43 comerciais, 28 industriais e 1 de iluminação
pública.

A fixture continua servindo ao que foi feita para servir: o caso fecha, converge
e usa o catálogo de curvas embarcado. Mas ela **não** exercita a cadeia de
classe — quem mexer nessa cadeia tem de olhar `tests/test_conversao.py` e os
testes unitários, não esta pasta.

Vale registrar que a linha da redação não é inteiramente coerente: `TIP_CC`
fica, e carrega a mesma informação que `CLAS_SUB` numa resolução maior. Rever
isso é decisão de quem assina a redistribuição, não do código.

## Procedência e limites

Dado aberto da ANEEL, redistribuído com atribuição. Das unidades consumidoras
saem as colunas que reidentificam pessoa — número de conta, CEP, CNAE e classe
de consumo. A BDGD ser aberta não torna tudo nela
publicável em qualquer forma: muda a facilidade, e muda quem assina a
redistribuição.
