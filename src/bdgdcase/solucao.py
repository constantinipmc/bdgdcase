"""Resolver o caso gerado.

Este módulo é a única parte do pacote que fala com o OpenDSS, e o
`import opendssdirect` mora **dentro** das funções, de propósito: a DLL do
simulador levanta uma exceção SEH ao carregar no Windows (benigna, tratada
pelo próprio runtime Pascal, mas ruidosa), e não há razão para `import
bdgdcase` ou `bdgdcase --help` pagarem esse preço. `tests/test_importacao.py`
exige que continue assim.

Duas operações, de profundidades diferentes:

* :func:`verificar` — o caso compila, resolve e converge? É a pergunta que fecha
  o ciclo do conversor: sem ela o pacote promete um modelo utilizável e prova
  apenas que os bytes são estáveis.
* :func:`rodar_dia` — 96 passos de 15 minutos, devolvendo potência no ponto de
  entrega, perdas e faixa de tensão. É o suficiente para desenhar a curva de
  carga do alimentador e para conferir o fechamento energético.

O que sai daqui é **o dia que a BDGD descreve**: as cargas e a geração que já
constam do cadastro, sob as curvas regulatórias da própria base. Não há
cenário, sorteio nem projeção — para isso, veja `docs/RESULTADOS.md`, que
mostra como chegar a qualquer outra grandeza a partir do circuito já resolvido.
"""
from __future__ import annotations

import math
import os

__all__ = ['verificar', 'rodar_dia', 'para_csv', 'caminho_exemplo',
           'PASSOS_DIA', 'MINUTOS_PASSO']

#: Um dia em passos de 15 minutos — a resolução das curvas CRVCRG da BDGD.
PASSOS_DIA = 96
MINUTOS_PASSO = 15

#: Abaixo disto a fase não está conectada naquele ponto, e não entra em
#: estatística de tensão nenhuma.
#:
#: O valor não é folga de segurança — é a fronteira entre duas coisas
#: diferentes. Uma rede de distribuição real tem laterais monofásicas e
#: bifásicas: o barramento declara três nós, e o nó da fase ausente fica
#: *flutuando*, acoplado só capacitivamente. Medido no alimentador IBA09, essas
#: fases assentam entre 0,05 e 0,50 pu — bem acima de zero, e bem abaixo de
#: qualquer tensão de operação.
#:
#: Com um limiar de 0,02 pu elas passavam por fases vivas, e como a tensão da
#: barra é a *pior* fase, uma barra com duas fases em 0,97 pu era reportada em
#: 0,064. No IBA09 isso dava 689 barras "abaixo de 0,90 pu" onde há 53 — 636
#: falsos positivos, e o mapa pintado de vermelho onde a rede está sã.
#:
#: Nenhum ponto em operação de uma rede de distribuição fica abaixo de 0,5 pu:
#: o PRODIST considera crítico já em 0,87 pu na baixa tensão. Abaixo da metade
#: da nominal não é subtensão, é ausência de conexão.
#:
#: **Só que a fronteira não é um número.** No alimentador IAL02 as fases
#: flutuantes assentaram em 0,555 pu — um nó a três condutores de um
#: secundário a dois, acoplado aos vizinhos vivos — e o caso foi recusado por
#: "tensão fora do plausível" com 16 nós assim. Subir o limiar mudaria só
#: onde o próximo caso cai. O que separa fase viva de fase solta é
#: **caminho até a fonte**, e é isso que :func:`nos_conectados` responde
#: nó a nó, andando pelos elementos do circuito resolvido. O limiar fica
#: como recuo para quando essa resposta não está à mão (resultados gravados
#: antes dela).
PU_FASE_ATIVA = 0.5

#: Abaixo disto a potência na cabeceira é ruído numérico, e não carga. Um
#: alimentador de verdade não passa o dia inteiro sob este valor.
LIMIAR_DIA_MORTO_KW = 1.0

#: Quanto o balanço de potência pode deixar de fechar antes de a solução deixar
#: de ser solução, como fração da potência na cabeceira.
#:
#: O que entra no circuito tem de sair: fonte + geração = carga + perdas. É
#: identidade, não aproximação, e um resíduo mede diretamente o quanto a
#: solução **não** satisfaz as leis de Kirchhoff. `Converged()` não mede isso —
#: ele compara a variação de tensão entre iterações, e uma iteração que
#: *empaca* satisfaz esse teste sem resolver nada.
#:
#: Medido num alimentador de 41 mil barras com 18 reguladores: no passo das
#: 23h45 a fonte entregava 8.119 kW enquanto cargas e perdas somavam 6.078 —
#: **2.041 kW que não existiam em elemento nenhum**, com `Converged=True` em
#: quatro iterações. Cinquenta dos 96 passos do dia estavam assim, e o efeito
#: visível era uma curva de carga achatada de madrugada e um pico às 23h45,
#: três horas depois do pico real das cargas.
#:
#: Um por cento é folgado de propósito: o resíduo honesto de um caso são fica
#: em 0,03%, e nos sete alimentadores sadios medidos nenhum passou de 0,35%.
#: Quem precisa ser pego erra por 25 a 45%.
TOLERANCIA_BALANCO = 0.01

#: A partir de que fração de passos recusados o dia é rodado de novo SEM os
#: reguladores. Ver `rodar_dia` — o recuo, e o que foi medido antes dele.
#:
#: Cinco por cento são cinco passos em 96. Um caso são não perde nenhum; os
#: que perdem, perdem dezenas (38, 46, 90). Não há zona cinzenta medida entre
#: um e dez que o limiar pudesse cortar errado.
FRACAO_RECUO_REGULADOR = 0.05

def caminho_exemplo(nome='TRO05_DU'):
    """O caso de exemplo que viaja dentro do pacote.

    Ele é instalado junto com o código, e não só versionado no repositório, por
    um motivo prático: assim quem acabou de instalar consegue rodar a coisa
    inteira em cinco segundos, antes de encarar o download de gigabytes da
    BDGD. Instalação que só dá para testar depois de um dia de trabalho é
    instalação que ninguém confirma.

    São 153 KB — o mesmo caso que os testes comparam byte a byte.
    """
    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'exemplo', nome)
    if not os.path.isdir(caminho):
        raise FileNotFoundError('exemplo %r não encontrado em %s' % (nome, caminho))
    return caminho


#: O motor em uso. `None` é o global do `opendssdirect`; depois de uma violação
#: de acesso ele é trocado por um contexto novo — ver :func:`_trocar_motor`.
_MOTOR = None


def _dss():
    """Devolve o OpenDSS pronto para uso, importando-o sob demanda."""
    if _MOTOR is not None:
        return _MOTOR
    try:
        import opendssdirect as dss
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise RuntimeError(
            'O OpenDSS não pôde ser carregado. Ele é dependência do pacote, '
            'então a instalação está incompleta ou quebrada: '
            'pip install --force-reinstall bdgdcase') from exc
    return dss


def _trocar_motor():
    """Descarta o motor corrompido e instala um contexto novo.

    Depois de uma violação de acesso o motor do OpenDSS não volta: **todo**
    comando seguinte falha, repetindo a descrição do erro original. Num script
    isso encerra o trabalho; numa janela é pior, porque a janela continua
    aberta e cada caso novo parece quebrado. A orientação que sobrava era
    fechar e reabrir o programa.

    `NewContext` dá um motor limpo dentro do mesmo processo. O corrompido é
    abandonado — não há como consertá-lo —, e o circuito que estava carregado
    nele se perde junto, o que não custa nada: quem chama isto está prestes a
    compilar de novo.

    Devolve o motor novo, ou `None` quando esta instalação não oferece
    contextos — aí não há o que fazer além de avisar.
    """
    global _MOTOR
    try:
        import opendssdirect

        novo = opendssdirect.NewContext()
        novo.Text.Command('Clear')
    except Exception:      # pragma: no cover - depende da versão instalada
        return None
    _MOTOR = novo
    return novo


def _master(pasta):
    """O caminho do `Master.dss` da pasta, com erro que diz o que fazer.

    Apontar para `Output/` em vez de `OpenDSS/` é o engano mais comum de quem
    está começando, e a mensagem do OpenDSS para isso não ajuda em nada.
    """
    caminho = os.path.join(str(pasta), 'Master.dss')
    if not os.path.isfile(caminho):
        raise FileNotFoundError(
            'Master.dss não encontrado em %s. Gere o caso antes: '
            'bdgdcase converter Output/{ALIMENTADOR}' % pasta)
    # Arquivo vazio é caso diferente de arquivo ausente, e merece dizer isso.
    # O OpenDSS compila um Master vazio sem reclamar: não há erro de sintaxe em
    # nada, e ele simplesmente termina sem circuito. Quem abre a pasta vê os
    # outros dez `.dss` no lugar e não desconfia do que está com zero byte.
    #
    # Acontece de verdade: o programa de mesa do OpenDSS, aberto sobre a pasta,
    # pode truncar o Master ao salvar.
    if os.path.getsize(caminho) == 0:
        raise RuntimeError(
            'Master.dss está vazio (0 bytes) em %s. Os outros arquivos do caso '
            'podem estar íntegros, mas sem o Master não há circuito. Gere o '
            'caso de novo: bdgdcase converter Output/{ALIMENTADOR}' % pasta)
    return os.path.abspath(caminho)


#: Comandos que `_compilar` de fato emite. Serve para reconhecer quando a
#: descrição de erro que o OpenDSS devolve fala de OUTRO comando — ver
#: `_erro_e_de_outro_comando`.
_COMANDOS_DE_COMPILAR = ('clear', 'compile')


