# -*- coding: utf-8 -*-
"""O que uma distribuidora é, para este conversor.

## Por que estes módulos existem, e por que não são o que parecem

A leitura natural de "faça um arquivo por concessionária" é que a conversão
passe a ter um caminho por distribuidora. **Não é o que está aqui, e é
deliberado.**

Ao investigar as quatro bases, a diferença entre elas nunca foi de esquema: as
geodatabases têm as mesmas 43 camadas, os mesmos campos e o mesmo CRS. O que
muda é o *conteúdo* — uma base escreve `TIP_TRAFO='B'` onde a outra não usa
esse valor, uma tem `TIP_CC` vazio, outra inverte a ordem dos PACs do ramal de
ligação. E toda vez que a correção foi escrita como *regra sobre o dado*, ela
serviu a todas de uma vez; toda vez que foi escrita como *convenção de uma
delas*, quebrou na seguinte.

O caso que mais ensinou: o limiar `if ten >= 0.30: ten /= √3` estava certo numa
base porque a baixa dela é toda 380/220. Escrito como `se for a distribuidora
X`, continuaria errado na quarta. Escrito como *`TEN_LIN_SE` é tensão de linha,
sempre*, ficou certo em todas — e a própria base provou a regra, com os trafos
trifásicos de 0,22 kV medindo 128 V fase-neutro.

Então a conversão **não** ramifica por distribuidora, e estes módulos são outra
coisa, mais útil e mais barata de manter:

1. **Onde o conhecimento fica escrito.** Cada arquivo diz o que aquela base tem
   de particular, com número medido ao lado, e qual decisão foi tomada por causa
   disso. É o que faltava quando a segunda base entrou.

2. **A conferência.** Cada perfil declara o que se espera encontrar, e
   `conferir()` compara com o alimentador que está sendo convertido. Uma base que
   se afaste do que foi documentado é dita em voz alta, em vez de sair num `.dss`
   plausível e errado — que foi exatamente o modo de falha das três primeiras.

3. **O ponto de extensão.** Se uma quinta base exigir de fato um caminho
   próprio, ele entra aqui, num arquivo só, com o motivo ao lado — e não
   espalhado por `modelo/`.

## O perfil desconhecido não é erro

Uma distribuidora que não esteja aqui converte igual. `DESCONHECIDA` existe
para isso: as regras do conversor são sobre o dado, e não sobre quem o
publicou. O que se perde sem perfil é a conferência — ninguém compara o que
veio com o que se esperava, porque não há expectativa registrada.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Achado:
    """Uma divergência entre o que o perfil espera e o que a base trouxe."""

    #: 'aviso' quando o caso sai utilizável; 'erro' quando o resultado é suspeito.
    nivel: str
    #: O que foi observado, com número.
    texto: str

    def __str__(self):                       # pragma: no cover - só apresentação
        """O achado em uma linha, com o nível na frente."""
        return '[%s] %s' % (self.nivel.upper(), self.texto)


@dataclass(frozen=True)
class Distribuidora:
    """O que se sabe de uma base, e o que se confere nela.

    Os campos são expectativas medidas, não configuração: mudá-los não muda a
    conversão, muda o que a conferência considera normal. Quem alterar um deles
    deve trocar junto o número no comentário do módulo que o define.
    """

    #: Código `DIST` da BDGD. É o identificador estável — o nome comercial muda.
    dist: str
    nome: str
    #: Uma linha sobre o que esta base tem de próprio.
    resumo: str

    #: Tensões de linha do secundário de baixa esperadas, em kV.
    tensoes_bt_kv: tuple = ()
    #: Valores de `TIP_TRAFO` que esta base usa.
    tipos_de_trafo: tuple = ()
    #: A camada `CRVCRG` vem preenchida?
    tem_curvas_de_carga: bool = True
    #: `TIP_CC` vem preenchido nas unidades consumidoras?
    tem_tipologia_de_carga: bool = True
    #: `DESCR` do UNCRMT distingue banco de subestação por prefixo?
    capacitor_tem_prefixo: bool = True
    #: No RAMLIG, `PAC_1` é o poste (True) ou a unidade consumidora (False)?
    ramal_pac1_e_poste: bool = True

    #: Conferências extras deste perfil: `(tabelas) -> lista de Achado`.
    conferencias: tuple = field(default=(), repr=False)

    # ── Conferência ────────────────────────────────────────────────────────

    def conferir(self, tabelas):
        """Compara o alimentador com o que este perfil declara esperar.

        `tabelas` é o dicionário de DataFrames que a conversão já carregou. O
        que não estiver lá é simplesmente não conferido — a conferência nunca
        pode ser motivo de o caso não sair.
        """
        achados = []
        achados.extend(self._conferir_tensao_bt(tabelas))
        achados.extend(self._conferir_tipos_de_trafo(tabelas))
        achados.extend(self._conferir_curvas(tabelas))
        achados.extend(self._conferir_tipologia(tabelas))
        achados.extend(self._conferir_ramal(tabelas))
        for extra in self.conferencias:
            try:
                achados.extend(extra(tabelas) or [])
            except Exception as exc:         # pragma: no cover - defensivo
                achados.append(Achado('aviso',
                                      'conferencia extra falhou: %s' % exc))
        return achados

    # As conferências abaixo são propositalmente tolerantes: coluna ausente,
    # tabela vazia e valor ilegível não geram achado. O objetivo é apontar o que
    # DIVERGE do documentado, não reclamar do que falta — disso já cuida o log
    # de camadas ausentes da própria conversão.

    def _conferir_tensao_bt(self, tabelas):
        """As tensões de secundário desta base são as que o perfil espera?

        Tensão fora do conjunto conhecido é o primeiro sinal de que
        `TEN_LIN_SE` está sendo lida com a semântica errada — e esse é o erro
        que não aparece em pu.
        """
        col = _coluna(tabelas.get('untrmt'), 'TEN_LIN_SE')
        if col is None or not self.tensoes_bt_kv:
            return []
        vistos = {round(float(v), 3) for v in col.dropna()
                  if _numero(v) and 0 < float(v) < 10}
        novos = sorted(vistos - {round(v, 3) for v in self.tensoes_bt_kv})
        if not novos:
            return []
        return [Achado('aviso',
                       'tensao de baixa nao documentada para %s: %s kV '
                       '(esperadas %s)'
                       % (self.nome, novos,
                          list(self.tensoes_bt_kv)))]

    def _conferir_tipos_de_trafo(self, tabelas):
        """Os tipos de transformador são os que este perfil conhece?

        Tipo novo costuma trazer ligação nova junto, e ligação nova lida como a
        antiga põe o número de nós errado na barra.
        """
        col = _coluna(tabelas.get('untrmt'), 'TIP_TRAFO')
        if col is None or not self.tipos_de_trafo:
            return []
        vistos = {str(v).strip().upper() for v in col.dropna()}
        novos = sorted(vistos - set(self.tipos_de_trafo) - {''})
        if not novos:
            return []
        return [Achado('erro',
                       'TIP_TRAFO nao documentado para %s: %s. O roteamento de '
                       'fases pode estar errado neste alimentador.'
                       % (self.nome, novos))]

    def _conferir_curvas(self, tabelas):
        # A chave ausente é diferente da tabela vazia, e só a segunda é
        # observação. Conferir o que não foi passado transformaria a conferência
        # em ruído, e ruído é o que faz o aviso legítimo passar despercebido.
        """A base traz curvas de carga próprias, ou o caso vai usar as de referência?

        Uma `CRVCRG` que existe com os 101 campos e **zero feições** não
        denuncia nada no tamanho do arquivo. Este é o achado que faz a
        substituição ficar declarada em vez de silenciosa.
        """
        if 'crvcrg' not in tabelas:
            return []
        crv = tabelas.get('crvcrg')
        tem = crv is not None and len(crv) > 0
        if tem == self.tem_curvas_de_carga:
            return []
        if self.tem_curvas_de_carga:
            return [Achado('aviso',
                           'CRVCRG vazia, e o perfil de %s a esperava '
                           'preenchida: as curvas virao do catalogo de '
                           'referencia embarcado.' % self.nome)]
        return [Achado('aviso',
                       'CRVCRG veio preenchida, e o perfil de %s a esperava '
                       'vazia: melhor do que o documentado.' % self.nome)]

    def _conferir_tipologia(self, tabelas):
        """O `TIP_CC` das unidades vem preenchido, ou as curvas cairão na classe?

        Base que não preenche a tipologia costuma ser a mesma que não publica o
        catálogo — e aí toda a carga cairia numa curva só.
        """
        col = _coluna(tabelas.get('ucbt'), 'TIP_CC')
        if col is None or not len(col):
            return []
        cheio = col.astype(str).str.strip().ne('').mean()
        tem = cheio > 0.5
        if tem == self.tem_tipologia_de_carga:
            return []
        if self.tem_tipologia_de_carga:
            return [Achado('aviso',
                           'TIP_CC preenchido em so %.0f%% das unidades, e o '
                           'perfil de %s o esperava completo: a classe da carga '
                           'vem de CLAS_SUB/GRU_TAR.' % (100 * cheio, self.nome))]
        return []

    def _conferir_ramal(self, tabelas):
        """Os ramais de ligação chegam a pontos que existem na baixa tensão?

        Ramal pendurado em ponto que não está na `SSDBT` é carga que não entra
        no caso, e o caso converge sem ela — mais leve do que a rede real, sem
        avisar.
        """
        ram = tabelas.get('ramlig')
        if ram is None or not len(ram) or 'PAC_1' not in ram.columns:
            return []
        ssdbt = tabelas.get('ssdbt')
        if ssdbt is None or not len(ssdbt) or 'PAC_1' not in ssdbt.columns:
            return []
        rede = set(ssdbt['PAC_1'].astype(str)) | set(ssdbt['PAC_2'].astype(str))
        p1_na_rede = ram['PAC_1'].astype(str).isin(rede).mean()
        p2_na_rede = ram['PAC_2'].astype(str).isin(rede).mean()
        observado = p1_na_rede >= p2_na_rede
        if observado == self.ramal_pac1_e_poste:
            return []
        return [Achado('aviso',
                       'no RAMLIG o poste parece estar em PAC_%d, e o perfil de '
                       '%s diz PAC_%d. A coordenada e herdada nos dois sentidos, '
                       'entao o desenho sai certo de qualquer jeito.'
                       % (2 if observado else 1, self.nome,
                          1 if self.ramal_pac1_e_poste else 2))]


def _coluna(df, nome):
    """A coluna pedida, ou None se a tabela ou a coluna não existirem.

    Cada base publica um conjunto diferente de campos, e coluna ausente é o
    caso comum, não a exceção: a conferência que não a encontra se cala, em vez
    de derrubar a leitura da base inteira.
    """
    if df is None or not len(df) or nome not in getattr(df, 'columns', []):
        return None
    return df[nome]


def _numero(v):
    """O valor é conversível para número?"""
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


#: O que se usa quando o `DIST` não está registrado. Converte igual — o que
#: falta é a conferência, porque não há expectativa com que comparar.
DESCONHECIDA = Distribuidora(
    dist='',
    nome='distribuidora não registrada',
    resumo=('Sem perfil. A conversão funciona: as regras são sobre o dado, e '
            'não sobre quem o publicou. O que não há é com o que comparar.'),
    tensoes_bt_kv=(),
    tipos_de_trafo=(),
)
