import unittest
from PIL import Image, ImageChops, ImageDraw

from letras_sincronizadas import ler_lrc
from letras_visual import VisualLetras, fundo_da_letra

def legenda_no_circulo(diferenca):
    bbox = diferenca.getbbox()
    return (bbox is not None and bbox[1] >= 302 and bbox[3] <= 391
            and all((x - 240) ** 2 + (y - 240) ** 2 < 228 ** 2
                    for x in (bbox[0], bbox[2]) for y in (bbox[1], bbox[3])))

class TestLetrasVisual(unittest.TestCase):
    def test_capa_central_e_borrada_ocupa_todos_os_pixels(self):
        capa = Image.new("RGB", (900, 480), (0, 255, 0))
        desenho = ImageDraw.Draw(capa)
        desenho.rectangle((210, 0, 689, 479), fill="red")
        desenho.rectangle((450, 0, 689, 479), fill="blue")
        capa = capa.convert("RGBA")
        capa.putalpha(0)

        fundo = fundo_da_letra(capa)

        assert fundo.mode == "RGB"
        assert fundo.size == (480, 480)
        assert fundo.getpixel((0, 240))[1] == fundo.getpixel((479, 240))[1] == 0
        assert fundo.getpixel((0, 240))[0] > 100
        r, _, b = fundo.getpixel((240, 240))
        assert 35 < r < 115 and 35 < b < 115
        assert fundo.getpixel((479, 240))[2] > 100


    def test_subtitulo_branco_contornado_no_circulo_com_ate_duas_linhas(self):
        quadro = Image.new("RGB", (480, 480), "lime")
        letra = ler_lrc(
            "[00:01]No cais o vento dobra a esquina e a cidade acende suas janelas\n[00:05]"
        )
        visual = VisualLetras()

        assert visual.clipe(quadro, letra, 0.4) is quadro
        assert visual.clipe(quadro, letra, 5) is quadro
        legendado = visual.clipe(quadro, letra, 3)
        assert legenda_no_circulo(ImageChops.difference(quadro, legendado))
        assert quadro.getpixel((240, 375)) == (0, 255, 0)
        cores = set(legendado.get_flattened_data())
        assert (255, 255, 255) in cores
        assert (0, 0, 0) in cores

        linhas = [bool(legendado.crop((0, y, 480, y + 1)).getcolors(maxcolors=2) is None)
                  for y in range(480)]
        assert sum(ligado and not anterior for anterior, ligado in zip(linhas, linhas[1:])) == 2


    def test_letra_no_spotify_destaca_linha_e_desloca_o_contexto(self):
        capa = Image.new("RGBA", (240, 240), (160, 100, 70, 0))
        visual = VisualLetras()
        letra = ler_lrc(
            "[00:01]A janela abre\n[00:03]O vento entra\n[00:06]As cortinas dançam"
        )

        primeira = visual.spotify(capa, letra, 1.6)
        transicao = visual.spotify(capa, letra, 3.12)
        segunda = visual.spotify(capa, letra, 3.4)
        repetida = visual.spotify(capa, letra, 3.4)
        assert repetida is segunda
        assert visual.spotify(capa, letra, 1.6).tobytes() == primeira.tobytes()
        assert transicao.tobytes() != segunda.tobytes()
        assert primeira.tobytes() != segunda.tobytes()
        assert (255, 255, 255) in set(segunda.get_flattened_data())
