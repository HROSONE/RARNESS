"""Gerenciamento de sessão estilo Claude Code: Read-Before-Edit, Stale File Guard, Hooks, Safe Bash e Persistência."""
from __future__ import annotations

import hashlib
import html.parser
import ipaddress
import json
import re
import socket
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rarness.executor import run_command
from rarness.state import STATE_DIR_NAME
from rarness_security import resolve_project_path

SESSIONS_DIR_NAME = "sessions"
HOOKS_FILE_NAME = "hooks.json"

# Comandos Bash puramente de leitura ou execução de testes que não alteram estado arbitrário
_SAFE_BASH_PREFIXES = (
    "python -m unittest",
    "python3 -m unittest",
    "pytest",
    "git status",
    "git diff",
    "git log",
    "git branch",
    "git show",
    "ls",
    "pwd",
    "which",
    "flake8",
    "ruff check",
    "mypy",
    "cargo check",
    "cargo test",
    "go test",
)
_SHELL_META_DANGER = re.compile(r"(?:[;&|`$><]|\brm\b|\bmv\b|\bdd\b|\bchmod\b|\bchown\b)")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


@dataclass
class SessionTracker:
    """Rastreia arquivos lidos na sessão para impor Read-Before-Edit e detectar conflitos externos."""

    files_read_hash: dict[str, str] = field(default_factory=dict)
    files_modified: list[str] = field(default_factory=list)
    tool_history: list[dict[str, Any]] = field(default_factory=list)
    enforce_read_before_edit: bool = False

    def record_read(self, rel_path: str, content: str) -> None:
        self.files_read_hash[rel_path] = _sha256(content)

    def record_write(self, rel_path: str, content: str) -> None:
        self.files_read_hash[rel_path] = _sha256(content)
        if rel_path not in self.files_modified:
            self.files_modified.append(rel_path)

    def check_can_modify(self, root: Path, rel_path: str) -> str | None:
        """Retorna mensagem de erro se a edição violar Read-Before-Edit ou se o arquivo mudou externamente."""
        alvo = resolve_project_path(rel_path, root.resolve())
        if not alvo.is_file():
            return None

        atual_disk = alvo.read_text(encoding="utf-8", errors="replace")
        hash_atual = _sha256(atual_disk)

        if rel_path not in self.files_read_hash:
            if self.enforce_read_before_edit:
                return (
                    f"Guarda Read-Before-Edit (Claude Code): você precisa ler '{rel_path}' com "
                    "`read_file` antes de editá-lo ou sobrescrevê-lo."
                )
            return None

        hash_lido = self.files_read_hash[rel_path]
        if hash_lido != hash_atual:
            return (
                f"Conflito de arquivo obsoleto (Stale File Guard): '{rel_path}' foi alterado no disco "
                "desde a sua última leitura. Use `read_file` novamente antes de editar."
            )
        return None


def is_safe_readonly_bash(command: str) -> bool:
    """Verifica se um comando Bash é puramente de inspeção/teste e livre de metacaracteres perigosos."""
    limpo = command.strip()
    if not limpo or _SHELL_META_DANGER.search(limpo):
        return False
    return any(limpo == p or limpo.startswith(p + " ") for p in _SAFE_BASH_PREFIXES)


def run_hook(root: Path, event: str, tool_name: str, context: dict[str, Any]) -> tuple[bool, str]:
    """Executa hooks configurados em .rarness/hooks.json (eventos: PreToolUse, PostToolUse)."""
    hooks_path = root.resolve() / STATE_DIR_NAME / HOOKS_FILE_NAME
    if not hooks_path.is_file():
        return True, ""
    try:
        config = json.loads(hooks_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True, ""

    event_hooks = config.get(event) if isinstance(config, dict) else None
    if not isinstance(event_hooks, dict):
        return True, ""

    cmd_template = event_hooks.get(tool_name) or event_hooks.get("*")
    if not isinstance(cmd_template, str) or not cmd_template.strip():
        return True, ""

    safe_path = str(context.get("path", "")).replace('"', "").replace("'", "")
    cmd = cmd_template.replace("{path}", safe_path).replace("{tool}", tool_name)
    res = run_command(cmd, root.resolve(), timeout_s=30)
    saida = (res.stdout + "\n" + res.stderr).strip()
    return res.exit_code == 0, saida[:2000]


def save_session(
    root: Path,
    messages: list[dict[str, Any]],
    session_id: str = "latest",
    redact_fn=None,
) -> Path:
    """Salva o histórico da conversa em .rarness/sessions/<session_id>.json."""
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "-", session_id).strip("-") or "latest"
    pasta = root.resolve() / STATE_DIR_NAME / SESSIONS_DIR_NAME
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"{safe_id}.json"

    payload = {
        "session_id": safe_id,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "messages": messages,
    }
    raw_json = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if redact_fn is not None:
        raw_json = redact_fn(raw_json, root.resolve())
    destino.write_text(raw_json, encoding="utf-8")
    if safe_id != "latest":
        (pasta / "latest.json").write_text(raw_json, encoding="utf-8")
    return destino


def load_session(root: Path, session_id: str = "latest") -> list[dict[str, Any]] | None:
    """Carrega os turnos de uma sessão salva em .rarness/sessions/<session_id>.json."""
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "-", session_id).strip("-") or "latest"
    caminho = root.resolve() / STATE_DIR_NAME / SESSIONS_DIR_NAME / f"{safe_id}.json"
    if not caminho.is_file():
        return None
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        msgs = dados.get("messages") if isinstance(dados, dict) else None
        if isinstance(msgs, list) and msgs:
            return msgs
    except (OSError, json.JSONDecodeError):
        return None
    return None


def list_sessions(root: Path) -> list[str]:
    pasta = root.resolve() / STATE_DIR_NAME / SESSIONS_DIR_NAME
    if not pasta.is_dir():
        return []
    return sorted(p.stem for p in pasta.glob("*.json"))


class _HTMLTextExtractor(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip == 0:
            limpo = data.strip()
            if limpo:
                self.parts.append(limpo)


def _is_Blocked_host(hostname: str) -> bool:
    """Bloqueia localhost e IPs privados/loopback/link-local contra SSRF."""
    host = hostname.strip().lower()
    if host in {"localhost", "0.0.0.0", "127.0.0.1", "::1"} or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        pass
    try:
        resolved_ip = ipaddress.ip_address(socket.gethostbyname(host))
        return resolved_ip.is_private or resolved_ip.is_loopback or resolved_ip.is_link_local
    except Exception:
        return False


def safe_web_fetch(url: str, max_chars: int = 12000, timeout: float = 15.0) -> str:
    """Busca o conteúdo textual de uma URL HTTP/HTTPS pública (ferramenta WebFetch do Claude Code)."""
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL inválida: apenas http:// e https:// públicos são permitidos.")
    if _is_Blocked_host(parsed.hostname):
        raise ValueError("Acesso a hosts locais ou IPs privados é bloqueado por segurança (SSRF Guard).")

    req = urllib.request.Request(
        url.strip(),
        headers={"User-Agent": "RARNESS-DocFetcher/2.0"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(500_000).decode("utf-8", errors="replace")
        content_type = (resp.headers.get("Content-Type") or "").lower()

    if "html" in content_type or "<html" in raw[:500].lower():
        parser = _HTMLTextExtractor()
        parser.feed(raw)
        texto = "\n".join(parser.parts)
    else:
        texto = raw

    return texto[:max_chars] or "(Página sem conteúdo textual)"
