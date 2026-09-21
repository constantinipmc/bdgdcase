# -*- coding: utf-8 -*-
"""Permite `python -m bdgdcase ...`, com os mesmos comandos do executável.

O pacote instala `bdgdcase` no PATH, e é assim que se espera usá-lo. Mas quem
está dentro do repositório, ou num ambiente em que o `Scripts/` não entrou no
PATH, tenta `python -m bdgdcase` — e recebia "cannot be directly executed", que
não ajuda ninguém a descobrir o comando certo.
"""
import sys

from bdgdcase.interface.cli import main

if __name__ == '__main__':
    sys.exit(main())
