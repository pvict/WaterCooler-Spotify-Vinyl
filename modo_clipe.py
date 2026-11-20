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
import unicodedata

from PIL import Image

from ao_vivo import BYTES_QUADRO, LADO, SEM_JANELA


FPS_CLIPE = 30
FILA_QUADROS = 8
FILTRO_CLIPE = (
    f"fps={FPS_CLIPE},"
    f"scale='ceil(max(1,dar)*{LADO}/2)*2':'ceil(max(1,1/dar)*{LADO}/2)*2',"
    f"crop={LADO}:{LADO},setsar=1"
)
INTERVALO = 0.10
TOLERANCIA = 1.0
ID_VIDEO = re.compile(r"[A-Za-z0-9_-]{11}")
VERSOES = ("ao vivo", "live", "acustico", "acoustic", "remix")
IGNORAR = ("lyrics", "lyric", "letra", "karaoke", "cover", "reaction", "reacao",
           "fan made", "unofficial", "nao oficial", "visualizer", "audio", "speed up",
           "sped up", "slowed", "instrumental", "traducao")
OFICIAL = ("official music video", "official video", "official hd video",
           "official 4k video", "clipe oficial", "video oficial", "videoclipe oficial",
           "official mv", "music video", "videoclipe", "video musical", "m v")
SUFIXO_SPOTIFY = re.compile(
    r"\s+(?:[-–—]\s+|[\[(])(?=(?:\d{4}\s+)?(?:remaster|radio edit|single version|"
    r"album version|original mix|ao vivo|live\b|ac[uú]stic|acoustic|remix|feat\b))", re.I)
ROTULOS_FINAIS = re.compile(r"(?:\s*[\[(][^\])]*[\])])+\s*$")


