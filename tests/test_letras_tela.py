import datetime
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

import tela_completa as tela
from controle_interface import status_letras_atual
from letras_sincronizadas import FaixaLetra, ResultadoLetra, ler_lrc
from letras_visual import fundo_da_letra


class TestPausaSpotify(unittest.IsolatedAsyncioTestCase):
    async def test_app_aberto_com_spotify_ja_pausado_nao_reacende_a_tela(self):
        snap = dict(tela.estado)
        parar = threading.Event()
        controles = Mock()
        controles.modo_efetivo.return_value = "clipe"
        status = tela.StatusReproducao
        sessao = Mock(source_app_user_model_id="Spotify")
        sessao.get_playback_info.side_effect = [
            SimpleNamespace(playback_status=valor)
            for valor in (status.PAUSED, status.PLAYING, status.PAUSED, status.PAUSED, status.STOPPED)
        ]
        sessao.try_get_media_properties_async = AsyncMock(return_value=SimpleNamespace(
            title="Faixa", artist="Artista", album_title="Álbum", thumbnail=None))
        sessao.get_timeline_properties.return_value = SimpleNamespace(
            start_time=datetime.timedelta(), end_time=datetime.timedelta(seconds=180),
            position=datetime.timedelta(seconds=5),
            last_updated_time=datetime.datetime.now(datetime.timezone.utc))
        gerenciador = Mock()
        gerenciador.get_sessions.side_effect = [[sessao]] * 5 + [[]]
        estados = []

        async def avancar(atraso):
            if atraso == 0.15:
                estados.append((snap["tocando"], snap["spotify_inativo_desde"]))
                if len(estados) == 6:
                    parar.set()

        with (patch.object(tela, "MediaManager", Mock(request_async=AsyncMock(return_value=gerenciador))),
              patch.object(tela, "controles", controles), patch.object(tela, "estado", snap),
              patch.object(tela, "parar", parar), patch.object(tela.asyncio, "sleep", avancar)):
            await tela.vigiar_spotify()
        pausa = estados[2][1]
        self.assertIsNotNone(pausa)
        self.assertEqual(estados, [(False, None), (True, None), (False, pausa), (False, pausa),
                                   (False, None), (False, None)])


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

    def quadro(self, tempo, *, tempo_real=None):
        tempo_real = tempo if tempo_real is None else tempo_real
        with (patch.object(tela, "estado", self.snap),
              patch.object(tela.controles, "config", self.config),
              patch.object(tela.time, "monotonic", return_value=tempo_real)):
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
        self.snap.update(tocando=False, pos=2.7, spotify_inativo_desde=110)
        self.painel.p = 1.0
        depois = self.quadro(120)
        assert self.quadro(132) is depois
        assert status_letras_atual(self.config, self.snap) == "exibindo"
        self.snap.update(pos=0.2, t_poll=133)
        assert self.quadro(133).tobytes() != depois.tobytes()

    def test_pausa_longa_apaga_o_quadro_e_o_brilho_usb_ate_retomar(self):
        self.config["modo"] = "clipe"
        self.clipe.quadro.return_value = None
        self.config["brilho"] = 65
        self.painel.brilho = 65
        self.painel.pipe = Mock()
        executar = self.painel.pipe.env.executar
        self.snap.update(tocando=False, pos=2.7, spotify_inativo_desde=100.0)
        self.painel.p = 1.0

        with (patch.object(tela, "log"),
              patch.object(tela.operations, "send_brightness_command") as enviar):
            assert self.quadro(219.9).getbbox() is not None
            executar.assert_not_called()
            assert self.quadro(210.0, tempo_real=220.0).getbbox() is None
            assert self.snap["status_letras"] is None
            executar.call_args.args[0]("usb")
            enviar.assert_called_once_with("usb", 0)

            self.snap.update(tocando=True, t_poll=220.1, spotify_inativo_desde=None)
            assert self.quadro(220.1).getbbox() is not None
            executar.call_args.args[0]("usb")
            enviar.assert_called_with("usb", 65)
            assert executar.call_count == 2

    def test_sem_sessao_nao_exibe_vinil_ou_ultimo_clipe(self):
        self.snap.update(musica=None, tocando=False)
        for modo in ("spotify", "clipe"):
            with self.subTest(modo=modo):
                self.config["modo"] = modo
                assert self.quadro(100.0).getbbox() is None
        self.clipe.quadro.assert_not_called()

    def test_modos_de_fundo_acendem_so_enquanto_recebem_quadros(self):
        self.usar_composicao_real()
        self.snap.update(musica=None, tocando=False, spotify_inativo_desde=100.0)
        for modo in ("dinamico", "video"):
            with self.subTest(modo=modo):
                self.config["modo"] = modo
                self.painel.fundo_ocioso = SimpleNamespace(proximo=lambda: self.quadro_normal)
                assert self.quadro(220.0) is self.quadro_normal
                self.painel.fundo_ocioso = SimpleNamespace(proximo=lambda: None)
                assert self.quadro(221.0).getbbox() is None
                self.painel.fundo_ocioso = SimpleNamespace(proximo=lambda: self.quadro_normal)
                assert self.quadro(222.0) is self.quadro_normal

    def test_retomada_respeita_tela_desligada(self):
        self.snap.update(tocando=True, spotify_inativo_desde=None)
        self.config["tela_ligada"] = False
        assert self.quadro(220.1).getbbox() is None

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

    def test_legenda_respeita_o_ajuste_sem_mudar_a_posicao_do_video(self):
        self.config.update(modo="clipe", adiantar_clipe_ms=1250, adiantar_letras_ms=-500)
        self.clipe.posicao_letra.return_value = 2.49
        assert self.quadro(100) is self.clip_frame

        self.config["adiantar_letras_ms"] = 0
        assert self.quadro(100) is not self.clip_frame
        assert self.config["adiantar_clipe_ms"] == 1250

    def test_lyric_video_preserva_letras_embutidas_sem_sobrepor_legenda(self):
        self.config["modo"] = "clipe"
        self.clipe.letra_no_video = True
        assert self.quadro(100.2) is self.clip_frame
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

    def test_letra_em_tela_cheia_aplica_o_adiantamento_configurado(self):
        self.clipe.quadro.return_value = None
        self.config["adiantar_letras_ms"] = -1250
        for modo in ("spotify", "dinamico", "clipe"):
            with self.subTest(modo=modo):
                self.usar_composicao_real()
                self.config["modo"] = modo
                with patch.object(self.painel.visual_letras, "spotify", return_value=self.quadro_normal) as render:
                    self.quadro(100)
                    render.assert_called_once_with(self.capa, self.letra, 0.25)

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
