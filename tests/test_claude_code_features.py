import tempfile
import unittest
from pathlib import Path

import opencode
from rarness.code_intel import (
    apply_multi_edit,
    apply_surgical_edit,
    build_repo_map,
    format_numbered_lines,
    search_codebase,
    unified_diff_preview,
)
from rarness.git_ops import restore_latest_backup
from rarness.memory import (
    append_memory_note,
    compact_history,
    expand_at_mentions,
    format_todos,
    generate_claude_md,
    load_claude_memories,
    load_todos,
    save_todos,
)


class CodeIntelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_format_numbered_lines_window(self):
        texto = "linha 1\nlinha 2\nlinha 3\nlinha 4"
        res = format_numbered_lines(texto, start_line=2, end_line=3)
        self.assertIn("[Linhas 2-3 de 4]", res)
        self.assertIn("   2 | linha 2", res)
        self.assertIn("   3 | linha 3", res)
        self.assertNotIn("linha 1", res)

    def test_surgical_edit_rejects_ambiguous_match_without_replace_all(self):
        conteudo = "x = 1\ny = 2\nx = 1\n"
        with self.assertRaisesRegex(ValueError, "ambíguo"):
            apply_surgical_edit(conteudo, "x = 1", "x = 99", replace_all=False)

    def test_surgical_edit_allows_replace_all(self):
        conteudo = "x = 1\ny = 2\nx = 1\n"
        novo, count = apply_surgical_edit(conteudo, "x = 1", "x = 99", replace_all=True)
        self.assertEqual(count, 2)
        self.assertEqual(novo, "x = 99\ny = 2\nx = 99\n")

    def test_multi_edit_is_atomic_on_error(self):
        conteudo = "def a():\n    return 1\n\ndef b():\n    return 2\n"
        novo, count = apply_multi_edit(
            conteudo,
            [
                {"old_text": "return 1", "new_text": "return 10"},
                {"old_text": "return 2", "new_text": "return 20"},
            ],
        )
        self.assertEqual(count, 2)
        self.assertIn("return 10", novo)
        self.assertIn("return 20", novo)

        with self.assertRaises(ValueError):
            apply_multi_edit(
                conteudo,
                [
                    {"old_text": "return 1", "new_text": "return 10"},
                    {"old_text": "nao_existe", "new_text": "x"},
                ],
            )

    def test_unified_diff_preview_contains_headers(self):
        diff = unified_diff_preview("a\nb\n", "a\nc\n", "src/app.py")
        self.assertIn("--- a/src/app.py", diff)
        self.assertIn("+++ b/src/app.py", diff)
        self.assertIn("-b", diff)
        self.assertIn("+c", diff)

    def test_search_codebase_supports_regex_glob_and_context(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "calc.py").write_text(
            "import math\ndef calcular_soma(a, b):\n    return a + b\n",
            encoding="utf-8",
        )
        (self.root / "README.md").write_text("calcular_soma doc\n", encoding="utf-8")

        res = search_codebase(
            self.root,
            r"def\s+calcular_\w+",
            regex=True,
            file_pattern="*.py",
            context_lines=1,
        )
        self.assertIn("src/calc.py:2:", res)
        self.assertIn(">    2 | def calcular_soma(a, b):", res)
        self.assertNotIn("README.md", res)

    def test_build_repo_map_extracts_python_classes_and_functions(self):
        (self.root / "mod.py").write_text(
            "class Motor:\n    def ligar(self):\n        pass\n\ndef auxiliar(x, y):\n    return x\n",
            encoding="utf-8",
        )
        mapa = build_repo_map(self.root)
        self.assertIn("mod.py:", mapa)
        self.assertIn("class Motor [ligar]", mapa)
        self.assertIn("def auxiliar(x, y)", mapa)


class MemoryAndContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_append_and_load_claude_memories(self):
        append_memory_note(self.root, "Usar type hints em todas as funções")
        mem = load_claude_memories(self.root)
        self.assertIn("Usar type hints em todas as funções", mem)

    def test_append_memory_rejects_secrets(self):
        with self.assertRaises(ValueError):
            append_memory_note(self.root, "GEMINI_API_KEY=segredo123")

    def test_generate_claude_md_inspects_project(self):
        (self.root / "requirements.txt").write_text("pytest\n", encoding="utf-8")
        (self.root / "app.py").write_text("def main():\n    pass\n", encoding="utf-8")
        md = generate_claude_md(self.root)
        self.assertIn("Python", md)
        self.assertIn("app.py", md)

    def test_expand_at_mentions_injects_numbered_lines_and_blocks_env(self):
        (self.root / "util.py").write_text("a = 10\nb = 20\nc = 30\n", encoding="utf-8")
        (self.root / ".env").write_text("SEGREDO=123\n", encoding="utf-8")

        expandido, arquivos = expand_at_mentions("Veja @util.py:2-3 e ignore @.env", self.root)
        self.assertEqual(arquivos, ["util.py"])
        self.assertIn("   2 | b = 20", expandido)
        self.assertNotIn("SEGREDO", expandido)

    def test_compact_history_preserves_system_and_recent_turns(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "pedido 1"},
            {"role": "assistant", "content": "resposta 1"},
            {"role": "user", "content": "pedido 2"},
            {"role": "assistant", "content": "resposta 2"},
            {"role": "user", "content": "pedido 3"},
            {"role": "assistant", "content": "resposta 3"},
        ]
        compactado = compact_history(msgs, keep_last_user_turns=1, focus="arquitetura")
        self.assertEqual(compactado[0]["role"], "system")
        self.assertIn("RESUMO DE CONTEXTO COMPACTADO", compactado[1]["content"])
        self.assertEqual(compactado[-2]["content"], "pedido 3")

    def test_todo_write_and_read_roundtrip(self):
        itens = save_todos(
            self.root,
            [
                {"id": "1", "content": "Analisar código", "status": "completed", "priority": "high"},
                {"id": "2", "content": "Implementar feature", "status": "in_progress", "priority": "medium"},
            ],
        )
        self.assertEqual(len(itens), 2)
        lidos = load_todos(self.root)
        texto = format_todos(lidos)
        self.assertIn("[x] (1) [high] Analisar código", texto)
        self.assertIn("[/] (2) [medium] Implementar feature", texto)
        self.assertIn("Progresso: 1/2 concluídas", texto)


class ToolExecutionAndModesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_plan_mode_blocks_mutations_and_allows_reads(self):
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        leitura = opencode.execute_tool(
            "read_file",
            {"path": "a.py", "line_numbers": True},
            self.root,
            permission_mode="plan",
        )
        self.assertIn("   1 | x = 1", leitura)

        bloqueio_write = opencode.execute_tool(
            "write_file",
            {"path": "b.py", "content": "y = 2"},
            self.root,
            permission_mode="plan",
        )
        self.assertIn("Modo 'plan' ativo", bloqueio_write)
        self.assertFalse((self.root / "b.py").exists())

        bloqueio_bash = opencode.execute_tool(
            "bash",
            {"command": "echo oi"},
            self.root,
            permission_mode="plan",
        )
        self.assertIn("Modo 'plan' ativo", bloqueio_bash)

    def test_auto_edit_mode_edits_without_prompt_and_undo_restores_backup(self):
        alvo = self.root / "app.py"
        alvo.write_text("valor = 1\n", encoding="utf-8")

        res = opencode.execute_tool(
            "edit_file",
            {"path": "app.py", "old_text": "valor = 1", "new_text": "valor = 42"},
            self.root,
            permission_mode="auto-edit",
        )
        self.assertIn("Arquivo atualizado", res)
        self.assertEqual(alvo.read_text(encoding="utf-8"), "valor = 42\n")

        restaurados = restore_latest_backup(self.root)
        self.assertEqual(restaurados, ["app.py"])
        self.assertEqual(alvo.read_text(encoding="utf-8"), "valor = 1\n")

    def test_multi_edit_and_todo_tools_via_execute_tool(self):
        alvo = self.root / "mod.py"
        alvo.write_text("a = 1\nb = 2\n", encoding="utf-8")

        res = opencode.execute_tool(
            "multi_edit",
            {
                "path": "mod.py",
                "edits": [
                    {"old_text": "a = 1", "new_text": "a = 10"},
                    {"old_text": "b = 2", "new_text": "b = 20"},
                ],
            },
            self.root,
            permission_mode="auto-edit",
        )
        self.assertIn("multi_edit", res)
        self.assertEqual(alvo.read_text(encoding="utf-8"), "a = 10\nb = 20\n")

        todo_res = opencode.execute_tool(
            "todo_write",
            {"todos": [{"id": "t1", "content": "Testar multi_edit", "status": "completed"}]},
            self.root,
            permission_mode="auto-edit",
        )
        self.assertIn("Progresso: 1/1", todo_res)

    def test_destructive_bash_is_blocked(self):
        res = opencode.execute_tool("bash", {"command": "rm -rf /"}, self.root)
        self.assertIn("recusado", res)

    def test_subagent_tool_returns_repo_overview_by_default(self):
        (self.root / "core.py").write_text("def iniciar():\n    return True\n", encoding="utf-8")
        res = opencode.execute_tool("subagent", {"prompt": "Onde fica iniciar?"}, self.root)
        self.assertIn("core.py", res)
        self.assertIn("def iniciar()", res)



    def test_legacy_gemini_models_are_strictly_blocked(self):
        for ok_model in ("gemini-3.8-flash", "gemini-4-argon"):
            self.assertEqual(opencode.validate_model_name(ok_model, "gemini"), ok_model)
        for bad_model in ("gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro", "gemini-1.5-pro", "gemini-2"):
            with self.subTest(bad_model=bad_model), self.assertRaises(ValueError):
                opencode.validate_model_name(bad_model, "gemini")


if __name__ == "__main__":
    unittest.main()
