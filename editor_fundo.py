"""Editor de fundo com prévia independente, sem acesso a USB/OpenRGB/Bluetooth."""
from pathlib import Path
import queue
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk
from ajustes_fundo import AJUSTES_PADRAO, LIMITES_AJUSTES, validar_ajustes, posicao_arraste
from aparencia_windows import preparar_janela
from estilo_interface import CanvasSuave, FonteInterface
from painel_cor import FUNDO, TEXTO, SECUNDARIO
from previa_fundo import PreviaFundo, LADO_PREVIA


class EditorFundo(tk.Toplevel):
    def __init__(self, janela, botao, slider):
        super().__init__(janela)
        self.withdraw()
        self.janela = janela
        self.relogio_animacao = janela.relogio_animacao
        self.f_sans = janela.f_sans
        self.configure(bg=FUNDO)
        self.title("Ajustar vídeo · Turing Vinyl")
        self.transient(janela)
        self.protocol("WM_DELETE_WINDOW", self.fechar)
        self.bind("<Escape>", lambda _: self.fechar())
        self.visivel = False
        self.moldura_windows = None
        self.previa = None
        self.job = self.preview_job = None
        self.revisao = self.revisao_pronta = None
        self.origem = None
        self.ajustes = dict(AJUSTES_PADRAO)
        self.arraste = None
        self.dimensoes = None
        self.canvas = CanvasSuave(self, width=900, height=640, bg=FUNDO,
                                  highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas.configure(scrollregion=(0, 0, 900, 640))
        self.vertical = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vertical.set, xscrollcommand=self.horizontal.set)
        self.bind("<MouseWheel>", self.rolar)
        self.canvas.create_text(28, 22, text="Ajustar vídeo", font=FonteInterface(24, 550, serif=True),
                                anchor="nw", fill=TEXTO)
        self.nome = self.canvas.create_text(28, 65, text="", font=janela.f_pequena,
                                           anchor="nw", fill=SECUNDARIO, width=790)
        self.fechar_botao = botao(self, "", self.fechar, largura=42, altura=42,
                                  simbolo="fechar", dica="Fechar sem aplicar")
        self.canvas.create_window(830, 18, window=self.fechar_botao, anchor="nw")
        self.preview = tk.Canvas(self, width=360, height=360, bg=FUNDO,
                                 bd=0, highlightthickness=0, cursor="fleur", takefocus=True)
        self.canvas.create_window(48, 126, window=self.preview, anchor="nw")
        self.foto = ImageTk.PhotoImage(Image.new("RGB", (360, 360), FUNDO), master=self)
        self.preview_item = self.preview.create_image(0, 0, image=self.foto, anchor="nw")
        self.preview.bind("<Button-1>", self.iniciar_arraste)
        self.preview.bind("<B1-Motion>", self.arrastar)
        self.preview.bind("<ButtonRelease-1>", lambda _: self.atualizar_previa(True))
        self.preview.bind("<MouseWheel>", self.zoom_roda)
        for tecla, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, -1), ("Down", 0, 1)):
            self.preview.bind(f"<{tecla}>", lambda _, x=dx, y=dy: self.mover_tecla(x, y))
        self.mascara = Image.new("L", (360, 360))
        ImageDraw.Draw(self.mascara).ellipse((0, 0, 359, 359), fill=255)
        self.canvas.create_text(228, 505, text="Arraste para posicionar · role para ajustar o zoom",
                                anchor="center", font=FonteInterface(11), fill=SECUNDARIO)
        self.sliders, self.valores = {}, {}
        rotulos = {"zoom": "Zoom", "x": "Posição horizontal", "y": "Posição vertical",
                   "brilho": "Brilho do vídeo", "saturacao": "Saturação", "nitidez": "Nitidez"}
        for i, (chave, rotulo) in enumerate(rotulos.items()):
            y = 106 + i * 68
            self.canvas.create_text(462, y, text=rotulo, font=janela.f_sans, fill=TEXTO, anchor="nw")
            self.valores[chave] = self.canvas.create_text(852, y, text="", font=janela.f_sans,
                                                        fill=TEXTO, anchor="ne")
            minimo, maximo = LIMITES_AJUSTES[chave]
            controle = slider(self, self.ajustes[chave],
                lambda valor, soltar, k=chave: self.mudar(k, valor, soltar),
                largura=430, fundo=FUNDO, minimo=minimo, maximo=maximo)
            self.sliders[chave] = controle
            self.canvas.create_window(440, y + 18, window=controle, anchor="nw")
        self.status = self.canvas.create_text(28, 540, text="", font=janela.f_pequena,
                                             fill=SECUNDARIO, anchor="nw", width=844)
        self.reset = botao(self, "Restaurar ajustes", self.restaurar, largura=190, altura=48)
        self.cancelar = botao(self, "Cancelar", self.fechar, largura=145, altura=48)
        self.aplicar_botao = botao(self, "Aplicar", self.aplicar, largura=155, altura=48, primario=True)
        self.canvas.create_window(20, 582, window=self.reset, anchor="nw")
        self.canvas.create_window(550, 582, window=self.cancelar, anchor="nw")
        self.canvas.create_window(713, 582, window=self.aplicar_botao, anchor="nw")

    def abrir(self, origem, ajustes=None):
        if self.visivel:
            self.fechar()
        self.origem = Path(origem)
        self.ajustes = validar_ajustes(ajustes)
        self.visivel = True
        self.revisao_pronta = None
        self.dimensoes = None
        self.canvas.itemconfigure(self.nome, text=self.janela.encurtar(self.origem.name, 790,
                                                                     self.janela.f_pequena))
        self.atualizar_controles()
        largura, altura = min(900, self.winfo_screenwidth() - 60), min(640, self.winfo_screenheight() - 110)
        self.vertical.grid(row=0, column=1, sticky="ns") if altura < 640 else self.vertical.grid_remove()
        self.horizontal.grid(row=1, column=0, sticky="ew") if largura < 900 else self.horizontal.grid_remove()
        x = max(0, min(self.janela.winfo_rootx() + 40, self.winfo_screenwidth() - largura - 30))
        y = max(0, min(self.janela.winfo_rooty() + 30, self.winfo_screenheight() - altura - 70))
        self.geometry(f"{largura}x{altura}+{x}+{y}")
        self.resizable(False, False)
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.deiconify()
        self.update_idletasks()
        if self.moldura_windows is None:
            raiz = Path(__file__).resolve().parent
            self.moldura_windows = preparar_janela(self, FUNDO, TEXTO,
                raiz / "assets/icons/turing-vinyl.ico", raiz / "iniciar_interface.pyw")
        self.lift()
        self.previa = PreviaFundo()
        self.atualizar_previa(True)
        self.receber()

    def atualizar_controles(self):
        for chave, controle in self.sliders.items():
            controle.atualizar(valor=self.ajustes[chave], cor=self.janela.cor_modo)
            self.canvas.itemconfigure(self.valores[chave], text=f"{self.ajustes[chave]}%")
        for botao in (self.reset, self.cancelar, self.aplicar_botao, self.fechar_botao):
            botao.atualizar(cor=self.janela.cor_modo)

    def mudar(self, chave, valor, soltar=False):
        self.ajustes[chave] = valor
        self.atualizar_controles()
        self.atualizar_previa(soltar)

    def atualizar_previa(self, imediato=False):
        if not self.visivel:
            return
        self.revisao_pronta = None
        self.aplicar_botao.atualizar(ativo=False)
        if self.preview_job is not None:
            if not imediato:
                return  # Cadência limitada: continua atualizando mesmo durante o arraste.
            self.after_cancel(self.preview_job)
            self.preview_job = None
        if imediato:
            self.enviar_previa()
        else:
            self.preview_job = self.after(180, self.enviar_previa)

    def enviar_previa(self):
        self.preview_job = None
        self.canvas.itemconfigure(self.status, text="Atualizando prévia…", fill=SECUNDARIO)
        self.revisao = self.previa.solicitar(self.origem, self.ajustes)

    def receber(self):
        self.job = None
        if not self.visivel:
            return
        try:
            revisao, dimensoes = self.previa.informacoes.get_nowait()
            if revisao == self.revisao:
                self.dimensoes = dimensoes
        except queue.Empty:
            pass
        try:
            revisao, erro = self.previa.eventos.get_nowait()
            if revisao == self.revisao:
                self.canvas.itemconfigure(self.status, text=erro[:220], fill="#966022")
                self.revisao_pronta = None
                self.aplicar_botao.atualizar(ativo=False)
        except queue.Empty:
            pass
        try:
            revisao, quadro = self.previa.quadros.get_nowait()
            if revisao == self.revisao:
                imagem = Image.new("RGB", (LADO_PREVIA, LADO_PREVIA), FUNDO)
                imagem.paste(quadro, (0, 0), self.mascara)
                self.foto.paste(imagem)
                self.revisao_pronta = revisao
                self.aplicar_botao.atualizar(ativo=self.preview_job is None)
                self.canvas.itemconfigure(self.status,
                    text="Prévia na interface. A tela só muda depois de Aplicar.", fill=SECUNDARIO)
        except queue.Empty:
            pass
        self.job = self.after(40, self.receber)

    def iniciar_arraste(self, evento):
        self.preview.focus_set()
        self.arraste = (evento.x, evento.y, self.ajustes["x"], self.ajustes["y"])

    def arrastar(self, evento):
        if self.arraste is None or self.dimensoes is None:
            return
        x, y, px, py = self.arraste
        self.ajustes["x"], self.ajustes["y"] = posicao_arraste(
            self.dimensoes, self.ajustes, (px, py), ((evento.x - x) / 360, (evento.y - y) / 360))
        self.atualizar_controles()
        self.atualizar_previa()

    def mover_tecla(self, dx, dy):
        self.ajustes["x"] = max(0, min(100, self.ajustes["x"] + dx))
        self.ajustes["y"] = max(0, min(100, self.ajustes["y"] + dy))
        self.atualizar_controles()
        self.atualizar_previa()
        return "break"

    def zoom_roda(self, evento):
        self.mudar("zoom", max(25, min(300, self.ajustes["zoom"] + (5 if evento.delta > 0 else -5))))
        return "break"

    def rolar(self, evento):
        self.canvas.yview_scroll(-int(evento.delta / 120), "units")

    def restaurar(self):
        self.ajustes = dict(AJUSTES_PADRAO)
        self.atualizar_controles()
        self.atualizar_previa(True)

    def aplicar(self):
        if self.revisao_pronta != self.revisao or self.preview_job is not None:
            return
        if self.janela.preparar_fundo(self.origem, dict(self.ajustes)):
            self.fechar()

    def fechar(self):
        self.visivel = False
        for atributo in ("job", "preview_job"):
            job = getattr(self, atributo)
            if job is not None:
                self.after_cancel(job)
                setattr(self, atributo, None)
        if self.previa is not None:
            self.previa.fechar()
            self.previa = None
        # Widgets escondidos não mantêm timers de hover, tecla ou dicas.
        for widget in self.winfo_children():
            dica = getattr(widget, "dica", None)
            if dica:
                dica.fechar()
            for atributo in ("job", "hover_job"):
                job = getattr(widget, atributo, None)
                if job is not None:
                    widget.after_cancel(job)
                    setattr(widget, atributo, None)
            self.relogio_animacao.marcar((id(widget), "hover"), False)
        self.withdraw()

    def encerrar(self):
        self.fechar()
        if self.moldura_windows is not None:
            self.moldura_windows.fechar()
        self.destroy()
