import json
import shutil
import subprocess
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from controle_interface import validar
from modo_clipe import (FILTRO_CLIPE, FPS_CLIPE, CandidatoClipe, FonteClipe, QuadrosClipe,
                        classificar_clipes, posicao_atual, titulo_base, video_tem_movimento)


def entrada(id, titulo, canal="Artista", duracao=200):
    return {"id": id, "title": titulo, "channel": canal, "duration": duracao}


def escolher_clipe(entradas, musica, duracao):
    candidatos = classificar_clipes(entradas, musica, duracao)
    return candidatos[0].id if candidatos else None


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
                      entrada("bbbbbbbbbbb", "Artista - Coração (Official Lyrics)")]
        self.assertIsNone(escolher_clipe(candidatos, ("Coração", "Artista"), 200))

    def test_titulo_canonico_remove_variacoes_de_edicao(self):
        for titulo in ("Girl on the Moon - 2025 Remix", "Girl on the Moon (2025 Remix)",
                       "Girl on the Moon [Remastered 2025]", "Girl on the Moon – Radio Edit",
                       "Girl on the Moon (feat. Convidada)", "Girl on the Moon (2025 Remixed)"):
            self.assertEqual(titulo_base(titulo), "Girl on the Moon")

    def test_prioriza_original_oficial_sobre_remix_e_outros_canais(self):
        candidatos = [
            entrada("eeeeeeeeeee", "Girl on the Moon", "Foreigner", 230),
            entrada("aaaaaaaaaaa", "Foreigner - Girl On The Moon (1981) HD", "BackToRock", 228),
            entrada("bbbbbbbbbbb", "Foreigner - Girl On The Moon (2025 Remix) (Official Music Video)",
                    "Foreigner", 230),
            entrada("ccccccccccc", "Foreigner - Girl On The Moon (Official Music Video)", "RHINO", 247),
            entrada("ddddddddddd", "Ronnie Romero - Girl On The Moon (Foreigner cover) (Official Video)",
                    "Frontiers", 230),
        ]
        faixa = ("Girl on the Moon - 2025 Remix", "Foreigner")
        resultados = classificar_clipes(candidatos, faixa, 230)
        self.assertEqual([c.id for c in resultados], ["ccccccccccc", "bbbbbbbbbbb", "aaaaaaaaaaa", "eeeeeeeeeee"])
        self.assertEqual([c.oficial for c in resultados], [True, True, False, False])

    def test_rejeita_titulo_parecido_e_interprete_diferente(self):
        candidatos = [
            entrada("aaaaaaaaaaa", "Artista - Stay With Me (Official Video)"),
            entrada("bbbbbbbbbbb", "Outra Banda - Stay (Artista) (Official Video)", "Outra Banda"),
            entrada("ccccccccccc", "Artista - Stay (Official Video)"),
        ]
        self.assertEqual([c.id for c in classificar_clipes(candidatos, ("Stay", "Artista"), 200)],
                         ["ccccccccccc"])

    def test_distingue_artista_topic_de_canal_automatico(self):
        candidatos = [
            entrada("aaaaaaaaaaa", "Topic - Breaking Me (Official Video)", "Topic"),
            entrada("bbbbbbbbbbb", "Topic - Breaking Me (Official Video)", "Topic - Topic"),
        ]
        resultados = classificar_clipes(candidatos, ("Breaking Me", "Topic"), 200)
        self.assertEqual([c.id for c in resultados], ["aaaaaaaaaaa"])

    def test_busca_canonica_repete_sem_termos_de_clipe_se_necessario(self):
        musica = ("Girl on the Moon - 2025 Remix", "Foreigner")
        chave = (musica, 1)
        snap = {"musica": musica, "reproducao_id": 1, "tocando": True, "dur": 230}
        fonte = FonteClipe(lambda: snap, lambda: True, lambda _: None, lambda _: None)
        resposta = {"entries": [entrada("aaaaaaaaaaa", "Foreigner - Girl on the Moon (1981) HD",
                                        "BackToRock", 228)]}
        with patch.object(fonte, "_youtube", side_effect=[b'{"entries":[]}', json.dumps(resposta).encode()]) as yt:
            self.assertEqual([c.id for c in fonte._buscar_clipes(snap, chave)], ["aaaaaaaaaaa"])
        consultas = [chamada.args[0][-1] for chamada in yt.call_args_list]
        self.assertEqual(consultas, ["ytsearch12:Foreigner Girl on the Moon official music video",
                                     "ytsearch12:Foreigner Girl on the Moon"])

    def test_preserva_resultado_quando_a_busca_complementar_falha(self):
        musica = ("Girl on the Moon - 2025 Remix", "Foreigner")
        chave = (musica, 1)
        snap = {"musica": musica, "reproducao_id": 1, "tocando": True, "dur": 230}
        fonte = FonteClipe(lambda: snap, lambda: True, lambda _: None, lambda _: None)
        resposta = {"entries": [entrada("aaaaaaaaaaa", "Foreigner - Girl on the Moon (1981) HD",
                                        "BackToRock", 228)]}
        with patch.object(fonte, "_youtube", side_effect=[json.dumps(resposta).encode(), RuntimeError]):
            self.assertEqual([c.id for c in fonte._buscar_clipes(snap, chave)], ["aaaaaaaaaaa"])

    def test_tenta_proximo_video_quando_principal_falha_ou_e_estatico(self):
        musica = ("Girl on the Moon - 2025 Remix", "Foreigner")
        chave = (musica, 1)
        snap = {"musica": musica, "reproducao_id": 1, "tocando": True, "dur": 230}
        fonte = FonteClipe(lambda: snap, lambda: True, lambda _: None, lambda _: None)
        candidatos = [CandidatoClipe("aaaaaaaaaaa", True), CandidatoClipe("bbbbbbbbbbb", False),
                      CandidatoClipe("ccccccccccc", False)]
        tentativas = []

        def baixar(video, pasta, _chave, timeout):
            tentativas.append(video)
            if video == "aaaaaaaaaaa":
                raise RuntimeError("Indisponível")
            arquivo = pasta / "clipe.mp4"
            self.assertFalse(arquivo.exists())
            arquivo.write_text(video, encoding="utf-8")
            return arquivo

        with TemporaryDirectory() as pasta, \
             patch.object(fonte, "_buscar_clipes", return_value=candidatos), \
             patch.object(fonte, "_baixar_video", side_effect=baixar), \
             patch("modo_clipe.video_tem_movimento", side_effect=[False, True]):
            arquivo = fonte._carregar_clipe(snap, chave, Path(pasta))
            self.assertEqual(arquivo.read_text(encoding="utf-8"), "ccccccccccc")
        self.assertEqual(tentativas, ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"])

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg não instalado")
    def test_detecta_imagem_estatica_antes_de_usar_video_alternativo(self):
        with TemporaryDirectory() as pasta:
            for nome, origem, esperado in (("estatico", "color=c=blue:", False),
                                          ("animado", "testsrc2=", True)):
                arquivo = Path(pasta) / f"{nome}.mp4"
                subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi",
                                "-i", f"{origem}s=128x128:r=8:d=20",
                                "-c:v", "libx264", str(arquivo)], check=True, timeout=10)
                self.assertIs(video_tem_movimento(arquivo), esperado)

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