def _erro_e_de_outro_comando(texto):
    """A descrição do erro fala de um comando que não é de compilação?

    O OpenDSS guarda a última descrição de erro num buffer, e ela nem sempre é
    substituída quando o comando seguinte falha. Depois de uma violação de
    acesso, o motor fica corrompido para o resto do processo e **todo** comando
    passa a falhar — mas exibindo o texto do erro ORIGINAL.

    Foi o que aconteceu num caso real: `Clear` falhou, e a mensagem entregue
    dizia `Set MaxIter=300`, comando de `rodar_dia` que só roda depois de
    compilar. Quem lê aquilo procura defeito no caso que está abrindo, e o
    defeito era de uma execução anterior, no mesmo processo.
    """
    marca = 'exception raised while processing dss command:'
    baixo = str(texto).lower()
    if marca not in baixo:
        return False
    trecho = baixo.split(marca, 1)[1].strip()
    return not trecho.startswith(_COMANDOS_DE_COMPILAR)


def _avisar(recado):
    """Um aviso que chega a quem está lendo, sem derrubar o trabalho.

    `warnings` e não `print`: a linha de comando o mostra, quem embute o pacote
    pode capturá-lo, e um teste pode exigir que ele tenha saído. Um `print`
    aqui se perderia no meio da saída da conversão.
    """
    import warnings

    warnings.warn(recado, RuntimeWarning, stacklevel=3)


def _abrir(dss, caminho):
    """Limpa o motor e compila o Master. Separado para poder ser repetido."""
    dss.Text.Command('Clear')
    dss.Text.Command('Compile "%s"' % caminho)


def _abrir_com_recuo(dss, caminho, anterior):
    """Compila o caso; se o motor estiver corrompido, troca-o e tenta uma vez.

    Devolve `(motor, erro)`, com `erro` em `None` quando o caso abriu. O motor
    devolvido pode não ser o que entrou: depois de uma violação de acesso ele é
    substituído, e quem chamou tem de passar a usar o novo.
    """
    try:
        _abrir(dss, caminho)
        return dss, None
    except Exception as exc:
        if not _erro_e_de_outro_comando(exc):
            return dss, exc
        # O motor está corrompido de uma falha ANTERIOR, e o caso desta vez
        # pode estar perfeito. Troca-se o motor e tenta-se de novo, uma vez só:
        # se o caso for bom, ninguém precisa saber que isto aconteceu.
        os.chdir(anterior)
        novo = _trocar_motor()
        if novo is None:
            return dss, exc
        try:
            _abrir(novo, caminho)
        except Exception as exc2:
            return novo, exc2
        # Recuperado — mas NÃO em silêncio. O motor só chega a este estado
        # depois de uma violação de acesso, e isso quer dizer que alguma coisa
        # ANTES nesta sessão morreu no meio. Este caso está bom; o anterior
        # pode não estar, e quem estiver lendo os números precisa saber disso.
        _avisar(
            'o motor do OpenDSS estava corrompido por uma falha anterior nesta '
            'sessão e foi substituído por um limpo. %s abriu normalmente, mas '
            'o que rodou ANTES da falha pode ter saído incompleto — confira o '
            'registro.' % caminho)
        return novo, None


def _compilar(pasta):
    """Compila o caso e devolve o módulo dss pronto para resolver.

    O `Compile` do OpenDSS troca o diretório de trabalho do processo — é assim
    que ele resolve os `Redirect` relativos do Master. Deixar isso vazar
    mudaria o diretório do programa que chamou, então ele é restaurado aqui.
    """
    dss = _dss()
    caminho = _master(pasta)
    anterior = os.getcwd()
    try:
        dss, exc = _abrir_com_recuo(dss, caminho, anterior)
    finally:
        os.chdir(anterior)

    if exc is not None:
        # O erro do OpenDSS já nomeia arquivo e linha, o que é bom; o que não
        # serve é o TIPO. Quem chama `rodar_dia` trata RuntimeError, e uma
        # exceção do fornecedor escapando derruba a CLI com traceback em vez
        # de uma linha dizendo qual alimentador falhou.
        recado = 'o OpenDSS recusou %s%s  %s' % (caminho, chr(10), exc)
        if _erro_e_de_outro_comando(exc):
            recado += (
                '%s%sATENÇÃO: a descrição acima é de um comando que a abertura '
                'do caso não emite — ela sobrou de uma falha ANTERIOR neste '
                'mesmo processo. Depois de uma violação de acesso o motor do '
                'OpenDSS não se recupera; aqui já se tentou trocá-lo por um '
                'limpo e o caso continuou sendo recusado. Procure a primeira '
                'falha do registro antes de concluir qualquer coisa sobre '
                'ESTE caso.' % (chr(10), chr(10)))
        raise RuntimeError(recado) from exc

    # A sondagem tem de sobreviver ao caso que ela existe para detectar. Sem
    # circuito, `Circuit.Name()` não devolve vazio: levanta a exceção do
    # OpenDSS, e a guarda morria antes de dar o recado. Quem chamava recebia
    # "(#8888) There is no active circuit", sem uma palavra sobre a pasta.
    try:
        nome = dss.Circuit.Name()
    except Exception:
        nome = ''
    if not nome:
        detalhe = ''
        try:
            detalhe = (dss.Error.Description() or '').strip()
        except Exception:      # pragma: no cover - versão sem Error
            pass
        recado = 'o OpenDSS não carregou circuito nenhum a partir de ' + caminho
        if detalhe:
            recado += chr(10) + '  OpenDSS: ' + detalhe
        raise RuntimeError(recado)
    return dss


def _geracao_por_barra(dss):
    """Quanto de geracao cada barra carrega, em kW de placa."""
    import collections

    por_barra = collections.defaultdict(float)
    for cont, classe in ((getattr(dss, 'PVsystems', None), 'PVSystem'),
                         (getattr(dss, 'Generators', None), 'Generator')):
        if cont is None:
            continue
        try:
            i = cont.First()
        except Exception:      # pragma: no cover - build sem a colecao
            continue
        while i:
            try:
                barra = dss.CktElement.BusNames()[0].split('.')[0].lower()
                pot = cont.Pmpp() if classe == 'PVSystem' else cont.kW()
                por_barra[barra] += float(pot or 0.0)
            except Exception:
                pass
            i = cont.Next()
    return por_barra


def _trafo_acima(dss, barra):
    """O transformador que alimenta esta barra, e a potencia dele em kVA.

    Anda o circuito a partir da barra ate encontrar o secundario de um
    transformador. `None` quando nao encontra — barra de media, ou ilha sem
    transformador nenhum.
    """
    import collections

    adj = collections.defaultdict(set)
    secundario = {}
    i = dss.Lines.First()
    while i:
        b = dss.CktElement.BusNames()
        a, c = b[0].split('.')[0].lower(), b[1].split('.')[0].lower()
        adj[a].add(c)
        adj[c].add(a)
        i = dss.Lines.Next()
    i = dss.Transformers.First()
    while i:
        nome = dss.Transformers.Name()
        b = dss.CktElement.BusNames()
        if len(b) >= 2:
            secundario[b[1].split('.')[0].lower()] = (nome, float(dss.Transformers.kVA()))
        i = dss.Transformers.Next()

    vistos, fila = {barra}, collections.deque([barra])
    while fila:
        x = fila.popleft()
        if x in secundario:
            return secundario[x]
        for y in adj[x]:
            if y not in vistos:
                vistos.add(y)
                fila.append(y)
    return None


