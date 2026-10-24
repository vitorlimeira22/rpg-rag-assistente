"""Audita o lock; exceções específicas expiram e continuam visíveis no relatório."""

from datetime import date
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]


def assess(report: dict, policy: dict, today: date) -> tuple[list[str], list[str]]:
    approved, blocked = [], []
    valid = today <= date.fromisoformat(policy["expires_on"])
    exceptions = {(e["package"], e["version"], e["id"]) for e in policy["exceptions"]} if valid else set()
    for dependency in report["dependencies"]:
        if dependency.get("skip_reason"):
            blocked.append(f"{dependency['name']}: auditoria indisponível")
        for vuln in dependency.get("vulns", []):
            key = (dependency["name"], dependency["version"], vuln["id"])
            description = f"{key[0]} {key[1]}: {key[2]}"
            (approved if key in exceptions else blocked).append(description)
    if not valid:
        blocked.append("Revisão das exceções de segurança expirada; reavalie antes de publicar.")
    return sorted(set(approved)), sorted(set(blocked))


def main() -> int:
    try:
        policy = json.loads((ROOT / "evaluation/dependency-exceptions.json").read_text(encoding="utf-8"))
        with TemporaryDirectory(prefix="rpg-dependency-audit-") as directory:
            requirements = Path(directory) / "requirements.txt"
            subprocess.run([
                "uv", "export", "--locked", "--extra", "test", "--group", "audit",
                "--no-emit-project", "--format", "requirements-txt", "--output-file", str(requirements),
            ], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
            result = subprocess.run([
                sys.executable, "-m", "pip_audit", "--requirement", str(requirements),
                "--no-deps", "--disable-pip", "--progress-spinner", "off", "--format", "json",
            ], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180)
            if result.returncode not in {0, 1}:
                raise RuntimeError("Auditoria indisponível")
            report = json.loads(result.stdout)
        approved, blocked = assess(report, policy, date.today())
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
        print("Não foi possível concluir a auditoria. Confira a instalação de uv/pip-audit e a conexão.")
        return 2
    print(f"Dependências avaliadas: {len(report['dependencies'])}.")
    for item in approved:
        print(f"EXCEÇÃO LOCAL DOCUMENTADA (até {policy['expires_on']}): {item}")
    for item in blocked:
        print(f"BLOQUEIO: {item}")
    if not blocked:
        print("Nenhum alerta novo fora das exceções revisadas. Isso não significa ausência de vulnerabilidades.")
    return int(bool(blocked))


if __name__ == "__main__":
    sys.exit(main())
