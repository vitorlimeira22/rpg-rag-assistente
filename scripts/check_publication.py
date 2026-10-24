"""Verifica arquivos públicos sem imprimir credenciais nem consultar a rede.

Padrão: arquivos rastreados e não ignorados da árvore de trabalho.
--staged: conteúdo efetivo do índice Git, inclusive arquivos adicionados à força.
Não substitui revisão humana, secret scanning do GitHub ou rotação de chaves.
"""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRS = {"data", "tmp", "output", ".venv", "venv", "node_modules", ".git", "dist", "build"}
PRIVATE_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".log", ".bak", ".pem", ".key", ".p12", ".pfx"}
PATTERNS = (
    ("credencial Groq", re.compile(rb"gsk_[A-Za-z0-9]{20,}")),
    ("credencial OpenAI", re.compile(rb"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{24,}")),
    ("credencial Google", re.compile(rb"AIza[0-9A-Za-z_-]{30,}")),
    ("credencial GitHub", re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("chave de acesso AWS", re.compile(rb"(?:AKIA|ASIA)[A-Z0-9]{16}")),
    ("chave privada", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")),
)
PERSONAL_PATH = re.compile(rb"[A-Za-z]:[\\/]+Users[\\/]+[^\s'\"`<>]+", re.I)


def path_problem(name: str) -> str | None:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", name):
        return "caminho fora do repositório"
    parts = [p.lower() for p in path.parts]
    base = parts[-1]
    if any(p in PRIVATE_DIRS for p in parts):
        return "diretório local/privado"
    if base == ".env" or (base.startswith(".env.") and base != ".env.example"):
        return "arquivo de ambiente privado"
    if path.suffix.lower() in PRIVATE_SUFFIXES or re.search(r"\.(?:sqlite3?|db)-", base):
        return "banco, log, backup ou chave privada"
    if base.startswith(("credentials", "service-account")) and base.endswith(".json"):
        return "arquivo de credenciais"
    if path.suffix.lower() == ".pdf" and path.as_posix() != "examples/demo_rules.pdf":
        return "PDF sem autorização explícita na demonstração"
    return None


def content_problems(content: bytes, local_secrets: tuple[bytes, ...] = ()) -> list[str]:
    problems = [label for label, pattern in PATTERNS if pattern.search(content)]
    if any(secret in content for secret in local_secrets):
        problems.append("credencial presente no .env local")
    if PERSONAL_PATH.search(content):
        problems.append("caminho pessoal absoluto")
    return problems


def local_secret_values(root: Path) -> tuple[bytes, ...]:
    """Somente comparação em memória; os valores nunca entram no relatório."""
    env = root / ".env"
    if not env.is_file():
        return ()
    values = []
    for line in env.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*(?:export\s+)?([\w]+)\s*=\s*(.*?)\s*$", line)
        if not match or not re.search(r"KEY|TOKEN|SECRET|PASSWORD", match[1], re.I):
            continue
        value = match[2].strip().strip("\"'")
        if len(value) >= 12:
            values.append(value.encode())
    return tuple(values)


def example_problems(content: bytes) -> list[str]:
    lines = content.decode("utf-8").splitlines()
    settings = {}
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            settings[key.strip()] = value.strip().strip("\"'")
    problems = []
    for key, value in settings.items():
        if re.search(r"KEY|SECRET|PASSWORD|(?:^|_)TOKEN$", key, re.I) and value:
            problems.append("o exemplo deve deixar credenciais vazias")
    if settings.get("ALLOW_PRIVATE_BOOK_TO_GROQ") != "false":
        problems.append("o exemplo deve desativar o envio de livro privado")
    if settings.get("SHEET_PDF_TEMPLATE"):
        problems.append("o exemplo deve deixar o caminho do formulário vazio")
    return list(dict.fromkeys(problems))


def audit(root: Path, staged: bool = False) -> tuple[int, list[str]]:
    args = ["git", "-C", str(root), "ls-files", "-z", "--cached"]
    if not staged:
        args += ["--others", "--exclude-standard"]
    names = sorted(set(subprocess.check_output(args).decode("utf-8").split("\0")) - {""})
    if not names:
        return 0, ["Nenhum arquivo encontrado; prepare o índice Git antes de verificar --staged."]
    secrets = local_secret_values(root)
    errors = []
    example_found = False
    for name in names:
        problem = path_problem(name)
        if problem:
            errors.append(f"{name}: {problem}")
            continue
        path = root / name
        if staged:
            mode = subprocess.check_output(["git", "-C", str(root), "ls-files", "--stage", "--", name])
            if mode.startswith(b"120000 "):
                errors.append(f"{name}: link simbólico exige revisão manual")
                continue
            content = subprocess.check_output(["git", "-C", str(root), "show", f":{name}"])
        else:
            if path.is_symlink():
                errors.append(f"{name}: link simbólico exige revisão manual")
                continue
            if not path.is_file():
                continue
            content = path.read_bytes()
        problems = content_problems(content, secrets)
        if name == ".env.example":
            example_found = True
            problems += example_problems(content)
        errors.extend(f"{name}: {problem}" for problem in problems)
    if not example_found:
        errors.append(".env.example vazio e seguro precisa acompanhar o repositório.")
    return len(names), errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="conferir os bytes preparados no índice Git")
    args = parser.parse_args()
    try:
        count, errors = audit(ROOT, args.staged)
    except (OSError, subprocess.CalledProcessError):
        print("Não foi possível conferir o repositório Git. Nenhum conteúdo privado foi exibido.")
        return 2
    if errors:
        print("PUBLICAÇÃO BLOQUEADA:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"OK: {count} arquivos conferidos; nenhuma ocorrência nas verificações de publicação.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
