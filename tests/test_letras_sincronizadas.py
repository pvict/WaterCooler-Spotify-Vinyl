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

        assert buscar_letra(faixa(), obter=obter, esperar=lambda _: None).linhas == ("A praça respira",)
        assert requisicoes[0][1]["duration"] == 120
        assert requisicoes[0][1]["album_name"] == "Vento de Vidro"


    def test_lrclib_ignora_letra_sem_tempo_e_faixa_instrumental(self):
        def obter(caminho, parametros):
            return (registro(instrumental=True) if caminho == "get"
                    else [registro(syncedLyrics=None, plainLyrics="Em algum lugar")])

        assert buscar_letra(faixa(), obter=obter, esperar=lambda _: None) is None


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
            assert fonte.consultar(faixa()) is None
            assert iniciou.wait(1)
            destino = faixa("Ponte de Luz")
            assert fonte.consultar(destino) is None
            liberar.set()
            assert segunda.wait(1)
            assert fonte.consultar(destino) is None

            prazo = time.monotonic() + 1
            while fonte.consultar(faixa()) is None and time.monotonic() < prazo:
                time.sleep(0.01)
            assert fonte.consultar(faixa()) is letra
            assert chamadas == [faixa(), destino]
        finally:
            liberar.set()
            fonte.fechar()
