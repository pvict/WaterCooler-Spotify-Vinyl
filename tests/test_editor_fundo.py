import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from controle_interface import PADRAO
from interface import Janela


class EditorFundoIntegrationTests(unittest.TestCase):
    def test_falha_ao_salvar_preserva_video_origem_e_ajustes(self):
        anterior = dict(PADRAO, video_ocioso="atual.mp4", video_origem="original.mp4",
                        video_ajustes={"zoom": 130})
        janela = SimpleNamespace(config_usuario=dict(anterior),
            fundo_painel=SimpleNamespace(atualizar=Mock()), motor=SimpleNamespace(enviar=Mock()))
        with patch("interface.salvar", side_effect=OSError("disco indisponível")):
            Janela.definir_fundo(janela, "novo.mp4", "nome", "nova origem.mp4", {"zoom": 180})
        self.assertEqual(janela.config_usuario, anterior)
        janela.motor.enviar.assert_not_called()

    def test_reabrir_editor_usa_origem_e_ajustes_em_vez_do_video_processado(self):
        abrir = Mock()
        janela = SimpleNamespace(fundo_convertendo=False,
            config_usuario=dict(PADRAO, video_ocioso="processado.mp4",
                                video_origem="original.mp4", video_ajustes={"zoom": 130}),
            abrir_editor_fundo=abrir)
        Janela.ajustar_fundo(janela)
        abrir.assert_called_once_with("original.mp4", {"zoom": 130})


if __name__ == "__main__":
    unittest.main()
