import hashlib
from pathlib import Path

from rpg_assistente import answer
from rpg_assistente import vector


def test_only_reviewed_demo_content_is_exempt_from_private_book_consent(tmp_path, monkeypatch):
    original = Path(answer.PROJECT_ROOT) / "examples/demo_rules.pdf"
    approved_bytes = original.read_bytes()
    assert hashlib.sha256(approved_bytes).hexdigest() == answer.PUBLIC_DEMO_SHA256
    demo = tmp_path / "examples/demo_rules.pdf"
    demo.parent.mkdir()
    demo.write_bytes(approved_bytes)
    monkeypatch.setattr(answer, "PROJECT_ROOT", tmp_path)
    book = {"pdf_path": str(demo), "sha256": answer.PUBLIC_DEMO_SHA256}
    assert answer._is_public_demo(book)
    demo.write_bytes(b"a different private book")
    assert not answer._is_public_demo(book)
    book["sha256"] = hashlib.sha256(demo.read_bytes()).hexdigest()
    assert not answer._is_public_demo(book)


def test_demo_missing_or_imported_from_other_path_is_not_automatically_public(tmp_path, monkeypatch):
    monkeypatch.setattr(answer, "PROJECT_ROOT", tmp_path)
    assert not answer._is_public_demo({"pdf_path": str(tmp_path / "elsewhere.pdf"), "sha256": answer.PUBLIC_DEMO_SHA256})
    assert not answer._is_public_demo(None)


def test_embedded_chroma_client_has_telemetry_disabled(tmp_path):
    from types import SimpleNamespace
    client = vector._client(SimpleNamespace(vector_path=tmp_path / "chroma"))
    assert client.get_settings().anonymized_telemetry is False