def _explicar_tensao(dss, pu_min, pu_max, conectados=None):
    """Onde a tensao saiu da faixa, e sob o que — para entrar no erro.

    A mensagem antiga mandava suspeitar de tensao de base, impedancia de cabo e
    ligacao de transformador. Nos casos medidos os tres eram o lugar errado: o
    culpado era uma unidade de geracao maior que o transformador que a alimenta,
    e o registro de ajustes ja dizia isso — so nao chegava a quem lia o erro.
    Duas investigacoes inteiras comecaram procurando impedancia de cabo.

    O que entra aqui e o que se mede sem interpretar: quantos nos sairam da
    faixa entre os energizados, em que barra esta o pior, e — quando ha geracao
    naquela barra — quanto ela e diante do transformador que a alimenta.
    """
    fora, vivos, pior = [], 0, None

    def anotar(b, pu):
        """Conta o nó como vivo e, se saiu da faixa, guarda-o (e o pior)."""
        nonlocal vivos, pior
        vivos += 1
        if not (pu_min <= pu <= pu_max):
            fora.append((b, float(pu)))
            if pior is None or abs(pu - 1.0) > abs(pior[1] - 1.0):
                pior = (b, float(pu))

    if conectados is not None:
        # Nó a nó, com a máscara de `nos_conectados`: a fase solta fica de
        # fora por não ter caminho, e não por estar abaixo de um número.
        for no, pu, vivo in zip(dss.Circuit.AllNodeNames(),
                                dss.Circuit.AllBusMagPu(), conectados):
            if vivo and math.isfinite(pu) and pu > 0.0:
                anotar(no.partition('.')[0].lower(), pu)
    else:
        for b in dss.Circuit.AllBusNames():
            dss.Circuit.SetActiveBus(b)
            for pu in dss.Bus.puVmagAngle()[::2]:
                if pu > PU_FASE_ATIVA:
                    anotar(b.lower(), pu)
    if not fora or pior is None:
        return ''

    recado = ['%d no(s) fora da faixa, de %d energizados (%.2f%%).'
              % (len(fora), vivos, 100.0 * len(fora) / max(vivos, 1)),
              'O pior esta em %s (%.3f pu).' % (pior[0], pior[1])]

    # A geracao e procurada entre TODAS as barras fora da faixa, e nao so na
    # pior: quem dispara a sobretensao e a barra da usina, e a que aparece com
    # o pu mais alto costuma ser a vizinha a jusante, que nao tem geracao
    # nenhuma. Apontar essa mandaria procurar no lugar errado de novo.
    por_barra = _geracao_por_barra(dss)
    candidatas = [(por_barra.get(b, 0.0), b, pu) for b, pu in fora
                  if por_barra.get(b, 0.0) > 0]
    if not candidatas:
        recado.append('Nenhuma dessas barras tem geracao.')
        return ' '.join(recado)

    gd, barra, pu = max(candidatas)
    trafo = _trafo_acima(dss, barra)
    if trafo and trafo[1] > 0:
        recado.append(
            'A barra %s (%.3f pu) tem %.0f kW de geracao sobre o transformador '
            '%s, de %.0f kVA (%.1fx).'
            % (barra, pu, gd, trafo[0], trafo[1], gd / trafo[1]))
        # So se fala em excesso quando ha excesso. Dez kW em 30 kVA nao
        # explicam nada — e o UVA01 saiu com esta frase apontando para uma
        # usina de 0,3x enquanto a causa era a media inteira a 0,8 pu.
        if gd > trafo[1] and pu > 1.0:
            recado.append(
                'Geracao acima do que o transformador aguenta e condicao do '
                'cadastro, e nao defeito do modelo: veja "Geracao maior que o '
                'transformador que a alimenta" no registro de ajustes.')
        else:
            recado.append(
                'Essa geracao nao explica a tensao: procure a causa a montante '
                '(a media tensao, o transformador, ou a carga do circuito).')
    else:
        recado.append('A barra %s (%.3f pu) tem %.0f kW de geracao.'
                      % (barra, pu, gd))
    return ' '.join(recado)


def nos_conectados(dss):
    """Quais nós têm caminho condutor até uma fonte — na ordem de `AllNodeNames`.

    Anda pelos elementos de potência do circuito: numa linha (e em qualquer
    elemento de dois terminais que não seja transformador), o condutor `i` de
    um terminal liga ao condutor `i` do outro — nó a nó, e não barra a
    barra, que é a diferença que importa; num transformador, todos os nós de
    todos os enrolamentos ficam juntos, porque o acoplamento é magnético.
    Terminal aberto (chave) não conduz. Elemento desligado não entra.

    O que sobra de fora é a fase solta: o terceiro condutor de um circuito
    declarado `.1.2.3` sob um secundário que só tem dois. Ela tem tensão —
    pega dos vizinhos, por acoplamento —, e por isso nenhum limiar a
    separa da fase viva com segurança. Isto separa.
    """
    nomes = [n.lower() for n in dss.Circuit.AllNodeNames()]
    indice = {n: i for i, n in enumerate(nomes)}
    adj = [[] for _ in nomes]

    def no_de(barra, k):
        """Índice do nó `barra.k`, ou None se o circuito não o tem."""
        return indice.get('%s.%d' % (barra, k))

    def liga(a, b):
        """Aresta entre dois nós; ignora terra e nós que não existem."""
        if a is not None and b is not None and a != b:
            adj[a].append(b)
            adj[b].append(a)

    sementes = []
    for nome in dss.Circuit.AllElementNames():
        tipo = nome.split('.', 1)[0].lower()
        if tipo in ('vsource', 'isource'):
            dss.Circuit.SetActiveElement(nome)
            if not dss.CktElement.Enabled():
                continue
            for bruto in dss.CktElement.BusNames():
                barra = bruto.split('.')[0].lower()
                for k in range(1, 4):
                    i = no_de(barra, k)
                    if i is not None:
                        sementes.append(i)
            continue
        if tipo not in ('line', 'transformer', 'reactor', 'autotrans', 'gictransformer'):
            continue
        dss.Circuit.SetActiveElement(nome)
        if not dss.CktElement.Enabled():
            continue
        n_term = int(dss.CktElement.NumTerminals())
        n_cond = int(dss.CktElement.NumConductors())
        if n_term < 2 or n_cond < 1:
            continue
        ordem = list(dss.CktElement.NodeOrder())
        barras = [b.split('.')[0].lower() for b in dss.CktElement.BusNames()]
        # nós por terminal, na ordem dos condutores; 0 é terra e não conduz nada
        por_terminal = []
        for t in range(n_term):
            fatia = ordem[t * n_cond:(t + 1) * n_cond]
            por_terminal.append([
                (no_de(barras[t], k) if (k and t < len(barras)) else None)
                for k in fatia])
        if tipo in ('transformer', 'autotrans', 'gictransformer'):
            todos = [i for lado in por_terminal for i in lado if i is not None]
            for i in todos[1:]:
                liga(todos[0], i)
            continue
        for c in range(n_cond):
            aberto = False
            for t in range(n_term):
                try:
                    if dss.CktElement.IsOpen(t + 1, c + 1):
                        aberto = True
                        break
                except Exception:              # pragma: no cover - defensivo
                    pass
            if aberto:
                continue
            for t in range(1, n_term):
                liga(por_terminal[0][c], por_terminal[t][c])

    vivo = [False] * len(nomes)
    fila = list(sementes)
    for i in fila:
        vivo[i] = True
    while fila:
        i = fila.pop()
        for j in adj[i]:
            if not vivo[j]:
                vivo[j] = True
                fila.append(j)
    return vivo


def _tensoes_pu(dss, conectados=None):
    """(mínima, média, máxima) em pu sobre as fases conectadas do circuito.

    ``conectados``: a máscara de :func:`nos_conectados`, quando quem chama já
    a tem (o dia inteiro a reaproveita). Sem ela vale o limiar
    `PU_FASE_ATIVA`, que é o recuo.
    """
    todos = dss.Circuit.AllBusMagPu()
    if conectados is not None and len(conectados) == len(todos):
        vivos = [v for v, c in zip(todos, conectados)
                 if c and math.isfinite(v) and v > 0.0]
    else:
        vivos = [v for v in todos if math.isfinite(v) and v > PU_FASE_ATIVA]
    if not vivos:
        return float('nan'), float('nan'), float('nan')
    return min(vivos), sum(vivos) / len(vivos), max(vivos)


def _potencia(dss):
    """Potência no ponto de entrega, em kW e kVAr, com P > 0 = importação.

    `Circuit.TotalPower` soma pelas fontes, com sinal invertido em relação ao
    que se costuma querer ler: aqui **P negativo significa exportação
    líquida**, isto é, o alimentador devolvendo energia ao sistema.
    """
    tp = dss.Circuit.TotalPower()
    return -float(tp[0]), -float(tp[1])


def _perdas_kw(dss):
    """Perdas do circuito em kW e kVAr (o OpenDSS as devolve em W e var)."""
    p, q = dss.Circuit.Losses()
    return float(p) / 1000.0, float(q) / 1000.0


# ── Nível 1: o caso resolve? ─────────────────────────────────────────────────

#: A faixa em que uma tensão de nó ainda é um número físico, e não um
#: sintoma de circuito aberto ou de curto. É deliberadamente larga: `verificar`
#: pergunta se o modelo fecha, e não se a tensão está dentro da norma — isso é
#: do PRODIST, e é do leitor.
PU_PLAUSIVEL_MIN = 0.5
PU_PLAUSIVEL_MAX = 1.5


def verificar(pasta, pu_min=PU_PLAUSIVEL_MIN, pu_max=PU_PLAUSIVEL_MAX):
    """Compila o caso, resolve um instantâneo e confere que faz sentido.

    Quatro perguntas, nesta ordem — cada uma só faz sentido se a anterior
    passou:

    1. o Master compila e produz um circuito; 2. a solução converge; 3. o
    circuito tem barras e cargas (um caso vazio também "converge"); 4. as
    tensões dos nós vivos caem numa faixa fisicamente plausível.

    A faixa padrão é folgada de propósito. O objetivo é pegar modelo quebrado —
    impedância trocada, fase pendurada, tensão de base errada —, e não julgar
    qualidade de tensão, que é assunto de norma e não deste pacote.

    **A solução é um instantâneo em regime nominal.** O modo `Snap` do OpenDSS
    não percorre as curvas de carga: cada elemento entra com o valor de placa.
    Os números de potência que saem daqui servem para dizer que o circuito
    fecha, e não para descrever a operação — se P sai negativo, é a geração
    nominal superando a carga nominal, não um horário de exportação. Para o dia
    de verdade, :func:`rodar_dia`.

    Devolve um dicionário com o que foi medido. Levanta `RuntimeError` na
    primeira pergunta que falhar, dizendo qual.
    """
    dss = _compilar(pasta)

    dss.Text.Command('Set Mode=Snap')
    dss.Solution.Solve()
    if not dss.Solution.Converged():
        raise RuntimeError('a solução não convergiu em %s' % pasta)

    n_barras = int(dss.Circuit.NumBuses())
    n_cargas = int(dss.Loads.Count())
    if n_barras == 0 or n_cargas == 0:
        raise RuntimeError('circuito vazio em %s: %d barras, %d cargas'
                           % (pasta, n_barras, n_cargas))

    conectados = nos_conectados(dss)
    v_min, v_med, v_max = _tensoes_pu(dss, conectados)
    # Modelo sem nó nenhum ligado à fonte — ou com a tensão de base errada
    # por uma ordem de grandeza, que zera tudo — não tem fase viva, e aí
    # `_tensoes_pu` devolve NaN. Sem este teste, a comparação com NaN seria
    # falsa e o caso quebrado passaria calado.
    if not math.isfinite(v_min):
        raise RuntimeError(
            'nenhuma fase energizada em %s: nenhum nó com caminho até a fonte '
            'e tensão acima de zero. Suspeite da tensão de base do circuito.'
            % pasta)
    if not (pu_min <= v_min and v_max <= pu_max):
        recado = ('tensões fora do plausível em %s: %.3f a %.3f pu (esperado '
                  'entre %.2f e %.2f).'
                  % (pasta, v_min, v_max, pu_min, pu_max))
        detalhe = _explicar_tensao(dss, pu_min, pu_max, conectados)
        if detalhe:
            recado += ' ' + detalhe
        else:
            recado += (' Suspeite de tensão de base, impedância de cabo ou '
                       'ligação de transformador.')
        raise RuntimeError(recado)

    p_kw, q_kvar = _potencia(dss)
    perdas_kw, _ = _perdas_kw(dss)
    return {
        'circuito': dss.Circuit.Name(),
        'convergiu': True,
        'barras': n_barras,
        'cargas': n_cargas,
        'linhas': int(dss.Lines.Count()),
        'transformadores': int(dss.Transformers.Count()),
        'v_min_pu': v_min,
        'v_med_pu': v_med,
        'v_max_pu': v_max,
        'p_kw': p_kw,
        'q_kvar': q_kvar,
        'perdas_kw': perdas_kw,
    }


