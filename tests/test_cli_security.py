import tempfile
import unittest
from pathlib import Path

import opencode


class BashEnvGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_command_reading_env_file_is_refused(self):
        result = opencode.execute_tool("bash", {"command": "cat .env"}, self.root)
        self.assertIn("recusado", result)

    def test_command_reading_quoted_env_local_is_refused(self):
        self.assertIsNotNone(opencode.ENV_FILE_IN_COMMAND.search('type "config/.env.local"'))

    def test_env_example_is_not_matched(self):
        self.assertIsNone(opencode.ENV_FILE_IN_COMMAND.search("cat .env.example"))


class RedactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_secret_value_from_project_env_is_removed(self):
        (self.root / ".env").write_text("GEMINI_API_KEY=segredo-de-teste-123\n", encoding="utf-8")
        self.assertEqual(
            opencode.redact("valor: segredo-de-teste-123 fim", self.root),
            "valor: [SEGREDO REMOVIDO] fim",
        )

    def test_text_without_secrets_is_unchanged(self):
        self.assertEqual(opencode.redact("saída normal", self.root), "saída normal")


class HistoryTrimTests(unittest.TestCase):
    def test_trim_keeps_system_prompt_and_starts_at_user_turn(self):
        system = {"role": "system", "content": "s"}
        turns = []
        for i in range(40):
            turns.append({"role": "user", "content": f"u{i}"})
            turns.append({"role": "assistant", "content": f"a{i}"})
        trimmed = opencode.trim_history([system, *turns])
        self.assertEqual(trimmed[0], system)
        self.assertEqual(trimmed[1]["role"], "user")
        self.assertLessEqual(len(trimmed) - 1, opencode.MAX_HISTORY_MESSAGES)


if __name__ == "__main__":
    unittest.main()
