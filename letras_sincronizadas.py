"""Busca de LRC por faixa e consulta à letra já disponível para o quadro atual."""

import bisect
import json
import random
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


_TEMPO = r"(\d{1,3}):([0-5]\d)(?:[.:](\d{1,3}))?"
_MARCA = re.compile(r"\[" + _TEMPO + r"\]")
_PALAVRA = re.compile(r"<" + _TEMPO + r">")
_OFFSET = re.compile(r"^\s*\[offset:\s*([+-]?\d{1,7})\s*\]", re.I | re.M)
_CREDITO = r"(?:(?:feat(?:uring)?|ft)\.?|part\.|participa[cç][aã]o de)\s+"
_CREDITO_TITULO = re.compile(r"\s*(?:[(\[]\s*" + _CREDITO + r"([^()\[\]]+)[)\]]|"
                           r"[-–—]\s*" + _CREDITO + r"(.+))\s*$", re.I)
_SEPARAR_ARTISTAS = re.compile(r"\s*[,;]\s*|\s+" + _CREDITO, re.I)
_SUFIXO_TITULO = re.compile(r"\s+[-–—]\s+")
_APOSTROFOS = str.maketrans("", "", "'‘’")
_LIMITE_RESPOSTA = 1024 * 1024
_LIMITE_CACHE = 64
_CACHE_POSITIVO = 8 * 60 * 60
_CACHE_NEGATIVO = 15 * 60
_CACHE_FALHA = 45
_FALHAS_BUSCA = (HTTPException, OSError, ValueError)
_TOLERANCIA_DURACAO = 2
_TOLERANCIA_MESMO_ALBUM = 8
_IDENTIFICACAO = "Turing Vinyl (https://github.com/pvict/WaterCooler-Spotify-Vinyl)"
_BLOQUEIO_HTTP = threading.Lock()
_PROXIMO_PEDIDO = {}


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


@dataclass(frozen=True)
class ResultadoLetra:
    situacao: str
    letra: LetraSincronizada | None = None


_OCULTA = ResultadoLetra("")
_BUSCANDO = ResultadoLetra("buscando")
_INDISPONIVEL = ResultadoLetra("indisponivel")
_ERRO = ResultadoLetra("erro")


def ler_lrc(lrc):
    if not isinstance(lrc, str):
        return None
    lrc = lrc.lstrip("\ufeff")
    ajuste = _OFFSET.search(lrc)
    offset = int(ajuste[1]) if ajuste else 0
    versos = []
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
        texto = _PALAVRA.sub("", linha)
        versos.extend((instante, texto) for instante in marcas)
    return _letra_de_versos(versos)


def _letra_de_versos(linhas):
    versos = {}
    for instante, linha in linhas:
        texto = " ".join(linha.split())[:1200]
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


def _chave_texto(valor):
    decomposto = unicodedata.normalize("NFD", _normalizar(valor)).translate(_APOSTROFOS)
    sem_acentos = "".join(caractere for caractere in decomposto if not unicodedata.combining(caractere))
    return " ".join(re.findall(r"[^\W_]+", sem_acentos))


def _titulo_e_credito(titulo):
    titulo = _normalizar(titulo)
    credito = _CREDITO_TITULO.search(titulo)
    if credito is None:
        return titulo, ""
    artistas = credito[1] or credito[2]
    if _SUFIXO_TITULO.search(artistas):
        return titulo, ""
    return titulo[:credito.start()].rstrip(), artistas


def _creditos(artista):
    return tuple(filter(None, map(_chave_texto, _SEPARAR_ARTISTAS.split(_normalizar(artista)))))


def _consulta_alternativa(faixa):
    titulo, _ = _titulo_e_credito(faixa.titulo)
    artistas = _creditos(faixa.artista)
    diversificar = (titulo != _normalizar(faixa.titulo) or _chave_texto(titulo) != titulo
                   or artistas != (_normalizar(faixa.artista),))
    if diversificar and artistas:
        return {"q": f"{_chave_texto(titulo)} {artistas[0]}"}


