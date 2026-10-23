import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rarness.state import (
    STATE_DIR_NAME,
    TASK_STATUSES,
    ProjectState,
    checkpoint,
    load_state,
    next_task,
    record_decision,
    save_state,
    set_task_status,
    state_path,
)


def estado_com_plano():
    return ProjectState(
        project_name="demo",
        project_type="python",
        plan={
            "tasks": [
                {"id": "A", "depends_on": []},
                {"id": "B", "depends_on": ["A"]},
                {"id": "C", "depends_on": ["B"]},
            ]
        },
    )


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_round_trip_cria_pasta_e_preserva_dados(self):
        estado = ProjectState(
            project_name="demo",
            project_type="python",
            plan={"tasks": [{"id": "A", "depends_on": []}]},
            task_status={"A": "done"},
            decisions=["usar sqlite"],
            files=["app.py"],
            test_results=[{"name": "suite", "passed": True}],
            next_steps=["criar API"],
        )

        caminho = save_state(self.root, estado)

        self.assertEqual(caminho, state_path(self.root))
        self.assertTrue((self.root / STATE_DIR_NAME).is_dir())
        self.assertEqual(load_state(self.root), estado)

    def test_load_sem_arquivo_retorna_none(self):
        self.assertIsNone(load_state(self.root))

    def test_json_corrompido_levanta_value_error(self):
        pasta = self.root / STATE_DIR_NAME
        pasta.mkdir()
        (pasta / "state.json").write_text("{nao é json", encoding="utf-8")

        with self.assertRaises(ValueError):
            load_state(self.root)

    def test_versao_desconhecida_levanta_value_error(self):
        pasta = self.root / STATE_DIR_NAME
        pasta.mkdir()
        dados = {"project_name": "x", "project_type": "y", "version": 99}
        (pasta / "state.json").write_text(json.dumps(dados), encoding="utf-8")

        with self.assertRaises(ValueError):
            load_state(self.root)

    def test_gravacao_atomica_nao_deixa_temporarios(self):
        save_state(self.root, estado_com_plano())

        pasta = self.root / STATE_DIR_NAME
        self.assertEqual(sorted(os.listdir(pasta)), ["state.json"])

    def test_falha_no_replace_remove_temporario_e_preserva_original(self):
        save_state(self.root, ProjectState(project_name="antigo", project_type="python"))
        pasta = self.root / STATE_DIR_NAME

        with mock.patch("rarness.state.os.replace", side_effect=OSError("falha simulada")):
            with self.assertRaises(OSError):
                save_state(self.root, ProjectState(project_name="novo", project_type="python"))

        self.assertEqual(sorted(os.listdir(pasta)), ["state.json"])
        self.assertEqual(load_state(self.root).project_name, "antigo")

    def test_recusa_chave_secreta_em_plan(self):
        estado = estado_com_plano()
        estado.plan["config"] = {"OPENAI_API_KEY": "valor-secreto"}

        with self.assertRaises(ValueError):
            save_state(self.root, estado)
        self.assertFalse(state_path(self.root).exists())

    def test_recusa_chave_secreta_em_test_results_sem_diferenciar_caixa(self):
        estado = ProjectState(
            project_name="demo",
            project_type="python",
            test_results=[{"meta": {"Auth_Token": "x"}}],
        )

        with self.assertRaises(ValueError):
            save_state(self.root, estado)

        for nome in ("db_password", "client_secret"):
            with self.subTest(nome=nome):
                estado = ProjectState(
                    project_name="demo",
                    project_type="python",
                    test_results=[{nome: "x"}],
                )
                with self.assertRaises(ValueError):
                    save_state(self.root, estado)

    def test_recusa_status_invalido_ao_gravar(self):
        estado = estado_com_plano()
        estado.task_status["A"] = "concluido"

        with self.assertRaises(ValueError):
            save_state(self.root, estado)

    def test_set_task_status_valida_status_e_id(self):
        estado = estado_com_plano()

        with self.assertRaises(ValueError):
            set_task_status(estado, "A", "concluido")
        with self.assertRaises(ValueError):
            set_task_status(estado, "Z", "done")

        set_task_status(estado, "A", "done")
        self.assertEqual(estado.task_status["A"], "done")
        for status in TASK_STATUSES:
            set_task_status(estado, "B", status)
            self.assertEqual(estado.task_status["B"], status)

    def test_set_task_status_aceita_qualquer_id_sem_plano_de_tarefas(self):
        estado = ProjectState(project_name="demo", project_type="python")

        set_task_status(estado, "livre", "in_progress")

        self.assertEqual(estado.task_status["livre"], "in_progress")

    def test_next_task_respeita_dependencias(self):
        estado = estado_com_plano()
        self.assertEqual(next_task(estado), "A")

        set_task_status(estado, "A", "in_progress")
        self.assertIsNone(next_task(estado))

        set_task_status(estado, "A", "done")
        self.assertEqual(next_task(estado), "B")

        set_task_status(estado, "B", "done")
        self.assertEqual(next_task(estado), "C")

        set_task_status(estado, "C", "done")
        self.assertIsNone(next_task(estado))

    def test_next_task_aceita_failed_e_ignora_blocked(self):
        estado = estado_com_plano()
        set_task_status(estado, "A", "done")
        set_task_status(estado, "B", "failed")

        self.assertEqual(next_task(estado), "B")

        set_task_status(estado, "B", "blocked")
        self.assertIsNone(next_task(estado))

    def test_record_decision_acrescenta_e_recusa_vazio(self):
        estado = ProjectState(project_name="demo", project_type="python")

        record_decision(estado, "  usar stdlib  ")

        self.assertEqual(estado.decisions, ["usar stdlib"])
        with self.assertRaises(ValueError):
            record_decision(estado, "   ")

    def test_checkpoint_sanitiza_rotulo(self):
        save_state(self.root, estado_com_plano())

        destino = checkpoint(self.root, "fase 1/ok!")

        self.assertTrue(destino.is_file())
        self.assertEqual(destino.parent, self.root / STATE_DIR_NAME / "checkpoints")
        self.assertRegex(destino.name, re.compile(r"^\d{8}-\d{6}-\d{6}-fase-1-ok\.json$"))
        self.assertEqual(json.loads(destino.read_text(encoding="utf-8"))["project_name"], "demo")

    def test_checkpoint_sem_estado_levanta_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            checkpoint(self.root, "qualquer")


if __name__ == "__main__":
    unittest.main()
