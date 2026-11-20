import unittest
from pathlib import Path
from unittest.mock import patch

from modo_clipe import FonteClipe, QuadrosClipe
from sincronia_audio_clipe import edicao_medida, trecho_da_edicao


class EdicaoThankYouTest(unittest.TestCase):
    def setUp(self):
        self.musica = ("Thank You", "Dido")
        self.chave = (self.musica, 1)
        self.snap = {"musica": self.musica, "reproducao_id": 1, "tocando": True,
                     "dur": 212.36, "pedido_capa_album": {"musica": self.musica, "album": "No Angel"}}
        self.arquivo = Path("clipe.1TO48Cnl66w.mp4")

    def test_os_dois_deslocamentos_ja_estao_disponiveis_ao_reposicionar(self):
        edicao = edicao_medida(self.arquivo, self.snap)
        for instante, esperado in ((5, None), (20, -17.40), (120, -17.40), (128, None),
                                   (132, -23.40), (205, -23.40)):
            trecho = trecho_da_edicao(edicao, instante)
            self.assertEqual(trecho.deslocamento if trecho else None, esperado)

    def test_edicao_medida_so_se_aplica_a_gravacao_e_ao_clipe_verificados(self):
        self.assertEqual(edicao_medida(Path("clipe.OutroVideo.mp4"), self.snap), ())
        self.assertEqual(edicao_medida(self.arquivo, dict(self.snap, dur=195)), ())
        self.assertEqual(edicao_medida(self.arquivo, dict(self.snap, pedido_capa_album=None)), ())
        outro = {"musica": self.musica, "album": "Outra edição"}
        self.assertEqual(edicao_medida(self.arquivo, dict(self.snap, pedido_capa_album=outro)), ())

    def test_corte_interno_conserva_ultimo_quadro_e_pausa_libera_o_vinil(self):
        fonte = FonteClipe(lambda: self.snap, lambda: True, lambda _: None, lambda _: None)
        tela = object()
        fonte._edicao_ativa = self.chave, edicao_medida(self.arquivo, self.snap)
        fonte._ultima_posicao = self.chave, (125.53, 100.0)
        fonte._ultimo_quadro = QuadrosClipe(), tela
        with patch("modo_clipe.time") as relogio:
            relogio.monotonic.side_effect = [100.1, 103.5, 106.1]
            self.assertIs(fonte.quadro(dict(self.snap, pos=125.63, t_poll=100.1), 100.1), tela)
            self.assertIs(fonte.quadro(dict(self.snap, pos=129.03, t_poll=103.5), 103.5), tela)
            self.assertIs(fonte.quadro(dict(self.snap, pos=131.63, t_poll=106.1), 106.1), tela)
            self.assertIsNone(fonte.quadro(dict(self.snap, tocando=False), 106.2))
