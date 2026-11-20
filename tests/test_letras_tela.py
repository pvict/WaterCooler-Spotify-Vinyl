import unittest
from unittest.mock import Mock, patch

from PIL import Image

import tela_completa as tela
from letras_sincronizadas import FaixaLetra, ler_lrc
from letras_visual import fundo_da_letra


class TestLetrasTela(unittest.TestCase):
    def setUp(self):
        self.capa = Image.new("RGBA", (240, 240), (170, 94, 80, 0))
        self.snap = dict(tela.estado, musica=("Farol de Papel", "Maré Alta"),
                         album="Vento de Vidro", capa_letras=self.capa, pos=1.5,
                         dur=90, t_poll=100, tocando=True)
        self.config = dict(tela.controles.config, modo="spotify", letras_ativas=True,
                           tela_ligada=True, brilho=100)
        self.clip_frame = Image.new("RGB", (480, 480), "green")
        self.clipe = Mock()
        self.clipe.quadro.return_value = self.clip_frame
        self.clipe.posicao_letra.return_value = 2.5
        self.fonte = Mock()
        self.fonte.consultar.return_value = ler_lrc("[00:02]O farol cruza a maré")
        self.quadro_normal = Image.new("RGB", (480, 480), "blue")
        self.painel = tela.Painel(None, None, 100, clipe=self.clipe, letras=self.fonte)
        self.painel._fundo = Mock(return_value=self.quadro_normal)
        self.painel._painel = Mock(return_value=Image.new("RGBA", (480, 480)))
        self.painel._publicar_visual = Mock()

    def quadro(self, tempo):
        with (patch.object(tela, "estado", self.snap),
              patch.object(tela.controles, "config", self.config),
              patch.object(tela.time, "monotonic", return_value=tempo)):
            return self.painel.quadro(tempo)

    def test_spotify_compõe_na_saida_usb_sem_desligar_o_preview_do_vinil(self):
        self.painel.p, self.painel.capa = 1.0, self.capa
        imagem = self.quadro(100.6)
        assert imagem.getpixel((0, 0)) == fundo_da_letra(self.capa).getpixel((0, 0))
        assert (255, 255, 255) in set(imagem.get_flattened_data())
        self.painel._publicar_visual.assert_called_once_with(True)
        self.fonte.consultar.assert_called_once_with(
            FaixaLetra("Farol de Papel", "Maré Alta", "Vento de Vidro", 90))

    def test_pausado_nao_avanca_e_mudanca_de_posicao_atualiza(self):
        self.snap.update(tocando=False, pos=2.7)
        self.painel.p = 1.0
        depois = self.quadro(120)
        assert self.quadro(132) is depois
        self.snap.update(pos=0.2, t_poll=133)
        assert self.quadro(133).tobytes() != depois.tobytes()

    def test_clipe_usa_posicao_de_apresentacao_e_fallback_preserva_quadro(self):
        self.config["modo"] = "clipe"
        imagem = self.quadro(100.2)
        assert imagem.tobytes() != self.clip_frame.tobytes()
        self.clipe.posicao_letra.assert_called_once_with(self.snap, 100.2)

        self.fonte.consultar.return_value = None
        assert self.quadro(100.2) is self.clip_frame
        self.config["letras_ativas"] = False
        self.fonte.consultar.return_value = None
        assert self.quadro(100.3) is self.clip_frame
        self.fonte.consultar.assert_called_with(None)

    def test_spotify_sem_letra_ou_sem_capa_mostra_o_quadro_normal(self):
        self.fonte.consultar.return_value = None
        assert self.quadro(100.7).tobytes() == self.quadro_normal.tobytes()
        self.fonte.consultar.return_value = ler_lrc("[00:02]O farol cruza a maré")
        self.snap["capa_letras"] = None
        assert self.quadro(100.8).tobytes() == self.quadro_normal.tobytes()
