"""Janela de controle do Turing Vinyl. Execute: python interface.py.

Tkinter + Pillow, sem navegador embutido. Prévia pequena com alvo de 60 FPS.
O script original roda em um processo separado, com comandos em pipes locais.
"""
import base64
import ctypes
import io
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque
from ctypes import wintypes
from pathlib import Path
from tkinter import filedialog

from PIL import Image, ImageDraw, ImageFilter, ImageTk

from aparencia_windows import preparar_janela, preparar_identidade_processo
from controles_spotify import ControlesSpotify
from fundo_usuario import ConversorFundo
from editor_fundo import EditorFundo
from leds_lotus import pasta_valida
from painel_lotus import PainelLotus
from painel_cor_fundo import PainelCorFundo
from controle_interface import (LIMITE_SINCRONIA_CLIPE_MS, LIMITE_SINCRONIA_LETRAS_MS, PREFIXO,
                                brilho_horario, carregar, salvar, reservar_execucao, liberar_execucao)
from estilo_interface import (CanvasSuave, FonteInterface, Dica, configurar_dpi,
                               superficie, icone, limitar_linhas, texto_suave,
                               RotacaoPreview, RelogioAnimacaoWindows)

RAIZ = Path(__file__).resolve().parent
FUNDO = "#e9e9e5"
CARTAO = FUNDO
TEXTO = "#303238"
SECUNDARIO = "#696c73"
PRATA = "#707b89"
MODOS = [
    ("dinamico", "Dinâmico", "Spotify durante a música; vídeo durante a pausa.", PRATA),
    ("spotify", "Spotify", "Só Spotify. O vinil permanece durante a pausa.", "#717b87"),
    ("video", "Vídeo", "Só o vídeo de fundo, sem informações da música.", "#737c87"),
    ("clipe", "Clipe", "Mostra na tela USB o clipe do YouTube da faixa em reprodução.", "#737c87"),
]
SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
UI_FPS = 60
TOPO_TRANSPORTE = 310
TOPO_TITULO = 177
SINCRONIAS = {
    "letras": ("Sincronia das letras", "adiantar_letras_ms", LIMITE_SINCRONIA_LETRAS_MS),
    "video": ("Sincronia do vídeo", "adiantar_clipe_ms", LIMITE_SINCRONIA_CLIPE_MS),
}