# ── Nível 2: o dia inteiro ───────────────────────────────────────────────────

#: Acima disto a solução não é uma rede: é uma divergência que o OpenDSS não
#: reconheceu. Uma rede real não passa de ~1,2 pu, e 10 é folgado o bastante
#: para não confundir um caso ruim com um caso impossível.
PU_ABSURDO = 10.0


def _solucao_finita(dss):
    """A solução deste passo é um número, ou explodiu?

    `Solution.Converged()` não basta. Num caso medido, o passo das 19:00 — a
    hora em que a curva da iluminação pública acende — divergiu de fato e
    reportou `Converged=False`; os **vinte passos seguintes** reportaram
    `Converged=True` com tensões em `inf`, porque o solver reparte do estado
    anterior e nunca mais sai dele.

    Sem este teste, esses passos entram no resultado como bons. O sintoma que
    chegava ao usuário era um `RuntimeWarning: overflow encountered in cast` do
    numpy, ao guardar `inf` num vetor de 32 bits — uma mensagem que fala do
    tamanho do inteiro e não da rede.
    """
    import numpy as _np

    try:
        v = _np.asarray(dss.Circuit.AllBusMagPu(), dtype=_np.float64)
    except Exception:                            # pragma: no cover - defensivo
        return False
    if not v.size:
        return True
    mx = float(_np.nanmax(_np.abs(v)))
    return bool(_np.isfinite(mx) and mx < PU_ABSURDO)


def residuo_balanco(dss):
    """Quanto da potência que entra no circuito não aparece em elemento nenhum.

    Devolve `(residuo_kw, fracao)`. Zero é o valor correto: a soma da potência
    de **todos** os terminais de **todos** os elementos é identicamente nula
    num circuito resolvido — o que a fonte injeta, cargas e perdas consomem.

    **A fração é relativa ao que o circuito move — carga mais perdas —, e não
    à potência na cabeceira.** Num alimentador com geração a cabeceira passa
    por zero ao meio-dia, e ali qualquer resíduo vira uma fração enorme sem
    significado: no caso de referência do pacote, 0,115 kW de resíduo sobre
    −1,20 kW de cabeceira davam 9,6% enquanto o circuito movia 46 kW — o erro
    real é de 0,25%.

    Serve para a pergunta que `Converged()` não responde. O critério do OpenDSS
    é a variação de tensão entre iterações, e num circuito mal condicionado —
    regulador é impedância série pequena, e este pacote emite bancos de três
    monofásicos — a iteração empaca com a tensão parada e o resíduo enorme.
    Medido: 2.041 kW de resíduo declarados como convergidos em 4 iterações.

    Resolver de novo a partir desse estado **diverge** (8.119 → 15.723 →
    23.172 kW), o que confirma que ali não havia solução: a tolerância frouxa
    não arredondava a resposta, escondia a divergência.
    """
    soma = 0.0
    movida = 0.0
    try:
        for nome in dss.Circuit.AllElementNames():
            dss.Circuit.SetActiveElement(nome)
            if not dss.CktElement.Enabled():
                continue
            p = dss.CktElement.TotalPowers()
            terminais = [p[i] for i in range(0, len(p), 2)]
            soma += sum(terminais)
            # A escala: o que cargas consomem e o que se perde no caminho.
            if nome.lower().startswith(('load.', 'line.', 'transformer.')):
                movida += abs(sum(terminais))
    except Exception:                            # pragma: no cover - defensivo
        return 0.0, 0.0
    if not math.isfinite(soma) or not math.isfinite(movida):
        return float('inf'), float('inf')
    try:
        movida = max(movida, abs(float(dss.Circuit.TotalPower()[0])))
    except Exception:                            # pragma: no cover - defensivo
        pass
    return soma, abs(soma) / max(movida, 1e-9)


def _balanco_fecha(dss, tolerancia=TOLERANCIA_BALANCO):
    """O balanço de potência fecha? Ver `residuo_balanco` e `TOLERANCIA_BALANCO`."""
    _, fracao = residuo_balanco(dss)
    return fracao <= tolerancia


def _nomes_regcontrol(dss):
    """Os `RegControl` do circuito, inclusive os DESLIGADOS.

    `RegControls.First()/Next()` só percorre os habilitados: com
    `enabled=no`, `Count()` continua dizendo seis e o laço devolve zero. O
    recuo de `rodar_dia` desliga os reguladores em alguns alimentadores, e
    sem isto eles sumiam do inventário — e do mapa, que é onde o usuário
    precisa vê-los, com o tape em zero e a marca de desligado.

    `AllElementNames()` lista todos, e `RegControls.Name(nome)` seleciona um
    desligado normalmente: `Transformer()`, `TapNumber()` e `ForwardVreg()`
    respondem. Medido.
    """
    nomes = []
    try:
        for n in dss.Circuit.AllElementNames():
            if str(n).lower().startswith('regcontrol.'):
                nomes.append(str(n).split('.', 1)[1])
    except Exception:                            # pragma: no cover - defensivo
        pass
    return nomes


def _regcontrol_ativo(dss, nome):
    """O `RegControl` está habilitado? Lê pelo elemento, que sabe."""
    try:
        dss.Circuit.SetActiveElement('RegControl.' + nome)
        return bool(dss.CktElement.Enabled())
    except Exception:                            # pragma: no cover - defensivo
        return True


