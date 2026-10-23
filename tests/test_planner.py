import unittest

from rarness.planner import (
    PROJECT_TYPES,
    Plan,
    Task,
    build_planning_prompt,
    parse_plan_json,
    plan_from_dict,
    topological_order,
    validate_plan,
)


def _plano(tarefas, tipo="site"):
    return Plan(project_name="Teste", project_type=tipo, requirements=["r1"], tasks=tarefas)


def _tarefa(id_, depends_on=None, files=None, title="Tarefa"):
    return Task(
        id=id_,
        title=title,
        files=["src/a.py"] if files is None else files,
        depends_on=depends_on or [],
    )


PLANO_DICT = {
    "project_name": "Loja",
    "project_type": "site",
    "requirements": ["Catálogo"],
    "tasks": [
        {"id": "t1", "title": "Criar página", "files": ["index.html"], "acceptance": ["abre"]},
        {"id": "t2", "title": "Estilo", "files": ["style.css"], "depends_on": ["t1"]},
    ],
    "questions": ["Qual cor?"],
}


class ValidatePlanTests(unittest.TestCase):
    def test_plano_valido_nao_tem_erros(self):
        plano = _plano([_tarefa("t1"), _tarefa("t2", ["t1"])])
        self.assertEqual(validate_plan(plano), [])

    def test_tipo_de_projeto_invalido(self):
        erros = validate_plan(_plano([_tarefa("t1")], tipo="foguete"))
        self.assertTrue(any("tipo de projeto" in e for e in erros))

    def test_todos_os_tipos_validos_passam(self):
        for tipo in PROJECT_TYPES:
            with self.subTest(tipo=tipo):
                self.assertEqual(validate_plan(_plano([_tarefa("t1")], tipo=tipo)), [])

    def test_id_vazio(self):
        erros = validate_plan(_plano([_tarefa("  ")]))
        self.assertTrue(any("id vazio" in e for e in erros))

    def test_id_duplicado(self):
        erros = validate_plan(_plano([_tarefa("t1"), _tarefa("t1")]))
        self.assertTrue(any("duplicado" in e for e in erros))

    def test_dependencia_inexistente(self):
        erros = validate_plan(_plano([_tarefa("t1", ["x"])]))
        self.assertTrue(any("inexistente" in e for e in erros))

    def test_ciclo(self):
        erros = validate_plan(_plano([_tarefa("a", ["b"]), _tarefa("b", ["a"])]))
        self.assertTrue(any("ciclo" in e for e in erros))

    def test_auto_dependencia_e_ciclo(self):
        erros = validate_plan(_plano([_tarefa("a", ["a"])]))
        self.assertTrue(any("ciclo" in e for e in erros))

    def test_caminho_vazio(self):
        erros = validate_plan(_plano([_tarefa("t1", files=[""])]))
        self.assertTrue(any("vazio" in e for e in erros))

    def test_caminho_absoluto(self):
        for caminho in ["/etc/passwd", "\\windows\\x", "C:/x.txt"]:
            with self.subTest(caminho=caminho):
                erros = validate_plan(_plano([_tarefa("t1", files=[caminho])]))
                self.assertTrue(any("absoluto" in e for e in erros))

    def test_caminho_com_traversal(self):
        for caminho in ["../fora.txt", "src/../../fora.txt", "src\\..\\x"]:
            with self.subTest(caminho=caminho):
                erros = validate_plan(_plano([_tarefa("t1", files=[caminho])]))
                self.assertTrue(any("'..'" in e for e in erros))

    def test_arquivo_env_bloqueado(self):
        for caminho in [".env", "config/.env.local", ".ENV.production"]:
            with self.subTest(caminho=caminho):
                erros = validate_plan(_plano([_tarefa("t1", files=[caminho])]))
                self.assertTrue(any(".env" in e for e in erros))

    def test_tarefa_sem_titulo(self):
        erros = validate_plan(_plano([_tarefa("t1", title="   ")]))
        self.assertTrue(any("sem título" in e for e in erros))


