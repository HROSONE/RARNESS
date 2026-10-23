"""Operações de arquivos do projeto com verificação de segurança do RARNESS."""
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from rarness_security import SENSITIVE_NAMES, resolve_project_path

IGNORED_DIR_NAMES = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".rarness"}


def _caminho_relativo(alvo: Path, raiz: Path) -> str:
    """Devolve o caminho relativo à raiz, sempre com '/'."""
    return alvo.relative_to(raiz).as_posix()


def _pasta_excluida(caminho: Path) -> bool:
    """Indica se uma pasta deve ser ignorada (nome excluído, sensível ou link simbólico)."""
    nome = caminho.name
    return nome in IGNORED_DIR_NAMES or nome.lower() in SENSITIVE_NAMES or caminho.is_symlink()


def _arquivos_do_projeto(root: Path) -> list[tuple[Path, str]]:
    """Lista os arquivos válidos do projeto como (caminho real, caminho relativo), ordenados.

    Pula pastas excluídas em qualquer nível, arquivos sensíveis e links simbólicos.
    """
    raiz = root.resolve()
    encontrados: list[tuple[Path, str]] = []
    for atual, pastas, nomes in os.walk(raiz):
        pasta_atual = Path(atual)
        pastas[:] = [p for p in pastas if not _pasta_excluida(pasta_atual / p)]
        for nome in nomes:
            caminho = pasta_atual / nome
            if nome.lower() in SENSITIVE_NAMES or caminho.is_symlink():
                continue
            if not caminho.is_file():
                continue
            encontrados.append((caminho, _caminho_relativo(caminho, raiz)))
    encontrados.sort(key=lambda item: item[1])
    return encontrados


def write_project_files(root: Path, files: dict[str, str], overwrite: bool = False) -> list[str]:
    """Grava arquivos (caminho relativo -> conteúdo UTF-8) dentro da raiz do projeto.

    Valida todos os caminhos antes de gravar qualquer um, para não deixar gravação parcial.
    Levanta FileExistsError se algum arquivo já existe e overwrite é False.
    Retorna os caminhos relativos gravados, na ordem recebida.
    """
    raiz = root.resolve()
    planejados: list[tuple[str, Path, str]] = []
    vistos: set[str] = set()

    for rel, conteudo in files.items():
        alvo = resolve_project_path(rel, raiz)
        if alvo == raiz:
            raise ValueError("Caminho inválido: a raiz do projeto não é um arquivo.")
        chave = _caminho_relativo(alvo, raiz)
        if chave in vistos:
            raise ValueError(f"Caminho duplicado após normalização: {chave}")
        vistos.add(chave)

        for pai in alvo.parents:
            if pai == raiz:
                break
            if pai.exists() and not pai.is_dir():
                raise ValueError(f"Um caminho pai não é pasta: {_caminho_relativo(pai, raiz)}")
        if alvo.is_dir():
            raise ValueError(f"Caminho é uma pasta, não um arquivo: {chave}")
        if alvo.exists() and not overwrite:
            raise FileExistsError(f"Arquivo já existe: {chave}")

        planejados.append((chave, alvo, conteudo))

    gravados: list[str] = []
    for chave, alvo, conteudo in planejados:
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_text(conteudo, encoding="utf-8")
        gravados.append(chave)
    return gravados


def create_backup(root: Path, rel_paths: list[str], backup_dir_name: str = ".rarness/backups") -> Path:
    """Copia os arquivos existentes para uma pasta com timestamp e devolve essa pasta.

    Arquivos inexistentes são ignorados. Todos os caminhos são validados antes da cópia.
    """
    raiz = root.resolve()
    pasta_base = resolve_project_path(backup_dir_name, raiz)

    origens: list[Path] = []
    for rel in rel_paths:
        origem = resolve_project_path(rel, raiz)
        if origem.is_file():
            origens.append(origem)

    destino = pasta_base / datetime.now().strftime("%Y%m%d-%H%M%S")
    destino.mkdir(parents=True, exist_ok=True)
    for origem in origens:
        alvo = destino / _caminho_relativo(origem, raiz)
        alvo.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origem, alvo)
    return destino


def build_zip(root: Path, out_path: Path) -> Path:
    """Gera um ZIP com os arquivos do projeto, sem exclusões, sensíveis, links ou o próprio ZIP."""
    lista = _arquivos_do_projeto(root)
    destino = out_path.resolve()
    destino.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zf:
        for caminho, rel in lista:
            if caminho == destino:
                continue
            zf.write(caminho, rel)
    return out_path


def list_project_files(root: Path) -> list[str]:
    """Lista, ordenados, os caminhos relativos dos arquivos válidos do projeto."""
    return [rel for _, rel in _arquivos_do_projeto(root)]
