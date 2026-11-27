import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageChops, ImageDraw

import tela_completa as tela


class AvisosEPausaTest(unittest.TestCase):
    def test_nome_longo_mostra_o_inicio_antes_de_correr_e_para_no_fim(self):
        pos = tela.deslocamento_playlist
        self.assertEqual(pos(550, 0.0), -25)
        self.assertEqual(pos(550, 0.8), -25)
        self.assertGreater(pos(550, 2.0), -25)
        self.assertEqual(pos(550, 60.0), 335)

    def test_aviso_longo_dura_o_suficiente_para_mostrar_o_final(self):
        painel = tela.Painel(None, None, 100)
        painel._iniciar_aviso_playlist("Minha playlist de músicas antigas e clássicos " * 2, 100)
        self.assertGreater(painel._mensagem_playlist_ate - 100, tela.PLAYLIST_DURACAO)
        painel._iniciar_aviso_playlist("Jazz", 110)
        self.assertEqual(painel._mensagem_playlist_ate, 110 + tela.PLAYLIST_DURACAO)

    def test_inicio_renderizado_fica_estavel_dentro_da_tela_redonda(self):
        snap = dict(musica=("Minha playlist favorita de rock brasileiro e clássicos", ""),
                    cor_viva=(0, 0, 0, 255), modo_playlist_texto=True,
                    rotulo_aviso=tela.ROTULO_PLAYLIST, inicio_leitura_texto=100)
        area = (0, 423, 480, 480)
        moldura = Image.new("L", (480, 480))
        ImageDraw.Draw(moldura).ellipse((0, 0, 479, 479), fill=255)

        def linha(agora):
            imagem = tela.renderizar(dict(snap, tempo_visual=agora)).crop(area)
            return imagem.getchannel("A").point(lambda a: 255 if a > 192 else 0)

        comeco = linha(100)
        self.assertEqual(comeco.tobytes(), linha(100.8).tobytes())
        self.assertNotEqual(comeco.tobytes(), linha(102).tobytes())
        x = 240 - tela.PLAYLIST_LARGURA // 2 + tela.MARQUEE_MARGEM
        primeiras_letras = (x, 0, x + 55, comeco.height)
        prefixo = comeco.crop(primeiras_letras)
        visivel = ImageChops.multiply(comeco, moldura.crop(area)).crop(primeiras_letras)
        self.assertIsNotNone(prefixo.getbbox())
        self.assertEqual(prefixo.tobytes(), visivel.tobytes())

    def test_titulo_curto_e_artista_ficam_fixos_durante_a_faixa(self):
        painel = tela.Painel(None, None, 100)
        areas = ((185, 390, 295, 430), (185, 430, 295, 465))
        faixa = dict(musica=("Sol", "Cantor"), tocando=True, dur=600)

        def posicoes(agora):
            snap = dict(faixa, pos=agora, t_poll=agora)
            quadro = painel._painel(snap, agora, 1 / 30, tocando=True)
            alfa = quadro.getchannel("A").point(lambda valor: 255 if valor > 192 else 0)
            return [alfa.crop(area).getbbox() for area in areas]

        inicio = posicoes(0)
        self.assertTrue(all(inicio))
        for agora in (2, 6, 59.9, 60, 120, 180, 240):
            with self.subTest(agora=agora):
                self.assertEqual(posicoes(agora), inicio)

    def test_titulo_e_artista_permanecem_visiveis_durante_a_faixa(self):
        areas = ((185, 390, 295, 430), (185, 430, 295, 465))

        def opacidades(posicao):
            quadro = tela.renderizar(dict(musica=("Sol", "Cantor"), cor_capa=None,
                                          tempo_visual=posicao))
            return [quadro.crop(area).getchannel("A").getextrema()[1] for area in areas]

        for tempo in (0, 20, 20.5, 21, 120, 299.9, 300, 300.5, 310, 601, 610):
            with self.subTest(tempo=tempo):
                self.assertEqual(opacidades(tempo), [255, 255])

    def test_painel_mantem_texto_no_vinil_e_atualiza_na_troca_de_faixa(self):
        for modo in ("dinamico", "spotify", "clipe"):
            with self.subTest(modo=modo):
                painel = tela.Painel(None, None, 100)
                painel.p = 1.0
                painel._fundo = Mock(return_value=Image.new("RGB", (480, 480), "black"))
                config = dict(tela.controles.config, modo=modo, tela_ligada=True)
                snap = dict(tela.estado, musica=("Sol", "Cantor"), tocando=True,
                            cor_viva=(0, 0, 0, 255), pos=19.5, t_poll=100.0, dur=180,
                            spotify_inativo_desde=None, ultima_midia=None)

                def mostra_texto(agora):
                    with patch.object(tela.time, "monotonic", return_value=agora):
                        quadro = painel.quadro(agora)
                    vermelho = quadro.getchannel("R")
                    return all(vermelho.crop(area).getextrema()[1] >= 200
                               for area in ((185, 390, 295, 430), (185, 430, 295, 465)))

                with patch.object(tela, "estado", snap), patch.object(tela.controles, "config", config):
                    self.assertTrue(mostra_texto(100.0))
                    snap.update(pos=60.0, t_poll=101.0)
                    self.assertTrue(mostra_texto(101.0))
                    snap.update(musica=("Lua", "Cantora"), pos=0.0, t_poll=102.0)
                    self.assertTrue(mostra_texto(102.0))
                    self.assertTrue(mostra_texto(102.0 + tela.TEXTO_SAI + tela.TEXTO_ENTRA + 0.1))
                    self.assertEqual(painel.titulo, ("Lua", "Cantora"))

    def test_fundo_do_vinil_para_de_consumir_quadros_ao_pausar_no_clipe(self):
        class Fundo:
            lidos = 0

            def proximo(self):
                self.lidos += 1
                return Image.new("RGB", (480, 480), (self.lidos, 0, 0))

        fundo = Fundo()
        with patch.object(tela.controles, "config", {"modo": "clipe"}):
            painel = tela.Painel(fundo, None, 100)
            painel._girar_fundo(1, 1 / 30, False)
            quadro = painel._fundo(1.0)
            for i in range(1, 5):
                painel._girar_fundo(1 + i / 30, 1 / 30, False)
                self.assertEqual(painel._fundo(1.0).getpixel((240, 240)), quadro.getpixel((240, 240)))
        self.assertEqual(fundo.lidos, 2)


if __name__ == "__main__":
    unittest.main()
