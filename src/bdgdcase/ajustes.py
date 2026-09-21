# -*- coding: utf-8 -*-
"""O que o conversor mudou entre o cadastro cru e o caso que resolve.

## Por que este módulo existe

A BDGD publicada não fecha um fluxo de potência como está. Entre ela e um caso
que converge há dezenas de decisões — um campo que traz outra grandeza e é
reposto na escala, um nó sem coordenada que herda a do vizinho, uma curva de
carga que a base não trouxe e vem de outro lugar. Cada uma dessas decisões está
documentada no código, e nenhuma delas chegava a quem abre o resultado.

Isso é um problema de honestidade, não de conveniência. Quem usa o caso para
decidir alguma coisa precisa saber que a potência daquela geração foi
**estimada**, e não lida; que aquela curva veio de outra distribuidora; que
aquele transformador tem mais geração do que aguenta e o modelo não corrigiu.
Sem isso, o `.dss` tem a mesma aparência de autoridade quer o cadastro
estivesse completo, quer tivesse sido remendado em quinze lugares.

## Como funciona

A conversão registra o que faz. O registro é gravado em `Ajustes.json`, dentro
da pasta do caso, junto dos `.dss` — de modo que a pasta viaja com a explicação
do que há nela. A janela do mapa lê esse arquivo e mostra numa aba.

Cada ajuste diz **quantos**, **o quê** e **por quê**, e declara o seu `efeito`:

`simulacao`
    muda o resultado do fluxo de potência. É o que mais importa saber.
`cadastro`
    muda o que se lê nas fichas e no painel, não o que a rede faz.
`desenho`
    muda o que o mapa consegue desenhar.
`nao_corrigido`
    o conversor encontrou e **não** consertou, de propósito. Vale tanto quanto
    os outros: é onde o resultado depende de um dado que a base publicou errado.

O registro é de processo, e não de biblioteca: `limpar()` no começo de cada
alimentador, `registrar()` durante, `salvar()` no fim.
"""
from __future__ import annotations

import json
import os

__all__ = ['CATEGORIAS', 'EFEITOS', 'limpar', 'contexto', 'registrar',
           'listar', 'salvar', 'carregar', 'por_categoria', 'NOME_ARQUIVO']

#: Nome do arquivo dentro da pasta do caso.
NOME_ARQUIVO = 'Ajustes.json'

#: As famílias, na ordem em que fazem sentido ser lidas: primeiro o que muda o
#: resultado elétrico, depois o que muda o que se vê, por fim o que ficou por
#: fazer. A ordem é dado, e não apresentação: quem lê a aba tem de encontrar
#: primeiro o que pode mudar a conclusão dele.
CATEGORIAS = (
    ('tensao', 'Tensão e ligação',
     'Como o secundário de cada transformador foi modelado, e em que tensão a '
     'rede de baixa ficou.'),
    ('potencia', 'Potência declarada',
     'Campos do cadastro que traziam outra grandeza, e a escala em que foram '
     'repostos.'),
    ('classe', 'Classificação',
     'A que classe cada carga e cada geração foi atribuída quando o cadastro '
     'não disse.'),
    ('curvas', 'Curvas de carga',
     'De onde veio a forma do dia de cada carga.'),
    ('topologia', 'Topologia e geometria',
     'Ligações e coordenadas que o cadastro não fechava.'),
    ('condutores', 'Condutores e impedâncias',
     'Cabos citados pela rede e o que foi usado no lugar quando faltaram.'),
    ('completude', 'Cadastro incompleto',
     'Campos vazios, ilegíveis ou fora do domínio, e o que entrou no lugar.'),
    ('nao_corrigido', 'Encontrado e NÃO corrigido',
     'Inconsistências do cadastro que o conversor não tem como resolver sem '
     'inventar dado. O resultado depende delas.'),
)

#: `efeito` → (rótulo curto, ordem de gravidade).
EFEITOS = {
    'simulacao': ('muda o resultado', 0),
    'nao_corrigido': ('não corrigido', 1),
    'desenho': ('muda o desenho', 2),
    'cadastro': ('muda a ficha', 3),
}

_REGISTRO = []
_CONTEXTO = {}


def limpar(alimentador=None, distribuidora=None, dia=None):
    """Começa o registro de um alimentador. Chamar antes de converter.

    Sem data e hora, de propósito. Converter o mesmo alimentador duas vezes tem
    de dar os mesmos bytes — é o que `tests/test_conversao.py` guarda, e é o
    que permite provar que uma mudança de código não mexeu no resultado. Um
    carimbo de tempo aqui faria dois casos idênticos diferirem, e a data em que
    a pasta foi feita o sistema de arquivos já sabe.
    """
    del _REGISTRO[:]
    _CONTEXTO.clear()
    _CONTEXTO.update({'alimentador': alimentador, 'dia': dia,
                      'distribuidora': distribuidora})


def contexto(**campos):
    """Acrescenta ao cabeçalho do registro sem apagar o que já foi anotado.

    A distribuidora só é identificada depois de ler o `CTMT`, e a essa altura
    já houve ajuste registrado — parte deles acontece ao carregar as tabelas.
    Por isso `limpar()` vem antes da carga e o resto do cabeçalho chega por
    aqui.
    """
    _CONTEXTO.update({k: v for k, v in campos.items() if v is not None})


