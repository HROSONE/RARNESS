"""Motor de Inferência de Modo, Intenção, Profundidade de Raciocínio (Thinking Budget) e Roteamento de Modelo.

Implementa a engenharia reversa do roteador cognitivo do Claude Code e o expande com:
- Classificação determinística de intenção (explicação, planejamento, review, debug/reparo, implementação, greenfield);
- Inferência automática de modo de permissão (plan vs ask/auto-edit) para impedir mutações em perguntas;
- Detecção de Thinking Budget (standard, deep/megathink, ultrathink) com ajuste automático de tokens e modelo;
- Seleção inteligente entre gemini-3.8-flash (rápido) e gemini-4-argon (raciocínio avançado), proibindo modelos 1.x/2.x;
- Extração automática de arquivos e linhas de stacktraces (Python/JS/Kotlin/Go/Rust) para injeção proativa de contexto.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from rarness.code_intel import format_numbered_lines
from rarness_security import SENSITIVE_NAMES, resolve_project_path

INTENT_KINDS = (
    "explanation",
    "planning",
    "review",
    "debug",
    "implementation",
    "greenfield",
)

THINKING_LEVELS = ("standard", "deep", "ultrathink")

# Gatilhos de profundidade cognitiva (Claude Code: think / think hard / megathink / ultrathink)
_ULTRATHINK_RE = re.compile(
    r"\b(ultrathink|think\s+harder|pense\s+profundamente|engenharia\s+reversa|"
    r"arquitetura\s+completa|redesenhe|reescreva\s+todo|análise\s+exaustiva)\b",
    re.IGNORECASE,
)
_DEEP_THINK_RE = re.compile(
    r"\b(megathink|think\s+hard|think\s+deeply|pense\s+passo\s+a\s+passo|"
    r"analise\s+com\s+cuidado|investigue\s+a\s+fundo|refatore|otimize)\b",
    re.IGNORECASE,
)

# Padrões de intenção
_REVIEW_RE = re.compile(
    r"\b(code\s+review|revise|revisar|revisão|audite|auditoria|procure\s+bugs|"
    r"vulnerabilidade|segurança\s+do\s+código|analise\s+o\s+diff)\b",
    re.IGNORECASE,
)
_DEBUG_RE = re.compile(
    r"(traceback\s+\(most\s+recent|syntaxerror|typeerror|valueerror|attributeerror|"
    r"importerror|modulenotfounderror|keyerror|indexerror|exception|segmentation\s+fault|"
    r"\bfalhou\b|\bfalhando\b|\bcorrija\b|\bconserte\b|\berro\b|\bbug\b|"
    r"não\s+funciona|quebrando|stack\s*trace)",
    re.IGNORECASE,
)
_PLANNING_RE = re.compile(
    r"^(?:planeje|projete|estruture|desenhe\s+a\s+arquitetura|crie\s+um\s+plano|"
    r"como\s+devo\s+arquitetar|quais\s+etapas|faça\s+um\s+roadmap)\b",
    re.IGNORECASE,
)
_EXPLANATION_RE = re.compile(
    r"^(?:o\s+que\s+é|o\s+que\s+faz|como\s+funciona|onde\s+fica|onde\s+está|"
    r"explique|me\s+explique|resuma|qual\s+a\s+função|quais\s+arquivos|"
    r"mostre\s+onde|liste\s+os|por\s+que\s+o)\b",
    re.IGNORECASE,
)
_MUTATION_VERBS_RE = re.compile(
    r"\b(crie|criar|implemente|implementar|adicione|adicionar|edite|editar|"
    r"altere|alterar|modifique|modificar|remova|remover|apague|escreva|"
    r"atualize|atualizar|melhore|melhorar|construa|construir|desenvolva|gere|gerar|"
    r"corrija|conserte|refatore|instale|configure)\b",
    re.IGNORECASE,
)
_GREENFIELD_RE = re.compile(
    r"\b(crie|construa|desenvolva|faça|gere)\s+(?:um|uma)\s+"
    r"(?:novo\s+|nova\s+)?(site|landing\s+page|jogo|game|app|aplicativo|"
    r"sistema|api|backend|cli|ferramenta|dashboard)\b",
    re.IGNORECASE,
)

# Extração de stacktrace: Python (`File "caminho.py", line 42`) e genérico (`caminho.py:42:`)
_PY_TRACE_RE = re.compile(r'File\s+"([^"]+\.py)",\s+line\s+(\d+)')
_GENERIC_TRACE_RE = re.compile(r"([A-Za-z0-9_./-]+\.(?:py|js|ts|tsx|jsx|kt|go|rs)):(\d+)(?::\d+)?")


@dataclass
class IntentProfile:
    """Resultado da inferência cognitiva sobre o pedido do usuário."""

    intent: str
    effective_mode: str
    thinking_level: str
    recommended_model: str
    max_output_tokens: int
    temperature: float
    mutation_allowed: bool
    requires_todo: bool
    inferred_project_type: str = "ferramenta"
    stacktrace_targets: list[tuple[str, int]] = field(default_factory=list)
    reasoning_directive: str = ""


def infer_project_type(text: str) -> str:
    """Infere automaticamente o tipo de projeto (site, jogo, app_android, desktop, api, ferramenta)."""
    t = text.lower()
    if any(k in t for k in ("android", "kotlin", "jetpack", "apk", "celular", "mobile")):
        return "app_android"
    if any(k in t for k in ("jogo", "game", "arcade", "canvas", "phaser", "pygame", " colisão", "pontuação")):
        return "jogo"
    if any(k in t for k in ("fastapi", "flask", "endpoint", "rest", "graphql", "microserviço", "webhook", "api")):
        return "api"
    if any(k in t for k in ("tkinter", "pyqt", "desktop", "janela", "gui", "electron")):
        return "desktop"
    if any(k in t for k in ("site", "html", "css", "landing", "web", "página", "frontend", "dashboard", "portfólio")):
        return "site"
    return "ferramenta"


def detect_thinking_level(text: str) -> str:
    """Detecta o nível de raciocínio (standard, deep, ultrathink) inspirado no Claude Code."""
    if _ULTRATHINK_RE.search(text):
        return "ultrathink"
    if _DEEP_THINK_RE.search(text) or len(text) > 700:
        return "deep"
    return "standard"


def extract_stacktrace_targets(text: str, root: Path | None = None) -> list[tuple[str, int]]:
    """Extrai pares (caminho_relativo, linha) de stacktraces presentes no prompt."""
    encontrados: list[tuple[str, int]] = []
    vistos: set[tuple[str, int]] = set()

    candidatos: list[tuple[str, int]] = []
    for m in _PY_TRACE_RE.finditer(text):
        candidatos.append((m.group(1), int(m.group(2))))
    for m in _GENERIC_TRACE_RE.finditer(text):
        candidatos.append((m.group(1), int(m.group(2))))

    raiz = root.resolve() if root is not None else None
    for raw_path, lineno in candidatos:
        rel = raw_path.strip()
        if raiz is not None:
            try:
                p = Path(rel)
                resolved = p.resolve() if p.is_absolute() else resolve_project_path(rel, raiz)
                rel = resolved.relative_to(raiz).as_posix()
                if not resolved.is_file() or any(part.lower() in SENSITIVE_NAMES for part in resolved.parts):
                    continue
            except ValueError:
                continue
        chave = (rel, lineno)
        if chave not in vistos:
            vistos.add(chave)
            encontrados.append(chave)
    return encontrados[:5]


def build_stacktrace_context(targets: list[tuple[str, int]], root: Path, window: int = 8) -> str:
    """Lê automaticamente a janela de código ao redor de cada linha apontada em um stacktrace."""
    if not targets:
        return ""
    raiz = root.resolve()
    blocos: list[str] = ["[CONTEXTO AUTOMÁTICO DE STACKTRACE]"]
    for rel, lineno in targets:
        try:
            caminho = resolve_project_path(rel, raiz)
            if not caminho.is_file():
                continue
            conteudo = caminho.read_text(encoding="utf-8", errors="replace")
            ini = max(1, lineno - window)
            fim = lineno + window
            trecho = format_numbered_lines(conteudo, start_line=ini, end_line=fim)
            blocos.append(f"Arquivo {rel} (ao redor da linha {lineno}):\n{trecho}")
        except (OSError, ValueError):
            continue
    return "\n\n".join(blocos) if len(blocos) > 1 else ""


def infer_intent_and_mode(
    user_text: str,
    configured_mode: str = "ask",
    current_model: str = "gemini-3.8-flash",
    root: Path | None = None,
    auto_route_model: bool = False,
) -> IntentProfile:
    """Analisa o prompt do usuário e infere intenção, modo seguro, thinking budget e modelo."""
    texto = user_text.strip()
    thinking = detect_thinking_level(texto)
    proj_type = infer_project_type(texto)
    stack_targets = extract_stacktrace_targets(texto, root=root)

    tem_verbo_mutacao = bool(_MUTATION_VERBS_RE.search(texto))
    eh_pergunta_pura = (
        (bool(_EXPLANATION_RE.search(texto)) or texto.endswith("?"))
        and not tem_verbo_mutacao
        and not stack_targets
    )

    if _REVIEW_RE.search(texto) and not re.search(r"\b(aplique|corrija|edite)\b", texto, re.IGNORECASE):
        intent = "review"
    elif stack_targets or _DEBUG_RE.search(texto):
        intent = "debug" if tem_verbo_mutacao or stack_targets else "explanation"
    elif _PLANNING_RE.search(texto) and not re.search(r"\b(e\s+implemente|e\s+escreva)\b", texto, re.IGNORECASE):
        intent = "planning"
    elif eh_pergunta_pura:
        intent = "explanation"
    elif _GREENFIELD_RE.search(texto):
        intent = "greenfield"
    else:
        intent = "implementation"

    # Inferência do modo efetivo:
    # - Se o usuário travou em 'plan', permanece sempre 'plan'.
    # - Se a intenção for puramente 'explanation', 'planning' ou 'review' (sem pedido de edição),
    #   o modo efetivo deste turno vira 'plan' (somente leitura) para proteger o repositório!
    if configured_mode == "plan" or intent in {"explanation", "planning", "review"}:
        effective_mode = "plan"
        mutation_allowed = False
    elif configured_mode == "auto-edit":
        effective_mode = "auto-edit"
        mutation_allowed = True
    else:
        effective_mode = "ask"
        mutation_allowed = True

    # Ajuste de Thinking Budget e Modelo (exclusivamente 3.8-flash e 4-argon no Gemini)
    if thinking == "ultrathink":
        max_tokens = 8192
        temp = 0.15
        rec_model = "gemini-4-argon"
        directive = (
            "[MODO ULTRATHINK ATIVO]: Decomponha o problema em profundidade máxima, considere casos de borda, "
            "segurança, invariantes de estado e impacto entre módulos antes de agir."
        )
    elif thinking == "deep" or intent in {"planning", "review", "debug", "greenfield"}:
        thinking = "deep" if thinking == "standard" else thinking
        max_tokens = 6144
        temp = 0.2
        rec_model = "gemini-4-argon" if auto_route_model else current_model
        directive = (
            "[RACIOCÍNIO ESTRUTURADO]: Inspecione os arquivos afetados, valide hipóteses com ferramentas "
            "e verifique testes/sintaxe após cada alteração."
        )
    else:
        max_tokens = 4096
        temp = 0.25
        rec_model = current_model if current_model in ("gemini-3.8-flash", "gemini-4-argon") else "gemini-3.8-flash"
        directive = ""

    if not auto_route_model and current_model in ("gemini-3.8-flash", "gemini-4-argon"):
        rec_model = current_model

    requires_todo = intent in {"greenfield", "planning"} or (
        intent == "implementation" and (thinking in {"deep", "ultrathink"} or len(texto) > 220)
    )

    return IntentProfile(
        intent=intent,
        effective_mode=effective_mode,
        thinking_level=thinking,
        recommended_model=rec_model,
        max_output_tokens=max_tokens,
        temperature=temp,
        mutation_allowed=mutation_allowed,
        requires_todo=requires_todo,
        inferred_project_type=proj_type,
        stacktrace_targets=stack_targets,
        reasoning_directive=directive,
    )
