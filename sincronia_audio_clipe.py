"""Alinha o relógio da faixa à trilha do clipe usando assinaturas acústicas locais."""

from pathlib import Path
import math
from statistics import linear_regression, median
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
from typing import NamedTuple

from ao_vivo import SEM_JANELA


PASSO = 1365 / 11025  # Janela da configuração padrão do Chromaprint, em segundos.
AMOSTRA = 12
INTERVALO_MEDICAO = 22
INTERVALO_EDICAO = 13
DESVIO_APLICAR = 0.18
MARGEM_BUSCA = 60
CAPTURA = Path(__file__).with_name("captura_spotify.py")
# Cada hash descreve uma janela de áudio posterior ao instante do índice.
CENTRO_HASH = 1.9


class TrechoEdicao(NamedTuple):
    inicio: float
    fim: float
    deslocamento: float
    taxa: float = 1.0

    def posicao_video(self, posicao):
        return posicao * self.taxa + self.deslocamento

    def posicao_faixa(self, posicao):
        return (posicao - self.deslocamento) / self.taxa


def trecho_da_edicao(edicao, posicao):
    return next((trecho for trecho in edicao if trecho.inicio <= posicao < trecho.fim), None)


def localizar_audio(amostra, clipe, posicao_spotify, erro_maximo=0.35):
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
    if erros[melhor] > erro_maximo * bits:
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


