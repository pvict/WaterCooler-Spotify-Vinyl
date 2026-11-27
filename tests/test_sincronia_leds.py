import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import leds_lotus
import leds_openrgb
from controle_interface import Controles, MODOS
from transicao_leds import CHAVE_PASSO, PassoRGB, ReferenciaRGB


class SincroniaLedsTests(unittest.TestCase):
    def test_fita_acompanha_cada_passo_mesmo_com_envios_espacados_na_placa(self):
        self.verificar_passos("musica")

    def test_dinamico_muda_do_video_para_a_musica_junto_dos_fans(self):
        self.verificar_passos("dinamico")

    def verificar_passos(self, modo):
        primeira_cor = (10, 20, 30)
        ultima_cor = (210, 220, 230)
        estado = {"midia_pronta": True, "modo_exibicao": "clipe"}
        config = {"lotus_ativo": True, "lotus_modo": modo, "tela_ligada": True,
                  "cor_fundo": primeira_cor}
        controles = SimpleNamespace(config=config)
        parar, placa_pronta, fita_pronta, fita_final = [threading.Event() for _ in range(4)]
        cores_placa, cores_fita = [], []
        ultimo_fan = None

        def escrever_fan(cor):
            nonlocal ultimo_fan
            ultimo_fan = (cor.red, cor.green, cor.blue)
            cores_placa.append(ultimo_fan)
            if ultimo_fan == primeira_cor:
                placa_pronta.set()

        fan = SimpleNamespace(id=0, set_color=escrever_fan)
        pch = SimpleNamespace(id=1, set_color=lambda cor: None)
        zonas = [SimpleNamespace(name="Addressable Header", leds=[fan]),
                 SimpleNamespace(name="PCH", leds=[pch])]
        placa = SimpleNamespace(name="ASRock teste", zones=zonas, leds=[fan, pch],
                                colors=[None, None], modes=[])
        cli = SimpleNamespace(devices=[placa], disconnect=lambda: None)
        fita = leds_lotus.LotusLantern(controles, estado, lambda _: 100, parar, lambda _: None)

        def abrir(_pasta):
            fita.proc = SimpleNamespace(poll=lambda: None)
            fita._endereco_selecionado = None
            fita._ultimo_envio = 0.0

        def escrever_fita(cor):
            cores_fita.append((cor, ultimo_fan))
            if cor == primeira_cor:
                fita_pronta.set()
            elif cor == ultima_cor:
                fita_final.set()

        with patch.object(fita, "_abrir", side_effect=abrir), \
             patch.object(fita, "_fechar"), \
             patch.object(fita, "_enviar", side_effect=escrever_fita), \
             patch.object(leds_lotus, "INTERVALO", 0.005), \
             patch.object(leds_lotus, "INTERVALO_SINCRONIA", 0.005), \
             patch.object(leds_lotus, "FADE", 0.05), \
             patch.object(leds_openrgb, "OpenRGBClient", return_value=cli), \
             patch.object(leds_openrgb, "_ler_perfil", return_value={}), \
             patch.object(leds_openrgb, "realcar", side_effect=lambda cor: cor), \
             patch.object(leds_openrgb, "FADE_PASSOS", 4), \
             patch.object(leds_openrgb, "INTERVALO_ZONAS", 0.04), \
             patch.object(leds_openrgb, "INTERVALO_ADDRESSABLE_EXTRA", 0), \
             patch.object(leds_openrgb, "CHECA_A_CADA", 0.005):
            pc = threading.Thread(target=leds_openrgb._laco,
                                  args=(estado, lambda _: 100, parar, lambda _: None),
                                  kwargs={"controles": controles}, daemon=True)
            pc.start()
            fita.iniciar()
            try:
                self.assertTrue(placa_pronta.wait(2))
                self.assertTrue(fita_pronta.wait(2))
                estado["ultima_midia"] = {"musica": ("Faixa 2", "Artista"), "cor_viva": ultima_cor}
                self.assertTrue(fita_final.wait(2))
            finally:
                parar.set()
                fita.fechar()
                pc.join(timeout=2)

        passos_fita = [(cor, fan) for cor, fan in cores_fita if cor != primeira_cor]
        self.assertEqual(passos_fita[-1], (ultima_cor, ultima_cor))
        self.assertEqual(passos_fita, [(cor, cor) for cor in cores_placa[1:]])
        self.assertEqual(len(passos_fita), 4)

    def test_dinamico_espelha_o_rgb_enviado_ao_header_nos_quatro_modos(self):
        fundo = (255, 30, 25)
        album = (10, 20, 30)
        album_pc = (20, 40, 60)
        for modo in MODOS:
            with self.subTest(modo=modo):
                controles = Controles()
                controles.aplicar({"modo": modo, "lotus_modo": "dinamico",
                                   "lotus_brilho": 15, "cor_fundo": fundo})
                self.assertEqual(controles.config["lotus_modo"], "dinamico")
                estado = {"midia_pronta": True, "modo_exibicao": modo}
                parar = threading.Event()
                fita = leds_lotus.LotusLantern(controles, estado, lambda _: 80, parar,
                                               lambda _: None)
                chave_header = "placa|Addressable Header"
                chave_pch = "placa|PCH"
                zona = SimpleNamespace(leds=[1])
                zonas = [(chave_pch, None, zona), (chave_header, None, zona)]
                perfil = {chave_header: [(100, 80, 60)], chave_pch: [(10, 20, 30)]}
                capturado = []

                def enviar(zonas, cores, escrito, etapa, passos, parar, log, registro, publicar):
                    publicar()
                    capturado.append((cores[chave_header][0], fita._cor_transicao(0, controles.config)))
                    return True

                def comparar(esperado):
                    capturado.clear()
                    with patch.object(leds_openrgb, "OpenRGBClient",
                                      return_value=SimpleNamespace(devices=[])), \
                         patch.object(leds_openrgb, "_zonas", return_value=zonas), \
                         patch.object(leds_openrgb, "_ler_perfil", return_value=perfil), \
                         patch.object(leds_openrgb, "realcar", return_value=album_pc), \
                         patch.object(leds_openrgb, "_enviar_etapa", side_effect=enviar):
                        leds_openrgb._laco(estado, lambda _: 80, parar, lambda _: None,
                                          controles=controles)
                    self.assertEqual(capturado, [(esperado, esperado)])

                comparar((204, 24, 20))
                estado.update(musica=("Faixa", "Artista"), cor_viva=album, tocando=True)
                comparar((204, 24, 20) if modo == "video" else (16, 32, 48))
                controles.aplicar({"cor_fundo": None})
                estado.update(musica=None, tocando=False)
                comparar((80, 64, 48))

    def test_fita_retoma_fade_local_quando_openrgb_sai(self):
        config = {"tela_ligada": True, "cor_fundo": (200, 200, 200)}
        estado = {"modo_exibicao": "video"}
        fita = leds_lotus.LotusLantern(SimpleNamespace(config=config), estado, lambda _: 100,
                                       threading.Event(), lambda _: None)
        fita._base_cor = (20, 20, 20)
        estado[CHAVE_PASSO] = PassoRGB(ReferenciaRGB(None, config["cor_fundo"], 100), object(), 0.5)

        fita._base_cor = fita._cor_transicao(0, config)
        self.assertEqual(fita._base_cor, (110, 110, 110))
        self.assertEqual(fita._cor_transicao(1, config), fita._base_cor)

        estado.pop(CHAVE_PASSO)
        self.assertEqual(fita._cor_transicao(1, config), fita._base_cor)
        self.assertEqual(fita._cor_transicao(3, config), (200, 200, 200))

    def test_dinamico_mantem_a_cor_do_fundo_sem_openrgb(self):
        config = {"tela_ligada": True, "lotus_modo": "dinamico",
                  "lotus_brilho": 10, "cor_fundo": (40, 80, 120)}
        estado = {"modo_exibicao": "dinamico"}
        fita = leds_lotus.LotusLantern(SimpleNamespace(config=config), estado, lambda _: 50,
                                       threading.Event(), lambda _: None)
        self.assertEqual(fita._cor_transicao(0, config), (20, 40, 60))

    def test_modo_manual_conclui_sem_aguardar_openrgb(self):
        config = {"tela_ligada": True, "lotus_modo": "manual", "lotus_cor": (10, 30, 50),
                  "lotus_brilho": 50}
        estado = {CHAVE_PASSO: PassoRGB(ReferenciaRGB(None, (200, 200, 200), 100),
                                        object(), 0.5, (100, 100, 100))}
        fita = leds_lotus.LotusLantern(SimpleNamespace(config=config), estado, lambda _: 100,
                                       threading.Event(), lambda _: None)
        fita._base_cor = fita._cor_transicao(0, config)
        self.assertEqual(fita._base_cor, (5, 15, 25))

        config["lotus_cor"] = (80, 120, 160)
        self.assertEqual(fita._cor_transicao(1, config), fita._base_cor)
        self.assertEqual(fita._cor_transicao(1.4, config), (40, 60, 80))


if __name__ == "__main__":
    unittest.main()
