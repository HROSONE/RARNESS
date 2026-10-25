"""Memória persistente (CLAUDE.md), menções @arquivo, compactação de histórico e TodoWrite."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from rarness.code_intel import build_repo_map, format_numbered_lines
from rarness.project_fs import list_project_files
from rarness.state import STATE_DIR_NAME
from rarness_security import SENSITIVE_NAMES, resolve_project_path

MEMORY_FILES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".rarness/memory.md")
TODOS_FILE_NAME = "todos.json"
TODO_STATUSES = ("pending", "in_progress", "completed")
TODO_PRIORITIES = ("high", "medium", "low")

AT_MENTION_PATTERN = re.compile(r"(?<![\w.+-])@([A-Za-z0-9_./-]+(?::\d+(?:-\d+)?)?)")


@dataclass
class TodoItem:
    id: str
    content: str
    status: str = "pending"
    priority: str = "medium"


def load_claude_memories(root: Path, max_chars_per_file: int = 20000) -> str:
    """Carrega instruções dos arquivos de memória do projeto (CLAUDE.md, CLAUDE.local.md, AGENTS.md)."""
    raiz = root.resolve()
    blocos: list[str] = []
    for rel in MEMORY_FILES:
        try:
            caminho = resolve_project_path(rel, raiz)
        except ValueError:
            continue
        if caminho.is_file() and not caminho.is_symlink():
            try:
                texto = caminho.read_text(encoding="utf-8", errors="replace").strip()
                if texto:
                    blocos.append(f"[INSTRUÇÕES DE {rel}]\n{texto[:max_chars_per_file]}")
            except OSError:
                continue
    return "\n\n".join(blocos)


def append_memory_note(root: Path, note: str, target: str = "CLAUDE.md") -> Path:
    """Adiciona uma nota ou regra persistente ao CLAUDE.md (estilo atalho '#' do Claude Code)."""
    limpa = note.strip()
    if not limpa:
        raise ValueError("A nota de memória não pode ser vazia.")
    if any(p in limpa.upper() for p in ("API_KEY=", "SECRET=", "PASSWORD=", "TOKEN=")):
        raise ValueError("Recusado: nunca grave chaves ou segredos no CLAUDE.md.")

    caminho = resolve_project_path(target, root.resolve())
    caminho.parent.mkdir(parents=True, exist_ok=True)
    existente = caminho.read_text(encoding="utf-8") if caminho.is_file() else "# Instruções do Projeto\n"
    if not existente.endswith("\n"):
        existente += "\n"
    linha = limpa if limpa.startswith("- ") else f"- {limpa}"
    caminho.write_text(existente + linha + "\n", encoding="utf-8")
    return caminho


def generate_claude_md(root: Path) -> str:
    """Inspeciona o projeto e gera um conteúdo estruturado para o CLAUDE.md (/init)."""
    raiz = root.resolve()
    arquivos = list_project_files(raiz)
    comandos_teste: list[str] = []
    stack: list[str] = []

    if "requirements.txt" in arquivos or "pyproject.toml" in arquivos or any(f.endswith(".py") for f in arquivos):
        stack.append("Python")
        if any(f.startswith("tests/") for f in arquivos):
            comandos_teste.append("python -m unittest discover -s tests -v")
    if "package.json" in arquivos:
        stack.append("Node.js / JavaScript")
        comandos_teste.append("npm test")
    if "Cargo.toml" in arquivos:
        stack.append("Rust")
        comandos_teste.append("cargo test")
    if "go.mod" in arquivos:
        stack.append("Go")
        comandos_teste.append("go test ./...")

    mapa = build_repo_map(raiz, max_files=30)
    stack_str = ", ".join(stack) if stack else "Geral"
    test_str = "\n".join(f"- `{cmd}`" for cmd in comandos_teste) or "- Defina o comando de testes do projeto"

    return (
        f"# CLAUDE.md — Guia do Projeto ({raiz.name})\n\n"
        "## Visão Geral e Stack\n"
        f"- Tecnologias detectadas: {stack_str}\n"
        f"- Total de arquivos rastreados: {len(arquivos)}\n\n"
        "## Comandos de Verificação e Testes\n"
        f"{test_str}\n\n"
        "## Diretrizes de Engenharia\n"
        "- Responda em português brasileiro, salvo pedido em contrário.\n"
        "- Inspecione os arquivos (`read_file` / `search_text` / `repo_map`) antes de editá-los.\n"
        "- Prefira edições cirúrgicas pequenas (`edit_file` / `multi_edit`) e explique o que mudou.\n"
        "- Execute os testes relevantes após modificar código.\n"
        "- Nunca exiba, copie ou envie chaves de API, tokens ou conteúdo de `.env`.\n\n"
        "## Estrutura Principal do Repositório\n"
        "```text\n"
        f"{mapa[:3000]}\n"
        "```\n"
    )


def expand_at_mentions(
    user_text: str,
    root: Path,
    max_bytes_per_file: int = 40_000,
) -> tuple[str, list[str]]:
    """Expande menções @arquivo[:inicio-fim] anexando o conteúdo seguro do arquivo ao prompt."""
    raiz = root.resolve()
    anexos: list[str] = []
    injetados: list[str] = []
    vistos: set[str] = set()

    for match in AT_MENTION_PATTERN.finditer(user_text):
        bruto = match.group(1)
        if bruto in vistos:
            continue
        vistos.add(bruto)

        caminho_str = bruto
        start_line: int | None = None
        end_line: int | None = None
        if ":" in bruto:
            caminho_str, intervalo = bruto.split(":", 1)
            if "-" in intervalo:
                p1, p2 = intervalo.split("-", 1)
                start_line, end_line = int(p1), int(p2)
            else:
                start_line = end_line = int(intervalo)

        if any(part.lower() in SENSITIVE_NAMES for part in Path(caminho_str).parts):
            continue
        try:
            caminho = resolve_project_path(caminho_str, raiz)
        except ValueError:
            continue
        if not caminho.is_file() or caminho.is_symlink():
            continue
        try:
            if caminho.stat().st_size > max_bytes_per_file:
                continue
            conteudo = caminho.read_text(encoding="utf-8", errors="replace")
            numerado = format_numbered_lines(conteudo, start_line=start_line, end_line=end_line)
            rel = caminho.relative_to(raiz).as_posix()
            anexos.append(f"[CONTEÚDO DE @{bruto} ({rel})]\n{numerado}")
            injetados.append(rel)
        except (OSError, ValueError):
            continue

    if not anexos:
        return user_text, []
    return user_text + "\n\n" + "\n\n".join(anexos), injetados


def _resumo_deterministico(mensagens: list[dict[str, Any]], focus: str = "") -> str:
    """Gera um resumo determinístico dos turnos antigos quando nenhum modelo é chamado."""
    pedidos_usuario: list[str] = []
    ferramentas_usadas: list[str] = []
    respostas_chave: list[str] = []

    for msg in mensagens:
        role = msg.get("role")
        content = str(msg.get("content") or "").strip()
        if role == "user" and content:
            primeira_linha = content.splitlines()[0][:160]
            pedidos_usuario.append(f"- Pedido: {primeira_linha}")
        elif role == "assistant":
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {}).get("name", "tool")
                ferramentas_usadas.append(fn)
            if content:
                primeira_linha = content.splitlines()[0][:160]
                respostas_chave.append(f"- Resposta: {primeira_linha}")

    linhas = ["[RESUMO DE CONTEXTO COMPACTADO]"]
    if focus:
        linhas.append(f"Foco solicitado: {focus}")
    if pedidos_usuario:
        linhas.append("Solicitações anteriores:")
        linhas.extend(pedidos_usuario[-8:])
    if ferramentas_usadas:
        contagem: dict[str, int] = {}
        for f in ferramentas_usadas:
            contagem[f] = contagem.get(f, 0) + 1
        resumo_tools = ", ".join(f"{k} ({v}x)" for k, v in sorted(contagem.items()))
        linhas.append(f"Ferramentas executadas: {resumo_tools}")
    if respostas_chave:
        linhas.append("Conclusões registradas:")
        linhas.extend(respostas_chave[-6:])
    return "\n".join(linhas)


def compact_history(
    messages: list[dict[str, Any]],
    summarizer: Callable[[str], str] | None = None,
    keep_last_user_turns: int = 2,
    focus: str = "",
) -> list[dict[str, Any]]:
    """Compacta o histórico preservando o system prompt e os últimos turnos de usuário completos."""
    if len(messages) <= 3:
        return list(messages)

    system = messages[0]
    rest = messages[1:]
    indices_user = [i for i, m in enumerate(rest) if m.get("role") == "user"]
    if len(indices_user) <= keep_last_user_turns:
        return list(messages)

    corte = indices_user[-keep_last_user_turns]
    antigas = rest[:corte]
    recentes = rest[corte:]

    resumo_base = _resumo_deterministico(antigas, focus=focus)
    if summarizer is not None:
        try:
            prompt = (
                "Resuma de forma objetiva o histórico de engenharia abaixo em português, "
                "preservando arquivos alterados, decisões técnicas e pendências.\n"
                f"{f'Foco especial: {focus}' if focus else ''}\n\n"
                f"{resumo_base}"
            )
            gerado = summarizer(prompt).strip()
            if gerado:
                resumo_base = f"[RESUMO DE CONTEXTO COMPACTADO]\n{gerado}"
        except Exception:
            pass

    return [
        system,
        {"role": "user", "content": resumo_base},
        {"role": "assistant", "content": "Entendido. Contexto anterior compactado e preservado."},
        *recentes,
    ]


def _todos_path(root: Path) -> Path:
    return root.resolve() / STATE_DIR_NAME / TODOS_FILE_NAME


def load_todos(root: Path) -> list[TodoItem]:
    """Lê a lista de tarefas (TodoWrite) salva em .rarness/todos.json."""
    caminho = _todos_path(root)
    if not caminho.is_file():
        return []
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        if not isinstance(dados, list):
            return []
        itens: list[TodoItem] = []
        for item in dados:
            if isinstance(item, dict) and item.get("id") and item.get("content"):
                itens.append(
                    TodoItem(
                        id=str(item["id"]),
                        content=str(item["content"]),
                        status=str(item.get("status", "pending")),
                        priority=str(item.get("priority", "medium")),
                    )
                )
        return itens
    except (OSError, json.JSONDecodeError):
        return []


def save_todos(root: Path, todos: list[dict[str, Any] | TodoItem]) -> list[TodoItem]:
    """Valida e grava a lista de tarefas do agente em .rarness/todos.json."""
    normalizados: list[TodoItem] = []
    for indice, bruto in enumerate(todos, 1):
        dados = asdict(bruto) if isinstance(bruto, TodoItem) else bruto
        if not isinstance(dados, dict):
            raise ValueError(f"Tarefa #{indice} deve ser um objeto.")
        tid = str(dados.get("id") or f"t{indice}").strip()
        content = str(dados.get("content") or "").strip()
        if not content:
            raise ValueError(f"Tarefa {tid!r} está sem descrição (content).")
        status = str(dados.get("status", "pending")).strip().lower()
        if status not in TODO_STATUSES:
            raise ValueError(f"Status inválido em {tid!r}: {status!r}. Use: {', '.join(TODO_STATUSES)}.")
        priority = str(dados.get("priority", "medium")).strip().lower()
        if priority not in TODO_PRIORITIES:
            priority = "medium"
        normalizados.append(TodoItem(id=tid, content=content, status=status, priority=priority))

    caminho = _todos_path(root)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(
        json.dumps([asdict(t) for t in normalizados], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return normalizados


def format_todos(todos: list[TodoItem]) -> str:
    """Formata a lista de tarefas em texto legível estilo checklist do Claude Code."""
    if not todos:
        return "Nenhuma tarefa registrada na lista (use todo_write para criar)."
    icones = {"pending": "[ ]", "in_progress": "[/]", "completed": "[x]"}
    linhas = []
    for item in todos:
        marcador = icones.get(item.status, "[ ]")
        linhas.append(f"{marcador} ({item.id}) [{item.priority}] {item.content}")
    concluidas = sum(1 for t in todos if t.status == "completed")
    linhas.append(f"Progresso: {concluidas}/{len(todos)} concluídas")
    return "\n".join(linhas)
