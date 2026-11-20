"""Busca clipes públicos e fornece quadros RGB para o fluxo da tela USB."""

import importlib.util
import json
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
from typing import NamedTuple
import unicodedata

from PIL import Image

from ao_vivo import BYTES_QUADRO, LADO, SEM_JANELA
from sincronia_audio_clipe import DESVIO_APLICAR, AjustePorAudio, trecho_da_edicao


FPS_CLIPE = 30
FILA_QUADROS = 8
FILTRO_CLIPE = (
    f"fps={FPS_CLIPE},"
    f"scale='ceil(max(1,dar)*{LADO}/2)*2':'ceil(max(1,1/dar)*{LADO}/2)*2',"
    f"crop={LADO}:{LADO},setsar=1"
)
INTERVALO = 0.10
TOLERANCIA = 1.0
DESVIO_SUAVE = 0.12
CONFIRMAR_DESVIO = 0.35
INTERVALO_CORRECAO = 0.25
DESVIO_PRIMEIRO_QUADRO = 0.20
ESPERA_REPOSICIONAR = 1.8
ID_VIDEO = re.compile(r"[A-Za-z0-9_-]{11}")
ARRANJOS = ("ao vivo", "live", "acustico", "acoustic", "unplugged")
IGNORAR = ("lyrics", "lyric", "letra", "karaoke", "cover", "reaction", "reacao",
           "audio", "speed up", "sped up", "slowed", "instrumental", "traducao",
           "interview", "entrevista", "commentary", "tutorial")
OFICIAL = ("official music video", "official video", "official hd video",
           "official 4k video", "clipe oficial", "video oficial", "videoclipe oficial",
           "official mv", "clip officiel", "video musical oficial")
MARCADOR_VIDEO = ("music video", "videoclipe", "video musical", "visualizer", "m v")
SUFIXO_SPOTIFY = re.compile(
    r"\s+(?:[-–—]\s+|[\[(])(?=(?:(?:7|12)\s*(?:[\"″”'’]{1,2}|-inch)\s*"
    r"(?:single\s+)?(?:edit|mix|version)\b|"
    r"(?:\d{4}\s+)?(?:remaster\w*|re-?mix(?:ed)?\b|(?:radio|single) (?:edit|mix|version)\b|"
    r"album version|original mix|anniversary|ao vivo|live\b|ac[uú]stic|"
    r"acoustic|unplugged|feat\b)))", re.I)
EDICAO_CURTA = re.compile(
    r"\b(?:7(?: inch)?(?: single)?|single|radio) (?:edit|mix|version)\b")
SEPARAR_CREDITOS = re.compile(r"\s*[,;&+]\s*|\s+(?:and|e|feat\.?|featuring|ft\.?|with)\s+", re.I)
ROTULOS_FINAIS = re.compile(
    r"(?:(?:\s*[,;]\s*|\s+)(?:hd|hq|uhd|4k|8k|(?:19|20)\d{2})\b|"
    r"\s*[\[({][^\])}]*[\])}])+\s*$", re.I)
MARCADOR_REMIX = re.compile(r"\bre ?mix(?:ed)?\b")
ROTULOS_VIDEO = re.compile(
    r"\s+(?:" + "|".join(sorted(OFICIAL + MARCADOR_VIDEO, key=len, reverse=True)) + r")$")
MENCOES_TITULO = re.compile(
    r"(?<!\w)(?:(?:feat(?:uring)?|ft|part|participaç[aã]o de)\.?\s+)?@[\w.-]+", re.I)