def _tempo_retry_after(valor):
    if valor:
        try:
            return max(0, int(valor))
        except ValueError:
            try:
                return max(0, parsedate_to_datetime(valor).timestamp() - time.time())
            except (TypeError, ValueError, OverflowError):
                pass
    return _CACHE_FALHA


def _requisitar_json(base, caminho, parametros):
    with _BLOQUEIO_HTTP:
        if time.monotonic() < _PROXIMO_PEDIDO.get(base, 0):
            raise TimeoutError("Aguardando nova consulta ao catálogo de letras")
    url = f"{base}/{caminho}?{urlencode(parametros)}"
    pedido = Request(url, headers={
        "Accept": "application/json",
        "User-Agent": _IDENTIFICACAO,
    })
    try:
        with urlopen(pedido, timeout=4) as resposta:
            dados = resposta.read(_LIMITE_RESPOSTA + 1)
    except HTTPError as erro:
        if erro.code in (429, 503):
            retry_after = erro.headers.get("Retry-After") if erro.headers else None
            atraso = _tempo_retry_after(retry_after) + random.uniform(0, 1)
            with _BLOQUEIO_HTTP:
                _PROXIMO_PEDIDO[base] = time.monotonic() + atraso
        raise
    if len(dados) > _LIMITE_RESPOSTA:
        raise ValueError("Resposta do catálogo de letras excedeu o limite")
    return json.loads(dados)


def _obter_json(caminho, parametros):
    return _requisitar_json("https://lrclib.net", f"api/{caminho}", parametros)


def _obter_mux(parametros):
    return _requisitar_json("https://api.lrcmux.dev", "get", parametros)


def _prioridade_faixa(faixa, titulo, artista, album, duracao, aceitar_mesmo_album):
    titulo_local, participacao_local = _titulo_e_credito(faixa.titulo)
    titulo_remoto, participacao_remota = _titulo_e_credito(titulo)
    chave_local = _chave_texto(titulo_local) or titulo_local
    chave_remota = _chave_texto(titulo_remoto) or titulo_remoto
    if not chave_local or chave_local != chave_remota:
        return None
    local, remoto = _creditos(faixa.artista), _creditos(artista)
    if not local or not remoto or local[0] != remoto[0]:
        return None
    mesmo_album = bool(_chave_texto(faixa.album)) and _chave_texto(faixa.album) == _chave_texto(album)
    creditos_locais = set(local[1:]) | set(_creditos(participacao_local))
    creditos_remotos = set(remoto[1:]) | set(_creditos(participacao_remota))
    creditos_incompletos = creditos_locais != creditos_remotos
    if creditos_incompletos and not (mesmo_album and (creditos_locais <= creditos_remotos
                                                     or creditos_remotos <= creditos_locais)):
        return None
    if type(duracao) not in (int, float) or not 1 <= duracao <= 3600:
        return None
    diferenca = abs(duracao - faixa.duracao)
    aproximada = diferenca > _TOLERANCIA_DURACAO
    # O timeline do Spotify no Windows pode reportar alguns segundos a menos.
    if aproximada and not (aceitar_mesmo_album and mesmo_album and diferenca <= _TOLERANCIA_MESMO_ALBUM):
        return None
    return aproximada, not mesmo_album, creditos_incompletos, diferenca


def _tem_letra_na_faixa(letra, faixa):
    return letra is not None and any(t <= faixa.duracao * 1000 and linha
                                     for t, linha in zip(letra.tempos, letra.linhas))


def _melhor_letra(registros, faixa, aceitar_mesmo_album=False):
    melhor = None
    for registro in registros:
        if not isinstance(registro, dict) or registro.get("instrumental"):
            continue
        prioridade = _prioridade_faixa(faixa, registro.get("trackName") or registro.get("name"),
                                       registro.get("artistName"), registro.get("albumName"),
                                       registro.get("duration"), aceitar_mesmo_album)
        if prioridade is None or (melhor is not None and prioridade >= melhor[0]):
            continue
        letra = ler_lrc(registro.get("syncedLyrics"))
        if _tem_letra_na_faixa(letra, faixa):
            melhor = prioridade, letra
    return melhor[1] if melhor else None


