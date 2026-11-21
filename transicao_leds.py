"""Referência de cor e progresso compartilhados pelos LEDs da placa e da fita."""
import math
from typing import NamedTuple

CHAVE_PASSO = "passo_openrgb"


class ReferenciaRGB(NamedTuple):
    capa: tuple | None
    brilho: int


class PassoRGB(NamedTuple):
    referencia: ReferenciaRGB
    transicao: object | None
    peso: float


def referencia_rgb(estado, brilho):
    modo = estado.get("modo_exibicao")
    midia = estado
    if modo in ("spotify", "clipe") and estado.get("musica") is None:
        midia = estado.get("ultima_midia") or estado
    if modo != "video" and midia.get("musica") is not None:
        capa = midia.get("cor_viva") or midia.get("cor_capa")
        if capa is not None:
            return ReferenciaRGB(tuple(capa[:3]), brilho)
    return ReferenciaRGB(None, brilho)


def peso_transicao(progresso):
    return (1 - math.cos(progresso * math.pi)) / 2