def normalizar(texto):
    sem_acentos = "".join(c for c in unicodedata.normalize("NFKD", texto.casefold())
                          if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", sem_acentos))


def contem(texto, trecho):
    return bool(trecho) and f" {trecho} " in f" {texto} "


def titulo_base(titulo):
    return SUFIXO_SPOTIFY.split(titulo, maxsplit=1)[0].strip()


def escolher_clipe(entradas, musica, duracao):
    titulo, artista = musica
    faixa = normalizar(titulo_base(titulo))
    artista_principal = normalizar(re.split(r"\s*[,;]\s*|\s+feat\.?\s+", artista,
                                          maxsplit=1, flags=re.I)[0])
    titulo_completo = normalizar(titulo)
    versoes = {v for v in VERSOES if contem(titulo_completo, v)}
    candidatos = []
    for indice, entrada in enumerate(entradas):
        if not isinstance(entrada, dict) or not ID_VIDEO.fullmatch(entrada.get("id") or ""):
            continue
        titulo_video = entrada.get("title") or ""
        partes = re.split(r"\s+[-–—]\s+", titulo_video, maxsplit=1)
        faixa_video = (partes[1] if len(partes) == 2 and contem(normalizar(partes[0]), artista_principal)
                       else titulo_video)
        nome = normalizar(faixa_video)
        canal = normalizar(entrada.get("channel") or entrada.get("uploader") or "")
        oficial = any(contem(nome, palavra) for palavra in OFICIAL)
        diferenca = abs((entrada.get("duration") or 0) - duracao) if duracao else 0
        if (not contem(nome, faixa) or not artista_principal
                or not (contem(normalizar(titulo_video), artista_principal) or contem(canal, artista_principal))
                or any(contem(nome, palavra) and not contem(titulo_completo, palavra)
                       for palavra in IGNORAR)
                or {v for v in VERSOES if contem(nome, v)} != versoes
                or contem(canal, "topic")
                or not oficial
                or duracao and diferenca > max(20, duracao * 0.2)):
            continue
        titulo_sem_rotulos = normalizar(titulo_base(ROTULOS_FINAIS.sub("", faixa_video)))
        candidatos.append((titulo_sem_rotulos != faixa, diferenca, indice, entrada["id"]))
    return min(candidatos)[-1] if candidatos else None


def posicao_atual(snap, agora):
    extra = min(max(agora - snap.get("t_poll", agora), 0.0), 1.0) if snap.get("tocando") else 0.0
    return max(0.0, snap.get("pos", 0.0) + extra)


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

    def quadro(self, posicao_spotify, agora):
        if self._offset is None or abs(agora + self._offset - posicao_spotify) > TOLERANCIA:
            self._offset = posicao_spotify - agora
        alvo = agora + self._offset
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

        if self._atual is not None and abs(self._atual[0] - posicao_spotify) <= TOLERANCIA:
            self.posicao_apresentada = self._atual[0]
            return self._atual[1]


class FonteClipe:
    """Busca e decodifica fora da thread que renderiza a tela."""

    def __init__(self, ler_estado, ativo, publicar, log):
        self.ler_estado, self.ativo, self.publicar, self.log = ler_estado, ativo, publicar, log
        self._parar = threading.Event()
        self._thread = threading.Thread(target=self._rodar, name="ModoClipe", daemon=True)
        self._fluxo = None
        self._status = ""
        self._busca = {}

    def iniciar(self):
        self._thread.start()

    def fechar(self):
        self._parar.set()
        self._thread.join(timeout=3)

    def quadro(self, snap, agora):
        fluxo = self._fluxo
        if fluxo is not None and fluxo[0] == chave_reproducao(snap):
            return fluxo[1].quadro(posicao_atual(snap, time.monotonic()), agora)

    def _chave(self, snap=None):
        if not self._parar.is_set() and self.ativo():
            return chave_reproducao(self.ler_estado() if snap is None else snap)

    def _mensagem(self, texto):
        if self._status != texto:
            self._status = texto
            self.publicar(texto)

    def _youtube(self, args, chave, timeout=16):
        limite = time.monotonic() + timeout
        with subprocess.Popen(comando_youtube() + args, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              creationflags=SEM_JANELA) as proc:
            try:
                while self._chave() == chave:
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

    def _buscar_video(self, snap, chave):
        musica = chave[0]
        if musica not in self._busca:
            faixa = musica[0] if any(contem(normalizar(musica[0]), v) for v in VERSOES) else titulo_base(musica[0])
            consulta = f"{musica[1]} {faixa} official music video"
            saida = self._youtube(["--flat-playlist", "--dump-single-json", "--",
                                   f"ytsearch8:{consulta}"], chave)
            if saida is None:
                return
            dados = json.loads(saida)
            if len(self._busca) >= 64:
                self._busca.pop(next(iter(self._busca)))
            self._busca[musica] = escolher_clipe(dados.get("entries") or [], musica, snap.get("dur", 0))
        return self._busca[musica]

    def _baixar_video(self, video, pasta, chave):
        saida = self._youtube(["--no-playlist", "--no-part", "--max-filesize", "45M",
                               "-f", "b[height<=480][protocol=https]/bv[height<=480][protocol=https]",
                               "-o", str(pasta / "clipe.%(ext)s"), "--",
                               f"https://www.youtube.com/watch?v={video}"], chave, timeout=40)
        if saida is not None:
            return next(pasta.glob("clipe.*"), None)
        for parcial in pasta.glob("clipe.*"):
            parcial.unlink()

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

    def _reproduzir(self, arquivo, chave):
        """Retorna True em um salto que exige reabrir o fluxo na nova posição."""
        agora = time.monotonic()
        pos = posicao_atual(self.ler_estado(), agora)
        anterior = (pos, agora)
        inicio = round(pos, 3)
        args = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-ss", str(inicio), "-i", str(arquivo),
                "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", FILTRO_CLIPE,
                "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
        cancelar = threading.Event()
        quadros = QuadrosClipe()
        with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, creationflags=SEM_JANELA) as proc:
            leitor = threading.Thread(target=self._ler_quadros,
                                      args=(proc, inicio, quadros, cancelar), daemon=True)
            leitor.start()
            self._fluxo = chave, quadros
            try:
                prazo_quadro = time.monotonic() + 12
                while self._chave() == chave and not quadros.esgotado.is_set():
                    agora = time.monotonic()
                    pos = posicao_atual(self.ler_estado(), agora)
                    if abs(pos - anterior[0] - (agora - anterior[1])) > TOLERANCIA:
                        return True
                    anterior = (pos, agora)
                    apresentado = quadros.posicao_apresentada
                    if apresentado is not None and abs(apresentado - pos) < TOLERANCIA:
                        if self._status:
                            self.log("clipe: quadros prontos para a tela USB")
                            self._mensagem("")
                        prazo_quadro = agora + 8
                    if agora >= prazo_quadro:
                        break
                    self._parar.wait(INTERVALO)
            finally:
                self._fluxo = None
                cancelar.set()
                if proc.poll() is None:
                    proc.kill()
                leitor.join(timeout=2)
        return False

    def _rodar(self):
        tentado = fim = None
        musica_salva = arquivo = None
        with TemporaryDirectory(prefix="turing-vinyl-clipe-") as diretorio:
            pasta = Path(diretorio)
            while not self._parar.is_set():
                snap = self.ler_estado()
                chave = self._chave(snap)
                if chave is None:
                    tentado = fim = None
                    self._mensagem("")
                    if not self.ativo() and arquivo is not None:
                        arquivo.unlink(missing_ok=True)
                        musica_salva = arquivo = None
                elif (chave != tentado or
                      fim is not None and posicao_atual(snap, time.monotonic()) < fim - TOLERANCIA):
                    tentado, fim = chave, None
                    self._mensagem("Buscando clipe no YouTube…")
                    try:
                        if musica_salva != chave[0]:
                            for anterior in pasta.glob("clipe.*"):
                                anterior.unlink()
                            musica_salva = arquivo = None
                            video = self._buscar_video(snap, chave)
                            if video and self._chave() == chave:
                                self._mensagem("Carregando clipe…")
                                arquivo = self._baixar_video(video, pasta, chave)
                                if arquivo:
                                    musica_salva = chave[0]
                        if arquivo:
                            while self._chave() == chave and self._reproduzir(arquivo, chave):
                                pass
                            fim = posicao_atual(self.ler_estado(), time.monotonic())
                        if self._chave() == chave:
                            self._mensagem("Clipe indisponível · mostrando o vinil")
                    except (OSError, ValueError, RuntimeError) as exc:
                        self.log(f"clipe: {type(exc).__name__}")
                        if self._chave() == chave:
                            self._mensagem(str(exc) if isinstance(exc, RuntimeError)
                                           else "Falha ao carregar o clipe.")
                self._parar.wait(INTERVALO)