def normalizar(texto):
    sem_acentos = "".join(c for c in unicodedata.normalize("NFKD", texto.casefold())
                          if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", sem_acentos))


def contem(texto, trecho):
    return bool(trecho) and f" {trecho} " in f" {texto} "


def titulo_base(titulo):
    return SUFIXO_SPOTIFY.split(titulo, maxsplit=1)[0].strip()


def normalizar_artista(nome):
    # Siglas como D.N.A. e DNA representam o mesmo crédito.
    return normalizar(re.sub(r"(?<=\w)\.(?=\w)", "", nome))


def contem_artista(creditos, artistas):
    return (normalizar_artista(creditos) in artistas or
            any(normalizar_artista(nome) in artistas for nome in SEPARAR_CREDITOS.split(creditos)))


def extras_do_titulo(titulo, faixa, prefixo):
    sem_mencoes = MENCOES_TITULO.sub("", titulo)
    for variante in dict.fromkeys((sem_mencoes, titulo)):
        sem_rotulos = ROTULOS_FINAIS.sub("", variante)
        titulos = {normalizar(titulo_base(t)).removeprefix(prefixo) for t in (variante, sem_rotulos)}
        if faixa in titulos or any(ROTULOS_VIDEO.sub("", t) == faixa for t in titulos):
            return normalizar(variante).removeprefix(prefixo).removeprefix(faixa)
    return None


class CandidatoClipe(NamedTuple):
    id: str
    oficial: bool
    exigir_movimento: bool = False


def classificar_clipes(entradas, musica, duracao):
    titulo, artista = musica
    base = titulo_base(titulo)
    faixa = normalizar(base)
    subtitulo = normalizar(titulo.removeprefix(base))
    arranjos = {a for a in ARRANJOS if contem(subtitulo, a)}
    edicao_curta = EDICAO_CURTA.search(subtitulo) is not None
    artistas = tuple(dict.fromkeys(filter(None, (
        normalizar_artista(artista), normalizar_artista(re.split(r"\s*[,;]\s*|\s+feat\.?\s+", artista,
                                                               maxsplit=1, flags=re.I)[0])))))
    if not faixa or not artistas:
        return []

    candidatos = []
    vistos = set()
    for ordem, entrada in enumerate(entradas):
        if not isinstance(entrada, dict):
            continue
        video = entrada.get("id") or ""
        if not ID_VIDEO.fullmatch(video) or video in vistos:
            continue
        titulo_video = entrada.get("title") or ""
        canal = normalizar_artista(entrada.get("channel") or entrada.get("uploader") or "")
        canal_artista = any(canal in (a, f"{a} official", f"{a} oficial",
                                     f"{a} vevo", f"{a.replace(' ', '')}vevo") for a in artistas)
        if canal.endswith(" topic") and not canal_artista:
            continue

        partes = re.split(r"\s+[-–—]\s+|:\s+", titulo_video, maxsplit=1)
        if len(partes) == 2 and contem_artista(partes[0], artistas):
            faixa_video, prefixo = partes[1], ""
        elif len(partes) == 2 and contem_artista(partes[1], artistas):
            faixa_video, prefixo = partes[0], ""
        else:
            faixa_video = titulo_video
            nome_completo = normalizar(titulo_video)
            prefixo = next((a + " " for a in artistas if nome_completo.startswith(a + " ")), "")
            if not prefixo and not canal_artista:
                continue

        extras = extras_do_titulo(faixa_video, faixa, prefixo)
        if extras is None:
            continue

        if (any(contem(extras, palavra) for palavra in IGNORAR)
                or {a for a in ARRANJOS if contem(extras, a)} != arranjos):
            continue
        remix = MARCADOR_REMIX.search(extras) is not None
        if remix and not MARCADOR_REMIX.search(subtitulo):
            continue
        oficial = (any(contem(extras, t) for t in OFICIAL)
                   and not any(contem(extras, t) for t in ("unofficial", "nao oficial", "fan made")))
        tem_rotulo = oficial or any(contem(extras, t) for t in MARCADOR_VIDEO)
        segundos = entrada.get("duration") or 0
        diferenca = abs(segundos - duracao) if segundos and duracao else 0
        margem = max(45, duracao * 0.3) if oficial else max(20, duracao * 0.2)
        integral = (edicao_curta and oficial and segundos > duracao
                    and diferenca <= min(360, duracao * 1.2))
        fora_da_margem = bool(segundos and duracao and diferenca > margem)
        if fora_da_margem and not integral:
            continue
        fonte_conhecida = canal_artista or (oficial and entrada.get("channel_is_verified") is True)
        # Em canais de artista, uploads com só o título frequentemente são áudio do catálogo.
        so_titulo_no_canal = not oficial and canal_artista and normalizar(titulo_video) == faixa
        prioridade = (not oficial, remix, fora_da_margem, so_titulo_no_canal, not fonte_conhecida,
                      not tem_rotulo, diferenca, ordem)
        candidatos.append((prioridade, CandidatoClipe(video, oficial, fora_da_margem)))
        vistos.add(video)
    return [c for _, c in sorted(candidatos)]


def referencias_de_audio(entradas, musica, duracao, video):
    titulo, artista = musica
    faixa = normalizar(titulo)
    nomes = {normalizar(artista), normalizar(re.split(r"[,;]", artista, maxsplit=1)[0])}
    nomes |= {normalizar(re.sub(r"\s+and\s+|&", " ", artista, flags=re.I)),
              normalizar(artista.replace("&", " and "))}
    prefixos = sorted(filter(None, nomes), key=len, reverse=True)
    candidatos = {}
    for entrada in entradas:
        if not isinstance(entrada, dict):
            continue
        id_audio = entrada.get("id") or ""
        if not ID_VIDEO.fullmatch(id_audio) or id_audio == video:
            continue
        canal = normalizar(entrada.get("channel") or entrada.get("uploader") or "")
        catalogo = canal.endswith(" topic") and canal.removesuffix(" topic") in nomes
        if not catalogo and (canal not in nomes or entrada.get("channel_is_verified") is not True):
            continue
        nome = normalizar(entrada.get("title") or "")
        if nome != faixa:
            nome = next((nome.removeprefix(a + " ") for a in prefixos if nome.startswith(a + " ")), nome)
        nome = re.sub(r"\s+(?:official audio|audio oficial)$", "", nome)
        if nome != faixa and nome not in (f"{faixa} single version", f"{faixa} album version"):
            continue
        segundos = entrada.get("duration") or 0
        if not segundos or abs(segundos - duracao) > max(8, min(12, duracao * 0.04)):
            continue
        candidatos[id_audio] = (nome != faixa, not catalogo, abs(segundos - duracao))
    return sorted(candidatos, key=candidatos.__getitem__)


def identidade_audio(snap):
    musica = snap.get("musica")
    album = snap.get("pedido_capa_album") or {}
    return musica, album.get("album") if album.get("musica") == musica else None


def video_tem_movimento(arquivo):
    lado = 48
    pixels = lado * lado
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", "3", "-i", str(arquivo),
           "-t", "26", "-map", "0:v:0", "-an", "-vf", f"fps=1/2,scale={lado}:{lado}",
           "-pix_fmt", "gray", "-f", "rawvideo", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=True, timeout=6, creationflags=SEM_JANELA)
    except (OSError, subprocess.SubprocessError):
        return False
    imagens = [proc.stdout[i:i + pixels] for i in range(0, len(proc.stdout), pixels)]
    mudancas = sum(sum(abs(x - y) for x, y in zip(a, b)) >= 4 * pixels
                   for a, b in zip(imagens, imagens[1:]))
    return mudancas >= 2


