"""SQLite persistence for a test run.

Phase 1 stores: the run record, discovered pages, executed tests, and
confirmed bugs. Later phases add coverage tracking, workflow records, and
regression history on top of this same schema (new tables, not rewrites).
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
    screenshot_path TEXT,
    console_errors_json TEXT,
    network_errors_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs (id)
);
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
                    json.dumps(page.get("forms", [])),
                    json.dumps(page.get("links", [])),
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
        with self.cursor() as cur:
            cur.execute(
                """INSERT INTO bugs (run_id, bug_ref, title, module, severity, url, steps_json,
                   expected, actual, reproducibility, screenshot_path, console_errors_json,
                   network_errors_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    bug["bug_ref"],
                    bug["title"],
                    bug.get("module"),
                    bug["severity"],
                    bug.get("url"),
                    json.dumps(bug.get("steps", [])),
                    bug.get("expected"),
                    bug.get("actual"),
                    bug.get("reproducibility"),
                    bug.get("screenshot_path"),
                    json.dumps(bug.get("console_errors", [])),
                    json.dumps(bug.get("network_errors", [])),
                    created_at,
                ),
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
