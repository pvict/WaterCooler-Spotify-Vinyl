import shutil
import subprocess
import unittest
from PIL import Image

from ajustes_fundo import AJUSTES_PADRAO, validar_ajustes, filtro_ajustes, posicao_arraste


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg necessário")
class AjustesFundoTests(unittest.TestCase):
    def quadro(self, imagem, **ajustes):
        comando = ["ffmpeg", "-v", "error", "-filter_threads", "1", "-f", "rawvideo",
                   "-pix_fmt", "rgb24", "-s", f"{imagem.width}x{imagem.height}", "-i", "pipe:0",
                   "-vf", filtro_ajustes(ajustes), "-frames:v", "1", "-f", "rawvideo",
                   "-pix_fmt", "rgb24", "pipe:1"]
        resultado = subprocess.run(comando, input=imagem.tobytes(), capture_output=True,
                                   timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(resultado.returncode, 0, resultado.stderr.decode(errors="replace"))
        return Image.frombytes("RGB", (480, 480), resultado.stdout)

    def test_posicao_e_zoom_preservam_enquadramento(self):
        imagem = Image.new("RGB", (960, 480), "red")
        imagem.paste("blue", (480, 0, 960, 480))
        self.assertEqual(self.quadro(imagem, x=0).getpixel((240, 240)), (255, 0, 0))
        self.assertEqual(self.quadro(imagem, x=100).getpixel((240, 240)), (0, 0, 255))
        pequena = self.quadro(Image.new("RGB", (480, 480), "red"), zoom=50)
        self.assertEqual(pequena.getpixel((5, 5)), (0, 0, 0))
        self.assertEqual(pequena.getpixel((240, 240)), (255, 0, 0))

    def test_brilho_saturacao_e_nitidez(self):
        imagem = Image.new("RGB", (480, 480), (100, 30, 20))
        self.assertEqual(self.quadro(imagem, brilho=0).getpixel((240, 240)), (0, 0, 0))
        cinza = self.quadro(imagem, saturacao=0).getpixel((240, 240))
        self.assertLessEqual(max(cinza) - min(cinza), 1)
        borda = Image.new("RGB", (480, 480), (80, 80, 80))
        borda.paste((160, 160, 160), (240, 0, 480, 480))
        nitida = self.quadro(borda, nitidez=160)
        suave = self.quadro(borda, nitidez=0)
        self.assertGreater(nitida.getpixel((240, 240))[0], 160)
        self.assertLess(suave.getpixel((240, 240))[0], 160)

    def test_validacao_rejeita_dados_invalidos(self):
        for dados in (None, [], {"zoom": float("nan"), "x": True, "brilho": 999,
                                "saturacao": "1;bad", "nitidez": -1}):
            self.assertEqual(validar_ajustes(dados), AJUSTES_PADRAO)
        self.assertEqual(validar_ajustes({"zoom": 125, "x": 0})["zoom"], 125)

    def test_arraste_tem_direcao_e_escala_corretas_por_eixo(self):
        ajustes = dict(AJUSTES_PADRAO, zoom=75)
        self.assertEqual(posicao_arraste((960, 480), ajustes, (50, 50), (0.1, 0.1)), (30, 90))
        self.assertEqual(posicao_arraste((480, 960), ajustes, (50, 50), (0.1, 0.1)), (90, 30))
        self.assertEqual(posicao_arraste((480, 480), AJUSTES_PADRAO, (50, 50), (0.1, 0.1)), (50, 50))


if __name__ == "__main__":
    unittest.main()
