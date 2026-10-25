"""Operações Git seguras e restauração de backups (/undo) para o RARNESS."""
from __future__ import annotations

import shutil
from pathlib import Path

from rarness.executor import run_command
from rarness_security import resolve_project_path


def git_status_summary(root: Path) -> str:
    """Retorna um resumo do status Git (branch, alterações e últimos commits)."""
    raiz = root.resolve()
    status = run_command("git status --short --branch", raiz, timeout_s=10)
    if status.exit_code != 0:
        return f"Repositório Git não detectado ou indisponível: {status.stderr.strip() or status.stdout.strip()}"
    log = run_command("git log -n 5 --oneline", raiz, timeout_s=10)
    partes = ["[GIT STATUS]", status.stdout.strip() or "( árvore limpa )"]
    if log.exit_code == 0 and log.stdout.strip():
        partes.append("\n[ÚLTIMOS COMMITS]")
        partes.append(log.stdout.strip())
    return "\n".join(partes)


def git_diff_summary(root: Path, staged: bool = False, max_chars: int = 12000) -> str:
    """Retorna o diff atual do repositório Git."""
    raiz = root.resolve()
    flag = "--staged" if staged else ""
    cmd = f"git diff {flag}".strip()
    res = run_command(cmd, raiz, timeout_s=15, max_output_chars=max_chars)
    if res.exit_code != 0:
        return f"Erro ao obter git diff: {res.stderr.strip()}"
    texto = res.stdout.strip()
    if not texto:
        stat = run_command("git status --short", raiz, timeout_s=10)
        return stat.stdout.strip() or "(Sem alterações no git diff)"
    return texto


def restore_latest_backup(root: Path, backup_dir_name: str = ".rarness/backups") -> list[str]:
    """Restaura os arquivos do backup mais recente em .rarness/backups e remove o ponto restaurado."""
    raiz = root.resolve()
    pasta_base = resolve_project_path(backup_dir_name, raiz)
    if not pasta_base.is_dir():
        return []

    snapshots = sorted([p for p in pasta_base.iterdir() if p.is_dir()], key=lambda p: p.name)
    if not snapshots:
        return []

    ultimo = snapshots[-1]
    restaurados: list[str] = []
    for arquivo_backup in sorted(ultimo.rglob("*")):
        if not arquivo_backup.is_file() or arquivo_backup.is_symlink():
            continue
        rel = arquivo_backup.relative_to(ultimo).as_posix()
        destino = resolve_project_path(rel, raiz)
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(arquivo_backup, destino)
        restaurados.append(rel)

    shutil.rmtree(ultimo, ignore_errors=True)
    return restaurados
