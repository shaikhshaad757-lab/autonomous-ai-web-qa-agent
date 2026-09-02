"""Regression tests for crash-safe bug persistence.

Covers the requirement: bugs must be persisted to the DB immediately
after deduplication, BEFORE Ollama analysis runs — so a crash/timeout
during analysis never loses confirmed bug evidence, and reports are
never blocked on analysis completing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.database.db import Database


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "run.db")
    database.create_run("run1", "2026-01-01T00:00:00Z", "https://example.com", "TEST")
    yield database
    database.close()


def _sample_bug(**overrides) -> dict:
    base = {
        "bug_ref": "BUG-0001",
        "title": "Sample bug",
        "module": "Patients",
        "severity": "P2",
        "url": "https://example.com/patients",
        "steps": ["Navigate to /patients", "Observe failure"],
        "expected": "Page loads cleanly",
        "actual": "3 console errors",
        "reproducibility": "2/2 retries failed",
        "reproducibility_pct": 100,
        "screenshot_path": "/tmp/screenshot.png",
        "console_errors": ["TypeError: x is undefined"],
        "network_errors": ["GET /api/patients -> 404"],
        "what_was_tested": "Patients page smoke test",
        "affected": [{"url": "https://example.com/patients", "detail": "3 console errors"}],
    }
    base.update(overrides)
    return base


def test_add_bug_persists_with_null_analysis_fields(db: Database):
    """Immediately after add_bug(), before any analysis, the AI fields
    must be NULL — not missing, not an empty string, actually NULL —
    so the report layer can distinguish 'not yet analyzed' from
    'analyzed, evidence insufficient'."""
    db.add_bug("run1", _sample_bug(), "2026-01-01T00:00:01Z")

    data = db.fetch_all("run1")
    assert len(data["bugs"]) == 1
    bug = data["bugs"][0]

    assert bug["root_cause"] is None
    assert bug["recommended_fix"] is None
    assert bug["confidence"] is None
    assert bug["analyzed_at"] is None
    # Evidence fields must already be fully present at this point.
    assert bug["title"] == "Sample bug"
    assert json.loads(bug["steps_json"]) == ["Navigate to /patients", "Observe failure"]
    assert json.loads(bug["console_errors_json"]) == ["TypeError: x is undefined"]


def test_update_bug_analysis_only_touches_analysis_fields(db: Database):
    db.add_bug("run1", _sample_bug(), "2026-01-01T00:00:01Z")
    db.update_bug_analysis(
        "run1", "BUG-0001", "Root cause X", "Fix Y", 85, "2026-01-01T00:05:00Z",
    )

    bug = db.fetch_all("run1")["bugs"][0]
    assert bug["root_cause"] == "Root cause X"
    assert bug["recommended_fix"] == "Fix Y"
    assert bug["confidence"] == 85
    assert bug["analyzed_at"] == "2026-01-01T00:05:00Z"
    # Evidence fields must be unchanged by the update.
    assert bug["title"] == "Sample bug"
    assert bug["severity"] == "P2"


def test_bug_survives_when_analysis_is_never_run(db: Database):
    """Simulates a crash between persistence and analysis: add_bug()
    runs, update_bug_analysis() never does. The bug must still be fully
    queryable with evidence intact and AI fields NULL — this is the
    exact scenario that produced 0 real bugs + a manual recovery script
    in the run being fixed here."""
    db.add_bug("run1", _sample_bug(bug_ref="BUG-0042"), "2026-01-01T00:00:01Z")

    bug = db.fetch_all("run1")["bugs"][0]
    assert bug["bug_ref"] == "BUG-0042"
    assert bug["root_cause"] is None
    # Not a RECOVERY-XXXX style ID and not missing any evidence field.
    assert bug["bug_ref"].startswith("BUG-")
    assert bug["module"] == "Patients"
    assert bug["reproducibility_pct"] == 100


@pytest.mark.parametrize("field", ["console_errors", "network_errors", "affected"])
def test_none_valued_list_fields_do_not_crash_or_store_null_literal(db: Database, field):
    """Per spec: safely handle console_errors=None / network_errors=None
    (and, by the same logic, affected=None) without raising and without
    storing the JSON literal 'null' where a list is expected."""
    db.add_bug("run1", _sample_bug(**{field: None}), "2026-01-01T00:00:01Z")

    bug = db.fetch_all("run1")["bugs"][0]
    json_col = {
        "console_errors": "console_errors_json",
        "network_errors": "network_errors_json",
        "affected": "affected_json",
    }[field]
    assert json.loads(bug[json_col]) == []


def test_multiple_bugs_all_persisted_independently(db: Database):
    for i in range(5):
        db.add_bug("run1", _sample_bug(bug_ref=f"BUG-{i:04d}"), "2026-01-01T00:00:01Z")

    data = db.fetch_all("run1")
    assert len(data["bugs"]) == 5
    assert {b["bug_ref"] for b in data["bugs"]} == {f"BUG-{i:04d}" for i in range(5)}


def test_update_bug_analysis_on_unknown_bug_ref_is_a_safe_noop(db: Database):
    """Guards against a typo'd bug_ref silently corrupting an unrelated
    row or raising — it should simply match zero rows."""
    db.add_bug("run1", _sample_bug(), "2026-01-01T00:00:01Z")
    # No exception, no rows affected.
    db.update_bug_analysis("run1", "BUG-DOES-NOT-EXIST", "x", "y", 50)

    bug = db.fetch_all("run1")["bugs"][0]
    assert bug["root_cause"] is None