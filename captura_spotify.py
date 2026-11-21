"""Amostra temporária do áudio do Spotify via loopback por processo no Windows."""

from contextlib import suppress
import ctypes as ct
from ctypes import wintypes as wt
import struct
import sys
import threading
import time

if __name__ == "__main__":
    sys.coinit_flags = 0

from comtypes import COINIT_MULTITHREADED, COMMETHOD, GUID, IUnknown
from comtypes import COMError, CoInitializeEx, CoUninitialize
from pycaw.api.audioclient import IAudioClient, WAVEFORMATEX
from psutil import Error as ErroProcesso
from pycaw.api.audiopolicy import IAudioSessionControl2
from pycaw.constants import DEVICE_STATE, EDataFlow
from pycaw.pycaw import AudioSession, AudioUtilities


TAXA_AUDIO = 44100
CANAIS_AUDIO = 2
BYTES_FRAME = CANAIS_AUDIO * 2


_Query = ct.WINFUNCTYPE(ct.HRESULT, ct.c_void_p, ct.POINTER(GUID), ct.POINTER(ct.c_void_p))
_Ref = ct.WINFUNCTYPE(wt.ULONG, ct.c_void_p)
_Completou = ct.WINFUNCTYPE(ct.HRESULT, ct.c_void_p, ct.c_void_p)
_GetResult = ct.WINFUNCTYPE(ct.HRESULT, ct.c_void_p, ct.POINTER(ct.HRESULT), ct.POINTER(ct.c_void_p))


class _TabelaAtivacao(ct.Structure):
    _fields_ = [("query", _Query), ("add_ref", _Ref), ("release", _Ref), ("completou", _Completou)]


class _Ativacao:
    _interfaces = (IUnknown._iid_, GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}"),
                   GUID("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}"))

    def __init__(self):
        self.concluiu = threading.Event()
        self.cliente = None
        self.referencias = 1
        self._tabela = _TabelaAtivacao(_Query(self._query), _Ref(self._add_ref),
                                       _Ref(self._release), _Completou(self._completou))
        self.objeto = ct.pointer(ct.pointer(self._tabela))

    def _query(self, objeto, tipo, retorno):
        if tipo.contents in self._interfaces:
            retorno[0] = objeto
            self._add_ref(objeto)
            return 0
        retorno[0] = None
        return -2147467262  # E_NOINTERFACE

    def _add_ref(self, _):
        self.referencias += 1
        return self.referencias

    def _release(self, _):
        self.referencias -= 1
        return self.referencias

    def _completou(self, _, operacao):
        try:
            tabela = ct.cast(operacao, ct.POINTER(ct.POINTER(ct.c_void_p))).contents
            obter_resultado = _GetResult(tabela[3])  # Após os três métodos de IUnknown.
            estado, interface = ct.HRESULT(), ct.c_void_p()
            if obter_resultado(operacao, ct.byref(estado), ct.byref(interface)) >= 0 and estado.value >= 0:
                self.cliente = ct.cast(interface, ct.POINTER(IAudioClient))
        finally:
            self.concluiu.set()
        return 0


class _Blob(ct.Structure):
    _fields_ = [("tamanho", wt.ULONG), ("dados", ct.c_void_p)]


class _ValorAtivacao(ct.Structure):
    _fields_ = [("tipo", wt.USHORT), ("reservados", wt.USHORT * 3), ("blob", _Blob)]


class _Captura(IUnknown):
    _iid_ = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
    _methods_ = [
        COMMETHOD([], ct.HRESULT, "GetBuffer",
                  (["out"], ct.POINTER(ct.POINTER(ct.c_ubyte)), "dados"),
                  (["out"], ct.POINTER(ct.c_uint32), "frames"),
                  (["out"], ct.POINTER(wt.DWORD), "flags"),
                  (["out"], ct.POINTER(ct.c_uint64), "posicao"),
                  (["out"], ct.POINTER(ct.c_uint64), "qpc")),
        COMMETHOD([], ct.HRESULT, "ReleaseBuffer", (["in"], ct.c_uint32, "frames")),
        COMMETHOD([], ct.HRESULT, "GetNextPacketSize", (["out"], ct.POINTER(ct.c_uint32), "frames")),
    ]


