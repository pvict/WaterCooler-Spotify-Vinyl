import unittest
import threading
import time
from urllib.error import HTTPError

from letras_sincronizadas import FaixaLetra, FonteLetras, buscar_letra, ler_lrc


def faixa(titulo="Céu de Papel", album="Vento de Vidro"):
    return FaixaLetra(titulo, "Maré Alta", album, 120)


def registro(**campos):
    return {
        "trackName": "Céu de Papel",
        "artistName": "Maré Alta",
        "albumName": "Vento de Vidro",
        "duration": 120,
        "syncedLyrics": "[00:02.00]O rio dobra a esquina",
        **campos,
    }


def sem_alternativa(_):
    return {}


def buscar_lrclib(musica, obter):
    return buscar_letra(musica, obter=obter, esperar=lambda _: None, obter_mux=sem_alternativa)


def resposta_mux(**campos):
    return {
        "track": {"title": "Céu de Papel", "artist": "Maré Alta",
                  "album": "Vento de Vidro", "duration": 120},
        "meta": {"level": "line", "source": {"id": "ytmusic"}},
        "lines": [{"text": "A praça respira", "start": 1987, "end": 2310},
                  {"text": "", "start": 3902, "end": 4510},
                  {"text": "O rio dobra a esquina", "start": 4510, "end": 5800}],
        **campos,
    }


