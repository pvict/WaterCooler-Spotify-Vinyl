"""Enquadramento e tratamento compartilhados pela prévia e pelo vídeo exportado."""
import math

AJUSTES_PADRAO = {"zoom": 100, "x": 50, "y": 50,
                  "brilho": 100, "saturacao": 100, "nitidez": 100}
LIMITES_AJUSTES = {"zoom": (25, 300), "x": (0, 100), "y": (0, 100),
                   "brilho": (0, 200), "saturacao": (0, 200), "nitidez": (0, 200)}


def validar_ajustes(dados):
    ajustes = dict(AJUSTES_PADRAO)
    if isinstance(dados, dict):
        for chave, (minimo, maximo) in LIMITES_AJUSTES.items():
            valor = dados.get(chave)
            if type(valor) in (int, float) and math.isfinite(valor) and minimo <= valor <= maximo:
                ajustes[chave] = round(valor)
    return ajustes


def posicao_arraste(dimensoes, ajustes, inicio, delta):
    """Converte movimento na prévia em recorte/padding, individualmente por eixo."""
    w, h = dimensoes
    escala = max(480 / w, 480 / h) * ajustes["zoom"] / 100
    tamanhos = (max(2, int(w * escala / 2) * 2), max(2, int(h * escala / 2) * 2))
    return tuple(max(0, min(100, round(p - d * 480 / (tamanho - 480) * 100)))
                 if tamanho != 480 else p
                 for tamanho, p, d in zip(tamanhos, inicio, delta))


def filtro_ajustes(dados):
    a = validar_ajustes(dados)
    zoom = a["zoom"] / 100
    x, y = a["x"] / 100, a["y"] / 100
    # A escala relativa usa as dimensões depois da autorrotação do FFmpeg.
    # Pad permite diminuir abaixo do preenchimento, com margens pretas reais.
    filtros = [
        f"scale=w='max(2,trunc(iw*max(480/iw,480/ih)*{zoom:.4f}/2)*2)':"
        f"h='max(2,trunc(ih*max(480/iw,480/ih)*{zoom:.4f}/2)*2)'",
        f"pad=w='max(iw,480)':h='max(ih,480)':x='(ow-iw)*{x:.4f}':y='(oh-ih)*{y:.4f}':color=black",
        f"crop=480:480:x='(iw-ow)*{x:.4f}':y='(ih-oh)*{y:.4f}'",
        "setsar=1",
    ]
    if a["saturacao"] != 100:
        s = a["saturacao"] / 100
        nomes = ("r", "g", "b")
        pesos = (0.2126, 0.7152, 0.0722)
        termos = [f"{destino}{origem}={(1 - s) * pesos[j] + (s if i == j else 0):.6f}"
                  for i, destino in enumerate(nomes) for j, origem in enumerate(nomes)]
        filtros.append("colorchannelmixer=" + ":".join(termos))
    if a["nitidez"] < 100:
        filtros.append(f"gblur=sigma={(100 - a['nitidez']) / 50:.4f}")
    elif a["nitidez"] > 100:
        filtros.append(f"unsharp=5:5:{(a['nitidez'] - 100) / 50:.4f}:5:5:0")
    if a["brilho"] != 100:
        fator = a["brilho"] / 100
        filtros.append(f"colorchannelmixer=rr={fator:.4f}:gg={fator:.4f}:bb={fator:.4f}")
    return ",".join(filtros)
