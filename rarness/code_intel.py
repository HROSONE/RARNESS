"""Inteligência de código, leitura numerada, diff unificado, edição cirúrgica/fuzzy, verificação AST e mapa do repositório."""
from __future__ import annotations

import ast
import difflib
import fnmatch
import json
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

_BRACE_CHECK_EXTENSIONS = {".js", ".ts", ".jsx", ".tsx", ".kt", ".kts", ".java", ".go", ".rs", ".c", ".cpp", ".css"}


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


def _fuzzy_indentation_replace(content: str, old_text: str, new_text: str) -> str | None:
    """Tenta localizar um bloco ignorando diferenças de indentação base, preservando o recuo original.

    Só aplica se houver exatamente 1 correspondência inequívoca no arquivo.
    """
    old_lines = old_text.splitlines()
    if not old_lines or not any(line.strip() for line in old_lines):
        return None

    content_lines = content.splitlines(keepends=True)
    n = len(old_lines)
    if n > len(content_lines):
        return None

    old_stripped = [line.strip() for line in old_lines]
    matches: list[int] = []
    for i in range(len(content_lines) - n + 1):
        window_stripped = [content_lines[i + j].strip() for j in range(n)]
        if window_stripped == old_stripped:
            matches.append(i)

    if len(matches) != 1:
        return None

    start_idx = matches[0]
    # Calcula a diferença de indentação na primeira linha não vazia
    first_non_empty_idx = next(j for j, line in enumerate(old_lines) if line.strip())
    actual_line = content_lines[start_idx + first_non_empty_idx]
    actual_indent = len(actual_line) - len(actual_line.lstrip(" "))
    old_indent = len(old_lines[first_non_empty_idx]) - len(old_lines[first_non_empty_idx].lstrip(" "))
    delta_indent = actual_indent - old_indent

    eol = "\r\n" if content_lines[start_idx].endswith("\r\n") else "\n"
    new_lines_raw = new_text.splitlines()
    adjusted_new_lines: list[str] = []
    for line in new_lines_raw:
        if not line.strip():
            adjusted_new_lines.append("")
        elif delta_indent > 0:
            adjusted_new_lines.append((" " * delta_indent) + line)
        elif delta_indent < 0:
            remove = min(-delta_indent, len(line) - len(line.lstrip(" ")))
            adjusted_new_lines.append(line[remove:])
        else:
            adjusted_new_lines.append(line)

    replacement_str = eol.join(adjusted_new_lines)
    if content_lines[start_idx + n - 1].endswith(("\n", "\r\n")):
        replacement_str += eol

    return "".join(content_lines[:start_idx]) + replacement_str + "".join(content_lines[start_idx + n:])


def apply_surgical_edit(
    content: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
) -> tuple[str, int]:
    """Substitui old_text por new_text com validação de unicidade e fallback de indentação inteligente."""
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
        if not replace_all:
            fuzzy_res = _fuzzy_indentation_replace(content, old_text, new_text)
            if fuzzy_res is not None:
                return fuzzy_res, 1
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


def patch_lines(content: str, start_line: int, end_line: int, new_text: str) -> str:
    """Substitui cirurgicamente o intervalo de linhas start_line..end_line (1-indexado, inclusivo)."""
    lines = content.splitlines(keepends=True)
    total = len(lines)
    if start_line < 1 or end_line < start_line or start_line > max(1, total + 1):
        raise ValueError(f"Intervalo de linhas inválido ({start_line}..{end_line}) para arquivo com {total} linhas.")
    eol = "\r\n" if (lines and lines[0].endswith("\r\n")) else "\n"
    new_lines = [line + eol for line in new_text.splitlines()]
    if new_text == "":
        new_lines = []
    prefix = lines[: start_line - 1]
    suffix = lines[min(total, end_line) :]
    result = "".join(prefix + new_lines + suffix)
    if not content.endswith(("\n", "\r\n")) and not suffix and result.endswith(eol):
        result = result[: -len(eol)]
    return result


