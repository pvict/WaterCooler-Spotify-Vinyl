import unittest
import threading
from types import SimpleNamespace
from unittest.mock import patch

from controle_interface import PADRAO, validar
from leds_openrgb import cores_alvo
import leds_openrgb
from leds_lotus import LotusLantern


class CorFundoTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(PADRAO, cor_fundo=[24, 120, 220], video_ocioso="custom.mp4")
        self.zonas = [("placa|zona", None, SimpleNamespace(leds=[1, 2]))]
        self.perfil = {"placa|zona": [(80, 0, 100), (90, 0, 110)]}

    def alvo_pc(self, estado, brilho=100, config=None):
        return cores_alvo(estado, brilho, self.zonas, self.perfil,
                          self.config if config is None else config)["placa|zona"]

    def test_preferencias_validam_cor_e_restauracao(self):
        self.assertEqual(validar(self.config)["cor_fundo"], [24, 120, 220])
        for invalida in ([256, 0, 0], [True, 2, 3], [1, 2], "#ffffff"):
            self.assertIsNone(validar({"cor_fundo": invalida})["cor_fundo"])
        self.assertIsNone(validar({"cor_fundo": None}, self.config)["cor_fundo"])

    def test_video_usa_cor_escolhida_com_brilho(self):
        self.assertEqual(self.alvo_pc({"modo_exibicao": "video"}, 50), [(12, 60, 110)] * 2)
        self.assertEqual(self.alvo_pc({}, 0), [(0, 0, 0)] * 2)

    def test_fade_envia_cor_escolhida_sem_voltar_ao_perfil(self):
        enviados = []

        def enviar(_zonas, cores, *_):
            enviados.append(cores)
            return True  # Encerra o loop sem conexão nem espera por hardware.

        with patch.object(leds_openrgb, "OpenRGBClient", return_value=SimpleNamespace(devices=[])), \
             patch.object(leds_openrgb, "_zonas", return_value=self.zonas), \
             patch.object(leds_openrgb, "_ler_perfil", return_value=self.perfil), \
             patch.object(leds_openrgb, "_enviar_etapa", side_effect=enviar):
            leds_openrgb._laco({"midia_pronta": True, "modo_exibicao": "video"},
                              lambda _: 100, threading.Event(), lambda _: None,
                              controles=SimpleNamespace(config=self.config))
        self.assertEqual(enviados, [{"placa|zona": [(24, 120, 220)] * 2}])

    def test_trocar_video_preserva_cor_e_seletor_continua_funcionando(self):
        for video in (None, "custom.mp4", "outro.mp4"):
            self.config["video_ocioso"] = video
            self.assertEqual(self.alvo_pc({}), [(24, 120, 220)] * 2)
        self.config["cor_fundo"] = [220, 40, 60]
        self.assertEqual(self.alvo_pc({}), [(220, 40, 60)] * 2)

    def test_restaurar_perfil_funciona_com_qualquer_video(self):
        for video in (None, "custom.mp4"):
            config = dict(self.config, cor_fundo=None, video_ocioso=video)
            self.assertEqual(self.alvo_pc({}, config=config), self.perfil["placa|zona"])

    def test_musica_e_ultima_midia_spotify_preservadas(self):
        musica = {"musica": ("Faixa", "Artista"), "cor_viva": (7, 8, 9, 255)}
        with patch("leds_openrgb.realcar", return_value=(10, 20, 30)):
            for estado in (dict(musica, modo_exibicao="dinamico"),
                           {"modo_exibicao": "spotify", "ultima_midia": musica}):
                self.assertEqual(self.alvo_pc(estado), [(10, 20, 30)] * 2)

    def test_fita_segue_fundo_mas_manual_e_desligamento_prevalecem(self):
        fita = LotusLantern.__new__(LotusLantern)
        fita.controles = SimpleNamespace(config=self.config)
        fita.estado = {"modo_exibicao": "video"}
        fita._cor_idle = (80, 0, 100)
        fita.brilho_para = lambda _: 50
        self.assertEqual(fita._cor_alvo(), (12, 60, 110))
        self.config["video_ocioso"] = "outro.mp4"
        self.config["cor_fundo"] = [220, 40, 60]
        self.assertEqual(fita._cor_alvo(), (110, 20, 30))
        self.config["cor_fundo"] = None
        self.assertEqual(fita._cor_alvo(), (40, 0, 50))
        self.config.update(lotus_modo="manual", lotus_cor=[200, 100, 0])
        self.assertEqual(fita._cor_alvo(), (100, 50, 0))
        self.config["tela_ligada"] = False
        self.assertEqual(fita._cor_alvo(), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
