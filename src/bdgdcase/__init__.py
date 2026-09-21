"""bdgdcase — da BDGD a um caso de simulação OpenDSS.

A Base de Dados Geográfica da Distribuidora é um cadastro de ativos, não um
modelo elétrico: não traz a estrutura de nós, ramos e condições de contorno que
um fluxo de potência exige. Este pacote faz a travessia entre os dois, e
documenta as decisões que ela obriga a tomar.

    from bdgdcase.api import extrair, converter

    extrair('BDGD.gdb', 'Output', ['TRO05'])   # .gdb    -> tabelas
    converter('Output/TRO05', dias=('DU',))    # tabelas -> caso OpenDSS

`bdgdcase.solucao` fecha o ciclo: resolve o caso no OpenDSS e devolve as
séries do dia. É o único módulo daqui que fala com o simulador, e o
`import opendssdirect` mora dentro das funções — importar a biblioteca não
carrega a DLL, e `bdgdcase --help` não paga esse preço.

O que este pacote NÃO faz: rodar Monte Carlo, varrer cenários ou modelar adoção
de tecnologia. Onde há geração, é a que já consta do cadastro.
"""

__version__ = '0.2.0'

__all__ = ['__version__']