def _verify_balanced_delimiters(content: str) -> tuple[bool, str]:
    """Verifica balanceamento de {}, () e [] ignorando strings e comentários."""
    stack: list[tuple[str, int]] = []
    pairs = {"}": "{", ")": "(", "]": "["}
    i = 0
    n = len(content)
    line = 1

    while i < n:
        ch = content[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        # Comentário de linha //
        if ch == "/" and i + 1 < n and content[i + 1] == "/":
            while i < n and content[i] != "\n":
                i += 1
            continue
        # Comentário de bloco /* ... */
        if ch == "/" and i + 1 < n and content[i + 1] == "*":
            i += 2
            while i + 1 < n and not (content[i] == "*" and content[i + 1] == "/"):
                if content[i] == "\n":
                    line += 1
                i += 1
            i += 2
            continue
        # Strings
        if ch in ("'", '"', "`"):
            quote = ch
            i += 1
            while i < n:
                c = content[i]
                if c == "\\":
                    i += 2
                    continue
                if c == "\n":
                    line += 1
                if c == quote:
                    i += 1
                    break
                i += 1
            continue

        if ch in ("{", "(", "["):
            stack.append((ch, line))
        elif ch in ("}", ")", "]"):
            expected = pairs[ch]
            if not stack:
                return False, f"Delimitador '{ch}' extra fechado na linha {line} sem abertura correspondente."
            open_ch, open_line = stack.pop()
            if open_ch != expected:
                return (
                    False,
                    f"Delimitador '{ch}' na linha {line} não corresponde a '{open_ch}' aberto na linha {open_line}.",
                )
        i += 1

    if stack:
        open_ch, open_line = stack[-1]
        return False, f"Falta fechar '{open_ch}' aberto na linha {open_line}."
    return True, "Sintaxe OK (delimitadores balanceados)"


def verify_syntax(path_or_name: str | Path, content: str) -> tuple[bool, str]:
    """Valida a sintaxe do arquivo após edição (AST para Python, parser JSON e balanceamento léxico)."""
    p = Path(path_or_name)
    suffix = p.suffix.lower()

    if suffix == ".py":
        try:
            ast.parse(content, filename=str(p))
            return True, "Sintaxe Python OK (AST validada)"
        except SyntaxError as exc:
            return False, f"Erro de sintaxe Python na linha {exc.lineno}: {exc.msg}"

    if suffix in {".json", ".ipynb"}:
        try:
            json.loads(content)
            return True, "Sintaxe JSON OK"
        except json.JSONDecodeError as exc:
            return False, f"Erro de sintaxe JSON na linha {exc.lineno}, coluna {exc.colno}: {exc.msg}"

    if suffix in _BRACE_CHECK_EXTENSIONS:
        return _verify_balanced_delimiters(content)

    return True, "Sintaxe OK"


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


def find_symbol(root: Path, symbol: str, max_results: int = 40) -> str:
    """Localiza definições (classes, funções, métodos) e referências de um símbolo no projeto."""
    nome = symbol.strip()
    if not nome or not re.match(r"^[A-Za-z_]\w*$", nome):
        return "Erro: informe um identificador de símbolo válido."

    raiz = root.resolve()
    def_re = re.compile(
        rf"^\s*(?:async\s+def|def|class|fun|suspend\s+fun|function|async\s+function|"
        rf"export\s+(?:async\s+)?function|export\s+class|pub\s+fn|fn|struct|interface|type)\s+{re.escape(nome)}\b"
    )
    ref_re = re.compile(rf"\b{re.escape(nome)}\b")

    definicoes: list[str] = []
    referencias: list[str] = []

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
            if caminho.is_symlink() or not caminho.is_file():
                continue
            try:
                if caminho.stat().st_size > MAX_DEFAULT_READ_BYTES:
                    continue
                linhas = caminho.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            rel = caminho.relative_to(raiz).as_posix()
            for idx, linha in enumerate(linhas, 1):
                if def_re.search(linha):
                    definicoes.append(f"{rel}:{idx}: {linha.strip()[:180]}")
                elif ref_re.search(linha) and len(referencias) < max_results:
                    referencias.append(f"{rel}:{idx}: {linha.strip()[:180]}")

    partes: list[str] = []
    if definicoes:
        partes.append("[DEFINIÇÕES]\n" + "\n".join(definicoes[:20]))
    if referencias:
        partes.append("[REFERÊNCIAS]\n" + "\n".join(referencias[:max_results]))
    return "\n\n".join(partes) or f"Símbolo {nome!r} não encontrado no projeto."


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
