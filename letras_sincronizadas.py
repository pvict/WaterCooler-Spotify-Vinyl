"""Busca de LRC por faixa e consulta à letra já disponível para o quadro atual."""

import bisect
import json
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


_TEMPO = r"(\d{1,3}):([0-5]\d)(?:[.:](\d{1,3}))?"
_MARCA = re.compile(r"\[" + _TEMPO + r"\]")
_PALAVRA = re.compile(r"<" + _TEMPO + r">")
_OFFSET = re.compile(r"^\s*\[offset:\s*([+-]?\d{1,7})\s*\]", re.I | re.M)
_LIMITE_RESPOSTA = 1024 * 1024
_LIMITE_CACHE = 64
_CACHE_POSITIVO = 8 * 60 * 60
_CACHE_NEGATIVO = 15 * 60
_CACHE_FALHA = 45


@dataclass(frozen=True)
class FaixaLetra:
    titulo: str
    artista: str
    album: str
    duracao: int

    @classmethod
    def do_spotify(cls, snap):
        musica = snap.get("musica")
        duracao = snap.get("dur", 0)
        if musica and all(musica) and 1 <= duracao <= 3600:
            return cls(*musica, snap.get("album") or "", round(duracao))


@dataclass(frozen=True)
class LetraSincronizada:
    tempos: tuple[int, ...]
    linhas: tuple[str, ...]

    def indice(self, posicao):
        return bisect.bisect_right(self.tempos, posicao * 1000) - 1


def ler_lrc(lrc):
    if not isinstance(lrc, str):
        return None
    lrc = lrc.lstrip("\ufeff")
    ajuste = _OFFSET.search(lrc)
    offset = int(ajuste[1]) if ajuste else 0
    versos = {}
    for linha in lrc.splitlines():
        linha = linha.strip()
        marcas = []
        while marca := _MARCA.match(linha):
            minutos, segundos, fracao = marca.groups()
            instante = ((int(minutos) * 60 + int(segundos)) * 1000
                        + int((fracao or "").ljust(3, "0")) - offset)
            marcas.append(max(0, instante))
            linha = linha[marca.end():].lstrip()
        if not marcas:
            continue
        texto = " ".join(_PALAVRA.sub("", linha).split())[:1200]
        for instante in marcas:
            simultaneas = versos.setdefault(instante, [])
            if texto and texto not in simultaneas:
                simultaneas.append(texto)
    if not any(versos.values()):
        return None
    tempos = tuple(sorted(versos))
    return LetraSincronizada(tempos, tuple(" ".join(versos[t]) for t in tempos))


def _normalizar(valor):
    if isinstance(valor, str):
        return " ".join(unicodedata.normalize("NFKC", valor).casefold().split())
    return ""


def _obter_json(caminho, parametros):
    url = f"https://lrclib.net/api/{caminho}?{urlencode(parametros)}"
    pedido = Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "Turing Vinyl (https://github.com/pvict/WaterCooler-Spotify-Vinyl)",
    })
    with urlopen(pedido, timeout=4) as resposta:
        dados = resposta.read(_LIMITE_RESPOSTA + 1)
    if len(dados) > _LIMITE_RESPOSTA:
        raise ValueError("Resposta do LRCLIB excedeu o limite")
    return json.loads(dados)


def _melhor_letra(registros, faixa):
    melhor = None
    titulo, artista, album = map(_normalizar, (faixa.titulo, faixa.artista, faixa.album))
    for registro in registros:
        if not isinstance(registro, dict) or registro.get("instrumental"):
            continue
        if (_normalizar(registro.get("trackName") or registro.get("name")) != titulo
                or _normalizar(registro.get("artistName")) != artista):
            continue
        duracao = registro.get("duration")
        if not isinstance(duracao, (int, float)) or not 1 <= duracao <= 3600:
            continue
        diferenca = abs(duracao - faixa.duracao)
        if diferenca > 2:
            continue
        letra = ler_lrc(registro.get("syncedLyrics"))
        if letra is None or not any(t <= faixa.duracao * 1000 and linha
                                    for t, linha in zip(letra.tempos, letra.linhas)):
            continue
        prioridade = (_normalizar(registro.get("albumName")) != album if album else False, diferenca)
        if melhor is None or prioridade < melhor[0]:
            melhor = prioridade, letra
    return melhor[1] if melhor else None


def buscar_letra(faixa, obter=_obter_json, esperar=time.sleep):
    consulta = {"track_name": faixa.titulo, "artist_name": faixa.artista}
    exata = dict(consulta, duration=faixa.duracao)
    if faixa.album:
        exata["album_name"] = faixa.album
    try:
        registro = obter("get", exata)
    except HTTPError as erro:
        if erro.code != 404:
            raise
        registro = None
    letra = _melhor_letra((registro,), faixa)
    if letra is not None:
        return letra
    esperar(0.25)
    registros = obter("search", consulta)
    return _melhor_letra(registros, faixa) if isinstance(registros, list) else None


class FonteLetras:
    """Um worker mantém em cache as consultas sem bloquear a geração de quadros."""

    def __init__(self, buscar=buscar_letra):
        self._buscar = buscar
        self._condicao = threading.Condition()
        self._desejada = None
        self._encerrada = False
        self._cache = OrderedDict()
        self._thread = threading.Thread(target=self._rodar, name="LetrasSincronizadas", daemon=True)

    def iniciar(self):
        self._thread.start()

    def fechar(self):
        with self._condicao:
            self._encerrada = True
            self._condicao.notify()
        self._thread.join(timeout=1)

    def _resultado(self, faixa):
        entrada = self._cache.get(faixa)
        return entrada if entrada is not None and entrada[0] > time.monotonic() else None

    def consultar(self, faixa):
        with self._condicao:
            self._desejada = faixa
            entrada = self._resultado(faixa)
            if entrada is not None:
                self._cache.move_to_end(faixa)
                return entrada[1]
            if faixa is not None:
                self._condicao.notify()

    def _rodar(self):
        while True:
            with self._condicao:
                self._condicao.wait_for(lambda: self._encerrada or (
                    self._desejada is not None and self._resultado(self._desejada) is None))
                if self._encerrada:
                    return
                faixa = self._desejada
            try:
                letra = self._buscar(faixa)
                validade = _CACHE_POSITIVO if letra else _CACHE_NEGATIVO
            except (HTTPException, OSError, ValueError):
                letra, validade = None, _CACHE_FALHA
            with self._condicao:
                self._cache[faixa] = time.monotonic() + validade, letra
                self._cache.move_to_end(faixa)
                while len(self._cache) > _LIMITE_CACHE:
                    self._cache.popitem(last=False)
