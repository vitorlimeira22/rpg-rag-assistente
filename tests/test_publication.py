"""A barreira deve detectar o conteúdo que será commitado, sem revelar o segredo."""

import importlib.util
from pathlib import Path
import subprocess

import pytest


spec = importlib.util.spec_from_file_location(
    "publication", Path(__file__).resolve().parents[1] / "scripts" / "check_publication.py"
)
publication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publication)


@pytest.mark.parametrize("name", [
    ".env", ".env.production", "nested/.env.local", "data/book.txt", "output/ficha.pdf",
    "tmp/answer.json", "private.sqlite3-wal", "book.PDF", "credentials.json", "key.pem",
    "../outside.txt", "nested/.venv/config.txt",
])
def test_private_paths_are_blocked(name):
    assert publication.path_problem(name)


@pytest.mark.parametrize("name", [".env.example", "examples/demo_rules.pdf", "src/code.py", "uv.lock"])
def test_public_paths_are_allowed(name):
    assert publication.path_problem(name) is None


def test_secrets_are_reported_without_values():
    secret = b"gsk_" + b"a" * 45
    report = publication.content_problems(b"key=" + secret)
    assert report and all(secret.decode() not in item for item in report)
    assert publication.content_problems(b"other-value", (b"other-value",))


def test_example_cannot_enable_private_data_or_contain_credentials():
    assert publication.example_problems(b"GROQ_API_KEY=filled\nALLOW_PRIVATE_BOOK_TO_GROQ=true")
    assert not publication.example_problems(
        b"GROQ_API_KEY=\nALLOW_PRIVATE_BOOK_TO_GROQ=false\nSHEET_PDF_TEMPLATE=\"\""
    )


def test_staged_content_is_checked_even_if_working_copy_is_clean(tmp_path):
    def git(*args):
        return subprocess.check_call(["git", "-C", str(tmp_path), *args], stdout=subprocess.DEVNULL)
    git("init")
    example = tmp_path / ".env.example"
    example.write_text("GROQ_API_KEY=\nALLOW_PRIVATE_BOOK_TO_GROQ=false\n")
    file = tmp_path / "notes.txt"
    secret = "gsk_" + "b" * 45
    file.write_text(secret)
    git("add", ".")
    file.write_text("clean")
    assert not publication.audit(tmp_path)[1]
    errors = publication.audit(tmp_path, staged=True)[1]
    assert any("notes.txt" in error for error in errors)
    assert all(secret not in error for error in errors)


def test_force_added_environment_is_blocked(tmp_path):
    subprocess.check_call(["git", "-C", str(tmp_path), "init"], stdout=subprocess.DEVNULL)
    (tmp_path / ".env").write_text("GROQ_API_KEY=local-only")
    subprocess.check_call(["git", "-C", str(tmp_path), "add", "-f", ".env"])
    assert any("ambiente privado" in error for error in publication.audit(tmp_path, staged=True)[1])