def _coletor_detalhado(dss, pasta, passos, medir_cargas=False,
                       conectados=None):
    """Prepara a coleta por barra e por linha; devolve (registrar, finalizar).

    Guardar 96 instantes de uma rede inteira é o que permite percorrer o dia
    depois, sem resolver de novo. Num alimentador urbano são 43 mil nós e 15
    mil linhas; em `float32` isso dá cerca de 22 MB, e é por isso que os
    vetores nascem com o tamanho final em vez de crescer por `append`.

    `medir_cargas` acrescenta P e Q de cada carga em cada passo. Vem desligado
    porque o custo não é a memória — são 9 MB para doze mil cargas — e sim a
    travessia: um milhão de idas à API do OpenDSS que praticamente dobram o
    tempo da resolução detalhada. Sem ele, a curva de uma unidade sai da placa
    vezes a forma da classe, que é o que o cadastro afirma; com ele, sai o que
    o solver de fato serviu, que difere porque as cargas são de impedância
    parcialmente constante.
    """
    import numpy as np

    nos = list(dss.Circuit.AllNodeNames())
    # 'BT_1655578.1' → ('BT_1655578', 1). O bus é o que tem coordenada.
    barras, indice_barra, fase_do_no = [], {}, []
    for no in nos:
        nome, _, fase = no.partition('.')
        if nome not in indice_barra:
            indice_barra[nome] = len(barras)
            barras.append(nome)
        fase_do_no.append((indice_barra[nome], int(fase or 0)))

    # Tensão de base de cada barra, em kV de linha. É o que separa média de
    # baixa — e as duas têm faixas de qualidade diferentes no PRODIST, de modo
    # que sem isto o mapa julgaria a rede inteira pela régua errada.
    #
    # `Bus.kVBase` é sempre fase-neutro; o nominal que a norma usa é o de
    # linha, daí o √3. Um barramento de 380/220 V dá 0,38 kV e um de 13,8 kV
    # dá 13,8 — e o corte entre as tabelas da norma é em 2,3 kV.
    kv_base = []
    for nome in barras:
        try:
            dss.Circuit.SetActiveBus(nome)
            kv_base.append(float(dss.Bus.kVBase() or 0.0) * math.sqrt(3.0))
        except Exception:      # pragma: no cover - barra sem base declarada
            kv_base.append(0.0)

    linhas, norm_amps, nomes_linha, km_linha = [], [], [], []
    k = dss.Lines.First()
    while k:
        linhas.append((dss.Lines.Bus1().split('.')[0],
                       dss.Lines.Bus2().split('.')[0]))
        norm_amps.append(float(dss.Lines.NormAmps() or 0.0))
        nomes_linha.append(dss.Lines.Name())
        # O comprimento POR LINHA, e não só o total: é o que permite somar km
        # por camada — o tronco de média separado dos ramais de baixa. Vem em
        # km porque é assim que o caso o declara (`units=km` em toda linha).
        km_linha.append(float(dss.Lines.Length() or 0.0))
        k = dss.Lines.Next()

    # As curvas de carga do caso, uma vez. São 66 no alimentador de exemplo, e
    # 96 pontos cada em float32 dá 25 KB — barato o bastante para vir sempre, e
    # é o que permite desenhar a curva de uma unidade sem medir nada.
    curvas = {}
    k = dss.LoadShape.First()
    while k:
        try:
            curvas[dss.LoadShape.Name().lower()] = [
                float(x) for x in dss.LoadShape.PMult()]
        except Exception:      # pragma: no cover - curva sem multiplicadores
            pass
        k = dss.LoadShape.Next()

    # O tape de cada regulador, passo a passo. Sem isto só se sabe onde ele
    # parou no fim do dia — e o que interessa é justamente vê-lo comutar: o
    # regulador é o único elemento do caso cujo estado muda ao longo da
    # simulação por decisão própria. São poucos controles, e 96 passos de meia
    # dúzia de inteiros não pesam.
    nomes_regcontrol = _nomes_regcontrol(dss)
    tap_reg = (np.zeros((passos, len(nomes_regcontrol)), dtype=np.float32)
               if nomes_regcontrol else None)

    nomes_carga = list(dss.Loads.AllNames()) if medir_cargas else []
    pq = (np.zeros((passos, len(nomes_carga), 2), dtype=np.float32)
          if medir_cargas else None)

    v = np.zeros((passos, len(nos)), dtype=np.float32)
    amp = np.zeros((passos, len(linhas)), dtype=np.float32)

    def registrar(k):
        """Guarda o estado do circuito no passo `k`: tensões e tapes.

        O tape é colhido AQUI, e não no fim: `RegControls` reporta a posição
        atual, e no fim do dia todos estariam na do último passo. A série do
        tape ao longo do dia é o que mostra se o regulador está regulando ou
        correndo atrás da rede.
        """
        v[k] = dss.Circuit.AllBusMagPu()
        if tap_reg is not None:
            for j, nome_rc in enumerate(nomes_regcontrol):
                try:
                    dss.RegControls.Name(nome_rc)
                    tap_reg[k, j] = _talvez(dss.RegControls.TapNumber)
                except Exception:                # pragma: no cover - defensivo
                    pass
        j = 0
        i = dss.Lines.First()
        while i:
            # CurrentsMagAng traz [|I|, ang] por fase e terminal; o maior módulo
            # entre as fases do primeiro terminal é o que carrega o condutor.
            c = dss.CktElement.CurrentsMagAng()
            n = max(1, dss.CktElement.NumPhases())
            amp[k, j] = max(c[0:2 * n:2]) if len(c) >= 2 * n else 0.0
            j += 1
            i = dss.Lines.Next()

        if pq is None:
            return
        j = 0
        i = dss.Loads.First()
        while i:
            # Powers() vem [P, Q] por condutor e por terminal, em kW/kvar, com
            # sinal positivo entrando no elemento — para carga, consumo. São os
            # condutores do primeiro terminal, e não as fases: as cargas BT
            # deste gerador têm neutro, e somar só as fases perderia parcela.
            p = dss.CktElement.Powers()
            n = max(1, dss.CktElement.NumConductors())
            if len(p) >= 2 * n:
                pq[k, j, 0] = sum(p[0:2 * n:2])
                pq[k, j, 1] = sum(p[1:2 * n:2])
            j += 1
            i = dss.Loads.Next()

    def finalizar():
        """Empacota tudo o que foi colhido no formato que o mapa consome.

        Um dicionário só, com as séries já em array: é o contrato entre quem
        resolve e quem desenha, e mantê-lo num lugar só é o que permite mudar o
        solver sem mexer na interface.
        """
        return {
            'barras': barras,
            'nos': nos,
            'no_para_barra': [b for b, _ in fase_do_no],
            'fase_do_no': [f for _, f in fase_do_no],
            'v_no_pu': v,
            # Quem tem caminho até a fonte. É o que separa fase viva de fase
            # solta no mapa — a solta tem tensão, por acoplamento, e nenhum
            # limiar a separa com segurança.
            'no_conectado': (np.asarray(conectados, dtype=bool)
                             if conectados is not None else None),
            'linhas': linhas,
            'amp_linha': amp,
            'norm_amps': norm_amps,
            'nomes_linha': nomes_linha,
            'km_linha': km_linha,
            'kv_base_barra': kv_base,
            'coordenadas': _ler_coordenadas(pasta),
            'inventario': _inventario(dss, _ler_cadastro(pasta)),
            'curvas': curvas,
            'nomes_regcontrol': nomes_regcontrol,
            'tap_regulador': tap_reg,
            'nomes_carga': nomes_carga,
            'pq_carga': pq,
        }

    return registrar, finalizar


#: Classes de consumo que o gerador escreve no nome da carga.
CLASSES = ('RES', 'COM', 'IND', 'RUR', 'PP', 'SP', 'CPR')


def _classe_da_carga(nome):
    """Classe de consumo a partir do nome da carga do OpenDSS.

    O gerador a codifica no nome — `ucbt_com_13610584_0` — porque a BDGD a traz
    por unidade e o OpenDSS não tem campo para ela. Nem todo nome segue esse
    formato: as cargas de média são `ucmt_{ponto}_{n}`, sem classe, e a
    iluminação pública é `pip_{trafo}`. Ler o segundo pedaço às cegas
    devolveria o número do ponto como se fosse uma classe.
    """
    partes = nome.lower().split('_')
    if len(partes) > 2 and partes[1].upper() in CLASSES:
        return partes[1].upper()
    if partes[0] == 'pip':
        return 'IP'          # iluminação pública
    if partes[0] == 'ucmt':
        return 'MT'
    return '—'


