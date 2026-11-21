import asyncio
import colorsys
import datetime
import hashlib
import io
import logging
import math
import os
import threading
import time
import sys
import contextlib
import shutil

# --- CORREÇÃO CRÍTICA PARA O WINRT (SPOTIFY) ---
sys.coinit_flags = 0  # 0 = COINIT_MULTITHREADED
import comtypes
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

import libusb_package
import usb.util
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat
from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MediaManager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as StatusReproducao,
)
from winrt.windows.storage.streams import Buffer, InputStreamOptions
from turingscreencli import operations
from turingscreencli.transport import (
    build_command_packet_header,
    encrypt_command_packet,
    write_to_device,
)
import ao_vivo
import animacao_capa
from spotify_playlist import SpotifyPlaylistWatcher
from controle_interface import Controles, faixa_para_letras, iniciar_ponte, reservar_execucao, liberar_execucao
from fundo_usuario import FundoOcioso
from letras_sincronizadas import FonteLetras
from letras_visual import VisualLetras
from modo_clipe import FPS_CLIPE, FonteClipe, posicao_atual
from modo_gaming import PreparadorGaming, PipelinePreparado, FPS_GAMING

os.chdir(os.path.dirname(os.path.abspath(__file__)))

logging.getLogger("turingscreencli").setLevel(logging.ERROR)
logging.getLogger("usb").setLevel(logging.CRITICAL)
logging.getLogger("pyusb").setLevel(logging.CRITICAL)

@contextlib.contextmanager
def silenciar_saida():
    with open(os.devnull, "w") as devnull:
        old_stderr = sys.stderr
        sys.stderr = devnull
        try:
            yield
        finally:
            sys.stderr = old_stderr

ARQUIVO_MUSICA = "video_tela.mp4"
ARQUIVO_BACKGROUND = "video_fundo.mp4"
FPS = 60
LARGURA = 300
TAM_CAPA = 240
ESCONDER_PAUSADO = True
FALHAS_MAX = 10
ESPERA_RECONEXAO = 5
KBPS_CLIPE = 2800
KBPS = 5000               # reduz a fila USB mantendo o alvo de 60 FPS
PAUSA_PARA_APAGAR_TELA = 120.0  # segundos sem reprodução antes de apagar o LCD
PLAYLIST_DURACAO = 5.0
PLAYLIST_ESPERA_INICIAL = 1.0
PLAYLIST_ESPERA_FINAL = 0.8
ROTULO_PLAYLIST = "Você está ouvindo"
MARQUEE_LARGURA = 325
PLAYLIST_LARGURA = 240
MARQUEE_MARGEM = 25
MARQUEE_VELOCIDADE = 35.0
LEGENDA_EXIBICAO_INICIAL = 20.0
LEGENDA_INTERVALO_REEXIBICAO = 300.0
LEGENDA_DURACAO_REEXIBICAO = 10.0
LEGENDA_FADE = 1.0
LEGENDA_INTERVALO_DESLOCAMENTO = 60.0
LEGENDA_DESLOCAMENTOS = ((0, 0), (3, 0), (3, -3), (0, -3))
TITULO_CURTO_PERIODO = 8.0
TITULO_CURTO_AMPLITUDE = 2
PROXIMA_ANTECEDENCIA = 22.0
PROXIMA_DURACAO = 7.0
PROXIMA_RETORNO_ALBUM = PROXIMA_ANTECEDENCIA - PROXIMA_DURACAO

parar = threading.Event()
controles = Controles(interface="--interface" in sys.argv)
estado = {
    "musica": None, "capa": None, "cor_capa": (30, 215, 96, 255),
    "album": None, "capa_letras": None, "status_letras": None,
    "cor_viva": (30, 215, 96, 255),
    "capa_playlist": None, "cor_playlist": None, "cor_viva_playlist": None,
    "playlist_uri": None, "playlist_nome": None, "playlist_evento": 0,
    "reproducao_id": 0, "proxima_faixa": None, "proximo_clipe": None,
    "pedido_capa_album": None, "capa_album_api": None,
    "pos": 0.0, "dur": 0.0, "t_poll": 0.0, "tocando": False,
    "spotify_inativo_desde": None,
    "midia_pronta": False,
    "volume": None, "volume_exibir_ate": 0.0,
    "modo_exibicao": controles.config["modo"], "ultima_midia": None,
    "tela_conectada": False, "erro_interface": "",
    "visual_disco": None,
    "fundo_ocioso_ativo": None, "erro_fundo": "",
    "gaming_ativo": False, "gaming_preparando": False, "erro_gaming": "",
}


