"""Interface de linha de comando — a forma primária de usar o pacote.

    bdgdcase listar    --gdb BDGD.gdb
    bdgdcase panorama  --gdb BDGD.gdb   # onde fica cada alimentador
    bdgdcase extrair   --gdb BDGD.gdb --saida Output --alim TRO05
    bdgdcase converter Output/TRO05 --dias DU SA
    bdgdcase verificar OpenDSS/TRO05_DU
    bdgdcase rodar     OpenDSS/TRO05_DU --csv dia.csv
    bdgdcase exemplo           # confere a instalacao, sem baixar nada
    bdgdcase caminhos
    bdgdcase gui               # a cadeia inteira numa janela
    bdgdcase mapa OpenDSS/TRO05_DU   # o alimentador no mapa, hora a hora
"""
from __future__ import annotations

import argparse
import sys

from bdgdcase import __version__
from bdgdcase.api import DIAS, converter, extrair, listar
from bdgdcase.solucao import PU_PLAUSIVEL_MAX, PU_PLAUSIVEL_MIN


def _cmd_listar(args):
    """Mostra os alimentadores (ou os municípios) de uma base.


    É o primeiro comando de quem baixou um `.gdb`: sem ele não há como
    saber que códigos existem, e o código do alimentador é o argumento de
    tudo o mais.
    """
    alimentadores, municipios = listar(args.gdb)
    if args.municipio:
        print('%d municipio(s):' % len(municipios))
        for m in municipios:
            print('  %s' % m)
        return 0
    print('%d alimentador(es) em %s:' % (len(alimentadores), args.gdb))
    # Em coluna: uma base de distribuidora traz centenas, e uma lista vertical
    # de 500 linhas nao ajuda ninguem a encontrar o codigo que procura.
    largura = max((len(a) for a in alimentadores), default=0) + 2
    por_linha = max(1, 78 // largura)
    for i in range(0, len(alimentadores), por_linha):
        print('  ' + ''.join(a.ljust(largura)
                             for a in alimentadores[i:i + por_linha]).rstrip())
    if municipios:
        print()
        print('%d municipios; use --municipio para ve-los.' % len(municipios))
    return 0


def _cmd_panorama(args):
    """Le a geometria da base e monta o mapa de todos os alimentadores.

    A janela faz isto sozinha depois de varrer. Aqui na linha de comando ele
    serve para duas coisas: pagar o custo antes, deixando o cache quente para
    quem for abrir a janela depois, e medir esse custo sem depender de ter
    Tkinter instalado.
    """
    from bdgdcase.api import panorama

    def _andamento(fracao, texto):
        """Uma linha por etapa: a leitura leva minutos e nao pode parecer parada."""
        print('  [%3.0f%%] %s' % (100.0 * fracao, texto))

    pan = panorama(args.gdb, progresso=_andamento)
    print()
    print('%d alimentador(es), %d traco(s) continuo(s), %d vertice(s).'
          % (len(pan.alimentadores), len(pan), pan.n_vertices))
    km = sum(f.get('km') or 0.0 for f in pan.info.values())
    if km:
        print('%.0f km de rede de media tensao na base.' % km)
    caixa = pan.caixa()
    if caixa:
        print('extensao   %.4f, %.4f  a  %.4f, %.4f  (lon, lat)' % caixa)
    print()
    print('Os vaos de poste a poste sao costurados num traco continuo por ramo')
    print('e simplificado: o traco fica inteiro, sem a posicao exata de cada')
    print('poste. Serve para localizar e escolher, nao para medir.')
    print('Ver `bdgdcase gui`, aba "Alimentadores no mapa".')
    return 0


def _cmd_extrair(args):
    """Recorta um ou mais alimentadores da base para `Output/`.


    Devolve 1 se algum falhou, e não na primeira falha: num lote de vinte
    alimentadores, parar no terceiro desperdiça os dezessete que teriam
    saído.
    """
    resultados = extrair(
        args.gdb, args.saida, args.alim,
        municipio=args.municipio,
        limpar_colunas=not args.manter_colunas,
        threads=args.threads,
    )
    for alim, ok, err in resultados:
        print('  %-10s %s' % (alim, 'ok' if ok else 'FALHOU: %s' % err))
    return 1 if any(not ok for _, ok, _ in resultados) else 0


def _cmd_converter(args):
    """Gera o caso OpenDSS a partir das tabelas já extraídas.


    Separado de `extrair` porque converter é barato e reextrair é caro:
    mudar uma premissa e refazer o caso leva segundos, e não precisa tocar
    na geodatabase.
    """
    erros = converter(args.pastas, dias=args.dias,
                      regulador_reverso=args.regulador_reverso)
    for msg in erros:
        print('  FALHOU %s' % msg)
    return 1 if erros else 0


def _cmd_verificar(args):
    """Resolve um passo de cada caso, só para saber se ele fecha.


    Barato e cedo. Caso que não resolve um passo não vai resolver noventa e
    seis, e descobrir isso agora poupa os minutos do dia inteiro.
    """
    from bdgdcase.solucao import verificar
    falhas = 0
    for pasta in args.pastas:
        try:
            r = verificar(pasta, pu_min=args.pu_min, pu_max=args.pu_max)
        except (RuntimeError, FileNotFoundError) as exc:
            print('  %s\n    FALHOU: %s' % (pasta, exc))
            falhas += 1
            continue
        print('  %s' % pasta)
        print('    convergiu   %d barras, %d cargas, %d linhas, %d trafos'
              % (r['barras'], r['cargas'], r['linhas'], r['transformadores']))
        print('    tensao      %.4f a %.4f pu (media %.4f)'
              % (r['v_min_pu'], r['v_max_pu'], r['v_med_pu']))
        # Regime nominal, sem curva de carga: serve para dizer que o circuito
        # fecha, nao para descrever a operacao. O dia esta em `bdgdcase rodar`.
        print('    nominal     P=%.1f kW  Q=%.1f kVAr  perdas=%.2f kW '
              '(instantaneo, sem curva)'
              % (r['p_kw'], r['q_kvar'], r['perdas_kw']))
    return 1 if falhas else 0


def _cmd_rodar(args):
    """Roda o dia inteiro — 96 passos — e, se pedido, salva o CSV."""
    from bdgdcase.solucao import para_csv, rodar_dia
    falhas = 0
    for pasta in args.pastas:
        try:
            r = rodar_dia(pasta, medir_cargas=args.medir_cargas)
        except (RuntimeError, FileNotFoundError) as exc:
            print('  %s\n    FALHOU: %s' % (pasta, exc))
            falhas += 1
            continue
        print('  %s  (%d passos de %d min)'
              % (r['alimentador'], r['passos'], r['minutos_por_passo']))
        print('    pico        %.1f kW as %.2f h'
              % (r['p_max_kw'], r['hora_p_max']))
        print('    energia     %.1f kWh importados, %.1f exportados'
              % (r['energia_importada_kwh'], r['energia_exportada_kwh']))
        print('    perdas      %.1f kWh (%.2f%% da energia importada)'
              % (r['perdas_kwh'], r['perdas_pct_dia']))
        print('    tensao      %.4f a %.4f pu no dia'
              % (r['v_min_dia_pu'], r['v_max_dia_pu']))
        rd = r.get('reguladores_desligados')
        if rd:
            print('    ATENCAO     %d regulador(es) de tensao DESLIGADOS neste dia. '
                  'Com eles habilitados, %d dos %d passos nao fechavam o balanco '
                  'de potencia; desligados, %d. A tensao minima do dia foi de '
                  '%.3f para %.3f pu e o pico de %.0f para %.0f kW — o que o '
                  'regulador segurava vinha de passos que nao eram solucao.'
                  % (rd['quantos'], rd['passos_recusados_com'], r['passos'],
                     rd['passos_recusados_sem'], rd['v_min_com_pu'] or 0.0,
                     rd['v_min_sem_pu'] or 0.0, rd['p_max_com_kw'] or 0.0,
                     rd['p_max_sem_kw'] or 0.0))
        if r['passos_nao_convergidos']:
            print('    ATENCAO     %d passo(s) nao convergiram e foram repetidos '
                  'do anterior: %s' % (len(r['passos_nao_convergidos']),
                                       r['passos_nao_convergidos']))
        if args.csv:
            destino = (args.csv if len(args.pastas) == 1
                       else '%s_%s.csv' % (args.csv.rsplit('.csv', 1)[0],
                                           r['alimentador']))
            print('    csv         %s' % para_csv(r, destino))
    return 1 if falhas else 0


def _cmd_exemplo(args):
    """Confere a instalacao inteira sem exigir dado nenhum do usuario."""
    from bdgdcase.solucao import caminho_exemplo
    caso = caminho_exemplo()
    print('Caso de exemplo: %s' % caso)
    print('  (alimentador TRO05 da BDGD Celesc, ja convertido)')
    print()
    # `exemplo` reaproveita `verificar` e `rodar`, e por isso precisa fornecer
    # o que os dois leem de `args`. Quem acrescentar uma opção a um deles tem de
    # acrescentar aqui também — e `tests/test_cli.py` cobra isso, porque a falta
    # só aparece na execução, com um AttributeError no meio da saída.
    args.pastas = [caso]
    args.pu_min, args.pu_max = PU_PLAUSIVEL_MIN, PU_PLAUSIVEL_MAX
    if _cmd_verificar(args):
        return 1
    print()
    args.csv, args.medir_cargas = None, False
    return _cmd_rodar(args)


def _cmd_mapa(args):
    """A mesma janela de `gui`, começando na aba do mapa."""
    from bdgdcase.interface.janela import abrir_janela
    abrir_janela(args.pasta, medir_cargas=args.medir_cargas, aba='mapa')
    return 0


def _cmd_caminhos(args):
    """Diz onde o pacote está procurando cada coisa, e se achou.


    Existe porque quase todo problema de primeira execução é de caminho, e
    adivinhar de onde o `Output/` está sendo lido custa mais que perguntar.
    """
    from bdgdcase.caminhos import resumo
    print(resumo())
    return 0


def _cmd_distribuidoras(args):
    """O que se sabe de cada base, e o que a conversão confere nela."""
    from bdgdcase.distribuidoras import PERFIS, perfil_por_dist

    if args.dist:
        perfil = perfil_por_dist(args.dist)
        if not perfil.dist:
            print('DIST %s não está registrado.' % args.dist)
            print('Registrados: %s' % ', '.join(sorted(PERFIS)))
            print()
            print('Uma base não registrada converte igual — as regras são '
                  'sobre o dado, não sobre quem o publicou. O que falta é a '
                  'conferência, porque não há expectativa com que comparar.')
            return 1
        modulo = __import__('bdgdcase.distribuidoras', fromlist=['x'])
        print('%s  (DIST=%s)' % (perfil.nome, perfil.dist))
        print('=' * 70)
        print(perfil.resumo)
        print()
        print('  tensoes de baixa esperadas : %s kV'
              % ', '.join('%g' % v for v in perfil.tensoes_bt_kv))
        print('  TIP_TRAFO usados           : %s'
              % ', '.join(perfil.tipos_de_trafo))
        print('  traz curvas de carga       : %s'
              % ('sim' if perfil.tem_curvas_de_carga else 'NAO'))
        print('  TIP_CC preenchido          : %s'
              % ('sim' if perfil.tem_tipologia_de_carga else 'NAO'))
        print('  capacitor com prefixo      : %s'
              % ('sim' if perfil.capacitor_tem_prefixo else 'nao'))
        print('  RAMLIG: o poste esta em    : PAC_%d'
              % (1 if perfil.ramal_pac1_e_poste else 2))
        print('  conferencias proprias      : %d' % len(perfil.conferencias))
        print()
        for nome in ('celesc', 'coopera', 'copel', 'rge'):
            mod = getattr(modulo, nome)
            if mod.PERFIL.dist == perfil.dist:
                print('O dossie completo, com os numeros medidos e as decisoes:')
                print('  src/bdgdcase/distribuidoras/%s.py' % nome)
        return 0

    print('Distribuidoras registradas')
    print('=' * 70)
    for dist in sorted(PERFIS, key=lambda d: PERFIS[d].nome):
        p = PERFIS[dist]
        print('  %-8s %-26s %s' % (dist, p.nome, p.resumo.split('.')[0] + '.'))
    print()
    print('`bdgdcase distribuidoras --dist <DIST>` para o dossie de uma delas.')
    print()
    print('A conversao NAO ramifica por distribuidora: as regras sao sobre o')
    print('dado. Estes perfis dizem o que se espera de cada base e conferem o')
    print('alimentador contra isso. Ver src/bdgdcase/distribuidoras/base.py.')
    return 0


def _cmd_gui(args):
    """A mesma janela de `mapa`, começando na aba de preparar.

    Os dois comandos existem porque são dois pontos de partida: quem tem um
    `.gdb` e quer um caso entra por `gui`; quem já tem o caso e quer olhar para
    ele entra por `mapa`. É a única diferença entre eles.
    """
    from bdgdcase.interface.preparar import abrir
    abrir(args.janela)
    return 0


def construir_parser():
    """Monta o parser de todos os subcomandos.


    Função à parte, e não montada dentro de `main`, porque é o que permite
    um teste percorrer os subcomandos e exigir que cada um esteja ligado a
    uma função — subparser sem `set_defaults(func=…)` só explode quando
    alguém o usa.
    """
    ap = argparse.ArgumentParser(
        prog='bdgdcase',
        description='Da BDGD a um caso de simulação OpenDSS.')
    ap.add_argument('--version', action='version',
                    version='bdgdcase %s' % __version__)
    sub = ap.add_subparsers(dest='comando', required=True)

    p = sub.add_parser('listar',
                       help='quais alimentadores existem no .gdb da BDGD')
    p.add_argument('--gdb', required=True, help='pasta .gdb da BDGD')
    p.add_argument('--municipio', action='store_true',
                   help='lista os municípios em vez dos alimentadores')
    p.set_defaults(func=_cmd_listar)

    p = sub.add_parser('panorama',
                       help='onde fica cada alimentador do .gdb; aquece o '
                            'cache do mapa da janela')
    p.add_argument('--gdb', required=True, help='pasta .gdb da BDGD')
    p.set_defaults(func=_cmd_panorama)

    p = sub.add_parser('extrair', help='.gdb da BDGD -> tabelas por alimentador')
    p.add_argument('--gdb', required=True, help='pasta .gdb da BDGD')
    p.add_argument('--saida', required=True, help='diretório de saída')
    p.add_argument('--alim', required=True, nargs='+',
                   help='código do alimentador (um ou mais)')
    p.add_argument('--municipio', default=None,
                   help='restringe ao município, pelo código IBGE de 7 dígitos '
                        '(veja `bdgdcase listar --municipio`)')
    p.add_argument('--threads', type=int, default=4)
    p.add_argument('--manter-colunas', action='store_true',
                   help='não descarta as colunas que a conversão não usa')
    p.set_defaults(func=_cmd_extrair)

    p = sub.add_parser('converter', help='tabelas -> caso OpenDSS')
    p.add_argument('pastas', nargs='+', help='pastas Output/{ALIMENTADOR}')
    p.add_argument('--dias', nargs='+', default=['DU'], choices=list(DIAS),
                   help='tipos de dia a gerar (padrão: DU)')
    p.add_argument('--regulador-reverso', default='neutro',
                   choices=['neutro', 'cogeracao', 'bidirecional', 'direto'],
                   help='o que o regulador de tensao faz quando o fluxo '
                        'inverte: neutro (padrao) manda o tape a posicao '
                        'neutra e ele para de atuar; cogeracao mantem a '
                        'regulacao a jusante com setpoints reversos; '
                        'bidirecional regula nos dois sentidos, podendo '
                        'oscilar; direto ignora o sentido do fluxo')
    p.set_defaults(func=_cmd_converter)

    # Os dois abaixo resolvem o caso no OpenDSS; o resto do pacote não toca
    # no motor, e um erro de conversão não chega disfarçado de erro de
    # convergência.
    p = sub.add_parser('verificar',
                       help='o caso compila, resolve e converge?')
    p.add_argument('pastas', nargs='+', help='pastas OpenDSS/{ALIMENTADOR}_{DIA}')
    p.add_argument('--pu-min', type=float, default=PU_PLAUSIVEL_MIN,
                   help='piso da faixa de tensão plausível (padrão: %s)'
                        % PU_PLAUSIVEL_MIN)
    p.add_argument('--pu-max', type=float, default=PU_PLAUSIVEL_MAX,
                   help='teto da faixa de tensão plausível (padrão: %s)'
                        % PU_PLAUSIVEL_MAX)
    p.set_defaults(func=_cmd_verificar)

    p = sub.add_parser('rodar',
                       help='resolve o dia em 96 passos de 15 min')
    p.add_argument('pastas', nargs='+', help='pastas OpenDSS/{ALIMENTADOR}_{DIA}')
    p.add_argument('--medir-cargas', action='store_true',
                   help='colhe P e Q de cada carga em cada passo. Dobra o '
                        'tempo; sem isto a curva de uma unidade e a nominal '
                        '(placa x forma da classe), que nao e a servida')
    p.add_argument('--csv', default=None,
                   help='grava as séries num CSV de 96 linhas')
    p.set_defaults(func=_cmd_rodar)

    p = sub.add_parser('mapa',
                       help='mapa georreferenciado, percorrível ao longo do dia '
                            '(exige Tkinter)')
    p.add_argument('--medir-cargas', action='store_true',
                   help='mede a potencia de cada unidade ao longo do dia, em '
                        'vez de usar a curva nominal da classe')
    p.add_argument('pasta', nargs='?', default=None,
                   help='pasta OpenDSS/{ALIMENTADOR}_{DIA}; sem ela, a janela '
                        'abre vazia e você escolhe')
    p.set_defaults(func=_cmd_mapa)

    p = sub.add_parser('exemplo',
                       help='roda o caso que vem no pacote — confere a '
                            'instalação sem baixar nada')
    p.set_defaults(func=_cmd_exemplo)

    p = sub.add_parser('caminhos', help='mostra os diretórios resolvidos')
    p.set_defaults(func=_cmd_caminhos)

    p = sub.add_parser('distribuidoras',
                       help='o que se sabe de cada base da BDGD já vista')
    p.add_argument('--dist', help='código DIST de uma delas (ex.: 396)')
    p.set_defaults(func=_cmd_distribuidoras)

    p = sub.add_parser('gui', help='abre a janela que percorre a cadeia inteira')
    p.add_argument('janela', nargs='?', default='completa',
                   choices=('completa',),
                   help='a janela que vai da BDGD ao caso simulado. O argumento '
                        'sobrevive por compatibilidade: há uma janela só')
    p.set_defaults(func=_cmd_gui)
    return ap


def main(argv=None):
    """Ponto de entrada do executável `bdgdcase`."""
    args = construir_parser().parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
