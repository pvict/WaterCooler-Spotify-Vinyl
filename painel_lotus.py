"""Controles da fita Bluetooth; editor e animação compartilhados com o fundo."""
import tkinter as tk
from tkinter import ttk
from controle_interface import brilho_horario
from estilo_interface import FonteInterface
from painel_cor import PainelCor, FUNDO, TEXTO, SECUNDARIO


class PainelLotus(PainelCor):
    TITULO = "Fita RGB"
    CHAVE_COR = "lotus_cor"
    DICA_VALOR = "Luminosidade da cor (V). O brilho da fita fica no slider abaixo."

    def construir_cabecalho(self, botao):
        janela = self.janela
        self.candidatos = []
        self._descricao_selecionada = tk.StringVar()
        self.ligar = botao(self, "Desligada", janela.alternar_lotus, largura=160, altura=44,
                           simbolo="power", dica="Ligar fita", mostrar_foco=False)
        self.canvas.create_window(358, 52, window=self.ligar, anchor="nw")
        self.musica = botao(self, "Música", lambda: self.modo("musica"), largura=162, altura=48,
                            dica="Acompanhar a cor do álbum com a calibração da fita")
        self.dinamico = botao(self, "Dinâmico", lambda: self.modo("dinamico"), largura=162, altura=48,
                              dica="Acompanhar as cores e o brilho dos fans e da placa-mãe")
        self.manual = botao(self, "Manual", lambda: self.modo("manual"), largura=162, altura=48,
                            dica="Escolher uma cor fixa para a fita")
        self.canvas.create_window(20, 96, window=self.musica, anchor="nw")
        self.canvas.create_window(189, 96, window=self.dinamico, anchor="nw")
        self.canvas.create_window(358, 96, window=self.manual, anchor="nw")

    def construir_rodape(self, botao, slider):
        janela = self.janela
        self.brilho_rotulo = self.canvas.create_text(32, 592, text="Brilho da fita",
                                                    font=janela.f_sans, fill=TEXTO, anchor="nw")
        self.brilho_item = self.canvas.create_text(508, 592, text="", font=janela.f_sans, fill=TEXTO, anchor="ne")
        self.brilho = slider(self, 100, self.mudar_brilho, largura=400, fundo=FUNDO)
        self.canvas.create_window(12, 616, window=self.brilho, anchor="nw")
        self.auto = botao(self, "Tela", self.brilho_tela, largura=108, altura=46,
                          dica="Acompanhar o brilho dos LEDs do PC")
        self.canvas.create_window(410, 616, window=self.auto, anchor="nw")
        self.pasta = botao(self, "Pasta Lotus", self.trocar_pasta, largura=142, altura=38,
                           mostrar_foco=False)
        self.canvas.create_window(20, 658, window=self.pasta, anchor="nw")
        self.address_item = self.canvas.create_text(180, 673, text="", font=FonteInterface(11),
                                                   fill=SECUNDARIO, anchor="w")
        self.dispositivos = ttk.Combobox(self, state="readonly", textvariable=self._descricao_selecionada,
                                         font=("Segoe UI", 10))
        self.dispositivos.bind("<<ComboboxSelected>>", self.escolher_dispositivo)
        self.device_window = self.canvas.create_window(180, 663, window=self.dispositivos, anchor="nw",
                                                       width=334, height=25, state="hidden")

    def definir_cor(self):
        self.janela.config_usuario["lotus_modo"] = "manual"
        super().definir_cor()

    def modo(self, modo):
        if self.color_job:
            self.usar_hsv()
        self.aplicar_campos(False)
        self.janela.config_usuario["lotus_modo"] = modo
        self.atualizar()
        self.persistir()

    def mudar_brilho(self, valor, soltar):
        self.janela.config_usuario["lotus_brilho"] = valor
        self.atualizar()
        self.persistir() if soltar else self.agendar_envio()

    def brilho_tela(self):
        config = self.janela.config_usuario
        config["lotus_brilho"] = None if config.get("lotus_brilho") is not None else self.brilho.valor
        self.atualizar()
        self.persistir()

    def trocar_pasta(self):
        self.janela.escolher_lotus()
        self.atualizar()

    def escolher_dispositivo(self, _=None):
        i = self.dispositivos.current()
        if 0 <= i < len(self.candidatos):
            self.janela.config_usuario["lotus_endereco"] = self.candidatos[i]["endereco"]
            self.persistir()

    def atualizar(self):
        config = self.janela.config_usuario
        ligada = config.get("lotus_ativo", False)
        cor = self.janela.cor_modo
        modo = config.get("lotus_modo")
        segue_pc = modo == "dinamico"
        valor = None if segue_pc else config.get("lotus_brilho")
        automatico = valor is None
        if automatico:
            valor = (config["brilho"] if config["brilho"] is not None else brilho_horario()) if config["tela_ligada"] else 0
        iluminada = (ligada and config["tela_ligada"] and valor > 0
                      and (modo != "manual" or any(config["lotus_cor"])))
        rotulo = "Ligada" if iluminada else "Em espera" if ligada else "Desligada"
        self.ligar.atualizar(texto=rotulo, selecionado=ligada, cor=cor, mostrar_ponto=iluminada,
                             dica="Desativar fita" if ligada else "Ligar fita")
        self.musica.atualizar(selecionado=modo == "musica", cor=cor)
        self.dinamico.atualizar(selecionado=segue_pc, cor=cor)
        self.manual.atualizar(selecionado=modo == "manual", cor=cor)
        for formato, botao in self.formatos.items():
            botao.atualizar(selecionado=self.formato == formato, cor=cor)
        self.brilho.configure(state="disabled" if segue_pc else "normal", takefocus=not segue_pc,
                              cursor="arrow" if segue_pc else "hand2")
        self.brilho.atualizar(valor=valor, cor=SECUNDARIO if segue_pc else cor)
        self.auto.atualizar(ativo=not segue_pc, selecionado=automatico, cor=cor)
        self.canvas.itemconfigure(self.brilho_rotulo,
                                  text="Brilho sincronizado com o PC" if segue_pc else "Brilho da fita")
        self.canvas.itemconfigure(self.brilho_item, text=f"{valor}%")
        estado = (self.janela.midia or {}).get("lotus") or {}
        if not ligada:
            texto = "Desligada"
        elif not self.janela.rodando:
            texto = "Conecta ao iniciar a exibição"
        elif not config["tela_ligada"]:
            texto = "Apagada pelo controle principal"
        else:
            texto = estado.get("status", "Conectando…")
        self.canvas.itemconfigure(self.status_item, text=texto)
        candidatos = (estado.get("candidatos") or []) if ligada else []
        if candidatos != self.candidatos:
            self.candidatos = candidatos
            self.dispositivos["values"] = [f"{c['nome']} · {c['endereco'].upper()}" for c in candidatos]
            self._descricao_selecionada.set("Escolher fita…")
        varios = len(candidatos) > 1
        self.canvas.itemconfigure(self.device_window, state="normal" if varios else "hidden")
        self.canvas.itemconfigure(self.address_item, state="hidden" if varios else "normal",
            text=(estado.get("nome", "") + " · " + estado.get("endereco", "").upper()).strip(" ·"))
