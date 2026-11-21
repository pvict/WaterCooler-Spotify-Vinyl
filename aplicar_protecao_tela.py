"""Aplica a proteção contra imagem estática em tela_completa.py e ao_vivo.py.

Uso (na pasta do projeto, onde estão os dois arquivos):
    python aplicar_protecao_tela.py

Cria cópias tela_completa.py.bak-protecao e ao_vivo.py.bak-protecao (ignoradas
pelo .gitignore) e só grava se TODAS as edições do arquivo casarem exatamente.
"""
import py_compile
import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
MARCADOR = "PAUSA_ESTATICA_MAX"

TELA = [
    # 1. Constantes
    ("ROTACAO_GRAUS_S = 40.0",
     "PAUSA_ESTATICA_MAX = 600.0   # s parado/sem música até o painel parado ceder ao vídeo em movimento\n"
     "TELA_APAGAR_APOS = 3600.0    # s sem música até apagar a tela de vez; 0 = nunca apaga\n"
     "ROTACAO_GRAUS_S = 40.0"),
    # 2. Estado no __init__
    ("        self._proxima_inicio = None\n        self._zerar()\n",
     "        self._proxima_inicio = None\n"
     "        # Ociosidade longa: evita imagem estática prolongada no LCD.\n"
     "        self._t_parado = None\n"
     "        self._apagada_por_inatividade = False\n"
     "        self._zerar()\n"),
    # 3. Brilho 0 enquanto apagada por inatividade
    ("\n        novo = brilho_tela_para(datetime.datetime.now())\n",
     "\n        novo = 0 if self._apagada_por_inatividade else brilho_tela_para(datetime.datetime.now())\n"),
    # 4. Cronômetro de ociosidade e apagar a tela
    ('        tocando = snap.get("musica") is not None and snap.get("tocando", False)\n'
     '        self._girar_fundo(agora, dt, tocando)\n',
     '        tocando = snap.get("musica") is not None and snap.get("tocando", False)\n'
     '        if tocando or modo == "video":\n'
     '            self._t_parado = None\n'
     '        elif self._t_parado is None:\n'
     '            self._t_parado = agora\n'
     '        parado_por = 0.0 if self._t_parado is None else agora - self._t_parado\n'
     '        ocioso_longo = parado_por > PAUSA_ESTATICA_MAX\n'
     '        if TELA_APAGAR_APOS and parado_por > TELA_APAGAR_APOS:\n'
     '            # Muito tempo sem música: apaga a tela e zera o brilho.\n'
     '            if self.letras is not None:\n'
     '                self.letras.consultar(None)\n'
     '            estado["status_letras"] = None\n'
     '            self._apagada_por_inatividade = True\n'
     '            self.estava_tocando = tocando\n'
     '            self._publicar_visual(False)\n'
     '            return self._quadro_apagado\n'
     '        self._apagada_por_inatividade = False\n'
     '        self._girar_fundo(agora, dt, tocando)\n'),
    # 5. Pausa longa libera o vídeo em movimento
    ('        mostrar = (modo in ("spotify", "clipe") or tocando\n'
     '                   or (modo != "video" and self.t_pausa is not None and agora - self.t_pausa < ATRASO_OCIOSO))\n',
     '        mostrar = (not ocioso_longo) and (\n'
     '            modo in ("spotify", "clipe") or tocando\n'
     '            or (modo != "video" and self.t_pausa is not None and agora - self.t_pausa < ATRASO_OCIOSO))\n'),
    # 6. Letras em tela cheia também saem na pausa longa
    ("        if letra is not None:\n            posicao = posicao_atual(",
     "        if letra is not None and not ocioso_longo:\n            posicao = posicao_atual("),
    # 7. Watchdog de quadros
    ("                    ultimo_brilho = brilho\n",
     "                    ultimo_brilho = brilho\n"
     "                    ultimo_q, t_q = -1, time.monotonic()\n"),
    ("                        parar_sessao.wait(0.1)\n",
     "                        if not economico:\n"
     "                            q = pipe.quadros\n"
     "                            if q != ultimo_q:\n"
     "                                ultimo_q, t_q = q, time.monotonic()\n"
     "                            elif time.monotonic() - t_q > 8.0:\n"
     '                                log("watchdog: nenhum quadro gerado em 8s; reiniciando o fluxo")\n'
     "                                parar_sessao.set()\n"
     "                                return\n"
     "                        parar_sessao.wait(0.1)\n"),
]

