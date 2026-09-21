"""Tipo de fonte da GD e fator de capacidade.

Fonte única da classificação de GD, e é para continuar sendo: quem decide
PVSystem × Generator em `modelo/geracao.py` e quem monta a curva de geração
consultam este módulo, e não cada um o seu critério. Dois lugares decidindo o
que é solar acabam discordando, e a discordância aparece como energia que não
fecha.

**O problema que este módulo resolve.** A BDGD **não** traz o tipo da fonte:
  - `TIP_GER` não existe nas tabelas UGBT/UGMT (é campo fantasma);
  - `TIP_SIST` vale 'RD_INTERLIG' em 100% dos registros (só diz que é rede interligada);
  - `CEG_GD` só revela o tipo quando o empreendimento tem código próprio da ANEEL
    (`CGH.`, `PCH.`, `UHE.`, `EOL.`, `UTE.`, `UFV.`).

O prefixo `GD.` significa "micro/mini geração distribuída" (REN 482 / Lei
14.300) — **não** significa solar. Uma CGH enquadrada como mini-GD recebe
código `GD.SC.…` e, sem mais nenhum sinal, seria modelada como fotovoltaica.

**O sinal que sobra: o fator de capacidade.** A BDGD fornece a energia injetada mês a mês (`ENE_01`…`ENE_12`) e a potência
instalada (`POT_INST`). O fator de capacidade anual

    FC = Σ ENE_mm  /  (POT_INST × 8760)

separa as fontes de forma inequívoca. Medido na BDGD Celesc 2024 (mediana):

    UFV / GD (solar) ....  13,6% / 8,2%      ← teto físico do FV em SC ≈ 20%
    EOL (eólica) ........  16,3%
    CGH / PCH / UHE .....  46,1% / 46,8% / 43,6%
    UTE (térmica) .......  61,9%

Adotamos **FC > 25% ⇒ não é solar** (limiar conservador: nenhum FV em Santa
Catarina alcança isso; hidro/térmica/biogás começam bem acima). Unidades com
prefixo `GD.` acima do limiar são tratadas como geração de base (`NSOL`).

Ressalva honesta: FC alto também pode indicar erro de cadastro (POT_INST
subdimensionada ou ENE_* trocada). Em ambos os casos o registro é impróprio
para representar um telhado fotovoltaico — e é modelado como geração de
base.
"""
from __future__ import annotations

import re
from typing import Optional

import numpy as np

# ── Critério ──────────────────────────────────────────────────────────────────
FC_MAX_SOLAR = 0.25   # acima disso a geração NÃO pode ser fotovoltaica

COLS_ENE = tuple(f'ENE_{i:02d}' for i in range(1, 13))
HORAS_ANO = 8760.0

# Prefixo do CEG (código ANEEL) → tipo. 'GD' é micro/mini GD: tipo indefinido.
_RE_PREFIXO_CEG = re.compile(r'^\s*(CGH|PCH|UHE|EOL|UFV|UTE|CGU|CGB|GD)\.', re.IGNORECASE)

# Tipos usados na modelagem elétrica:
#   PV   → PVSystem com a curva solar (Curva_PV)
#   CGH  → hidro (base)      | EOL → eólica | UTE → térmica/biomassa
#   NSOL → não-solar de tipo desconhecido, detectado pelo FC (base)
# Fator de capacidade padrão quando a BDGD não traz energia (ENE_* zerado).
# Valores = mediana medida por tipo na própria base.
FC_PADRAO = {
    'CGH':  0.46,
    'EOL':  0.16,
    'UTE':  0.62,
    'NSOL': 0.45,
}


def fator_capacidade(pot_inst, energias) -> float:
    """FC anual = Σ ENE_mm / (POT_INST × 8760). Retorna 0.0 quando indeterminado."""
    try:
        pot = float(pot_inst)
    except (TypeError, ValueError):
        return 0.0
    if not np.isfinite(pot) or pot <= 0:
        return 0.0

    total = 0.0
    for e in (energias or []):
        try:
            v = float(e)
        except (TypeError, ValueError):
            continue
        if np.isfinite(v):
            total += v
    fc = total / (pot * HORAS_ANO)
    return float(fc) if np.isfinite(fc) and fc >= 0 else 0.0


def fator_capacidade_row(row) -> float:
    """FC de uma linha de UGBT/UGMT (usa POT_INST e ENE_01..ENE_12)."""
    getter = row.get if hasattr(row, 'get') else (lambda k, d=None: row[k] if k in row else d)
    return fator_capacidade(getter('POT_INST', 0), [getter(c, 0) for c in COLS_ENE])


def prefixo_ceg(ceg_gd) -> str:
    """Prefixo do código CEG em maiúsculas ('CGH', 'GD', …) ou '' se não casar."""
    m = _RE_PREFIXO_CEG.match(str(ceg_gd) if ceg_gd is not None else '')
    return m.group(1).upper() if m else ''


def classificar_tipo_gd(ceg_gd, fc: Optional[float] = None) -> str:
    """Classifica a fonte da GD em 'PV', 'CGH', 'EOL', 'UTE' ou 'NSOL'.

    Prioridade:
      1. prefixo explícito do CEG (código próprio da ANEEL — informação declarada);
      2. fator de capacidade acima do teto solar ⇒ 'NSOL' (geração de base);
      3. caso contrário, 'PV' (micro/mini GD sem contra-indicação = fotovoltaica).
    """
    p = prefixo_ceg(ceg_gd)
    if p in ('CGH', 'PCH', 'UHE'):
        return 'CGH'
    if p == 'EOL':
        return 'EOL'
    if p in ('UTE', 'CGU', 'CGB'):
        return 'UTE'
    if p == 'UFV':
        return 'PV'
    # Prefixo 'GD.' (ou ausente): o registro não diz a fonte. Decide o FC.
    if fc is not None and float(fc) > FC_MAX_SOLAR:
        return 'NSOL'
    return 'PV'


def fc_efetivo(tipo: str, fc: Optional[float]) -> float:
    """FC a aplicar na geração não-solar: o medido, ou a mediana do tipo se ausente."""
    try:
        v = float(fc) if fc is not None else 0.0
    except (TypeError, ValueError):
        v = 0.0
    if 0.0 < v <= 1.0:
        return v
    return FC_PADRAO.get(str(tipo).upper(), 0.45)