def posicao_atual(snap, agora):
    extra = min(max(agora - snap.get("t_poll", agora), 0.0), 1.0) if snap.get("tocando") else 0.0
    return max(0.0, snap.get("pos", 0.0) + extra)


def amostra_posicao(snap):
    return snap.get("pos", 0.0), snap.get("t_poll", 0.0)


def mudou_posicao(anterior, atual):
    posicao, instante = atual
    posicao_anterior, instante_anterior = anterior
    # Cada estimativa interpolada entre amostras tem o mesmo t_poll.
    return (instante > instante_anterior
            and abs(posicao - posicao_anterior - (instante - instante_anterior)) > TOLERANCIA)


def chave_reproducao(snap):
    if snap.get("tocando") and snap.get("musica") and all(snap["musica"]):
        return (snap["musica"], snap.get("reproducao_id"))


def comando_youtube():
    if importlib.util.find_spec("yt_dlp") is not None:
        base = [sys.executable, "-m", "yt_dlp"]
    else:
        local = Path(__file__).resolve().parent / ".venv" / "Scripts" / "yt-dlp.exe"
        if not local.is_file():
            raise RuntimeError("Instale o yt-dlp no ambiente do Turing Vinyl.")
        base = [str(local)]
    base += ["--ignore-config", "--no-plugin-dirs", "--no-cache-dir",
             "--encoding", "utf-8", "--socket-timeout", "6", "--retries", "0",
             "--extractor-retries", "0", "--no-warnings", "--quiet"]
    if shutil.which("node"):
        base += ["--js-runtimes", "node"]
    return base


