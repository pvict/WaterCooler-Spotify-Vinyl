"""Alinha o relógio da faixa à trilha do clipe usando assinaturas acústicas locais."""

from pathlib import Path
import math
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time

from ao_vivo import SEM_JANELA


PASSO = 1365 / 11025  # Janela da configuração padrão do Chromaprint, em segundos.
AMOSTRA = 12
INTERVALO_MEDICAO = 22
INTERVALO_EDICAO = 13
DESVIO_APLICAR = 0.18
MARGEM_BUSCA = 60
CAPTURA = Path(__file__).with_name("captura_spotify.py")


def localizar_audio(amostra, clipe, posicao_spotify):
    """Retorna clipe − Spotify em segundos, se a melhor região for distinguível."""
    tamanho = len(amostra)
    if tamanho < 50 or len(set(amostra)) < 20:
        return
    primeiro = max(0, math.ceil((posicao_spotify - MARGEM_BUSCA) / PASSO))
    ultimo = min(len(clipe) - tamanho, math.floor((posicao_spotify + MARGEM_BUSCA) / PASSO))
    erros = []
    for i in range(primeiro, ultimo + 1):
        trecho = clipe[i:i + tamanho]
        erros.append(sum((a ^ b).bit_count() for a, b in zip(amostra, trecho)))
    if not erros:
        return
    melhor = min(range(len(erros)), key=erros.__getitem__)
    bits = tamanho * 32
    if erros[melhor] > 0.35 * bits:
        return
    # Picos vizinhos são a mesma região. Um segundo pico forte em outro ponto é ambíguo.
    concorrente = min((e for i, e in enumerate(erros) if abs(i - melhor) > 5), default=0)
    if concorrente - erros[melhor] < 0.04 * bits:
        return
    ajuste = 0.0
    if 0 < melhor < len(erros) - 1:
        esquerda, centro, direita = erros[melhor - 1:melhor + 2]
        curvatura = esquerda + direita - 2 * centro
        if curvatura:
            ajuste = 0.5 * (esquerda - direita) / curvatura
    return (primeiro + melhor + ajuste) * PASSO - posicao_spotify


def _executar(comando, ativo, timeout, entrada=None):
    prazo = time.monotonic() + timeout
    with subprocess.Popen(comando, stdin=subprocess.PIPE if entrada is not None else subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          creationflags=SEM_JANELA) as proc:
        concluiu = threading.Event()
        saida = b""

        def comunicar():
            nonlocal saida
            try:
                saida, _ = proc.communicate(entrada)
            finally:
                concluiu.set()

        # Em Windows a escrita de PCM no stdin pode bloquear dentro de communicate().
        leitor = threading.Thread(target=comunicar, name="PipeSincroniaClipe", daemon=True)
        leitor.start()
        try:
            while ativo() and time.monotonic() < prazo:
                if concluiu.wait(min(0.12, max(0, prazo - time.monotonic()))):
                    return saida if ativo() and proc.returncode == 0 else b""
        finally:
            if proc.poll() is None:
                proc.kill()
            leitor.join(timeout=1)
    return b""


def impressao_audio(entrada_ffmpeg, ativo, pcm=None):
    comando = ["ffmpeg", "-nostdin", "-loglevel", "error", *entrada_ffmpeg,
               "-map", "0:a:0", "-t", "600", "-f", "chromaprint",
               "-algorithm", "1", "-fp_format", "raw", "-"]
    saida = _executar(comando, ativo, 12, pcm)
    return tuple(x[0] for x in struct.iter_unpack("<I", saida))


class AjustePorAudio:
    """Mede em paralelo à reprodução e mantém o último ajuste confiável."""

    def __init__(self, arquivo, log, buscar_audio=None):
        self.arquivo, self.log = arquivo, log
        self.buscar_audio = buscar_audio
        self.deslocamento = 0.0
        self._impressao_clipe = None
        self._proxima = 0.0
        self._thread = None

    @property
    def calibrando(self):
        return self._thread is not None and self._thread.is_alive()

    def solicitar(self, agora, ativo, posicao_em):
        if (self._impressao_clipe == () or agora < self._proxima
                or sys.platform != "win32" or sys.getwindowsversion().build < 20348
                or self.calibrando):
            return
        intervalo = INTERVALO_EDICAO if abs(self.deslocamento) >= 1 else INTERVALO_MEDICAO
        self._proxima = agora + intervalo
        self._thread = threading.Thread(target=self._medir, args=(ativo, posicao_em),
                                        name="SincroniaAudioClipe", daemon=True)
        self._thread.start()

    def aguardar(self):
        if self._thread is not None:
            self._thread.join(timeout=1)

    def _medir(self, ativo, posicao_em):
        try:
            if self._impressao_clipe is None:
                impressao = impressao_audio(["-i", str(self.arquivo)], ativo)
                if not impressao and self.buscar_audio is not None and ativo():
                    comando = ["ffprobe", "-v", "error", "-select_streams", "a:0",
                               "-show_entries", "stream=index", "-of", "csv=p=0", str(self.arquivo)]
                    if not _executar(comando, ativo, 4) and ativo():
                        with TemporaryDirectory(prefix="turing-vinyl-audio-") as pasta:
                            audio = self.buscar_audio(Path(pasta), ativo)
                            if audio is not None and ativo():
                                impressao = impressao_audio(["-i", str(audio)], ativo)
                if not ativo():
                    return
                self._impressao_clipe = impressao
            if not self._impressao_clipe:
                return
            bruto = _executar([sys.executable, str(CAPTURA), str(AMOSTRA)], ativo, AMOSTRA + 5)
            if len(bruto) < 8 or not ativo():
                return
            instante = struct.unpack_from("<d", bruto)[0]
            amostra = impressao_audio(["-f", "s16le", "-ar", "44100", "-ac", "2", "-i", "pipe:0"],
                                      ativo, bruto[8:])
            ajuste = localizar_audio(amostra, self._impressao_clipe, posicao_em(instante)) if ativo() else None
            if ajuste is not None and ativo() and abs(ajuste - self.deslocamento) >= DESVIO_APLICAR:
                self.deslocamento = ajuste
                if abs(ajuste) >= 1:
                    self._proxima = min(self._proxima, time.monotonic() + 1.0)
                self.log(f"clipe: ajuste pelo áudio do Spotify {ajuste:+.2f}s")
        except (OSError, ValueError, struct.error) as exc:
            self.log(f"clipe: sincronização por áudio indisponível ({type(exc).__name__})")
        finally:
            if not ativo():
                self._proxima = 0.0
