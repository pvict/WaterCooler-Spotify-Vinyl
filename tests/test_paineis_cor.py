import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from controle_interface import PADRAO
from estilo_interface import FonteInterface, RelogioAnimacaoWindows
from interface import Botao, Slider
from painel_lotus import PainelLotus
from painel_cor_fundo import PainelCorFundo


class PaineisCorTests(unittest.TestCase):
    def setUp(self):
        self.raiz = tk.Tk()
        self.raiz.withdraw()
        self.raiz.config_usuario = dict(PADRAO)
        self.raiz.f_sans = FonteInterface(14, 500)
        self.raiz.f_pequena = FonteInterface(12, 450)
        self.raiz.cor_modo = "#345678"
        self.raiz.relogio_animacao = RelogioAnimacaoWindows()
        self.raiz.midia = None
        self.raiz.rodando = False
        self.raiz.alternar_lotus = lambda: None
        self.raiz.escolher_lotus = lambda: None
        self.comandos = []
        self.raiz.motor = SimpleNamespace(enviar=self.comandos.append)
        self.raiz.guardar = lambda: self.comandos.append(dict(self.raiz.config_usuario))
        self.paineis = []

    def tearDown(self):
        for painel in self.paineis:
            painel.encerrar()
        self.raiz.relogio_animacao.fechar()
        self.raiz.destroy()

    def painel(self, classe):
        with patch("painel_cor.gerar_roda", return_value=Image.new("RGBA", (300, 300))):
            painel = classe(self.raiz, Botao, Slider)
        self.paineis.append(painel)
        painel.visivel = True
        return painel

    def test_editor_fundo_hex_hsv_e_restauracao_nao_alteram_fita(self):
        painel = self.painel(PainelCorFundo)
        painel.trocar_formato("HEX")
        painel.vars[0].set("#2468ac")
        painel.aplicar_campos(True)
        self.assertEqual(self.raiz.config_usuario["cor_fundo"], [36, 104, 172])
        self.assertEqual(self.raiz.config_usuario["lotus_modo"], "musica")
        painel.trocar_formato("HSV")
        painel.vars[0].set("120")
        painel.vars[1].set("100")
        painel.vars[2].set("50")
        painel.aplicar_campos(True)
        self.assertEqual(self.raiz.config_usuario["cor_fundo"], [0, 128, 0])
        painel.usar_perfil()
        self.assertIsNone(self.raiz.config_usuario["cor_fundo"])
        self.assertIsNone(painel.send_job)
        self.assertIsNone(painel.save_job)
        painel.fechar()
        self.assertIsNone(self.raiz.config_usuario["cor_fundo"])

    def test_editor_lotus_continua_selecionando_manual(self):
        painel = self.painel(PainelLotus)
        painel.cor = (80, 120, 200)
        painel.mudar_brilho(40, True)
        painel.definir_cor()
        self.assertEqual(self.raiz.config_usuario["lotus_modo"], "manual")
        self.assertEqual(self.raiz.config_usuario["lotus_cor"], [80, 120, 200])
        self.assertIsNone(self.raiz.config_usuario["cor_fundo"])
        painel.dinamico.comando()
        self.assertTrue(painel.dinamico.selecionado)
        self.assertEqual(self.raiz.config_usuario["lotus_modo"], "dinamico")
        self.assertEqual(self.raiz.config_usuario["lotus_cor"], [80, 120, 200])
        self.assertEqual(painel.brilho["state"], "disabled")
        painel.manual.comando()
        self.assertEqual(painel.brilho["state"], "normal")
        self.assertEqual(painel.brilho.valor, 40)
        painel.modo("musica")
        self.assertEqual(self.raiz.config_usuario["lotus_modo"], "musica")


if __name__ == "__main__":
    unittest.main()
