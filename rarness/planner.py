"""Planejador de tarefas do RARNESS: modelos, validação, ordem de execução e prompt de planejamento."""
import json
import re
from dataclasses import dataclass, field

PROJECT_TYPES = ("site", "jogo", "app_android", "desktop", "api", "ferramenta")


@dataclass
class Task:
    id: str
    title: str
    files: list[str]
    depends_on: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)


@dataclass
class Plan:
    project_name: str
    project_type: str
    requirements: list[str]
    tasks: list[Task]
    questions: list[str] = field(default_factory=list)


def _caminho_invalido(caminho: str) -> str | None:
    """Devolve o motivo pelo qual o caminho não é permitido, ou None se estiver ok."""
    if not caminho.strip():
        return "caminho vazio"
    if caminho.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", caminho):
        return "caminho absoluto não é permitido"
    partes = re.split(r"[\\/]", caminho)
    if ".." in partes:
        return "caminho com '..' não é permitido"
    if any(parte.lower().startswith(".env") for parte in partes):
        return "arquivos .env não podem ser planejados"
    return None


def _ordem_estavel(tarefas: list[Task]) -> list[Task] | None:
    """Ordena respeitando depends_on, mantendo a ordem original em empates.

    Devolve None se houver ciclo. Assume que as dependências existem.
    """
    pendentes = list(tarefas)
    concluidos: set[str] = set()
    ordem: list[Task] = []
    while pendentes:
        for i, tarefa in enumerate(pendentes):
            if all(dep in concluidos for dep in tarefa.depends_on):
                ordem.append(tarefa)
                concluidos.add(tarefa.id)
                del pendentes[i]
                break
        else:
            return None
    return ordem


def validate_plan(plan: Plan) -> list[str]:
    """Devolve a lista de erros do plano; lista vazia significa plano válido."""
    erros: list[str] = []

    if plan.project_type not in PROJECT_TYPES:
        erros.append(f"tipo de projeto inválido: {plan.project_type!r}")

    ids_vistos: set[str] = set()
    ids_duplicados: set[str] = set()
    for tarefa in plan.tasks:
        if not tarefa.id.strip():
            erros.append("tarefa com id vazio")
        elif tarefa.id in ids_vistos:
            ids_duplicados.add(tarefa.id)
        ids_vistos.add(tarefa.id)
        if not tarefa.title.strip():
            erros.append(f"tarefa {tarefa.id!r} sem título")
        for caminho in tarefa.files:
            motivo = _caminho_invalido(caminho)
            if motivo:
                erros.append(f"tarefa {tarefa.id!r}, arquivo {caminho!r}: {motivo}")

    for duplicado in sorted(ids_duplicados):
        erros.append(f"id de tarefa duplicado: {duplicado!r}")

    for tarefa in plan.tasks:
        for dep in tarefa.depends_on:
            if dep not in ids_vistos:
                erros.append(f"tarefa {tarefa.id!r} depende de tarefa inexistente: {dep!r}")

    # Checagem de ciclo considera apenas dependências que existem.
    tarefas_validas = [
        Task(t.id, t.title, t.files, [d for d in t.depends_on if d in ids_vistos], t.acceptance)
        for t in plan.tasks
    ]
    if _ordem_estavel(tarefas_validas) is None:
        erros.append("ciclo de dependências entre tarefas")

    return erros


def topological_order(plan: Plan) -> list[Task]:
    """Devolve as tarefas em ordem de execução, respeitando depends_on."""
    ids = {t.id for t in plan.tasks}
    for tarefa in plan.tasks:
        for dep in tarefa.depends_on:
            if dep not in ids:
                raise ValueError(f"tarefa {tarefa.id!r} depende de tarefa inexistente: {dep!r}")
    ordem = _ordem_estavel(plan.tasks)
    if ordem is None:
        raise ValueError("ciclo de dependências entre tarefas")
    return ordem


def _exigir(dados: dict, campo: str, tipo: type, contexto: str) -> object:
    if campo not in dados:
        raise ValueError(f"campo obrigatório ausente{contexto}: {campo!r}")
    valor = dados[campo]
    if not isinstance(valor, tipo):
        raise ValueError(f"campo {campo!r}{contexto} deve ser {tipo.__name__}")
    return valor


