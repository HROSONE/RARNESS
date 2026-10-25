"""Inteligência de código, leitura numerada, diff unificado, edição cirúrgica e mapa do repositório."""
from __future__ import annotations

import ast
import difflib
import fnmatch
import os
import re
from pathlib import Path
from typing import Any

from rarness.project_fs import IGNORED_DIR_NAMES
from rarness_security import SENSITIVE_NAMES, resolve_project_path

MAX_DEFAULT_READ_BYTES = 1_000_000

_SYMBOL_PATTERNS = [
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_]\w*)", re.MULTILINE),
    re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_]\w*)", re.MULTILINE),
    re.compile(r"^\s*(?:pub\s+)?(?:fn|struct|enum|trait)\s+([A-Za-z_]\w*)", re.MULTILINE),
    re.compile(r"^\s*func\s+(?:\([^)]+\)\s*)?([A-Za-z_]\w*)", re.MULTILINE),
    re.compile(r"^\s*(?:public|private|protected|internal)?\s*(?:suspend\s+)?fun\s+([A-Za-z_]\w*)", re.MULTILINE),
]


def format_numbered_lines(
    text: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """Formata o texto com números de linha (1-indexado), suportando janela start_line..end_line."""
    if not text:
        return "(Arquivo vazio)"
    lines = text.splitlines()
    total = len(lines)
    inicio = 1 if start_line is None else max(1, int(start_line))
    fim = total if end_line is None else min(total, int(end_line))
    if inicio > fim:
        raise ValueError("start_line não pode ser maior que end_line.")
    selecionadas = [
        f"{num:4d} | {lines[num - 1]}"
        for num in range(inicio, fim + 1)
    ]
    cabecalho = ""
    if start_line is not None or end_line is not None:
        cabecalho = f"[Linhas {inicio}-{fim} de {total}]\n"
    return cabecalho + "\n".join(selecionadas)


def unified_diff_preview(
    old_text: str,
    new_text: str,
    path_label: str = "arquivo",
    context_lines: int = 3,
    max_chars: int = 4000,
) -> str:
    """Gera um unified diff legível entre o conteúdo anterior e o novo."""
    diff_lines = list(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{path_label}",
            tofile=f"b/{path_label}",
            n=context_lines,
        )
    )
    if not diff_lines:
        return "(Sem alterações de conteúdo)"
    joined = "".join(diff_lines).rstrip("\n")
    if len(joined) > max_chars:
        return joined[:max_chars] + "\n... [diff truncado]"
    return joined


def apply_surgical_edit(
    content: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
) -> tuple[str, int]:
    """Substitui old_text por new_text com validação de unicidade estilo Claude Code."""
    if not old_text:
        raise ValueError("old_text não pode ser vazio.")
    if old_text == new_text:
        raise ValueError("old_text e new_text são idênticos.")

    alvo = old_text
    substituto = new_text
    if alvo not in content and "\r\n" in content and "\r\n" not in alvo:
        alvo_crlf = alvo.replace("\n", "\r\n")
        if alvo_crlf in content:
            alvo = alvo_crlf
            substituto = new_text.replace("\n", "\r\n")

    ocorrencias = content.count(alvo)
    if ocorrencias == 0:
        raise ValueError("trecho exato não encontrado; leia o arquivo antes de tentar novamente.")
    if ocorrencias > 1 and not replace_all:
        raise ValueError(
            f"trecho ambíguo: encontrado {ocorrencias} vezes no arquivo. "
            "Inclua mais linhas de contexto em old_text ou defina replace_all=true."
        )

    if replace_all:
        return content.replace(alvo, substituto), ocorrencias
    return content.replace(alvo, substituto, 1), 1


def apply_multi_edit(content: str, edits: list[dict[str, Any]]) -> tuple[str, int]:
    """Aplica uma lista de edições sequenciais de forma atômica sobre o texto."""
    if not isinstance(edits, list) or not edits:
        raise ValueError("edits deve ser uma lista não vazia de operações.")
    atual = content
    total_substituicoes = 0
    for indice, item in enumerate(edits, 1):
        if not isinstance(item, dict):
            raise ValueError(f"edição #{indice} inválida: esperado objeto JSON.")
        old_text = str(item.get("old_text", ""))
        new_text = str(item.get("new_text", ""))
        replace_all = bool(item.get("replace_all", False))
        try:
            atual, cont = apply_surgical_edit(atual, old_text, new_text, replace_all=replace_all)
            total_substituicoes += cont
        except ValueError as exc:
            raise ValueError(f"falha na edição #{indice}: {exc}") from exc
    return atual, total_substituicoes


