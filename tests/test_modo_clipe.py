import shutil
import subprocess
import threading
import time
import unittest

from controle_interface import validar
from modo_clipe import FILTRO_CLIPE, FPS_CLIPE, FonteClipe, QuadrosClipe, escolher_clipe, posicao_atual


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
        quadros = QuadrosClipe()
        quadros.adicionar(12, imagem, threading.Event())
        fonte._fluxo = ((musica, 10), quadros)
        self.assertIs(fonte.quadro(snap, 100), imagem)
        self.assertIsNone(fonte.quadro(dict(snap, reproducao_id=11), 100))
        self.assertIsNone(fonte.quadro(dict(snap, pos=50), 100))
        self.assertIsNone(fonte.quadro(dict(snap, tocando=False), 100))

    def test_avanca_um_quadro_por_tick_apos_atraso_do_usb(self):
        quadros = QuadrosClipe()
        cancelar = threading.Event()
        for i in range(6):
            quadros.adicionar(60 + i / FPS_CLIPE, i, cancelar)
        self.assertEqual(quadros.quadro(60, 100), 0)
        self.assertEqual(quadros.quadro(60.20, 100 + 1 / FPS_CLIPE), 1)
        self.assertEqual(quadros.quadro(60.21, 100 + 2 / FPS_CLIPE), 2)
        self.assertEqual(quadros.quadro(60.22, 100 + 3 / FPS_CLIPE), 3)

    def test_consumir_ultimos_quadros_antes_de_voltar_ao_vinil(self):
        quadros = QuadrosClipe()
        quadros.adicionar(60, "primeiro", threading.Event())
        quadros.adicionar(60 + 1 / FPS_CLIPE, "último", threading.Event())
        quadros.fim_leitura.set()
        self.assertEqual(quadros.quadro(60, 100), "primeiro")
        self.assertEqual(quadros.quadro(60 + 1 / FPS_CLIPE, 100 + 1 / FPS_CLIPE), "último")
        self.assertIsNone(quadros.quadro(60.1, 100.1))
        self.assertTrue(quadros.esgotado.is_set())

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg não instalado")
    def test_ffmpeg_recorta_paisagem_e_retrato_sem_barras(self):
        for w, h in [(640, 360), (360, 640)]:
            barras = ("drawbox=0:0:100:ih:red:t=fill,drawbox=iw-100:0:100:ih:red:t=fill"
                      if w > h else "drawbox=0:0:iw:100:red:t=fill,drawbox=0:ih-100:iw:100:red:t=fill")
            proc = subprocess.run(
                ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                 f"color=blue:s={w}x{h}:r=30,{barras}", "-vf", FILTRO_CLIPE, "-frames:v", "1",
                 "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                capture_output=True, check=True, timeout=10)
            self.assertEqual(len(proc.stdout), 480 * 480 * 3)
            for i in (0, (480 * 240 + 240) * 3, len(proc.stdout) - 3):
                r, g, b = proc.stdout[i:i + 3]
                self.assertLess(max(r, g), 5)
                self.assertGreater(b, 230)


if __name__ == "__main__":
    unittest.main()
