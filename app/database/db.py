"""SQLite persistence for a test run.

Stores: the run record, discovered pages, executed tests, and confirmed
bugs. A fresh run.db is created per run (see orchestrator.py), so no
migration path is needed when the schema changes between runs.

Crash-safety (Slice: bug persistence):
- add_bug() is called immediately after deduplication, BEFORE any Ollama
  analysis runs — bug evidence (title, steps, expected/actual, severity,
  screenshots, console/network errors, affected pages) is durable the
  moment a bug is confirmed, independent of whether AI analysis ever
  completes.
- root_cause / recommended_fix / confidence start NULL on insert and are
  filled in later, ONLY via update_bug_analysis(), which touches nothing
  else. A crash or timeout during analysis leaves the bug's evidence
  fully intact with those three fields simply still NULL.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    hms_url TEXT NOT NULL,
    environment TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    depth INTEGER,
    forms_json TEXT,
    links_json TEXT,
    screenshot_path TEXT,
    discovered_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs (id)
);

CREATE TABLE IF NOT EXISTS tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    target_url TEXT,
    status TEXT NOT NULL,
    detail TEXT,
    executed_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs (id)
);

CREATE TABLE IF NOT EXISTS bugs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    bug_ref TEXT NOT NULL,
    title TEXT NOT NULL,
    module TEXT,
    severity TEXT NOT NULL,
    url TEXT,
    steps_json TEXT,
    expected TEXT,
    actual TEXT,
    reproducibility TEXT,
    reproducibility_pct INTEGER,
    screenshot_path TEXT,
    console_errors_json TEXT,
    network_errors_json TEXT,
    what_was_tested TEXT,
    affected_json TEXT,
    root_cause TEXT,
    recommended_fix TEXT,
    confidence INTEGER,
    created_at TEXT NOT NULL,
    analyzed_at TEXT,
    FOREIGN KEY (run_id) REFERENCES runs (id)
);

CREATE INDEX IF NOT EXISTS idx_bugs_run_ref ON bugs (run_id, bug_ref);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    def create_run(self, run_id: str, started_at: str, hms_url: str, environment: str) -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO runs (id, started_at, hms_url, environment) VALUES (?, ?, ?, ?)",
                (run_id, started_at, hms_url, environment),
            )

    def finish_run(self, run_id: str, finished_at: str, status: str) -> None:
        with self.cursor() as cur:
            cur.execute(
                "UPDATE runs SET finished_at = ?, status = ? WHERE id = ?",
                (finished_at, status, run_id),
            )

    def add_page(self, run_id: str, page: Dict[str, Any], discovered_at: str) -> None:
        with self.cursor() as cur:
            cur.execute(
                """INSERT INTO pages (run_id, url, title, depth, forms_json, links_json,
                   screenshot_path, discovered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    page.get("url"),
                    page.get("title"),
                    page.get("depth", 0),
                    json.dumps(page.get("forms") or []),
                    json.dumps(page.get("links") or []),
                    page.get("screenshot_path"),
                    discovered_at,
                ),
            )

    def add_test(
        self,
        run_id: str,
        name: str,
        category: str,
        status: str,
        executed_at: str,
        target_url: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        with self.cursor() as cur:
            cur.execute(
                """INSERT INTO tests (run_id, name, category, target_url, status, detail, executed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, name, category, target_url, status, detail, executed_at),
            )

    def add_bug(self, run_id: str, bug: Dict[str, Any], created_at: str) -> None:
        """Persist a confirmed bug's full evidence. Intended to be called
        IMMEDIATELY after deduplication, before any Ollama analysis runs.
        root_cause/recommended_fix/confidence are always NULL at insert
        time — they're filled in later (if at all) via
        update_bug_analysis(). Every list-valued field is coalesced from
        None to [] before serialization, so callers may safely pass
        network_errors=None / console_errors=None / affected=None
        without this raising or storing the literal string "null"."""
        with self.cursor() as cur:
            cur.execute(
                """INSERT INTO bugs (
                       run_id, bug_ref, title, module, severity, url, steps_json,
                       expected, actual, reproducibility, reproducibility_pct,
                       screenshot_path, console_errors_json, network_errors_json,
                       what_was_tested, affected_json, root_cause, recommended_fix,
                       confidence, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    bug["bug_ref"],
                    bug["title"],
                    bug.get("module"),
                    bug["severity"],
                    bug.get("url"),
                    json.dumps(bug.get("steps") or []),
                    bug.get("expected"),
                    bug.get("actual"),
                    bug.get("reproducibility"),
                    bug.get("reproducibility_pct"),
                    bug.get("screenshot_path"),
                    json.dumps(bug.get("console_errors") or []),
                    json.dumps(bug.get("network_errors") or []),
                    bug.get("what_was_tested"),
                    json.dumps(bug.get("affected") or []),
                    None,
                    None,
                    None,
                    created_at,
                ),
            )

    def update_bug_analysis(
        self,
        run_id: str,
        bug_ref: str,
        root_cause: Optional[str],
        recommended_fix: Optional[str],
        confidence: Optional[int],
        analyzed_at: Optional[str] = None,
    ) -> None:
        """Update ONLY the AI-analysis fields of an already-persisted bug.
        Never touches evidence fields — this is the second half of the
        crash-safe two-phase write (add_bug now, update_bug_analysis
        later, possibly never)."""
        with self.cursor() as cur:
            cur.execute(
                """UPDATE bugs
                   SET root_cause = ?, recommended_fix = ?, confidence = ?, analyzed_at = ?
                   WHERE run_id = ? AND bug_ref = ?""",
                (root_cause, recommended_fix, confidence, analyzed_at, run_id, bug_ref),
            )

    def fetch_all(self, run_id: str) -> Dict[str, Any]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM pages WHERE run_id = ?", (run_id,))
            pages = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM tests WHERE run_id = ?", (run_id,))
            tests = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM bugs WHERE run_id = ?", (run_id,))
            bugs = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
            run_row = cur.fetchone()
            run = dict(run_row) if run_row else {}
        return {"run": run, "pages": pages, "tests": tests, "bugs": bugs}

    def close(self) -> None:
        self._conn.close()