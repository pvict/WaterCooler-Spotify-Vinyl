import time
import unittest

from controle_interface import validar
from modo_clipe import FonteClipe, escolher_clipe, posicao_atual


def entrada(id, titulo, canal="Artista", duracao=200):
    return {"id": id, "title": titulo, "channel": canal, "duration": duracao}


class ModoClipeTest(unittest.TestCase):
    def test_modo_e_salvo_pela_configuracao(self):
        self.assertEqual(validar({"modo": "clipe"})["modo"], "clipe")

    def test_prioriza_clipe_e_rejeita_outras_versoes(self):
        musica = ("Coração - 2015 Remaster", "Artista")
        candidatos = [
            entrada("bbbbbbbbbbb", "Artista - Coração (Official Lyric Video)"),
            entrada("ccccccccccc", "Artista - Coração (Ao Vivo)"),
            entrada("ddddddddddd", "Artista - Coração (Official Video)", duracao=540),
            entrada("eeeeeeeeeee", "Outro - Coração (Official Video)", "Outro"),
            entrada("aaaaaaaaaaa", "Artista - Coracao (Clipe Oficial)", duracao=208),
        ]
        self.assertEqual(escolher_clipe(candidatos, musica, 200), "aaaaaaaaaaa")
        self.assertIsNone(escolher_clipe(candidatos[:-1], musica, 200))

    def test_nao_troca_versao_ao_vivo_pela_de_estudio(self):
        candidatos = [entrada("aaaaaaaaaaa", "Artista - Coração (Official Video)")]
        self.assertIsNone(escolher_clipe(candidatos, ("Coração - Ao Vivo", "Artista"), 200))

    def test_preserva_parenteses_no_titulo_e_live_no_nome_da_banda(self):
        candidatos = [
            entrada("aaaaaaaaaaa", "The Rolling Stones - (I Can't Get No) Satisfaction (Official Video)",
                    "The Rolling Stones"),
            entrada("bbbbbbbbbbb", "Live - Lightning Crashes (Official Music Video)", "Live"),
        ]
        self.assertEqual(escolher_clipe(candidatos, ("(I Can’t Get No) Satisfaction", "The Rolling Stones"),
                                       200), "aaaaaaaaaaa")
        self.assertEqual(escolher_clipe(candidatos, ("Lightning Crashes", "Live"), 200), "bbbbbbbbbbb")

    def test_nao_escolhe_publicacao_so_de_audio(self):
        candidatos = [entrada("aaaaaaaaaaa", "Artista - Coração (Audio)"),
                      entrada("bbbbbbbbbbb", "Artista - Coração")]
        self.assertIsNone(escolher_clipe(candidatos, ("Coração", "Artista"), 200))

    def test_posicao_avanca_so_quando_tocando(self):
        snap = {"pos": 12, "t_poll": 10, "tocando": True}
        self.assertEqual(posicao_atual(snap, 10.5), 12.5)
        self.assertEqual(posicao_atual(snap, 22), 13)
        snap["tocando"] = False
        self.assertEqual(posicao_atual(snap, 22), 12)

    def test_descarta_quadro_antigo_apos_troca_ou_seek(self):
        musica = ("Título", "Artista")
        snap = {"musica": musica, "reproducao_id": 10, "tocando": True,
                "pos": 12, "t_poll": time.monotonic()}
        imagem = object()
        fonte = FonteClipe(lambda: snap, lambda: True, lambda _: None, lambda _: None)
        fonte._ultimo_quadro = ((musica, 10), 12, imagem)
        self.assertIs(fonte.quadro(snap), imagem)
        self.assertIsNone(fonte.quadro(dict(snap, reproducao_id=11)))
        self.assertIsNone(fonte.quadro(dict(snap, pos=50)))
        self.assertIsNone(fonte.quadro(dict(snap, tocando=False)))


if __name__ == "__main__":
    unittest.main()