class QuadrosClipe:
    """Fila curta entre o decodificador e o relógio do vídeo H.264."""

    def __init__(self):
        self._fila = queue.Queue(maxsize=FILA_QUADROS)
        self._atual = self._proximo = self._offset = None
        self._direcao_desvio = 0
        self._proxima_correcao = 0.0
        self.ajustes_suaves = 0
        self.reposicionar = threading.Event()
        self.ajuste_audio_em = None
        self.posicao_apresentada = None
        self.fim_leitura = threading.Event()
        self.esgotado = threading.Event()

    def adicionar(self, posicao, imagem, cancelar):
        while not cancelar.is_set():
            try:
                self._fila.put((posicao, imagem), timeout=INTERVALO)
                return
            except queue.Full:
                pass

    def _posicao_alvo(self, posicao_spotify, agora):
        offset = posicao_spotify - agora
        desvio = offset - self._offset if self._offset is not None else 0.0
        if self._offset is None or abs(desvio) > TOLERANCIA:
            self._offset = offset
            self._direcao_desvio = 0
        else:
            # Oscilações curtas do relógio do Windows e da fila USB não mudam a cadência.
            direcao = 0 if abs(desvio) < DESVIO_SUAVE else (1 if desvio > 0 else -1)
            if direcao != self._direcao_desvio:
                self._direcao_desvio = direcao
                self._proxima_correcao = agora + CONFIRMAR_DESVIO
            elif direcao and agora >= self._proxima_correcao:
                self._offset += direcao / FPS_CLIPE
                self._proxima_correcao = agora + INTERVALO_CORRECAO
                self.ajustes_suaves += 1
        return agora + self._offset

    def quadro(self, posicao_spotify, agora):
        alvo = self._posicao_alvo(posicao_spotify, agora)
        for _ in range(FILA_QUADROS + 1):
            if self._proximo is None:
                try:
                    self._proximo = self._fila.get_nowait()
                except queue.Empty:
                    break
            if self._proximo[0] > alvo + 0.5 / FPS_CLIPE:
                break
            self._atual, self._proximo = self._proximo, None

        if (self.fim_leitura.is_set() and self._proximo is None and self._fila.empty()
                and (self._atual is None or alvo - self._atual[0] >= 1 / FPS_CLIPE)):
            self.esgotado.set()
            return None

        tolerancia = DESVIO_PRIMEIRO_QUADRO if self.posicao_apresentada is None else TOLERANCIA
        if self._atual is not None and abs(self._atual[0] - posicao_spotify) <= tolerancia:
            self.posicao_apresentada = self._atual[0]
            return self._atual[1]


