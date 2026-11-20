"""Texto Pillow com fallback somente em clusters de emoji; sem substituir letras."""
import os
import unicodedata
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


@lru_cache(maxsize=1)
def _propriedades():
    dados = Path(__file__).resolve().parent / "assets/fonts/emoji-data.txt"
    propriedades = {"Emoji": set(), "Emoji_Presentation": set()}
    for linha in dados.read_text(encoding="utf-8").splitlines():
        conteudo = linha.split("#", 1)[0].strip()
        if not conteudo:
            continue
        faixa, propriedade = (s.strip() for s in conteudo.split(";"))
        if propriedade in propriedades:
            extremos = faixa.split("..")
            propriedades[propriedade].update(range(int(extremos[0], 16), int(extremos[-1], 16) + 1))
    return propriedades


def unidades(texto):
    """Mantém modificadores, ZWJ, bandeiras e keycaps juntos ao quebrar/cortar."""
    i = 0
    while i < len(texto):
        inicio = i
        i += 1
        if 0x1F1E6 <= ord(texto[inicio]) <= 0x1F1FF and i < len(texto) and 0x1F1E6 <= ord(texto[i]) <= 0x1F1FF:
            i += 1
        while i < len(texto):
            cp = ord(texto[i])
            if (unicodedata.category(texto[i]).startswith("M") or cp in (0xFE0E, 0xFE0F)
                    or 0x1F3FB <= cp <= 0x1F3FF or 0xE0020 <= cp <= 0xE007F):
                i += 1
            elif cp == 0x200D and i + 1 < len(texto) and texto[i + 1] != "\n":
                i += 2
            else:
                break
        yield texto[inicio:i]


def _emoji(unidade):
    if "\ufe0e" in unidade:
        return False  # O nome pediu apresentação textual explicitamente.
    p = _propriedades()
    return (any(ord(c) in p["Emoji_Presentation"] for c in unidade)
            or (any(c in unidade for c in ("\ufe0f", "\u20e3", "\u200d"))
                and any(ord(c) in p["Emoji"] for c in unidade)))


@lru_cache(maxsize=512)
def segmentos(texto):
    partes = []
    for unidade in unidades(texto):
        emoji = _emoji(unidade)
        if not emoji and partes and not partes[-1][1]:
            partes[-1] = (partes[-1][0] + unidade, False)
        else:
            partes.append((unidade, emoji))
    return tuple(partes)


def contem_emoji(texto):
    return any(emoji for _, emoji in segmentos(texto))


@lru_cache(maxsize=128)
def _emoji_bitmap(texto, tamanho):
    if os.name == "nt":
        from emoji_windows import rasterizar_emoji
        try:
            return rasterizar_emoji(texto, tamanho)
        except (OSError, AttributeError, ValueError):
            pass
    fonte = _fonte_emoji(tamanho)
    if fonte is not None:
        try:
            caixa = fonte.getbbox(texto, anchor="ls")
            imagem = Image.new("RGBA", (max(1, caixa[2] - caixa[0]), max(1, caixa[3] - caixa[1])))
            ImageDraw.Draw(imagem).text((-caixa[0], -caixa[1]), texto, font=fonte,
                                        anchor="ls", embedded_color=True)
            return imagem, fonte.getlength(texto), caixa[:2]
        except (OSError, ValueError):
            return None


@lru_cache(maxsize=16)
def _fonte_emoji(tamanho):
    try:
        return ImageFont.truetype("seguiemj.ttf", tamanho)
    except OSError:
        return None


@lru_cache(maxsize=128)
def _layout(texto, fonte):
    cursor, caixas, partes = 0.0, [], []
    for trecho, emoji in segmentos(texto):
        bitmap = _emoji_bitmap(trecho, fonte.size) if emoji else None
        if bitmap is not None:
            imagem, largura, (dx, dy) = bitmap
            caixa = (cursor + dx, dy, cursor + dx + imagem.width, dy + imagem.height)
            partes.append((cursor, imagem, (dx, dy)))
        else:
            largura = fonte.getlength(trecho)
            b = fonte.getbbox(trecho, anchor="ls")
            caixa = (cursor + b[0], b[1], cursor + b[2], b[3])
            partes.append((cursor, trecho, None))
        caixas.append(caixa)
        cursor += largura
    caixa = (min(b[0] for b in caixas), min(b[1] for b in caixas),
             max(b[2] for b in caixas), max(b[3] for b in caixas)) if caixas else (0, 0, 0, 0)
    return cursor, caixa, tuple(partes)


def medir_texto(texto, fonte):
    return _layout(texto, fonte)[0] if contem_emoji(texto) else fonte.getlength(texto)


def caixa_texto(texto, fonte):
    """Caixa relativa à âncora padrão Pillow (ascendente esquerdo)."""
    if not contem_emoji(texto):
        return fonte.getbbox(texto)
    _, caixa, _ = _layout(texto, fonte)
    ascendente = fonte.getmetrics()[0]
    return caixa[0], caixa[1] + ascendente, caixa[2], caixa[3] + ascendente


def desenhar_texto(imagem, posicao, texto, fonte, cor, ancora=None, *, stroke_width=0, stroke_fill=None):
    desenho = ImageDraw.Draw(imagem)
    if not contem_emoji(texto):
        desenho.text(posicao, texto, font=fonte, fill=cor, anchor=ancora,
                     stroke_width=stroke_width, stroke_fill=stroke_fill)
        return
    largura, caixa, partes = _layout(texto, fonte)
    horizontal, vertical = ancora or "la"
    x, y = posicao
    x -= largura / 2 if horizontal == "m" else largura if horizontal == "r" else 0
    if vertical == "t":
        y -= caixa[1]
    elif vertical == "b":
        y -= caixa[3]
    elif vertical == "m":
        y -= (caixa[1] + caixa[3]) / 2
    elif vertical == "a":
        y += fonte.getmetrics()[0]
    for cursor, conteudo, offset in partes:
        if offset is None:
            desenho.text((x + cursor, y), conteudo, font=fonte, fill=cor, anchor="ls",
                         stroke_width=stroke_width, stroke_fill=stroke_fill)
        else:
            destino = (round(x + cursor + offset[0]), round(y + offset[1]))
            if stroke_width:
                contorno = ImageOps.expand(conteudo.getchannel("A"), stroke_width)
                contorno = contorno.filter(ImageFilter.MaxFilter(2 * stroke_width + 1))
                origem = (destino[0] - stroke_width, destino[1] - stroke_width)
                caixa_contorno = (*origem, origem[0] + contorno.width, origem[1] + contorno.height)
                imagem.paste(stroke_fill or cor, caixa_contorno, contorno)
            if imagem.mode == "RGBA":
                imagem.alpha_composite(conteudo, destino)
            else:
                imagem.paste(conteudo, destino, conteudo)


def encurtar_texto(texto, largura, medir):
    if medir(texto) <= largura:
        return texto
    partes = list(unidades(texto))
    while partes and medir("".join(partes) + "…") > largura:
        partes.pop()
    return "".join(partes) + "…"
