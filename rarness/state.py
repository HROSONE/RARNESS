"""Estado persistente do projeto, para retomar trabalho interrompido.

O estado fica em `<raiz>/.rarness/state.json`. A gravação é atômica: primeiro
vai para um arquivo temporário na mesma pasta e depois substitui o original.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

STATE_DIR_NAME = ".rarness"
STATE_FILE_NAME = "state.json"
CHECKPOINTS_DIR_NAME = "checkpoints"
TASK_STATUSES = ("pending", "in_progress", "done", "failed", "blocked")
VERSAO_ATUAL = 1

# Trechos que indicam dado sensível. A comparação ignora maiúsculas e minúsculas.
PADROES_SEGREDO = ("KEY", "TOKEN", "SECRET", "PASSWORD")


@dataclass
class ProjectState:
    """Retrato do projeto que permite retomar o trabalho de onde parou."""

    project_name: str
    project_type: str
    plan: dict = field(default_factory=dict)
    task_status: dict[str, str] = field(default_factory=dict)
    decisions: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    test_results: list[dict] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    version: int = VERSAO_ATUAL


def state_path(root: Path) -> Path:
    """Retorna o caminho do arquivo de estado dentro da raiz do projeto."""
    return Path(root) / STATE_DIR_NAME / STATE_FILE_NAME


def _valida_estado(state: ProjectState) -> None:
    """Confere os tipos básicos e os status das tarefas. Levanta ValueError se algo estiver errado."""
    if not isinstance(state.plan, dict):
        raise ValueError("O campo 'plan' deve ser um dicionário.")
    if not isinstance(state.task_status, dict):
        raise ValueError("O campo 'task_status' deve ser um dicionário.")
    for nome in ("decisions", "files", "next_steps"):
        if not isinstance(getattr(state, nome), list):
            raise ValueError(f"O campo '{nome}' deve ser uma lista.")
    if not isinstance(state.test_results, list) or not all(
        isinstance(item, dict) for item in state.test_results
    ):
        raise ValueError("O campo 'test_results' deve ser uma lista de dicionários.")

    tarefas = state.plan.get("tasks", [])
    if not isinstance(tarefas, list):
        raise ValueError("'plan[\"tasks\"]' deve ser uma lista.")
    for tarefa in tarefas:
        if not isinstance(tarefa, dict) or not isinstance(tarefa.get("id"), str):
            raise ValueError("Cada tarefa em 'plan[\"tasks\"]' precisa de um 'id' do tipo texto.")

    for task_id, status in state.task_status.items():
        _valida_status(status)


def _valida_status(status: str) -> None:
    if status not in TASK_STATUSES:
        validos = ", ".join(TASK_STATUSES)
        raise ValueError(f"Status inválido: {status!r}. Use um de: {validos}.")


def _nome_sensivel(nome: object) -> bool:
    texto = str(nome).upper()
    return any(padrao in texto for padrao in PADROES_SEGREDO)


def _procura_segredos(valor: object, caminho: str) -> None:
    """Percorre dicionários e listas e recusa chaves com nomes sensíveis.

    A mensagem mostra só o caminho da chave, nunca o valor.
    """
    if isinstance(valor, dict):
        for chave, conteudo in valor.items():
            sub_caminho = f"{caminho}.{chave}"
            if _nome_sensivel(chave):
                raise ValueError(
                    f"Campo sensível recusado em '{sub_caminho}': segredos não podem ser gravados no estado."
                )
            _procura_segredos(conteudo, sub_caminho)
    elif isinstance(valor, list):
        for indice, item in enumerate(valor):
            _procura_segredos(item, f"{caminho}[{indice}]")


def _para_dict(state: ProjectState) -> dict:
    return asdict(state)


def load_state(root: Path) -> ProjectState | None:
    """Carrega o estado salvo. Retorna None se o arquivo não existir.

    Levanta ValueError se o JSON estiver corrompido, se a versão for desconhecida
    ou se os campos não estiverem no formato esperado.
    """
    caminho = state_path(root)
    if not caminho.exists():
        return None

    texto = caminho.read_text(encoding="utf-8")
    try:
        dados = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Estado corrompido em {caminho}: {exc}") from exc

    if not isinstance(dados, dict):
        raise ValueError(f"Estado inválido em {caminho}: a raiz do JSON deve ser um objeto.")

    versao = dados.get("version")
    if versao != VERSAO_ATUAL:
        raise ValueError(f"Versão de estado desconhecida: {versao!r} (esperada: {VERSAO_ATUAL}).")

    campos = {item.name for item in fields(ProjectState)}
    desconhecidos = sorted(set(dados) - campos)
    if desconhecidos:
        raise ValueError(f"Campos desconhecidos no estado: {', '.join(desconhecidos)}.")

    faltando = [nome for nome in ("project_name", "project_type") if nome not in dados]
    if faltando:
        raise ValueError(f"Campos obrigatórios ausentes no estado: {', '.join(faltando)}.")

    state = ProjectState(**dados)
    _valida_estado(state)
    return state


def save_state(root: Path, state: ProjectState) -> Path:
    """Grava o estado de forma atômica e devolve o caminho do arquivo.

    Recusa com ValueError chaves sensíveis em 'plan' e 'test_results' e status fora de TASK_STATUSES.
    """
    _valida_estado(state)
    _procura_segredos(state.plan, "plan")
    _procura_segredos(state.test_results, "test_results")

    try:
        conteudo = json.dumps(_para_dict(state), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    except TypeError as exc:
        raise ValueError(f"Estado não é serializável em JSON: {exc}") from exc

    destino = state_path(root)
    pasta = destino.parent
    pasta.mkdir(parents=True, exist_ok=True)

    # O temporário fica na mesma pasta para que os.replace seja atômico.
    fd, temporario = tempfile.mkstemp(dir=pasta, prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as arquivo:
            arquivo.write(conteudo)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, destino)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporario)
        raise
    return destino


def set_task_status(state: ProjectState, task_id: str, status: str) -> None:
    """Define o status de uma tarefa.

    Levanta ValueError se o status for inválido ou se o task_id não existir em
    plan["tasks"], quando o plano tiver tarefas.
    """
    _valida_status(status)
    tarefas = state.plan.get("tasks", [])
    if tarefas and task_id not in {tarefa["id"] for tarefa in tarefas}:
        raise ValueError(f"Tarefa desconhecida: {task_id!r}.")
    state.task_status[task_id] = status


def next_task(state: ProjectState) -> str | None:
    """Retorna o primeiro task_id elegível, ou None se nenhum for elegível.

    Elegível: status 'pending' ou 'failed' e todas as dependências 'done'.
    Tarefas sem status registrado contam como 'pending'.
    """
    for tarefa in state.plan.get("tasks", []):
        task_id = tarefa["id"]
        status = state.task_status.get(task_id, "pending")
        if status not in ("pending", "failed"):
            continue
        dependencias = tarefa.get("depends_on", [])
        if all(state.task_status.get(dep) == "done" for dep in dependencias):
            return task_id
    return None


def record_decision(state: ProjectState, text: str) -> None:
    """Acrescenta uma decisão ao estado. Levanta ValueError se o texto estiver vazio."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("A decisão não pode ser vazia.")
    state.decisions.append(text.strip())


def _sanitiza_rotulo(label: str) -> str:
    """Mantém só letras ASCII, dígitos, hífen e underscore. Os demais trechos viram hífen."""
    return re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-")


def checkpoint(root: Path, label: str) -> Path:
    """Copia o estado atual para .rarness/checkpoints/<timestamp>-<label>.json.

    Levanta FileNotFoundError se não houver estado salvo e ValueError se o rótulo
    ficar vazio depois da sanitização.
    """
    origem = state_path(root)
    if not origem.is_file():
        raise FileNotFoundError(f"Não há estado para criar checkpoint em {origem}.")

    rotulo = _sanitiza_rotulo(label)
    if not rotulo:
        raise ValueError("O rótulo do checkpoint ficou vazio após a sanitização.")

    pasta = origem.parent / CHECKPOINTS_DIR_NAME
    pasta.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destino = pasta / f"{carimbo}-{rotulo}.json"
    contador = 1
    while destino.exists():
        destino = pasta / f"{carimbo}-{rotulo}-{contador}.json"
        contador += 1

    shutil.copyfile(origem, destino)
    return destino
