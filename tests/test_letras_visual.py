import unittest
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw

from letras_sincronizadas import ler_lrc
from letras_visual import VisualLetras, _fonte, _linhas, fundo_da_letra

def legenda_no_circulo(diferenca):
    bbox = diferenca.getbbox()
    return (bbox is not None and bbox[1] >= 302 and bbox[3] <= 391
            and all((x - 240) ** 2 + (y - 240) ** 2 < 228 ** 2
                    for x in (bbox[0], bbox[2]) for y in (bbox[1], bbox[3])))


def caixas_dos_versos(diferenca):
    _, linhas = diferenca.getprojection()
    caixas = []
    topo = None
    for y, visivel in enumerate([*linhas, 0]):
        if visivel and topo is None:
            topo = y
        elif not visivel and topo is not None:
            x0, _, x1, _ = diferenca.crop((0, topo, diferenca.width, y)).getbbox()
            caixas.append((x0, topo, x1, y))
            topo = None
    return caixas


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

    def test_letra_ocupa_o_circulo_com_versos_de_contexto_menores(self):
        capa = Image.new("RGB", (480, 480), "black")
        letra = ler_lrc("\n".join(
            f"[00:{segundo:02}]Canta o mar sem fim" for segundo in range(1, 34, 3)))
        quadro = VisualLetras().spotify(capa, letra, 17)
        caixas = caixas_dos_versos(ImageChops.difference(quadro, capa))
        ativo = min(caixas, key=lambda caixa: abs((caixa[1] + caixa[3]) / 2 - 240))
        assert ativo[2] - ativo[0] > 328
        anterior = caixas[caixas.index(ativo) - 1]
        seguinte = caixas[caixas.index(ativo) + 1]

        for contexto in (anterior, seguinte):
            assert contexto[2] - contexto[0] < 0.8 * (ativo[2] - ativo[0])
            assert contexto[3] - contexto[1] < 0.85 * (ativo[3] - ativo[1])
        assert caixas[0][1] < 75 and caixas[-1][3] > 405
        assert all((x - 240) ** 2 + (y - 240) ** 2 <= 223 ** 2
                   for y in range(480) for x in range(480)
                   if quadro.getpixel((x, y)) != (0, 0, 0))

    def test_troca_de_verso_move_e_redimensiona_no_tempo_da_musica(self):
        capa = Image.new("RGB", (480, 480), "black")
        letra = ler_lrc("[00:01]Canta o mar sem fim\n[00:04]Canta o mar sem fim\n"
                        "[00:07]Canta o mar sem fim")
        visual = VisualLetras()
        antes = visual.spotify(capa, letra, 6.99)
        inicio = visual.spotify(capa, letra, 7)
        meio = visual.spotify(capa, letra, 7.09)
        fim = visual.spotify(capa, letra, 7.4)

        assert antes.tobytes() == inicio.tobytes()
        quadros = [caixas_dos_versos(quadro) for quadro in (inicio, meio, fim)]
        saida, entrada = zip(*(caixas[-2:] for caixas in quadros))
        assert entrada[0][1] > entrada[1][1] > entrada[2][1]
        assert saida[0][1] > saida[1][1] > saida[2][1]
        larguras_entrada = [x1 - x0 for x0, _, x1, _ in entrada]
        larguras_saida = [x1 - x0 for x0, _, x1, _ in saida]
        assert larguras_entrada[0] < larguras_entrada[1] < larguras_entrada[2]
        assert larguras_saida[0] > larguras_saida[1] > larguras_saida[2]

        assert visual.spotify(capa, letra, 7.09).tobytes() == meio.tobytes()
        assert visual.spotify(capa, letra, 7.09) is visual.spotify(capa, letra, 7.09)

    def test_emojis_coloridos_na_letra_e_na_legenda(self):
        fundo = Image.new("RGB", (480, 480), (48, 48, 48))
        letra = ler_lrc("[00:01]Canta comigo 👩‍🚀 ❤️")
        visual = VisualLetras()
        for quadro in (visual.spotify(fundo, letra, 2), visual.clipe(fundo, letra, 2)):
            cores = quadro.get_flattened_data()
            assert any(r > 150 and r - g > 90 and r - b > 60 for r, g, b in cores)
            assert (255, 255, 255) in cores

    def test_quebra_de_linha_preserva_emoji_composto_e_variacao(self):
        linhas, completa = _linhas("👩‍🚀❤️🎤", _fonte(42), 60, 4)
        assert completa
        assert linhas == ["👩‍🚀", "❤️", "🎤"]

    def test_letra_preserva_texto_quando_render_de_emoji_falha(self):
        fundo = Image.new("RGB", (480, 480), "black")
        letra = ler_lrc("[00:01]O jardim 🦋")
        visual = VisualLetras()
        with (patch("emoji_windows.rasterizar_emoji", side_effect=OSError),
              patch("texto_emoji._fonte_emoji", return_value=None)):
            quadro = visual.spotify(fundo, letra, 2)
            legenda = visual.clipe(fundo, letra, 2)
        assert (255, 255, 255) in quadro.get_flattened_data()
        assert (255, 255, 255) in legenda.get_flattened_data()