class FonteClipe:
    """Busca e decodifica fora da thread que renderiza a tela."""

    def __init__(self, ler_estado, ativo, publicar, log, ler_adiantamento_ms=lambda: 0):
        self.ler_estado, self.ativo, self.publicar, self.log = ler_estado, ativo, publicar, log
        self.ler_adiantamento_ms = ler_adiantamento_ms
        self._parar = threading.Event()
        self._thread = threading.Thread(target=self._rodar, name="ModoClipe", daemon=True)
        self._fluxo = None
        self._edicao_ativa = None
        self._ultimo_quadro = None
        self._ultima_posicao = None
        self._congelado_ate = 0.0
        self._status = ""
        self._busca = {}

    def iniciar(self):
        self._thread.start()

    def fechar(self):
        self._parar.set()
        self._thread.join(timeout=3)

    def quadro(self, snap, agora):
        chave = chave_reproducao(snap)
        if chave is None:
            self._ultimo_quadro = self._ultima_posicao = None
            self._congelado_ate = 0.0
            return
        instante = time.monotonic()
        posicao = self._posicao_tela(snap, instante)
        amostra = amostra_posicao(snap)
        anterior = self._ultima_posicao
        self._ultima_posicao = chave, amostra
        fluxo = self._fluxo
        ultimo = self._ultimo_quadro
        if anterior is None or anterior[0][0] != chave[0]:
            ultimo = self._ultimo_quadro = None
            self._congelado_ate = 0.0
        elif ultimo is not None:
            chave_anterior, amostra_anterior = anterior
            if chave != chave_anterior or mudou_posicao(amostra_anterior, amostra):
                self._congelado_ate = instante + ESPERA_REPOSICIONAR
                if fluxo is not None and fluxo[1] is ultimo[0]:
                    fluxo[1].reposicionar.set()

        edicao_ativa = self._edicao_ativa
        if edicao_ativa and edicao_ativa[0] == chave:
            edicao = edicao_ativa[1]
            if trecho_da_edicao(edicao, posicao) is None:
                proximo = next((t for t in edicao if t.inicio > posicao), None)
                if (ultimo is not None and posicao >= edicao[0].fim and proximo is not None
                        and proximo.inicio - posicao <= 8):
                    # Durante o trecho suprimido pelo clipe, segura o último quadro compatível.
                    self._congelado_ate = instante + (proximo.inicio - posicao) + ESPERA_REPOSICIONAR
                    return ultimo[1]
                self._ultimo_quadro = None
                return

        if fluxo is not None and fluxo[0] == chave:
            quadros = fluxo[1]
            if not quadros.reposicionar.is_set():
                imagem = quadros.quadro(posicao, agora)
                if imagem is not None:
                    self._ultimo_quadro = quadros, imagem
                    self._congelado_ate = 0.0
                    return imagem
        if ultimo is not None:
            ajuste_em = ultimo[0].ajuste_audio_em
            if (instante < self._congelado_ate
                    or ajuste_em is not None and instante < ajuste_em + ESPERA_REPOSICIONAR):
                return ultimo[1]
        self._ultimo_quadro = None

    def _chave(self, snap=None):
        if not self._parar.is_set() and self.ativo():
            return chave_reproducao(self.ler_estado() if snap is None else snap)

    def _mensagem(self, texto):
        if self._status != texto:
            self._status = texto
            self.publicar(texto)

    def _youtube(self, args, chave, timeout=16, ativo=None):
        limite = time.monotonic() + timeout
        with subprocess.Popen(comando_youtube() + args, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              creationflags=SEM_JANELA) as proc:
            try:
                while (chave is None or self._chave() == chave) and (ativo is None or ativo()):
                    if time.monotonic() >= limite:
                        raise RuntimeError("O YouTube demorou para responder.")
                    try:
                        saida, _ = proc.communicate(timeout=INTERVALO)
                        if proc.returncode:
                            raise RuntimeError("O YouTube não disponibilizou o vídeo.")
                        return saida
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()

    def _buscar_clipes(self, snap, chave):
        musica = chave[0]
        duracao = snap.get("dur") or 0
        if musica not in self._busca:
            titulo, artista = musica
            faixa = titulo_base(titulo)
            subtitulo = normalizar(titulo.removeprefix(faixa))
            arranjos = [a for a in ARRANJOS if contem(subtitulo, a)]
            consulta = " ".join([artista, faixa, *arranjos])
            entradas = []
            candidatos = []
            falha = None
            for texto in (f"{consulta} official music video", consulta):
                try:
                    saida = self._youtube(["--flat-playlist", "--dump-single-json", "--",
                                           f"ytsearch12:{texto}"], chave)
                except RuntimeError as exc:
                    falha = exc
                    continue
                if saida is None:
                    return []
                entradas.extend(json.loads(saida).get("entries") or [])
                candidatos = classificar_clipes(entradas, musica, duracao)
                if candidatos and candidatos[0].oficial and not candidatos[0].exigir_movimento:
                    break
            if not candidatos or not candidatos[0].oficial:
                confirmada = self._confirmar_canal(entradas, candidatos, musica, duracao, chave)
                if confirmada:
                    candidatos = classificar_clipes([*entradas, confirmada], musica, duracao)
            if falha is not None and not entradas:
                raise falha
            if candidatos:
                if len(self._busca) >= 64:
                    self._busca.pop(next(iter(self._busca)))
                self._busca[musica] = candidatos
            return candidatos
        return self._busca[musica]

    def _confirmar_canal(self, entradas, candidatos, musica, duracao, chave):
        resultados = [e for e in entradas if isinstance(e, dict)]
        conhecidos = {c.id for c in candidatos}
        conhecidos.update(e.get("id") for e in resultados if e.get("channel") or e.get("uploader"))
        incompletas = [dict(e, channel=musica[1]) for e in resultados if e.get("id") not in conhecidos]
        # Só consulta páginas com título compatível. O canal usado acima serve apenas para essa triagem.
        for candidato in classificar_clipes(incompletas, musica, duracao)[:2]:
            args = ["--no-playlist", "--skip-download", "--dump-single-json", "--",
                    f"https://www.youtube.com/watch?v={candidato.id}"]
            try:
                saida = self._youtube(args, chave, timeout=5)
            except RuntimeError:
                continue
            if saida is None:
                break
            entrada = json.loads(saida)
            if entrada.get("id") == candidato.id and classificar_clipes([entrada], musica, duracao):
                return entrada

    def _baixar_video(self, video, pasta, chave, timeout):
        saida = self._youtube(["--no-playlist", "--no-part", "--max-filesize", "45M",
                               "--match-filters", "description !^=? 'Provided to YouTube by'",
                               "-f", "b[height<=480][protocol=https]/bv[height<=480][protocol=https]",
                               "-o", str(pasta / f"clipe.{video}.%(ext)s"), "--",
                               f"https://www.youtube.com/watch?v={video}"], chave, timeout=timeout)
        if saida is not None:
            return next(pasta.glob(f"clipe.{video}.*"), None)

    def _baixar_audio_do_clipe(self, arquivo, pasta, ativo):
        video = arquivo.stem.removeprefix("clipe.")
        if ID_VIDEO.fullmatch(video) and ativo():
            chave = self._chave()
            self._youtube(["--no-playlist", "--no-part", "--max-filesize", "12M",
                           "-f", "ba[protocol=https]", "-o", str(pasta / "audio.%(ext)s"),
                           "--", f"https://www.youtube.com/watch?v={video}"],
                          chave, timeout=20, ativo=ativo)
            return next(pasta.glob("audio.*"), None)

    def _baixar_referencia(self, arquivo, pasta, ativo):
        snap = self.ler_estado()
        musica, album = identidade_audio(snap)
        duracao = snap.get("dur") or 0
        if not musica or not duracao or not ativo():
            return
        titulo, artista = musica
        video = arquivo.stem.removeprefix("clipe.")
        entradas = []
        for consulta in dict.fromkeys((
                " ".join(filter(None, (artista, titulo, album, "official audio"))),
                f"{artista} {titulo}")):
            try:
                busca = ["--flat-playlist", "--dump-single-json", "--", f"ytsearch18:{consulta}"]
                saida = self._youtube(busca, None, timeout=9, ativo=ativo)
            except RuntimeError:
                continue
            if not saida or not ativo():
                return
            entradas.extend(json.loads(saida).get("entries") or [])
        for id_audio in referencias_de_audio(entradas, musica, duracao, video)[:4]:
            try:
                args = ["--no-playlist", "--no-part", "--max-filesize", "12M",
                        "--match-filters", "description ^= 'Provided to YouTube by'",
                        "-f", "ba[protocol=https]", "-o", str(pasta / f"referencia.{id_audio}.%(ext)s"),
                        "--", f"https://www.youtube.com/watch?v={id_audio}"]
                saida = self._youtube(args, None, timeout=12, ativo=ativo)
            except RuntimeError:
                continue
            if not ativo():
                return
            if saida is not None:
                audio = next(pasta.glob(f"referencia.{id_audio}.*"), None)
                if audio:
                    return audio

    def _carregar_clipe(self, snap, chave, pasta):
        candidatos = self._buscar_clipes(snap, chave)
        if not candidatos:
            return
        self._mensagem("Carregando clipe…")
        limite = time.monotonic() + 48
        ultima_falha = None
        for candidato in candidatos[:5]:
            restante = limite - time.monotonic()
            if self._chave() != chave or restante <= 0:
                break
            try:
                arquivo = self._baixar_video(candidato.id, pasta, chave, timeout=min(40, restante))
                if arquivo and (candidato.oficial and not candidato.exigir_movimento
                                or video_tem_movimento(arquivo)):
                    return arquivo
            except RuntimeError as exc:
                ultima_falha = exc
            for parcial in pasta.glob("clipe.*"):
                parcial.unlink(missing_ok=True)
        if ultima_falha is not None and self._chave() == chave:
            raise ultima_falha

    def _ler_quadros(self, proc, inicio, quadros, cancelar):
        indice = 0
        try:
            while not cancelar.is_set():
                dados = proc.stdout.read(BYTES_QUADRO)
                if len(dados) != BYTES_QUADRO:
                    return
                imagem = Image.frombytes("RGB", (LADO, LADO), dados)
                quadros.adicionar(inicio + indice / FPS_CLIPE, imagem, cancelar)
                indice += 1
        finally:
            quadros.fim_leitura.set()

    def _posicao_no_instante(self, instante):
        posicao, amostra_em = amostra_posicao(self.ler_estado())
        return max(0.0, posicao + instante - amostra_em) if amostra_em else posicao

    def _posicao_tela(self, snap, agora):
        return max(0.0, posicao_atual(snap, agora) + self.ler_adiantamento_ms() / 1000)

    def _reproduzir(self, arquivo, chave, ajuste_audio, audio):
        """Retorna True quando o fluxo deve ser reaberto em outra posição."""
        snap = self.ler_estado()
        anterior = amostra_posicao(snap)
        adiantamento = self.ler_adiantamento_ms()
        pos = self._posicao_tela(snap, time.monotonic())
        edicao = ajuste_audio.edicao
        self._edicao_ativa = (chave, edicao) if edicao else None
        trecho = trecho_da_edicao(edicao, pos)
        if edicao and trecho is None:
            while self._chave() == chave and identidade_audio(self.ler_estado()) == audio:
                agora = time.monotonic()
                pos = self._posicao_tela(self.ler_estado(), agora)
                if ajuste_audio.edicao is not edicao or trecho_da_edicao(edicao, pos):
                    return True
                ajuste_audio.solicitar(agora, lambda: self._chave() == chave
                                        and identidade_audio(self.ler_estado()) == audio,
                                        self._posicao_no_instante)
                self._parar.wait(INTERVALO)
            return False
        deslocamento = ajuste_audio.deslocamento
        inicio = max(0.0, round(trecho.posicao_video(pos) if trecho else pos + deslocamento, 3))
        origem = trecho.posicao_faixa(inicio) if trecho else inicio - deslocamento
        filtro = FILTRO_CLIPE
        if trecho is not None and trecho.taxa != 1.0:
            filtro = f"setpts=PTS/{trecho.taxa:.8f},{filtro}"
        args = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-ss", str(inicio), "-i", str(arquivo),
                "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", filtro,
                "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
        cancelar = threading.Event()
        quadros = QuadrosClipe()
        with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, creationflags=SEM_JANELA) as proc:
            leitor = threading.Thread(target=self._ler_quadros,
                                      args=(proc, origem, quadros, cancelar), daemon=True)
            leitor.start()
            self._fluxo = chave, quadros
            try:
                prazo_quadro = time.monotonic() + 12
                while self._chave() == chave:
                    agora = time.monotonic()
                    snap = self.ler_estado()
                    if identidade_audio(snap) != audio:
                        break
                    amostra = amostra_posicao(snap)
                    if quadros.reposicionar.is_set() or mudou_posicao(anterior, amostra):
                        return True
                    anterior = amostra
                    pos = self._posicao_tela(snap, agora)
                    if (self.ler_adiantamento_ms() != adiantamento or ajuste_audio.edicao is not edicao
                            or trecho is not None and trecho_da_edicao(edicao, pos) != trecho
                            or not edicao and abs(ajuste_audio.deslocamento - deslocamento) >= DESVIO_APLICAR):
                        quadros.ajuste_audio_em = agora
                        quadros.reposicionar.set()
                        return True
                    ajuste_audio.solicitar(
                        agora, lambda: not cancelar.is_set() and self._chave() == chave
                        and identidade_audio(self.ler_estado()) == audio,
                        self._posicao_no_instante)
                    apresentado = quadros.posicao_apresentada
                    if apresentado is not None and abs(apresentado - pos) < TOLERANCIA:
                        if self._status:
                            self.log("clipe: quadros prontos para a tela USB")
                            self._mensagem("")
                        prazo_quadro = agora + 8
                    if (quadros.esgotado.is_set() or agora >= prazo_quadro) and not ajuste_audio.preparando:
                        break
                    quadros.reposicionar.wait(INTERVALO)
            finally:
                self._fluxo = None
                cancelar.set()
                if proc.poll() is None:
                    proc.kill()
                leitor.join(timeout=2)
                if quadros.ajustes_suaves:
                    self.log(f"clipe: ajustes graduais de sincronia={quadros.ajustes_suaves}")
        return False

    def _rodar(self):
        tentado = fim = ultimo_adiantamento = None
        musica_salva = arquivo = ajuste_audio = None
        with TemporaryDirectory(prefix="turing-vinyl-clipe-") as diretorio:
            pasta = Path(diretorio)
            while not self._parar.is_set():
                snap = self.ler_estado()
                chave = self._chave(snap)
                audio = identidade_audio(snap)
                adiantamento = self.ler_adiantamento_ms()
                if chave is None:
                    tentado = fim = None
                    self._edicao_ativa = None
                    self._mensagem("")
                    if not self.ativo() and arquivo is not None:
                        ajuste_audio.aguardar()
                        arquivo.unlink(missing_ok=True)
                        musica_salva = arquivo = ajuste_audio = None
                elif ((chave, audio) != tentado or
                      fim is not None and (adiantamento != ultimo_adiantamento
                                           or posicao_atual(snap, time.monotonic()) < fim - TOLERANCIA)):
                    tentado, fim = (chave, audio), None
                    self._mensagem("Buscando clipe no YouTube…")
                    try:
                        if musica_salva != audio:
                            if ajuste_audio is not None:
                                ajuste_audio.aguardar()
                            for anterior in pasta.glob("clipe.*"):
                                anterior.unlink()
                            musica_salva = arquivo = ajuste_audio = None
                            arquivo = self._carregar_clipe(snap, chave, pasta)
                            if arquivo:
                                musica_salva = audio
                                ajuste_audio = AjustePorAudio(
                                    arquivo, self.log,
                                    lambda pasta, ativo, arquivo=arquivo:
                                    self._baixar_audio_do_clipe(arquivo, pasta, ativo),
                                    lambda pasta, ativo, arquivo=arquivo:
                                    self._baixar_referencia(arquivo, pasta, ativo),
                                    lambda audio=audio: self._chave() is not None
                                    and identidade_audio(self.ler_estado()) == audio)
                        if arquivo:
                            while (self._chave() == chave and identidade_audio(self.ler_estado()) == audio
                                   and self._reproduzir(arquivo, chave, ajuste_audio, audio)):
                                pass
                            fim = posicao_atual(self.ler_estado(), time.monotonic())
                            ultimo_adiantamento = self.ler_adiantamento_ms()
                        if self._chave() == chave:
                            self._mensagem("Clipe indisponível · mostrando o vinil")
                    except (OSError, ValueError, RuntimeError) as exc:
                        self.log(f"clipe: {type(exc).__name__}")
                        if self._chave() == chave:
                            self._mensagem(str(exc) if isinstance(exc, RuntimeError)
                                           else "Falha ao carregar o clipe.")
                self._parar.wait(INTERVALO)
            if ajuste_audio is not None:
                ajuste_audio.aguardar()
