import random
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from modo_clipe import FonteClipe, QuadrosClipe, referencias_de_audio
from sincronia_audio_clipe import AjustePorAudio, PASSO, TrechoEdicao, mapear_edicao, trecho_da_edicao


class EdicaoClipeTest(unittest.TestCase):
    def setUp(self):
        self.musica = ("Faixa", "Artista")
        self.chave = (self.musica, 1)
        self.snap = {"musica": self.musica, "reproducao_id": 1, "tocando": True}
        rng = random.Random(769)
        self.audio = [rng.getrandbits(32) for _ in range(1500)]
        self.clipe = self.audio[140:700] + self.audio[750:]

    def test_mapeia_dois_trechos_separados_por_corte_sem_identificar_a_musica(self):
        edicao = mapear_edicao(self.audio, self.clipe, lambda: True)
        self.assertEqual(len(edicao), 2)
        for instante, esperado in ((5, None), (30, -140 * PASSO), (60, -140 * PASSO),
                                   (90, None), (105, -190 * PASSO), (150, -190 * PASSO)):
            trecho = trecho_da_edicao(edicao, instante)
            if esperado is None:
                self.assertIsNone(trecho)
            else:
                self.assertAlmostEqual(trecho.deslocamento, esperado, delta=PASSO)

    def test_referencia_so_entra_em_uso_quando_o_audio_do_spotify_confirma(self):
        ajuste = AjustePorAudio(Path("clipe.mp4"), Mock())
        mapa = mapear_edicao(self.audio, self.clipe, lambda: True)
        ajuste._mapa_referencia = mapa, self.audio
        ajuste._confirmar_referencia(self.audio[20:95], 450 * PASSO)
        self.assertEqual(ajuste.edicao, ())
        ajuste._confirmar_referencia(self.audio[450:525], 445 * PASSO)
        self.assertAlmostEqual(ajuste.edicao[0].deslocamento, -135 * PASSO, delta=PASSO)

    def test_mapeia_diferenca_de_velocidade(self):
        taxa = 0.99
        clipe = [self.audio[round(i / taxa)] for i in range(1480)]
        edicao = mapear_edicao(self.audio, clipe, lambda: True)
        trecho = trecho_da_edicao(edicao, 120)
        self.assertAlmostEqual(trecho.taxa, taxa, delta=0.003)
        self.assertAlmostEqual(trecho.posicao_video(120), 120 * taxa, delta=0.18)

    def test_referencia_da_mesma_versao_tem_preferencia(self):
        def entrada(id_audio, titulo, canal="Artista", verificacao=True):
            return {"id": id_audio, "title": titulo, "channel": canal,
                    "duration": 200, "channel_is_verified": verificacao}

        entradas = [
            entrada("aaaaaaaaaaa", "Faixa (Official Video)"),
            entrada("bbbbbbbbbbb", "Faixa", "Outro Artista"),
            entrada("ccccccccccc", "Faixa (2025 Remix)", "Artista - Topic", False),
            entrada("ddddddddddd", "Artista - Faixa (2025 Remix) (Official Audio)"),
            entrada("eeeeeeeeeee", "Faixa"),
        ]
        self.assertEqual(referencias_de_audio(entradas, ("Faixa - 2025 Remix", "Artista"), 200,
                                              "aaaaaaaaaaa"), ["ccccccccccc", "ddddddddddd"])

    def test_corte_interno_conserva_ultimo_quadro_e_pausa_libera_o_vinil(self):
        fonte = FonteClipe(lambda: self.snap, lambda: True, lambda _: None, lambda _: None)
        tela = object()
        fonte._edicao_ativa = self.chave, (TrechoEdicao(10, 50, -10), TrechoEdicao(56, 90, -16))
        fonte._ultima_posicao = self.chave, (49.95, 100.0)
        fonte._ultimo_quadro = QuadrosClipe(), tela
        with patch("modo_clipe.time") as relogio:
            relogio.monotonic.side_effect = [100.1, 103.5, 106.1]
            self.assertIs(fonte.quadro(dict(self.snap, pos=50.05, t_poll=100.1), 100.1), tela)
            self.assertIs(fonte.quadro(dict(self.snap, pos=53.45, t_poll=103.5), 103.5), tela)
            self.assertIs(fonte.quadro(dict(self.snap, pos=56.05, t_poll=106.1), 106.1), tela)
            self.assertIsNone(fonte.quadro(dict(self.snap, tocando=False), 106.2))

    def test_adiantamento_muda_quadro_e_preserva_relogio_da_captura(self):
        snap = dict(self.snap, pos=30, t_poll=100)
        fonte = FonteClipe(lambda: snap, lambda: True, Mock(), Mock(), lambda: 1500)
        quadros = QuadrosClipe()
        fonte._fluxo = self.chave, quadros
        para_exibir = object()
        for pos, imagem in [(30, object()), (31.5, para_exibir)]:
            quadros.adicionar(pos, imagem, threading.Event())
        with patch("modo_clipe.time.monotonic", return_value=100):
            self.assertIs(fonte.quadro(snap, 100), para_exibir)
        self.assertEqual(fonte._posicao_no_instante(100), 30)
