"""Funções de segurança de caminhos para o RARNESS."""
from pathlib import Path

SENSITIVE_NAMES = {".env", ".env.local", ".env.production", ".env.development"}

def resolve_project_path(raw_path: str, root: Path) -> Path:
    """Resolve um caminho e recusa qualquer destino fora da raiz do projeto."""
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("Caminho fora da pasta do projeto não permitido.") from exc
    if any(part.lower() in SENSITIVE_NAMES for part in resolved.parts):
        raise ValueError("Acesso a arquivos de ambiente (.env) bloqueado.")
    return resolved