def _buscar_lrclib(faixa, obter, esperar):
    consulta = {"track_name": faixa.titulo, "artist_name": faixa.artista}
    exata = dict(consulta, duration=faixa.duracao)
    if faixa.album:
        exata["album_name"] = faixa.album
    try:
        registro = obter("get", exata)
    except HTTPError as erro:
        if erro.code in (429, 503):
            raise
        registro = None
    except _FALHAS_BUSCA:
        registro = None
    letra = _melhor_letra((registro,), faixa)
    if letra is not None:
        return letra
    for termos in (consulta, _consulta_alternativa(faixa)):
        if termos is None:
            break
        esperar(0.25)
        try:
            registros = obter("search", termos)
            if not isinstance(registros, list):
                raise ValueError("Resposta inválida da pesquisa do LRCLIB")
        except _FALHAS_BUSCA:
            letra = _melhor_letra((registro,), faixa, aceitar_mesmo_album=True)
            if letra is None:
                raise
            return letra
        letra = _melhor_letra((registro, *registros), faixa, aceitar_mesmo_album=True)
        if letra is not None:
            return letra


def _buscar_mux(faixa, obter):
    consulta = {"title": faixa.titulo, "artist": faixa.artista, "duration": faixa.duracao,
                "format": "json", "sources": "!lrclib"}
    if faixa.album:
        consulta["album"] = faixa.album
    try:
        dados = obter(consulta)
    except HTTPError as erro:
        if erro.code == 404:
            return None
        raise
    if not isinstance(dados, dict):
        raise ValueError("Resposta inválida do LRCMux")
    faixa_remota, meta, linhas = dados.get("track"), dados.get("meta"), dados.get("lines")
    if (not isinstance(faixa_remota, dict) or not isinstance(meta, dict)
            or meta.get("instrumental") or meta.get("level") not in ("line", "word")
            or not isinstance(linhas, list)):
        return None
    fonte = meta.get("source")
    if not isinstance(fonte, dict) or fonte.get("id") in (None, "", "lrclib"):
        return None
    prioridade = _prioridade_faixa(faixa, faixa_remota.get("title"), faixa_remota.get("artist"),
                                   faixa_remota.get("album"), faixa_remota.get("duration"),
                                   aceitar_mesmo_album=True)
    if prioridade is None:
        return None
    limite = (faixa.duracao + _TOLERANCIA_MESMO_ALBUM) * 1000
    versos = ((linha["start"], linha["text"]) for linha in linhas
              if isinstance(linha, dict) and type(linha.get("start")) is int
              and 0 <= linha["start"] <= limite and isinstance(linha.get("text"), str))
    letra = _letra_de_versos(versos)
    return letra if _tem_letra_na_faixa(letra, faixa) else None


def buscar_letra(faixa, obter=_obter_json, esperar=time.sleep, *, obter_mux=_obter_mux):
    falha_lrclib = None
    try:
        letra = _buscar_lrclib(faixa, obter, esperar)
        if letra is not None:
            return letra
    except _FALHAS_BUSCA as erro:
        falha_lrclib = erro
    letra = _buscar_mux(faixa, obter_mux)
    if letra is None and falha_lrclib is not None:
        raise falha_lrclib
    return letra


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
            if self._encerrada:
                return _OCULTA
            self._desejada = faixa
            if faixa is None:
                return _OCULTA
            entrada = self._resultado(faixa)
            if entrada is not None:
                self._cache.move_to_end(faixa)
                return entrada[1]
            self._condicao.notify()
            return _BUSCANDO

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
                resultado, validade = ((ResultadoLetra("pronta", letra), _CACHE_POSITIVO)
                                       if letra else (_INDISPONIVEL, _CACHE_NEGATIVO))
            except _FALHAS_BUSCA:
                resultado, validade = _ERRO, _CACHE_FALHA
            with self._condicao:
                if self._encerrada:
                    return
                self._cache[faixa] = time.monotonic() + validade, resultado
                self._cache.move_to_end(faixa)
                while len(self._cache) > _LIMITE_CACHE:
                    self._cache.popitem(last=False)
