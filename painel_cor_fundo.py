"""Preferência de iluminação do vídeo de fundo, sem acesso ao hardware."""
from painel_cor import PainelCor, TEXTO, SECUNDARIO


class PainelCorFundo(PainelCor):
    TITULO = "Cor dos LEDs"
    CHAVE_COR = "cor_fundo"
    COR_INICIAL = (142, 0, 218)
    ALTURA = 660
    DICA_VALOR = "Luminosidade da cor (V). O brilho geral continua no controle principal."

    def construir_cabecalho(self, botao):
        self.canvas.itemconfigure(self.status_item, width=475)
        self.explicacao = self.canvas.create_text(
            25, 95, text="Com qualquer vídeo de fundo, ilumina o PC e a fita no modo Música.\n"
            "Durante as músicas, os LEDs continuam seguindo a capa.",
            font=self.janela.f_pequena, fill=SECUNDARIO, anchor="nw", width=475)

    def construir_rodape(self, botao, slider):
        self.restaurar = botao(self, "Usar perfil", self.usar_perfil,
                              largura=172, altura=46, dica="Restaurar as cores originais do fundo")
        self.canvas.create_window(20, 596, window=self.restaurar, anchor="nw")
        self.canvas.create_text(210, 606, text="Brilho no controle principal.",
                                font=self.janela.f_pequena, fill=TEXTO, anchor="nw")

    def usar_perfil(self):
        # Primeiro conclui os campos e timers para nenhum arraste pendente
        # recolocar uma cor depois da restauração.
        if self.color_job:
            self.usar_hsv()
        self.aplicar_campos(False)
        self.janela.config_usuario["cor_fundo"] = None
        self.persistir()
        self.atualizar()

    def atualizar(self):
        config = self.janela.config_usuario
        personalizada = config.get("cor_fundo") is not None
        if not config["tela_ligada"]:
            texto = "Cor salva. Iluminação apagada pelo controle principal."
        else:
            texto = "Cor personalizada" if personalizada else "Perfil original ativo"
        self.canvas.itemconfigure(self.status_item, text=texto)
        self.restaurar.atualizar(ativo=personalizada, cor=self.janela.cor_modo)
        for formato, botao in self.formatos.items():
            botao.atualizar(selecionado=self.formato == formato, cor=self.janela.cor_modo)
