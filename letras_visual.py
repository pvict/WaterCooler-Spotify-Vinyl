"""Composição das letras para a área circular da tela USB de 480 pixels."""

import os
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from texto_emoji import desenhar_texto, medir_texto, unidades


_LADO = 480
_CENTRO = _LADO // 2
_PASTA = Path(__file__).resolve().parent
_RAIO_LETRAS = 222
_TOPO_LETRAS, _BASE_LETRAS = _CENTRO - _RAIO_LETRAS, _CENTRO + _RAIO_LETRAS + 1
_LARGURA_LETRAS = 392
_ENTRELINHA = 48
_ESPACO_VERSOS = 12
_ESCALA_CONTEXTO = 0.67
_ALFA_CONTEXTO = 142
_TEMPO_ROLAGEM = 0.32
_FADE_BORDAS = 24


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
    if capa is None:
        return Image.new("RGB", (_LADO, _LADO), (10, 10, 20))
    # putalpha() na capa do vinil mantém o RGB do recorte quadrado completo.
    quadrada = ImageOps.fit(capa.convert("RGB"), (_LADO, _LADO), Image.Resampling.LANCZOS)
    borrada = quadrada.filter(ImageFilter.GaussianBlur(23))
    return ImageEnhance.Brightness(borrada).enhance(0.60)


def _linhas(texto, fonte, largura, limite):
    linhas = []
    while texto and len(linhas) < limite:
        if medir_texto(texto, fonte) <= largura:
            linhas.append(texto)
            texto = ""
            break
        ultima = len(linhas) == limite - 1
        sufixo = "…" if ultima else ""
        grupos = list(unidades(texto))
        fim = 1
        while (fim < len(grupos)
               and medir_texto("".join(grupos[:fim + 1]) + sufixo, fonte) <= largura):
            fim += 1
        corte = sum(len(grupo) for grupo in grupos[:fim])
        espaco = texto.rfind(" ", 0, corte + 1)
        if espaco > 0 and (not ultima or espaco > corte * 0.7):
            corte = espaco
        linhas.append(texto[:corte].rstrip() + sufixo)
        texto = texto[corte:].lstrip()
    return linhas, not texto


@lru_cache(maxsize=256)
def _verso_renderizado(texto):
    fonte = _fonte(42)
    linhas, _ = _linhas(texto, fonte, _LARGURA_LETRAS - 4, 3)
    verso = Image.new("RGBA", (_LARGURA_LETRAS, max(1, len(linhas)) * _ENTRELINHA))
    for i, linha in enumerate(linhas):
        desenhar_texto(verso, (_LARGURA_LETRAS // 2, i * _ENTRELINHA + 38),
                      linha, fonte, "white", "ms")
    return verso


def _escalar_verso(verso, escala):
    if escala == 1:
        return verso
    tamanho = round(verso.width * escala), round(verso.height * escala)
    return verso.resize(tamanho, Image.Resampling.BICUBIC)


@lru_cache(maxsize=256)
def _verso_contexto(texto):
    return _escalar_verso(_verso_renderizado(texto), _ESCALA_CONTEXTO)


@lru_cache(maxsize=128)
def _legenda(texto):
    largura, altura = 332, 80
    for tamanho in (32, 30, 28, 26, 24):
        fonte = _fonte(tamanho)
        linhas, completa = _linhas(texto, fonte, largura - 14, 2)
        if completa:
            break
    legenda = Image.new("RGBA", (largura, altura))
    for i, linha in enumerate(reversed(linhas)):
        desenhar_texto(legenda, (largura // 2, altura - 11 - i * 35), linha, fonte,
                       "white", "ms", stroke_width=3, stroke_fill="black")
    return legenda


@lru_cache(maxsize=1)
def _fade_bordas():
    mascara = Image.new("L", (_LADO, _LADO))
    desenho = ImageDraw.Draw(mascara)
    for passo in range(1, _FADE_BORDAS + 1):
        raio = _RAIO_LETRAS - passo
        desenho.ellipse((_CENTRO - raio, _CENTRO - raio, _CENTRO + raio, _CENTRO + raio),
                        fill=round(255 * passo / _FADE_BORDAS))
    return mascara


class VisualLetras:
    def __init__(self):
        self._capa = None
        self._fundo = None
        self._visual = None
        self._quadro = None

    def spotify(self, capa, letra, posicao):
        indice = letra.indice(posicao)
        fase = 1.0 if indice >= 0 else 0.0
        if indice > 0:
            decorrido = max(0, posicao - letra.tempos[indice] / 1000)
            fase = 1 - (1 - min(1, decorrido / _TEMPO_ROLAGEM)) ** 3
        if self._fundo is None or capa is not self._capa:
            self._capa, self._fundo = capa, fundo_da_letra(capa)
            self._visual = None
        chave = (letra, indice, fase)
        if chave == self._visual:
            return self._quadro

        central = max(indice, 0)
        texto = letra.linhas[central]
        centro_y = _CENTRO
        anterior = None
        if indice > 0 and fase < 1:
            anterior = _verso_renderizado(letra.linhas[indice - 1])
            distancia = (anterior.height + _verso_contexto(texto).height) / 2 + _ESPACO_VERSOS
            centro_y += distancia * (1 - fase)
        ativo = (_verso_contexto(texto) if fase == 0 else
                 _escalar_verso(_verso_renderizado(texto), _ESCALA_CONTEXTO + (1 - _ESCALA_CONTEXTO) * fase))
        diferenca_alfa = 255 - _ALFA_CONTEXTO
        topo = centro_y - ativo.height / 2
        quadro = self._fundo.copy()
        self._colar_verso(quadro, ativo, round(topo), round(_ALFA_CONTEXTO + diferenca_alfa * fase))
        for direcao, borda in ((-1, topo), (1, topo + ativo.height)):
            i = central + direcao
            while 0 <= i < len(letra.linhas) and _TOPO_LETRAS < borda < _BASE_LETRAS:
                if anterior is not None and i == indice - 1:
                    verso = _escalar_verso(anterior, 1 - (1 - _ESCALA_CONTEXTO) * fase)
                    alfa = round(255 - diferenca_alfa * fase)
                else:
                    verso = _verso_contexto(letra.linhas[i])
                    alfa = _ALFA_CONTEXTO
                linha_y = (borda - _ESPACO_VERSOS - verso.height if direcao < 0
                           else borda + _ESPACO_VERSOS)
                self._colar_verso(quadro, verso, round(linha_y), alfa)
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
        x = (_LADO - verso.width) // 2
        faixa = _fade_bordas().crop((x, topo, x + verso.width, base))
        mascara = ImageChops.multiply(verso.getchannel("A"), faixa)
        if alpha != 255:
            mascara = mascara.point(lambda v: int(v * alpha / 255))
        quadro.paste(verso, (x, topo), mascara)

    def clipe(self, quadro, letra, posicao):
        indice = letra.indice(posicao)
        if indice < 0 or not letra.linhas[indice]:
            return quadro
        legenda = _legenda(letra.linhas[indice])
        legendado = quadro.copy()
        legendado.paste(legenda, (_CENTRO - legenda.width // 2, 391 - legenda.height), legenda)
        return legendado