def _pid_spotify():
    for dispositivo in AudioUtilities.GetAllDevices(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value):
        with suppress(COMError, OSError):
            sessoes = dispositivo.AudioSessionManager.GetSessionEnumerator()
            for i in range(sessoes.GetCount()):
                controle = sessoes.GetSession(i).QueryInterface(IAudioSessionControl2)
                sessao = AudioSession(controle)
                if int(sessao.State) != 1:
                    continue
                with suppress(ErroProcesso):
                    proc = sessao.Process
                    if proc and proc.name().casefold() == "spotify.exe":
                        return proc.pid


def _abrir_captura(pid):
    # AUDIOCLIENT_ACTIVATION_PARAMS: loopback com inclusão só da árvore do PID.
    dados = (wt.DWORD * 3)(1, pid, 0)
    parametros = _ValorAtivacao(65, (wt.USHORT * 3)(), _Blob(ct.sizeof(dados), ct.addressof(dados)))
    ativacao = _Ativacao()
    operacao = ct.c_void_p()
    abrir = ct.windll.Mmdevapi.ActivateAudioInterfaceAsync
    abrir.argtypes = [wt.LPCWSTR, ct.POINTER(GUID), ct.POINTER(_ValorAtivacao),
                      ct.c_void_p, ct.POINTER(ct.c_void_p)]
    abrir.restype = ct.HRESULT
    resultado = abrir("VAD\\Process_Loopback", ct.byref(IAudioClient._iid_), ct.byref(parametros),
                      ct.cast(ativacao.objeto, ct.c_void_p),
                      ct.byref(operacao))
    if resultado >= 0 and operacao:
        try:
            # O processo pai limita a espera e mantém o callback vivo até a conclusão.
            ativacao.concluiu.wait()
            return ativacao.cliente
        finally:
            tabela = ct.cast(operacao, ct.POINTER(ct.POINTER(ct.c_void_p))).contents
            _Ref(tabela[2])(operacao)


def capturar_spotify(segundos, ativo):
    """Retorna PCM estéreo e instante monotônico do primeiro pacote, só em memória."""
    CoInitializeEx(COINIT_MULTITHREADED)
    try:
        pid = _pid_spotify()
        cliente = _abrir_captura(pid) if pid and ativo() else None
        if not cliente:
            return
        formato = WAVEFORMATEX(1, CANAIS_AUDIO, TAXA_AUDIO, TAXA_AUDIO * BYTES_FRAME,
                               BYTES_FRAME, 16, 0)
        cliente.Initialize(0, 0x80020000, 0, 0, ct.byref(formato), None)
        captura = cliente.GetService(ct.byref(_Captura._iid_)).QueryInterface(_Captura)
        alvo = round(segundos * TAXA_AUDIO) * BYTES_FRAME
        dados = bytearray()
        inicio = None
        cliente.Start()
        try:
            prazo = time.monotonic() + segundos + 1
            while ativo() and len(dados) < alvo and time.monotonic() < prazo:
                if not captura.GetNextPacketSize():
                    time.sleep(0.015)
                    continue
                buffer, frames, flags, _, qpc = captura.GetBuffer()
                try:
                    if dados and flags & 0x1:  # Houve lacuna no áudio durante a captura.
                        return
                    tamanho = frames * BYTES_FRAME
                    if inicio is None:
                        agora = time.monotonic()
                        instante = qpc / 10_000_000
                        inicio = instante if abs(instante - agora) < 0.5 else agora - frames / TAXA_AUDIO
                    dados.extend(bytes(tamanho) if flags & 0x2 else ct.string_at(buffer, tamanho))
                finally:
                    captura.ReleaseBuffer(frames)
            if len(dados) >= alvo and ativo():
                return bytes(dados[:alvo]), inicio
        finally:
            with suppress(OSError):
                cliente.Stop()
    finally:
        captura = cliente = None
        CoUninitialize()


if __name__ == "__main__":
    amostra = capturar_spotify(float(sys.argv[1]), lambda: True)
    if amostra:
        dados, instante = amostra
        sys.stdout.buffer.write(struct.pack("<d", instante))
        sys.stdout.buffer.write(dados)
