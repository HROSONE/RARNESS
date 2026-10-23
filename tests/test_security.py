import tempfile
import unittest
from pathlib import Path

from rarness_security import resolve_project_path


class PathSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_relative_path_inside_project(self):
        self.assertEqual(resolve_project_path("src/main.py", self.root), self.root / "src/main.py")

    def test_absolute_path_outside_project_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_project_path(str(self.root.parent / "outside.txt"), self.root)

    def test_parent_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_project_path("../outside.txt", self.root)

    def test_env_file_is_blocked(self):
        with self.assertRaises(ValueError):
            resolve_project_path(".env", self.root)

    def test_nested_env_file_is_blocked(self):
        with self.assertRaises(ValueError):
            resolve_project_path("config/.env.production", self.root)


if __name__ == "__main__":
    unittest.main()