def _inventario(dss, cadastro=None):
    """O que existe no circuito, por elemento e por barra.

    Colhido do OpenDSS já compilado, e não lido dos `.dss` como texto: o parser
    seria mais uma coisa a manter em dia com o gerador, e erraria em silêncio
    no dia em que o formato mudasse.

    Serve a dois usos — o resumo do alimentador, e a resposta a "o que tem
    neste ponto?" quando alguém clica no mapa.

    `cadastro` é o que `_ler_cadastro` achou nos `Cadastro_*.csv` da pasta. Ele
    entra num sub-dicionário `'cadastro'`, e não misturado aos campos de cima,
    por duas razões: o que vem do circuito e o que vem do cadastro têm
    procedências diferentes e não devem se confundir na leitura; e quem já lê
    `carga['kw']` continua lendo a mesma coisa.
    """
    cadastro = cadastro or {}

    def _cad(nome):
        """A ficha de cadastro daquele elemento, ou um dicionário vazio.

        Busca por nome em minúsculas porque o OpenDSS não distingue maiúsculas
        nos nomes de elemento, e o `.csv` guarda como foi escrito.
        """
        return cadastro.get(str(nome).strip().lower(), {})
    def _bus(prefixo, nome):
        """As barras a que aquele elemento está ligado, sem os nós."""
        dss.Circuit.SetActiveElement('%s.%s' % (prefixo, nome))
        nomes = dss.CktElement.BusNames()
        return [b.split('.')[0] for b in nomes]

    cargas = []
    i = dss.Loads.First()
    while i:
        nome = dss.Loads.Name()
        cad = _cad(nome)
        cargas.append({
            # O cadastro sabe a classe de primeira mão; o nome só a carrega
            # porque o OpenDSS não tem campo para ela. Onde houver cadastro,
            # ele manda — a engenharia reversa do nome fica de recuo, para as
            # pastas geradas antes de o cadastro existir.
            'classe': cad.get('classe') or _classe_da_carga(nome),
            'nome': nome, 'bus': _bus('Load', nome)[0],
            'kw': dss.Loads.kW(), 'kvar': dss.Loads.kvar(),
            'fases': dss.Loads.Phases(), 'curva': dss.Loads.Daily(),
            'kv': dss.Loads.kV(),
            'cadastro': cad,
        })
        i = dss.Loads.Next()

    # ── Reguladores de tensão ───────────────────────────────────────────────
    #
    # No OpenDSS um regulador é um `Transformer` com um `RegControl` apontado
    # para ele. Sem separar os dois, o inventário conta um regulador trifásico
    # como TRÊS transformadores de distribuição — e o mapa o desenha como tal,
    # com "carregamento nominal 0 %" e um circuito de baixa vazio, porque
    # regulador não atende ninguém.
    #
    # Quem sabe a diferença é o próprio circuito, e não o nome do elemento:
    # perguntar ao `RegControl` mantém isto válido para um caso gerado por
    # outra ferramenta, que não usaria o prefixo `REG_`.
    controlados = {}
    for nome_rc in _nomes_regcontrol(dss):
        try:
            dss.RegControls.Name(nome_rc)
        except Exception:                        # pragma: no cover - defensivo
            continue
        alvo = str(dss.RegControls.Transformer() or '').strip().lower()
        if alvo:
            controlados[alvo] = {
                'controle': nome_rc,
                'indice_controle': len(controlados),
                # Falso quando o recuo de `rodar_dia` o desligou: o mapa
                # continua a desenhá-lo, com o tape em zero e a marca.
                'ativo': _regcontrol_ativo(dss, nome_rc),
                'vreg': _talvez(dss.RegControls.ForwardVreg),
                'banda': _talvez(dss.RegControls.ForwardBand),
                'vreg_reverso': _talvez(dss.RegControls.ReverseVreg),
                'banda_reversa': _talvez(dss.RegControls.ReverseBand),
                'tap': _talvez(dss.RegControls.TapNumber),
                'ptratio': _talvez(dss.RegControls.PTRatio),
                'ctprim': _talvez(dss.RegControls.CTPrimary),
                'max_passos': _talvez(dss.RegControls.MaxTapChange),
                'atraso_s': _talvez(dss.RegControls.Delay),
                'barra_monitorada': _talvez(dss.RegControls.MonitoredBus, ''),
            }
            controlados[alvo].update(_modo_do_regulador(dss, nome_rc))

    trafos, enrolamentos_reg = [], []
    i = dss.Transformers.First()
    while i:
        nome = dss.Transformers.Name()
        buses = _bus('Transformer', nome)
        item = {'nome': nome, 'kva': dss.Transformers.kVA(),
                'buses': buses, 'cadastro': _cad(nome)}
        reg = controlados.get(nome.lower())
        if reg is None:
            trafos.append(item)
        else:
            item.update(reg)
            item['tap_atual'] = _talvez(dss.Transformers.Tap)
            # Quanto vale um passo de tape, para traduzir número em razão.
            n_taps = _talvez(dss.Transformers.NumTaps, 32) or 32
            item['tap_min'] = _talvez(dss.Transformers.MinTap, 0.9)
            item['tap_max'] = _talvez(dss.Transformers.MaxTap, 1.1)
            item['tap_passo'] = (item['tap_max'] - item['tap_min']) / float(n_taps)
            enrolamentos_reg.append(item)
        i = dss.Transformers.Next()

    reguladores = _agrupar_reguladores(enrolamentos_reg)

    # ── Capacitores ─────────────────────────────────────────────────────────
    #
    # `States()` importa: o conversor escreve `states=[0]` nos bancos de
    # subestação, que entram no caso desligados. Um banco desligado no mapa não
    # é o mesmo que um banco injetando reativo, e o painel precisa dizer qual é.
    capacitores = []
    i = dss.Capacitors.First()
    while i:
        nome = dss.Capacitors.Name()
        estados = list(_talvez(dss.Capacitors.States, []) or [])
        capacitores.append({
            'nome': nome, 'bus': _bus('Capacitor', nome)[0],
            'kvar': _talvez(dss.Capacitors.kvar, 0.0),
            'kv': _talvez(dss.Capacitors.kV, 0.0),
            'passos': _talvez(dss.Capacitors.NumSteps, 1),
            'ligado': (any(e > 0 for e in estados) if estados else True),
            'cadastro': _cad(nome),
        })
        i = dss.Capacitors.Next()

    # O nome da coleção mudou de caixa entre versões do OpenDSSDirect, e as duas
    # grafias circulam. A escolha é por EXISTÊNCIA e não por verdade: a coleção
    # vazia é falsa, então um `a or b` mandaria todo alimentador sem geração
    # distribuída para o segundo nome — e num OpenDSSDirect que só tem o
    # primeiro, isso é AttributeError na cara de quem abriu o mapa.
    pv = getattr(dss, 'PVsystems', None)
    if pv is None:
        pv = getattr(dss, 'PVSystems', None)
    if pv is None:                      # pragma: no cover - build sem PVSystem
        raise RuntimeError('este OpenDSSDirect não expõe a coleção PVSystem')

    gd = []
    i = pv.First()
    while i:
        nome = pv.Name()
        gd.append({'nome': nome, 'bus': _bus('PVSystem', nome)[0],
                   'kva': pv.kVARated(), 'pmpp': pv.Pmpp(),
                   'cadastro': _cad(nome)})
        i = pv.Next()

    km = 0.0
    i = dss.Lines.First()
    while i:
        km += dss.Lines.Length()
        i = dss.Lines.Next()

    return {
        'cargas': cargas, 'trafos': trafos, 'gd': gd,
        'capacitores': capacitores, 'reguladores': reguladores,
        'km_linhas': km,
        # Contagens: bancos, e não enrolamentos. Um regulador trifásico é UM
        # equipamento no cadastro e no pátio, ainda que sejam três `Transformer`
        # no arquivo.
        'n_capacitores': len(capacitores),
        'n_reguladores': len(reguladores),
        'tensoes_base': list(dss.Settings.VoltageBases()),
    }


def _talvez(funcao, padrao=0.0):
    """Chama a função da API e devolve `padrao` quando ela não existe ou falha.

    A superfície do OpenDSSDirect varia entre versões, e um atributo ausente
    não pode derrubar o inventário inteiro por causa de um campo acessório.
    """
    try:
        return funcao()
    except Exception:      # pragma: no cover - depende da versão do solver
        return padrao


#: Como o modo de operação em fluxo reverso aparece no `RegControl`, e o que
#: cada combinação significa. A ordem importa: `revNeutral` só faz sentido com
#: `reversible=yes`, e `Cogen` tem precedência sobre a regulação reversa comum.
MODOS_REGULADOR = (
    ('cogeracao', 'cogeração',
     'em fluxo reverso continua regulando a jusante, com os setpoints '
     'reversos — o regulador segue influindo na tensão'),
    ('neutro', 'neutro em fluxo reverso',
     'ao detectar fluxo reverso o tape vai para a posição neutra e o '
     'regulador para de atuar, deixando de influir no resultado'),
    ('bidirecional', 'regula nos dois sentidos',
     'tenta regular também no sentido reverso; pode oscilar entre direto e '
     'reverso quando a geração vai e volta'),
    ('direto', 'só no sentido direto',
     'ignora o sentido do fluxo e regula sempre pelos setpoints diretos'),
)


def _modo_do_regulador(dss, nome_rc):
    """Como este `RegControl` se comporta em fluxo reverso.

    Lido do caso compilado, e não da configuração de quem converteu: quem abre
    uma pasta pronta não tem como saber com que opção ela foi gerada, e a
    escolha muda o resultado — um regulador que vai a neutro no reverso deixa
    de influir na tensão, e outro que continua regulando não.
    """
    def _prop(nome, padrao=''):
        """O valor de uma propriedade do elemento ativo, ou o padrão.

        Propriedade que não existe naquela versão do OpenDSS levanta em vez de
        devolver vazio, e o que se quer aqui é ler o que houver sem depender da
        versão.
        """
        try:
            return str(dss.Properties.Value(nome) or '').strip()
        except Exception:      # pragma: no cover - propriedade de outra versão
            return padrao

    try:
        dss.Circuit.SetActiveElement('RegControl.%s' % nome_rc)
    except Exception:          # pragma: no cover
        return {'modo': 'direto', 'modo_rotulo': '', 'modo_nota': ''}

    sim = ('yes', 'true', 'y', '1')
    reversivel = _prop('reversible').lower() in sim
    if _prop('Cogen').lower() in sim:
        chave = 'cogeracao'
    elif reversivel and _prop('revNeutral').lower() in sim:
        chave = 'neutro'
    elif reversivel:
        chave = 'bidirecional'
    else:
        chave = 'direto'

    rotulo, nota = next((r, n) for c, r, n in MODOS_REGULADOR if c == chave)
    limiar = _prop('revThreshold')
    return {'modo': chave, 'modo_rotulo': rotulo, 'modo_nota': nota,
            'limiar_reverso_kw': limiar}


def _agrupar_reguladores(enrolamentos):
    """Junta os enrolamentos de um mesmo regulador num equipamento só.

    Um banco trifásico entra no `.dss` como três `Transformer`, um por fase,
    entre o mesmo par de barras. Contá-los como três equipamentos infla o
    inventário e enche o mapa de marcadores empilhados no mesmo ponto.

    O agrupamento é pelo par de barras, e não pelo nome: o nome depende de quem
    gerou o caso, o par de barras é do circuito.
    """
    from collections import OrderedDict

    bancos = OrderedDict()
    for e in enrolamentos:
        chave = tuple(e['buses'][:2])
        banco = bancos.get(chave)
        if banco is None:
            banco = bancos[chave] = {
                'nome': e['nome'], 'buses': list(e['buses']),
                'kva': 0.0, 'fases': 0, 'enrolamentos': [],
                'controle': e.get('controle'), 'vreg': e.get('vreg'),
                'ativo': e.get('ativo', True),
                'banda': e.get('banda'), 'tap': e.get('tap'),
                'tap_atual': e.get('tap_atual'),
                'barra_monitorada': e.get('barra_monitorada'),
                'modo': e.get('modo'), 'modo_rotulo': e.get('modo_rotulo'),
                'modo_nota': e.get('modo_nota'),
                'limiar_reverso_kw': e.get('limiar_reverso_kw'),
                'vreg_reverso': e.get('vreg_reverso'),
                'banda_reversa': e.get('banda_reversa'),
                'ptratio': e.get('ptratio'), 'ctprim': e.get('ctprim'),
                'max_passos': e.get('max_passos'),
                'atraso_s': e.get('atraso_s'),
                'tap_passo': e.get('tap_passo'),
                'controles': [],
                'cadastro': e.get('cadastro') or {},
            }
        banco['kva'] += e.get('kva') or 0.0
        banco['fases'] += 1
        banco['enrolamentos'].append(e['nome'])
        if e.get('controle'):
            banco['controles'].append(e['controle'])
        if not banco['cadastro']:
            banco['cadastro'] = e.get('cadastro') or {}
    return list(bancos.values())


