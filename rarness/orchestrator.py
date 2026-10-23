"""Orquestra um projeto: planeja, gera arquivos por tarefa, testa e corrige.

O modelo e a confirmação do usuário entram como funções, para que este módulo
possa ser testado sem rede e sem terminal.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from rarness.planner import Plan, Task, build_planning_prompt, parse_plan_json, plan_from_dict, validate_plan
from rarness.project_fs import build_zip, create_backup, write_project_files
from rarness.repair import repair_loop
from rarness.state import STATE_DIR_NAME, ProjectState, next_task, record_decision, save_state, set_task_status
from rarness_security import resolve_project_path

LIMITE_CONTEXTO = 20000

Ask = Callable[[str], str]
Confirm = Callable[[dict[str, str]], bool]


def parse_files_json(text: str) -> dict[str, str]:
    """Lê a resposta do modelo no formato {"arquivos": {"caminho": "conteúdo"}}."""
    limpo = text.strip()
    if limpo.startswith("```"):
        limpo = limpo.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        dados = json.loads(limpo)
    except json.JSONDecodeError as exc:
        raise ValueError(f"resposta do modelo não é JSON válido: {exc}") from exc
    arquivos = dados.get("arquivos") if isinstance(dados, dict) else None
    if not isinstance(arquivos, dict) or not arquivos:
        raise ValueError('a resposta precisa conter um objeto "arquivos" com pelo menos um arquivo')
    if not all(isinstance(caminho, str) and isinstance(conteudo, str) for caminho, conteudo in arquivos.items()):
        raise ValueError("caminhos e conteúdos de arquivos precisam ser texto")
    return arquivos


def build_files_prompt(task: Task, existentes: dict[str, str], falhas: set[str] | None = None, saida: str = "") -> str:
    """Monta o pedido de geração (ou correção) dos arquivos de uma tarefa."""
    linhas = [
        f'Você está implementando a tarefa "{task.title}" (id {task.id}) de um projeto.',
        f"Crie ou altere SOMENTE estes arquivos: {', '.join(task.files)}.",
    ]
    if task.acceptance:
        linhas.append("Critérios de aceitação:")
        linhas.extend(f"- {criterio}" for criterio in task.acceptance)
    if falhas:
        linhas.append("Os testes falharam. Corrija a causa, sem apagar funcionalidades existentes.")
        linhas.append("Falhas: " + ", ".join(sorted(falhas)))
        linhas.append("Saída dos testes (trecho):")
        linhas.append(saida[-4000:])
    for caminho, conteudo in existentes.items():
        linhas.append(f"Conteúdo atual de {caminho}:")
        linhas.append(conteudo)
    linhas.append(
        'Responda somente com JSON no formato {"arquivos": {"caminho/relativo": "conteúdo completo do arquivo"}}, '
        "sem texto fora do JSON."
    )
    return "\n".join(linhas)


def create_project(root: Path, description: str, project_type: str, ask: Ask) -> ProjectState:
    """Gera o plano com o modelo, valida e grava o estado inicial do projeto."""
    plan = parse_plan_json(ask(build_planning_prompt(description, project_type)))
    erros = validate_plan(plan)
    if erros:
        raise ValueError("plano inválido: " + "; ".join(erros))
    state = ProjectState(
        project_name=plan.project_name,
        project_type=plan.project_type,
        plan=asdict(plan),
        task_status={task.id: "pending" for task in plan.tasks},
    )
    record_decision(state, f"Plano gerado a partir de: {description[:200]}")
    save_state(root, state)
    return state


def _tarefa(plan: Plan, task_id: str) -> Task:
    for task in plan.tasks:
        if task.id == task_id:
            return task
    raise ValueError(f"tarefa desconhecida: {task_id}")


def _conteudo_atual(root: Path, caminhos: list[str]) -> dict[str, str]:
    conteudo = {}
    for relativo in caminhos:
        try:
            caminho = resolve_project_path(relativo, root)
        except ValueError:
            continue
        if caminho.is_file():
            conteudo[relativo] = caminho.read_text(encoding="utf-8", errors="replace")[:LIMITE_CONTEXTO]
    return conteudo


def _valida_arquivos(task: Task, arquivos: dict[str, str]) -> dict[str, str]:
    fora = sorted(set(arquivos) - set(task.files))
    if fora:
        raise ValueError(f"o modelo tentou gravar arquivos fora da tarefa: {', '.join(fora)}")
    return arquivos


def generate_task(root: Path, state: ProjectState, task: Task, ask: Ask, confirm: Confirm) -> list[str] | None:
    """Gera os arquivos de uma tarefa. Devolve None se o usuário recusar a gravação."""
    existentes = _conteudo_atual(root, task.files)
    arquivos = _valida_arquivos(task, parse_files_json(ask(build_files_prompt(task, existentes))))
    if not confirm(arquivos):
        return None
    create_backup(root, list(arquivos))
    gravados = write_project_files(root, arquivos, overwrite=True)
    state.files = sorted(set(state.files) | set(gravados))
    set_task_status(state, task.id, "done")
    return gravados


def _corrige(root: Path, task: Task, falhas: set[str], saida: str, ask: Ask, confirm: Confirm) -> str | None:
    existentes = _conteudo_atual(root, task.files)
    prompt = build_files_prompt(task, existentes, falhas=falhas, saida=saida)
    try:
        arquivos = _valida_arquivos(task, parse_files_json(ask(prompt)))
    except ValueError:
        return None
    if not confirm(arquivos):
        return None
    create_backup(root, list(arquivos))
    gravados = write_project_files(root, arquivos, overwrite=True)
    return "arquivos corrigidos: " + ", ".join(gravados)


def build_project(
    root: Path,
    state: ProjectState,
    ask: Ask,
    confirm: Confirm,
    run_tests: Callable[[], object] | None = None,
    max_attempts: int = 3,
    progress: Callable[[str], None] | None = None,
) -> ProjectState:
    """Executa as tarefas elegíveis em ordem, testando e corrigindo cada uma.

    Para na primeira tarefa que falha ou é recusada. O estado é salvo a cada
    passo, então uma nova chamada retoma de onde parou.
    """
    plan = plan_from_dict(state.plan)
    avisar = progress or (lambda mensagem: None)
    while (task_id := next_task(state)) is not None:
        task = _tarefa(plan, task_id)
        set_task_status(state, task_id, "in_progress")
        save_state(root, state)
        avisar(f"Gerando {task_id}: {task.title}")
        try:
            gravados = generate_task(root, state, task, ask, confirm)
        except ValueError as exc:
            set_task_status(state, task_id, "failed")
            record_decision(state, f"{task_id} falhou na geração: {exc}")
            save_state(root, state)
            return state
        if gravados is None:
            set_task_status(state, task_id, "pending")
            save_state(root, state)
            avisar(f"{task_id} recusada pelo usuário.")
            return state
        if run_tests is not None:
            resultado = repair_loop(
                run_tests,
                lambda falhas, saida: _corrige(root, task, falhas, saida, ask, confirm),
                max_attempts=max_attempts,
            )
            state.test_results.append({"tarefa": task_id, "aprovado": resultado.passed, "motivo": resultado.reason})
            if not resultado.passed:
                set_task_status(state, task_id, "failed")
                record_decision(state, f"{task_id} não passou nos testes: {resultado.reason}")
                save_state(root, state)
                avisar(f"{task_id} falhou nos testes: {resultado.reason}")
                return state
        save_state(root, state)
    return state


def package_project(root: Path, state: ProjectState) -> Path:
    """Gera o ZIP do projeto em .rarness/exportacoes, sem segredos nem dependências."""
    nome = re.sub(r"[^A-Za-z0-9_-]+", "-", state.project_name).strip("-") or "projeto"
    destino = root / STATE_DIR_NAME / "exportacoes" / f"{nome}.zip"
    destino.parent.mkdir(parents=True, exist_ok=True)
    return build_zip(root, destino)