def registrar(categoria, titulo, quantos=None, unidade='', efeito='simulacao',
              detalhe='', porque='', exemplos=()):
    """Anota um ajuste.

    `quantos` é o número de elementos atingidos, e `None` quando não faz
    sentido contar. `detalhe` diz o que foi feito, `porque` diz por que — os
    dois em uma ou duas frases, porque quem lê a aba está lendo quinze desses.

    `exemplos` são identificadores concretos (até uns poucos), para quem quiser
    ir olhar. Um ajuste que não sabe apontar um caso costuma ser um ajuste que
    ninguém consegue conferir.
    """
    if quantos is not None and not quantos:
        return                      # nada aconteceu, nada a dizer
    _REGISTRO.append({
        'categoria': categoria,
        'titulo': titulo,
        'quantos': quantos,
        'unidade': unidade,
        'efeito': efeito,
        'detalhe': detalhe,
        'porque': porque,
        'exemplos': list(exemplos)[:6],
    })


def listar():
    """Os ajustes registrados até agora, na ordem em que aconteceram."""
    return list(_REGISTRO)


def _ordenar(itens):
    """Por categoria (na ordem de `CATEGORIAS`) e, dentro dela, por gravidade."""
    ordem_cat = {c: i for i, (c, _, _) in enumerate(CATEGORIAS)}
    return sorted(itens,
                  key=lambda a: (ordem_cat.get(a.get('categoria'), 99),
                                 EFEITOS.get(a.get('efeito'), ('', 9))[1],
                                 -(a.get('quantos') or 0)))


def salvar(pasta):
    """Grava `Ajustes.json` na pasta do caso. Nunca levanta.

    Falhar aqui não pode custar o caso: o `.dss` já está escrito, e um registro
    que não gravou é uma perda de informação, não de resultado.
    """
    try:
        dados = dict(_CONTEXTO)
        dados['ajustes'] = _ordenar(_REGISTRO)
        caminho = os.path.join(pasta, NOME_ARQUIVO)
        with open(caminho, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(dados, f, ensure_ascii=False, indent=1)
        return caminho
    except Exception as exc:                     # pragma: no cover - defensivo
        print('  [AVISO] nao foi possivel gravar %s: %s' % (NOME_ARQUIVO, exc))
        return None


def anotar_execucao(pasta, titulo, entrada):
    """Acrescenta ao `Ajustes.json` de um caso uma anotacao feita na SIMULACAO.

    O registro nasce na conversao; alguns ajustes so se decidem ao rodar o dia
    — desligar um regulador que nao deixa o dia fechar, por exemplo. Isto
    grava a anotacao no arquivo ja existente, substituindo qualquer outra de
    mesmo `titulo`, e a REMOVE quando `entrada` e `None`: rodar de novo sem o
    recuo agir nao pode deixar uma anotacao velha dizendo o contrario.

    Nunca levanta e nunca mexe em `_REGISTRO`: o registro em memoria e o da
    conversao em curso, e este e o de um caso ja gravado.
    """
    caminho = os.path.join(str(pasta), NOME_ARQUIVO)
    if not os.path.exists(caminho):
        return None
    try:
        with open(caminho, encoding='utf-8') as f:
            dados = json.load(f)
        if not isinstance(dados, dict):
            return None
        antes = dados.get('ajustes') or []
        itens = [a for a in antes
                 if not (isinstance(a, dict) and a.get('titulo') == titulo)]
        if entrada:
            itens.append(dict(entrada))
        if itens == antes:
            return caminho             # nada a mudar: nao se toca no arquivo
        dados['ajustes'] = _ordenar(itens)
        with open(caminho, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(dados, f, ensure_ascii=False, indent=1)
        return caminho
    except Exception as exc:                     # pragma: no cover - defensivo
        print('  [AVISO] nao foi possivel anotar em %s: %s' % (NOME_ARQUIVO, exc))
        return None


def carregar(pasta):
    """Lê o registro de uma pasta de caso, ou `None` se não houver.

    `None` é resposta legítima e comum: um caso convertido por uma versão
    anterior não tem o arquivo, e a janela precisa dizer isso em vez de parecer
    que a conversão não ajustou nada.
    """
    caminho = os.path.join(str(pasta), NOME_ARQUIVO)
    if not os.path.exists(caminho):
        return None
    try:
        with open(caminho, encoding='utf-8') as f:
            dados = json.load(f)
    except Exception:
        return None
    if not isinstance(dados, dict):
        return None
    dados['ajustes'] = _ordenar(dados.get('ajustes') or [])
    return dados


def por_categoria(dados):
    """`[(chave, rótulo, descrição, [ajustes])]`, só com as categorias usadas."""
    if not dados:
        return []
    grupos = {}
    for a in dados.get('ajustes') or []:
        grupos.setdefault(a.get('categoria'), []).append(a)
    fora = []
    for chave, rotulo, descricao in CATEGORIAS:
        if chave in grupos:
            fora.append((chave, rotulo, descricao, grupos.pop(chave)))
    for chave, itens in grupos.items():          # categoria não prevista
        fora.append((chave, str(chave), '', itens))
    return fora
