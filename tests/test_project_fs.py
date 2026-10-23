import os
import re
import tempfile
import unittest
import zipfile
from pathlib import Path

from rarness.project_fs import (
    IGNORED_DIR_NAMES,
    build_zip,
    create_backup,
    list_project_files,
    write_project_files,
)


class ProjetoTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "projeto"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()


class GravacaoTests(ProjetoTestCase):
    def test_gravacao_basica_cria_pastas(self):
        gravados = write_project_files(self.root, {"src/app.py": "print('oi')\n", "README.txt": "ola"})

        self.assertEqual(gravados, ["src/app.py", "README.txt"])
        self.assertEqual((self.root / "src" / "app.py").read_text(encoding="utf-8"), "print('oi')\n")
        self.assertEqual((self.root / "README.txt").read_text(encoding="utf-8"), "ola")

    def test_recusa_env(self):
        with self.assertRaises(ValueError):
            write_project_files(self.root, {".env": "SEGREDO=1"})
        with self.assertRaises(ValueError):
            write_project_files(self.root, {"config/.env.local": "SEGREDO=1"})
        self.assertFalse((self.root / ".env").exists())

    def test_recusa_caminho_com_ponto_ponto(self):
        with self.assertRaises(ValueError):
            write_project_files(self.root, {"../fora.txt": "x"})
        self.assertFalse((self.base / "fora.txt").exists())

    def test_existente_levanta_erro_sem_gravacao_parcial(self):
        (self.root / "existe.txt").write_text("original", encoding="utf-8")

        with self.assertRaises(FileExistsError):
            write_project_files(self.root, {"novo.txt": "a", "existe.txt": "b"})

        self.assertFalse((self.root / "novo.txt").exists())
        self.assertEqual((self.root / "existe.txt").read_text(encoding="utf-8"), "original")

    def test_overwrite_substitui_arquivo(self):
        (self.root / "existe.txt").write_text("original", encoding="utf-8")

        gravados = write_project_files(self.root, {"existe.txt": "novo", "novo.txt": "a"}, overwrite=True)

        self.assertEqual(gravados, ["existe.txt", "novo.txt"])
        self.assertEqual((self.root / "existe.txt").read_text(encoding="utf-8"), "novo")
        self.assertEqual((self.root / "novo.txt").read_text(encoding="utf-8"), "a")

    def test_recusa_duplicados_apos_normalizacao(self):
        with self.assertRaises(ValueError):
            write_project_files(self.root, {"a.txt": "1", "./a.txt": "2"})
        self.assertFalse((self.root / "a.txt").exists())


class BackupTests(ProjetoTestCase):
    def test_backup_copia_existentes_e_ignora_inexistentes(self):
        (self.root / "a.txt").write_text("conteudo A", encoding="utf-8")
        (self.root / "pasta").mkdir()
        (self.root / "pasta" / "b.txt").write_text("conteudo B", encoding="utf-8")

        destino = create_backup(self.root, ["a.txt", "pasta/b.txt", "nao_existe.txt"])

        self.assertTrue(destino.is_dir())
        self.assertEqual(destino.parent, self.root / ".rarness" / "backups")
        self.assertRegex(destino.name, r"^\d{8}-\d{6}$")
        self.assertEqual((destino / "a.txt").read_text(encoding="utf-8"), "conteudo A")
        self.assertEqual((destino / "pasta" / "b.txt").read_text(encoding="utf-8"), "conteudo B")
        self.assertFalse((destino / "nao_existe.txt").exists())

    def test_backup_recusa_env(self):
        (self.root / ".env").write_text("SEGREDO=1", encoding="utf-8")
        with self.assertRaises(ValueError):
            create_backup(self.root, [".env"])


class ZipTests(ProjetoTestCase):
    def _montar_projeto(self):
        arquivos = {
            "src/app.py": "x = 1",
            "README.md": "docs",
            ".env": "SEGREDO=1",
            ".git/config": "git",
            "node_modules/pkg/index.js": "js",
            "pasta/__pycache__/m.pyc": "pyc",
        }
        for rel, conteudo in arquivos.items():
            caminho = self.root / rel
            caminho.parent.mkdir(parents=True, exist_ok=True)
            caminho.write_text(conteudo, encoding="utf-8")

    def test_zip_exclui_sensiveis_ignorados_e_o_proprio_zip(self):
        self._montar_projeto()
        saida = self.root / "projeto.zip"

        retorno = build_zip(self.root, saida)

        self.assertEqual(retorno, saida)
        with zipfile.ZipFile(saida) as zf:
            nomes = zf.namelist()
        self.assertEqual(sorted(nomes), ["README.md", "src/app.py"])

    def test_zip_fora_da_raiz(self):
        self._montar_projeto()
        saida = self.base / "fora.zip"

        build_zip(self.root, saida)

        with zipfile.ZipFile(saida) as zf:
            self.assertEqual(sorted(zf.namelist()), ["README.md", "src/app.py"])

    def test_lista_ordenada_e_sem_exclusoes(self):
        self._montar_projeto()
        (self.root / "b.txt").write_text("b", encoding="utf-8")
        (self.root / "a.txt").write_text("a", encoding="utf-8")

        lista = list_project_files(self.root)

        self.assertEqual(lista, sorted(lista))
        self.assertEqual(lista, ["README.md", "a.txt", "b.txt", "src/app.py"])
        for item in lista:
            partes = item.split("/")
            self.assertFalse(set(partes[:-1]) & IGNORED_DIR_NAMES)
            self.assertNotIn(partes[-1], {".env"})

    def test_link_simbolico_para_fora_nao_e_incluido(self):
        outro = tempfile.TemporaryDirectory()
        self.addCleanup(outro.cleanup)
        segredo_externo = Path(outro.name) / "externo.txt"
        segredo_externo.write_text("externo", encoding="utf-8")
        pasta_externa = Path(outro.name) / "pasta_externa"
        pasta_externa.mkdir()
        (pasta_externa / "dentro.txt").write_text("dentro", encoding="utf-8")
        (self.root / "normal.txt").write_text("ok", encoding="utf-8")

        try:
            os.symlink(segredo_externo, self.root / "link.txt")
            os.symlink(pasta_externa, self.root / "linkdir", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Sistema sem suporte a links simbólicos")

        lista = list_project_files(self.root)
        saida = self.base / "saida.zip"
        build_zip(self.root, saida)

        self.assertEqual(lista, ["normal.txt"])
        with zipfile.ZipFile(saida) as zf:
            self.assertEqual(zf.namelist(), ["normal.txt"])


if __name__ == "__main__":
    unittest.main()