def search_codebase(
    root: Path,
    query: str,
    *,
    regex: bool = False,
    case_sensitive: bool = False,
    file_pattern: str = "*",
    context_lines: int = 0,
    max_results: int = 50,
    max_read_bytes: int = MAX_DEFAULT_READ_BYTES,
) -> str:
    """Pesquisa textual ou por regex no repositório, com filtro glob e linhas de contexto."""
    termo = query.strip()
    if not termo:
        return "Erro: pesquisa vazia."

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        padrao_re = re.compile(termo if regex else re.escape(termo), flags)
    except re.error as exc:
        return f"Erro em expressão regular: {exc}"

    raiz = root.resolve()
    resultados: list[str] = []
    contexto = max(0, min(5, int(context_lines)))

    for atual, dirs, filenames in os.walk(raiz):
        pasta_atual = Path(atual)
        dirs[:] = sorted(
            d for d in dirs
            if d not in IGNORED_DIR_NAMES
            and d.lower() not in SENSITIVE_NAMES
            and not (pasta_atual / d).is_symlink()
        )
        for filename in sorted(filenames):
            if filename.lower() in SENSITIVE_NAMES:
                continue
            caminho = pasta_atual / filename
            if caminho.is_symlink():
                continue
            rel = caminho.relative_to(raiz).as_posix()
            if file_pattern and file_pattern != "*":
                if not (fnmatch.fnmatch(filename, file_pattern) or fnmatch.fnmatch(rel, file_pattern)):
                    continue
            try:
                if caminho.stat().st_size > max_read_bytes:
                    continue
                linhas = caminho.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue

            for idx, linha in enumerate(linhas):
                if padrao_re.search(linha):
                    num_linha = idx + 1
                    if contexto == 0:
                        resultados.append(f"{rel}:{num_linha}: {linha[:200]}")
                    else:
                        ini = max(0, idx - contexto)
                        fim = min(len(linhas), idx + contexto + 1)
                        bloco = [f"{rel}:{num_linha}:"]
                        for j in range(ini, fim):
                            marcador = ">" if j == idx else " "
                            bloco.append(f"  {marcador} {j + 1:4d} | {linhas[j][:200]}")
                        resultados.append("\n".join(bloco))
                    if len(resultados) >= max_results:
                        return "\n".join(resultados) + f"\n(Limite de {max_results} resultados.)"

    return "\n".join(resultados) or "Nenhuma ocorrência encontrada."


def _python_symbols(source: str) -> list[str]:
    """Extrai classes e funções de primeiro/segundo nível de um arquivo Python via AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    simbolos: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in node.args.args]
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            simbolos.append(f"L{node.lineno}: {prefix} {node.name}({', '.join(args)})")
        elif isinstance(node, ast.ClassDef):
            metodos = [
                n.name for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            resumo_metodos = f" [{', '.join(metodos[:8])}]" if metodos else ""
            simbolos.append(f"L{node.lineno}: class {node.name}{resumo_metodos}")
    return simbolos


def _generic_symbols(source: str) -> list[str]:
    encontrados: list[str] = []
    for padrao in _SYMBOL_PATTERNS:
        for match in padrao.finditer(source):
            linha = source.count("\n", 0, match.start()) + 1
            encontrados.append(f"L{linha}: {match.group(1)}")
            if len(encontrados) >= 20:
                return encontrados
    return encontrados


def build_repo_map(root: Path, sub_path: str = ".", max_files: int = 80) -> str:
    """Constrói um mapa estrutural de arquivos, classes e funções do projeto."""
    alvo_raiz = resolve_project_path(sub_path, root) if sub_path not in ("", ".") else root.resolve()
    raiz = root.resolve()
    if not alvo_raiz.exists():
        return "Erro: caminho não encontrado."

    linhas_mapa: list[str] = []
    arquivos_processados = 0

    for atual, dirs, filenames in os.walk(alvo_raiz):
        pasta_atual = Path(atual)
        dirs[:] = sorted(
            d for d in dirs
            if d not in IGNORED_DIR_NAMES
            and d.lower() not in SENSITIVE_NAMES
            and not (pasta_atual / d).is_symlink()
        )
        for filename in sorted(filenames):
            if filename.lower() in SENSITIVE_NAMES:
                continue
            caminho = pasta_atual / filename
            if caminho.is_symlink() or not caminho.is_file():
                continue
            rel = caminho.relative_to(raiz).as_posix()
            arquivos_processados += 1
            if arquivos_processados > max_files:
                linhas_mapa.append(f"... (truncado após {max_files} arquivos)")
                return "\n".join(linhas_mapa)

            simbolos: list[str] = []
            try:
                if caminho.stat().st_size <= MAX_DEFAULT_READ_BYTES:
                    conteudo = caminho.read_text(encoding="utf-8", errors="ignore")
                    if caminho.suffix == ".py":
                        simbolos = _python_symbols(conteudo)
                    elif caminho.suffix in {".js", ".ts", ".tsx", ".jsx", ".kt", ".go", ".rs"}:
                        simbolos = _generic_symbols(conteudo)
            except OSError:
                pass

            if simbolos:
                linhas_mapa.append(f"{rel}:")
                for s in simbolos[:15]:
                    linhas_mapa.append(f"  - {s}")
            else:
                linhas_mapa.append(f"{rel}")

    return "\n".join(linhas_mapa) or "Nenhum arquivo encontrado no projeto."
