import importlib.util
from datetime import date
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "dependency_audit", Path(__file__).resolve().parents[1] / "scripts/check_dependencies.py"
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_exception_is_limited_to_package_version_and_review_date():
    policy = {"expires_on": "2026-11-24", "exceptions": [
        {"package": "example", "version": "1.0", "id": "ISSUE-1"}
    ]}
    report = {"dependencies": [{"name": "example", "version": "1.0", "vulns": [{"id": "ISSUE-1"}]}]}
    assert audit.assess(report, policy, date(2026, 10, 24)) == (["example 1.0: ISSUE-1"], [])
    assert audit.assess(report, policy, date(2026, 11, 25))[1]
    report["dependencies"][0]["version"] = "2.0"
    assert audit.assess(report, policy, date(2026, 10, 24))[1]


def test_new_findings_and_unavailable_audits_are_not_hidden():
    report = {"dependencies": [
        {"name": "other", "version": "1", "vulns": [{"id": "NEW-ISSUE"}]},
        {"name": "unknown", "skip_reason": "not found"},
    ]}
    approved, blocked = audit.assess(report, {"expires_on": "2026-11-24", "exceptions": []}, date(2026, 10, 24))
    assert not approved and len(blocked) == 2
