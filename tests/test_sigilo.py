# -*- coding: utf-8 -*-
"""Nada do projeto privado de onde este pacote saiu pode aparecer aqui.

Este repositório é público. Ele nasceu como um recorte de um projeto de
pesquisa maior, que continua privado e que tem trabalho ainda não publicado.
Nada disso é deste pacote, e nada disso pode vazar para cá nem em comentário.

Enquanto o pacote era *gerado* a partir daquele projeto, quem impedia o
vazamento era o próprio gerador: ele reescrevia comentários e apagava trechos
no caminho. Os dois projetos agora são independentes, e essa proteção sumiu
junto com o gerador.

Este teste ocupa o lugar dela, e cobre mais: o gerador só sanava o que vinha de
lá, enquanto aqui vale para tudo o que for escrito daqui em diante — inclusive
por quem nunca ouviu falar do outro projeto.

O critério é deliberadamente estreito. Proibir termo genérico transformaria o
teste num incômodo que se aprende a silenciar: "Monte Carlo", por exemplo, é
permitido, porque nomeia a amostragem Beta da curva fotovoltaica que este
pacote de fato faz — e porque o README precisa poder dizer que o pacote **não**
faz Monte Carlo de cenários. O que se proíbe é o vocabulário dos métodos do
outro projeto, que não têm lugar num conversor de cadastro para caso OpenDSS.
"""
import io
import os
import re

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Onde procurar. Fora ficam `.git`, caches e o que não é texto.
PASTAS = ('src', 'tests', 'docs', 'scripts', '.github')
SOLTOS = ('README.md', 'CITATION.cff', 'pyproject.toml')
EXTENSOES = ('.py', '.md', '.txt', '.toml', '.cff', '.yml', '.yaml', '.cfg')

#: O arquivo cujo trabalho é enunciar a política, e que por isso precisa poder
#: nomeá-la. "Nada de modelo de propensão" é mais claro que qualquer
#: circunlóquio que escapasse do filtro, e um documento vago sobre sigilo não
#: protege ninguém.
#:
#: A lista é curta de propósito e há um teste que a mantém assim: isentar um
#: arquivo é abrir um buraco, e um buraco que cresce sem ninguém notar deixa de
#: ser exceção e vira a regra.
ISENTOS = ('tests/test_sigilo.py',)

#: O que não pode aparecer, e por quê. A mensagem entra na falha do teste, para
#: que quem tropeçar entenda o motivo sem ter de vir ler este arquivo.
PROIBIDO = [
    (r'propens[ãa]o|propensity',
     'modelo de adoção do projeto privado, ainda não publicado'),
    (r'floresta aleat[óo]ria|random[ _-]?forest',
     'o classificador do projeto privado, ainda não publicado'),
    (r'aprendizado de m[áa]quina|machine[ _-]learning|\bscikit',
     'método do projeto privado, ainda não publicado'),
    (r'renda[-_ ]ibge|setor[ _-]censit[áa]rio|socioecon[ôo]mic',
     'enriquecimento socioeconômico do projeto privado: não é da BDGD, não '
     'entra no caso OpenDSS, e o estudo que o usa não é público'),
]


def _arquivos():
    for pasta in PASTAS:
        raiz = os.path.join(RAIZ, pasta)
        for base, dirs, nomes in os.walk(raiz):
            dirs[:] = [d for d in dirs
                       if d not in ('__pycache__', '.pytest_cache')]
            for nome in nomes:
                if nome.endswith(EXTENSOES):
                    yield os.path.join(base, nome)
    for nome in SOLTOS:
        caminho = os.path.join(RAIZ, nome)
        if os.path.isfile(caminho):
            yield caminho


def _ler(caminho):
    return io.open(caminho, encoding='utf-8', errors='replace').read()


@pytest.mark.parametrize('padrao,motivo', PROIBIDO,
                         ids=[p[:24] for p, _ in PROIBIDO])
def test_nada_do_projeto_privado_aparece(padrao, motivo):
    rx = re.compile(padrao, re.IGNORECASE)
    achados = []
    for caminho in _arquivos():
        rel = os.path.relpath(caminho, RAIZ).replace(os.sep, '/')
        if rel in ISENTOS:
            continue
        for n, linha in enumerate(_ler(caminho).splitlines(), 1):
            if rx.search(linha):
                achados.append('%s:%d: %s'
                               % (os.path.relpath(caminho, RAIZ), n,
                                  linha.strip()[:100]))
    assert not achados, (
        '%s\n\n%s\n\nEste repositório é público; o material acima é do projeto '
        'privado de onde ele saiu.' % (motivo, '\n'.join(achados)))


def test_o_teste_esta_mesmo_olhando_para_o_pacote():
    """Uma varredura que não varre nada passa calada — esta não pode.

    Sem isto, um erro de caminho (pasta renomeada, teste rodado de outro
    diretório) transformaria a garantia em teatro: zero arquivos, zero
    achados, teste verde.
    """
    arquivos = list(_arquivos())
    assert len(arquivos) > 30, arquivos
    nomes = {os.path.basename(a) for a in arquivos}
    assert {'rede.py', 'motor.py', 'mapa.py', 'README.md'} <= nomes


def test_a_lista_de_isentos_nao_cresce():
    """Isentar arquivo é abrir buraco; que abri-lo custe uma linha aqui.

    O único isento é o que enuncia a política. Qualquer segundo é um arquivo
    que passou a poder conter o que o teste existe para impedir — e isso é
    uma decisão, não um detalhe de implementação.
    """
    assert ISENTOS == ('tests/test_sigilo.py',)
    for rel in ISENTOS:
        assert os.path.isfile(os.path.join(RAIZ, rel)), rel


def test_o_padrao_pega_o_que_deveria(tmp_path):
    """E que os padrões de fato casam — senão o teste acima também é teatro."""
    amostras = ['modelo de propensão calibrado', 'usa random forest',
                'treinado com scikit-learn', 'renda por setor censitário']
    for texto in amostras:
        assert any(re.search(p, texto, re.IGNORECASE) for p, _ in PROIBIDO), texto


def test_monte_carlo_da_curva_pv_continua_permitido():
    """O critério é estreito de propósito, e isto fixa o limite.

    A curva fotovoltaica deste pacote é a média de sorteios Beta — é Monte
    Carlo, é publicado, e chamá-lo pelo nome é correto. Se alguém "endurecer" o
    teste proibindo o termo, é aqui que a intenção aparece.
    """
    from bdgdcase import curva_pv
    assert 'Monte Carlo' in (curva_pv.gerar_curva_pv_beta.__doc__ or '')
