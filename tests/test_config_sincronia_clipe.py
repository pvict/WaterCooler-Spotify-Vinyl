import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from controle_interface import Controles, carregar, salvar


class ConfigSincroniaClipeTests(unittest.TestCase):
    def test_preferencia_antiga_comeca_em_zero_e_o_ajuste_e_salvo(self):
        with TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "interface.json"
            arquivo.write_text('{"modo": "clipe"}', encoding="utf-8")
            with patch("controle_interface.ARQUIVO", arquivo):
                self.assertEqual(carregar().get("adiantar_clipe_ms"), 0)
                salvar({"modo": "clipe", "adiantar_clipe_ms": 1500})
                self.assertEqual(carregar().get("adiantar_clipe_ms"), 1500)

    def test_ponte_aceita_apenas_inteiros_no_intervalo(self):
        controles = Controles()
        for valor in (3000, -3000):
            controles.aplicar({"adiantar_clipe_ms": valor})
            self.assertEqual(controles.config.get("adiantar_clipe_ms"), valor)
        for valor in (3001, -3001, True, 50.0, "50", None):
            with self.subTest(valor=valor):
                controles.aplicar({"adiantar_clipe_ms": valor})
                self.assertEqual(controles.config.get("adiantar_clipe_ms"), -3000)
        controles.aplicar({"modo": "video"})
        self.assertEqual(controles.config.get("adiantar_clipe_ms"), -3000)
