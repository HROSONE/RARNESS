"""Executor de comandos com limites de tempo, memória e tamanho de saída.

Usado pelos agentes do RARNESS para rodar comandos de shell dentro de um
diretório do projeto.

Atenção: isto NÃO é um sandbox. O comando roda com as mesmas permissões do
usuário, pode ler e gravar arquivos fora do projeto e acessar a rede. Os
limites aqui servem só para evitar travamentos e saídas gigantes.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import resource
except ImportError:  # Windows não tem o módulo resource
    resource = None

_POSIX = os.name == "posix"
_AVISO_MEMORIA_WINDOWS = "limite de memória não aplicado neste sistema"
_ESPERA_APOS_MATAR_S = 5


@dataclass
class CommandResult:
    """Resultado de um comando executado por run_command."""

    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float
    truncated: bool


def _criar_limitador_memoria(limite_bytes: int):
    """Devolve uma função para rodar no filho (preexec_fn) limitando o espaço de endereçamento."""

    def _aplicar() -> None:
        resource.setrlimit(resource.RLIMIT_AS, (limite_bytes, limite_bytes))

    return _aplicar


def _matar_processo(processo: subprocess.Popen) -> None:
    """Mata o processo e, no POSIX, todo o grupo dele."""
    try:
        if _POSIX:
            os.killpg(processo.pid, signal.SIGKILL)
        else:
            processo.kill()
    except OSError:
        try:
            processo.kill()
        except OSError:
            pass  # o processo já terminou


def _cortar(texto: str, limite: int) -> tuple[str, bool]:
    """Corta o texto no limite de caracteres e informa se houve corte."""
    if len(texto) > limite:
        return texto[:limite], True
    return texto, False


def run_command(
    command: str,
    cwd: Path,
    timeout_s: float = 120,
    max_output_chars: int = 20000,
    memory_mb: int | None = None,
) -> CommandResult:
    """Executa um comando de shell em cwd, com tempo e saída limitados.

    Nunca levanta exceção por causa do código de saída do comando. Levanta
    ValueError apenas para parâmetros inválidos (cwd que não é diretório,
    timeout_s, max_output_chars ou memory_mb não positivos).

    No POSIX o comando roda em um novo grupo de processos, para que o timeout
    mate também os processos filhos. No Windows só o processo principal é
    morto. O limite de memória só é aplicado no POSIX.
    """
    if not isinstance(command, str) or not command.strip():
        raise ValueError("comando deve ser um texto não vazio")

    diretorio = Path(cwd)
    if not diretorio.is_dir():
        raise ValueError(f"cwd não é um diretório existente: {diretorio}")

    if timeout_s <= 0:
        raise ValueError("timeout_s deve ser maior que zero")
    if max_output_chars <= 0:
        raise ValueError("max_output_chars deve ser maior que zero")
    if memory_mb is not None and memory_mb <= 0:
        raise ValueError("memory_mb deve ser maior que zero quando informado")

    opcoes: dict = {}
    avisos: list[str] = []
    if _POSIX:
        opcoes["start_new_session"] = True
        if memory_mb is not None and resource is not None:
            opcoes["preexec_fn"] = _criar_limitador_memoria(memory_mb * 1024 * 1024)
    elif memory_mb is not None:
        avisos.append(_AVISO_MEMORIA_WINDOWS)

    inicio = time.monotonic()
    try:
        processo = subprocess.Popen(
            command,
            shell=True,
            cwd=str(diretorio),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **opcoes,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as erro:
        avisos.append(f"falha ao iniciar comando: {erro}")
        return CommandResult(
            command=command,
            exit_code=None,
            stdout="",
            stderr="\n".join(avisos),
            timed_out=False,
            duration_s=time.monotonic() - inicio,
            truncated=False,
        )

    timed_out = False
    try:
        saida, erros = processo.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _matar_processo(processo)
        try:
            saida, erros = processo.communicate(timeout=_ESPERA_APOS_MATAR_S)
        except subprocess.TimeoutExpired:
            # Netos que escaparam do grupo ainda seguram os pipes.
            saida, erros = None, None
            avisos.append("saída pode estar incompleta: processos filhos ainda abrem os pipes")

    duracao = time.monotonic() - inicio

    texto_saida = (saida or b"").decode("utf-8", errors="replace")
    texto_erros = (erros or b"").decode("utf-8", errors="replace")
    if avisos:
        texto_erros = "\n".join(avisos) + ("\n" + texto_erros if texto_erros else "")

    texto_saida, cortou_saida = _cortar(texto_saida, max_output_chars)
    texto_erros, cortou_erros = _cortar(texto_erros, max_output_chars)

    return CommandResult(
        command=command,
        exit_code=None if timed_out else processo.returncode,
        stdout=texto_saida,
        stderr=texto_erros,
        timed_out=timed_out,
        duration_s=duracao,
        truncated=cortou_saida or cortou_erros,
    )


def format_result(result: CommandResult, max_chars: int = 4000) -> str:
    """Formata o resultado em texto curto para enviar ao modelo.

    O texto final nunca passa de max_chars caracteres.
    """
    if max_chars <= 0:
        raise ValueError("max_chars deve ser maior que zero")

    linhas: list[str] = []
    if result.timed_out:
        linhas.append(f"AVISO: tempo esgotado após {result.duration_s:.1f}s; processo encerrado.")
        linhas.append("Código de saída: não disponível (tempo esgotado)")
    else:
        linhas.append(f"Código de saída: {result.exit_code}")
    linhas.append(f"Duração: {result.duration_s:.2f}s")
    if result.truncated:
        linhas.append("AVISO: saída truncada por exceder o limite de captura.")
    linhas.append("STDOUT:")
    linhas.append(result.stdout.rstrip() or "(vazio)")
    linhas.append("STDERR:")
    linhas.append(result.stderr.rstrip() or "(vazio)")

    texto = "\n".join(linhas)
    if len(texto) > max_chars:
        aviso = f"\n[... texto cortado para caber em {max_chars} caracteres]"
        if max_chars <= len(aviso):
            return texto[:max_chars]
        texto = texto[: max_chars - len(aviso)] + aviso
    return texto
