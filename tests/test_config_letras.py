import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from controle_interface import Controles, carregar, salvar


class ConfigLetrasTests(unittest.TestCase):
    def test_preferencia_antiga_comeca_desligada_e_escolha_e_persistida(self):
        with TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "interface.json"
            arquivo.write_text('{"modo": "clipe", "adiantar_clipe_ms": 800}', encoding="utf-8")
            with patch("controle_interface.ARQUIVO", arquivo):
                config = carregar()
                self.assertIs(config.get("letras_ativas"), False)
                config["letras_ativas"] = True
                salvar(config)
                self.assertIs(carregar().get("letras_ativas"), True)
                self.assertEqual(carregar()["adiantar_clipe_ms"], 800)

    def test_ponte_mantem_preferencia_ate_receber_um_booleano(self):
        controles = Controles()
        controles.aplicar({"letras_ativas": True})
        self.assertIs(controles.config.get("letras_ativas"), True)
        for valor in ("false", 0, 1, None):
            with self.subTest(valor=valor):
                controles.aplicar({"letras_ativas": valor})
                self.assertIs(controles.config["letras_ativas"], True)
        controles.aplicar({"modo": "video", "gaming": True})
        self.assertIs(controles.config["letras_ativas"], True)
        controles.aplicar({"letras_ativas": False})
        self.assertIs(controles.config["letras_ativas"], False)
