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
from modo_clipe import (FILTRO_CLIPE, FILTRO_LYRIC_VIDEO, FPS_CLIPE, CandidatoClipe, FonteClipe, QuadrosClipe,
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

    def test_dvd_e_formas_equivalentes_de_ao_vivo_valem_para_qualquer_artista(self):
        musica = ("Vento do Mar - Ao Vivo", "Artista")
        candidatos = [
            dict(entrada("aaaaaaaaaaa", "Artista - Vento do Mar (DVD No Teatro)", "Canal do show"),
                 view_count=150),
            dict(entrada("bbbbbbbbbbb", "Artista - Vento do Mar (Live in the Park)", "Canal do show"),
                 view_count=1000),
            dict(entrada("ccccccccccc", "Artista - Vento do Mar (En Vivo)", "Canal do show"),
                 view_count=800),
            dict(entrada("ddddddddddd", "Artista - Vento do Mar (Versão de Estúdio)", "Artista"),
                 channel_is_verified=True, view_count=2500),
            dict(entrada("eeeeeeeeeee", "Artista - Vento do Mar (DVD No Teatro) (Áudio Oficial)", "Artista"),
                 channel_is_verified=True, view_count=5000),
        ]
        escolhidos = classificar_clipes(candidatos, musica, 200, "No Teatro (Ao Vivo)")
        self.assertEqual([c.id for c in escolhidos], ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"])
        self.assertTrue(escolhidos[0].exigir_movimento)

    def test_nome_do_projeto_distingue_gravacoes_do_mesmo_artista(self):
        musica = ("Acordando O Prédio - Ao Vivo", "Luan Santana")
        candidatos = [
            dict(entrada("mvWXaz0Gtbo", "Luan Santana - Acordando o Prédio (Vídeo Oficial)", "Luan Santana", 247),
                 channel_is_verified=True, view_count=327_000_000),
            dict(entrada("GPwGsv1te5M", "Luan Santana - Acordando o Prédio (Registro Histórico)", None, 214),
                 view_count=2_800_000),
            dict(entrada("eS7yRwHE0Ow", "Luan Santana - Acordando o Prédio (DVD 1977)", "Outro canal", 157),
                 view_count=88_000),
        ]
        escolhidos = classificar_clipes(candidatos, musica, 212, "Registro Histórico Parte 01 (Ao Vivo)")
        self.assertEqual(escolhidos[0].id, "GPwGsv1te5M")
        self.assertEqual(classificar_clipes(candidatos, musica, 157, "1977")[0].id, "eS7yRwHE0Ow")

    def test_album_define_versao_se_o_nome_da_faixa_nao_a_explicita(self):
        candidatos = [
            entrada("aaaaaaaaaaa", "Artista - Brisa (Festival da Serra Unplugged)"),
            entrada("bbbbbbbbbbb", "Artista - Brisa (Versão de Estúdio)"),
        ]
        disco = "Festival da Serra (Acústico ao Vivo)"
        self.assertEqual([c.id for c in classificar_clipes(candidatos, ("Brisa", "Artista"), 200, disco)],
                         ["aaaaaaaaaaa"])
        self.assertEqual([c.id for c in classificar_clipes(
            candidatos, ("Brisa - Versão de Estúdio", "Artista"), 200, disco)], ["bbbbbbbbbbb"])

    def test_busca_consulta_o_album_se_a_primeira_resposta_traz_outro_show(self):
        musica = ("Vento do Mar - Ao Vivo", "Artista")
        chave = (musica, 1)
        snap = {"musica": musica, "dur": 200, "tocando": True, "reproducao_id": 1,
                "pedido_capa_album": {"musica": musica, "album": "No Teatro Parte 01 (Ao Vivo)"}}
        fonte = FonteClipe(lambda: snap, lambda: True, lambda _: None, lambda _: None)
        respostas = [
            {"entries": [entrada("aaaaaaaaaaa", "Artista - Vento do Mar (Ao Vivo no Parque)")]},
            {"entries": [entrada("bbbbbbbbbbb", "Artista - Vento do Mar (No Teatro)")]},
        ]
        with patch.object(fonte, "_youtube", side_effect=[json.dumps(r).encode() for r in respostas]) as youtube:
            self.assertEqual([c.id for c in fonte._buscar_clipes(snap, chave)], ["bbbbbbbbbbb", "aaaaaaaaaaa"])
        self.assertEqual([chamada.args[0][-1] for chamada in youtube.call_args_list], [
            "ytsearch20:Artista Vento do Mar ao vivo", "ytsearch20:Artista Vento do Mar No Teatro"])

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

    def test_lyric_video_ordena_por_visualizacoes_e_filtra_outras_faixas(self):
        musica = ("Coração", "Artista")
        candidatos = [
            dict(entrada("aaaaaaaaaaa", "Artista - Coração (Official Lyric Video)"), view_count=80),
            dict(entrada("bbbbbbbbbbb", "[Lyrics] Artista - Coração"), view_count=150),
            dict(entrada("ccccccccccc", "Artista - Coração (Clipe Oficial)"), view_count=800),
            dict(entrada("ddddddddddd", "Artista - Coração (Karaoke Lyrics)"), view_count=900),
            dict(entrada("eeeeeeeeeee", "Outro artista - Coração (Letra)", "Outro artista"), view_count=1000),
        ]
        self.assertEqual([c.id for c in classificar_clipes(candidatos, musica, 200)], ["ccccccccccc"])
        self.assertEqual([c.id for c in classificar_clipes(candidatos, musica, 200, lyric_video=True)],
                         ["bbbbbbbbbbb", "aaaaaaaaaaa"])

    def test_titulo_canonico_remove_variacoes_de_edicao(self):
        for titulo in ("Girl on the Moon - 2025 Remix", "Girl on the Moon (2025 Remix)",
                       "Girl on the Moon [Remastered 2025]", "Girl on the Moon – Radio Edit",
                       "Girl on the Moon (feat. Convidada)", "Girl on the Moon (2025 Remixed)"):
            self.assertEqual(titulo_base(titulo), "Girl on the Moon")

    def test_corte_de_single_admite_clipe_integral_com_creditos_da_banda(self):
        musica = ('Purple Rain - 7" Single Edit; 2017 Remaster', "Prince")
        candidatos = [
            entrada("aaaaaaaaaaa", "Purple Rain (7\" Single Edit) (2017 Remaster)", "Prince", 246),
            entrada("bbbbbbbbbbb", "Prince - Purple Rain (Official Video)", "The Codfather", 478),
            entrada("ccccccccccc", "Prince & The Revolution - Purple Rain (Official Video), HD "
                    "(Digitally Remastered and Upscaled)", "Enhanced Music Videos", 425),
            entrada("ddddddddddd", "Prince - Purple Rain (Live in Syracuse)", "Prince", 993),
            entrada("eeeeeeeeeee", "Prince - Purple Rain (Official Video)", "Outro canal", 800),
            entrada("fffffffffff", "Prince & The Revolution - Purple Rain (Official Music Video)",
                    "Outro canal", 533),
        ]
        self.assertEqual(titulo_base(musica[0]), "Purple Rain")
        resultados = classificar_clipes(candidatos, musica, 246)
        self.assertEqual([c.id for c in resultados[:2]], ["ccccccccccc", "bbbbbbbbbbb"])
        self.assertTrue(all(c.exigir_movimento for c in resultados[:2]))
        self.assertNotIn("ddddddddddd", [c.id for c in resultados])
        self.assertNotIn("eeeeeeeeeee", [c.id for c in resultados])

    def test_versao_de_sete_polegadas_encontra_credito_em_qualquer_ordem(self):
        musica = ('Tom\'s Diner - 7″ Version', "DNA")
        candidatos = [
            entrada("aaaaaaaaaaa", "Suzanne Vega, DNA - Tom's Diner", "Suzanne Vega", 228),
            entrada("bbbbbbbbbbb", "Suzanne Vega featuring D.N.A. - Tom's Diner (Music Video)",
                    "Canal musical", 228),
            entrada("ccccccccccc", "Suzanne Vega - Tom's Diner (Acapella Version)",
                    "Suzanne Vega", 127),
            entrada("ddddddddddd", "DNA feat. Suzanne Vega - Tom's Diner (Lyrics)",
                    "Canal musical", 228),
            entrada("eeeeeeeeeee", "DNA & Suzanne Vega - Tom's Diner Again", "Canal musical", 228),
        ]
        self.assertEqual(titulo_base(musica[0]), "Tom's Diner")
        self.assertEqual({c.id for c in classificar_clipes(candidatos, musica, 228)},
                         {"aaaaaaaaaaa", "bbbbbbbbbbb"})

    def test_clipe_longo_em_canal_terceiro_exige_imagem_em_movimento(self):
        musica = ('Faixa - 7" Version', "Artista")
        chave = (musica, 1)
        snap = {"musica": musica, "reproducao_id": 1, "tocando": True, "dur": 200}
        fonte = FonteClipe(lambda: snap, lambda: True, lambda _: None, lambda _: None)
        candidatos = classificar_clipes([
            entrada("aaaaaaaaaaa", "Artista - Faixa (Official Video)", "Outro canal", 360),
        ], musica, 200)
        with TemporaryDirectory() as pasta, \
             patch.object(fonte, "_buscar_clipes", side_effect=[candidatos, []]), \
             patch.object(fonte, "_baixar_video", return_value=Path(pasta) / "clipe.mp4"), \
             patch("modo_clipe.video_tem_movimento", return_value=False) as verificar:
            self.assertIsNone(fonte._carregar_clipe(snap, chave, Path(pasta)))
            verificar.assert_called_once()

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

    def test_aceita_nomes_bilingues_sem_trocar_por_coreografia_ou_remix(self):
        candidatos = [
            entrada("YLXfyHsfFz0", "BTS (방탄소년단) 'Butter (Hotter Remix)' Official MV", "HYBE LABELS", 196),
            entrada("WMweEpGlu_U", "BTS (방탄소년단) 'Butter' Official MV", "HYBE LABELS", 183),
            entrada("DbXMjAYSa68", "[CHOREOGRAPHY] BTS (방탄소년단) 'Butter' Special Performance Video",
                    "BANGTANTV", 177),
            entrada("TQTlCHxyuu8", 'Stray Kids "神메뉴(God\'s Menu)" M/V', "JYP Entertainment", 187),
        ]
        self.assertEqual([c.id for c in classificar_clipes(candidatos, ("Butter", "BTS"), 164)],
                         ["WMweEpGlu_U"])
        self.assertEqual(escolher_clipe(candidatos, ("God's Menu", "Stray Kids"), 168), "TQTlCHxyuu8")

    def test_reconhece_creditos_de_participacao_e_rotulos_em_ordens_diferentes(self):
        candidatos = [
            entrada("N000qglmmY0", "Dua Lipa - Levitating (Official Animated Music Video)", "Dua Lipa", 210),
            entrada("TUVcZfQe-Kw", "Dua Lipa - Levitating Featuring DaBaby (Official Music Video)", "Dua Lipa", 231),
            entrada("3YxaaGgTQYM", "Evanescence - Bring Me To Life (Official HD Music Video) ft. Paul McCoy",
                    "Evanescence", 254),
            entrada("aaaaaaaaaaa", "Clipe Oficial | A Faixa • Artista | 4K", "Gravadora"),
            entrada("bbbbbbbbbbb", "Artista - A Faixa (Outra Canção) | 4K", "Artista"),
        ]
        self.assertEqual(escolher_clipe(candidatos, ("Levitating (feat. DaBaby)", "Dua Lipa"), 203), "TUVcZfQe-Kw")
        self.assertEqual(escolher_clipe(candidatos, ("Bring Me To Life", "Evanescence"), 235), "3YxaaGgTQYM")
        self.assertEqual([c.id for c in classificar_clipes(candidatos, ("A Faixa", "Artista"), 200)],
                         ["aaaaaaaaaaa"])

    def test_tolera_um_erro_de_grafia_somente_com_evidencia_de_autoria(self):
        musica = ("Purple Rain", "Prince")
        variantes = [
            dict(entrada("aaaaaaaaaaa", "Prince - Purle Rain (Official Video)", "Prince"), channel_is_verified=True),
            entrada("bbbbbbbbbbb", "Prince - Purle Rain (Official Video)", "Fã de Prince"),
            dict(entrada("ccccccccccc", "Prince - Purple Rains (Official Video)", "Prince"), channel_is_verified=True),
            dict(entrada("ddddddddddd", "Prince - Purple Rain Again (Official Video)", "Prince"),
                 channel_is_verified=True),
        ]
        candidatos = classificar_clipes(variantes, musica, 200)
        self.assertEqual([c.id for c in candidatos], ["aaaaaaaaaaa"])
        self.assertTrue(candidatos[0].exigir_movimento)
        exato = entrada("eeeeeeeeeee", "Prince - Purple Rain", "Prince")
        self.assertEqual(escolher_clipe([*variantes, exato], musica, 200), "eeeeeeeeeee")

    def test_admite_duracao_maior_no_clipe_do_canal_verificado(self):
        musica = ("The Less I Know The Better", "Tame Impala")
        candidatos = [
            entrada("aaaaaaaaaaa", "Tame Impala - The Less I Know The Better (Official Video)", "Fã", 343),
            dict(entrada("sBzrzS1Ag_g", "Tame Impala - The Less I Know The Better (Official Video)",
                         "Tame Impala", 343), channel_is_verified=True),
        ]
        self.assertEqual([c.id for c in classificar_clipes(candidatos, musica, 216)], ["sBzrzS1Ag_g"])

    def test_titulo_com_projeto_do_proprio_artista_encontra_love_exalando(self):
        musica = ("Love Exalando", "Leo Balbinotti")
        clipe = dict(entrada("s532TOnC5Wg", "Leo Balbinotti - Love Exalando (Novo Eu)",
                             "Leo Balbinotti", 148), channel_is_verified=True)
        catalogo = entrada("aaaaaaaaaaa", "Love Exalando", "Leo Balbinotti", 143)
        candidatos = classificar_clipes([catalogo, clipe], musica, 136.65)
        self.assertEqual([c.id for c in candidatos], ["s532TOnC5Wg", "aaaaaaaaaaa"])
        self.assertTrue(candidatos[0].exigir_movimento)

    def test_complementos_de_projeto_ainda_exigem_autoria_e_faixa_compativeis(self):
        def video(id_video, titulo, duracao=200, canal="Artista", verificado=True):
            return dict(entrada(id_video, titulo, canal, duracao), channel_is_verified=verificado)

        candidatos = [
            video("aaaaaaaaaaa", "Artista - Brisa da Praia | Sessões de Verão"),
            video("bbbbbbbbbbb", "Artista - Brisa da Praia (Projeto Azul) (Vídeo Oficial)"),
            video("ccccccccccc", "Artista - Brisa da Praia (Projeto Azul)", canal="Outro Canal"),
            video("ddddddddddd", "Artista - Brisa da Praia (Projeto Azul)", verificado=False),
            video("eeeeeeeeeee", "Artista - Brisa da Praia (Remix)"),
            video("fffffffffff", "Artista - Brisa da Praia (Ao Vivo)"),
            video("ggggggggggg", "Artista - Brisa da Praia (Demo)"),
            video("hhhhhhhhhhh", "Artista - Brisa da Praia (Projeto Azul)", duracao=260),
            video("iiiiiiiiiii", "Artista - Brisa da Praia de Manhã (Projeto Azul)"),
            video("jjjjjjjjjjj", "Artista - Brisa da Praia (Projeto Azul) (Lyrics)"),
            video("kkkkkkkkkkk", "Artista - Brisa da Praia"),
        ]
        resultados = classificar_clipes(candidatos, ("Brisa da Praia", "Artista"), 200)
        self.assertEqual([c.id for c in resultados], ["kkkkkkkkkkk", "bbbbbbbbbbb", "aaaaaaaaaaa"])
        self.assertTrue(all(c.exigir_movimento for c in resultados[1:]))

    def test_prioriza_visualizacoes_entre_clipes_da_faixa(self):
        def resultado(id_video, titulo, vistas):
            return dict(entrada(id_video, titulo, "Jorge & Mateus Oficial", 158), view_count=vistas)

        candidatos = [
            resultado("aaaaaaaaaaa", "Jorge & Mateus - Logo Eu (Clipe)", 1_500_000),
            resultado("bbbbbbbbbbb", "Jorge & Mateus - Logo Eu (Vídeo Oficial)", 200_000),
            resultado("ccccccccccc", "Jorge & Mateus - Logo Eu [Áudio Oficial]", 80_000_000),
            resultado("ddddddddddd", "Jorge & Mateus - Sosseguei (Vídeo Oficial)", 200_000_000),
        ]
        self.assertEqual([c.id for c in classificar_clipes(candidatos, ("Logo Eu", "Jorge e Mateus"), 157)],
                         ["aaaaaaaaaaa", "bbbbbbbbbbb"])

    def test_encontra_o_video_do_artista_quando_o_titulo_so_acrescenta_mencoes(self):
        musica = ("Resenha da Muierada", "Muierada")
        candidatos = [
            entrada("GM2AZpYP5HQ", "Resenha da Muierada", "MUIERADA", 247),
            entrada("DwCmZYjrDoA", "Resenha da Muierada, @JulyaeMaryana @juliarafaela_ "
                    "@KamilaeKavicOficial", "MUIERADA", 248),
            entrada("aaaaaaaaaaa", "Resenha da Muierada @Artista (Lyric Video)", "MUIERADA", 248),
            entrada("bbbbbbbbbbb", "Resenha da Muierada 2 @Artista", "MUIERADA", 248),
            entrada("ccccccccccc", "Resenha da Muierada @Muierada", "Outro canal", 248),
        ]
        self.assertEqual([c.id for c in classificar_clipes(candidatos, musica, 247)],
                         ["DwCmZYjrDoA", "GM2AZpYP5HQ"])

    def test_aceita_mencoes_depois_de_creditos_mas_preserva_o_nome_inteiro(self):
        candidatos = [
            entrada("aaaaaaaaaaa", "Artista - A Faixa feat. @Canal-Oficial (Videoclipe Oficial)"),
            entrada("bbbbbbbbbbb", "Artista - A Faixa @Canal-Oficial Versão Alternativa"),
            entrada("ccccccccccc", "Artista - A Faixa © @Convidada"),
        ]
        self.assertEqual([c.id for c in classificar_clipes(candidatos, ("A Faixa", "Artista"), 200)],
                         ["aaaaaaaaaaa", "ccccccccccc"])
        self.assertEqual(escolher_clipe(candidatos, ("A Faixa @Canal-Oficial Versão Alternativa", "Artista"),
                                       200), "bbbbbbbbbbb")

    def test_busca_aproveita_canal_quando_a_primeira_resposta_veio_sem_creditos(self):
        musica = ("A Faixa", "Artista")
        chave = (musica, 1)
        snap = {"musica": musica, "reproducao_id": 1, "tocando": True, "dur": 200}
        fonte = FonteClipe(lambda: snap, lambda: True, lambda _: None, lambda _: None)
        video = entrada("aaaaaaaaaaa", "A Faixa, @Convidada", None)
        respostas = [{"entries": [video]}, {"entries": [dict(video, channel="Artista")]}]
        with patch.object(fonte, "_youtube", side_effect=[json.dumps(r).encode() for r in respostas]):
            self.assertEqual([c.id for c in fonte._buscar_clipes(snap, chave)], ["aaaaaaaaaaa"])

    def test_confirma_canal_quando_buscas_so_retornam_titulo_e_identificador(self):
        musica = ("A Faixa", "Artista")
        chave = (musica, 1)
        snap = {"musica": musica, "reproducao_id": 1, "tocando": True, "dur": 200}
        fonte = FonteClipe(lambda: snap, lambda: True, lambda _: None, lambda _: None)
        incompleto = entrada("aaaaaaaaaaa", "A Faixa, @Convidada", None)
        busca = {"entries": [incompleto, entrada("bbbbbbbbbbb", "Outra Faixa", None)]}
        with patch.object(fonte, "_youtube", side_effect=[json.dumps(r).encode() for r in
                         (busca, busca, dict(incompleto, channel="Artista"))]) as youtube:
            self.assertEqual([c.id for c in fonte._buscar_clipes(snap, chave)], ["aaaaaaaaaaa"])
        args = youtube.call_args.args[0]
        self.assertIn("--skip-download", args)
        self.assertEqual(args[-1], "https://www.youtube.com/watch?v=aaaaaaaaaaa")

    def test_resultado_de_catalogo_nao_impede_confirmacao_de_clipe_sem_canal(self):
        musica = ("A Faixa", "Artista")
        chave = (musica, 1)
        snap = {"musica": musica, "reproducao_id": 1, "tocando": True, "dur": 200}
        fonte = FonteClipe(lambda: snap, lambda: True, lambda _: None, lambda _: None)
        clipe = entrada("aaaaaaaaaaa", "A Faixa, @Convidada", None)
        busca = {"entries": [entrada("bbbbbbbbbbb", "A Faixa"), clipe]}
        meta = dict(clipe, channel="Artista")
        with patch.object(fonte, "_youtube", side_effect=[json.dumps(r).encode() for r in
                         (busca, meta)]):
            self.assertEqual([c.id for c in fonte._buscar_clipes(snap, chave)], ["aaaaaaaaaaa", "bbbbbbbbbbb"])

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
        self.assertEqual(consultas, ["ytsearch20:Foreigner Girl on the Moon",
                                     'ytsearch20:Foreigner "Girl on the Moon"'])

    def test_consulta_simples_com_resultado_evitar_busca_complementar(self):
        musica = ("Girl on the Moon - 2025 Remix", "Foreigner")
        chave = (musica, 1)
        snap = {"musica": musica, "reproducao_id": 1, "tocando": True, "dur": 230}
        fonte = FonteClipe(lambda: snap, lambda: True, lambda _: None, lambda _: None)
        resposta = {"entries": [entrada("aaaaaaaaaaa", "Foreigner - Girl on the Moon (1981) HD",
                                        "BackToRock", 228)]}
        with patch.object(fonte, "_youtube", return_value=json.dumps(resposta).encode()) as youtube:
            self.assertEqual([c.id for c in fonte._buscar_clipes(snap, chave)], ["aaaaaaaaaaa"])
        self.assertEqual([c.args[0][-1] for c in youtube.call_args_list],
                         ["ytsearch20:Foreigner Girl on the Moon"])

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
        self.assertFalse(fonte.letra_no_video)

    def test_recorre_a_lyric_video_depois_de_esgotar_os_clipes(self):
        musica = ("Coração", "Artista")
        chave = (musica, 1)
        snap = {"musica": musica, "tocando": True, "reproducao_id": 1, "dur": 200}
        fonte = FonteClipe(lambda: snap, lambda: True, lambda _: None, lambda _: None)
        clipe = CandidatoClipe("aaaaaaaaaaa", True)
        lyric = CandidatoClipe("bbbbbbbbbbb", False)
        with TemporaryDirectory() as diretorio:
            pasta = Path(diretorio)
            arquivo = pasta / "clipe.bbbbbbbbbbb.mp4"
            with (patch.object(fonte, "_buscar_clipes", side_effect=[[clipe], [lyric]]) as buscar,
                  patch.object(fonte, "_baixar_video", side_effect=[RuntimeError("YouTube"), arquivo]) as baixar,
                  patch("modo_clipe.video_tem_movimento", return_value=True)):
                self.assertEqual(fonte._carregar_clipe(snap, chave, pasta), arquivo)
        self.assertEqual([chamada.args[0] for chamada in baixar.call_args_list], [clipe.id, lyric.id])
        self.assertTrue(buscar.call_args.kwargs.get("lyric_video"))
        self.assertTrue(fonte.letra_no_video)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg não instalado")
    def test_lyric_video_preserva_bordas_do_video_dentro_do_circulo(self):
        cmd = ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi",
               "-i", "color=blue:s=640x360,drawbox=0:0:100:ih:red:t=fill",
               "-vf", FILTRO_LYRIC_VIDEO, "-frames:v", "1", "-pix_fmt", "rgb24",
               "-f", "rawvideo", "-"]
        quadro = subprocess.check_output(cmd, timeout=10)
        self.assertEqual(len(quadro), 480 * 480 * 3)
        i = (240 * 480 + 75) * 3
        r, _, b = quadro[i:i + 3]
        self.assertGreater(r, 230)
        self.assertLess(b, 20)

    def test_reconsulta_uma_vez_apos_falha_temporaria_na_mesma_faixa(self):
        musica = ("Faixa", "Artista")
        snap = {"musica": musica, "tocando": True, "reproducao_id": 1}
        fonte = FonteClipe(lambda: snap, lambda: True, lambda _: None, lambda _: None)
        relogio = 100

        def esperar(_intervalo):
            nonlocal relogio
            relogio += 1
            if relogio >= 130:
                fonte._parar.set()

        with patch("modo_clipe.time.monotonic", side_effect=lambda: relogio), \
             patch.object(fonte._parar, "wait", side_effect=esperar), \
             patch.object(fonte, "_carregar_clipe", side_effect=RuntimeError("YouTube")) as carregar:
            fonte._rodar()
        self.assertEqual(carregar.call_count, 2)

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

    def test_congela_clipe_ao_avancar_e_troca_direto_para_o_novo_quadro(self):
        musica = ("Título", "Artista")
        chave = (musica, 10)
        snap = {"musica": musica, "reproducao_id": 10, "tocando": True, "pos": 12, "t_poll": 100}
        fonte = FonteClipe(lambda: snap, lambda: True, lambda _: None, lambda _: None)
        original, novo = QuadrosClipe(), QuadrosClipe()
        imagem, proxima = object(), object()
        original.adicionar(12, imagem, threading.Event())
        fonte._fluxo = (chave, original)

        with patch("modo_clipe.time") as relogio:
            relogio.monotonic.side_effect = [100, 100.05, 100.10, 100.20]
            self.assertIs(fonte.quadro(snap, 100), imagem)
            snap.update(pos=27, t_poll=100.05)
            self.assertIs(fonte.quadro(snap, 100.05), imagem)
            self.assertTrue(original.reposicionar.is_set())
            fonte._fluxo = None
            self.assertIs(fonte.quadro(snap, 100.10), imagem)
            novo.adicionar(27.15, proxima, threading.Event())
            fonte._fluxo = (chave, novo)
            self.assertIs(fonte.quadro(snap, 100.20), proxima)

    def test_ajuste_pelo_audio_preserva_o_ultimo_quadro_do_clipe(self):
        musica = ("Faixa", "Artista")
        chave = (musica, 9)
        snap = {"musica": musica, "reproducao_id": 9, "tocando": True, "pos": 4, "t_poll": 100}
        fonte = FonteClipe(lambda: snap, lambda: True, lambda _: None, lambda _: None)
        original, corrigido = QuadrosClipe(), QuadrosClipe()
        anterior, novo = object(), object()
        original.adicionar(4, anterior, threading.Event())
        fonte._fluxo = chave, original
        with patch("modo_clipe.time") as relogio:
            relogio.monotonic.side_effect = [100, 100.08, 100.21]
            self.assertIs(fonte.quadro(snap, 100), anterior)
            original.ajuste_audio_em = 100.05
            original.reposicionar.set()
            fonte._fluxo = None
            self.assertIs(fonte.quadro(snap, 100.08), anterior)
            corrigido.adicionar(4.20, novo, threading.Event())
            fonte._fluxo = chave, corrigido
            self.assertIs(fonte.quadro(snap, 100.21), novo)

    def test_congela_ao_voltar_mas_libera_em_trocas_ou_pausas(self):
        musica = ("Título", "Artista")
        snap = {"musica": musica, "reproducao_id": 10, "tocando": True, "pos": 12, "t_poll": 100}
        for termino in (dict(musica=("Outra faixa", "Artista"), reproducao_id=12),
                        dict(tocando=False), {}):
            with self.subTest(termino=termino):
                fonte = FonteClipe(lambda: snap, lambda: True, lambda _: None, lambda _: None)
                quadros = QuadrosClipe()
                imagem = object()
                quadros.adicionar(12, imagem, threading.Event())
                fonte._fluxo = ((musica, 10), quadros)
                fim = 100.10 if termino else 103
                with patch("modo_clipe.time") as relogio:
                    relogio.monotonic.side_effect = [100, 100.05, fim]
                    self.assertIs(fonte.quadro(snap, 100), imagem)
                    seek = dict(snap, reproducao_id=11, pos=3, t_poll=100.05)
                    self.assertIs(fonte.quadro(seek, 100.05), imagem)
                    fonte._fluxo = None
                    self.assertIsNone(fonte.quadro(dict(seek, **termino), fim))

    def test_corrige_desvio_persistente_em_um_quadro_por_vez(self):
        quadros = QuadrosClipe()
        self.assertAlmostEqual(quadros._posicao_alvo(60, 100), 60)
        self.assertAlmostEqual(quadros._posicao_alvo(60.45, 100.2), 60.2)
        self.assertAlmostEqual(quadros._posicao_alvo(60.7, 100.45), 60.45)
        self.assertAlmostEqual(quadros._posicao_alvo(60.85, 100.6), 60.6 + 1 / FPS_CLIPE)
        self.assertAlmostEqual(quadros._posicao_alvo(60.95, 100.7), 60.7 + 1 / FPS_CLIPE)
        self.assertAlmostEqual(quadros._posicao_alvo(61.15, 100.9), 60.9 + 2 / FPS_CLIPE)

    def test_oscilacoes_breves_nao_mudam_a_cadencia(self):
        quadros = QuadrosClipe()
        self.assertAlmostEqual(quadros._posicao_alvo(60, 100), 60)
        self.assertAlmostEqual(quadros._posicao_alvo(60.4, 100.2), 60.2)
        self.assertAlmostEqual(quadros._posicao_alvo(60.35, 100.35), 60.35)
        self.assertAlmostEqual(quadros._posicao_alvo(60.66, 100.5), 60.5)
        self.assertAlmostEqual(quadros._posicao_alvo(60.75, 100.75), 60.75)
        self.assertAlmostEqual(quadros._posicao_alvo(75, 100.8), 75)

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