class TopologicalOrderTests(unittest.TestCase):
    def test_respeita_dependencias(self):
        plano = _plano([_tarefa("c", ["b"]), _tarefa("b", ["a"]), _tarefa("a")])
        self.assertEqual([t.id for t in topological_order(plano)], ["a", "b", "c"])

    def test_empate_mantem_ordem_original(self):
        plano = _plano([_tarefa("x"), _tarefa("y"), _tarefa("z", ["x"])])
        self.assertEqual([t.id for t in topological_order(plano)], ["x", "y", "z"])

    def test_empate_com_dependencia_tardia(self):
        plano = _plano([_tarefa("b", ["a"]), _tarefa("c"), _tarefa("a")])
        self.assertEqual([t.id for t in topological_order(plano)], ["c", "a", "b"])

    def test_ciclo_levanta_erro(self):
        plano = _plano([_tarefa("a", ["b"]), _tarefa("b", ["a"])])
        with self.assertRaises(ValueError):
            topological_order(plano)

    def test_dependencia_inexistente_levanta_erro(self):
        plano = _plano([_tarefa("a", ["zz"])])
        with self.assertRaises(ValueError):
            topological_order(plano)


class PlanFromDictTests(unittest.TestCase):
    def test_converte_dicionario_completo(self):
        plano = plan_from_dict(PLANO_DICT)
        self.assertEqual(plano.project_name, "Loja")
        self.assertEqual(plano.tasks[1].depends_on, ["t1"])
        self.assertEqual(plano.tasks[0].acceptance, ["abre"])
        self.assertEqual(plano.questions, ["Qual cor?"])

    def test_defaults_de_opcionais(self):
        plano = plan_from_dict(PLANO_DICT)
        self.assertEqual(plano.tasks[1].acceptance, [])
        self.assertEqual(plano.tasks[0].depends_on, [])

    def test_campo_faltando_no_plano(self):
        dados = dict(PLANO_DICT)
        del dados["project_name"]
        with self.assertRaisesRegex(ValueError, "project_name"):
            plan_from_dict(dados)

    def test_campo_faltando_na_tarefa(self):
        dados = {**PLANO_DICT, "tasks": [{"id": "t1", "title": "x"}]}
        with self.assertRaisesRegex(ValueError, "files"):
            plan_from_dict(dados)

    def test_tipo_errado(self):
        dados = {**PLANO_DICT, "requirements": "um texto"}
        with self.assertRaisesRegex(ValueError, "requirements"):
            plan_from_dict(dados)

    def test_tarefa_nao_e_objeto(self):
        dados = {**PLANO_DICT, "tasks": ["t1"]}
        with self.assertRaises(ValueError):
            plan_from_dict(dados)

    def test_raiz_nao_e_dicionario(self):
        with self.assertRaises(ValueError):
            plan_from_dict(["x"])


class ParsePlanJsonTests(unittest.TestCase):
    def setUp(self):
        import json

        self.texto = json.dumps(PLANO_DICT, ensure_ascii=False)

    def test_json_puro(self):
        self.assertEqual(parse_plan_json(self.texto).project_name, "Loja")

    def test_json_dentro_de_cerca(self):
        texto = f"Aqui está o plano:\n```json\n{self.texto}\n```\n"
        self.assertEqual(parse_plan_json(texto).project_name, "Loja")

    def test_cerca_sem_identificador_de_linguagem(self):
        texto = f"```\n{self.texto}\n```"
        self.assertEqual(parse_plan_json(texto).project_name, "Loja")

    def test_texto_invalido(self):
        with self.assertRaisesRegex(ValueError, "JSON"):
            parse_plan_json("isto não é json")

    def test_json_valido_mas_campo_faltando(self):
        with self.assertRaises(ValueError):
            parse_plan_json('{"project_name": "x"}')


class BuildPlanningPromptTests(unittest.TestCase):
    def test_prompt_contem_descricao_tipo_e_chaves(self):
        prompt = build_planning_prompt("Uma loja online", "site")
        self.assertIn("Uma loja online", prompt)
        self.assertIn("site", prompt)
        for chave in ("project_name", "project_type", "requirements", "tasks", "questions", "depends_on"):
            self.assertIn(chave, prompt)

    def test_tipo_invalido_levanta_erro(self):
        with self.assertRaisesRegex(ValueError, "tipo de projeto"):
            build_planning_prompt("Uma loja", "foguete")

    def test_descricao_vazia_levanta_erro(self):
        with self.assertRaises(ValueError):
            build_planning_prompt("   ", "site")


if __name__ == "__main__":
    unittest.main()