def _estender_trecho(referencia, clipe, ancora, direcao, taxa, deslocamento):
    """Estende uma região confirmada até o áudio deixar de concordar."""
    limite = min(max(round((ancora - CENTRO_HASH) / PASSO), 0), len(referencia) - 1)
    janela = 8
    i = limite
    while 0 <= i < len(referencia):
        trecho = range(max(i - janela // 2, 0), min(i + janela // 2, len(referencia)))
        pares = [(k, round((taxa * (k * PASSO + CENTRO_HASH) + deslocamento - CENTRO_HASH) / PASSO))
                 for k in trecho]
        erros = [(referencia[k] ^ clipe[j]).bit_count() for k, j in pares if 0 <= j < len(clipe)]
        if len(erros) < janela // 2 or sum(erros) > 0.30 * 32 * len(erros):
            # Confirma por aproximadamente um segundo antes de encerrar o trecho.
            if abs(i - limite) >= janela:
                break
        else:
            limite = i
        i += direcao
    return limite * PASSO + CENTRO_HASH + (PASSO / 2 if direcao > 0 else -PASSO / 2)


def mapear_edicao(referencia, clipe, ativo):
    """Mede cortes e mudanças de velocidade a partir das duas gravações."""
    janela, passo = 75, 24
    grupos = []
    for i in range(0, len(referencia) - janela + 1, passo):
        if not ativo():
            return ()
        desvio = localizar_audio(referencia[i:i + janela], clipe, i * PASSO)
        if desvio is None:
            continue
        tempo = (i + (janela - 1) / 2) * PASSO + CENTRO_HASH
        if grupos:
            anterior, ultimo_desvio = grupos[-1][-1]
            intervalo = tempo - anterior
            if intervalo <= 18 and abs(desvio - ultimo_desvio) <= 0.25 + 0.035 * intervalo:
                grupos[-1].append((tempo, desvio))
                continue
        grupos.append([(tempo, desvio)])

    trechos = []
    for grupo in grupos:
        if len(grupo) < 3:
            continue
        tempos, desvios = zip(*grupo)
        inclinacao, _ = linear_regression(tempos, desvios)
        if abs(inclinacao) > 0.04:
            continue
        inclinacao = inclinacao if abs(inclinacao) >= 0.002 else 0.0
        taxa = 1.0 + inclinacao
        deslocamento = median(d - inclinacao * t for t, d in grupo)
        if max(abs(d - inclinacao * t - deslocamento) for t, d in grupo) > 0.32:
            continue
        inicio = _estender_trecho(referencia, clipe, tempos[0], -1, taxa, deslocamento)
        fim = _estender_trecho(referencia, clipe, tempos[-1], 1, taxa, deslocamento)
        zero_video = -deslocamento / taxa
        if inicio - max(0.0, zero_video) <= CENTRO_HASH + 0.6:
            inicio = max(0.0, zero_video)
        if trechos:
            anterior = trechos[-1]
            # Uma edição que suprimiu áudio avança a faixa sem voltar o vídeo.
            inicio = max(inicio, anterior.fim, (anterior.posicao_video(anterior.fim) - deslocamento) / taxa)
        if fim - inicio >= 5:
            trechos.append(TrechoEdicao(inicio, fim, deslocamento, taxa))
    return tuple(trechos)


def ancorar_edicao(edicao, amostra, referencia, posicao):
    """Confirma o áudio de referência com o que o Spotify está tocando agora."""
    desvio = localizar_audio(amostra, referencia, posicao)
    if desvio is None or abs(desvio) > 2.5:
        return ()
    return tuple(TrechoEdicao(max(0.0, t.inicio - desvio), t.fim - desvio,
                             t.deslocamento + t.taxa * desvio, t.taxa) for t in edicao)


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

    def __init__(self, arquivo, log, buscar_audio=None, buscar_referencia=None, ativo_faixa=None):
        self.arquivo, self.log = arquivo, log
        self.buscar_audio, self.buscar_referencia = buscar_audio, buscar_referencia
        self.ativo_faixa = ativo_faixa
        self.deslocamento = 0.0
        self.edicao = ()
        self._impressao_clipe = None
        self._mapa_referencia = None
        self._referencia_thread = None
        self._ancora_referencia = None
        self._proxima = 0.0
        self._thread = None

    @property
    def calibrando(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def preparando(self):
        return self.calibrando or (self._referencia_thread is not None and
                                   self._referencia_thread.is_alive())

    def solicitar(self, agora, ativo, posicao_em):
        if (self._impressao_clipe == () or agora < self._proxima
                or sys.platform != "win32" or sys.getwindowsversion().build < 20348
                or self.calibrando):
            return
        intervalo = INTERVALO_EDICAO if not self.edicao and abs(self.deslocamento) >= 1 else INTERVALO_MEDICAO
        self._proxima = agora + intervalo
        self._thread = threading.Thread(target=self._medir, args=(ativo, posicao_em),
                                        name="SincroniaAudioClipe", daemon=True)
        self._thread.start()

    def aguardar(self):
        for thread in (self._thread, self._referencia_thread):
            if thread is not None:
                thread.join(timeout=1)

    def _preparar_referencia(self):
        ativo = self.ativo_faixa
        try:
            with TemporaryDirectory(prefix="turing-vinyl-referencia-") as pasta:
                arquivo = self.buscar_referencia(Path(pasta), ativo)
                if arquivo is None or not ativo():
                    return
                impressao = impressao_audio(["-i", str(arquivo)], ativo)
            mapa = mapear_edicao(impressao, self._impressao_clipe, ativo)
            cobertura = sum(t.fim - t.inicio for t in mapa)
            if mapa and cobertura >= 0.50 * len(impressao) * PASSO and ativo():
                self._mapa_referencia = mapa, impressao
                self._proxima = 0.0
        except (OSError, ValueError, RuntimeError) as exc:
            self.log(f"clipe: referência de áudio indisponível ({type(exc).__name__})")
        finally:
            # Repita a busca se ela foi interrompida pela pausa ou pela troca de modo.
            if not ativo() and self._mapa_referencia is None:
                self._referencia_thread = None

    def _confirmar_referencia(self, amostra, posicao):
        if self._mapa_referencia is None:
            return
        mapa, impressao = self._mapa_referencia
        edicao = ancorar_edicao(mapa, amostra, impressao, posicao)
        if not edicao:
            return
        desvio = (edicao[0].deslocamento - mapa[0].deslocamento) / mapa[0].taxa
        if self._ancora_referencia is None or abs(desvio - self._ancora_referencia) >= 0.35:
            self._ancora_referencia = desvio
            self.edicao = edicao
            self.log(f"clipe: referência confirmada pelo Spotify, {len(edicao)} trecho(s)")

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
            if self.buscar_referencia is not None and self._referencia_thread is None:
                self._referencia_thread = threading.Thread(target=self._preparar_referencia,
                                                          name="ReferenciaAudioClipe", daemon=True)
                self._referencia_thread.start()
            bruto = _executar([sys.executable, str(CAPTURA), str(AMOSTRA)], ativo, AMOSTRA + 5)
            if len(bruto) < 8 or not ativo():
                return
            instante = struct.unpack_from("<d", bruto)[0]
            amostra = impressao_audio(["-f", "s16le", "-ar", "44100", "-ac", "2", "-i", "pipe:0"],
                                      ativo, bruto[8:])
            if not ativo():
                return
            posicao = posicao_em(instante)
            self._confirmar_referencia(amostra, posicao)
            if self.edicao:
                return
            ajuste = localizar_audio(amostra, self._impressao_clipe, posicao)
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
