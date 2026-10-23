import json
import tempfile
import unittest
import zipfile
from dataclasses import dataclass
from pathlib import Path

from rarness.orchestrator import build_project, create_project, package_project, parse_files_json
from rarness.state import load_state


PLANO = {
    "project_name": "Demo",
    "project_type": "ferramenta",
    "requirements": ["soma"],
    "tasks": [
        {"id": "t1", "title": "Base", "files": ["app.py"], "depends_on": [], "acceptance": ["roda"]},
        {"id": "t2", "title": "Testes", "files": ["tests/test_app.py"], "depends_on": ["t1"], "acceptance": ["passa"]},
    ],
    "questions": [],
}


def arquivos(**conteudo):
    return json.dumps({"arquivos": conteudo})


@dataclass
class Resultado:
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""


def sequencia(*respostas):
    fila = list(respostas)

    def ask(prompt):
        return fila.pop(0)
    return ask


class OrquestradorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_create_project_saves_pending_tasks(self):
        state = create_project(self.root, "Uma ferramenta de soma", "ferramenta", sequencia(json.dumps(PLANO)))
        self.assertEqual(state.task_status, {"t1": "pending", "t2": "pending"})
        self.assertEqual(load_state(self.root).project_name, "Demo")

    def test_create_project_rejects_invalid_plan(self):
        plano = json.loads(json.dumps(PLANO))
        plano["tasks"][1]["depends_on"] = ["inexistente"]
        with self.assertRaises(ValueError):
            create_project(self.root, "pedido", "ferramenta", sequencia(json.dumps(plano)))
        self.assertIsNone(load_state(self.root))

    def test_build_generates_all_tasks_in_order(self):
        state = create_project(self.root, "pedido", "ferramenta", sequencia(json.dumps(PLANO)))
        ask = sequencia(arquivos(**{"app.py": "print(1)"}), arquivos(**{"tests/test_app.py": "assert True"}))
        state = build_project(self.root, state, ask, lambda _: True)
        self.assertEqual(state.task_status, {"t1": "done", "t2": "done"})
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "print(1)")
        self.assertEqual(load_state(self.root).task_status["t2"], "done")

    def test_refused_write_leaves_task_pending(self):
        state = create_project(self.root, "pedido", "ferramenta", sequencia(json.dumps(PLANO)))
        state = build_project(self.root, state, sequencia(arquivos(**{"app.py": "x"})), lambda _: False)
        self.assertEqual(state.task_status["t1"], "pending")
        self.assertFalse((self.root / "app.py").exists())

    def test_model_cannot_write_files_outside_task(self):
        state = create_project(self.root, "pedido", "ferramenta", sequencia(json.dumps(PLANO)))
        ask = sequencia(arquivos(**{"outro.py": "x"}))
        state = build_project(self.root, state, ask, lambda _: True)
        self.assertEqual(state.task_status["t1"], "failed")
        self.assertFalse((self.root / "outro.py").exists())

    def test_failed_tests_are_repaired_then_pass(self):
        state = create_project(self.root, "pedido", "ferramenta", sequencia(json.dumps(PLANO)))
        ask = sequencia(
            arquivos(**{"app.py": "versao 1"}),
            arquivos(**{"app.py": "versao 2"}),
            arquivos(**{"tests/test_app.py": "assert True"}),
        )
        chamadas = []

        def run_tests():
            chamadas.append(1)
            if len(chamadas) == 1:
                return Resultado(1, stdout="FAIL: test_soma (app.Testes)\n")
            return Resultado(0)

        state = build_project(self.root, state, ask, lambda _: True, run_tests=run_tests)
        self.assertEqual(state.task_status, {"t1": "done", "t2": "done"})
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "versao 2")
        self.assertTrue(all(item["aprovado"] for item in state.test_results))

    def test_unrepaired_failure_marks_task_failed(self):
        state = create_project(self.root, "pedido", "ferramenta", sequencia(json.dumps(PLANO)))
        ask = sequencia(arquivos(**{"app.py": "x"}), "sem json")
        state = build_project(self.root, state, ask, lambda _: True,
                              run_tests=lambda: Resultado(1, stdout="FAIL: test_a (m.C)\n"))
        self.assertEqual(state.task_status["t1"], "failed")
        self.assertFalse(state.test_results[0]["aprovado"])

    def test_parse_files_json_accepts_fence(self):
        texto = "```json\n" + arquivos(**{"a.py": "x"}) + "\n```"
        self.assertEqual(parse_files_json(texto), {"a.py": "x"})

    def test_parse_files_json_rejects_bad_shapes(self):
        for texto in ["não é json", json.dumps({"outro": {}}), json.dumps({"arquivos": {}})]:
            with self.subTest(texto=texto), self.assertRaises(ValueError):
                parse_files_json(texto)

    def test_package_project_writes_zip_without_state(self):
        state = create_project(self.root, "pedido", "ferramenta", sequencia(json.dumps(PLANO)))
        (self.root / ".env").write_text("SEGREDO=1", encoding="utf-8")
        (self.root / "app.py").write_text("x", encoding="utf-8")
        destino = package_project(self.root, state)
        self.assertEqual(destino, self.root / ".rarness" / "exportacoes" / "Demo.zip")
        with zipfile.ZipFile(destino) as zip_file:
            nomes = zip_file.namelist()
        self.assertIn("app.py", nomes)
        self.assertNotIn(".env", nomes)


if __name__ == "__main__":
    unittest.main()
