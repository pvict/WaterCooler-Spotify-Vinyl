"""Composição das letras para a área circular da tela USB de 480 pixels."""

import os
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps


_LADO = 480
_CENTRO = _LADO // 2
_PASTA = Path(__file__).resolve().parent
_TOPO_LETRAS, _BASE_LETRAS = 82, 398
_MARGEM_LETRAS = 80
_ENTRELINHA = 38
_ESPACO_VERSOS = 9
_TEMPO_ROLAGEM = 0.28
_FADE_BORDAS = 35


@lru_cache(maxsize=4)
def _fonte(tamanho):
    arquivos = (
        _PASTA / "Fonts" / "SFPRODISPLAYBOLD.OTF",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeuib.ttf",
        _PASTA / "assets" / "fonts" / "dmsans.ttf",
    )
    for arquivo in arquivos:
        try:
            fonte = ImageFont.truetype(arquivo, tamanho)
        except OSError:
            continue
        if arquivo.name == "dmsans.ttf":
            fonte.set_variation_by_name("Bold")
        return fonte
    return ImageFont.load_default(size=tamanho)


def fundo_da_letra(capa):
    # putalpha() na capa do vinil mantém o RGB do recorte quadrado completo.
    quadrada = ImageOps.fit(capa.convert("RGB"), (_LADO, _LADO), Image.Resampling.LANCZOS)
    borrada = quadrada.filter(ImageFilter.GaussianBlur(23))
    return ImageEnhance.Brightness(borrada).enhance(0.60)


def _linhas(texto, fonte, largura, limite):
    linhas = []
    while texto and len(linhas) < limite:
        if fonte.getlength(texto) <= largura:
            linhas.append(texto)
            texto = ""
            break
        ultima = len(linhas) == limite - 1
        sufixo = "…" if ultima else ""
        fim = 1
        while fim < len(texto) and fonte.getlength(texto[:fim + 1] + sufixo) <= largura:
            fim += 1
        espaco = texto.rfind(" ", 0, fim + 1)
        if espaco > 0 and (not ultima or espaco > fim * 0.7):
            fim = espaco
        linhas.append(texto[:fim].rstrip() + sufixo)
        texto = texto[fim:].lstrip()
    return linhas, not texto


@lru_cache(maxsize=256)
def _mascara_verso(texto):
    fonte = _fonte(34)
    largura = _LADO - 2 * _MARGEM_LETRAS
    linhas, _ = _linhas(texto, fonte, largura - 8, 3)
    mascara = Image.new("L", (largura, max(1, len(linhas)) * _ENTRELINHA))
    desenho = ImageDraw.Draw(mascara)
    for i, linha in enumerate(linhas):
        desenho.text((4, i * _ENTRELINHA + 29), linha, font=fonte, fill=255, anchor="ls")
    return mascara


@lru_cache(maxsize=128)
def _legenda(texto):
    largura, altura = 332, 80
    for tamanho in (32, 30, 28, 26, 24):
        fonte = _fonte(tamanho)
        linhas, completa = _linhas(texto, fonte, largura - 14, 2)
        if completa:
            break
    legenda = Image.new("RGBA", (largura, altura))
    desenho = ImageDraw.Draw(legenda)
    for i, linha in enumerate(reversed(linhas)):
        desenho.text((largura // 2, altura - 11 - i * 35), linha, anchor="ms",
                     font=fonte, fill="white", stroke_width=3, stroke_fill="black")
    return legenda


@lru_cache(maxsize=1)
def _fade_bordas():
    altura = _BASE_LETRAS - _TOPO_LETRAS
    mascara = Image.new("L", (1, altura))
    mascara.putdata([round(255 * min(1, y / _FADE_BORDAS, (altura - 1 - y) / _FADE_BORDAS))
                     for y in range(altura)])
    return mascara.resize((_LADO - 2 * _MARGEM_LETRAS, altura))


class VisualLetras:
    def __init__(self):
        self._capa = None
        self._fundo = None
        self._visual = None
        self._quadro = None

    def spotify(self, capa, letra, posicao):
        indice = letra.indice(posicao)
        central = max(indice, 0)
        mascara = _mascara_verso(letra.linhas[central])
        y = (_LADO - mascara.height) // 2
        if indice > 0:
            decorrido = max(0, posicao - letra.tempos[indice] / 1000)
            fase = min(1, decorrido / _TEMPO_ROLAGEM)
            anterior = _mascara_verso(letra.linhas[indice - 1])
            distancia = (mascara.height + anterior.height) / 2 + _ESPACO_VERSOS
            y += round(distancia * (1 - fase) ** 3)
        if capa is not self._capa:
            self._capa, self._fundo = capa, fundo_da_letra(capa)
            self._visual = None
        chave = (letra, indice, y)
        if chave == self._visual:
            return self._quadro

        quadro = self._fundo.copy()
        self._colar_verso(quadro, mascara, y, alpha=255 if indice >= 0 else 142)
        for direcao, borda in ((-1, y), (1, y + mascara.height)):
            i = central + direcao
            while 0 <= i < len(letra.linhas) and _TOPO_LETRAS < borda < _BASE_LETRAS:
                verso = _mascara_verso(letra.linhas[i])
                linha_y = (borda - _ESPACO_VERSOS - verso.height if direcao < 0
                           else borda + _ESPACO_VERSOS)
                self._colar_verso(quadro, verso, linha_y, alpha=142)
                borda = linha_y if direcao < 0 else linha_y + verso.height
                i += direcao
        self._visual, self._quadro = chave, quadro
        return quadro

    @staticmethod
    def _colar_verso(quadro, verso, y, alpha):
        topo, base = max(y, _TOPO_LETRAS), min(y + verso.height, _BASE_LETRAS)
        if topo >= base:
            return
        if topo != y or base != y + verso.height:
            verso = verso.crop((0, topo - y, verso.width, base - y))
        if topo < _TOPO_LETRAS + _FADE_BORDAS or base > _BASE_LETRAS - _FADE_BORDAS:
            faixa = _fade_bordas().crop((0, topo - _TOPO_LETRAS, verso.width, base - _TOPO_LETRAS))
            verso = ImageChops.multiply(verso, faixa)
        if alpha != 255:
            verso = verso.point(lambda v: int(v * alpha / 255))
        quadro.paste("white", (_MARGEM_LETRAS, topo, _MARGEM_LETRAS + verso.width, base), verso)

    def clipe(self, quadro, letra, posicao):
        indice = letra.indice(posicao)
        if indice < 0 or not letra.linhas[indice]:
            return quadro
        legenda = _legenda(letra.linhas[indice])
        legendado = quadro.copy()
        legendado.paste(legenda, (_CENTRO - legenda.width // 2, 391 - legenda.height), legenda)
        return legendado
