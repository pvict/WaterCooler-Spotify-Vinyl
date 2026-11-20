import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

import tela_completa as tela
from controle_interface import status_letras_atual
from letras_sincronizadas import FaixaLetra, ResultadoLetra, ler_lrc
from letras_visual import fundo_da_letra


class TestLetrasTela(unittest.TestCase):
    def setUp(self):
        self.capa = Image.new("RGBA", (240, 240), (170, 94, 80, 0))
        self.snap = dict(tela.estado, musica=("Farol de Papel", "Maré Alta"),
                         album="Vento de Vidro", capa_letras=self.capa, pos=1.5,
                         dur=90, t_poll=100, tocando=True, tela_conectada=True)
        self.config = dict(tela.controles.config, modo="spotify", letras_ativas=True,
                           tela_ligada=True, brilho=100, gaming=False)
        self.clip_frame = Image.new("RGB", (480, 480), "green")
        self.clipe = Mock()
        self.clipe.quadro.return_value = self.clip_frame
        self.clipe.posicao_letra.return_value = 2.5
        self.fonte = Mock()
        self.letra = ler_lrc("[00:02]O farol cruza a maré")
        self.fonte.consultar.return_value = ResultadoLetra("pronta", self.letra)
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

    def usar_composicao_real(self):
        fundo = SimpleNamespace(proximo=lambda: self.quadro_normal)
        self.painel = tela.Painel(fundo, fundo, 100, clipe=self.clipe, letras=self.fonte)

    def test_spotify_compõe_na_saida_usb_sem_desligar_o_preview_do_vinil(self):
        self.painel.p, self.painel.capa = 1.0, self.capa
        imagem = self.quadro(100.6)
        assert imagem.getpixel((0, 0)) == fundo_da_letra(self.capa).getpixel((0, 0))
        assert (255, 255, 255) in set(imagem.get_flattened_data())
        assert status_letras_atual(self.config, self.snap) == "exibindo"
        self.painel._publicar_visual.assert_called_once_with(True)
        self.fonte.consultar.assert_called_once_with(
            FaixaLetra("Farol de Papel", "Maré Alta", "Vento de Vidro", 90))

    def test_pausado_nao_avanca_e_mudanca_de_posicao_atualiza(self):
        self.snap.update(tocando=False, pos=2.7)
        self.painel.p = 1.0
        depois = self.quadro(120)
        assert self.quadro(132) is depois
        assert status_letras_atual(self.config, self.snap) == "exibindo"
        self.snap.update(pos=0.2, t_poll=133)
        assert self.quadro(133).tobytes() != depois.tobytes()

    def test_clipe_usa_posicao_de_apresentacao_e_fallback_preserva_quadro(self):
        self.config["modo"] = "clipe"
        snap = dict(self.snap)
        imagem = self.quadro(100.2)
        assert imagem.tobytes() != self.clip_frame.tobytes()
        assert status_letras_atual(self.config, self.snap) == "exibindo"
        self.clipe.posicao_letra.assert_called_once_with(snap, 100.2)

        self.fonte.consultar.return_value = ResultadoLetra("indisponivel")
        assert self.quadro(100.2) is self.clip_frame
        assert status_letras_atual(self.config, self.snap) == "indisponivel"
        self.config["letras_ativas"] = False
        assert status_letras_atual(self.config, self.snap) == ""
        assert self.quadro(100.3) is self.clip_frame
        self.fonte.consultar.assert_called_with(None)

    def test_legenda_antecipa_o_verso_em_meio_segundo_alem_do_avanco_do_video(self):
        self.config["modo"] = "clipe"
        self.clipe.posicao_letra.return_value = 1.49
        assert self.quadro(100) is self.clip_frame

        self.clipe.posicao_letra.return_value = 1.5
        assert self.quadro(100) is not self.clip_frame
        assert status_letras_atual(self.config, self.snap) == "exibindo"

    def test_letra_em_tela_cheia_usa_o_mesmo_avanco_em_todos_os_modos(self):
        self.clipe.quadro.return_value = None
        for modo in ("spotify", "dinamico", "clipe"):
            with self.subTest(modo=modo):
                self.usar_composicao_real()
                self.config["modo"] = modo
                with patch.object(self.painel.visual_letras, "spotify", return_value=self.quadro_normal) as render:
                    self.quadro(100)
                    render.assert_called_once_with(self.capa, self.letra, 2.0)

    def test_clipe_sem_quadro_mostra_letra_ate_o_video_chegar(self):
        self.usar_composicao_real()
        self.config["modo"] = "clipe"
        self.clipe.quadro.return_value = None

        letra = self.quadro(100.7)
        assert letra.getpixel((0, 0)) == (102, 56, 48)
        assert (255, 255, 255) in letra.crop((80, 200, 400, 280)).get_flattened_data()
        assert status_letras_atual(self.config, self.snap) == "exibindo"

        self.clipe.quadro.return_value = self.clip_frame
        legendado = self.quadro(100.8)
        topo = (0, 0, 480, 310)
        assert legendado.crop(topo).tobytes() == self.clip_frame.crop(topo).tobytes()
        pixels_legenda = legendado.crop((80, 310, 400, 391)).get_flattened_data()
        assert (255, 255, 255) in pixels_legenda
        assert (0, 0, 0) in pixels_legenda
        assert status_letras_atual(self.config, self.snap) == "exibindo"

    def test_letras_aparecem_no_primeiro_quadro_sem_capa(self):
        self.usar_composicao_real()
        self.snap["capa_letras"] = None

        imagem = self.quadro(100.7)
        assert imagem.getpixel((0, 0)) == (10, 10, 20)
        assert (255, 255, 255) in imagem.crop((80, 200, 400, 280)).get_flattened_data()
        assert status_letras_atual(self.config, self.snap) == "exibindo"

    def test_letras_sem_capa_nao_usam_a_arte_da_faixa_anterior(self):
        self.clipe.quadro.return_value = None
        capa_anterior = Image.new("RGBA", self.capa.size, "red")
        self.snap.update(capa=capa_anterior, capa_letras=capa_anterior)
        for modo in ("spotify", "dinamico", "clipe"):
            with self.subTest(modo=modo):
                self.usar_composicao_real()
                self.config["modo"] = modo
                self.snap.update(musica=("Faixa anterior", "Maré Alta"), capa_letras=capa_anterior)
                assert self.quadro(100.7).getpixel((0, 0)) == (153, 0, 0)

                self.snap.update(musica=("Farol de Papel", "Maré Alta"), capa_letras=None)
                imagem = self.quadro(100.8)
                assert imagem.getpixel((0, 0)) == (10, 10, 20)
                assert (255, 255, 255) in imagem.crop((80, 200, 400, 280)).get_flattened_data()
                assert status_letras_atual(self.config, self.snap) == "exibindo"

                self.snap["capa_letras"] = self.capa
                assert self.quadro(100.9).getpixel((0, 0)) == (102, 56, 48)

    def test_spotify_sem_letra_mostra_o_quadro_normal(self):
        self.fonte.consultar.return_value = ResultadoLetra("buscando")
        assert self.quadro(100.7).tobytes() == self.quadro_normal.tobytes()
        assert status_letras_atual(self.config, self.snap) == "buscando"

    def test_status_de_falha_nao_aparece_em_outra_musica_ou_com_tela_apagada(self):
        self.fonte.consultar.return_value = ResultadoLetra("erro")
        self.quadro(100.7)
        assert status_letras_atual(self.config, self.snap) == "erro"
        self.snap["musica"] = ("Outra faixa", "Maré Alta")
        assert status_letras_atual(self.config, self.snap) == ""
        self.snap["musica"] = ("Farol de Papel", "Maré Alta")
        self.config["tela_ligada"] = False
        assert status_letras_atual(self.config, self.snap) == ""
        self.quadro(100.8)
        self.fonte.consultar.assert_called_with(None)
