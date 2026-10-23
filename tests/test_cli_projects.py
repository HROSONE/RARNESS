import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import opencode
from rarness.executor import run_command


def cliente_falso(finish_reason, conteudo="{}"):
    escolha = SimpleNamespace(finish_reason=finish_reason, message=SimpleNamespace(content=conteudo))
    completion = SimpleNamespace(choices=[escolha])
    criar = mock.Mock(return_value=completion)
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=criar)))


PLANO = {
    "project_name": "Demo",
    "project_type": "ferramenta",
    "requirements": [],
    "tasks": [{"id": "t1", "title": "Base", "files": ["app.py"], "depends_on": [], "acceptance": []}],
    "questions": [],
}


class ModelOutputTests(unittest.TestCase):
    def test_truncated_answer_is_rejected(self):
        ask = opencode.model_ask(cliente_falso("length", "{\"arquivos\": {\"a.py\": \"cort"), "gemini-3.8-flash")
        with self.assertRaises(ValueError):
            ask("prompt")

    def test_complete_answer_is_returned(self):
        ask = opencode.model_ask(cliente_falso("stop", "ok"), "gemini-3.8-flash")
        self.assertEqual(ask("prompt"), "ok")

    def test_secret_variables_are_removed_from_test_environment(self):
        ambiente = {"GEMINI_API_KEY": "x", "GITHUB_TOKEN": "y", "DB_PASSWORD": "z", "PATH": "/bin"}
        with mock.patch.dict(os.environ, ambiente, clear=True):
            limpo = opencode.ambiente_sem_segredos()
        self.assertEqual(limpo, {"PATH": "/bin"})

    def test_run_command_uses_given_environment_only(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        comando = f'"{sys.executable}" -c "import os; print(os.environ.get(\'SEGREDO_X\', \'ausente\'))"'
        with mock.patch.dict(os.environ, {"SEGREDO_X": "presente"}):
            resultado = run_command(comando, Path(temp.name), env={"PATH": os.environ.get("PATH", "")})
        self.assertIn("ausente", resultado.stdout)


class ProjectCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_unknown_command_is_not_handled(self):
        self.assertFalse(opencode.handle_project_command("olá", self.root, None))

    def test_plano_without_description_shows_usage(self):
        self.assertTrue(opencode.handle_project_command("/plano api", self.root, None))
        self.assertFalse((self.root / ".rarness" / "state.json").exists())

    def test_construir_without_plan_does_nothing(self):
        self.assertTrue(opencode.handle_project_command("/construir", self.root, None))
        self.assertFalse((self.root / ".rarness").exists())

    def test_zip_without_plan_does_nothing(self):
        self.assertTrue(opencode.handle_project_command("/zip", self.root, None))
        self.assertFalse((self.root / ".rarness").exists())

    def test_plano_creates_state_from_model_answer(self):
        ask = lambda prompt: json.dumps(PLANO)
        self.assertTrue(opencode.handle_project_command("/plano ferramenta soma de números", self.root, ask))
        self.assertTrue((self.root / ".rarness" / "state.json").is_file())

    def test_construir_refuses_env_in_test_command(self):
        ask = lambda prompt: json.dumps(PLANO)
        opencode.handle_project_command("/plano ferramenta soma", self.root, ask)
        self.assertTrue(opencode.handle_project_command("/construir cat .env", self.root, ask))
        self.assertFalse((self.root / "app.py").exists())


if __name__ == "__main__":
    unittest.main()