def log(msg):
    linha = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    if "--interface" not in sys.argv:
        print(linha)
    try:
        with open("tela.log", "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except OSError:
        pass


BRILHO_DIA = 100


def brilho_para(agora):
    # O estado desligado vale para a tela e para os LEDs sincronizados.
    if not controles.config["tela_ligada"]:
        return 0
    if controles.config["brilho"] is not None:
        return controles.config["brilho"]
    h = agora.hour
    return 30 if 1 <= h < 7 else (60 if h >= 18 or h < 1 else BRILHO_DIA)


def spotify_em_repouso(snap, agora):
    if snap.get("tocando") and snap.get("musica"):
        return False
    # Sem uma pausa confirmada, não há imagem da faixa para manter na tela.
    desde = snap.get("spotify_inativo_desde")
    return desde is None or agora - desde >= PAUSA_PARA_APAGAR_TELA


def brilho_tela_para(agora, em_repouso=False):
    return 0 if em_repouso else brilho_para(agora)


def fonte(nome_arquivo, tamanho):
    try:
        if os.path.exists(nome_arquivo):
            return ImageFont.truetype(nome_arquivo, tamanho)
        caminho_windows = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", nome_arquivo)
        if os.path.exists(caminho_windows):
            return ImageFont.truetype(caminho_windows, tamanho)
        return ImageFont.truetype(nome_arquivo, tamanho)
    except Exception as e:
        print(f"[ERRO] Não foi possível carregar a fonte '{nome_arquivo}': {e}")
        return ImageFont.load_default()

F_TITULO = fonte("SFPRODISPLAYBOLD.otf", 32)
F_ARTISTA = fonte("SFPRODISPLAYREGULAR.otf", 20)


def cortar(d, texto, f):
    if d.textlength(texto, font=f) <= LARGURA:
        return texto
    while len(texto) > 1 and d.textlength(texto + "…", font=f) > LARGURA:
        texto = texto[:-1]
    return texto + "…"


def txt(d, x, y, texto, f, cor, ancora="mt"):
    d.text(
        (x, y), texto, font=f, fill=cor, anchor=ancora,
        stroke_width=0, stroke_fill=(0, 0, 0, 255),
    )


def cor_viva_da_capa(img, fallback):
    pequena = img.resize((64, 64), Image.BILINEAR)
    paleta = getattr(Image, "Palette", Image).ADAPTIVE
    pequena = pequena.convert("P", palette=paleta, colors=8)
    pal = pequena.getpalette()
    melhor, melhor_pontos = None, 0.0
    for contagem, idx in pequena.getcolors():
        r, g, b = pal[idx * 3: idx * 3 + 3]
        h, sat, val = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if sat < 0.25 or val < 0.25:
            continue
        pontos = contagem * sat * sat * val
        if pontos > melhor_pontos:
            melhor, melhor_pontos = (r, g, b, 255), pontos
    return melhor or fallback


def preparar_capa(dados, calcular_cores=True):
    img = Image.open(io.BytesIO(dados)).convert("RGB")

    if calcular_cores:
        cor_media = img.resize((1, 1), resample=Image.BILINEAR).getpixel((0, 0))
        cor_rgba = (cor_media[0], cor_media[1], cor_media[2], 255)
        cor_viva = cor_viva_da_capa(img, cor_rgba)
    else:
        cor_rgba = cor_viva = None

    lado = min(img.size)
    x0 = (img.width - lado) // 2
    y0 = (img.height - lado) // 2
    img = img.crop((x0, y0, x0 + lado, y0 + lado))
    img = img.resize((TAM_CAPA, TAM_CAPA), Image.LANCZOS)
    
    grande = TAM_CAPA * 4
    mascara = Image.new("L", (grande, grande), 0)
    ImageDraw.Draw(mascara).ellipse((0, 0, grande - 1, grande - 1), fill=255)
    mascara = mascara.resize((TAM_CAPA, TAM_CAPA), Image.LANCZOS)
    
    saida = img.convert("RGBA")
    saida.putalpha(mascara)
    return saida, cor_rgba, cor_viva


def atualizar_capa_playlist(uri, nome, dados):
    if not dados:
        estado.update(
            capa_playlist=None, cor_playlist=None, cor_viva_playlist=None,
            playlist_uri=None, playlist_nome=None,
        )
        return
    try:
        # Usa o mesmo tamanho e recorte circular já aplicado à capa do álbum.
        capa, cor, cor_viva = preparar_capa(dados, calcular_cores=False)
        mudou_playlist = uri != estado.get("playlist_uri")
        estado.update(
            capa_playlist=capa, cor_playlist=cor, cor_viva_playlist=cor_viva,
            playlist_uri=uri, playlist_nome=nome,
            playlist_evento=estado.get("playlist_evento", 0) + int(mudou_playlist),
        )
        log(f"Spotify playlist: capa carregada ({nome or 'sem nome'}).")
    except Exception as exc:
        log(f"Spotify playlist: erro preparando a capa: {exc}")


def atualizar_proxima_faixa(reproducao, uri, nome, dados, duracao_spotify):
    """Prepara a imagem na thread Spotify e publica tudo numa única referência."""
    if estado.get("reproducao_id") != reproducao:
        return
    try:
        capa, _, _ = preparar_capa(dados, calcular_cores=False)
        if estado.get("reproducao_id") == reproducao:
            estado["proxima_faixa"] = {
                "reproducao_id": reproducao, "uri": uri, "nome": nome, "capa": capa,
                "duracao_spotify": duracao_spotify,
            }
            log(f"Spotify a seguir: capa preparada ({nome}).")
    except Exception as exc:
        log(f"Spotify a seguir: erro preparando a capa: {exc}")


def atualizar_proximo_clipe(dados):
    if estado.get("reproducao_id") == dados["reproducao_id"]:
        estado["proximo_clipe"] = dados if dados.get("uri") else None


def atualizar_capa_album(pedido_id, musica, uri, dados):
    """Prepara na thread da API; só o monitor da mídia aplica o resultado à tela."""
    pedido = estado.get("pedido_capa_album")
    if not pedido or pedido["id"] != pedido_id or estado.get("musica") != musica:
        return False
    try:
        capa, cor, cor_viva = preparar_capa(dados)
        pedido = estado.get("pedido_capa_album")
        if not pedido or pedido["id"] != pedido_id or estado.get("musica") != musica:
            return False
        estado["capa_album_api"] = {
            "pedido_id": pedido_id, "musica": musica, "uri": uri,
            "capa": capa, "cor_capa": cor, "cor_viva": cor_viva,
        }
        return True
    except Exception as exc:
        log(f"Spotify capa: erro preparando a imagem do álbum: {exc}")
        return False


def capas_equivalentes(atual, nova):
    """Tolera diferenças de JPEG/tamanho sem trocar a imagem ou as cores já calibradas."""
    if atual is nova:
        return True
    if atual is None or nova is None:
        return False
    pequena_atual = atual.convert("RGB").resize((32, 32), Image.BILINEAR)
    pequena_nova = nova.convert("RGB").resize((32, 32), Image.BILINEAR)
    diferenca = ImageChops.difference(pequena_atual, pequena_nova)
    mascara = atual.getchannel("A").resize((32, 32), Image.BILINEAR)
    return sum(ImageStat.Stat(diferenca, mask=mascara).mean) / 3 <= 6.0


_cache_marquee_pos = 0.0
_cache_marquee_titulo = None
_cache_marquee_img = None
_cache_marquee_largura = 0
_cache_marquee_mascara = None

_cache_halo_cor = None
_cache_halo_img = None
_cache_halo_fade = (None, -1, None)
_cache_capa_escala = (None, 0, None)

_cache_hud_glow_masks = {}

_transicao_musica_atual = None
_capa_anterior = None
_transicao_progresso = 1.0  # 1.0 = transição concluída


def _atualizar_volume(nivel):
    """Só publica o valor; o aviso de áudio não desenha nem escreve logs."""
    vol = min(100, max(0, int(round(nivel * 100))))
    anterior = estado.get("volume")
    if anterior is None:
        estado["volume"] = vol
    elif vol != anterior:
        estado.update(volume=vol, volume_exibir_ate=time.monotonic() + 3.0)


def _laco_volume():
    """Recebe avisos do Windows; usa a leitura anterior como alternativa."""
    # Os callbacks chegam em threads do Windows, sem precisar de um loop de UI.
    comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    enumerador = monitor_saida = None
    trocar_saida = threading.Event()
    ultimo_erro = None
    try:
        try:
            from pycaw.callbacks import AudioEndpointVolumeCallback, MMNotificationClient
        except ImportError:
            AudioEndpointVolumeCallback = MMNotificationClient = None

        if AudioEndpointVolumeCallback is not None:
            class AvisoVolume(AudioEndpointVolumeCallback):
                ativo = True

                def on_notify(self, new_volume, new_mute, event_context,
                              channels, channel_volumes):
                    if self.ativo and not parar.is_set():
                        _atualizar_volume(new_volume)

            class AvisoSaida(MMNotificationClient):
                def on_default_device_changed(self, flow, flow_id, role,
                                             role_id, default_device_id):
                    # GetSpeakers usa a saída multimídia (render=0, role=1).
                    if flow_id == 0 and role_id == 1:
                        trocar_saida.set()

            try:
                enumerador = AudioUtilities.GetDeviceEnumerator()
                monitor_saida = AvisoSaida()
                enumerador.RegisterEndpointNotificationCallback(monitor_saida)
            except Exception as exc:
                monitor_saida = None
                log(f"volume: aviso de troca de saída indisponível: {exc}")

        while not parar.is_set():
            callback = volume_interface = None
            trocar_saida.clear()
            try:
                dispositivo = AudioUtilities.GetSpeakers()
                if dispositivo is None:
                    raise RuntimeError("nenhuma saída de áudio disponível")
                try:
                    volume_interface = dispositivo.EndpointVolume.QueryInterface(IAudioEndpointVolume)
                except AttributeError:
                    from ctypes import cast, POINTER
                    interface = dispositivo.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
                    volume_interface = cast(interface, POINTER(IAudioEndpointVolume))

                _atualizar_volume(volume_interface.GetMasterVolumeLevelScalar())
                if AudioEndpointVolumeCallback is not None:
                    candidato = AvisoVolume()
                    try:
                        volume_interface.RegisterControlChangeNotify(candidato)
                    except Exception as exc:
                        candidato.ativo = False
                        log(f"volume: usando leitura direta; aviso indisponível: {exc}")
                    else:
                        callback = candidato
                        # Releitura única cobre uma mudança entre a leitura inicial
                        # e o registro do aviso, inclusive ao trocar a saída.
                        _atualizar_volume(volume_interface.GetMasterVolumeLevelScalar())
                        log("volume: avisos do Windows ativos")

                ultimo_erro = None
                if callback is not None:
                    # A thread dorme; os avisos atualizam o estado imediatamente.
                    while not parar.is_set() and not trocar_saida.wait(0.5):
                        pass
                else:
                    while not parar.is_set() and not trocar_saida.is_set():
                        if controles.gaming_ativo:
                            parar.wait(0.5)
                            continue
                        _atualizar_volume(volume_interface.GetMasterVolumeLevelScalar())
                        parar.wait(0.01)
            except Exception as exc:
                if str(exc) != ultimo_erro:
                    ultimo_erro = str(exc)
                    log(f"erro lendo volume: {exc}")
                parar.wait(1.0)
            finally:
                if callback is not None:
                    callback.ativo = False
                    with contextlib.suppress(Exception):
                        volume_interface.UnregisterControlChangeNotify(callback)
    finally:
        if monitor_saida is not None:
            with contextlib.suppress(Exception):
                enumerador.UnregisterEndpointNotificationCallback(monitor_saida)
        comtypes.CoUninitialize()


def percurso_playlist(largura):
    return max(0.0, largura - (PLAYLIST_LARGURA - 2 * MARQUEE_MARGEM))


def deslocamento_playlist(largura, tempo_visivel):
    percorrido = max(0.0, tempo_visivel - PLAYLIST_ESPERA_INICIAL) * MARQUEE_VELOCIDADE
    return round(min(percorrido, percurso_playlist(largura))) - MARQUEE_MARGEM


def opacidade_legenda(posicao):
    if posicao < LEGENDA_INTERVALO_REEXIBICAO:
        return 1.0 - _suave((posicao - LEGENDA_EXIBICAO_INICIAL) / LEGENDA_FADE)
    fase = posicao % LEGENDA_INTERVALO_REEXIBICAO
    tempo_da_borda = min(fase, LEGENDA_DURACAO_REEXIBICAO - fase)
    return _suave(tempo_da_borda / LEGENDA_FADE)


def renderizar(snap):
    global _cache_marquee_pos
    global _cache_marquee_titulo, _cache_marquee_img, _cache_marquee_largura
    global _cache_halo_cor, _cache_halo_img
    global _cache_halo_fade
    global _cache_capa_escala

    img = Image.new("RGBA", (480, 480), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    cx, cy = 240, 240          
    raio_anel = (TAM_CAPA // 2) + 18   

    musica = snap.get("musica")
    cor_destaque = snap.get("cor_viva") or snap.get("cor_capa", (30, 215, 96, 255))
    
    # --- HALO DE LUZ DINÂMICO ---
    if musica and cor_destaque:
        if _cache_halo_cor != cor_destaque:
            _cache_halo_cor = cor_destaque
            
            camada_halo = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
            d_halo = ImageDraw.Draw(camada_halo)
            
            cx_p, cy_p = 60, 60
            raio_halo_p = int((raio_anel + 25) / 4)
            cor_halo = (cor_destaque[0], cor_destaque[1], cor_destaque[2], 160)
            
            d_halo.ellipse(
                (cx_p - raio_halo_p, cy_p - raio_halo_p, cx_p + raio_halo_p, cy_p + raio_halo_p),
                fill=cor_halo
            )
            
            from PIL import ImageFilter
            camada_halo = camada_halo.filter(ImageFilter.GaussianBlur(12))
            _cache_halo_img = camada_halo.resize((480, 480), Image.BILINEAR)

        if _cache_halo_img is not None:
            nivel_glow = round(255 * snap.get("glow_alpha", 1.0))
            if nivel_glow > 0:
                halo_img = _cache_halo_img
                if nivel_glow < 255:
                    origem, nivel, atenuado = _cache_halo_fade
                    if origem is not halo_img or nivel != nivel_glow:
                        atenuado = halo_img.copy()
                        atenuado.putalpha(halo_img.getchannel("A").point(lambda a: a * nivel_glow // 255))
                        _cache_halo_fade = (halo_img, nivel_glow, atenuado)
                    halo_img = atenuado
                img = Image.alpha_composite(img, halo_img)
                d = ImageDraw.Draw(img)
    else:
        _cache_halo_cor = None
        _cache_halo_img = None

    # 1. Trilha de fundo do anel
    d.arc(
        (cx - raio_anel, cy - raio_anel, cx + raio_anel, cy + raio_anel),
        0, 360, fill=(255, 255, 255, 35), width=4
    )

    if musica and snap.get("dur", 0) > 0:
        duracao = snap["dur"]
        pos_alvo = snap["pos"]
        
        if snap.get("tocando"):
            pos_alvo += time.monotonic() - snap["t_poll"]
            
        # Inicializa a variável de posição suavizada no cache global se não existir
        global _prog_suave
        if "_prog_suave" not in globals() or _prog_suave is None or abs(_prog_suave - pos_alvo) > 3.0:
            _prog_suave = pos_alvo
            
        # Interpolação linear (LERP): suaviza a transição aproximando o valor atual do valor alvo gradualmente
        _prog_suave += (pos_alvo - _prog_suave) * 0.25
        
        frac = min(max(_prog_suave / duracao, 0), 1)
        frac *= snap.get("progresso_preenchimento", 1.0)
        ang_fim = -90 + 360 * frac

        # Na pausa, só o preenchimento colorido se dissolve na trilha neutra.
        alfa_arco = snap.get("progresso_alpha", 1.0)
        if alfa_arco > 0.001 and frac > 0.00001:
            cor_arco = tuple(round(255 + (c - 255) * alfa_arco) for c in cor_destaque[:3])
            cor_arco += (round(35 + (255 - 35) * alfa_arco),)
            d.arc(
                (cx - raio_anel, cy - raio_anel, cx + raio_anel, cy + raio_anel),
                -90, ang_fim, fill=cor_arco, width=4
            )

    # 3. Capa do Álbum (Perfeitamente redonda, instantânea e leve)
    capa = snap.get("capa")
    if capa is not None and musica:
        escala = snap.get("escala_capa", 1.0)
        if escala < 0.999:
            lado = max(1, round(capa.width * escala))
            anterior, tamanho, reduzida = _cache_capa_escala
            if anterior is not capa or tamanho != lado:
                reduzida = capa.resize((lado, lado), Image.Resampling.BICUBIC)
                _cache_capa_escala = (capa, lado, reduzida)
            capa = reduzida
        x_capa = cx - (capa.width // 2)
        y_capa = cy - (capa.height // 2)
        
        # Desenha diretamente a capa já tratada e circular, sem sobrecarregar o CPU com filtros por frame
        img.alpha_composite(capa, (x_capa, y_capa))

    # 4. Textos e Marquee (Com fade suave nas pontas)
    texto_alpha = snap.get("texto_alpha", 1.0)
    dy_texto = int(round(snap.get("texto_dy", 0.0)))
    modo_playlist_texto = snap.get("modo_playlist_texto", False)
    posicao_legenda = None if modo_playlist_texto else snap.get("posicao_legenda")
    if posicao_legenda is not None:
        texto_alpha *= opacidade_legenda(posicao_legenda)
    _img_base = None
    if musica and texto_alpha > 0.0:
        tempo_visual = snap.get("tempo_visual")
        if tempo_visual is None:
            tempo_visual = time.monotonic()
        centro_texto = cx
        if posicao_legenda is not None:
            etapa = int(tempo_visual // LEGENDA_INTERVALO_DESLOCAMENTO) % len(LEGENDA_DESLOCAMENTOS)
            dx, dy = LEGENDA_DESLOCAMENTOS[etapa]
            centro_texto += dx
            dy_texto += dy

        if texto_alpha < 0.999:
            # o texto é desenhado numa camada própria para poder aplicar o fade
            _img_base = img
            img = Image.new("RGBA", (480, 480), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
        titulo, artista = musica
        y_texto = cy + raio_anel + 20 + dy_texto
        y_titulo = y_texto
        if modo_playlist_texto:
            txt(d, centro_texto, y_texto, snap.get("rotulo_aviso") or "Você está ouvindo",
                F_ARTISTA, (200, 200, 200, 255))
            y_titulo += 25
        
        aviso_playlist = snap.get("rotulo_aviso") == ROTULO_PLAYLIST
        largura_janela = PLAYLIST_LARGURA if aviso_playlist else MARQUEE_LARGURA
        titulo_str = titulo or ""
        largura_titulo = d.textlength(titulo_str, font=F_TITULO)
        
        cabe_uma_vez = (percurso_playlist(largura_titulo) == 0 if aviso_playlist
                       else largura_titulo <= largura_janela)
        if cabe_uma_vez:
            centro_titulo = centro_texto
            if posicao_legenda is not None:
                fase = math.tau * (posicao_legenda % TITULO_CURTO_PERIODO) / TITULO_CURTO_PERIODO
                centro_titulo += round(TITULO_CURTO_AMPLITUDE * math.sin(fase))
            txt(d, centro_titulo, y_titulo, titulo_str, F_TITULO, (255, 255, 255, 255))
        else:
            if _cache_marquee_titulo != titulo_str:
                _cache_marquee_titulo = titulo_str
                texto_duplicado = titulo_str + "    •    " + titulo_str + "    •    "
                largura_total_img = int(d.textlength(texto_duplicado, font=F_TITULO)) + 100
                
                _cache_marquee_img = Image.new("RGBA", (largura_total_img, 60), (0, 0, 0, 0))
                d_cache = ImageDraw.Draw(_cache_marquee_img)
                d_cache.text(
                    (0, 0), texto_duplicado, font=F_TITULO, fill=(255, 255, 255, 255),
                    stroke_width=0, stroke_fill=(0, 0, 0, 255)
                )
                _cache_marquee_largura = int(d.textlength(titulo_str + "    •    ", font=F_TITULO))

            if aviso_playlist:
                inicio_leitura = snap.get("inicio_leitura_texto")
                tempo_visivel = tempo_visual - inicio_leitura if inicio_leitura is not None else 0.0
                deslocamento_int = deslocamento_playlist(largura_titulo, tempo_visivel)
            else:
                inicio_mensagem = snap.get("mensagem_playlist_inicio") if modo_playlist_texto else None
                tempo_marquee = (max(0.0, tempo_visual - inicio_mensagem) if inicio_mensagem is not None
                                 else 0.0 if modo_playlist_texto else tempo_visual)
                deslocamento_int = int((tempo_marquee * MARQUEE_VELOCIDADE) % _cache_marquee_largura)
            
            janela = _cache_marquee_img.crop((deslocamento_int, 0, deslocamento_int + largura_janela, 60))
            
            # --- MÁSCARA DE FADE NAS EXTREMIDADES ---
            global _cache_marquee_mascara
            if _cache_marquee_mascara is None or _cache_marquee_mascara.width != largura_janela:
                mascara = Image.new("L", (largura_janela, 60), 255)
                d_mask = ImageDraw.Draw(mascara)
                fade_w = MARQUEE_MARGEM
                for x in range(fade_w):
                    alpha = int(255 * (x / fade_w))
                    d_mask.line([(x, 0), (x, 60)], fill=alpha)
                    d_mask.line([(largura_janela - 1 - x, 0), (largura_janela - 1 - x, 60)], fill=alpha)
                _cache_marquee_mascara = mascara

            # Aplica o gradiente alfa de forma otimizada usando ImageChops
            from PIL import ImageChops
            r, g, b, a = janela.split()
            a_fade = ImageChops.multiply(a, _cache_marquee_mascara)
            janela = Image.merge("RGBA", (r, g, b, a_fade))
            
            x_pos = centro_texto - (largura_janela // 2)
            img.alpha_composite(janela, (x_pos, int(y_titulo)))
            
        if artista and not modo_playlist_texto:
            txt(d, centro_texto, y_texto + 35, cortar(d, artista, F_ARTISTA),
                F_ARTISTA, (200, 200, 200, 255))

    if _img_base is not None:
        a_txt = img.getchannel("A").point(lambda v: int(v * texto_alpha))
        img.putalpha(a_txt)
        _img_base.alpha_composite(img)
        img = _img_base
        d = ImageDraw.Draw(img)

    raio_tela = 239
    # Se houver música usa a cor viva da capa com transparência; se não, fica apagado/neutro
    if musica and cor_destaque:
        cor_aro_externo = (cor_destaque[0], cor_destaque[1], cor_destaque[2], 40)
    else:
        cor_aro_externo = (255, 255, 255, 10)
        
    d.arc(
        (cx - raio_tela, cy - raio_tela, cx + raio_tela, cy + raio_tela),
        0, 360, fill=cor_aro_externo, width=5
    )

# 5. Overlay de Volume
    vol_real = snap.get("volume")
    if vol_real is not None:
        vol_visual = snap.get("volume_visual", vol_real)
        hud_a = snap.get("hud_alpha")
        if hud_a is None:
            hud_a = 1.0 if time.monotonic() < snap.get("volume_exibir_ate", 0.0) else 0.0
        if hud_a > 0.01:
            from PIL import ImageFilter
            opac = int(255 * hud_a)
            layer_hud = Image.new("RGBA", (480, 480), (0, 0, 0, 0))
            d_hud = ImageDraw.Draw(layer_hud)
            vol_percentual = min(1.0, max(0.0, vol_visual / 100.0))
            marcas_preenchidas = vol_percentual * HUD_NUM_MARCAS
            cor_ativa = (255, 255, 255, opac)
            if cor_destaque:
                cor_ativa = (cor_destaque[0], cor_destaque[1], cor_destaque[2], opac)
            cor_inativa = (80, 80, 80, int(100 * hud_a))

            # O arco fino e a ponta se movem continuamente entre as marcas.
            # São formas simples, sem desfoque adicional por quadro.
            if vol_percentual > 0.0:
                angulo_ponta = HUD_ANGULO_INICIO + HUD_ARCO * vol_percentual
                d_hud.arc((cx - HUD_RAIO, cy - HUD_RAIO, cx + HUD_RAIO, cy + HUD_RAIO),
                          HUD_ANGULO_INICIO, angulo_ponta,
                          fill=(*cor_ativa[:3], int(opac * 0.45)), width=1)
                rad_ponta = math.radians(angulo_ponta)
                x_ponta = cx + HUD_RAIO * math.cos(rad_ponta)
                y_ponta = cy + HUD_RAIO * math.sin(rad_ponta)
                d_hud.ellipse((x_ponta - 2, y_ponta - 2, x_ponta + 2, y_ponta + 2),
                              fill=cor_ativa)

            # Só a marca parcialmente preenchida precisa de mistura de cor.
            for i, segmento in enumerate(HUD_MARCAS):
                preenchimento = marcas_preenchidas - i
                if preenchimento >= 1.0:
                    cor_tick = cor_ativa
                elif preenchimento <= 0.0:
                    cor_tick = cor_inativa
                else:
                    cor_tick = tuple(round(a + (b - a) * preenchimento)
                                     for a, b in zip(cor_inativa, cor_ativa))
                d_hud.line(segmento, fill=cor_tick, width=HUD_ESPESSURA)

            # Mantém o cache de no máximo 47 máscaras do glow original.
            ticks_ativos = min(HUD_NUM_MARCAS, math.ceil(marcas_preenchidas))
            mascara_glow = _cache_hud_glow_masks.get(ticks_ativos)
            if mascara_glow is None:
                layer_glow_mini = Image.new("L", (120, 120), 0)
                d_glow_mini = ImageDraw.Draw(layer_glow_mini)
                for segmento in HUD_MARCAS_MINI[:ticks_ativos]:
                    d_glow_mini.line(segmento, fill=255, width=3)
                mascara_glow = layer_glow_mini.filter(ImageFilter.GaussianBlur(4)).resize(
                    (480, 480), Image.BILINEAR
                )
                _cache_hud_glow_masks[ticks_ativos] = mascara_glow

            cor_glow = (cor_ativa[0], cor_ativa[1], cor_ativa[2], 255)
            layer_glow = Image.new("RGBA", (480, 480), cor_glow)
            if opac < 255:
                mascara_frame = mascara_glow.point(lambda v: v * opac // 255)
            else:
                mascara_frame = mascara_glow
            layer_glow.putalpha(mascara_frame)
            # Compõe o glow expansivo e intenso por trás dos ticks nítidos
            img = Image.alpha_composite(img, layer_glow)
            img = Image.alpha_composite(img, layer_hud)
            
    return img


COR_PADRAO = (30, 215, 96, 255)


def _cor_rgba(cor):
    c = tuple(int(round(x)) for x in (cor or COR_PADRAO))
    return c if len(c) == 4 else (c[0], c[1], c[2], 255)


# --- animações (ajuste à vontade) ---
PAUSA_ESTATICA_MAX = 20   # s parado/sem música até o painel parado ceder ao vídeo em movimento
TELA_APAGAR_APOS = 3600.0    # s sem música até apagar a tela de vez; 0 = nunca apaga
ROTACAO_GRAUS_S = 40.0   # giro do disco enquanto toca (graus por segundo); 0 = capa parada
TEMPO_ACELERAR = 1.0     # s para o disco chegar à velocidade ao tocar/retomar
TEMPO_FREAR = 2.0        # s para o disco parar ao pausar (desacelera até zero)
ATRASO_OCIOSO = 3.0      # s entre pausar e começar a voltar ao vídeo ocioso
FADE_ENTRA = 0.7         # s do crossfade ocioso -> música
FADE_SAI = 1.0           # s do crossfade música -> ocioso
TEXTO_SAI = 0.30         # s para o texto antigo sumir ao trocar de faixa
TEXTO_ENTRA = 0.40       # s para o texto novo aparecer
TEXTO_DESLOC = 12        # px que o texto desliza ao sumir/aparecer
HUD_ENTRA = 0.10         # s de fade do HUD de volume ao aparecer
HUD_SAI = 0.40           # s de fade do HUD de volume ao sumir
HUD_VOLUME_TAU = 0.06    # s: alcança ~90% de uma mudança em 0,14 s, sem saltos
HUD_NUM_MARCAS = 46
HUD_RAIO = 175
HUD_TAMANHO_MARCA = 12
HUD_ESPESSURA = 3
HUD_ANGULO_INICIO = 140
HUD_ARCO = 260


def _geometria_hud():
    """Calcula as posições uma vez, fora do caminho de renderização."""
    segmentos = []
    for i in range(HUD_NUM_MARCAS):
        angulo = math.radians(HUD_ANGULO_INICIO + HUD_ARCO * i / (HUD_NUM_MARCAS - 1))
        coseno, seno = math.cos(angulo), math.sin(angulo)
        dentro = HUD_RAIO - HUD_TAMANHO_MARCA / 2
        fora = HUD_RAIO + HUD_TAMANHO_MARCA / 2
        segmentos.append(((240 + dentro * coseno, 240 + dentro * seno),
                          (240 + fora * coseno, 240 + fora * seno)))
    return tuple(segmentos)


HUD_MARCAS = _geometria_hud()
HUD_MARCAS_MINI = tuple(tuple((x / 4, y / 4) for x, y in segmento)
                       for segmento in HUD_MARCAS)


def _suave(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


class Painel:
    """Monta cada quadro da transmissão ao vivo: vídeo de fundo + painel + animações.

    Tudo vira vídeo H.264 (ao_vivo.py), então as animações não dependem do tempo que a tela
    leva para receber cada imagem. Roda na thread do pipeline.

    Presença do painel (self.p, de 0 a 1): sobe quando toca e desce só ATRASO_OCIOSO segundos
    depois de pausar; o mesmo valor controla o crossfade dos vídeos e o fade do painel.
    Enquanto o painel segue na tela depois da pausa, ele mostra a última faixa, com o disco
    desacelerando até parar."""

    def __init__(self, fundo_musica, fundo_ocioso, brilho_inicial, clipe=None, fps=FPS, letras=None):
        self.clipe = clipe
        self.letras = letras
        self.visual_letras = VisualLetras()
        self.fps = fps
        self.fundo_musica = fundo_musica
        self.fundo_ocioso = fundo_ocioso
        self.pipe = None
        self.brilho = brilho_inicial
        self.t_brilho = 0.0
        self.erro_log = ""
        self.t_ultimo = None
        self.estava_tocando = False
        self.t_pausa = None
        self.p = 0.0
        self.hud = 0.0
        self.volume_anim = None
        self._ultimo_log_hud = 0.0
        self._hud_render_max_ms = 0.0
        self._diag_tempos = {}
        self._diag_playlist_ativa = False
        self._diag_playlist_uri = None
        self._revisao_brilho = controles.revisao
        self._em_repouso = False
        self._quadro_apagado = Image.new("RGB", (480, 480), (0, 0, 0))
        self._ultimo_fundo_vinil = None
        self._seguinte_fundo_vinil = None
        self._fase_fundo_vinil = 0.0
        self._cache_fundo_vinil = (None, None, None, None)
        self._dt_fundo = 0.0
        self._pausa = animacao_capa.PausaSuave()
        self._escala_capa, self._alfa_progresso = 1.0, 1.0
        self._cache_giro = (None, None, None)
        self._angulo_fundo, self._vel_fundo = 0.0, 0.0
        self._alvo_fundo, self._t_fundo, self._v0_fundo = None, 0.0, 0.0
        # A retomada sobrevive ao reset do painel quando só o fundo está visível.
        self._retomar_giro_dinamico = False
        self._capa_retomada_dinamica = None
        self._cor_retomada_dinamica = None
        # O mesmo contexto não é anunciado novamente ao voltar do vídeo ocioso.
        self._playlist_evento_visto = 0
        # Sobrevivem à pausa: o mesmo aviso não entra duas vezes na mesma faixa.
        self._proxima_reproducao = None
        self._proxima_ja_mostrada = False
        self._proxima_ate = 0.0
        self._proxima_inicio = None
        # Ociosidade longa: evita imagem estática prolongada no LCD.
        self._t_parado = None
        self._apagada_por_inatividade = False
        self._zerar()

    def _zerar(self):
        self.capa = None        # capa exibida (quando não há transição)
        self.cor = None
        self.destino = None     # (capa, cor) para onde a transição em curso vai
        self.alvo = None        # última capa do estado que já provocou uma transição
        self.trans = None
        self.titulo = None      # (título, artista) exibido
        self.titulo_alvo = None
        self.modo_playlist_texto = False
        self.modo_playlist_texto_alvo = False
        self.rotulo_aviso = None
        self.rotulo_aviso_alvo = None
        self.t_texto = None     # início da animação de troca de texto
        self._inicio_leitura_texto = None
        self._posicao_legenda = None
        self.angulo = 0.0
        self.vel = 0.0
        self._v_alvo = None
        self._r_t0 = 0.0
        self._r_v0 = 0.0
        self.exibindo_playlist = False
        self.exibindo_proxima = False
        self._album_atual = None
        self._musica_atual = None
        self._cor_album_atual = None
        self._playlist_uri_timer = None
        self._proxima_mensagem_playlist = None
        self._mensagem_playlist_ate = 0.0
        self._inicio_mensagem_playlist = None

    def _checar_brilho(self, agora, em_repouso):
        if self.pipe is None:
            return
        if (em_repouso == self._em_repouso and controles.revisao == self._revisao_brilho
                and agora - self.t_brilho < 2.0):
            return
        self._em_repouso = em_repouso
        self._revisao_brilho = controles.revisao
        self.t_brilho = agora
        novo = brilho_tela_para(datetime.datetime.now(), em_repouso)
        if novo != self.brilho:
            self.brilho = novo
            # só a thread do USB fala com a tela
            self.pipe.env.executar(lambda dev, b=novo: operations.send_brightness_command(dev, b))
            log(f"Brilho alterado para: {novo}%")

    def _fundo(self, ps):
        inicio_fundo = time.perf_counter()
        def ler(dec):
            if dec is self.fundo_musica and controles.config["modo"] in ("spotify", "dinamico", "clipe"):
                # Dois quadros vizinhos: interpola a fração durante a frenagem,
                # em vez de alternar entre congelar e saltar um quadro inteiro.
                # No modo dinâmico, mantém a mesma parada suave enquanto o
                # painel aguarda e faz a transição para o vídeo ocioso.
                if self._ultimo_fundo_vinil is None:
                    self._ultimo_fundo_vinil = dec.proximo()
                    self._seguinte_fundo_vinil = dec.proximo()
                velocidade_relativa = self._vel_fundo / max(ROTACAO_GRAUS_S, 1)
                # O pipeline chama uma vez por quadro codificado. Em velocidade
                # plena avança exatamente um quadro, sem erro acumulado do float.
                avanco = (1.0 if self._dt_fundo > 0 else 0.0) if velocidade_relativa >= 0.999 else (
                    velocidade_relativa * self.fps * self._dt_fundo)
                self._fase_fundo_vinil += avanco
                while self._fase_fundo_vinil >= 1.0:
                    self._fase_fundo_vinil -= 1.0
                    self._ultimo_fundo_vinil = self._seguinte_fundo_vinil
                    self._seguinte_fundo_vinil = dec.proximo()
                atual, seguinte = self._ultimo_fundo_vinil, self._seguinte_fundo_vinil
                if atual is None:
                    return Image.new("RGB", (480, 480), (10, 10, 20))
                if velocidade_relativa >= 0.999:
                    # Em velocidade normal, segue um quadro por vez sem mistura.
                    return atual
                # A interpolação entra/sai aos poucos perto da velocidade plena.
                mistura = self._fase_fundo_vinil * _suave(min(1.0, (1 - velocidade_relativa) / 0.12))
                if seguinte is None or mistura < 0.001:
                    return atual
                if mistura > 0.999:
                    return seguinte
                origem, destino, fase, quadro = self._cache_fundo_vinil
                if origem is atual and destino is seguinte and fase == mistura:
                    return quadro
                quadro = Image.blend(atual, seguinte, mistura)
                self._cache_fundo_vinil = (atual, seguinte, mistura, quadro)
                return quadro
            if dec is self.fundo_musica:
                # No modo Só vídeo, uma eventual saída do vinil usa leitura normal.
                self._ultimo_fundo_vinil = self._seguinte_fundo_vinil = None
                self._fase_fundo_vinil = 0.0
            return dec.proximo() or Image.new("RGB", (480, 480), (10, 10, 20))
        if ps <= 0.0:
            quadro = ler(self.fundo_ocioso)
        elif ps >= 1.0:
            quadro = ler(self.fundo_musica)
        else:
            quadro = Image.blend(ler(self.fundo_ocioso), ler(self.fundo_musica), ps)
        self._diag_tempos["fundo_ms"] = (time.perf_counter() - inicio_fundo) * 1000
        return quadro

    def _girar(self, agora, dt, tocando):
        """Velocidade do disco: acelera suave ao tocar e desacelera até zero ao pausar."""
        alvo = ROTACAO_GRAUS_S if tocando else 0.0
        if alvo != self._v_alvo:
            self._v_alvo, self._r_t0, self._r_v0 = alvo, agora, self.vel
        if controles.config["modo"] == "spotify" or alvo == 0.0:
            duracao = TEMPO_FREAR if alvo == 0.0 else TEMPO_ACELERAR
            u = min(max((agora - self._r_t0) / duracao, 0.0), 1.0)
            # Velocidade e aceleração chegam suavemente aos dois extremos.
            suave = u ** 3 * (u * (6 * u - 15) + 10)
            self.vel = self._r_v0 + (alvo - self._r_v0) * suave
        else:
            u = min((agora - self._r_t0) / TEMPO_ACELERAR, 1.0)
            self.vel = self._r_v0 + (alvo - self._r_v0) * _suave(u)
        if self.trans is None:      # durante o giro de troca de capa o disco fica na posição 0
            self.angulo = (self.angulo + self.vel * dt) % 360

    def _girar_fundo(self, agora, dt, tocando):
        # O vinil segue a reprodução mesmo quando a capa da playlist fica parada.
        alvo = ROTACAO_GRAUS_S if tocando else 0.0
        if alvo != self._alvo_fundo:
            self._alvo_fundo, self._t_fundo, self._v0_fundo = alvo, agora, self._vel_fundo
        duracao = TEMPO_ACELERAR if tocando else TEMPO_FREAR
        u = min(1.0, max(0.0, (agora - self._t_fundo) / duracao))
        suave = u ** 3 * (u * (6 * u - 15) + 10)
        self._vel_fundo = self._v0_fundo + (alvo - self._v0_fundo) * suave
        self._angulo_fundo = (self._angulo_fundo + self._vel_fundo * dt) % 360

    def _capa_girada(self):
        if self.capa is None:
            return None
        a = self.angulo % 360
        origem, angulo, girada = self._cache_giro
        if origem is self.capa and angulo == a:
            return girada
        if a < 0.05 or a > 359.95:
            return self.capa
        girada = self.capa.rotate(-a, resample=Image.BICUBIC)   # sentido horário
        self._cache_giro = (self.capa, a, girada)
        return girada

    def _painel(self, snap, agora, dt, tocando):
        inicio_animacao = time.perf_counter()
        musica = snap.get("musica")
        cap = snap.get("capa")
        cap_playlist = snap.get("capa_playlist") if snap.get("mostrar_capa_playlist") else None
        proxima = snap.get("proxima_faixa") if snap.get("mostrar_proxima") else None
        cap_aviso = proxima["capa"] if proxima else cap_playlist
        # A paleta da tela e dos LEDs continua vindo da capa do álbum.
        cor_base = snap.get("cor_viva") or snap.get("cor_capa")
        cor_nova = _cor_rgba(cor_base)
        giro_retomada = (controles.config["modo"] == "dinamico" and tocando
                         and self._retomar_giro_dinamico)

        if musica is not None:
            self._album_atual, self._musica_atual = cap, musica
            self._cor_album_atual = cor_nova
        restaurar_pausado = not tocando and self.exibindo_proxima
        if restaurar_pausado:
            # Ao pausar o aviso, volta ao álbum enquanto o disco desacelera.
            cap, musica, cor_nova = self._album_atual, self._musica_atual, self._cor_album_atual

        if musica is not None or restaurar_pausado:
            # Texto da faixa e aviso da playlist compartilham a mesma animação.
            rotulo_alvo = "A seguir" if proxima else (
                ROTULO_PLAYLIST if snap.get("mensagem_playlist") else None)
            modo_playlist_alvo = rotulo_alvo is not None
            texto_alvo = (
                (proxima["nome"], "") if proxima else
                ((snap.get("playlist_nome") or "", "") if modo_playlist_alvo else musica)
            )
            if self.titulo is None:
                self.titulo = self.titulo_alvo = texto_alvo
                self.modo_playlist_texto = self.modo_playlist_texto_alvo = modo_playlist_alvo
                self.rotulo_aviso = self.rotulo_aviso_alvo = rotulo_alvo
                self._inicio_leitura_texto = agora
            elif (texto_alvo != self.titulo_alvo
                  or rotulo_alvo != self.rotulo_aviso_alvo):
                self.titulo_alvo = texto_alvo
                self.modo_playlist_texto_alvo = modo_playlist_alvo
                self.rotulo_aviso_alvo = rotulo_alvo
                self.t_texto = agora
            # Playlist e álbum usam a mesma transição circular de troca de capa.
            capa_alvo = cap_aviso if cap_aviso is not None else cap
            self.exibindo_playlist = cap_aviso is not None
            self.exibindo_proxima = proxima is not None
            if capa_alvo is not None and (capa_alvo is not self.alvo or giro_retomada):
                retomou_mesma = (
                    cap_aviso is None and not self.estava_tocando
                    and self.capa is not None and musica == self.titulo
                )
                if retomou_mesma and not giro_retomada:
                    self.alvo, self.capa, self.cor = capa_alvo, capa_alvo, cor_nova
                else:
                    if self.trans is not None and self.destino is not None:
                        self.capa, self.cor = self.destino
                    ant = self._capa_girada()
                    cor_ant = self.cor
                    if giro_retomada:
                        # Mesmo álbum: vira a capa novamente, como numa troca.
                        # Após o vídeo ocioso, usa a imagem preservada da pausa.
                        if ant is None:
                            ant = self._capa_retomada_dinamica
                            cor_ant = self._cor_retomada_dinamica
                        if ant is None:
                            ant = capa_alvo
                    self.alvo = capa_alvo
                    self.destino = (capa_alvo, cor_nova)
                    self.angulo = 0.0
                    self.trans = animacao_capa.TransicaoCapa(
                        ant, cor_ant, capa_alvo, cor_nova, inicio=agora,
                        estilo="giro" if giro_retomada else animacao_capa.ESTILO)
                if giro_retomada:
                    self._retomar_giro_dinamico = False
                    self._capa_retomada_dinamica = None
                    self._cor_retomada_dinamica = None

        self._girar(agora, dt, tocando and not self.exibindo_playlist)
        if controles.config["modo"] == "spotify":
            self._escala_capa, self._alfa_progresso = self._pausa.quadro(not tocando, dt)
        else:
            if (self._pausa.tempo or self._pausa._pausado
                    or self._pausa.glow != 1.0 or self._pausa.preenchimento != 1.0
                    or self._pausa._retorno_t is not None):
                self._pausa.resetar()
            self._escala_capa, self._alfa_progresso = 1.0, 1.0

        capa_img, cor = None, None
        self._diag_tempos["transicao_capa"] = self.trans is not None
        if self.trans is not None:
            capa_img, cor, _metade, fim = self.trans.quadro(agora)
            if fim:
                self.trans = None
                self.capa, self.cor = self.destino
        else:
            if self.capa is None and tocando:
                self.cor = cor_nova                         # sem capa: usa a cor do estado
            capa_img, cor = self._capa_girada(), self.cor or cor_nova

        # texto
        ta, dy = 1.0, 0.0
        if self.t_texto is not None:
            t = agora - self.t_texto
            if t < TEXTO_SAI:
                u = _suave(t / TEXTO_SAI)
                ta, dy = 1 - u, -TEXTO_DESLOC * u
            else:
                self.titulo = self.titulo_alvo
                self.modo_playlist_texto = self.modo_playlist_texto_alvo
                self.rotulo_aviso = self.rotulo_aviso_alvo
                self._inicio_leitura_texto = self.t_texto + TEXTO_SAI + TEXTO_ENTRA
                u = (t - TEXTO_SAI) / TEXTO_ENTRA
                if u >= 1:
                    self.t_texto = None
                else:
                    u = _suave(u)
                    ta, dy = u, TEXTO_DESLOC * (1 - u)

        # Até o fim do fade de troca, o título anterior conserva seu próprio tempo.
        if not self.modo_playlist_texto and musica is not None and self.titulo == musica:
            self._posicao_legenda = (posicao_atual(snap, agora)
                                    if tocando or snap.get("dur", 0) > 0 else None)

        # HUD de volume
        volume_alvo = snap.get("volume")
        if volume_alvo is not None:
            if self.volume_anim is None:
                self.volume_anim = float(volume_alvo)
            else:
                # Usa o mesmo relógio de quadros que mantém o giro da capa suave.
                fator = -math.expm1(-dt / HUD_VOLUME_TAU)
                self.volume_anim += (volume_alvo - self.volume_anim) * fator
                if abs(volume_alvo - self.volume_anim) < 0.03:
                    self.volume_anim = float(volume_alvo)
        alvo_hud = 1.0 if time.monotonic() < snap.get("volume_exibir_ate", 0.0) else 0.0
        if self.hud <= 0.0 and alvo_hud > 0.0 and self.pipe is not None:
            self.pipe.marcar_evento("HUD de volume entrou")
        if self.hud < alvo_hud:
            self.hud = min(alvo_hud, self.hud + dt / HUD_ENTRA)
        elif self.hud > alvo_hud:
            self.hud = max(alvo_hud, self.hud - dt / HUD_SAI)

        cor = _cor_rgba(cor)
        snap.update(musica=self.titulo, capa=capa_img, cor_viva=cor, cor_capa=cor,
                    texto_alpha=ta, texto_dy=dy, posicao_legenda=self._posicao_legenda,
                    hud_alpha=_suave(self.hud),
                    volume_visual=self.volume_anim,
                    modo_playlist_texto=self.modo_playlist_texto,
                    rotulo_aviso=self.rotulo_aviso, tempo_visual=agora,
                    inicio_leitura_texto=self._inicio_leitura_texto,
                    escala_capa=self._escala_capa, progresso_alpha=self._alfa_progresso,
                    glow_alpha=self._pausa.glow,
                    progresso_preenchimento=self._pausa.preenchimento)
        inicio_render = time.perf_counter()
        self._diag_tempos["animacao_ms"] = (inicio_render - inicio_animacao) * 1000
        quadro = renderizar(snap)
        render_ms = (time.perf_counter() - inicio_render) * 1000
        self._diag_tempos["desenhar_ms"] = render_ms

        if self.hud > 0.01:
            self._hud_render_max_ms = max(self._hud_render_max_ms, render_ms)
            agora_log = time.monotonic()
            if agora_log - self._ultimo_log_hud >= 1.0:
                fila_video = self.pipe.cod.saida.qsize() if self.pipe else -1
                log(
                    f"diagnóstico HUD volume: render_máx={self._hud_render_max_ms:.1f}ms "
                    f"fila_vídeo={fila_video}"
                )
                self._ultimo_log_hud = agora_log
                self._hud_render_max_ms = 0.0

        return quadro

    def _publicar_visual(self, visivel):
        if not controles.interface:
            return
        # Troca atômica da referência; a janela recebe números, não quadros do vídeo.
        estado["visual_disco"] = {
            "angulo": self.angulo,
            "velocidade": self.vel if visivel and self.trans is None else 0.0,
            "angulo_disco": self._angulo_fundo,
            "velocidade_disco": self._vel_fundo if visivel else 0.0,
            "escala": self._escala_capa, "visivel": bool(visivel),
            "instante": time.monotonic(), "capa": self.capa,
        }

    def contexto_diagnostico(self):
        return {**self._diag_tempos, "playlist": self.exibindo_playlist,
                "texto_animando": self.t_texto is not None, "hud": self.hud > 0.01}

    def _aviso_proxima(self, snap, agora, tocando):
        reproducao = snap.get("reproducao_id", 0)
        if reproducao != self._proxima_reproducao:
            self._proxima_reproducao = reproducao
            self._proxima_ja_mostrada = False
            self._proxima_ate = 0.0
            self._proxima_inicio = None
        proxima = snap.get("proxima_faixa") or {}
        # A duração do Windows pode ser menor (ex.: 243,6s contra 249,6s na API).
        duracao = proxima.get("duracao_spotify") or snap.get("dur", 0.0)
        restante = duracao - snap.get("pos", 0.0)
        if tocando:
            restante -= max(0.0, time.monotonic() - snap.get("t_poll", 0.0))
        valida = (tocando and proxima.get("reproducao_id") == reproducao
                  and proxima.get("capa") is not None and snap.get("capa") is not None
                  and 0.0 < restante <= PROXIMA_ANTECEDENCIA)
        # Não abre um aviso tarde demais para retornar ao álbum antes da troca.
        if valida and restante > PROXIMA_RETORNO_ALBUM and not self._proxima_ja_mostrada:
            self._proxima_ja_mostrada = True
            self._proxima_inicio = agora
            self._proxima_ate = agora + PROXIMA_DURACAO
            log(f"Spotify a seguir: exibindo {proxima['nome']} (faltam {restante:.1f}s).")
        if not valida or restante <= PROXIMA_RETORNO_ALBUM:
            self._proxima_ate = 0.0
        mostrar = valida and agora < self._proxima_ate
        snap["mostrar_proxima"] = mostrar
        return valida

    def _iniciar_aviso_playlist(self, nome, agora):
        largura = F_TITULO.getlength(nome or "")
        duracao = PLAYLIST_DURACAO
        if percurso_playlist(largura) > 0:
            tempo_leitura = (PLAYLIST_ESPERA_INICIAL + percurso_playlist(largura) / MARQUEE_VELOCIDADE
                             + PLAYLIST_ESPERA_FINAL)
            duracao = max(duracao, TEXTO_SAI + TEXTO_ENTRA + tempo_leitura)
        self._mensagem_playlist_ate = agora + duracao
        self._inicio_mensagem_playlist = agora
        self._proxima_mensagem_playlist = agora + max(60.0, duracao + 1.0)

    def _quadro_sem_painel(self, agora_real, mostrar_fundo=False):
        if self.letras is not None:
            self.letras.consultar(None)
        estado["status_letras"] = None
        self._publicar_visual(False)
        quadro = None
        if mostrar_fundo:
            if self.p or self.titulo is not None:
                self._zerar()
            self.p, self.estava_tocando = 0.0, False
            if self.fundo_ocioso is not None:
                inicio = time.perf_counter()
                quadro = self.fundo_ocioso.proximo()
                self._diag_tempos["fundo_ms"] = (time.perf_counter() - inicio) * 1000
        self._checar_brilho(agora_real, quadro is None)
        return self._quadro_apagado if quadro is None else quadro

    def quadro(self, agora):
        self._diag_tempos = {"fundo_ms": 0.0, "animacao_ms": 0.0,
                             "desenhar_ms": 0.0, "compor_ms": 0.0,
                             "transicao_capa": False}
        dt = 0.0 if self.t_ultimo is None else min(max(agora - self.t_ultimo, 0.0), 0.2)
        self.t_ultimo = agora
        self._dt_fundo = dt
        agora_real = time.monotonic()
        config = controles.config
        modo = config["modo"]
        snap = dict(estado)
        if not config["tela_ligada"]:
            return self._quadro_sem_painel(agora_real)
        if modo == "video":
            self._retomar_giro_dinamico = False
            self._capa_retomada_dinamica = self._cor_retomada_dinamica = None
            return self._quadro_sem_painel(agora_real, mostrar_fundo=True)
        if spotify_em_repouso(snap, agora_real):
            return self._quadro_sem_painel(agora_real, mostrar_fundo=modo == "dinamico")

        letra = status_letras = None
        if self.letras is not None:
            faixa = faixa_para_letras(config, snap)
            resultado = self.letras.consultar(faixa)
            if faixa is not None:
                letra = resultado.letra
                status_letras = (faixa, modo, resultado.situacao)
        if modo == "clipe" and self.clipe is not None:
            quadro = self.clipe.quadro(snap, agora)
            if quadro is not None:
                self._checar_brilho(agora_real, False)
                self._publicar_visual(False)
                if self.clipe.letra_no_video is True:
                    estado["status_letras"] = (faixa, modo, "exibindo") if faixa is not None else None
                    return quadro
                if letra is not None:
                    posicao = self.clipe.posicao_letra(snap, time.monotonic()) + config["adiantar_letras_ms"] / 1000
                    legendado = self.visual_letras.clipe(quadro, letra, posicao)
                    estado["status_letras"] = (faixa, modo, "exibindo") if legendado is not quadro else status_letras
                    return legendado
                estado["status_letras"] = status_letras
                return quadro
        if modo != "dinamico":
            self._retomar_giro_dinamico = False
            self._capa_retomada_dinamica = None
            self._cor_retomada_dinamica = None
        if modo in ("spotify", "clipe") and snap.get("musica") is None:
            snap.update(snap.get("ultima_midia") or {
                "musica": ("Pronto para tocar", "Abra o Spotify"), "capa": None,
                "pos": 0.0, "dur": 0.0,
            })
            snap["tocando"] = False
        tocando = snap.get("musica") is not None and snap.get("tocando", False)
        if tocando or modo == "video":
            self._t_parado = None
        elif self._t_parado is None:
            self._t_parado = agora
        parado_por = 0.0 if self._t_parado is None else agora - self._t_parado
        ocioso_longo = parado_por > PAUSA_ESTATICA_MAX
        if TELA_APAGAR_APOS and parado_por > TELA_APAGAR_APOS:
            # Muito tempo sem música: apaga a tela e zera o brilho.
            if self.letras is not None:
                self.letras.consultar(None)
            estado["status_letras"] = None
            self._apagada_por_inatividade = True
            self.estava_tocando = tocando
            self._publicar_visual(False)
            return self._quadro_apagado
        self._apagada_por_inatividade = False
        self._girar_fundo(agora, dt, tocando)
        reservar_final = self._aviso_proxima(snap, agora, tocando)
        uri_playlist = snap.get("playlist_uri") if tocando and snap.get("capa_playlist") is not None else None
        evento_playlist = snap.get("playlist_evento", 0)
        if evento_playlist != self._playlist_evento_visto and uri_playlist:
            # Mudança detectada pelo monitor Spotify: anuncia antes da capa do álbum.
            self._playlist_evento_visto = evento_playlist
            self._playlist_uri_timer = uri_playlist
            self._iniciar_aviso_playlist(snap.get("playlist_nome"), agora)
        elif uri_playlist != self._playlist_uri_timer:
            self._playlist_uri_timer = uri_playlist
            self._proxima_mensagem_playlist = agora + 60.0 if uri_playlist else None
            self._mensagem_playlist_ate = 0.0
            self._inicio_mensagem_playlist = None
        elif uri_playlist and self._proxima_mensagem_playlist is not None and agora >= self._proxima_mensagem_playlist:
            self._iniciar_aviso_playlist(snap.get("playlist_nome"), agora)
        if reservar_final:
            # A seguir tem prioridade; adia a playlist para evitar avisos seguidos.
            self._mensagem_playlist_ate = 0.0
            self._proxima_mensagem_playlist = agora + 60.0 if uri_playlist else None
        mostrar_playlist = bool(uri_playlist and agora < self._mensagem_playlist_ate)
        if (mostrar_playlist != self._diag_playlist_ativa
                or (mostrar_playlist and uri_playlist != self._diag_playlist_uri)):
            self._diag_playlist_ativa = mostrar_playlist
            self._diag_playlist_uri = uri_playlist if mostrar_playlist else None
            evento = (f"playlist entrou/trocou: {snap.get('playlist_nome')}"
                      if mostrar_playlist else "playlist saiu; retorno ao álbum ou pausa")
            if self.pipe is not None:
                self.pipe.marcar_evento(evento)
        snap["mostrar_capa_playlist"] = mostrar_playlist
        snap["mensagem_playlist"] = (
            f"Você está ouvindo '{snap.get('playlist_nome')}'"
            if mostrar_playlist and snap.get("playlist_nome") else None
        )
        snap["mensagem_playlist_inicio"] = (
            self._proxima_inicio if snap["mostrar_proxima"] else self._inicio_mensagem_playlist)
        if tocando:
            self.t_pausa = None
        elif self.estava_tocando:
            self.t_pausa = agora
            if modo == "dinamico":
                self._retomar_giro_dinamico = True
                self._capa_retomada_dinamica = self._capa_girada()
                self._cor_retomada_dinamica = self.cor
        mostrar = (modo in ("spotify", "clipe") or tocando
                   or (modo != "video" and self.t_pausa is not None and agora - self.t_pausa < ATRASO_OCIOSO))
        alvo = 1.0 if mostrar else 0.0
        if self.p < alvo:
            self.p = min(alvo, self.p + dt / FADE_ENTRA)
        elif self.p > alvo:
            self.p = max(alvo, self.p - dt / FADE_SAI)
        ps = _suave(self.p)

        if (self.p <= 0.0 and not tocando) or (modo not in ("spotify", "clipe") and not tocando and self.titulo is None):
            return self._quadro_sem_painel(agora_real, mostrar_fundo=True)

        self._checar_brilho(agora_real, False)
        bg = self._fundo(ps)
        try:
            ov = self._painel(snap, agora, dt, tocando)
            if ps < 0.999:
                a = ov.getchannel("A").point(lambda v: int(v * ps))
                ov.putalpha(a)
        except Exception as exc:
            if str(exc) != self.erro_log:
                self.erro_log = str(exc)
                log(f"erro desenhando o painel: {exc}")
            self.estava_tocando = tocando
            estado["status_letras"] = status_letras
            return bg
        self.estava_tocando = tocando
        self._publicar_visual(self.p > 0.0 and self.capa is not None)
        if letra is not None:
            posicao = posicao_atual(snap, time.monotonic()) + config["adiantar_letras_ms"] / 1000
        if letra is not None and not ocioso_longo:
            posicao = posicao_atual(snap, time.monotonic()) + ADIANTAMENTO_LETRAS
            # A capa confirmada é da faixa atual. O painel pode ainda mostrar a anterior.
            quadro = self.visual_letras.spotify(snap.get("capa_letras"), letra, posicao)
            estado["status_letras"] = (faixa, modo, "exibindo")
            return quadro
        estado["status_letras"] = status_letras
        inicio_compor = time.perf_counter()
        if bg.mode == "RGB":
            # Fundo opaco do FFmpeg: aplica o alfa diretamente em RGB, evitando
            # as cópias intermediárias RGBA e a conversão de volta por quadro.
            quadro = bg.copy()
            quadro.paste(ov, (0, 0), ov)
        else:
            base = bg.convert("RGBA")
            base.alpha_composite(ov)
            quadro = base.convert("RGB")
        self._diag_tempos["compor_ms"] = (time.perf_counter() - inicio_compor) * 1000
        return quadro


def sessao_usb(gaming, clipe, letras):
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg não encontrado no PATH")
    dev = libusb_package.find(idVendor=0x1CBE, idProduct=0x21)
    if dev is None:
        raise RuntimeError("tela não encontrada")
    try:
        dev.set_configuration()

        def cmd(n):
            pacote = encrypt_command_packet(build_command_packet_header(n))
            return write_to_device(dev, pacote)

        for n in (111, 112, 13):
            cmd(n)

        primeira = True
        while not parar.is_set():
            arquivo_gaming = gaming.destino()
            economico = arquivo_gaming is not None
            modo_clipe = not economico and controles.config["modo"] == "clipe"
            fps_atual = FPS_GAMING if economico else (FPS_CLIPE if modo_clipe else FPS)
            kbps_atual = KBPS_CLIPE if modo_clipe else KBPS
            with silenciar_saida():
                # Reinicia o decodificador da tela entre dois fluxos H.264 completos.
                # A thread do envio anterior já foi encerrada antes de chegar aqui.
                for n in (111, 112, 13):
                    cmd(n)
                    time.sleep(0.05)
                # Só o primeiro quadro confirma a disponibilidade do vídeo ao vivo.
                em_repouso = not economico and (controles.config["modo"] == "video"
                                              or spotify_em_repouso(dict(estado), time.monotonic()))
                brilho = brilho_tela_para(datetime.datetime.now(), em_repouso)
                operations.send_brightness_command(dev, brilho)
                time.sleep(0.05)
                cmd(41)
                time.sleep(0.05)
                operations.clear_image(dev)
                time.sleep(0.05)
                operations.send_frame_rate_command(dev, fps_atual)
                time.sleep(0.05)

            controles.gaming_ativo = economico
            estado.update(tela_conectada=True, erro_interface="", gaming_ativo=economico,
                          modo_exibicao=controles.modo_efetivo(), visual_disco=None, status_letras=None)
            if economico:
                estado["fundo_ocioso_ativo"] = gaming.origem_do(arquivo_gaming)
            if primeira:
                log(f"tela conectada - brilho inicial: {brilho}%")
                primeira = False
            log("Gaming: vídeo preparado a 30 FPS, sem renderização ou codificação ao vivo"
                if economico else f"tela: fluxo {'Clipe' if modo_clipe else 'normal'} a {fps_atual} FPS")

            # Cada fluxo tem seu evento para trocar o perfil da tela durante o uso.
            parar_sessao = threading.Event()
            fundo_musica = fundo_ocioso = pipe = ponte_thread = None
            try:
                if economico:
                    pipe = PipelinePreparado(dev, arquivo_gaming, parar_sessao, log)
                else:
                    fundo_musica = ao_vivo.FundoDecoder(
                        ARQUIVO_MUSICA, fps_atual, log=log, nome="fundo da música")
                    if controles.interface:
                        fundo_ocioso = FundoOcioso(controles, ao_vivo.FundoDecoder,
                                                   ARQUIVO_BACKGROUND, fps_atual, estado, log)
                    else:
                        fundo_ocioso = ao_vivo.FundoDecoder(
                            ARQUIVO_BACKGROUND, fps_atual, log=log, nome="fundo ocioso")
                    painel = Painel(fundo_musica, fundo_ocioso, brilho, clipe, fps=fps_atual, letras=letras)
                    pipe = ao_vivo.PipelineAoVivo(
                        dev, painel.quadro, fps=fps_atual, kbps=kbps_atual, parar=parar_sessao, log=log)
                    painel.pipe = pipe
                    pipe.contexto = painel.contexto_diagnostico

                def ponte():
                    ultimo_brilho = brilho
                    ultimo_q, t_q = -1, time.monotonic()
                    while not parar_sessao.is_set():
                        perfil_clipe = not economico and controles.config["modo"] == "clipe"
                        if parar.is_set() or gaming.destino() != arquivo_gaming or perfil_clipe != modo_clipe:
                            parar_sessao.set()
                            return
                        if economico:
                            novo = brilho_tela_para(datetime.datetime.now())
                            if novo != ultimo_brilho:
                                pipe.env.executar(
                                    lambda tela, b=novo: operations.send_brightness_command(tela, b))
                                ultimo_brilho = novo
                                log(f"Brilho alterado para: {novo}%")
                        if not economico:
                            q = pipe.quadros
                            if q != ultimo_q:
                                ultimo_q, t_q = q, time.monotonic()
                            elif time.monotonic() - t_q > 8.0:
                                log("watchdog: nenhum quadro gerado em 8s; reiniciando o fluxo")
                                parar_sessao.set()
                                return
                        parar_sessao.wait(0.1)

                ponte_thread = threading.Thread(target=ponte, name="PerfilTela", daemon=True)
                ponte_thread.start()
                pipe.rodar()
            finally:
                parar_sessao.set()
                letras.consultar(None)
                estado["status_letras"] = None
                if ponte_thread is not None:
                    ponte_thread.join(timeout=1)
                for decoder in (fundo_musica, fundo_ocioso):
                    if decoder is not None:
                        decoder.fechar()
                if pipe is not None and pipe.env.ident is not None:
                    pipe.env.join(timeout=8)
                    if pipe.env.is_alive():
                        raise RuntimeError("O envio USB não encerrou; aguarde antes de iniciar outro fluxo.")
                with contextlib.suppress(Exception):
                    cmd(123)
                if parar.is_set():
                    with contextlib.suppress(Exception):
                        operations.send_brightness_command(dev, 0)
                    with contextlib.suppress(Exception):
                        operations.clear_image(dev)
    finally:
        controles.gaming_ativo = False
        estado["gaming_ativo"] = False
        estado["modo_exibicao"] = controles.modo_efetivo()
        estado["tela_conectada"] = False
        estado["visual_disco"] = None
        try:
            usb.util.dispose_resources(dev)
        except Exception:
            pass


def transmitir(gaming, clipe, letras):
    while not parar.is_set():
        try:
            sessao_usb(gaming, clipe, letras)
        except Exception as exc:
            estado["erro_interface"] = str(exc)
            log(f"erro: {exc}")
        if parar.is_set():
            break
        log(f"tentando reconectar em {ESPERA_RECONEXAO}s")
        parar.wait(ESPERA_RECONEXAO)


async def ler_capa(info, assinatura_atual=None):
    fluxo = None
    try:
        if info.thumbnail is None:
            return None, None, None, None
        fluxo = await info.thumbnail.open_read_async()
        tamanho = fluxo.size
        if not 0 < tamanho <= 8 * 1024 * 1024:
            raise ValueError("miniatura vazia ou maior que 8 MB")
        buf = Buffer(tamanho)
        lido = await fluxo.read_async(buf, tamanho, InputStreamOptions.READ_AHEAD)
        if lido.length != tamanho:
            raise ValueError("miniatura incompleta; aguardando nova leitura")
        dados = bytes(memoryview(lido)[:lido.length])
        assinatura = hashlib.sha256(dados).digest()
        if assinatura == assinatura_atual:
            # A imagem não mudou: evita decodificação, nova transição e novo RGB.
            return None, None, None, assinatura
        capa, cor, cor_viva = preparar_capa(dados)
        return capa, cor, cor_viva, assinatura
    except Exception as exc:
        log(f"capa: {exc}")
        return None, None, None, None
    finally:
        if fluxo is not None:
            with contextlib.suppress(Exception):
                fluxo.close()


async def vigiar_spotify():
    mgr = await MediaManager.request_async()
    loop = asyncio.get_running_loop()
    chave, capa_carregada_para, tentativas, ultimo_erro = None, None, 0, ""
    proxima_tentativa_capa = 0.0
    assinatura_capa, capa_api_confirmada = None, False
    conferencias_capa = []
    pedido_id = 0
    ultima_reproducao, ultima_pos = None, 0.0
    em_video = False
    revisao_capa, revisao_lida, revisao_sessoes = 0, -1, 0
    sessao_observada, token_capa, chave_sessao = None, None, None

    def marcar_capa():
        nonlocal revisao_capa
        revisao_capa += 1

    def marcar_sessoes():
        nonlocal revisao_sessoes
        revisao_sessoes += 1
        marcar_capa()

    def agendar_aviso(funcao):
        # Eventos WinRT podem chegar de outra thread; só publicam um aviso leve.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(funcao)

    def observar_sessao(sessao):
        nonlocal sessao_observada, token_capa, chave_sessao
        nova_chave = (sessao.source_app_user_model_id, revisao_sessoes) if sessao else None
        if nova_chave == chave_sessao:
            return
        if sessao_observada is not None and token_capa is not None:
            with contextlib.suppress(Exception):
                sessao_observada.remove_media_properties_changed(token_capa)
        sessao_observada, token_capa, chave_sessao = sessao, None, nova_chave
        marcar_capa()
        if sessao is not None:
            try:
                token_capa = sessao.add_media_properties_changed(
                    lambda _s, _e: agendar_aviso(marcar_capa))
            except Exception as exc:
                log(f"capa: avisos do Windows indisponíveis ({exc}); mantendo as releituras iniciais e a API")

    token_sessoes = None
    try:
        token_sessoes = mgr.add_sessions_changed(lambda _m, _e: agendar_aviso(marcar_sessoes))
    except Exception as exc:
        log(f"capa: aviso de sessões indisponível ({exc})")
    try:
        while not parar.is_set():
            modo = controles.modo_efetivo()
            if modo == "video":
                if not em_video:
                    observar_sessao(None)
                    estado.update(musica=None, capa=None, album=None, capa_letras=None,
                                  tocando=False, midia_pronta=True, spotify_inativo_desde=None,
                                  proxima_faixa=None, proximo_clipe=None, pedido_capa_album=None,
                                  capa_album_api=None)
                    chave = capa_carregada_para = ultima_reproducao = None
                    tentativas, proxima_tentativa_capa = 0, 0.0
                    em_video = True
                await asyncio.sleep(0.25)
                continue
            em_video = False
            try:
                sessao = None
                sessao_inativa = None
                pausado = False
                for s in mgr.get_sessions():
                    app_id = s.source_app_user_model_id.lower()
                    if "spotify" in app_id:
                        status = s.get_playback_info().playback_status
                        if status == StatusReproducao.PLAYING:
                            sessao = s
                            break
                        if sessao_inativa is None or (not pausado and status == StatusReproducao.PAUSED):
                            sessao_inativa = s
                            pausado = status == StatusReproducao.PAUSED

                tocando = sessao is not None
                if not tocando:
                    desde = estado["spotify_inativo_desde"] if pausado else None
                    # Abrir o app com a música já pausada não dá mais tempo à imagem parada.
                    if pausado and desde is None and estado["tocando"]:
                        desde = time.monotonic()
                    # Publica a pausa antes de abrir a capa ou consultar a posição.
                    estado.update(tocando=False, spotify_inativo_desde=desde)

                if not tocando and (not ESCONDER_PAUSADO or modo in ("spotify", "clipe")):
                    sessao = sessao_inativa

                observar_sessao(sessao)

                if sessao is None:
                    estado.update(musica=None, capa=None, cor_capa=(30, 215, 96, 255),
                                  cor_viva=(30, 215, 96, 255), tocando=False, midia_pronta=True,
                                  album=None, capa_letras=None,
                                  pedido_capa_album=None, capa_album_api=None)
                    chave = capa_carregada_para = None
                    tentativas, proxima_tentativa_capa = 0, 0.0
                else:
                    info = await sessao.try_get_media_properties_async()
                    nova = (info.title, info.artist)
                    identidade_capa = (*nova, info.album_title)

                    if identidade_capa != chave:
                        chave = identidade_capa
                        capa_carregada_para, assinatura_capa = None, None
                        capa_api_confirmada = False
                        tentativas, proxima_tentativa_capa = 0, 0.0
                        pedido_id += 1
                        estado.update(pedido_capa_album={"id": pedido_id, "musica": nova,
                                                       "album": info.album_title},
                                      capa_album_api=None)
                        # Alguns players avisam o título antes da imagem definitiva.
                        inicio_capa = time.monotonic()
                        conferencias_capa = [inicio_capa + atraso for atraso in (1.0, 3.0, 7.0)]

                    recuperada = estado.get("capa_album_api")
                    if (recuperada is not None and recuperada["pedido_id"] == pedido_id
                            and recuperada["musica"] == nova and not capa_api_confirmada):
                        anterior = estado.get("capa") if capa_carregada_para == nova else None
                        if not capas_equivalentes(anterior, recuperada["capa"]):
                            estado.update(capa=recuperada["capa"], cor_capa=recuperada["cor_capa"],
                                          cor_viva=recuperada["cor_viva"])
                            log(f"capa: recuperada pela API do Spotify ({nova[0]})")
                        capa_carregada_para = nova
                        # Não volte à miniatura temporária depois da confirmação da API.
                        capa_api_confirmada = True
                        conferencias_capa = []

                    agora_capa = time.monotonic()
                    conferencia_pendente = bool(conferencias_capa and agora_capa >= conferencias_capa[0])
                    precisa_carregar_capa = (not capa_api_confirmada
                        and agora_capa >= proxima_tentativa_capa
                        and (capa_carregada_para != nova or revisao_capa != revisao_lida
                             or conferencia_pendente))
                    if precisa_carregar_capa:
                        tentativas += 1
                        await asyncio.sleep(0.08)
                        # Releia após esperar: o objeto anterior pode conter a miniatura antiga.
                        info = await sessao.try_get_media_properties_async()
                        if (info.title, info.artist, info.album_title) != identidade_capa:
                            continue
                        revisao_lida = revisao_capa
                        while conferencias_capa and conferencias_capa[0] <= agora_capa:
                            conferencias_capa.pop(0)
                        capa_img, cor_capa, cor_viva, assinatura = await ler_capa(info, assinatura_capa)
                        confirmacao = await sessao.try_get_media_properties_async()
                        if (confirmacao.title, confirmacao.artist, confirmacao.album_title) != identidade_capa:
                            continue
                        if assinatura is not None:
                            if capa_img is not None:
                                anterior = estado.get("capa") if capa_carregada_para == nova else None
                                if not capas_equivalentes(anterior, capa_img):
                                    estado.update(capa=capa_img, cor_capa=cor_capa, cor_viva=cor_viva)
                                    if anterior is not None:
                                        log(f"capa: imagem atualizada pelo Windows ({nova[0]})")
                            assinatura_capa = assinatura
                            capa_carregada_para = nova
                            proxima_tentativa_capa = time.monotonic() + 0.5
                        else:
                            proxima_tentativa_capa = time.monotonic() + 1.0

                    tl = sessao.get_timeline_properties()
                    dur = (tl.end_time - tl.start_time).total_seconds()
                    pos = (tl.position - tl.start_time).total_seconds()
                    if tocando:
                        lu = tl.last_updated_time
                        if lu.tzinfo is None:
                            lu = lu.replace(tzinfo=datetime.timezone.utc)
                        agora = datetime.datetime.now(datetime.timezone.utc)
                        extra = (agora - lu).total_seconds()
                        if 0 <= extra <= dur:
                            pos += extra

                    # Pausar mantém o identificador; trocar/voltar na faixa invalida a fila.
                    mudou_reproducao = nova != ultima_reproducao or pos < ultima_pos - 2.0
                    reproducao_id = estado["reproducao_id"] + int(mudou_reproducao)
                    ultima_reproducao, ultima_pos = nova, pos
                    estado.update(
                        musica=nova, pos=pos, dur=dur,
                        album=info.album_title,
                        capa_letras=estado.get("capa") if capa_carregada_para == nova else None,
                        t_poll=time.monotonic(), tocando=tocando,
                        spotify_inativo_desde=None if tocando else desde,
                        reproducao_id=reproducao_id,
                        proxima_faixa=None if mudou_reproducao else estado.get("proxima_faixa"),
                    )
                    if capa_carregada_para == nova and estado.get("capa") is not None:
                        estado["ultima_midia"] = {
                            k: estado[k] for k in ("musica", "capa", "cor_capa", "cor_viva", "pos", "dur", "t_poll")
                        }
                    # Só libera o OpenRGB após tentar obter a capa que define a cor inicial.
                    if estado.get("capa") is not None or tentativas >= 2:
                        estado["midia_pronta"] = True
            except Exception as exc:
                if str(exc) != ultimo_erro:
                    ultimo_erro = str(exc)
                    log(f"spotify/midia: {exc}")

            await asyncio.sleep(0.15)
    finally:
        observar_sessao(None)
        if token_sessoes is not None:
            with contextlib.suppress(Exception):
                mgr.remove_sessions_changed(token_sessoes)


async def main_async():
    await vigiar_spotify()


def main():
    gaming = PreparadorGaming(controles, estado, parar, ARQUIVO_BACKGROUND, log)
    gaming.iniciar()
    if "--interface" in sys.argv:
        iniciar_ponte(controles, estado, parar, log)
    watcher_playlist = SpotifyPlaylistWatcher(
        atualizar_capa_playlist, log,
        ler_estado=lambda: dict(estado), ao_proxima=atualizar_proxima_faixa,
        ao_capa_album=atualizar_capa_album,
        ao_proximo_clipe=atualizar_proximo_clipe,
        ao_reproducao=lambda dados: estado.update(spotify_reproducao=dados))
    watcher_playlist.iniciar()
    try:
        import leds_openrgb
        leds_openrgb.iniciar(estado, brilho_para, parar, log)
    except ImportError as exc:
        log(f"LEDs desligados (openrgb-python não instalado): {exc}")
    
    from leds_lotus import LotusLantern
    lotus = LotusLantern(controles, estado, brilho_para, parar, log).iniciar()

    t_vol = threading.Thread(target=_laco_volume, daemon=True)
    t_vol.start()

    clipe = FonteClipe(lambda: dict(estado),
                      lambda: (not parar.is_set() and estado.get("tela_conectada")
                               and controles.modo_efetivo() == "clipe"
                               and controles.config["tela_ligada"]),
                      lambda texto: estado.update(status_clipe=texto), log,
                      ler_adiantamento_ms=lambda: controles.config["adiantar_clipe_ms"])
    clipe.iniciar()
    letras = FonteLetras()
    letras.iniciar()
    t = threading.Thread(target=transmitir, args=(gaming, clipe, letras), daemon=True)
    t.start()
    
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass
    parar.set()
    letras.fechar()
    clipe.fechar()
    lotus.fechar()
    gaming.fechar()
    watcher_playlist.fechar()
    t.join(timeout=12)
    log("parado")


if __name__ == "__main__":
    try:
        reserva = reservar_execucao()
    except RuntimeError as exc:
        log(str(exc))
        sys.exit(1)
    try:
        main()
    finally:
        liberar_execucao(reserva)