def _ler_coordenadas(pasta):
    """`BusCoords.csv` → {barra: (lon, lat)}, em minúsculas como o OpenDSS usa."""
    import csv

    caminho = os.path.join(str(pasta), 'BusCoords.csv')
    coords = {}
    if not os.path.isfile(caminho):
        return coords
    with open(caminho, encoding='utf-8') as f:
        leitor = csv.reader(f)
        for linha in leitor:
            if len(linha) < 3:
                continue
            try:
                coords[linha[0].strip().lower()] = (float(linha[1]),
                                                    float(linha[2]))
            except ValueError:
                continue          # o cabeçalho cai aqui, e é o que se quer
    return coords


def _ler_cadastro(pasta):
    """`Cadastro_*.csv` → {elemento: {campo: valor}}, tudo em minúsculas.

    É o irmão de `_ler_coordenadas`, e pela mesma razão: um caso OpenDSS
    descreve um circuito, não um cadastro. `Load.ucbt_com_13610584_2` diz kW,
    kV e a curva; não diz que unidade consumidora é aquela, de que classe, nem
    quanto ela consumiu em cada mês. Isso vem daqui.

    A pasta pode não ter esses arquivos — casos gerados antes de eles
    existirem, ou por outra ferramenta. O painel funciona sem eles, só com
    menos informação, e é por isso que a ausência devolve um dicionário vazio
    em vez de erro.
    """
    import csv
    import glob

    fora = {}
    for caminho in sorted(glob.glob(os.path.join(str(pasta), 'Cadastro_*.csv'))):
        try:
            with open(caminho, encoding='utf-8') as f:
                # O aviso de sigilo do cabeçalho vem em linhas `#`.
                while True:
                    onde = f.tell()
                    linha = f.readline()
                    if not linha:
                        break
                    if not linha.startswith('#'):
                        f.seek(onde)
                        break
                for registro in csv.DictReader(f):
                    chave = (registro.get('elemento') or '').strip().lower()
                    if not chave:
                        continue
                    campos = {k: v for k, v in registro.items()
                              if k and k != 'elemento' and v not in (None, '')}
                    fora.setdefault(chave, {}).update(campos)
        except (OSError, UnicodeDecodeError, csv.Error):
            continue              # arquivo ilegível não pode derrubar o mapa
    return fora


def rodar_dia(pasta, passos=PASSOS_DIA, detalhado=False,
              medir_cargas=False, _sem_reguladores=False):
    """Resolve o dia em passos de 15 minutos e devolve as séries agregadas.

    Cada passo é um `Solve` no modo diário do OpenDSS, que avança o relógio e
    reavalia as curvas de carga. Os controles ficam em `ControlMode=Time`, de
    modo que reguladores e capacitores atuem ao longo do dia como atuariam de
    verdade, e não instantaneamente a cada passo.

    O resultado é um dicionário com séries de `passos` pontos e alguns
    agregados do dia. As chaves estão documentadas em `docs/RESULTADOS.md`; as
    principais:

    ``p_kw``
        potência ativa no ponto de entrega. **Negativa significa exportação** —
        o alimentador devolvendo energia ao sistema.
    ``perdas_kw`` e ``perdas_pct``
        perdas do circuito, em kW e como fração da potência servida.
    ``v_min_pu``, ``v_med_pu``, ``v_max_pu``
        faixa de tensão entre os nós vivos, passo a passo.
    ``convergiu``
        um booleano por passo. Passo que não converge entra no resultado com o
        valor do passo anterior e fica **marcado**: número interpolado que não se
        anuncia é a pior espécie de número.
    ``reguladores_desligados``
          presente só quando o recuo abaixo agiu. Diz quantos foram desligados,
          quantos passos não fechavam com eles e quantos sem, e o que a tensão
          mínima do dia perdeu.

    **O recuo sem reguladores.** Em alguns alimentadores os reguladores de
    tensão não deixam o dia fechar: o `RegControl` lê uma tensão de um passo
    que empacou, move o tape para o lado errado, e dali em diante dezenas de
    passos saem com o balanço de potência violado — a fonte injetando 2 MW que
    não chegam a elemento nenhum, com `Converged=True`. Foram medidas, uma a
    uma, as alavancas que existem: tolerância do solver, quantidade de tapes
    por passo, atraso entre tapes, `ControlMode=Static`, passo de 5 e de 1
    minuto, e a posição inicial dos tapes. **Nenhuma serve a todos**; cada uma
    cura um alimentador e quebra outro.

    O que serve é o que o usuário faria à mão: desligar os reguladores e
    avisar. Com eles desligados, o alimentador que perdia 38 passos passa a
    fechar todos os 96, com o pico na hora certa — e a tensão mínima cai de
    0,837 para 0,792 pu, que é o preço honesto de não ter regulação e vai
    escrito no resultado. A tensão que o regulador segurava não era de
    confiança: vinha dos passos que a guarda recusou.

    O recuo só age quando a guarda recusa mais de `FRACAO_RECUO_REGULADOR` dos
    passos e o caso tem regulador; e só fica com o dia sem reguladores se ele
    recusar MENOS passos que o dia com eles. Regulador que não atrapalha
    continua ligado.
    """
    dss = _compilar(pasta)
    n_reguladores = 0
    try:
        n_reguladores = int(dss.RegControls.Count())
    except Exception:                            # pragma: no cover - defensivo
        pass
    if _sem_reguladores and n_reguladores:
        # Antes do instantaneo, para que o tape fique no neutro: um regulador
        # desligado que herdasse a posicao de plena carga da semente seria um
        # transformador com relacao fixa errada o dia inteiro.
        dss.Text.Command('batchedit regcontrol..* enabled=no')

    # Um instantaneo ANTES de entrar no dia, so para dar ponto de partida.
    #
    # O primeiro passo do modo diario parte de tensao plana. Num alimentador
    # dificil isso e o bastante para o solver cair numa solucao degenerada e
    # DECLARA-LA convergida: medido, um alimentador de 7.740 cargas fechava os
    # 96 passos com 0,0 kW na cabeceira, 0,9 A no tronco e todas as barras em
    # 1,020 pu — dia inteiro de zeros, sem erro, sem aviso, sem passo nao
    # convergido. O balanco denunciava (3.150 kW de carga sem fonte), mas nada
    # no resultado dizia isso.
    #
    # O `Snap` resolve o mesmo circuito com o valor de placa e deixa as tensoes
    # perto do que o dia vai encontrar. Com ele, o mesmo caso passa a fechar em
    # 3.020 kW, com o balanco batendo. Custa uma solucao a mais em 97.
    #
    # Nao se exige que ele convirja: em caso que so fecha com curva, o
    # instantaneo nominal pode nao fechar, e ainda assim as tensoes que ele
    # deixa sao melhor partida que a plana.
    dss.Text.Command('Set Mode=Snap')
    dss.Solution.Solve()

    dss.Text.Command('Set Mode=Daily StepSize=%dm Number=1' % MINUTOS_PASSO)
    dss.Text.Command('Set ControlMode=Time')

    # O Master já os fixa, mas alguns comandos do OpenDSS repõem o padrão
    # (MaxControlIter = 10) ao trocar de modo — e dez é pouco para um
    # alimentador com vários reguladores monofásicos de um tape por vez.
    dss.Text.Command('Set MaxControlIter=500')
    dss.Text.Command('Set MaxIter=300')

    # Os reguladores saem do instantaneo na posicao de PLENA carga, e o dia
    # comeca a meia-noite com um terco dela: com maxtapchange=1 eles levam
    # umas cinco horas descendo um tape por passo. E cosmetico e foi medido —
    # o pico e a energia do dia nao mudam. Duas tentativas de corrigir aqui
    # foram medidas e REJEITADAS: zerar os tapes derruba dois alimentadores na
    # guarda do dia morto, e um passo em ControlMode=Static para assenta-los
    # envenena o estado de tensao num terceiro (152 iteracoes de controle,
    # tapes em zero, 16% de desbalanco em todo passo do dia). A semente nao
    # entrega so tensao; em alimentador dificil a posicao dos tapes e parte do
    # que torna o dia resolvivel, e mexer nela antes do primeiro passo e mexer
    # no que faz o dia fechar.

    # Uma vez por dia: a topologia nao muda ao longo dele (tape nao abre
    # nem fecha nada), e e ela — nao a tensao — que diz que fase esta viva.
    conectados = nos_conectados(dss)

    registrar = finalizar = None
    if detalhado:
        registrar, finalizar = _coletor_detalhado(
            dss, pasta, passos, medir_cargas=medir_cargas,
            conectados=conectados)

    r = {chave: [] for chave in
         ('horas', 'p_kw', 'q_kvar', 's_kva', 'perdas_kw', 'perdas_kvar',
          'perdas_pct', 'v_min_pu', 'v_med_pu', 'v_max_pu', 'convergiu')}

    def _repetir_ultimo():
        """Repete o último valor de cada série, para um passo que não convergiu.

        Não é maquiagem: o passo é contado como não convergido no relatório, e
        essa contagem é o que se olha. Repetir mantém todas as séries do mesmo
        comprimento — sem isso, um passo perdido desalinharia o dia inteiro e o
        gráfico mostraria 19h onde são 18h45.
        """
        for chave, serie in r.items():
            if chave in ('horas', 'convergiu'):
                continue
            serie.append(serie[-1] if serie else 0.0)

    for k in range(passos):
        r['horas'].append(k * MINUTOS_PASSO / 60.0)
        dss.Solution.Solve()
        # As duas coisas: o solver dizer que convergiu E a solução ser um
        # número. A segunda não é redundante — ver `_solucao_finita`.
        # Três perguntas, e a terceira é a que pega o caso deste parágrafo:
        # o solver dizer que convergiu, a solução ser um número, e o balanço
        # de potência fechar. Ver `residuo_balanco` — um passo pode passar nas
        # duas primeiras e ainda assim violar Kirchhoff por 2 MW.
        ok = (bool(dss.Solution.Converged()) and _solucao_finita(dss)
              and _balanco_fecha(dss))
        r['convergiu'].append(ok)
        if not ok:
            _repetir_ultimo()
            continue

        if registrar is not None:
            registrar(k)

        p, q = _potencia(dss)
        perdas_p, perdas_q = _perdas_kw(dss)
        s = math.hypot(p, q)
        v_min, v_med, v_max = _tensoes_pu(dss, conectados)

        r['p_kw'].append(p)
        r['q_kvar'].append(q)
        r['s_kva'].append(s)
        r['perdas_kw'].append(perdas_p)
        r['perdas_kvar'].append(perdas_q)
        # Referência robusta: em passo de exportação líquida, |P| no ponto de
        # entrega pode ser quase zero e a razão explodiria.
        r['perdas_pct'].append(100.0 * perdas_p / max(abs(p), perdas_p, 1.0))
        r['v_min_pu'].append(v_min)
        r['v_med_pu'].append(v_med)
        r['v_max_pu'].append(v_max)

    _conferir_dia_com_carga(r, pasta, dss)
    r.update(_agregados(r, pasta, dss))
    if finalizar is not None:
        r.update(finalizar())

    recusados = len(r['passos_nao_convergidos'])
    if (not _sem_reguladores and n_reguladores
            and recusados > FRACAO_RECUO_REGULADOR * len(r['horas'])):
        sem = rodar_dia(pasta, passos=passos, detalhado=detalhado,
                        medir_cargas=medir_cargas, _sem_reguladores=True)
        recusados_sem = len(sem['passos_nao_convergidos'])
        if recusados_sem < recusados:
            sem['reguladores_desligados'] = {
                'quantos': n_reguladores,
                'passos_recusados_com': recusados,
                'passos_recusados_sem': recusados_sem,
                'v_min_com_pu': r.get('v_min_dia_pu'),
                'v_min_sem_pu': sem.get('v_min_dia_pu'),
                'p_max_com_kw': r.get('p_max_kw'),
                'p_max_sem_kw': sem.get('p_max_kw'),
            }
            _anotar_reguladores_desligados(pasta, sem['reguladores_desligados'],
                                           len(r['horas']))
            return sem
    if not _sem_reguladores:
        # Este dia rodou com os reguladores: uma anotacao de execucao anterior
        # que dissesse o contrario estaria mentindo na aba.
        _anotar_reguladores_desligados(pasta, None, len(r['horas']))
    return r


