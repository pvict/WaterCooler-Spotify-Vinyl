import unittest
from unittest.mock import Mock, patch

from spotify_playlist import SpotifyPlaylistWatcher


class FilaClipeTest(unittest.TestCase):
    def test_clipe_recebe_metadados_da_fila_antes_da_capa_e_revalida_no_final(self):
        item = {"name": "Atual", "artists": [{"name": "Cantora"}], "type": "track",
                "uri": "spotify:track:atual", "duration_ms": 200000}
        proxima = dict(item, name="Seguinte", uri="spotify:track:proxima", album={"name": "Álbum"})
        snap = {"modo_exibicao": "clipe", "musica": ("Atual", "Cantora"),
                "tocando": True, "reproducao_id": 1, "pos": 10.0, "t_poll": 50.0, "dur": 200.0}
        atual = {"item": item, "is_playing": True, "progress_ms": 10000}
        ao_proxima, ao_proximo_clipe = Mock(), Mock()
        watcher = SpotifyPlaylistWatcher(Mock(), Mock(), ler_estado=lambda: snap,
                                         ao_proxima=ao_proxima, ao_proximo_clipe=ao_proximo_clipe)
        with (patch("spotify_playlist.time.monotonic", return_value=50.0),
              patch.object(watcher, "_api", return_value={"currently_playing": item,
                                                         "queue": [proxima]}) as api):
            watcher._consultar_proxima(atual, "token")
            watcher._consultar_proxima(atual, "token")
            self.assertEqual(api.call_count, 1)
            publicado = ao_proximo_clipe.call_args.args[0]
            self.assertEqual(publicado["musica"], ("Seguinte", "Cantora"))
            self.assertEqual(publicado["album"], "Álbum")
            self.assertEqual(publicado["reproducao_id"], 1)
            ao_proxima.assert_not_called()
            atual["progress_ms"] = 175000
            proxima["name"] = "Fila editada"
            watcher._consultar_proxima(atual, "token")
            self.assertEqual(api.call_count, 2)
            self.assertEqual(ao_proximo_clipe.call_args.args[0]["musica"], ("Fila editada", "Cantora"))