class TestLetrasSincronizadas(unittest.TestCase):
    def test_lrc_usa_offset_ordenacao_repeticoes_e_silencio(self):
        letra = ler_lrc(
            "[ar:Maré Alta]\n[00:09.200]\n[00:04.17][00:02.2]Uma <00:02.9>onda\n"
            "[00:04.17]acorda a praça\n[offset:-200]\n[00:01]Começo"
        )

        assert letra.tempos == (1200, 2400, 4370, 9400)
        assert letra.linhas == ("Começo", "Uma onda", "Uma onda acorda a praça", "")
        assert [letra.indice(t) for t in (1.1, 1.2, 5.2, 2.5, 9.4)] == [-1, 0, 2, 1, 3]
        assert ler_lrc("[00:03.00]\nLinha sem tempo") is None


    def test_lrclib_filtra_titulo_artista_e_duracao_e_prefere_o_album(self):
        requisicoes = []

        def obter(caminho, parametros):
            requisicoes.append((caminho, parametros))
            if caminho == "get":
                raise HTTPError("https://lrclib.net", 404, "Track not found", None, None)
            return [
                registro(artistName="Outra banda"),
                registro(trackName="Céu de Papel (Ao Vivo)"),
                registro(duration=125),
                registro(syncedLyrics=None, plainLyrics="sem tempo"),
                registro(albumName="Compilação"),
                registro(duration=121, syncedLyrics="[00:03.00]A praça respira"),
            ]

        assert buscar_lrclib(faixa(), obter).linhas == ("A praça respira",)
        assert requisicoes[0][1]["duration"] == 120
        assert requisicoes[0][1]["album_name"] == "Vento de Vidro"


    def test_lrclib_ignora_letra_sem_tempo_e_faixa_instrumental(self):
        def obter(caminho, parametros):
            return (registro(instrumental=True) if caminho == "get"
                    else [registro(syncedLyrics=None, plainLyrics="Em algum lugar")])

        assert buscar_lrclib(faixa(), obter) is None


    def test_lrclib_recupera_love_exalando_com_duracao_menor_no_windows(self):
        musica = FaixaLetra("Love Exalando", "Leo Balbinotti", "Love Exalando", 137)

        def obter(caminho, parametros):
            if caminho == "get":
                assert parametros == {"track_name": musica.titulo, "artist_name": musica.artista,
                                      "album_name": musica.album, "duration": 137}
                raise HTTPError("https://lrclib.net/api/get", 404, "Track not found", None, None)
            assert caminho == "search"
            assert parametros == {"track_name": musica.titulo, "artist_name": musica.artista}
            return [registro(trackName=musica.titulo, artistName=musica.artista,
                             albumName=musica.album, duration=143,
                             syncedLyrics="[00:02.48]Linha de teste")]

        letra = buscar_lrclib(musica, obter)
        assert letra is not None
        assert letra.tempos == (2480,)

    def test_lrclib_reserva_tolerancia_maior_para_o_mesmo_album(self):
        def obter(caminho, parametros):
            if caminho == "get":
                return registro(duration=137)
            return [registro(duration=137), registro(duration=126, albumName="Coletânea")]

        assert buscar_lrclib(faixa(), obter) is None

    def test_lrclib_prefere_duracao_exata_a_mesmo_album_com_duracao_incompativel(self):
        def obter(caminho, parametros):
            if caminho == "get":
                return registro(duration=126)
            return [registro(duration=120, albumName="Coletânea", syncedLyrics="[00:04]Linha de teste")]

        assert buscar_lrclib(faixa(), obter).tempos == (4000,)

    def test_lrclib_tenta_busca_quando_requisicao_exata_expira(self):
        def obter(caminho, parametros):
            if caminho == "get":
                raise TimeoutError("tempo limite")
            return [registro()]

        assert buscar_lrclib(faixa(), obter).tempos == (2000,)

    def test_lrclib_encontra_credito_e_grafia_no_mesmo_album(self):
        musica = FaixaLetra("Céu de Papel (feat. Ana)", "Maré Alta, Ana", "Vento de Vidro", 120)

        def obter(caminho, parametros):
            if caminho == "get":
                return None
            if parametros == {"q": "ceu de papel mare alta"}:
                return [registro(trackName="Ceu de Papel", artistName="Mare Alta")]
            return []

        assert buscar_lrclib(musica, obter).tempos == (2000,)

    def test_lrclib_recusa_versoes_alternativas_e_creditos_conflitantes(self):
        musica = FaixaLetra("Céu de Papel (feat. Ana)", "Maré Alta, Ana", "Vento de Vidro", 120)
        registros = [
            registro(trackName="Céu de Papel - Ao Vivo", artistName="Maré Alta, Ana"),
            registro(trackName="Céu de Papel (feat. Clara)", artistName="Maré Alta, Clara"),
            registro(trackName="Céu de Papel", albumName="Ilha de Vidro"),
        ]

        assert buscar_lrclib(musica, lambda caminho, _: None if caminho == "get" else registros) is None

    def test_lrclib_preserva_parte_no_titulo(self):
        outras_partes = [registro(trackName="Céu de Papel - Part 2"),
                         registro(trackName="Céu de Papel (Part 3)")]

        assert buscar_lrclib(faixa(), lambda caminho, _: None if caminho == "get" else outras_partes) is None

    def test_lrcmux_recupera_tempos_reais_apos_falha_da_lrclib(self):
        def obter(_, __):
            raise TimeoutError("tempo limite")

        def obter_mux(parametros):
            assert parametros["sources"] == "!lrclib"
            assert parametros["title"] == "Céu de Papel"
            assert parametros["artist"] == "Maré Alta"
            assert parametros["album"] == "Vento de Vidro"
            assert parametros["duration"] == 120
            return resposta_mux()

        letra = buscar_letra(faixa(), obter=obter, esperar=lambda _: None, obter_mux=obter_mux)

        assert letra.tempos == (1987, 3902, 4510)
        assert letra.linhas == ("A praça respira", "", "O rio dobra a esquina")

    def test_lrcmux_consulta_outra_fonte_se_a_lrclib_pedir_pausa(self):
        def obter(caminho, _):
            assert caminho == "get"
            raise HTTPError("https://lrclib.net", 429, "Too many requests", None, None)

        letra = buscar_letra(faixa(), obter=obter, esperar=lambda _: None, obter_mux=lambda _: resposta_mux())

        assert letra.tempos[0] == 1987

    def test_lrcmux_recusa_faixas_de_outra_versao_mesmo_com_duracao_identica(self):
        dados = resposta_mux()
        dados["track"]["title"] = "Céu de Papel (Piano)"

        assert buscar_letra(faixa(), obter=lambda caminho, _: None if caminho == "get" else [],
                            esperar=lambda _: None, obter_mux=lambda _: dados) is None

    def test_lrcmux_recusa_texto_sem_tempos_e_duracao_incompativel(self):
        def obter(caminho, _):
            return None if caminho == "get" else []

        for alteracao in ({"meta": {"level": "none", "source": {"id": "ytmusic"}}},
                          {"lines": [{"text": "A praça respira"}]},
                          {"track": {**resposta_mux()["track"], "duration": 140}}):
            with self.subTest(campos=tuple(alteracao)):
                assert buscar_letra(faixa(), obter=obter, esperar=lambda _: None,
                                    obter_mux=lambda _: resposta_mux(**alteracao)) is None


    def test_fonte_busca_fora_da_thread_do_quadro_e_nao_entrega_faixa_anterior(self):
        iniciou = threading.Event()
        liberar = threading.Event()
        segunda = threading.Event()
        chamadas = []
        letra = ler_lrc("[00:01]A praça respira")

        def buscar(faixa_atual):
            chamadas.append(faixa_atual)
            if faixa_atual == faixa():
                iniciou.set()
                liberar.wait(2)
                return letra
            segunda.set()
            return None

        fonte = FonteLetras(buscar=buscar)
        fonte.iniciar()
        try:
            assert fonte.consultar(faixa()).situacao == "buscando"
            assert iniciou.wait(1)
            destino = faixa("Ponte de Luz")
            assert fonte.consultar(destino).situacao == "buscando"
            liberar.set()
            assert segunda.wait(1)
            assert esperar_resultado(fonte, destino).situacao == "indisponivel"

            resultado = fonte.consultar(faixa())
            assert resultado.situacao == "pronta"
            assert resultado.letra is letra
            assert chamadas == [faixa(), destino]
        finally:
            liberar.set()
            fonte.fechar()

    def test_fonte_distingue_falha_temporaria_e_limpa_consulta_ao_desativar(self):
        def buscar(_):
            raise TimeoutError("tempo limite")

        fonte = FonteLetras(buscar=buscar)
        fonte.iniciar()
        self.addCleanup(fonte.fechar)

        assert esperar_resultado(fonte, faixa()).situacao == "erro"
        assert fonte.consultar(None).situacao == ""


def esperar_resultado(fonte, musica):
    prazo = time.monotonic() + 1
    while True:
        resultado = fonte.consultar(musica)
        if resultado.situacao != "buscando" or time.monotonic() >= prazo:
            return resultado
        time.sleep(0.01)