VIVO = [
    ('    def __init__(self, caminho, fps=30, log=None, nome="fundo"):\n        self.proc = subprocess.Popen(',
     '    def __init__(self, caminho, fps=30, log=None, nome="fundo"):\n'
     '        self._args = (caminho, fps, log, nome)\n'
     '        self._falhas = 0\n'
     '        self._fechado = False\n'
     '        self._retentar_em = 0.0\n'
     '        self.proc = subprocess.Popen('),
    ('    def proximo(self):\n'
     '        dados = self.proc.stdout.read(BYTES_QUADRO)\n'
     '        if len(dados) < BYTES_QUADRO:\n'
     '            return None\n'
     '        return Image.frombytes("RGB", (LADO, LADO), dados)\n',
     '    def proximo(self):\n'
     '        dados = self.proc.stdout.read(BYTES_QUADRO)\n'
     '        if len(dados) < BYTES_QUADRO:\n'
     '            self._falhas += 1\n'
     '            if self._falhas >= 3 and not self._fechado and time.monotonic() >= self._retentar_em:\n'
     '                self._retentar_em = time.monotonic() + 5.0   # no máximo uma tentativa a cada 5 s\n'
     '                self._reiniciar()\n'
     '            return None\n'
     '        self._falhas = 0\n'
     '        return Image.frombytes("RGB", (LADO, LADO), dados)\n'
     '\n'
     '    def _reiniciar(self):\n'
     '        """O ffmpeg do fundo encerrou: abre outro em vez de ficar num quadro sólido."""\n'
     '        caminho, fps, log, nome = self._args\n'
     '        self._falhas = 0\n'
     '        if log:\n'
     '            log(f"ffmpeg {nome}: decoder encerrou; reiniciando")\n'
     '        try:\n'
     '            self.proc.kill()\n'
     '            self.proc.wait(timeout=2)\n'
     '        except Exception:\n'
     '            pass\n'
     '        self.proc = subprocess.Popen(\n'
     '            ["ffmpeg", "-nostdin", "-loglevel", "warning", "-stream_loop", "-1", "-i", caminho,\n'
     '             "-vf", f"scale={LADO}:{LADO}", "-r", str(fps),\n'
     '             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],\n'
     '            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,\n'
     '            creationflags=SEM_JANELA,\n'
     '        )\n'
     '        _monitorar_stderr(self.proc, nome, log)\n'),
    ('    def fechar(self):\n'
     '        try:\n'
     '            self.proc.kill()\n'
     '            self.proc.wait(timeout=2)\n'
     '        except Exception:\n'
     '            pass\n',
     '    def fechar(self):\n'
     '        self._fechado = True\n'
     '        try:\n'
     '            self.proc.kill()\n'
     '            self.proc.wait(timeout=2)\n'
     '        except Exception:\n'
     '            pass\n'),
]


def aplicar(nome, edicoes, pasta):
    caminho = pasta / nome
    if not caminho.is_file():
        sys.exit(f"[ERRO] {caminho} não encontrado. Rode o script na pasta do projeto.")
    bruto = caminho.read_bytes().decode("utf-8")
    crlf = "\r\n" in bruto
    texto = bruto.replace("\r\n", "\n")
    if "tela_completa" in nome and MARCADOR in texto or ("ao_vivo" in nome and "_reiniciar" in texto):
        print(f"[ok] {nome}: já tem a proteção; nada a fazer.")
        return None
    falhas = [(i, antigo, texto.count(antigo)) for i, (antigo, _n) in enumerate(edicoes, 1)
              if texto.count(antigo) != 1]
    if falhas:
        for i, antigo, n in falhas:
            print(f"[ERRO] {nome}: edição {i} casou {n} vez(es) (esperado 1).\n--- trecho esperado ---\n{antigo}")
        sys.exit("Nada foi gravado: o arquivo difere da versão usada no patch. Envie a saída acima.")
    for antigo, novo in edicoes:
        texto = texto.replace(antigo, novo)
    return caminho, (texto.replace("\n", "\r\n") if crlf else texto)


def main():
    pasta = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else RAIZ
    pendentes = [r for r in (aplicar("tela_completa.py", TELA, pasta),
                             aplicar("ao_vivo.py", VIVO, pasta)) if r]
    for caminho, texto in pendentes:
        compile(texto, str(caminho), "exec")   # recusa gravar se a sintaxe quebrou
    for caminho, texto in pendentes:
        shutil.copy2(caminho, caminho.with_name(caminho.name + ".bak-protecao"))
        caminho.write_bytes(texto.encode("utf-8"))
        py_compile.compile(str(caminho), doraise=True)
        print(f"[ok] {caminho.name} atualizado (backup: {caminho.name}.bak-protecao)")


if __name__ == "__main__":
    main()
