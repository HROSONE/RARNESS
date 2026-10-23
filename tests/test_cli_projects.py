import json
import tempfile
import unittest
from pathlib import Path

import opencode


PLANO = {
    "project_name": "Demo",
    "project_type": "ferramenta",
    "requirements": [],
    "tasks": [{"id": "t1", "title": "Base", "files": ["app.py"], "depends_on": [], "acceptance": []}],
    "questions": [],
}


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
