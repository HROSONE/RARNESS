import unittest

from rarness.repair import (
    RepairResult,
    detect_regressions,
    parse_failures,
    repair_loop,
)

A = "test_a (tests.test_x.TestX)"
B = "test_b (tests.test_x.TestX)"
C = "test_c (tests.test_y.TestY)"
X = "test_x (tests.test_z.TestZ)"


class Resultado:
    """Resultado falso com os mesmos atributos do contrato de run_tests."""

    def __init__(self, exit_code, stdout="", stderr=""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


def resultado(exit_code, *falhas):
    """Monta saída no formato de unittest com as falhas informadas."""
    linhas = [f"FAIL: {falha}" for falha in falhas]
    linhas.append("OK" if exit_code == 0 else f"FAILED (failures={len(falhas)})")
    return Resultado(exit_code, stderr="\n".join(linhas) + "\n")


class Testes:
    """run_tests falso que segue um roteiro e falha se for chamado além dele."""

    def __init__(self, roteiro):
        self.roteiro = list(roteiro)
        self.chamadas = 0

    def __call__(self):
        if not self.roteiro:
            raise AssertionError("run_tests chamado mais vezes que o roteiro")
        self.chamadas += 1
        return self.roteiro.pop(0)


class Correcao:
    """apply_fix falso que devolve as notas em ordem e registra as chamadas."""

    def __init__(self, notas):
        self.notas = list(notas)
        self.recebidas = []

    def __call__(self, falhas, saida):
        self.recebidas.append((set(falhas), saida))
        return self.notas.pop(0) if self.notas else None


class ParseFalhasTests(unittest.TestCase):
    def test_le_saida_unittest(self):
        saida = (
            "======================================================================\n"
            f"FAIL: {A}\n"
            "----------------------------------------------------------------------\n"
            "Traceback (most recent call last):\n"
            f"ERROR: {C}\n"
            "Ran 3 tests\n"
            "FAILED (failures=1, errors=1)\n"
        )
        self.assertEqual(parse_failures(saida), {A, C})

    def test_le_saida_pytest(self):
        saida = (
            "FAILED tests/test_x.py::TestX::test_a - AssertionError: 1 != 2\n"
            "ERROR tests/test_y.py::test_b - ValueError\n"
            "=== 2 failed ===\n"
        )
        self.assertEqual(
            parse_failures(saida),
            {"tests/test_x.py::TestX::test_a", "tests/test_y.py::test_b"},
        )

    def test_saida_sem_falhas(self):
        self.assertEqual(parse_failures("Ran 5 tests\n\nOK\n"), set())

    def test_detect_regressions(self):
        self.assertEqual(detect_regressions({A}, {A, C}), {C})
        self.assertEqual(detect_regressions({A, B}, {B}), set())
        self.assertEqual(detect_regressions(set(), {A}), {A})


class RepairLoopTests(unittest.TestCase):
    def test_passa_de_primeira(self):
        testes = Testes([resultado(0)])
        correcao = Correcao([])

        res = repair_loop(testes, correcao)

        self.assertIsInstance(res, RepairResult)
        self.assertTrue(res.passed)
        self.assertEqual(res.reason, "passou")
        self.assertEqual(res.attempts, [])
        self.assertEqual(correcao.recebidas, [])
        self.assertEqual(testes.chamadas, 1)

    def test_corrige_na_segunda_tentativa(self):
        testes = Testes([resultado(1, A, B), resultado(1, B), resultado(0)])
        correcao = Correcao(["ajuste 1", "ajuste 2"])

        res = repair_loop(testes, correcao)

        self.assertTrue(res.passed)
        self.assertEqual(res.reason, "passou")
        self.assertEqual(len(res.attempts), 2)
        self.assertEqual(res.attempts[0].failures, {B})
        self.assertEqual(res.attempts[0].regressions, set())
        self.assertEqual(res.attempts[0].note, "ajuste 1")
        self.assertEqual(res.attempts[1].exit_code, 0)
        self.assertEqual(correcao.recebidas[0][0], {A, B})
        self.assertIn(f"FAIL: {A}", correcao.recebidas[0][1])
        self.assertEqual(correcao.recebidas[1][0], {B})

    def test_limite_de_tentativas(self):
        testes = Testes([resultado(1, A), resultado(1, A), resultado(1, A)])
        correcao = Correcao(["a", "b"])

        res = repair_loop(testes, correcao, max_attempts=2)

        self.assertFalse(res.passed)
        self.assertEqual(res.reason, "limite de tentativas")
        self.assertEqual(len(res.attempts), 2)
        self.assertEqual(len(correcao.recebidas), 2)
        self.assertEqual(testes.chamadas, 3)

    def test_regressao_interrompe(self):
        testes = Testes([resultado(1, A), resultado(1, A, C)])
        correcao = Correcao(["ajuste"])

        res = repair_loop(testes, correcao, max_attempts=3)

        self.assertFalse(res.passed)
        self.assertEqual(res.reason, "regressão")
        self.assertEqual(len(res.attempts), 1)
        self.assertEqual(res.attempts[0].regressions, {C})
        self.assertEqual(len(correcao.recebidas), 1)

    def test_baseline_evita_regressao_de_falha_conhecida(self):
        testes = Testes([resultado(1, A), resultado(1, A, X), resultado(0)])
        correcao = Correcao(["a", "b"])

        res = repair_loop(testes, correcao, baseline={X})

        self.assertTrue(res.passed)
        self.assertEqual(res.reason, "passou")
        self.assertEqual(res.attempts[0].regressions, set())

    def test_apply_fix_none_gera_sem_progresso(self):
        testes = Testes([resultado(1, A)])
        correcao = Correcao([None])

        res = repair_loop(testes, correcao)

        self.assertFalse(res.passed)
        self.assertEqual(res.reason, "sem progresso")
        self.assertEqual(len(res.attempts), 1)
        self.assertEqual(res.attempts[0].failures, {A})
        self.assertEqual(testes.chamadas, 1)

    def test_max_attempts_menor_que_um_levanta_erro(self):
        for valor in (0, -1):
            with self.subTest(max_attempts=valor):
                testes = Testes([])
                with self.assertRaises(ValueError):
                    repair_loop(testes, Correcao([]), max_attempts=valor)
                self.assertEqual(testes.chamadas, 0)


if __name__ == "__main__":
    unittest.main()
