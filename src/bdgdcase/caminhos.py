from __future__ import annotations

import json
import os
from pathlib import Path


def _detectar_base_dir() -> Path:
    """A pasta de trabalho: `BDGD_BASE_DIR`, ou o diretório atual.

    A variável de ambiente existe para os testes e para quem roda em lote. Sem
    ela, tudo nasce ao lado de onde o comando foi chamado, que é o que alguém
    espera na primeira vez.
    """
    env = os.environ.get('BDGD_BASE_DIR')
    if env:
        return Path(env).resolve()
    return Path.cwd()


BASE_DIR: Path = _detectar_base_dir()

_CFG_PATH = BASE_DIR / 'bdgdcase.json'
_overrides: dict = {}
if _CFG_PATH.is_file():
    try:
        _overrides = json.loads(_CFG_PATH.read_text(encoding='utf-8'))
    except Exception as exc:  # configuração quebrada não impede o programa de abrir
        print(f'[bdgdcase] Aviso: bdgdcase.json inválido ({exc}); usando padrões.')


def _dir(chave: str, padrao: str) -> Path:
    """Uma das pastas do projeto, resolvida contra a base se for relativa."""
    valor = _overrides.get(chave)
    p = Path(valor) if valor else Path(padrao)
    return p if p.is_absolute() else BASE_DIR / p


INPUT_DIR: Path = _dir('input_dir', 'input')
OUTPUT_DIR: Path = _dir('output_dir', 'Output')
OPENDSS_DIR: Path = _dir('opendss_dir', 'OpenDSS')
LOGS_DIR: Path = _dir('logs_dir', 'logs')

#: Os dados que viajam DENTRO do pacote — catálogos de referência, exemplo.
#: Ancorado no diretório do pacote, e não no `__file__` de quem pergunta: um
#: caminho derivado do arquivo que lê acompanha esse arquivo quando ele muda de
#: pasta, e passa a apontar para uma pasta que não existe. Este módulo mora na
#: raiz do pacote e fica lá.
DADOS_DIR: Path = Path(__file__).resolve().parent / 'dados'


def garantir_dirs() -> None:
    """Cria os diretórios em que o pacote sempre escreve."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def listar_alimentadores() -> list[str]:
    """Alimentadores já extraídos: subpastas de Output/ que não começam com '_'."""
    if not OUTPUT_DIR.is_dir():
        raise FileNotFoundError(f'Diretório não encontrado: {OUTPUT_DIR}')
    return sorted(d.name for d in OUTPUT_DIR.iterdir()
                  if d.is_dir() and not d.name.startswith('_'))


def resumo() -> str:
    """Caminhos resolvidos e o estado de cada um, para o `bdgdcase caminhos`."""
    def _st(p: Path) -> str:
        """'OK' ou 'NAO ENCONTRADO', para o resumo dos caminhos."""
        return 'OK' if p.exists() else 'NAO ENCONTRADO'

    origem = ('env BDGD_BASE_DIR' if os.environ.get('BDGD_BASE_DIR')
              else 'diretório de trabalho')
    presente = 'presente' if _CFG_PATH.is_file() else 'ausente — usando padrões'
    return '\n'.join([
        f'Base     : {BASE_DIR}',
        f'  origem : {origem}',
        f'Config   : {_CFG_PATH} [{presente}]',
        f'input/   : {INPUT_DIR}  [{_st(INPUT_DIR)}]',
        f'Output/  : {OUTPUT_DIR}  [{_st(OUTPUT_DIR)}]',
        f'OpenDSS/ : {OPENDSS_DIR}  [{_st(OPENDSS_DIR)}]',
    ])
