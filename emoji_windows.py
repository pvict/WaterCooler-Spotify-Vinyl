"""Rasteriza somente emojis com Segoe UI Emoji/DirectWrite, incluindo ligaduras.

Direct2D/WIC produzem RGBA fora de qualquer janela. Nenhuma fonte é instalada.
Os objetos COM ficam na thread que os criou; o cache guarda apenas pixels.
Referência: https://learn.microsoft.com/windows/win32/directwrite/color-fonts
"""
import ctypes as c
import math
import uuid
from functools import lru_cache

from PIL import Image


class GUID(c.Structure):
    _fields_ = [("bytes", c.c_ubyte * 16)]

    def __init__(self, texto):
        super().__init__((c.c_ubyte * 16).from_buffer_copy(uuid.UUID(texto).bytes_le))


class Cor(c.Structure):
    _fields_ = [(nome, c.c_float) for nome in ("r", "g", "b", "a")]


class Ponto(c.Structure):
    _fields_ = [("x", c.c_float), ("y", c.c_float)]


class Propriedades(c.Structure):
    _fields_ = [("tipo", c.c_uint), ("formato", c.c_uint), ("alfa", c.c_uint),
                ("dpi_x", c.c_float), ("dpi_y", c.c_float),
                ("uso", c.c_uint), ("nivel", c.c_uint)]


class Metricas(c.Structure):
    _fields_ = [(nome, c.c_float) for nome in
                ("left", "top", "width", "width_trailing", "height", "layout_width", "layout_height")]
    _fields_ += [("depth", c.c_uint), ("lines", c.c_uint)]


class Linha(c.Structure):
    _fields_ = [("length", c.c_uint), ("spaces", c.c_uint), ("newlines", c.c_uint),
                ("height", c.c_float), ("baseline", c.c_float), ("trimmed", c.c_int)]


def _chamar(objeto, indice, retorno, tipos, *args):
    tabela = c.cast(objeto, c.POINTER(c.POINTER(c.c_void_p))).contents
    metodo = c.WINFUNCTYPE(retorno, c.c_void_p, *tipos)(tabela[indice])
    return metodo(objeto, *args)


def _conferir(hr):
    if hr < 0:
        raise OSError(f"Renderização de emoji: HRESULT 0x{hr & 0xffffffff:08X}")


