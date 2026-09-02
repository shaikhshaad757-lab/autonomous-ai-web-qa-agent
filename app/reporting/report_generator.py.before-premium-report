"""Generates report.json / report.csv / report.html for a run."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List

from jinja2 import Template

_HTML_TEMPLATE = Template(
    """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>HMS QA Report — {{ run.id }}</title>
<style>
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; padding: 2rem; background: #f6f7f9; color: #1a1a1a; }
  h1 { margin-bottom: 0.25rem; }
  .meta { color: #666; margin-bottom: 2rem; }
  .cards { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 2rem; }
  .card { background: white; border-radius: 8px; padding: 1rem 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.08); min-width: 140px; }
  .card .num { font-size: 1.8rem; font-weight: 700; }
  .card .label { color: #666; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.03em; }
  .pass { color: #1a8a4a; } .fail { color: #c23b3b; }
  table { width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; margin-bottom: 2rem; }
  th, td { text-align: left; padding: 0.6rem 1rem; border-bottom: 1px solid #eee; font-size: 0.9rem; }
  th { background: #fafafa; }
  .sev-P0, .sev-P1 { color: #c23b3b; font-weight: 700; }
  .sev-P2 { color: #b8762a; font-weight: 600; }
  .sev-P3, .sev-P4 { color: #555; }
  section h2 { margin-top: 0; }
</style>
</head>
<body>
  <h1>HMS QA Report</h1>
  <div class="meta">
    Run <code>{{ run.id }}</code> · {{ run.hms_url }} · environment: {{ run.environment }}<br>
    Started {{ run.started_at }} — Finished {{ run.finished_at }}
  </div>

  <div class="cards">
    <div class="card"><div class="num">{{ tests|length }}</div><div class="label">Tests run</div></div>
    <div class="card"><div class="num pass">{{ passed_count }}</div><div class="label">Passed</div></div>
    <div class="card"><div class="num fail">{{ failed_count }}</div><div class="label">Failed</div></div>
    <div class="card"><div class="num">{{ pages|length }}</div><div class="label">Pages discovered</div></div>
    <div class="card"><div class="num fail">{{ bugs|length }}</div><div class="label">Confirmed bugs</div></div>
  </div>

  <section>
    <h2>Confirmed Bugs</h2>
    {% if bugs %}
    <table>
      <tr><th>ID</th><th>Title</th><th>Module</th><th>Severity</th><th>URL</th><th>Reproducibility</th></tr>
      {% for b in bugs %}
      <tr>
        <td>{{ b.bug_ref }}</td>
        <td>{{ b.title }}</td>
        <td>{{ b.module }}</td>
        <td class="sev-{{ b.severity }}">{{ b.severity }}</td>
        <td><a href="{{ b.url }}">{{ b.url }}</a></td>
        <td>{{ b.reproducibility }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <p>No confirmed bugs this run.</p>
    {% endif %}
  </section>

  <section>
    <h2>Discovered Pages</h2>
    <table>
      <tr><th>URL</th><th>Title</th><th>Depth</th><th>Forms</th><th>Links</th></tr>
      {% for p in pages %}
      <tr>
        <td><a href="{{ p.url }}">{{ p.url }}</a></td>
        <td>{{ p.title }}</td>
        <td>{{ p.depth }}</td>
        <td>{{ p.form_count }}</td>
        <td>{{ p.link_count }}</td>
      </tr>
      {% endfor %}
    </table>
  </section>

  <section>
    <h2>Test Results</h2>
    <table>
      <tr><th>Name</th><th>Category</th><th>Status</th><th>Detail</th></tr>
      {% for t in tests %}
      <tr>
        <td>{{ t.name }}</td>
        <td>{{ t.category }}</td>
        <td class="{{ 'pass' if t.status == 'passed' else 'fail' }}">{{ t.status }}</td>
        <td>{{ t.detail or '' }}</td>
      </tr>
      {% endfor %}
    </table>
  </section>
</body>
</html>
"""
)


def generate_reports(output_dir: Path, run: Dict[str, Any], pages: List[Dict], tests: List[Dict], bugs: List[Dict]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    passed_count = sum(1 for t in tests if t.get("status") == "passed")
    failed_count = sum(1 for t in tests if t.get("status") == "failed")

    report_data = {
        "run": run,
        "summary": {
            "total_tests": len(tests),
            "passed": passed_count,
            "failed": failed_count,
            "pass_rate": round(passed_count / len(tests), 4) if tests else None,
            "pages_discovered": len(pages),
            "bugs_confirmed": len(bugs),
        },
        "pages": pages,
        "tests": tests,
        "bugs": bugs,
    }

    (output_dir / "report.json").write_text(json.dumps(report_data, indent=2, default=str), encoding="utf-8")

    with open(output_dir / "report.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["type", "name_or_url", "category_or_module", "status_or_severity", "detail"])
        for t in tests:
            writer.writerow(["test", t.get("name"), t.get("category"), t.get("status"), t.get("detail") or ""])
        for b in bugs:
            writer.writerow(["bug", b.get("title"), b.get("module"), b.get("severity"), b.get("actual") or ""])

    html = _HTML_TEMPLATE.render(
        run=run, pages=pages, tests=tests, bugs=bugs,
        passed_count=passed_count, failed_count=failed_count,
    )
    (output_dir / "report.html").write_text(html, encoding="utf-8")
