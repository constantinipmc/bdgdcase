"""Curva fotovoltaica diária, sintetizada com ruído de semente fixa.

Gera curva PV diária em PU (96 pontos de 15 min) usando:
  - envelope de céu claro (semi-seno entre hora_inicio e hora_fim)
  - índice de claridade k_t ~ Beta(alpha, beta)

Saída principal para integração com OpenDSS:
  New Loadshape.<nome> npts=96 minterval=15 mult=[...]
"""

from __future__ import annotations

import argparse
import math
import random
from typing import List


def clear_sky_curve(
    npts: int = 96,
    hora_inicio: float = 6.0,
    hora_fim: float = 18.0,
    intervalo_horas: float = 0.25,
) -> List[float]:
    """Curva de céu claro normalizada em [0, 1] no horizonte diário."""
    if npts <= 0:
        raise ValueError("npts deve ser positivo.")
    if hora_fim <= hora_inicio:
        raise ValueError("hora_fim deve ser maior que hora_inicio.")
    if intervalo_horas <= 0:
        raise ValueError("intervalo_horas deve ser positivo.")

    janela = hora_fim - hora_inicio
    curva = []
    for step in range(npts):
        hora_dec = step * intervalo_horas
        if hora_dec < hora_inicio or hora_dec >= hora_fim:
            curva.append(0.0)
            continue
        frac = (hora_dec - hora_inicio) / janela
        curva.append(max(0.0, math.sin(frac * math.pi)))
    return curva


def moving_average_same(vals: List[float], window: int = 3) -> List[float]:
    """Média móvel com saída no mesmo tamanho da série."""
    if window <= 1:
        return list(vals)
    if window % 2 == 0:
        raise ValueError("window deve ser ímpar para alinhamento central.")
    n = len(vals)
    k = window // 2
    out = []
    for i in range(n):
        a = max(0, i - k)
        b = min(n, i + k + 1)
        trecho = vals[a:b]
        out.append(sum(trecho) / len(trecho))
    return out


def gerar_curva_pv_beta(
    npts: int = 96,
    alpha: float = 6.0,
    beta_param: float = 2.0,
    hora_inicio: float = 6.0,
    hora_fim: float = 18.0,
    smooth_window: int = 3,
    seed: int | None = None,
    n_dias_mc: int = 1,
) -> List[float]:
    """Gera curva PV em PU com pico normalizado em 1.0.

    Quando n_dias_mc > 1, calcula a média Monte Carlo de n_dias_mc curvas
    diárias.
    """
    if alpha <= 0 or beta_param <= 0:
        raise ValueError("alpha e beta_param devem ser positivos.")
    if n_dias_mc <= 0:
        raise ValueError("n_dias_mc deve ser positivo.")

    rng = random.Random(seed)
    ceu_claro = clear_sky_curve(
        npts=npts,
        hora_inicio=hora_inicio,
        hora_fim=hora_fim,
        intervalo_horas=24.0 / float(npts),
    )
    soma_curvas = [0.0] * npts
    for _ in range(n_dias_mc):
        kt = [rng.betavariate(alpha, beta_param) for _ in range(npts)]
        curva_dia = [c * k for c, k in zip(ceu_claro, kt)]

        if smooth_window and smooth_window > 1:
            curva_dia = moving_average_same(curva_dia, window=smooth_window)

        for i, v in enumerate(curva_dia):
            soma_curvas[i] += v

    curva_media = [v / float(n_dias_mc) for v in soma_curvas]
    vmax = max(curva_media) if curva_media else 0.0
    if vmax > 0:
        curva_media = [v / vmax for v in curva_media]
    return curva_media


def curva_para_linha_dss(mults: List[float], nome: str = "Curva_PV") -> str:
    """A curva como uma linha `New Loadshape` de 96 pontos a 15 minutos."""
    s = " ".join(f"{m:.6f}" for m in mults)
    return f"New Loadshape.{nome} npts={len(mults)} minterval=15 mult=[{s}]"


def exportar_dss(path_saida: str, mults: List[float], nome: str = "Curva_PV") -> None:
    """Grava a curva num `.dss` avulso, com o cabeçalho que diz de onde veio."""
    linhas = [
        "! Curva PV: bdgdcase.curva_pv (envelope de ceu claro x Beta, semente fixa)",
        curva_para_linha_dss(mults, nome=nome),
        "",
    ]
    with open(path_saida, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas))


def _parse_args() -> argparse.Namespace:
    """Os argumentos da linha de comando deste gerador avulso."""
    parser = argparse.ArgumentParser(description="Gera curva PV Beta para OpenDSS.")
    parser.add_argument("--alpha", type=float, default=6.0)
    parser.add_argument("--beta", dest="beta_param", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--hora-inicio", type=float, default=6.0)
    parser.add_argument("--hora-fim", type=float, default=18.0)
    parser.add_argument("--smooth-window", type=int, default=3)
    parser.add_argument("--n-dias-mc", type=int, default=1)
    parser.add_argument("--npts", type=int, default=96)
    parser.add_argument("--nome-loadshape", default="Curva_PV")
    parser.add_argument("--out-dss", required=True)
    return parser.parse_args()


def main() -> None:
    """Gera uma curva PV e a grava — o modo avulso deste módulo.

    Existe para experimentar parâmetros da Beta sem rodar a conversão inteira:
    a forma da curva de geração muda o pico de tensão do meio-dia, e vê-la
    isolada é mais rápido que inferi-la de um alimentador.
    """
    args = _parse_args()
    curva = gerar_curva_pv_beta(
        npts=args.npts,
        alpha=args.alpha,
        beta_param=args.beta_param,
        hora_inicio=args.hora_inicio,
        hora_fim=args.hora_fim,
        smooth_window=args.smooth_window,
        seed=args.seed,
        n_dias_mc=args.n_dias_mc,
    )
    exportar_dss(args.out_dss, curva, nome=args.nome_loadshape)


if __name__ == "__main__":
    main()