@lru_cache(maxsize=256)
def rasterizar_emoji(texto, tamanho):
    """(imagem RGBA, avanço, deslocamento relativo à linha de base)."""
    ole = c.WinDLL("ole32")
    ole.CoInitializeEx.argtypes = [c.c_void_p, c.c_uint]
    ole.CoInitializeEx.restype = c.c_long
    ole.CoUninitialize.argtypes = []
    hr = ole.CoInitializeEx(None, 0)
    inicializou = hr in (0, 1)
    if not inicializou and (hr & 0xffffffff) != 0x80010106:  # já inicializado em STA
        _conferir(hr)
    objetos = []

    def saida():
        ptr = c.c_void_p()
        objetos.append(ptr)
        return ptr

    try:
        dwrite = c.WinDLL("dwrite")
        dwrite.DWriteCreateFactory.argtypes = [c.c_uint, c.POINTER(GUID), c.POINTER(c.c_void_p)]
        dwrite.DWriteCreateFactory.restype = c.c_long
        dw = saida()
        _conferir(dwrite.DWriteCreateFactory(0, c.byref(GUID("b859ee5a-d838-4b5b-a2e8-1adc7d93db48")), c.byref(dw)))
        formato = saida()
        _conferir(_chamar(dw, 15, c.c_long,
            [c.c_wchar_p, c.c_void_p, c.c_uint, c.c_uint, c.c_uint, c.c_float, c.c_wchar_p, c.POINTER(c.c_void_p)],
            "Segoe UI Emoji", None, 400, 0, 5, tamanho, "pt-BR", c.byref(formato)))
        layout = saida()
        # DirectWrite recebe comprimento em unidades UTF-16, não codepoints.
        comprimento = len(texto.encode("utf-16-le")) // 2
        _conferir(_chamar(dw, 18, c.c_long,
            [c.c_wchar_p, c.c_uint, c.c_void_p, c.c_float, c.c_float, c.POINTER(c.c_void_p)],
            texto, comprimento, formato, tamanho * 16, tamanho * 4, c.byref(layout)))
        metricas, linha, quantidade = Metricas(), Linha(), c.c_uint()
        _conferir(_chamar(layout, 60, c.c_long, [c.POINTER(Metricas)], c.byref(metricas)))
        _conferir(_chamar(layout, 59, c.c_long, [c.POINTER(Linha), c.c_uint, c.POINTER(c.c_uint)],
                         c.byref(linha), 1, c.byref(quantidade)))
        margem = max(4, math.ceil(tamanho / 3))
        w = max(1, math.ceil(metricas.width_trailing) + margem * 2)
        h = max(1, math.ceil(metricas.height) + margem * 2)
        wic = saida()
        ole.CoCreateInstance.argtypes = [c.POINTER(GUID), c.c_void_p, c.c_uint, c.POINTER(GUID), c.POINTER(c.c_void_p)]
        ole.CoCreateInstance.restype = c.c_long
        _conferir(ole.CoCreateInstance(c.byref(GUID("cacaf262-9370-4615-a13b-9f5539da4c0a")), None, 1,
            c.byref(GUID("ec5ec8a9-c395-4314-9c77-54d7a935ff70")), c.byref(wic)))
        bitmap = saida()
        pixel = GUID("6fddc324-4e03-4bfe-b185-3d77768dc910")  # 32bppPBGRA
        _conferir(_chamar(wic, 17, c.c_long,
            [c.c_uint, c.c_uint, c.POINTER(GUID), c.c_uint, c.POINTER(c.c_void_p)],
            w, h, c.byref(pixel), 2, c.byref(bitmap)))
        d2d = c.WinDLL("d2d1")
        d2d.D2D1CreateFactory.argtypes = [c.c_uint, c.POINTER(GUID), c.c_void_p, c.POINTER(c.c_void_p)]
        d2d.D2D1CreateFactory.restype = c.c_long
        fabrica = saida()
        _conferir(d2d.D2D1CreateFactory(0, c.byref(GUID("06152247-6f50-465a-9245-118bfd3b6007")), None, c.byref(fabrica)))
        alvo = saida()
        propriedades = Propriedades(0, 87, 1, 96, 96, 0, 0)
        _conferir(_chamar(fabrica, 13, c.c_long,
            [c.c_void_p, c.POINTER(Propriedades), c.POINTER(c.c_void_p)],
            bitmap, c.byref(propriedades), c.byref(alvo)))
        pincel = saida()
        _conferir(_chamar(alvo, 8, c.c_long, [c.POINTER(Cor), c.c_void_p, c.POINTER(c.c_void_p)],
                         c.byref(Cor(1, 1, 1, 1)), None, c.byref(pincel)))
        _chamar(alvo, 48, None, [])  # BeginDraw
        _chamar(alvo, 47, None, [c.POINTER(Cor)], c.byref(Cor(0, 0, 0, 0)))
        _chamar(alvo, 28, None, [Ponto, c.c_void_p, c.c_void_p, c.c_uint],
                Ponto(margem, margem), layout, pincel, 4)  # ENABLE_COLOR_FONT
        _conferir(_chamar(alvo, 49, c.c_long, [c.c_void_p, c.c_void_p], None, None))
        buffer = (c.c_ubyte * (w * h * 4))()
        _conferir(_chamar(bitmap, 7, c.c_long, [c.c_void_p, c.c_uint, c.c_uint, c.c_void_p],
                         None, w * 4, len(buffer), buffer))
        # WIC usa canais premultiplicados. Pillow desfaz a premultiplicação.
        imagem = Image.frombytes("RGBa", (w, h), bytes(buffer), "raw", "BGRa").convert("RGBA")
        caixa = imagem.getbbox() or (0, 0, 1, 1)
        return imagem.crop(caixa), metricas.width_trailing, (caixa[0] - margem, caixa[1] - margem - linha.baseline)
    finally:
        for objeto in reversed(objetos):
            if objeto:
                _chamar(objeto, 2, c.c_uint, [])
        if inicializou:
            ole.CoUninitialize()
