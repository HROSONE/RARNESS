import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from rarness.executor import CommandResult, format_result, run_command


def _comando_python(codigo: str) -> str:
    """Monta um comando que roda o Python atual com o código dado, sem depender de `python` no PATH."""
    args = [sys.executable, "-c", codigo]
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return shlex.join(args)


class ExecutorBasicoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_comando_com_saida_zero(self):
        result = run_command(_comando_python("print('ok')"), self.root)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.strip(), "ok")
        self.assertFalse(result.timed_out)
        self.assertFalse(result.truncated)

    def test_codigo_de_saida_nao_nulo_nao_levanta_excecao(self):
        result = run_command(_comando_python("raise SystemExit(3)"), self.root)
        self.assertEqual(result.exit_code, 3)
        self.assertFalse(result.timed_out)

    def test_stderr_e_capturado_e_bytes_invalidos_viram_substituicao(self):
        codigo = "import sys; sys.stderr.write('erro'); sys.stdout.buffer.write(b'\\xff ok')"
        result = run_command(_comando_python(codigo), self.root)
        self.assertEqual(result.stderr, "erro")
        self.assertIn("� ok", result.stdout)

    def test_timeout_e_respeitado(self):
        inicio = time.monotonic()
        result = run_command(_comando_python("import time; time.sleep(10)"), self.root, timeout_s=1)
        elapsed = time.monotonic() - inicio
        self.assertTrue(result.timed_out)
        self.assertIsNone(result.exit_code)
        self.assertLess(elapsed, 8)

    def test_saida_grande_e_truncada(self):
        result = run_command(_comando_python("print('x' * 50000)"), self.root, max_output_chars=100)
        self.assertEqual(len(result.stdout), 100)
        self.assertTrue(result.truncated)
        self.assertEqual(result.exit_code, 0)

    def test_cwd_invalido_levanta_value_error(self):
        with self.assertRaises(ValueError):
            run_command("echo oi", self.root / "nao_existe")

    def test_cwd_que_e_arquivo_levanta_value_error(self):
        arquivo = self.root / "arquivo.txt"
        arquivo.write_text("x", encoding="utf-8")
        with self.assertRaises(ValueError):
            run_command("echo oi", arquivo)

    def test_parametros_invalidos_levantam_value_error(self):
        with self.assertRaises(ValueError):
            run_command("echo oi", self.root, timeout_s=0)
        with self.assertRaises(ValueError):
            run_command("echo oi", self.root, timeout_s=-1)
        with self.assertRaises(ValueError):
            run_command("echo oi", self.root, max_output_chars=0)
        with self.assertRaises(ValueError):
            run_command("echo oi", self.root, memory_mb=0)
        with self.assertRaises(ValueError):
            run_command("echo oi", self.root, memory_mb=-5)

    def test_comando_vazio_levanta_value_error(self):
        with self.assertRaises(ValueError):
            run_command("   ", self.root)


class FormatResultTests(unittest.TestCase):
    def _resultado(self, **campos):
        base = dict(
            command="cmd",
            exit_code=0,
            stdout="",
            stderr="",
            timed_out=False,
            duration_s=0.5,
            truncated=False,
        )
        base.update(campos)
        return CommandResult(**base)

    def test_contem_codigo_de_saida(self):
        texto = format_result(self._resultado(exit_code=3))
        self.assertIn("Código de saída: 3", texto)

    def test_contem_duracao(self):
        texto = format_result(self._resultado(duration_s=1.25))
        self.assertIn("1.25s", texto)

    def test_avisa_timeout(self):
        texto = format_result(self._resultado(exit_code=None, timed_out=True, duration_s=1.0))
        self.assertIn("tempo esgotado", texto)
        self.assertIn("Código de saída", texto)

    def test_avisa_truncamento(self):
        texto = format_result(self._resultado(truncated=True))
        self.assertIn("truncada", texto)

    def test_respeita_max_chars(self):
        texto = format_result(self._resultado(stdout="a" * 5000), max_chars=200)
        self.assertLessEqual(len(texto), 200)
        self.assertIn("cortado", texto)

    def test_max_chars_invalido_levanta_value_error(self):
        with self.assertRaises(ValueError):
            format_result(self._resultado(), max_chars=0)


@unittest.skipUnless(os.name == "posix", "limite de memória via RLIMIT_AS só é aplicado no POSIX")
class MemoriaPosixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_limite_aplicado_ao_filho_e_nao_ao_pai(self):
        import resource

        antes = resource.getrlimit(resource.RLIMIT_AS)
        codigo = "import resource; print(resource.getrlimit(resource.RLIMIT_AS)[0])"
        result = run_command(_comando_python(codigo), self.root, memory_mb=256)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(int(result.stdout.strip()), 256 * 1024 * 1024)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_AS), antes)


if __name__ == "__main__":
    unittest.main()
