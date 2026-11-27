"""Prévia FFmpeg local, com um quadro pendente e sem conexão com a tela USB."""
import queue
import json
import shutil
import subprocess
import tempfile
import threading

from PIL import Image
from ajustes_fundo import filtro_ajustes, validar_ajustes

LADO_PREVIA = 360
FLAGS = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
         | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))


class PreviaFundo:
    def __init__(self):
        self.quadros = queue.Queue(maxsize=1)
        self.eventos = queue.Queue(maxsize=2)
        self.informacoes = queue.Queue(maxsize=1)
        self._lock = threading.Lock()
        self._alterado = threading.Event()
        self._encerrar = threading.Event()
        self._pedido = None
        self._revisao = 0
        self._proc = None
        self._metadados = None
        self.thread = threading.Thread(target=self._rodar, name="PreviaFundo", daemon=True)
        self.thread.start()

    def solicitar(self, caminho, ajustes):
        with self._lock:
            self._revisao += 1
            self._pedido = (self._revisao, str(caminho), validar_ajustes(ajustes))
            revisao = self._revisao
            self._interromper()
        self._alterado.set()
        return revisao

    def _interromper(self):
        # Não espera pelo processo na thread Tk. O worker recolhe os recursos.
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except OSError:
                pass

    def _publicar(self, fila, revisao, valor):
        with self._lock:
            if self._encerrar.is_set() or revisao != self._revisao:
                return
            try:
                fila.put_nowait((revisao, valor))
            except queue.Full:
                try:
                    fila.get_nowait()
                except queue.Empty:
                    pass
                fila.put_nowait((revisao, valor))

    def _rodar(self):
        while not self._encerrar.is_set():
            self._alterado.wait()
            self._alterado.clear()
            if self._encerrar.is_set():
                break
            with self._lock:
                revisao, caminho, ajustes = self._pedido
            proc = timer = None
            try:
                ffmpeg = shutil.which("ffmpeg")
                if not ffmpeg:
                    raise RuntimeError("FFmpeg não encontrado. Confira a instalação.")
                dimensoes = self._dimensoes(caminho, revisao)
                if dimensoes is None:
                    continue
                self._publicar(self.informacoes, revisao, dimensoes)
                with tempfile.TemporaryFile() as erros:
                    comando = [ffmpeg, "-nostdin", "-v", "error", "-threads", "2",
                               "-filter_threads", "1", "-re", "-stream_loop", "-1", "-i", caminho,
                               "-map", "0:v:0", "-an", "-sn", "-dn", "-vf",
                               "fps=15," + filtro_ajustes(ajustes) + f",scale={LADO_PREVIA}:{LADO_PREVIA}",
                               "-threads", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
                    with self._lock:
                        if revisao != self._revisao or self._encerrar.is_set():
                            continue
                        proc = subprocess.Popen(comando, stdin=subprocess.DEVNULL,
                                                stdout=subprocess.PIPE, stderr=erros,
                                                creationflags=FLAGS)
                        self._proc = proc
                    timer = threading.Timer(10, self._limite_abertura, args=(proc,))
                    timer.daemon = True
                    timer.start()
                    while not self._encerrar.is_set():
                        dados = proc.stdout.read(LADO_PREVIA * LADO_PREVIA * 3)
                        if len(dados) != LADO_PREVIA * LADO_PREVIA * 3:
                            erros.seek(0)
                            detalhe = erros.read(1200).decode("utf-8", errors="replace").strip()
                            raise RuntimeError("Não foi possível mostrar a prévia. " + detalhe)
                        timer.cancel()
                        imagem = Image.frombytes("RGB", (LADO_PREVIA, LADO_PREVIA), dados)
                        self._publicar(self.quadros, revisao, imagem)
            except Exception as exc:
                self._publicar(self.eventos, revisao, str(exc))
            finally:
                if timer:
                    timer.cancel()
                if proc is not None:
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                    proc.stdout.close()
                with self._lock:
                    if self._proc is proc:
                        self._proc = None

    def _dimensoes(self, caminho, revisao):
        if self._metadados is not None and self._metadados[0] == caminho:
            return self._metadados[1]
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            raise RuntimeError("FFprobe não encontrado. Confira a instalação.")
        with self._lock:
            if revisao != self._revisao or self._encerrar.is_set():
                return None
            proc = subprocess.Popen([ffprobe, "-v", "error", "-select_streams", "v:0",
                "-show_streams", "-of", "json", caminho], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=FLAGS)
            self._proc = proc
        try:
            saida, erros = proc.communicate(timeout=8)
            with self._lock:
                if revisao != self._revisao or self._encerrar.is_set():
                    return None
            if proc.returncode:
                detalhe = erros.decode("utf-8", errors="replace").strip()
                raise RuntimeError("Não foi possível abrir o vídeo." + (" " + detalhe[:600] if detalhe else
                                   " Confira se o arquivo ainda está disponível."))
            streams = json.loads(saida).get("streams", [])
            if not streams:
                raise RuntimeError("O arquivo não contém um vídeo válido.")
            stream = streams[0]
            w, h = int(stream["width"]), int(stream["height"])
            if w <= 0 or h <= 0:
                raise RuntimeError("Dimensões do vídeo inválidas.")
            rotacao = float(stream.get("tags", {}).get("rotate", 0))
            for lado in stream.get("side_data_list", []):
                if "rotation" in lado:
                    rotacao = float(lado["rotation"])
            if round(rotacao) % 180 == 90:
                w, h = h, w
            self._metadados = (caminho, (w, h))
            return w, h
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)
            proc.stdout.close()
            proc.stderr.close()
            with self._lock:
                if self._proc is proc:
                    self._proc = None

    def _limite_abertura(self, proc):
        with self._lock:
            if self._proc is proc:
                self._interromper()

    def fechar(self):
        self._encerrar.set()
        with self._lock:
            self._interromper()
        self._alterado.set()
