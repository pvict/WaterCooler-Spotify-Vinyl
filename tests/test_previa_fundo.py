import queue
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops, ImageStat
from ajustes_fundo import AJUSTES_PADRAO
from controle_interface import validar
from fundo_usuario import ConversorFundo
from previa_fundo import PreviaFundo


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg necessário")
class PreviaFundoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.pasta = Path(self.temp.name)
        self.origem = self.pasta / "vídeo original com espaços.mp4"
        imagem = Image.new("RGB", (640, 360), (40, 100, 180))
        imagem.paste((220, 60, 20), (0, 0, 320, 360))
        comando = ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                   "-s", "640x360", "-r", "1", "-i", "pipe:0", "-frames:v", "1",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", str(self.origem)]
        subprocess.run(comando, input=imagem.tobytes(), capture_output=True, check=True,
                       timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.previa = PreviaFundo()

    def tearDown(self):
        self.previa.fechar()
        self.previa.thread.join(timeout=4)
        self.assertFalse(self.previa.thread.is_alive())
        self.temp.cleanup()

    def quadro(self, revisao, timeout=10):
        fim = time.monotonic() + timeout
        while time.monotonic() < fim:
            try:
                seq, imagem = self.previa.quadros.get(timeout=0.2)
                if seq == revisao:
                    return imagem
            except queue.Empty:
                pass
            if not self.previa.eventos.empty():
                self.fail(self.previa.eventos.get()[1])
        self.fail("Prévia não produziu quadro")

    def test_mudar_ajustes_descarta_quadro_antigo_e_mantem_fila_limitada(self):
        revisao = self.previa.solicitar(self.origem, AJUSTES_PADRAO)
        self.assertEqual(self.quadro(revisao).size, (360, 360))
        revisao = self.previa.solicitar(self.origem, {"brilho": 0})
        self.assertEqual(self.quadro(revisao).getpixel((180, 180)), (0, 0, 0))
        self.assertLessEqual(self.previa.quadros.qsize(), 1)

    def test_erro_de_abertura_preserva_detalhe_do_ffprobe(self):
        revisao = self.previa.solicitar(self.pasta / "missing.mp4", AJUSTES_PADRAO)
        seq, erro = self.previa.eventos.get(timeout=5)
        self.assertEqual(seq, revisao)
        self.assertIn("missing.mp4", erro)
        self.assertTrue(self.previa.quadros.empty())

    def test_previa_corresponde_ao_video_aplicado_sem_alterar_original(self):
        ajustes = dict(AJUSTES_PADRAO, zoom=140, x=35, brilho=80, saturacao=125, nitidez=120)
        original = self.origem.read_bytes()
        revisao = self.previa.solicitar(self.origem, ajustes)
        visto = self.quadro(revisao)
        conversor = ConversorFundo()
        with patch("fundo_usuario.PASTA_FUNDOS", self.pasta / "fundos"):
            self.assertTrue(conversor.iniciar(self.origem, ajustes))
            fim = time.monotonic() + 15
            while time.monotonic() < fim:
                tipo, dados = conversor.eventos.get(timeout=10)
                if tipo != "progresso":
                    break
            self.assertEqual(tipo, "pronto", str(dados))
        self.assertEqual(dados["origem"], str(self.origem))
        self.assertEqual(dados["ajustes"], ajustes)
        self.assertEqual(self.origem.read_bytes(), original)
        resultado = subprocess.run(["ffmpeg", "-v", "error", "-i", dados["caminho"],
            "-vf", "scale=360:360", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            capture_output=True, check=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        aplicado = Image.frombytes("RGB", (360, 360), resultado.stdout)
        # Margem de poucos níveis para a compressão H.264/YUV420.
        self.assertLess(max(ImageStat.Stat(ImageChops.difference(visto, aplicado)).mean), 5)
        preferencias = validar({"video_origem": str(self.origem), "video_ajustes": ajustes})
        self.assertEqual(preferencias["video_ajustes"], ajustes)


if __name__ == "__main__":
    unittest.main()