TITULO_REGULADORES_DESLIGADOS = 'Reguladores de tensão desligados na simulação do dia'


def _anotar_reguladores_desligados(pasta, rd, passos):
    """Poe (ou tira) a anotacao do recuo no `Ajustes.json` do caso.

    O registro de ajustes e escrito na conversao, e o recuo acontece na
    simulacao — horas ou dias depois, por outro comando. Quem abre a aba
    "Ajustes do cadastro" precisa ver ali que a simulacao desligou os
    reguladores, e por que; o aviso no terminal nao chega a quem abriu o caso
    pela janela. Ver `ajustes.anotar_execucao`.
    """
    from bdgdcase import ajustes as _aj

    if not rd:
        _aj.anotar_execucao(pasta, TITULO_REGULADORES_DESLIGADOS, None)
        return
    _aj.anotar_execucao(pasta, TITULO_REGULADORES_DESLIGADOS, {
        'categoria': 'tensao',
        'titulo': TITULO_REGULADORES_DESLIGADOS,
        'quantos': rd['quantos'],
        'unidade': 'reguladores',
        'efeito': 'simulacao',
        'detalhe': 'Com os reguladores habilitados, %d dos %d passos do dia nao '
                   'fechavam o balanco de potencia — a fonte injetava energia '
                   'que nao chegava a elemento nenhum, com o solver declarando '
                   'convergencia. Desligados, %d. A tensao minima do dia foi de '
                   '%.3f para %.3f pu e o pico de %.0f para %.0f kW.'
                   % (rd['passos_recusados_com'], passos, rd['passos_recusados_sem'],
                      rd['v_min_com_pu'] or 0.0, rd['v_min_sem_pu'] or 0.0,
                      rd['p_max_com_kw'] or 0.0, rd['p_max_sem_kw'] or 0.0),
        'porque': 'O RegControl le a tensao de um passo que empacou, move o '
                  'tape para o lado errado e encosta no batente; dali em diante '
                  'o circuito nao tem solucao. Foram medidas todas as alavancas '
                  'do modelo — tolerancia, tapes por passo, atraso, modo de '
                  'controle, sub-passo, posicao inicial — e nenhuma serve a '
                  'todos os alimentadores. O regulador continua no mapa, com o '
                  'tape em zero. O que ele "segurava" vinha de passos que nao '
                  'eram solucao.',
        'exemplos': [],
    })


def _conferir_dia_com_carga(r, pasta, dss):
    """Um dia inteiro de zeros num circuito com carga não é um resultado.

    A guarda por passo — finito e abaixo de 10 pu — não pega este caso: as
    tensões ficam num plano perfeito de 1,02 pu, cada passo se declara
    convergido, e o dia sai com 0,0 kW na cabeceira do começo ao fim. Medido
    num alimentador de 7.740 cargas: 0,9 A no tronco, 3.150 kW de carga sem
    fonte que os alimentasse, e nada no resultado dizendo isso. O CSV saía
    cheio de zeros com cara de dado.

    A causa era a partida a frio do modo diário, hoje corrigida com o
    instantâneo em `rodar_dia`. Esta conferência é a rede de segurança: se
    voltar a acontecer, por outro caminho, o silêncio não volta com ela.
    """
    if not dss.Loads.Count() or not r['p_kw']:
        return
    if max(abs(p) for p in r['p_kw']) > LIMIAR_DIA_MORTO_KW:
        return
    raise RuntimeError(
        'o dia inteiro saiu em zero em %s: %d cargas no circuito e nenhum '
        'passo com potência na cabeceira. A solução se diz convergida, mas '
        'não alimenta nada — suspeite da partida do modo diário ou de o '
        'circuito ter perdido a fonte.' % (pasta, dss.Loads.Count()))


def _agregados(r, pasta, dss):
    """Números do dia inteiro, derivados das séries."""
    horas_passo = MINUTOS_PASSO / 60.0
    energia = sum(p for p in r['p_kw'] if p > 0) * horas_passo
    exportada = -sum(p for p in r['p_kw'] if p < 0) * horas_passo
    perdas = sum(r['perdas_kw']) * horas_passo
    p_max = max(r['p_kw']) if r['p_kw'] else float('nan')
    vivos = [v for v in r['v_min_pu'] if math.isfinite(v)]
    nao_convergidos = [k for k, ok in enumerate(r['convergiu']) if not ok]
    return {
        'alimentador': dss.Circuit.Name().upper(),
        'pasta': str(pasta),
        'passos': len(r['horas']),
        'minutos_por_passo': MINUTOS_PASSO,
        'energia_importada_kwh': energia,
        'energia_exportada_kwh': exportada,
        'perdas_kwh': perdas,
        'perdas_pct_dia': 100.0 * perdas / energia if energia else float('nan'),
        'p_max_kw': p_max,
        'hora_p_max': (r['horas'][r['p_kw'].index(p_max)]
                       if r['p_kw'] else float('nan')),
        'v_min_dia_pu': min(vivos) if vivos else float('nan'),
        'v_max_dia_pu': (max(v for v in r['v_max_pu'] if math.isfinite(v))
                         if r['v_max_pu'] else float('nan')),
        'passos_nao_convergidos': nao_convergidos,
    }


# ── Levar os números para outro lugar ────────────────────────────────────────

SERIES = ('horas', 'p_kw', 'q_kvar', 's_kva', 'perdas_kw', 'perdas_kvar',
          'perdas_pct', 'v_min_pu', 'v_med_pu', 'v_max_pu', 'convergiu')


def para_csv(resultado, caminho):
    """Grava as séries de :func:`rodar_dia` como um CSV de 96 linhas.

    Uma linha por passo, uma coluna por série — o formato que qualquer
    planilha, R ou pandas lê sem cerimônia. Os agregados do dia não entram: são
    um por execução, não uma série, e cabem melhor no nome do arquivo ou num
    log.
    """
    import csv

    caminho = str(caminho)
    os.makedirs(os.path.dirname(os.path.abspath(caminho)), exist_ok=True)
    colunas = [c for c in SERIES if c in resultado]
    with open(caminho, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(colunas)
        for k in range(resultado['passos']):
            w.writerow([resultado[c][k] for c in colunas])
    return caminho