def centralizar_janela(janela, largura, altura):
    esquerda = topo = 0
    direita, base = janela.winfo_screenwidth(), janela.winfo_screenheight()
    largura_externa, altura_externa = largura, altura
    if os.name == "nt":
        try:
            user = ctypes.WinDLL("user32", use_last_error=True)
            area = wintypes.RECT()
            if user.SystemParametersInfoW(0x0030, 0, ctypes.byref(area), 0):  # SPI_GETWORKAREA
                esquerda, topo, direita, base = area.left, area.top, area.right, area.bottom
            moldura = wintypes.RECT(0, 0, largura, altura)
            if user.AdjustWindowRectEx(ctypes.byref(moldura), 0x00CF0000, False, 0):  # WS_OVERLAPPEDWINDOW
                largura_externa = moldura.right - moldura.left
                altura_externa = moldura.bottom - moldura.top
        except (OSError, AttributeError):
            pass
    x = max(esquerda, esquerda + (direita - esquerda - largura_externa) // 2)
    y = max(topo, topo + (base - topo - altura_externa) // 2)
    janela.geometry(f"{largura}x{altura}+{x}+{y}")


def proximo_quadro(widget, inicio, callback):
    """Agenda no relógio: desenhar não acrescenta atraso a cada quadro."""
    agora = time.monotonic()
    indice = math.floor((agora - inicio) * UI_FPS) + 1
    espera = math.ceil((inicio + indice / UI_FPS - agora) * 1000)
    return widget.after(max(1, espera), callback)


def precisao_animacao(widget, tipo, ativo):
    relogio = getattr(widget.winfo_toplevel(), "relogio_animacao", None)
    if relogio is not None:
        relogio.marcar((id(widget), tipo), ativo)


def rgb(cor):
    return tuple(int(cor[i:i + 2], 16) for i in (1, 3, 5))


def misturar(a, b, t):
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(rgb(a), rgb(b)))


def cor_album(cor):
    """Mantém o matiz do álbum, com contraste sobre a superfície clara."""
    canais = tuple(max(0, min(255, round(c))) for c in cor[:3])
    ganho = min(1.0, 150 / max(max(canais), 1))
    acento = "#%02x%02x%02x" % tuple(min(255, round(c * ganho)) for c in canais)
    return misturar(acento, "#59616e", 0.18)


def animar_hover(widget, valor):
    """Um timer curto por entrada/saída; não mantém um loop ocioso."""
    if getattr(widget, "hover_job", None):
        widget.after_cancel(widget.hover_job)
    widget.hover_alvo = bool(valor)
    origem = widget.hover_t
    inicio = time.monotonic()
    precisao_animacao(widget, "hover", True)

    def passo():
        t = min(1.0, (time.monotonic() - inicio) / 0.26)
        s = t * t * (3 - 2 * t)
        widget.hover_t = origem + (float(valor) - origem) * s
        widget.desenhar()
        widget.hover_job = proximo_quadro(widget, inicio, passo) if t < 1 else None
        if t >= 1:
            precisao_animacao(widget, "hover", False)
    passo()


def halo(cor, tamanho=(330, 180), intensidade=75):
    img = Image.new("RGBA", tamanho)
    d = ImageDraw.Draw(img)
    d.ellipse((50, 44, tamanho[0] - 50, tamanho[1] - 44), fill=(*rgb(cor), intensidade))
    return img.filter(ImageFilter.GaussianBlur(24))


_BASES_FUNDO = {}
_MASCARA_ACENTO = None


def fundo_janela(cor=PRATA):
    global _MASCARA_ACENTO
    if "solido" not in _BASES_FUNDO:
        img = Image.new("RGBA", (1000, 720), FUNDO)
        img.alpha_composite(superficie((348, 348), "#e4e5e1", raio=174, pressionado=True), (111, 89))
        _BASES_FUNDO["solido"] = img
    if _MASCARA_ACENTO is None:
        mask = Image.new("L", (1000, 720))
        ImageDraw.Draw(mask).ellipse((164, 142, 406, 384), fill=25)
        _MASCARA_ACENTO = mask.filter(ImageFilter.GaussianBlur(37))
    if cor is None:
        return _BASES_FUNDO["solido"].copy()
    acento = Image.new("RGBA", (1000, 720), cor)
    acento.putalpha(_MASCARA_ACENTO)
    return Image.alpha_composite(_BASES_FUNDO["solido"], acento)


def acento_vinil(cor):
    # A transição atualiza apenas esta região, não um bitmap de toda a janela.
    img = Image.new("RGBA", (388, 388), cor)
    img.putalpha(_MASCARA_ACENTO.crop((91, 69, 479, 457)))
    return img


def disco_base():
    # O desenho fixo é suavizado uma vez; a rotação continua trabalhando em 270 px.
    s = 3
    img = Image.new("RGBA", (270 * s, 270 * s))
    d = ImageDraw.Draw(img)
    d.ellipse((8 * s, 8 * s, 262 * s, 262 * s), fill="#141516", outline="#545b64", width=s)
    for r in range(121, 59, -2):
        cor = (26, 27, 29, 255)
        d.ellipse(tuple(n * s for n in (135 - r, 135 - r, 135 + r, 135 + r)), outline=cor, width=s)
    reflexo = Image.new("RGBA", img.size)
    rd = ImageDraw.Draw(reflexo)
    for desloc in range(38):
        alfa = round(30 * (1 - desloc / 38))
        rd.pieslice((14 * s, 14 * s, 256 * s, 256 * s), 210 + desloc, 226 + desloc,
                    fill=(218, 220, 218, alfa))
        rd.pieslice((14 * s, 14 * s, 256 * s, 256 * s), 30 + desloc, 46 + desloc,
                    fill=(218, 220, 218, alfa))
    img = Image.alpha_composite(img, reflexo.filter(ImageFilter.GaussianBlur(3 * s)))
    return img.resize((270, 270), Image.Resampling.LANCZOS)


class Botao(CanvasSuave):
    def __init__(self, master, texto, comando, largura=150, altura=40,
                 primario=False, fundo=FUNDO, simbolo=None, dica="", midia=False,
                 mostrar_ponto=True, mostrar_foco=True, foco_na_face=False):
        super().__init__(master, width=largura, height=altura, bg=fundo,
                         highlightthickness=0, bd=0, takefocus=True, cursor="hand2")
        self.texto, self.comando, self.primario = texto, comando, primario
        self.midia = midia
        self.foco_na_face = foco_na_face
        self.mostrar_ponto, self.mostrar_foco = mostrar_ponto, mostrar_foco
        self.cor = PRATA
        self.simbolo, self.selecionado = simbolo, False
        self.ativo, self.hover = True, False
        self.hover_t, self.hover_job = 0.0, None
        self.fonte = master.f_sans if hasattr(master, "f_sans") else FonteInterface()
        self._ultima_pintura = None
        self.bind("<Button-1>", lambda e: self.acionar())
        self.bind("<Return>", lambda e: self.acionar())
        self.bind("<space>", lambda e: self.acionar())
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<FocusIn>", lambda e: self.desenhar())
        self.bind("<FocusOut>", lambda e: self.desenhar())
        self.dica = Dica(self, dica)
        self.desenhar()

    def _hover(self, valor):
        self.hover = valor
        animar_hover(self, valor)

    def acionar(self):
        if self.ativo:
            self.focus_set()
            self.comando()
        return "break"

    def atualizar(self, texto=None, ativo=None, cor=None, selecionado=None, simbolo=None, dica=None,
                  mostrar_ponto=None):
        if texto is not None:
            self.texto = texto
        if ativo is not None:
            self.ativo = ativo
        if cor is not None:
            self.cor = cor
        if selecionado is not None:
            self.selecionado = selecionado
        if simbolo is not None:
            self.simbolo = simbolo
        if dica is not None:
            self.dica.texto = dica
        if mostrar_ponto is not None:
            self.mostrar_ponto = mostrar_ponto
        chave = (self.texto, self.ativo, self.cor, self.selecionado, self.simbolo,
                 self.primario, self.mostrar_ponto)
        if chave != self._ultima_pintura:
            self.desenhar()

    def desenhar(self):
        w, h = int(self["width"]), int(self["height"])
        preenchimento = (misturar("#33373e", self.cor, 0.28) if self.primario else
                         misturar(FUNDO, self.cor, 0.11) if self.selecionado else
                         misturar(FUNDO, "#ffffff", self.hover_t * 0.25))
        if not self.ativo:
            preenchimento = FUNDO
        # Mantém os itens do Canvas. Mover o mouse não destrói imagens ou letras.
        if not hasattr(self, "face_item"):
            vazio = Image.new("RGBA", (1, 1))
            self.face_item = self.imagem(0, 0, vazio, anchor="nw")
            self.icone_item = self.imagem(w / 2, h / 2, vazio)
            self.rotulo_item = self.create_text(0, h / 2, text="", font=self.fonte, anchor="w")
            self.ponto_item = self.create_oval(w - 22, h / 2 - 2, w - 18, h / 2 + 2,
                                               outline="", state="hidden")
            self.foco_item = self.create_line(w / 2 - 7, h - 12, w / 2 + 7, h - 12,
                                               width=2, capstyle=tk.ROUND, state="hidden")
        com_foco = self.mostrar_foco and self.focus_get() is self
        if self.ativo:
            preenchimento = misturar(preenchimento, "#ffffff", round(self.hover_t * 12) / 12 * 0.12)
            if com_foco and self.foco_na_face:
                preenchimento = misturar(preenchimento, self.cor, 0.14)
        self.alterar_imagem(self.face_item, superficie((w, h), preenchimento,
                                raio=(w / 2 if not self.texto else 16), pressionado=self.selecionado))
        tinta = ("#ffffff" if self.primario else TEXTO) if self.ativo else SECUNDARIO
        if self.midia and self.selecionado and self.ativo and not self.primario:
            tinta = self.cor
        total = self.fonte.measure(self.texto) + (29 if self.simbolo and self.texto else 0)
        x = (w - total) / 2
        if self.simbolo:
            self.coords(self.icone_item, x + 10 if self.texto else w / 2, h / 2)
            self.alterar_imagem(self.icone_item, icone(self.simbolo, tinta, 20))
            self.itemconfigure(self.icone_item, state="normal")
            x += 29
        else:
            self.itemconfigure(self.icone_item, state="hidden")
        if self.texto:
            self.coords(self.rotulo_item, x, h / 2)
        self.itemconfigure(self.rotulo_item, text=self.texto, font=self.fonte, fill=tinta)
        self.itemconfigure(self.ponto_item, fill=self.cor,
                           state="normal" if self.selecionado and self.mostrar_ponto else "hidden")
        if self.midia:
            self.coords(self.ponto_item, w / 2 - 2, h - 11, w / 2 + 2, h - 7)
        self.itemconfigure(self.foco_item, fill=self.cor,
                            state="normal" if com_foco and not self.foco_na_face
                            and not self.selecionado else "hidden")
        self._ultima_pintura = (self.texto, self.ativo, self.cor, self.selecionado, self.simbolo,
                               self.primario, self.mostrar_ponto)


class Seletor(CanvasSuave):
    largura = 912
    margem = 12
    segmento = (largura - 2 * margem) // len(MODOS)
    simbolos = ("onda", "spotify", "video", "play")

    def __init__(self, master, selecionado, comando, fonte, fonte_pequena):
        super().__init__(master, width=self.largura, height=92, bg=FUNDO,
                         highlightthickness=0, takefocus=True, cursor="hand2")
        self.comando, self.fonte, self.fonte_pequena = comando, fonte, fonte_pequena
        self.indice = next(i for i, m in enumerate(MODOS) if m[0] == selecionado)
        self.x = self.margem + self.indice * self.segmento
        self.cor = MODOS[self.indice][3]
        self.hover_indice, self.hover_t, self.hover_job = None, 0.0, None
        self.hover_alvo = False
        self.hover_valores = [0.0] * len(MODOS)
        self.job = None
        self.bind("<Button-1>", self.clicar)
        self.bind("<Motion>", self.mover_mouse)
        self.bind("<Leave>", lambda e: self.animar_realce(None))
        self.bind("<Left>", lambda e: self.escolher(max(0, self.indice - 1)))
        self.bind("<Right>", lambda e: self.escolher(min(len(MODOS) - 1, self.indice + 1)))
        self.bind("<Home>", lambda e: self.escolher(0))
        self.bind("<End>", lambda e: self.escolher(len(MODOS) - 1))
        self.bind("<FocusIn>", lambda e: self.desenhar())
        self.bind("<FocusOut>", lambda e: self.desenhar())
        self.dica = Dica(self, MODOS[self.indice][2])
        self.desenhar()

    def indice_em(self, x):
        return max(0, min(len(MODOS) - 1, (x - self.margem) // self.segmento))

    def clicar(self, e):
        self.focus_set()
        self.escolher(self.indice_em(e.x))

    def mover_mouse(self, e):
        indice = self.indice_em(e.x)
        if indice != self.hover_indice or not self.hover_alvo:
            self.dica.texto = ("Modo retomado ao sair de Gaming." if
                               self.master.config_usuario.get("gaming") else MODOS[indice][2])
            self.dica.agendar()
            self.animar_realce(indice)

    def animar_realce(self, indice):
        if self.hover_job is not None:
            self.after_cancel(self.hover_job)
        self.hover_indice, self.hover_alvo = indice, indice is not None
        origem = list(self.hover_valores)
        inicio = time.monotonic()
        precisao_animacao(self, "hover", True)

        def passo():
            t = min(1.0, (time.monotonic() - inicio) / 0.26)
            s = t * t * (3 - 2 * t)
            self.hover_valores = [v + (float(i == indice) - v) * s for i, v in enumerate(origem)]
            self.desenhar()
            self.hover_job = proximo_quadro(self, inicio, passo) if t < 1 else None
            if t >= 1:
                precisao_animacao(self, "hover", False)
        passo()

    def atualizar_cor(self, cor):
        if cor != self.cor:
            self.cor = cor
            self.desenhar()

    def escolher(self, indice):
        if indice == self.indice:
            return "break"
        if self.job is not None:
            self.after_cancel(self.job)
            self.job = None
        origem_x = self.x
        self.indice = indice
        inicio, destino = time.monotonic(), self.margem + indice * self.segmento
        precisao_animacao(self, "selecao", True)
        self.comando(MODOS[indice][0])

        def passo():
            t = min(1.0, (time.monotonic() - inicio) / 0.38)
            s = t * t * (3 - 2 * t)
            self.x = origem_x + (destino - origem_x) * s
            self.desenhar()
            self.job = proximo_quadro(self, inicio, passo) if t < 1 else None
            if t >= 1:
                precisao_animacao(self, "selecao", False)
        passo()
        return "break"

    def desenhar(self):
        if not hasattr(self, "face_item"):
            self.imagem(0, 0, superficie((self.largura, 92), FUNDO, raio=30, pressionado=True), anchor="nw")
            self.face_item = self.imagem(self.x, 5, superficie((self.segmento, 82), raio=24), anchor="nw")
            self.rotulos, self.icones = [], []
            for i, (_, titulo, _, _) in enumerate(MODOS):
                total = self.fonte.measure(titulo) + 38
                x = self.margem + (i + 0.5) * self.segmento - total / 2
                self.icones.append(self.imagem(x + 12, 46, icone(self.simbolos[i], SECUNDARIO)))
                self.rotulos.append(self.create_text(x + 38, 46, text=titulo, anchor="w",
                                                      font=self.fonte, fill=SECUNDARIO))
            self.foco_item = self.create_oval(0, 72, 6, 75, outline="", state="hidden")
        face = misturar("#f8f8f5", self.cor, 0.035)
        self.coords(self.face_item, self.x, 5)
        self.alterar_imagem(self.face_item, superficie((self.segmento, 82), face, raio=24))
        for i, (_, titulo, _, _) in enumerate(MODOS):
            selecionado = i == self.indice
            sobre = self.hover_valores[i]
            tinta = self.cor if selecionado else SECUNDARIO
            self.alterar_imagem(self.icones[i], icone(self.simbolos[i], tinta, 24))
            self.itemconfigure(self.rotulos[i], fill=misturar(TEXTO if selecionado else SECUNDARIO, self.cor, sobre))
        centro = self.margem + (self.indice + 0.5) * self.segmento
        self.coords(self.foco_item, centro - 3, 72, centro + 3, 75)
        self.itemconfigure(self.foco_item, fill=self.cor, state="normal" if self.focus_get() is self else "hidden")


class Slider(CanvasSuave):
    def __init__(self, master, valor, comando, largura=620, fundo=CARTAO,
                 minimo=0, maximo=100, passo=1, altura=46, compacto=False):
        super().__init__(master, width=largura, height=altura, bg=fundo,
                         highlightthickness=0, takefocus=True, cursor="hand2")
        self.altura = altura
        self.compacto = compacto
        self.margem = 9 if compacto else altura // 2 + 1
        self.percurso = largura - 2 * self.margem
        self.minimo, self.maximo = minimo, maximo
        self.passo = passo
        self.valor, self.comando = valor, comando
        self.cor, self.job = PRATA, None
        self.hover_t, self.hover_job = 0.0, None
        self.bind("<Enter>", lambda e: animar_hover(self, True))
        self.bind("<Leave>", lambda e: animar_hover(self, False))
        self.bind("<Button-1>", self.arrastar)
        self.bind("<B1-Motion>", self.arrastar)
        self.bind("<ButtonRelease-1>", self.soltar)
        self.bind("<Left>", lambda e: self.tecla(-1))
        self.bind("<Right>", lambda e: self.tecla(1))
        self.bind("<Prior>", lambda e: self.tecla(10))
        self.bind("<Next>", lambda e: self.tecla(-10))
        self.bind("<Home>", lambda e: self.tecla(self.minimo - self.maximo))
        self.bind("<End>", lambda e: self.tecla(self.maximo - self.minimo))
        self.bind("<FocusIn>", lambda e: self.desenhar())
        self.bind("<FocusOut>", lambda e: self.desenhar())
        self.desenhar()

    def arrastar(self, e):
        if self["state"] == "disabled":
            return "break"
        self.focus_set()
        t = max(0, min(1, (e.x - self.margem) / self.percurso))
        unidades = round(t * (self.maximo - self.minimo) / self.passo)
        self.valor = min(self.maximo, self.minimo + unidades * self.passo)
        self.desenhar()
        self.comando(self.valor, False)

    def soltar(self, e):
        if self["state"] != "disabled":
            if self.job is not None:
                self.after_cancel(self.job)
                self.job = None
            self.comando(self.valor, True)

    def tecla(self, incremento):
        if self["state"] == "disabled":
            return "break"
        self.valor = max(self.minimo, min(self.maximo, self.valor + incremento * self.passo))
        self.desenhar()
        self.comando(self.valor, False)
        if self.job:
            self.after_cancel(self.job)
        self.job = self.after(250, lambda: self.soltar(None))
        return "break"

    def atualizar(self, valor=None, cor=None, limites=None):
        anterior = (self.valor, self.cor, self.minimo, self.maximo)
        if valor is not None:
            self.valor = valor
        if cor:
            self.cor = cor
        if limites is not None:
            self.minimo, self.maximo = limites
        if (self.valor, self.cor, self.minimo, self.maximo) != anterior:
            self._trilho_chave = None
            self.desenhar()

    def desenhar(self):
        x = self.margem + (self.valor - self.minimo) / (self.maximo - self.minimo) * self.percurso
        w = int(self["width"])
        centro = self.altura // 2
        raio_botao = 7 if self.compacto else centro - 1
        y_botao = centro - raio_botao
        if not hasattr(self, "trilho_item"):
            vazio = Image.new("RGBA", (1, 1))
            self.trilho_item = self.imagem(0, 0, vazio, anchor="nw")
            lado = 2 * raio_botao
            if self.compacto:
                botao = Image.new("RGBA", (lado * 3, lado * 3))
                ImageDraw.Draw(botao).ellipse((1, 1, lado * 3 - 2, lado * 3 - 2), fill="#fbfbf8")
                botao = botao.resize((lado, lado), Image.Resampling.LANCZOS)
            else:
                botao = superficie((lado, lado), "#fbfbf8", raio=raio_botao)
            self.botao_item = self.imagem(x - raio_botao, y_botao, botao, anchor="nw")
            self.foco_item = self.create_oval(0, 0, 1, 1, outline=SECUNDARIO, state="hidden")
            self.ponto_item = self.create_oval(0, 0, 1, 1, outline="")
            self._trilho_chave = None
        raio = 3 + self.hover_t
        self.coords(self.ponto_item, x - raio, centro - raio, x + raio, centro + raio)
        raio_foco = raio_botao - 3
        self.coords(self.foco_item, x - raio_foco, centro - raio_foco, x + raio_foco, centro + raio_foco)
        self.itemconfigure(self.foco_item, state="normal" if self.focus_get() is self else "hidden")
        chave = (self.valor, self.cor)
        if chave == self._trilho_chave:
            return
        # O trilho e o botão usam a mesma escala de suavização dos ícones.
        escala = 3
        img = Image.new("RGBA", (w * escala, self.altura * escala))
        d = ImageDraw.Draw(img)
        a, b, y = self.margem * escala, (self.margem + self.percurso) * escala, centro * escala
        meio_trilho = (2 if self.compacto else 4) * escala
        d.rounded_rectangle((a, y - meio_trilho, b, y + meio_trilho), radius=meio_trilho,
                            fill="#d0d2d0")
        if not self.compacto:
            d.line((a + 3 * escala, y - 3 * escala, b - 3 * escala, y - 3 * escala),
                   fill="#bdc1c1", width=escala)
            d.line((a + 3 * escala, y + 4 * escala, b - 3 * escala, y + 4 * escala),
                   fill="#fafaf7", width=escala)
        origem = a
        if self.minimo < 0 < self.maximo:
            origem += -self.minimo / (self.maximo - self.minimo) * self.percurso * escala
        if x * escala != origem:
            inicio, fim = sorted((origem, x * escala))
            meio_ativo = min(meio_trilho, 3 * escala)
            d.rounded_rectangle((inicio, y - meio_ativo, fim, y + meio_ativo),
                                radius=meio_ativo, fill=misturar("#828995", self.cor, 0.65))
        if origem != a and not self.compacto:
            d.line((origem, y - 6 * escala, origem, y + 6 * escala), fill="#959ba1", width=escala)
        self.alterar_imagem(self.trilho_item, img.resize((w, self.altura), Image.Resampling.LANCZOS))
        self.coords(self.botao_item, x - raio_botao, y_botao)
        self.itemconfigure(self.ponto_item, fill=self.cor)
        self._trilho_chave = chave


class PainelFundo(CanvasSuave):
    def __init__(self, master):
        super().__init__(master, width=452, height=350, bg=CARTAO,
                         highlightthickness=0, bd=0)
        self.janela, self.f_sans = master, master.f_sans
        self.definir_fundo(superficie((452, 350), "#f1f1ed", raio=22))
        self.create_text(24, 24, text="Fundo", anchor="nw", font=master.f_modo, fill=TEXTO)
        self.fechar = Botao(self, "", master.alternar_painel_fundo, simbolo="fechar",
                            largura=40, altura=40, fundo=CARTAO, dica="Fechar")
        self.create_window(395, 11, window=self.fechar, anchor="nw")
        self.nome = self.create_text(24, 64, anchor="nw", font=master.f_sans, fill=SECUNDARIO)
        self.escolher = Botao(self, "Escolher vídeo", master.escolher_fundo,
                              largura=238, altura=48, primario=True, fundo=CARTAO)
        self.create_window(16, 98, window=self.escolher, anchor="nw")
        self.padrao = Botao(self, "Padrão", master.acao_fundo,
                            largura=162, altura=48, fundo=CARTAO)
        self.create_window(264, 98, window=self.padrao, anchor="nw")
        self.ajustar = Botao(self, "Ajustar vídeo", master.ajustar_fundo,
                             largura=190, altura=44, fundo=CARTAO,
                             dica="Prévia, posição, zoom e ajustes da imagem")
        self.create_window(16, 150, window=self.ajustar, anchor="nw")
        self.create_text(225, 172, text="Posição, zoom e imagem", font=master.f_pequena,
                         anchor="w", fill=SECUNDARIO)
        self.percentual = self.create_text(428, 204, anchor="ne", font=master.f_pequena,
                                           fill=SECUNDARIO)
        self.barra = self.create_line(25, 226, 428, 226, fill="#d0d2d0", width=5,
                                      capstyle=tk.ROUND)
        self.preenchimento = self.create_line(25, 226, 25, 226, width=5, capstyle=tk.ROUND)
        self.status = self.create_text(24, 245, anchor="nw", font=master.f_pequena,
                                       fill=SECUNDARIO, width=405)
        self.cor_leds = Botao(self, "Cor dos LEDs", master.abrir_cor_fundo,
                              largura=190, altura=44, fundo=CARTAO,
                              dica="Escolher a iluminação do vídeo de fundo")
        self.create_window(16, 295, window=self.cor_leds, anchor="nw")
        self.cor_amostra = self.create_oval(234, 311, 248, 325, outline="")
        self.cor_texto = self.create_text(260, 318, anchor="w", font=master.f_pequena,
                                        fill=SECUNDARIO)

    def atualizar(self):
        janela = self.janela
        ocupado = janela.fundo_convertendo
        nome = (janela.fundo_nome_em_preparo if ocupado else
                janela.config_usuario["video_ocioso_nome"] or "Fundo padrão")
        self.itemconfigure(self.nome, text=janela.encurtar(nome, 394, janela.f_sans))
        self.escolher.atualizar(texto="Preparando…" if ocupado else "Escolher vídeo",
                                ativo=not ocupado, cor=janela.cor_modo)
        self.padrao.atualizar(texto="Cancelar" if ocupado else "Padrão",
                              ativo=ocupado or janela.config_usuario["video_ocioso"] is not None,
                              cor=janela.cor_modo)
        self.fechar.atualizar(cor=janela.cor_modo)
        self.cor_leds.atualizar(cor=janela.cor_modo)
        self.ajustar.atualizar(ativo=not ocupado, cor=janela.cor_modo)
        cor = janela.config_usuario.get("cor_fundo")
        hexa = "#%02X%02X%02X" % tuple(cor) if cor is not None else None
        self.itemconfigure(self.cor_amostra, fill=hexa or janela.cor_modo)
        self.itemconfigure(self.cor_texto, text=hexa or "Perfil original")
        progresso = janela.fundo_progresso
        valor = 0 if progresso is None else progresso
        self.coords(self.preenchimento, 25, 226, 25 + 403 * valor / 100, 226)
        self.itemconfigure(self.preenchimento, fill=janela.cor_modo,
                            state="normal" if valor > 0 else "hidden")
        self.itemconfigure(self.percentual, text=f"{valor}%" if ocupado and progresso is not None else "")
        texto, cor = janela.fundo_status
        self.itemconfigure(self.status, text=texto if len(texto) <= 140 else texto[:137] + "…", fill=cor)


class Motor:
    """A janela nunca acessa o hardware; acompanha somente o processo que iniciou."""
    def __init__(self):
        self.proc = None
        self.eventos = queue.Queue(maxsize=24)
        self.erros = deque(maxlen=12)
        self.lock = threading.Lock()
        self._config_pendente = None
        self.iniciando = False

    def evento(self, tipo, dados):
        try:
            self.eventos.put_nowait((tipo, dados))
        except queue.Full:
            try:
                self.eventos.get_nowait()
            except queue.Empty:
                pass
            self.eventos.put_nowait((tipo, dados))

    def iniciar(self, config):
        if self.iniciando or (self.proc and self.proc.poll() is None):
            return
        self.iniciando = True
        with self.lock:
            self._config_pendente = dict(config)

        def executar():
            try:
                # Detecta também scripts anteriores que ainda não usam o mutex.
                if os.name == "nt":
                    consulta = ("Get-CimInstance Win32_Process -Filter \"Name = 'python.exe' OR Name = 'pythonw.exe'\" "
                                "| Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
                    resultado = subprocess.run(["powershell", "-NoProfile", "-Command", consulta],
                                               capture_output=True, text=True, timeout=12,
                                               creationflags=SEM_JANELA)
                    if resultado.returncode != 0:
                        raise RuntimeError("Não foi possível conferir se a tela já está em uso.")
                    processos = json.loads(resultado.stdout or "[]")
                    if isinstance(processos, dict):
                        processos = [processos]
                    if any("tela_completa.py" in (p.get("CommandLine") or "").lower() for p in (processos or [])):
                        raise RuntimeError("O script já está aberto no terminal. Encerre-o com Ctrl+C antes de iniciar aqui.")
                self.erros.clear()
                ambiente = dict(os.environ, PYTHONIOENCODING="utf-8")
                python = Path(sys.executable)
                if python.name.lower() == "pythonw.exe":
                    python = python.with_name("python.exe")
                self.proc = subprocess.Popen(
                    [str(python), "-u", str(RAIZ / "tela_completa.py"), "--interface"],
                    cwd=RAIZ, env=ambiente, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                    creationflags=SEM_JANELA)
                self.enviar({"acao": "configurar"})
                self.evento("iniciado", None)
                for linha in self.proc.stdout:
                    pos = linha.find(PREFIXO)
                    if pos >= 0:
                        try:
                            self.evento("estado", json.loads(linha[pos + len(PREFIXO):]))
                        except ValueError:
                            pass
                    elif any(palavra in linha.lower() for palavra in ("error", "erro", "exception", "traceback")):
                        self.erros.append(linha.strip())
                codigo = self.proc.wait()
                self.evento("parado", "\n".join(self.erros) if codigo else "")
            except Exception as exc:
                self.evento("erro", str(exc))
            finally:
                self.iniciando = False
        threading.Thread(target=executar, name="JanelaMotor", daemon=True).start()

    def enviar(self, dados):
        with self.lock:
            if dados.get("acao") == "configurar":
                if isinstance(dados.get("config"), dict):
                    self._config_pendente = dict(dados["config"])
                dados = {"acao": "configurar", "config": self._config_pendente}
            if self.proc is not None and self.proc.poll() is None:
                try:
                    self.proc.stdin.write(json.dumps(dados, ensure_ascii=True) + "\n")
                    self.proc.stdin.flush()
                except (OSError, ValueError):
                    pass

    def parar(self):
        self.enviar({"acao": "parar"})


class Janela(tk.Tk):
    def __init__(self):
        configurar_dpi()
        super().__init__()
        self.relogio_animacao = RelogioAnimacaoWindows()
        self.title("Turing Vinyl")
        self.minsize(1000, 720)
        centralizar_janela(self, 1000, 720)
        self.resizable(True, True)
        self.configure(bg=FUNDO)
        self.img_icone_header = None
        self.carregar_icone()
        self.protocol("WM_DELETE_WINDOW", self.fechar)
        self.config_usuario = carregar()
        self._arraste_brilho_ativo = False
        self._brilho_antes_arraste = self.config_usuario["brilho"]
        self._sincronia_aberta = None
        self._sincronia_pendente = False
        self.moldura_windows = None
        self.controles_spotify = ControlesSpotify()
        self.estado_controles_midia = {}
        self.midia_ocupada = False
        self.conversor_fundo = ConversorFundo()
        self.fundo_painel_aberto, self.fundo_convertendo = False, False
        self.lotus_painel = None
        self.cor_fundo_painel = None
        self.editor_fundo = None
        self.fundo_nome_em_preparo = ""
        self.fundo_progresso, self.fundo_pendente = 0, False
        self.fundo_status = ("Escolha um vídeo. A preparação acontece aqui no app.", SECUNDARIO)
        self.motor = Motor()
        self.encerrando, self.iniciando, self.rodando = False, False, False
        self.config_job = None
        self.midia, self.capa = None, None
        self._chave_texto_midia = None
        self._posicao_controles_midia = TOPO_TRANSPORTE
        self.capa_preview = None
        self.capa_base = disco_base()
        self.rotacao_preview = RotacaoPreview(self.capa_base)
        self._apresentar_job = None
        self._disco_pil = None
        self._fundo_preview_chave = None
        self._fundo_preview = None
        self.cor_modo = PRATA if self.config_usuario.get("gaming") else next(
            m[3] for m in MODOS if m[0] == self.config_usuario["modo"])
        self.cor_destino, self.tema_job = self.cor_modo, None
        self.disco_job = None
        self._visual = {}
        self._angulo_gui, self._escala_gui = 0.0, 1.0
        self._angulo_externo = 0.0
        self._velocidade_gui = self._velocidade_externa_gui = 0.0
        self._tempo_preview = time.monotonic()
        self._relogio_preview = self._tempo_preview
        self._preview_chave = None
        self.f_sans = FonteInterface(14, 500)
        self.f_pequena = FonteInterface(12, 450)
        self.f_modo = FonteInterface(16, 550)
        self.f_logo = FonteInterface(25, 550, serif=True)
        self.f_faixa = FonteInterface(32, 500, serif=True)
        self.f_valor = FonteInterface(17, 550)
        self.canvas = CanvasSuave(self, width=1000, height=720, bg=FUNDO, bd=0, highlightthickness=0)
        self.canvas.pack(expand=True)
        self.fundo_atual = fundo_janela(None)
        self.img_fundo = ImageTk.PhotoImage(self.fundo_atual)
        self.fundo_item = self.canvas.create_image(0, 0, image=self.img_fundo, anchor="nw")
        self.img_acento = ImageTk.PhotoImage(acento_vinil(self.cor_modo))
        self.acento_item = self.canvas.create_image(91, 69, image=self.img_acento, anchor="nw")
        self.desenhar()
        self.after_idle(self.preparar_moldura)
        self.after(50, self.eventos)
        self.bind("<Escape>", lambda e: self.fechar_paineis())

    def texto(self, x, y, texto, fonte=None, cor=TEXTO, **opcoes):
        return self.canvas.create_text(x, y, text=texto, font=fonte or self.f_sans,
                                       fill=cor, anchor="nw", **opcoes)

    def carregar_icone(self):
        """PNG no cabeçalho e ícone de várias resoluções na janela do Windows."""
        try:
            with Image.open(RAIZ / "assets" / "icons" / "turing-vinyl.png") as arquivo:
                imagem = arquivo.convert("RGBA")
                self.img_icone_app = ImageTk.PhotoImage(
                    imagem.resize((256, 256), Image.Resampling.LANCZOS))
                self.img_icone_header = ImageTk.PhotoImage(
                    imagem.resize((40, 40), Image.Resampling.LANCZOS))
            self.iconphoto(True, self.img_icone_app)
            if os.name == "nt":
                self.iconbitmap(str(RAIZ / "assets" / "icons" / "turing-vinyl.ico"))
        except (OSError, tk.TclError):
            # O controle da tela continua disponível se o recurso não for copiado.
            pass

    def preparar_moldura(self):
        if not self.encerrando:
            self.moldura_windows = preparar_janela(
                self, FUNDO, TEXTO, RAIZ / "assets" / "icons" / "turing-vinyl.ico",
                RAIZ / "iniciar_interface.pyw")

    def desenhar(self):
        c = self.canvas
        if self.img_icone_header is not None:
            c.create_image(64, 42, image=self.img_icone_header)
        c.create_text(100, 42, text="Turing Vinyl", font=self.f_logo, fill=TEXTO, anchor="w")
        self.status_ponto = c.create_oval(102, 67, 108, 73, fill=SECUNDARIO, outline="")
        self.status_texto = c.create_text(116, 70, text="Pronto", font=self.f_pequena,
                                          fill=SECUNDARIO, anchor="w")
        self.fundo_botao = Botao(self, "Fundo", self.alternar_painel_fundo,
                                 largura=130, altura=48, simbolo="video", dica="Escolher o vídeo de fundo")
        c.create_window(822, 19, window=self.fundo_botao, anchor="nw")

        self.disco_item = c.create_image(285, 263)
        self.hero_rotulo = self.texto(529, 146, "", self.f_pequena, SECUNDARIO)
        self.faixa_texto = self.texto(526, TOPO_TITULO, "Esperando por você…", self.f_faixa, width=408)
        self.artista_texto = self.texto(529, 304, "", self.f_sans, SECUNDARIO)
        self.playlist_texto = self.texto(529, 333, "", self.f_pequena, SECUNDARIO)
        self.midia_botoes = {}
        for acao, simbolo, dica, centro, lado in (
                ("aleatorio", "aleatorio", "Aleatório", 604, 48),
                ("anterior", "anterior", "Música anterior", 668, 48),
                ("play_pause", "play", "Reproduzir", 732, 60),
                ("proxima", "proxima", "Próxima música", 796, 48),
                ("repetir", "repetir", "Repetição desligada", 860, 48)):
            botao = Botao(self, "", lambda a=acao: self.acao_midia(a),
                           largura=lado, altura=lado, primario=acao == "play_pause",
                           simbolo=simbolo, dica=dica, midia=True)
            botao.atualizar(ativo=False)
            self.midia_botoes[acao] = botao
            c.create_window(centro - lado / 2, TOPO_TRANSPORTE + (60 - lado) / 2, window=botao,
                            anchor="nw", tags=("controles_midia",))
        x_letras = 467
        largura_letras = 100
        intervalo_botoes = 6
        x_sincronia = x_letras + largura_letras + intervalo_botoes
        y_letras = TOPO_TRANSPORTE + 64
        altura_botoes = 44
        largura_sincronia = 184
        y_ajuste = y_letras + altura_botoes + 1
        tags_letras = ("controles_midia", "letras")
        self.letras_botao = Botao(self, "Letras", self.alternar_letras, largura=largura_letras,
                                 altura=altura_botoes,
                                 foco_na_face=True)
        c.create_window(x_letras, y_letras, window=self.letras_botao, anchor="nw",
                        state="hidden", tags=tags_letras)
        self.letras_status = self.texto(x_letras + 8, y_ajuste + 4, "", self.f_pequena, SECUNDARIO,
                                        tags=tags_letras)
        self.sincronia_botoes = {}
        for canal, x in (("letras", x_sincronia), ("video", x_sincronia + largura_sincronia + intervalo_botoes)):
            nome, _, _ = SINCRONIAS[canal]
            botao = Botao(self, nome, lambda tipo=canal: self.alternar_sincronia(tipo),
                          largura=largura_sincronia, altura=altura_botoes, foco_na_face=True,
                          mostrar_ponto=False)
            self.sincronia_botoes[canal] = botao
            c.create_window(x, y_letras, window=botao, anchor="nw", state="hidden",
                            tags=("controles_midia", f"sincronia_{canal}"))
        tags_slider = ("controles_midia", "sincronia_ajuste")
        self.sincronia_valor = c.create_text(939, y_ajuste + 12, text="", font=self.f_pequena,
                                            fill=TEXTO, anchor="e", state="hidden", tags=tags_slider)
        limite = LIMITE_SINCRONIA_LETRAS_MS
        self.sincronia_slider = Slider(self, 0, self.mudar_sincronia, largura=238, altura=24,
                                       minimo=-limite, maximo=limite, passo=25, compacto=True)
        self.sincronia_slider.dica = Dica(self.sincronia_slider,
                                         "Valores positivos adiantam. Negativos atrasam.")
        c.create_window(609, y_ajuste, window=self.sincronia_slider,
                        anchor="nw", state="hidden", tags=tags_slider)

        self.atualizar_disco()
        self.seletor = Seletor(self, self.config_usuario["modo"], self.mudar_modo, self.f_modo, self.f_pequena)
        c.create_window(44, 466, window=self.seletor, anchor="nw")
        self.modo_dica = c.create_text(934, 464, text="", font=self.f_pequena, fill=SECUNDARIO, anchor="se")

        self.texto(64, 577, "Brilho", self.f_sans)
        self.valor_texto = self.texto(712, 577, "", self.f_valor)
        c.itemconfigure(self.valor_texto, anchor="ne")
        self.slider = Slider(self, self.valor_brilho(), self.mudar_brilho, largura=694, fundo=FUNDO)
        c.create_window(42, 598, window=self.slider, anchor="nw")
        self.slider.dica = Dica(self.slider, "Ajustar brilho. 0% apaga a tela e os LEDs.")
        self.auto = Botao(self, "Auto", self.automatico, largura=94, altura=48,
                          dica="Brilho automático por horário")
        c.create_window(750, 594, window=self.auto, anchor="nw")
        self.power = Botao(self, "", self.alternar_tela, largura=48, altura=48,
                           simbolo="power", dica="Apagar tela e LEDs",
                           mostrar_ponto=False, mostrar_foco=False)
        c.create_window(904, 594, window=self.power, anchor="nw")
        self.brilho_dica = self.texto(64, 646, "", self.f_pequena, SECUNDARIO)

        self.iniciar = Botao(self, "Iniciar", self.alternar_motor, largura=150, altura=48,
                             primario=True, simbolo="play", dica="Iniciar exibição")
        c.create_window(802, 656, window=self.iniciar, anchor="nw")
        self.gaming_botao = Botao(self, "Gaming", self.alternar_gaming, largura=154,
                                  altura=48, simbolo="gaming", dica="Usar só o fundo e reduzir o processamento")
        c.create_window(636, 656, window=self.gaming_botao, anchor="nw")
        self.lotus_botao = Botao(self, "Fita RGB", self.abrir_lotus, largura=150,
                                 altura=48, dica="Cor, brilho e conexão da fita Bluetooth")
        c.create_window(44, 656, window=self.lotus_botao, anchor="nw")
        self.lotus_botao.bind("<Button-3>", lambda e: self.escolher_lotus())
        self.aviso = self.texto(210, 673, "", self.f_pequena, SECUNDARIO, width=402)
        self.fundo_painel = PainelFundo(self)
        self.fundo_painel_item = c.create_window(504, 77, window=self.fundo_painel,
                                                 anchor="nw", state="hidden")
        self.atualizar_controles()
        self.fundo_painel.atualizar()

    def escolher_lotus(self):
        parent = self.lotus_painel if self.lotus_painel is not None and self.lotus_painel.visivel else self
        pasta = filedialog.askdirectory(parent=parent, title="Pasta do Lotus Lantern para Windows",
                                         initialdir=self.config_usuario.get("lotus_pasta") or str(Path.home()))
        if not pasta:
            return False
        if not pasta_valida(pasta):
            self.mensagem("Escolha a pasta com config.cmd e src/ble-device.cjs.", "#966022")
            return False
        self.config_usuario["lotus_pasta"] = pasta
        self.config_usuario["lotus_endereco"] = None
        self.guardar()
        return True

    def abrir_lotus(self):
        if self.lotus_painel is None:
            self.lotus_painel = PainelLotus(self, Botao, Slider)
        if self.lotus_painel.visivel:
            self.lotus_painel.fechar()
        else:
            self.lotus_painel.abrir()

    def abrir_cor_fundo(self):
        if self.cor_fundo_painel is None:
            self.cor_fundo_painel = PainelCorFundo(self, Botao, Slider)
        if self.cor_fundo_painel.visivel:
            self.cor_fundo_painel.fechar()
        else:
            self.cor_fundo_painel.abrir()

    def alternar_lotus(self):
        ativa = not self.config_usuario.get("lotus_ativo", False)
        if ativa and not pasta_valida(self.config_usuario.get("lotus_pasta")):
            if not self.escolher_lotus():
                return
        self.config_usuario["lotus_ativo"] = ativa
        self.atualizar_controles()
        self.guardar()
        if self.lotus_painel is not None:
            self.lotus_painel.atualizar()
        self.mensagem("Fita RGB conecta ao iniciar a exibição." if ativa and not self.rodando else "")

    def alternar_painel_fundo(self):
        self.fundo_painel_aberto = not self.fundo_painel_aberto
        self.fundo_painel.atualizar()
        self.canvas.itemconfigure(self.fundo_painel_item,
                                   state="normal" if self.fundo_painel_aberto else "hidden")
        if self.fundo_painel_aberto:
            self.canvas.tag_raise(self.fundo_painel_item)

    def escolher_fundo(self):
        if self.fundo_convertendo or self.conversor_fundo.ocupado:
            return
        caminho = filedialog.askopenfilename(
            parent=self, title="Escolher vídeo de fundo",
            filetypes=[("Vídeos", "*.mp4 *.mkv *.mov *.webm *.avi *.m4v *.wmv *.gif"),
                       ("Todos os arquivos", "*.*")])
        if not caminho or self.encerrando:
            return
        self.abrir_editor_fundo(caminho)

    def ajustar_fundo(self):
        if self.fundo_convertendo:
            return
        config = self.config_usuario
        origem = config.get("video_origem") or config["video_ocioso"] or str(RAIZ / "video_fundo.mp4")
        self.abrir_editor_fundo(origem, config.get("video_ajustes"))

    def abrir_editor_fundo(self, caminho, ajustes=None):
        if self.editor_fundo is None:
            self.editor_fundo = EditorFundo(self, Botao, Slider)
        self.editor_fundo.abrir(caminho, ajustes)

    def preparar_fundo(self, caminho, ajustes):
        if self.fundo_convertendo or self.conversor_fundo.ocupado:
            self.mensagem("Aguarde a preparação do fundo atual.", "#966022")
            return False
        if self.conversor_fundo.iniciar(caminho, ajustes):
            self.fundo_convertendo = True
            self.fundo_nome_em_preparo = Path(caminho).name
            self.fundo_progresso = 0
            self.fundo_status = ("Abrindo o vídeo…", SECUNDARIO)
            self.fundo_botao.atualizar(texto="Preparando…")
            self.fundo_painel.atualizar()
            return True
        return False

    def acao_fundo(self):
        if self.fundo_convertendo:
            self.conversor_fundo.cancelar()
            self.fundo_status = ("Cancelando a preparação…", SECUNDARIO)
            self.fundo_painel.atualizar()
        else:
            self.definir_fundo(None, "")

    def definir_fundo(self, caminho, nome, origem=None, ajustes=None):
        anterior = dict(self.config_usuario)
        self.config_usuario.update(video_ocioso=caminho, video_ocioso_nome=nome,
                                   video_origem=origem, video_ajustes=ajustes)
        try:
            salvar(self.config_usuario)
        except OSError:
            self.config_usuario = anterior
            self.fundo_status = ("Não foi possível salvar o novo fundo. O anterior foi mantido.", "#966022")
            self.fundo_painel.atualizar()
            return
        self.motor.enviar({"acao": "configurar", "config": self.config_usuario})
        self.fundo_progresso = 100 if caminho else 0
        self.fundo_pendente = self.rodando or self.iniciando
        texto = ("Fundo pronto. Será usado no modo ocioso." if self.fundo_pendente else
                 "Fundo salvo. Ele será usado ao iniciar a exibição.")
        self.fundo_status = (texto, SECUNDARIO)
        self.fundo_painel.atualizar()
        self.mensagem("Novo fundo salvo." if caminho else "Fundo padrão selecionado.")

    def eventos_fundo(self):
        try:
            while True:
                tipo, dados = self.conversor_fundo.eventos.get_nowait()
                if tipo == "progresso":
                    self.fundo_progresso, texto = dados
                    self.fundo_status = (texto, SECUNDARIO)
                else:
                    self.fundo_convertendo = False
                    self.fundo_botao.atualizar(texto="Fundo")
                    if self.conversor_fundo.cancelamento.is_set():
                        tipo = "cancelado"
                    if tipo == "pronto":
                        self.definir_fundo(dados["caminho"], dados["nome"],
                                           dados.get("origem"), dados.get("ajustes"))
                    elif tipo == "cancelado":
                        self.fundo_progresso = 0
                        self.fundo_status = ("Preparação cancelada. O fundo anterior foi mantido.", SECUNDARIO)
                    elif tipo == "erro":
                        self.fundo_progresso = 0
                        self.fundo_status = (dados, "#966022")
                self.fundo_painel.atualizar()
        except queue.Empty:
            pass

    def fechar_paineis(self):
        if self._sincronia_aberta is not None:
            self.alternar_sincronia(self._sincronia_aberta)
        if self.editor_fundo is not None and self.editor_fundo.visivel:
            self.editor_fundo.fechar()
        if self.cor_fundo_painel is not None and self.cor_fundo_painel.visivel:
            self.cor_fundo_painel.fechar()
        if self.lotus_painel is not None and self.lotus_painel.visivel:
            self.lotus_painel.fechar()
        if self.fundo_painel_aberto:
            self.alternar_painel_fundo()

    def valor_brilho(self):
        if not self.config_usuario["tela_ligada"]:
            return 0
        return self.config_usuario["brilho"] or brilho_horario()

    def atualizar_disco(self):
        capa_visivel = (self.capa_preview if self.config_usuario["modo"] != "video"
                        and not self.config_usuario.get("gaming") else None)
        lado = max(1, round(114 * self._escala_gui))
        chave = (id(capa_visivel), round(self._angulo_gui, 3),
                 round(self._angulo_externo, 3), lado,
                 self.cor_modo if capa_visivel is None else None)
        if chave == self._preview_chave:
            return
        self._preview_chave = chave
        self.rotacao_preview.solicitar(chave, capa_visivel, self._angulo_gui,
                                       self._angulo_externo, lado,
                                       misturar("#3b424d", self.cor_modo, 0.22))
        if self._apresentar_job is None:
            self._apresentar_job = self.after(8, self.mostrar_disco_pronto)

    def mostrar_disco_pronto(self):
        self._apresentar_job = None
        if self.encerrando:
            return
        resultado = self.rotacao_preview.receber()
        if resultado is not None:
            chave, disco = resultado
            # Descarta uma capa anterior se o modo ou a música mudou durante o cálculo.
            if (chave[0] == self._preview_chave[0]
                    and chave[4] == self._preview_chave[4]):
                self.apresentar_disco(disco)
        if self.rotacao_preview.pendente():
            self._apresentar_job = self.after(8, self.mostrar_disco_pronto)

    def apresentar_disco(self, disco):
        self._disco_pil = disco
        chave_fundo = (id(self.fundo_atual), self.cor_modo)
        if chave_fundo != self._fundo_preview_chave:
            # O disco de 270 px está centrado em (285,263) no Canvas.
            # Reproduz sob ele as mesmas duas camadas que a janela desenha:
            # fundo fixo e acento colocado em (91,69).
            base = self.fundo_atual.crop((150, 128, 420, 398))
            acento = acento_vinil(self.cor_modo).crop((59, 59, 329, 329))
            self._fundo_preview = Image.alpha_composite(base, acento)
            self._fundo_preview_chave = chave_fundo
        # Transferir RGBA pelo PhotoImage.paste custa mais que RGB no Tk.
        # Compor este pequeno trecho no Pillow permite reutilizar buffers
        # RGB opacos, preservando o fundo e o antialias das bordas do disco.
        imagem = Image.alpha_composite(self._fundo_preview, disco).convert("RGB")
        if not hasattr(self, "_buffers_disco"):
            self._buffers_disco = [ImageTk.PhotoImage(imagem), ImageTk.PhotoImage(imagem)]
            self._buffer_disco_indice = 0
        else:
            self._buffer_disco_indice = 1 - self._buffer_disco_indice
            self._buffers_disco[self._buffer_disco_indice].paste(imagem)
        self.img_disco = self._buffers_disco[self._buffer_disco_indice]
        self.canvas.itemconfigure(self.disco_item, image=self.img_disco)

    def cor_desejada(self):
        if self.config_usuario.get("gaming"):
            return PRATA
        cor = (self.midia or {}).get("cor")
        if self.config_usuario["modo"] != "video" and cor:
            return cor_album(cor)
        return next(m[3] for m in MODOS if m[0] == self.config_usuario["modo"])

    def atualizar_tema(self):
        destino = self.cor_desejada()
        if destino == self.cor_destino:
            return
        self.cor_destino = destino
        if self.tema_job:
            self.after_cancel(self.tema_job)
        origem = self.cor_modo
        inicio = time.monotonic()
        precisao_animacao(self, "tema", True)

        def passo():
            if self.encerrando:
                self.tema_job = None
                return
            t = 1.0 if self.state() == "iconic" else min(1.0, (time.monotonic() - inicio) / 0.65)
            s = t * t * (3 - 2 * t)
            self.cor_modo = misturar(origem, destino, s)
            self.img_acento.paste(acento_vinil(self.cor_modo))
            if self._disco_pil is not None:
                # Atualiza também o fundo sob um vinil pausado, sem pedir
                # outra rotação nem esperar um quadro do motor da tela.
                self.apresentar_disco(self._disco_pil)
            self.canvas.itemconfigure(self.hero_rotulo, fill=SECUNDARIO)
            self.seletor.atualizar_cor(self.cor_modo)
            for botao in (self.auto, self.power, self.iniciar, self.fundo_botao,
                          self.gaming_botao, self.lotus_botao, self.letras_botao,
                          *self.sincronia_botoes.values()):
                botao.atualizar(cor=self.cor_modo)
            for botao in self.midia_botoes.values():
                botao.atualizar(cor=self.cor_modo)
            self.slider.atualizar(cor=self.cor_modo)
            self.sincronia_slider.atualizar(cor=self.cor_modo)
            if self.fundo_painel_aberto:
                self.fundo_painel.atualizar()
            if (self.midia or {}).get("conectada"):
                self.canvas.itemconfigure(self.status_ponto, fill=self.cor_modo)
                self.canvas.itemconfigure(self.status_texto, fill=SECUNDARIO)
            if (self.capa_preview is None or self.config_usuario["modo"] == "video"
                    or self.config_usuario.get("gaming")):
                self.atualizar_disco()
            self.tema_job = proximo_quadro(self, inicio, passo) if t < 1 else None
            if t >= 1:
                precisao_animacao(self, "tema", False)
        passo()

    def atualizar_visual(self, visual):
        self._visual = {} if self.config_usuario.get("gaming") else visual
        if self.disco_job is None:
            self.animar_disco()

    def animar_disco(self):
        self.disco_job = None
        if self.encerrando:
            return
        agora = time.monotonic()
        dt = min(0.1, max(0.001, agora - self._tempo_preview))
        self._tempo_preview = agora
        visual = self._visual
        visivel = (self.rodando and visual.get("visivel", False)
                   and self.config_usuario["modo"] != "video" and self.config_usuario["tela_ligada"]
                   and not self.config_usuario.get("gaming"))
        velocidade = visual.get("velocidade", 0.0) if visivel else 0.0
        velocidade_externa = visual.get("velocidade_disco", velocidade) if visivel else 0.0
        if visivel:
            fator = -math.expm1(-dt / 0.065)
            self._velocidade_gui += (velocidade - self._velocidade_gui) * fator
            self._velocidade_externa_gui += (velocidade_externa - self._velocidade_externa_gui) * fator
            if abs(self._velocidade_gui - velocidade) < 0.02:
                self._velocidade_gui = velocidade
            if abs(self._velocidade_externa_gui - velocidade_externa) < 0.02:
                self._velocidade_externa_gui = velocidade_externa
        else:
            self._velocidade_gui = self._velocidade_externa_gui = 0.0
        velocidade, velocidade_externa = self._velocidade_gui, self._velocidade_externa_gui
        # Só números atravessam o pipe. O disco 270px é animado localmente a 60 FPS.
        # A correção da fase é amortecida para os pacotes não causarem saltos.
        if visivel:
            idade = max(0.0, agora - visual.get("instante", agora))
            previsto = (self._angulo_gui + velocidade * dt) % 360
            alvo = (visual.get("angulo", 0.0)
                    + visual.get("velocidade", 0.0) * idade) % 360
            erro = (alvo - previsto + 180) % 360 - 180 if idade < 0.4 else 0.0
            self._angulo_gui = (previsto + erro * -math.expm1(-dt / 0.32)) % 360
            previsto_externo = (self._angulo_externo + velocidade_externa * dt) % 360
            alvo_externo = (visual.get("angulo_disco", visual.get("angulo", 0.0))
                            + visual.get("velocidade_disco", visual.get("velocidade", 0.0)) * idade) % 360
            erro_externo = (alvo_externo - previsto_externo + 180) % 360 - 180 if idade < 0.4 else 0.0
            self._angulo_externo = (previsto_externo + erro_externo * -math.expm1(-dt / 0.32)) % 360
        else:
            erro = erro_externo = 0.0
        escala_alvo = visual.get("escala", 1.0) if visivel else self._escala_gui
        self._escala_gui += (escala_alvo - self._escala_gui) * -math.expm1(-dt / 0.06)
        escala_movendo = abs(escala_alvo - self._escala_gui) > 0.001
        if not escala_movendo:
            self._escala_gui = escala_alvo
        if abs(erro) < 0.02 and velocidade == 0 and visivel:
            self._angulo_gui = visual.get("angulo", self._angulo_gui)
        if abs(erro_externo) < 0.02 and velocidade_externa == 0 and visivel:
            self._angulo_externo = visual.get("angulo_disco", self._angulo_externo)
        minimizada = self.state() == "iconic"
        if not minimizada:
            self.atualizar_disco()
        movendo = (abs(velocidade) > 0.001 or abs(velocidade_externa) > 0.001
                    or abs(erro) > 0.02 or abs(erro_externo) > 0.02 or escala_movendo)
        precisao_animacao(self, "vinil", movendo and not minimizada)
        if movendo:
            self.disco_job = (self.after(250, self.animar_disco) if minimizada else
                              proximo_quadro(self, self._relogio_preview, self.animar_disco))

    def atualizar_controles(self):
        valor = self.valor_brilho()
        ligada = self.config_usuario["tela_ligada"]
        self.canvas.itemconfigure(self.valor_texto, text=f"{valor}%" if ligada else "0%")
        automatico = self.config_usuario["brilho"] is None
        self.auto.atualizar(texto="Auto", cor=self.cor_modo, selecionado=automatico)
        self.power.atualizar(cor=self.cor_modo, selecionado=not ligada,
                             dica="Apagar tela e LEDs" if ligada else "Ligar tela e LEDs")
        self.iniciar.atualizar(cor=self.cor_modo)
        gaming = self.config_usuario.get("gaming", False)
        self.gaming_botao.atualizar(texto="Gaming", selecionado=gaming, cor=self.cor_modo,
                                    dica="Desligar Gaming" if gaming else "Só o fundo, com menos processamento")
        lotus = self.config_usuario.get("lotus_ativo", False)
        self.lotus_botao.atualizar(selecionado=lotus, cor=self.cor_modo,
                                   dica="Abrir cor, brilho e conexão da fita RGB")
        self.seletor.dica.texto = ("Modo retomado ao sair de Gaming." if gaming
                                   else MODOS[self.seletor.indice][2])
        self.canvas.itemconfigure(self.modo_dica, text="")
        self.atualizar_letras()
        self.atualizar_sincronias()
        self.slider.atualizar(valor, self.cor_modo)
        if self.lotus_painel is not None and self.lotus_painel.visivel:
            self.lotus_painel.atualizar()
        if self.cor_fundo_painel is not None and self.cor_fundo_painel.visivel:
            self.cor_fundo_painel.atualizar()
        self.canvas.itemconfigure(self.brilho_dica, text="")
        # As informações iniciais também respeitam o modo salvo na última sessão.
        if hasattr(self, "artista_texto"):
            self.atualizar_texto_midia()

    def atualizar_letras(self):
        visivel = self.config_usuario["modo"] != "video" and not self.config_usuario["gaming"]
        self.canvas.itemconfigure("letras", state="normal" if visivel else "hidden")
        ativas = self.config_usuario["letras_ativas"]
        self.letras_botao.atualizar(selecionado=ativas, cor=self.cor_modo,
                                    dica="Ocultar letras na tela USB" if ativas else "Mostrar letras na tela USB")
        self.atualizar_status_letras()

    def atualizar_sincronias(self):
        modo = self.config_usuario["modo"]
        gaming = self.config_usuario["gaming"]
        canais_visiveis = {"letras": modo != "video" and not gaming, "video": modo == "clipe" and not gaming}
        if self._sincronia_aberta and not canais_visiveis[self._sincronia_aberta]:
            self._sincronia_aberta = None
        for canal, visivel in canais_visiveis.items():
            self.canvas.itemconfigure(f"sincronia_{canal}", state="normal" if visivel else "hidden")
            self.sincronia_botoes[canal].atualizar(selecionado=canal == self._sincronia_aberta, cor=self.cor_modo)
        self.canvas.itemconfigure("sincronia_ajuste", state="normal" if self._sincronia_aberta else "hidden")
        if self._sincronia_aberta:
            self.atualizar_slider_sincronia()

    def atualizar_slider_sincronia(self):
        _, chave, limite = SINCRONIAS[self._sincronia_aberta]
        valor = self.config_usuario[chave]
        self.canvas.itemconfigure(self.sincronia_valor, text=f"{valor:+d} ms" if valor else "0 ms")
        self.sincronia_slider.atualizar(valor, self.cor_modo, limites=(-limite, limite))

    def alternar_sincronia(self, canal):
        self.sincronia_botoes[canal].dica.fechar()
        self.sincronia_slider.dica.fechar()
        if self.sincronia_slider.job is not None:
            self.sincronia_slider.after_cancel(self.sincronia_slider.job)
            self.sincronia_slider.job = None
        if self._sincronia_pendente:
            self.guardar()
        self._sincronia_aberta = None if self._sincronia_aberta == canal else canal
        self.atualizar_sincronias()

    def mudar_sincronia(self, valor, aplicar):
        if not self._sincronia_aberta:
            return
        _, chave, _ = SINCRONIAS[self._sincronia_aberta]
        if valor != self.config_usuario[chave]:
            self.config_usuario[chave] = valor
            self._sincronia_pendente = True
            self.atualizar_slider_sincronia()
        if aplicar and self._sincronia_pendente:
            self.guardar()

    def atualizar_status_letras(self):
        dados = self.midia or {}
        config = self.config_usuario
        config_motor = dados.get("config", {})
        texto = ""
        if (self.rodando and dados.get("conectada") and config["letras_ativas"]
                and config["tela_ligada"] and not config["gaming"]
                and config["modo"] != "video"
                and config["modo"] == config_motor.get("modo")
                and config_motor.get("letras_ativas") and config_motor.get("tela_ligada")):
            texto = {
                "buscando": "Buscando…",
                "pronta": "Pronta para exibir",
                "exibindo": "Na tela USB",
                "indisponivel": "Sem letra",
                "erro": "Falha na busca",
            }.get(dados.get("status_letras"), "")
        self.canvas.itemconfigure(self.letras_status, text=texto)

    def alternar_letras(self):
        self.config_usuario["letras_ativas"] = not self.config_usuario["letras_ativas"]
        if self.midia is not None:
            self.midia.pop("status_letras", None)
        self.atualizar_letras()
        self.guardar()

    def guardar(self):
        try:
            salvar(self.config_usuario)
        except OSError:
            self.mensagem("Não foi possível salvar as preferências.", "#966022")
        else:
            self._sincronia_pendente = False
        self.motor.enviar({"acao": "configurar", "config": self.config_usuario})
        self.fundo_painel.atualizar()

    def mudar_modo(self, modo):
        self.config_usuario["modo"] = modo
        self.atualizar_tema()
        self.atualizar_controles()
        self.atualizar_disco()
        self.atualizar_texto_midia()
        self.guardar()

    def alternar_gaming(self):
        self.config_usuario["gaming"] = not self.config_usuario.get("gaming", False)
        if self.config_usuario["gaming"]:
            self._visual = {}
            if self.disco_job is not None:
                self.after_cancel(self.disco_job)
                self.disco_job = None
            precisao_animacao(self, "vinil", False)
        self.atualizar_tema()
        self.atualizar_controles()
        self.atualizar_disco()
        self.guardar()
        self.mensagem("Preparando Gaming…"
                      if self.config_usuario["gaming"] else "Voltando ao modo de exibição selecionado…")

    def mudar_brilho(self, valor, aplicar):
        if not self._arraste_brilho_ativo:
            self._brilho_antes_arraste = self.config_usuario["brilho"]
            self._arraste_brilho_ativo = True
        self.config_usuario["tela_ligada"] = valor > 0
        if valor > 0:
            self.config_usuario["brilho"] = valor
        else:
            # Voltar a ligar recupera o brilho anterior ao gesto, e não o
            # último 1% pelo qual o slider passou antes de chegar a zero.
            self.config_usuario["brilho"] = self._brilho_antes_arraste
        self.atualizar_controles()
        if aplicar:
            self._arraste_brilho_ativo = False
            self.guardar()  # um comando ao soltar, sem inundar USB ou OpenRGB

    def automatico(self):
        self._arraste_brilho_ativo = False
        if self.slider.job is not None:
            self.after_cancel(self.slider.job)
            self.slider.job = None
        if not self.config_usuario["tela_ligada"]:
            self.config_usuario["brilho"] = None
            self.config_usuario["tela_ligada"] = True
        else:
            self.config_usuario["brilho"] = self.valor_brilho() if self.config_usuario["brilho"] is None else None
        self.atualizar_controles()
        self.guardar()

    def alternar_tela(self):
        self._arraste_brilho_ativo = False
        if self.slider.job is not None:
            self.after_cancel(self.slider.job)
            self.slider.job = None
        self.config_usuario["tela_ligada"] = not self.config_usuario["tela_ligada"]
        self.atualizar_controles()
        self.guardar()

    def alternar_motor(self):
        if self.iniciando:
            return
        if self.rodando:
            self.motor.parar()
            self.iniciar.atualizar(texto="Encerrando…", ativo=False)
        else:
            self.midia = None
            self.atualizar_texto_midia()
            self.guardar()
            self.iniciando = True
            self.iniciar.atualizar(texto="Conectando…", ativo=False)
            self.mensagem("Preparando a tela…")
            self.motor.iniciar(dict(self.config_usuario))

    def mensagem(self, texto, cor=SECUNDARIO):
        self.canvas.itemconfigure(self.aviso, text=texto[:104], fill=cor)

    def encurtar(self, texto, largura, fonte):
        if fonte.measure(texto) <= largura:
            return texto
        while texto and fonte.measure(texto + "…") > largura:
            texto = texto[:-1]
        return texto + "…"

    def eventos(self):
        if self.encerrando:
            return
        self.eventos_fundo()
        self.eventos_midia()
        try:
            while True:
                tipo, dados = self.motor.eventos.get_nowait()
                if tipo == "iniciado":
                    self.rodando, self.iniciando = True, False
                    self.iniciar.atualizar(texto="Parar", ativo=True, simbolo="stop", dica="Parar exibição")
                    self.atualizar_texto_midia()
                elif tipo in ("parado", "erro"):
                    self.rodando, self.iniciando = False, False
                    self._visual = {}
                    self.atualizar_status_letras()
                    self.iniciar.atualizar(texto="Iniciar", ativo=True, simbolo="play", dica="Iniciar exibição")
                    self.canvas.itemconfigure(self.status_texto, text="Parada", fill=SECUNDARIO)
                    self.canvas.itemconfigure(self.status_ponto, fill="#89929f")
                    self.mensagem(dados or "", "#966022" if dados else SECUNDARIO)
                    self.atualizar_texto_midia()
                elif tipo == "estado":
                    self.atualizar_estado(dados)
        except queue.Empty:
            pass
        self.after(50, self.eventos)

    def acao_midia(self, acao):
        if self.midia_ocupada or self.encerrando:
            return
        if self.controles_spotify.enviar(acao):
            self.midia_ocupada = True
            self.atualizar_botoes_midia()
        else:
            self.mensagem("Os controles do Spotify estão indisponíveis.", "#966022")

    def eventos_midia(self):
        try:
            while True:
                dados = self.controles_spotify.eventos.get_nowait()
                self.estado_controles_midia = dados
                self.midia_ocupada = False
                self.atualizar_botoes_midia()
                if dados.get("erro"):
                    self.mensagem(dados["erro"], "#966022")
        except queue.Empty:
            pass

    def atualizar_botoes_midia(self):
        dados = self.estado_controles_midia
        disponivel = dados.get("disponivel", False) and not self.midia_ocupada
        tocando = dados.get("tocando", False)
        repeticao = dados.get("repeticao", 0)
        for acao, botao in self.midia_botoes.items():
            opcoes = {"ativo": disponivel and dados.get(acao, False), "cor": self.cor_modo}
            if acao == "play_pause":
                opcoes.update(simbolo="pause" if tocando else "play",
                               dica="Pausar" if tocando else "Reproduzir")
            elif acao == "aleatorio":
                ligado = dados.get("aleatorio_ativo", False)
                opcoes.update(selecionado=ligado,
                               dica="Desativar aleatório" if ligado else "Ativar aleatório")
            elif acao == "repetir":
                opcoes.update(selecionado=repeticao != 0,
                               simbolo="repetir_um" if repeticao == 1 else "repetir",
                               dica={0: "Repetição desligada", 2: "Repetir fila", 1: "Repetir faixa"}.get(
                                   repeticao, "Repetição"))
            botao.atualizar(**opcoes)

    def posicionar_controles_midia(self, y=TOPO_TRANSPORTE):
        if y == self._posicao_controles_midia:
            return
        self.canvas.move("controles_midia", 0, y - self._posicao_controles_midia)
        self._posicao_controles_midia = y

    def atualizar_estado(self, dados):
        if "visual" in dados:
            self.atualizar_visual(dados["visual"])
        if "config" not in dados:
            return  # pacote pequeno de movimento, sem repetir metadados/tema/capa
        conectada = dados.get("conectada", False)
        if not conectada:
            self._visual = {}
        self.canvas.itemconfigure(self.status_texto, text="Conectada" if conectada else "Conectando…",
                                  fill=SECUNDARIO)
        self.canvas.itemconfigure(self.status_ponto, fill=self.cor_modo if conectada else "#89929f")
        self.midia = dados
        self.atualizar_status_letras()
        status_clipe = (dados.get("status_clipe", "") if self.config_usuario["modo"] == "clipe"
                        and not self.config_usuario.get("gaming") and conectada else "")
        self.canvas.itemconfigure(self.modo_dica, text=status_clipe)
        if not self.fundo_convertendo:
            if dados.get("erro_fundo"):
                self.fundo_status = (dados["erro_fundo"], "#966022")
                self.fundo_painel.atualizar()
            elif conectada and self.fundo_pendente and dados.get("fundo_ocioso_ativo") == self.config_usuario["video_ocioso"]:
                self.fundo_pendente = False
                self.fundo_status = ("Fundo atualizado. Ele aparece nos momentos ociosos.", SECUNDARIO)
                self.fundo_painel.atualizar()
        self.atualizar_tema()
        self.atualizar_texto_midia()
        if "capa" in dados:
            try:
                self.capa = Image.open(io.BytesIO(base64.b64decode(dados["capa"]))).convert("RGBA") if dados["capa"] else None
                self.capa_preview = self.capa.resize((114, 114), Image.Resampling.LANCZOS) if self.capa else None
                self.atualizar_disco()
            except (ValueError, OSError):
                pass
        lotus = dados.get("lotus") or {}
        if self.config_usuario.get("lotus_ativo"):
            self.lotus_botao.atualizar(dica="Fita RGB: " + lotus.get("status", "Conectando…") +
                                      ". Clique para ajustar cor e brilho.")
        if self.lotus_painel is not None and self.lotus_painel.visivel:
            self.lotus_painel.atualizar()
        if self.cor_fundo_painel is not None and self.cor_fundo_painel.visivel:
            self.cor_fundo_painel.atualizar()
        if dados.get("erro"):
            self.mensagem(dados["erro"], "#966022")
        elif self.config_usuario.get("lotus_ativo") and lotus.get("erro"):
            self.mensagem(lotus["erro"], "#966022")
        elif dados.get("erro_gaming"):
            self.mensagem(dados["erro_gaming"], "#966022")
        elif self.config_usuario.get("gaming") and dados.get("gaming_preparando"):
            self.mensagem("Preparando Gaming…")
        elif dados.get("gaming_ativo"):
            self.mensagem("")
        elif conectada:
            self.mensagem("")
        if self.config_usuario["brilho"] is None and self.config_usuario["tela_ligada"]:
            if self.slider.valor != self.valor_brilho():
                self.atualizar_controles()

    def atualizar_texto_midia(self):
        dados = self.midia or {}
        musica = dados.get("musica")
        chave = (
            self.rodando, bool(dados.get("conectada")),
            self.config_usuario["modo"], bool(self.config_usuario.get("gaming")),
            tuple(musica) if musica else None, bool(dados.get("tocando")),
            dados.get("playlist_nome"), bool(dados.get("gaming_preparando")),
            self.config_usuario["video_ocioso_nome"],
            self.f_faixa.chave, self.f_sans.chave, self.f_pequena.chave,
        )
        if chave == self._chave_texto_midia:
            return
        self._desenhar_texto_midia(dados)
        self._chave_texto_midia = chave

    def _desenhar_texto_midia(self, dados):
        musica = dados.get("musica")
        gaming = self.config_usuario.get("gaming", False)
        self.canvas.itemconfigure(self.playlist_texto, text="")
        if not self.rodando or not dados.get("conectada"):
            self.posicionar_controles_midia()
            self.canvas.itemconfigure(self.hero_rotulo, text="")
            self.canvas.itemconfigure(self.faixa_texto, text="Esperando por você…")
            self.canvas.itemconfigure(self.artista_texto, text="")
            return
        if gaming or self.config_usuario["modo"] == "video":
            self.posicionar_controles_midia()
            self.canvas.itemconfigure(self.hero_rotulo, text="Gaming" if gaming else "Vídeo")
            self.canvas.itemconfigure(self.faixa_texto, text="Vídeo de fundo")
            self.canvas.itemconfigure(self.artista_texto, text="Preparando…" if dados.get("gaming_preparando") else
                                      self.encurtar(self.config_usuario["video_ocioso_nome"] or "", 408, self.f_sans))
            self.canvas.coords(self.artista_texto, 529, TOPO_TITULO + 58)
            return
        if musica:
            titulo, artista = musica
            # Até duas linhas, sem texto promocional ou estado repetido junto do artista.
            titulo = limitar_linhas(titulo, self.f_faixa, 408, limite=2)
            self.canvas.itemconfigure(self.hero_rotulo, text="Spotify" if dados.get("tocando") else "Em pausa")
            self.canvas.itemconfigure(self.faixa_texto, text=titulo)
            self.canvas.itemconfigure(self.artista_texto, text=self.encurtar(artista, 408, self.f_sans))
            altura = texto_suave(titulo, self.f_faixa.chave, TEXTO, 408).height
            y_artista = TOPO_TITULO + altura + 14
            self.canvas.coords(self.artista_texto, 529, y_artista)
            playlist = dados.get("playlist_nome")
            if playlist:
                self.canvas.coords(self.playlist_texto, 529, y_artista + 28)
                self.canvas.itemconfigure(self.playlist_texto,
                                           text=self.encurtar(playlist, 408, self.f_pequena))
            self.posicionar_controles_midia(max(TOPO_TRANSPORTE, y_artista + (56 if playlist else 32)))
        else:
            self.posicionar_controles_midia()
            self.canvas.itemconfigure(self.hero_rotulo, text="")
            self.canvas.itemconfigure(self.faixa_texto, text="Esperando por você…")
            self.canvas.itemconfigure(self.artista_texto, text="")

    def fechar(self):
        if self.encerrando:
            return
        if self._sincronia_pendente:
            self.guardar()
        self.encerrando = True
        if self.editor_fundo is not None:
            self.editor_fundo.encerrar()
            self.editor_fundo = None
        if self.cor_fundo_painel is not None:
            self.cor_fundo_painel.encerrar()
            self.cor_fundo_painel = None
        if self.lotus_painel is not None:
            self.lotus_painel.encerrar()
            self.lotus_painel = None
        self.controles_spotify.fechar()
        self.relogio_animacao.fechar()
        self.rotacao_preview.fechar()
        self.conversor_fundo.cancelar()
        self.motor.parar()
        self.iniciar.atualizar(texto="Encerrando…", ativo=False)
        inicio = time.monotonic()

        def aguardar():
            proc = self.motor.proc
            if (proc is None or proc.poll() is not None) and not self.conversor_fundo.ocupado:
                if self.moldura_windows is not None:
                    self.moldura_windows.fechar()
                self.destroy()
            elif time.monotonic() - inicio >= 10:
                # Fechar o stdin aciona o mesmo encerramento se o comando se perdeu.
                try:
                    if proc is not None:
                        proc.stdin.close()
                except (OSError, ValueError):
                    pass
                self.mensagem("Aguardando a tela encerrar…")
                self.after(500, aguardar)
            else:
                self.after(150, aguardar)
        aguardar()


def main():
    try:
        reserva = reservar_execucao("Local\\TuringVinylInterface")
    except RuntimeError:
        return
    try:
        preparar_identidade_processo()
        Janela().mainloop()
    finally:
        liberar_execucao(reserva)


if __name__ == "__main__":
    main()