def _lista_de_textos(dados: dict, campo: str, contexto: str, obrigatorio: bool) -> list[str]:
    if campo not in dados:
        if obrigatorio:
            raise ValueError(f"campo obrigatório ausente{contexto}: {campo!r}")
        return []
    valor = dados[campo]
    if not isinstance(valor, list) or not all(isinstance(item, str) for item in valor):
        raise ValueError(f"campo {campo!r}{contexto} deve ser uma lista de textos")
    return list(valor)


def plan_from_dict(data: dict) -> Plan:
    """Converte um dicionário (ex.: JSON já decodificado) em Plan."""
    if not isinstance(data, dict):
        raise ValueError("o plano deve ser um objeto JSON (dicionário)")

    project_name = _exigir(data, "project_name", str, "")
    project_type = _exigir(data, "project_type", str, "")
    requirements = _lista_de_textos(data, "requirements", "", obrigatorio=True)
    tarefas_brutas = _exigir(data, "tasks", list, "")

    tasks: list[Task] = []
    for indice, bruta in enumerate(tarefas_brutas):
        contexto = f" na tarefa {indice}"
        if not isinstance(bruta, dict):
            raise ValueError(f"tarefa {indice} deve ser um objeto JSON (dicionário)")
        tasks.append(
            Task(
                id=_exigir(bruta, "id", str, contexto),
                title=_exigir(bruta, "title", str, contexto),
                files=_lista_de_textos(bruta, "files", contexto, obrigatorio=True),
                depends_on=_lista_de_textos(bruta, "depends_on", contexto, obrigatorio=False),
                acceptance=_lista_de_textos(bruta, "acceptance", contexto, obrigatorio=False),
            )
        )

    questions = _lista_de_textos(data, "questions", "", obrigatorio=False)

    return Plan(
        project_name=project_name,
        project_type=project_type,
        requirements=requirements,
        tasks=tasks,
        questions=questions,
    )


def parse_plan_json(text: str) -> Plan:
    """Lê um plano a partir de JSON puro ou de JSON dentro de cerca ```json ... ```."""
    conteudo = text.strip()
    cerca = re.search(r"```(?:json)?\s*(.*?)```", conteudo, re.DOTALL | re.IGNORECASE)
    if cerca:
        conteudo = cerca.group(1).strip()
    try:
        data = json.loads(conteudo)
    except json.JSONDecodeError as exc:
        raise ValueError(f"texto não é JSON válido: {exc.msg}") from exc
    return plan_from_dict(data)


def build_planning_prompt(description: str, project_type: str) -> str:
    """Monta o prompt que pede ao modelo um plano somente em JSON."""
    if project_type not in PROJECT_TYPES:
        raise ValueError(
            f"tipo de projeto inválido: {project_type!r}; use um de {', '.join(PROJECT_TYPES)}"
        )
    if not description.strip():
        raise ValueError("a descrição do projeto não pode estar vazia")

    return (
        "Você é o planejador do RARNESS. Transforme o pedido abaixo em um plano de execução.\n\n"
        f"Tipo de projeto: {project_type}\n"
        f"Pedido do usuário:\n{description.strip()}\n\n"
        "Regras:\n"
        "- Divida o trabalho em tarefas pequenas, cada uma com um id único.\n"
        "- Use depends_on para indicar tarefas que precisam ser concluídas antes.\n"
        "- Liste em files apenas caminhos relativos à raiz do projeto, sem '..', sem caminhos "
        "absolutos e sem arquivos .env.\n"
        "- Cada tarefa deve ter critérios de aceitação verificáveis em acceptance.\n"
        "- Se o pedido for ambíguo, liste em questions as perguntas necessárias.\n\n"
        "Responda SOMENTE com JSON, sem texto antes ou depois, usando exatamente estas chaves:\n"
        "{\n"
        '  "project_name": "string",\n'
        '  "project_type": "string",\n'
        '  "requirements": ["string"],\n'
        '  "tasks": [\n'
        "    {\n"
        '      "id": "string",\n'
        '      "title": "string",\n'
        '      "files": ["caminho/relativo.ext"],\n'
        '      "depends_on": ["id"],\n'
        '      "acceptance": ["string"]\n'
        "    }\n"
        "  ],\n"
        '  "questions": ["string"]\n'
        "}\n"
    )
