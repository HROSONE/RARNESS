"""Ciclo de testes e correção com limite de tentativas e detecção de regressão.

O módulo não executa testes nem altera código: recebe dois callables.

- ``run_tests()`` roda a suíte e devolve um objeto com ``exit_code``,
  ``stdout`` e ``stderr`` (contrato ``ResultadoTestes``).
- ``apply_fix(falhas, saida_recente)`` tenta corrigir o código e devolve uma
  nota textual, ou ``None`` se não conseguiu corrigir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Protocol

# Captura o identificador estável de cada falha:
# - unittest: "FAIL: nome (módulo.Classe)" e "ERROR: nome (módulo.Classe)"
# - pytest: "FAILED caminho::teste" (e "ERROR caminho::teste" no resumo)
FAILURE_PATTERN = re.compile(
    r"^(?:FAIL: |ERROR: |FAILED |ERROR )([\w.]+ \([\w.]+\)|\S+::\S+)",
    re.MULTILINE,
)


class ResultadoTestes(Protocol):
    """Contrato mínimo do resultado devolvido por ``run_tests``."""

    exit_code: int | None
    stdout: str
    stderr: str


@dataclass
class Attempt:
    """Uma tentativa de correção e o resultado da rodada de testes que se seguiu."""

    number: int
    exit_code: int | None
    failures: set[str]
    regressions: set[str]
    note: str = ""


@dataclass
class RepairResult:
    """Resultado final do ciclo.

    ``reason`` é um de: "passou", "limite de tentativas", "regressão"
    ou "sem progresso".
    """

    passed: bool
    attempts: list[Attempt] = field(default_factory=list)
    reason: str = ""


def parse_failures(output: str) -> set[str]:
    """Devolve os identificadores de falhas encontrados em uma saída de testes."""
    return {m.group(1) for m in FAILURE_PATTERN.finditer(output)}


def detect_regressions(before: set[str], after: set[str]) -> set[str]:
    """Devolve as falhas que aparecem em ``after`` e não estavam em ``before``."""
    return set(after) - set(before)


def _saida(resultado: ResultadoTestes) -> str:
    """Junta stdout e stderr, pois unittest escreve falhas no stderr."""
    return f"{resultado.stdout or ''}\n{resultado.stderr or ''}"


def _passou(resultado: ResultadoTestes, falhas: set[str]) -> bool:
    return resultado.exit_code == 0 and not falhas


def repair_loop(
    run_tests: Callable[[], ResultadoTestes],
    apply_fix: Callable[[set[str], str], str | None],
    max_attempts: int = 3,
    baseline: set[str] | None = None,
) -> RepairResult:
    """Roda os testes e tenta corrigir falhas até o limite de tentativas.

    Regra de regressão: após cada correção, uma falha é regressão se aparece
    na nova rodada e não estava no ``baseline`` nem nas falhas da rodada
    anterior. Regressão encerra o ciclo com reason="regressão".

    Se ``apply_fix`` devolver ``None``, encerra com reason="sem progresso".
    Se o número de correções chegar a ``max_attempts``, encerra com
    reason="limite de tentativas".

    Levanta ``ValueError`` se ``max_attempts`` for menor que 1.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts deve ser pelo menos 1")

    conhecidas = set(baseline) if baseline is not None else set()

    resultado = run_tests()
    saida = _saida(resultado)
    falhas = parse_failures(saida)
    if _passou(resultado, falhas):
        return RepairResult(passed=True, attempts=[], reason="passou")

    tentativas: list[Attempt] = []
    for numero in range(1, max_attempts + 1):
        nota = apply_fix(set(falhas), saida)
        if nota is None:
            tentativas.append(
                Attempt(
                    number=numero,
                    exit_code=resultado.exit_code,
                    failures=set(falhas),
                    regressions=set(),
                    note="correção não aplicada",
                )
            )
            return RepairResult(passed=False, attempts=tentativas, reason="sem progresso")

        resultado = run_tests()
        saida = _saida(resultado)
        novas = parse_failures(saida)
        regressoes = detect_regressions(conhecidas | falhas, novas)
        tentativas.append(
            Attempt(
                number=numero,
                exit_code=resultado.exit_code,
                failures=novas,
                regressions=regressoes,
                note=nota,
            )
        )

        if _passou(resultado, novas):
            return RepairResult(passed=True, attempts=tentativas, reason="passou")
        if regressoes:
            return RepairResult(passed=False, attempts=tentativas, reason="regressão")
        falhas = novas

    return RepairResult(passed=False, attempts=tentativas, reason="limite de tentativas")
