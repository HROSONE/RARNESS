import json
import tempfile
import unittest
from pathlib import Path

import opencode
from rarness.code_intel import (
    apply_multi_edit,
    apply_surgical_edit,
    build_repo_map,
    find_symbol,
    format_numbered_lines,
    patch_lines,
    search_codebase,
    unified_diff_preview,
    verify_syntax,
)
from rarness.git_ops import restore_latest_backup
from rarness.intent_router import (
    build_stacktrace_context,
    detect_thinking_level,
    extract_stacktrace_targets,
    infer_intent_and_mode,
    infer_project_type,
)
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
from rarness.session import (
    SessionTracker,
    is_safe_readonly_bash,
    list_sessions,
    load_session,
    safe_web_fetch,
    save_session,
)


class _FakeFunction:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, tc_id: str, name: str, args_dict: dict):
        self.id = tc_id
        self.function = _FakeFunction(name, json.dumps(args_dict))


class IntentAndModeRouterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_question_infers_plan_readonly_mode(self):
        profile = infer_intent_and_mode("Onde fica a função de execução de comandos?", configured_mode="ask")
        self.assertEqual(profile.intent, "explanation")
        self.assertEqual(profile.effective_mode, "plan")
        self.assertFalse(profile.mutation_allowed)

    def test_planning_request_infers_plan_mode_and_requires_todo(self):
        profile = infer_intent_and_mode("Planeje a arquitetura completa de um sistema de cache", configured_mode="ask")
        self.assertEqual(profile.intent, "planning")
        self.assertEqual(profile.effective_mode, "plan")
        self.assertTrue(profile.requires_todo)

    def test_review_request_infers_review_readonly_mode(self):
        profile = infer_intent_and_mode("Faça um code review procurando vulnerabilidades", configured_mode="ask")
        self.assertEqual(profile.intent, "review")
        self.assertEqual(profile.effective_mode, "plan")
        self.assertFalse(profile.mutation_allowed)

    def test_ultrathink_triggers_max_budget_and_argon_model(self):
        profile = infer_intent_and_mode(
            "ultrathink: faça a engenharia reversa e implemente o novo motor",
            configured_mode="auto-edit",
            auto_route_model=True,
        )
        self.assertEqual(profile.thinking_level, "ultrathink")
        self.assertEqual(profile.recommended_model, "gemini-4-argon")
        self.assertEqual(profile.max_output_tokens, 8192)
        self.assertEqual(profile.effective_mode, "auto-edit")
        self.assertTrue(profile.mutation_allowed)

    def test_stacktrace_extraction_and_auto_context_injection(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text(
            "def f():\n    x = 1\n    return 1 / 0\n    y = 2\n",
            encoding="utf-8",
        )
        prompt = 'Corrija este erro:\nTraceback (most recent call last):\n  File "src/app.py", line 3, in f\nZeroDivisionError'
        targets = extract_stacktrace_targets(prompt, root=self.root)
        self.assertEqual(targets, [("src/app.py", 3)])
        ctx = build_stacktrace_context(targets, self.root, window=1)
        self.assertIn("src/app.py", ctx)
        self.assertIn("return 1 / 0", ctx)

    def test_infer_project_type_and_auto_plano_command(self):
        self.assertEqual(infer_project_type("um jogo 2D de nave no canvas"), "jogo")
        self.assertEqual(infer_project_type("um app android em kotlin com jetpack compose"), "app_android")
        self.assertEqual(infer_project_type("uma api rest em fastapi"), "api")
        self.assertEqual(infer_project_type("um site landing page moderno"), "site")

        plano_json = json.dumps({
            "project_name": "SiteDemo",
            "project_type": "site",
            "requirements": ["home"],
            "tasks": [{"id": "t1", "title": "Index", "files": ["index.html"], "depends_on": [], "acceptance": ["ok"]}],
            "questions": [],
        })
        handled = opencode.handle_project_command("/plano criar um site de portfólio", self.root, lambda _: plano_json)
        self.assertTrue(handled)
        self.assertTrue((self.root / ".rarness" / "state.json").is_file())


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

    def test_fuzzy_indentation_surgical_edit_preserves_file_indent(self):
        conteudo = "class Servico:\n    def calcular(self):\n        return 10\n"
        # Modelo enviou sem os 4 espaços iniciais da classe:
        novo, count = apply_surgical_edit(
            conteudo,
            "def calcular(self):\n    return 10",
            "def calcular(self):\n    return 42",
        )
        self.assertEqual(count, 1)
        self.assertIn("    def calcular(self):\n        return 42\n", novo)

    def test_patch_lines_replaces_target_window(self):
        original = "a = 1\nb = 2\nc = 3\nd = 4\n"
        patched = patch_lines(original, 2, 3, "b = 20\nc = 30")
        self.assertEqual(patched, "a = 1\nb = 20\nc = 30\nd = 4\n")

    def test_verify_syntax_for_python_json_and_delimiters(self):
        ok_py, _ = verify_syntax("a.py", "def x():\n    return 1\n")
        self.assertTrue(ok_py)
        bad_py, msg_py = verify_syntax("a.py", "def x(\n")
        self.assertFalse(bad_py)
        self.assertIn("linha", msg_py)

        ok_js, _ = verify_syntax("app.js", "function a() { const s = '{'; return [1, 2]; }")
        self.assertTrue(ok_js)
        bad_js, msg_js = verify_syntax("app.js", "function a() { return [1, 2); }")
        self.assertFalse(bad_js)
        self.assertIn("Delimitador", msg_js)

    def test_find_symbol_separates_definitions_and_references(self):
        (self.root / "engine.py").write_text(
            "def executar_ciclo(x):\n    return x + 1\n\nres = executar_ciclo(5)\n",
            encoding="utf-8",
        )
        saida = find_symbol(self.root, "executar_ciclo")
        self.assertIn("[DEFINIÇÕES]", saida)
        self.assertIn("engine.py:1: def executar_ciclo(x):", saida)
        self.assertIn("[REFERÊNCIAS]", saida)
        self.assertIn("engine.py:4: res = executar_ciclo(5)", saida)

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
        self.assertIn("gemini-3.8-flash", md)

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


class ToolExecutionModesAndSessionGuardsTests(unittest.TestCase):
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
            {"command": "echo oi > x.txt"},
            self.root,
            permission_mode="plan",
        )
        self.assertIn("Modo 'plan' ativo", bloqueio_bash)

    def test_read_before_edit_and_stale_file_guard(self):
        alvo = self.root / "protegido.py"
        alvo.write_text("v = 1\n", encoding="utf-8")
        tracker = SessionTracker(enforce_read_before_edit=True)

        # Tentar editar antes de ler -> bloqueado pela guarda Read-Before-Edit
        res_sem_ler = opencode.execute_tool(
            "edit_file",
            {"path": "protegido.py", "old_text": "v = 1", "new_text": "v = 2"},
            self.root,
            permission_mode="auto-edit",
            session=tracker,
        )
        self.assertIn("Read-Before-Edit", res_sem_ler)
        self.assertEqual(alvo.read_text(encoding="utf-8"), "v = 1\n")

        # Ler o arquivo (usando alias canônico 'Read' do Claude Code)
        opencode.execute_tool("Read", {"path": "protegido.py"}, self.root, session=tracker)

        # Agora a edição passa e retorna o selo de sintaxe AST validada
        res_apos_ler = opencode.execute_tool(
            "Edit",
            {"path": "protegido.py", "old_text": "v = 1", "new_text": "v = 2"},
            self.root,
            permission_mode="auto-edit",
            session=tracker,
        )
        self.assertIn("Sintaxe Python OK", res_apos_ler)
        self.assertEqual(alvo.read_text(encoding="utf-8"), "v = 2\n")

        # Modificação externa no disco -> bloqueado pelo Stale File Guard
        alvo.write_text("v = 999\n", encoding="utf-8")
        res_conflito = opencode.execute_tool(
            "Edit",
            {"path": "protegido.py", "old_text": "v = 999", "new_text": "v = 3"},
            self.root,
            permission_mode="auto-edit",
            session=tracker,
        )
        self.assertIn("Stale File Guard", res_conflito)

    def test_parallel_readonly_batch_execution(self):
        (self.root / "f1.py").write_text("def func_um():\n    return 1\n", encoding="utf-8")
        (self.root / "f2.py").write_text("def func_dois():\n    return 2\n", encoding="utf-8")
        calls = [
            _FakeToolCall("c1", "Read", {"path": "f1.py"}),
            _FakeToolCall("c2", "Read", {"path": "f2.py"}),
            _FakeToolCall("c3", "FindSymbol", {"symbol": "func_um"}),
        ]
        tracker = SessionTracker(enforce_read_before_edit=True)
        msgs = opencode.execute_tool_calls_batch(calls, self.root, permission_mode="plan", session=tracker)
        self.assertEqual(len(msgs), 3)
        self.assertIn("func_um", msgs[0]["content"])
        self.assertIn("func_dois", msgs[1]["content"])
        self.assertIn("[DEFINIÇÕES]", msgs[2]["content"])

    def test_pre_tool_hook_can_block_operation(self):
        pasta = self.root / ".rarness"
        pasta.mkdir()
        (pasta / "hooks.json").write_text(
            json.dumps({"PreToolUse": {"write_file": "python3 -c 'import sys; sys.exit(1)'"}}),
            encoding="utf-8",
        )
        res = opencode.execute_tool(
            "write_file",
            {"path": "bloqueado.py", "content": "x = 1\n"},
            self.root,
            permission_mode="auto-edit",
        )
        self.assertIn("PreToolUse", res)
        self.assertFalse((self.root / "bloqueado.py").exists())

    def test_session_persistence_and_redaction(self):
        (self.root / ".env").write_text("GEMINI_API_KEY=chave-super-secreta-999\n", encoding="utf-8")
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "teste chave-super-secreta-999"},
        ]
        save_session(self.root, msgs, session_id="dev1", redact_fn=opencode.redact)
        self.assertIn("dev1", list_sessions(self.root))
        carregadas = load_session(self.root, "dev1")
        self.assertIsNotNone(carregadas)
        self.assertIn("[SEGREDO REMOVIDO]", carregadas[1]["content"])

    def test_safe_readonly_bash_classifier_and_ssrf_guard(self):
        self.assertTrue(is_safe_readonly_bash("python3 -m unittest discover -s tests -v"))
        self.assertTrue(is_safe_readonly_bash("git status --short"))
        self.assertFalse(is_safe_readonly_bash("git status && rm -rf x"))
        self.assertFalse(is_safe_readonly_bash("python3 -c 'import os'"))

        for bad_url in ("http://localhost:8000", "http://127.0.0.1/admin", "file:///etc/passwd"):
            with self.subTest(bad_url=bad_url), self.assertRaises(ValueError):
                safe_web_fetch(bad_url)

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

    def test_multi_edit_patch_lines_and_todo_tools_via_execute_tool(self):
        alvo = self.root / "mod.py"
        alvo.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")

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
        self.assertEqual(alvo.read_text(encoding="utf-8"), "a = 10\nb = 20\nc = 3\n")

        res_patch = opencode.execute_tool(
            "patch_lines",
            {"path": "mod.py", "start_line": 3, "end_line": 3, "new_text": "c = 30"},
            self.root,
            permission_mode="auto-edit",
        )
        self.assertIn("Sintaxe Python OK", res_patch)
        self.assertEqual(alvo.read_text(encoding="utf-8"), "a = 10\nb = 20\nc = 30\n")

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
        res = opencode.execute_tool("subagent", {"prompt": "Onde fica iniciar?", "role": "reviewer"}, self.root)
        self.assertIn("core.py", res)
        self.assertIn("def iniciar()", res)

    def test_legacy_gemini_models_are_strictly_blocked(self):
        for ok_model in ("gemini-3.8-flash", "gemini-4-argon"):
            self.assertEqual(opencode.validate_model_name(ok_model, "gemini"), ok_model)
        for bad_model in ("gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro", "gemini-1.5-pro", "gemini-2"):
            with self.subTest(bad_model=bad_model), self.assertRaises(ValueError):
                opencode.validate_model_name(bad_model, "gemini")



    def test_free_coding_providers_and_catalog_are_supported(self):
        self.assertIn("groq", opencode.FREE_CODING_CATALOG)
        self.assertIn("cerebras", opencode.FREE_CODING_CATALOG)
        self.assertIn("openrouter", opencode.FREE_CODING_CATALOG)
        self.assertIn("openai/gpt-oss-120b", opencode.FREE_CODING_CATALOG["groq"])
        self.assertIn("gpt-oss-120b", opencode.FREE_CODING_CATALOG["cerebras"])
        self.assertIn("cohere/north-mini-code:free", opencode.FREE_CODING_CATALOG["openrouter"])

        for prov, mod in [
            ("groq", "openai/gpt-oss-120b"),
            ("groq", "qwen/qwen3.8-27b"),
            ("cerebras", "gpt-oss-120b"),
            ("openrouter", "cohere/north-mini-code:free"),
            ("openrouter", "poolside/laguna-s-2.1:free"),
        ]:
            self.assertEqual(opencode.validate_model_name(mod, prov), mod)


if __name__ == "__main__":
    unittest.main()
