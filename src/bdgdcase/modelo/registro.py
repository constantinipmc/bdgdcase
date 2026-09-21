# -*- coding: utf-8 -*-
"""O que a conversão conta enquanto trabalha.

Duas coisas diferentes, e vale distingui-las:

**O relato no terminal** (`_log_secao`, `_log_kv`, `_fmt_amostra`) é para quem
está olhando agora. Sai em ordem, acompanha o passo a passo, e some quando a
janela fecha.

**O diagnóstico** (`_DIAG`, `_diag_set`, `_diag_inc`) é um placar que atravessa
a conversão inteira: quantos trechos ficaram sem condutor, quantas UCs não
acharam poste, quantos transformadores tiveram a tensão corrigida. Ele é
consultado no fim, quando já se sabe o que perguntar — e é o que permite dizer
"foram 412" em vez de "houve casos".

Nenhum dos dois é o registro de ajustes: aquele é `bdgdcase.ajustes`, tem
categoria, efeito e motivo, e vai parar na aba que o usuário lê. Este aqui é
para quem está depurando a conversão, não para quem está lendo o caso.
"""
from __future__ import annotations


def _log_secao(titulo):
    """Abre uma seção do relato, para o terminal não virar um muro de linhas."""
    print(f"\n[INFO] {titulo}")


def _log_kv(chave, valor):
    """Uma linha de `chave: valor` alinhada, dentro de uma seção."""
    print(f"  - {chave:<24}: {valor}")


def _fmt_amostra(seq, n=8):
    """Os primeiros itens de uma sequência, para o relato mostrar exemplo.

    Contagem sem exemplo não se investiga: saber que 412 trechos ficaram sem
    condutor não diz por onde começar; ver oito COD_ID diz.
    """
    vals = list(seq)[:n]
    return ", ".join(str(v) for v in vals) if vals else "(vazio)"


#: O placar da conversão, preenchido ao longo dela e lido no fim.
_DIAG = {}


def _diag_set(key, value):
    """Anota um valor no placar, substituindo o que houver."""
    _DIAG[key] = value


def _diag_inc(key, delta=1):
    """Soma ao placar, começando do zero se for a primeira vez."""
    _DIAG[key] = int(_DIAG.get(key, 0)) + int(delta)
