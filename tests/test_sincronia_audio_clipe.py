from pathlib import Path
import random
import struct
from unittest.mock import Mock, patch
import unittest

from sincronia_audio_clipe import AjustePorAudio, PASSO, localizar_audio


class SincroniaPorAudioTest(unittest.TestCase):
    def setUp(self):
        self.random = random.Random(526)
        self.video = [self.random.getrandbits(32) for _ in range(800)]

    def test_compensa_introducao_mapeando_a_amostra_da_musica(self):
        inicio_clipe = 320
        inicio_spotify = 120 * PASSO
        amostra = [x ^ self.random.getrandbits(4) for x in self.video[320:393]]
        ajuste = localizar_audio(amostra, self.video, inicio_spotify)
        self.assertIsNotNone(ajuste)
        self.assertAlmostEqual(ajuste, inicio_clipe * PASSO - inicio_spotify, delta=PASSO / 2)

    def test_permite_abrir_o_clipe_antes_do_tempo_do_spotify(self):
        amostra = self.video[400:473]
        ajuste = localizar_audio(amostra, self.video, 470 * PASSO)
        self.assertAlmostEqual(ajuste, -70 * PASSO, delta=PASSO / 2)

    def test_descarta_refrao_repetido_em_dois_pontos(self):
        amostra = self.video[160:233]
        self.video[320:393] = amostra
        self.assertIsNone(localizar_audio(amostra, self.video, 175 * PASSO))

    def test_descarta_gravacao_sem_correspondencia_e_amostra_curta(self):
        amostra = [self.random.getrandbits(32) for _ in range(73)]
        self.assertIsNone(localizar_audio(amostra, self.video, 250 * PASSO))
        self.assertIsNone(localizar_audio(self.video[:10], self.video, 0))


    def test_busca_o_audio_se_o_arquivo_do_clipe_so_contem_video(self):
        fonte = Mock(return_value=Path("audio.webm"))
        ajustar = AjustePorAudio(Path("clipe.mp4"), Mock(), fonte)
        posicao = 120 * PASSO
        amostra = self.video[320:393]
        bruto = struct.pack("<d", 100) + b"pcm"
        with patch("sincronia_audio_clipe.impressao_audio", side_effect=[(), self.video, amostra]), \
             patch("sincronia_audio_clipe._executar", side_effect=[b"", bruto]):
            ajustar._medir(lambda: True, lambda _: posicao)
        fonte.assert_called_once()
        self.assertAlmostEqual(ajustar.deslocamento, 200 * PASSO, delta=PASSO / 2)
