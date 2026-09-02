"""Professional, concise HMS QA report generator.

Produces:
    report.json
    report.csv
    report.html
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from jinja2 import Template


_HTML_TEMPLATE = Template(
r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HMS QA Report — {{ run.id }}</title>

<style>
:root {
  --bg:#f5f7fa;
  --panel:#ffffff;
  --text:#172033;
  --muted:#697386;
  --line:#e5e9f0;
  --green:#16834b;
  --red:#c93636;
  --amber:#b7791f;
  --blue:#2563eb;
  --shadow:0 8px 30px rgba(20,30,50,.06);
}

* { box-sizing:border-box; }

body {
  margin:0;
  background:var(--bg);
  color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
  line-height:1.45;
}

.container {
  max-width:1400px;
  margin:auto;
  padding:32px;
}

header {
  background:var(--panel);
  border:1px solid var(--line);
  border-radius:18px;
  padding:28px;
  box-shadow:var(--shadow);
  margin-bottom:22px;
}

h1 {
  margin:0 0 6px;
  font-size:30px;
  letter-spacing:-.03em;
}

h2 {
  font-size:20px;
  margin:0 0 18px;
}

h3 {
  font-size:15px;
  margin:0 0 8px;
}

.meta {
  color:var(--muted);
  font-size:13px;
}

.status {
  display:inline-flex;
  align-items:center;
  padding:6px 11px;
  border-radius:999px;
  font-size:12px;
  font-weight:700;
  margin-top:14px;
  text-transform:uppercase;
}

.status.pass {
  color:var(--green);
  background:#eaf8f0;
}

.status.warn {
  color:var(--amber);
  background:#fff6df;
}

.status.fail {
  color:var(--red);
  background:#fdecec;
}

.grid {
  display:grid;
  grid-template-columns:repeat(5,minmax(0,1fr));
  gap:14px;
  margin-bottom:22px;
}

.card {
  background:var(--panel);
  border:1px solid var(--line);
  border-radius:14px;
  padding:18px;
  box-shadow:var(--shadow);
}

.num {
  font-size:28px;
  font-weight:750;
  letter-spacing:-.03em;
}

.label {
  margin-top:3px;
  color:var(--muted);
  font-size:11px;
  text-transform:uppercase;
  letter-spacing:.07em;
}

.green { color:var(--green); }
.red { color:var(--red); }
.amber { color:var(--amber); }
.blue { color:var(--blue); }

section {
  background:var(--panel);
  border:1px solid var(--line);
  border-radius:16px;
  padding:22px;
  margin-bottom:18px;
  box-shadow:var(--shadow);
}

.summary-grid {
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:18px;
}

.score {
  font-size:48px;
  font-weight:800;
  letter-spacing:-.05em;
}

.progress {
  height:9px;
  background:#edf0f5;
  border-radius:99px;
  overflow:hidden;
  margin-top:10px;
}

.progress > div {
  height:100%;
  background:var(--green);
}

table {
  width:100%;
  border-collapse:collapse;
}

th,td {
  text-align:left;
  padding:10px 11px;
  border-bottom:1px solid var(--line);
  font-size:13px;
  vertical-align:top;
}

th {
  color:var(--muted);
  background:#fafbfc;
  font-size:11px;
  text-transform:uppercase;
  letter-spacing:.05em;
}

tr:last-child td {
  border-bottom:0;
}

.badge {
  display:inline-block;
  padding:4px 8px;
  border-radius:7px;
  font-size:11px;
  font-weight:700;
  text-transform:uppercase;
}

.badge.pass {
  background:#eaf8f0;
  color:var(--green);
}

.badge.fail {
  background:#fdecec;
  color:var(--red);
}

.badge.skip {
  background:#f1f3f6;
  color:#697386;
}

.badge.warn {
  background:#fff6df;
  color:var(--amber);
}

.code {
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:12px;
  word-break:break-all;
}

ul {
  margin:8px 0 0 18px;
  padding:0;
}

.muted {
  color:var(--muted);
}

.alert {
  padding:13px 15px;
  border-radius:10px;
  margin-bottom:9px;
  background:#f8fafc;
  border:1px solid var(--line);
}

footer {
  color:var(--muted);
  text-align:center;
  font-size:11px;
  padding:10px;
}

@media(max-width:900px) {
  .grid { grid-template-columns:repeat(2,1fr); }
  .summary-grid { grid-template-columns:1fr; }
}

@media(max-width:600px) {
  .container { padding:15px; }
  .grid { grid-template-columns:1fr 1fr; }
  table { display:block; overflow-x:auto; }
}

.bug-card {
  border:1px solid var(--line);
  border-radius:14px;
  padding:18px;
  margin-bottom:16px;
  background:#fdfdfe;
}

.bug-card-head {
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:10px;
  margin-bottom:10px;
}

.bug-card-head h3 {
  margin:0;
  font-size:16px;
}

.bug-card h4 {
  font-size:12px;
  text-transform:uppercase;
  letter-spacing:.05em;
  color:var(--muted);
  margin:14px 0 6px;
}

table.bug-fields {
  margin-bottom:6px;
}

table.bug-fields th {
  width:180px;
  background:transparent;
  text-transform:none;
  letter-spacing:normal;
  font-size:12px;
  color:var(--muted);
}

.evidence-shot {
  max-width:100%;
  border:1px solid var(--line);
  border-radius:10px;
  margin-top:4px;
}
</style>
</head>

<body>
<div class="container">

<header>
  <h1>HMS QA Report</h1>
  <div class="meta">
    Run <span class="code">{{ run.id }}</span>
    · {{ run.hms_url }}
    · Environment: <strong>{{ run.environment }}</strong>
  </div>

  <div class="meta">
    Started: {{ run.started_at }}
    {% if run.finished_at %}
    · Finished: {{ run.finished_at }}
    {% endif %}
  </div>

  <div class="status {{ overall_class }}">
    {{ overall_status }}
  </div>
</header>

<div class="grid">
  <div class="card">
    <div class="num">{{ tests|length }}</div>
    <div class="label">Tests</div>
  </div>

  <div class="card">
    <div class="num green">{{ passed_count }}</div>
    <div class="label">Passed</div>
  </div>

  <div class="card">
    <div class="num red">{{ failed_count }}</div>
    <div class="label">Failed</div>
  </div>

  <div class="card">
    <div class="num blue">{{ pages|length }}</div>
    <div class="label">Pages</div>
  </div>

  <div class="card">
    <div class="num {% if bugs|length %}red{% else %}green{% endif %}">
      {{ bugs|length }}
    </div>
    <div class="label">Confirmed Bugs</div>
  </div>
</div>

<section>
  <h2>Executive Summary</h2>

  <div class="summary-grid">
    <div>
      <h3>Quality Score</h3>
      <div class="score">{{ quality_score }}%</div>
      <div class="progress">
        <div style="width:{{ quality_score }}%"></div>
      </div>
    </div>

    <div>
      <h3>Coverage</h3>
      <ul>
        <li>{{ pages|length }} pages discovered</li>
        <li>{{ tests|length }} tests executed</li>
        <li>{{ passed_count }} passed</li>
        <li>{{ failed_count }} failed</li>
        <li>{{ bugs|length }} confirmed bugs</li>
      </ul>
    </div>
  </div>
</section>

<section>
  <h2>Run Configuration</h2>

  <table>
    <tr><th>Environment</th><td>{{ run.environment }}</td></tr>
    <tr><th>Target</th><td class="code">{{ run.hms_url }}</td></tr>
    <tr><th>Run ID</th><td class="code">{{ run.id }}</td></tr>
    <tr><th>Started</th><td>{{ run.started_at }}</td></tr>
    <tr><th>Finished</th><td>{{ run.finished_at or "—" }}</td></tr>
  </table>
</section>

<section>
  <h2>Bug Summary</h2>

  {% if bugs %}
  <table>
    <tr>
      <th>Bug ID</th>
      <th>Severity</th>
      <th>Module</th>
      <th>Title</th>
      <th>Occurrences</th>
      <th>Status</th>
    </tr>

    {% for b in bugs %}
    <tr>
      <td class="code">{{ b.bug_ref }}</td>
      <td>
        <span class="badge fail">{{ b.severity }}</span>
      </td>
      <td>{{ b.module }}</td>
      <td>{{ b.title }}</td>
      <td>{{ b.affected|length if b.affected else 1 }}</td>
      <td><span class="badge fail">Confirmed</span></td>
    </tr>
    {% endfor %}
  </table>
  {% else %}
  <div class="alert">
    <strong>No confirmed bugs.</strong>
    <span class="muted">No reproducible defect was confirmed during this run.</span>
  </div>
  {% endif %}
</section>

<section>
  <h2>Detailed Bug Reports</h2>

  {% if bugs %}
    {% for b in bugs %}
    <div class="bug-card">
      <div class="bug-card-head">
        <h3>{{ b.bug_ref }} — {{ b.title }}</h3>
        <span class="badge fail">{{ b.severity }}</span>
      </div>

      <table class="bug-fields">
        <tr><th>Page / Module</th><td>{{ b.module }}</td></tr>
        <tr><th>URL</th><td class="code">{{ b.url }}</td></tr>
        <tr><th>What was tested</th><td>{{ b.what_was_tested or b.title }}</td></tr>
      </table>

      <h4>Steps to reproduce</h4>
      {% if b.steps %}
      <ol>
        {% for step in b.steps %}
        <li>{{ step }}</li>
        {% endfor %}
      </ol>
      {% else %}
      <div class="alert muted">Not available.</div>
      {% endif %}

      <table class="bug-fields">
        <tr><th>Expected</th><td>{{ b.expected or "Not available." }}</td></tr>
        <tr><th>Actual</th><td>{{ b.actual or "Not available." }}</td></tr>
      </table>

      <h4>Technical evidence</h4>
      <table class="bug-fields">
        <tr>
          <th>Console</th>
          <td>
            {% if b.console_errors %}
              <ul>{% for c in b.console_errors %}<li class="code">{{ c }}</li>{% endfor %}</ul>
            {% else %}
              <span class="muted">Not captured.</span>
            {% endif %}
          </td>
        </tr>
        <tr>
          <th>Network / API</th>
          <td>
            {% if b.network_errors %}
              <ul>{% for n in b.network_errors %}<li class="code">{{ n }}</li>{% endfor %}</ul>
            {% else %}
              <span class="muted">Not captured.</span>
            {% endif %}
          </td>
        </tr>
        <tr><th>HTTP status</th><td><span class="muted">Not captured.</span></td></tr>
        <tr><th>Response timing</th><td><span class="muted">Not captured.</span></td></tr>
        <tr>
          <th>Reproducibility</th>
          <td>
            {% if b.reproducibility_pct is not none %}
              {{ b.reproducibility_pct }}% <span class="muted">({{ b.reproducibility }})</span>
            {% else %}
              {{ b.reproducibility or "Not available." }}
            {% endif %}
          </td>
        </tr>
      </table>

      <h4>Screenshot</h4>
      {% if b.screenshot_rel %}
        <img class="evidence-shot" src="{{ b.screenshot_rel }}" alt="Evidence for {{ b.bug_ref }}">
      {% else %}
        <div class="alert muted">Not captured.</div>
      {% endif %}

      <h4>Affected pages / tests</h4>
      {% if b.affected %}
        <ul>
          {% for occurrence in b.affected %}
          <li><span class="code">{{ occurrence.url }}</span>{% if occurrence.detail %} — {{ occurrence.detail }}{% endif %}</li>
          {% endfor %}
        </ul>
      {% else %}
        <div class="alert muted">Not available.</div>
      {% endif %}

      <table class="bug-fields">
        <tr><th>Root cause</th><td>{{ b.root_cause or "Not available — LLM-assisted analysis not yet implemented." }}</td></tr>
        <tr><th>Recommended fix</th><td>{{ b.recommended_fix or "Not available — LLM-assisted analysis not yet implemented." }}</td></tr>
      </table>
    </div>
    {% endfor %}
  {% else %}
    <div class="alert">
      <strong>No confirmed bugs.</strong>
      <span class="muted">No reproducible defect was confirmed during this run.</span>
    </div>
  {% endif %}
</section>

<section>
  <h2>Test Results</h2>

  {% if tests %}
  <table>
    <tr>
      <th>#</th>
      <th>Name</th>
      <th>Category</th>
      <th>Status</th>
      <th>Detail</th>
    </tr>

    {% for t in tests %}
    <tr>
      <td>{{ loop.index }}</td>
      <td>{{ t.name }}</td>
      <td>{{ t.category }}</td>
      <td>
        <span class="badge
          {% if t.status == 'passed' %}pass
          {% elif t.status == 'failed' %}fail
          {% else %}skip{% endif %}">
          {{ t.status }}
        </span>
      </td>
      <td>{{ t.detail or "—" }}</td>
    </tr>
    {% endfor %}
  </table>
  {% else %}
  <div class="alert">No tests recorded.</div>
  {% endif %}
</section>

<section>
  <h2>Application Coverage</h2>

  <table>
    <tr>
      <th>Page</th>
      <th>Depth</th>
      <th>Forms</th>
      <th>Links</th>
      <th>Elements</th>
    </tr>

    {% for p in pages %}
    <tr>
      <td>
        <strong>{{ p.title or "Untitled" }}</strong><br>
        <span class="code muted">{{ p.url }}</span>
      </td>
      <td>{{ p.depth }}</td>
      <td>{{ p.form_count }}</td>
      <td>{{ p.link_count }}</td>
      <td>
        {% if p.interactive_count is defined %}
          {{ p.interactive_count }}
        {% else %}
          —
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>
</section>

<section>
  <h2>Data Flow & Persistence</h2>

  {% if dataflow %}
    <table>
      <tr>
        <th>URL</th>
        <th>Status</th>
        <th>Detail</th>
        <th>Persistence</th>
      </tr>

      {% for d in dataflow %}
      <tr>
        <td class="code">{{ d.url }}</td>
        <td>
          <span class="badge
            {% if d.status == 'passed' %}pass
            {% elif d.status == 'failed' %}fail
            {% else %}skip{% endif %}">
            {{ d.status }}
          </span>
        </td>
        <td>{{ d.detail }}</td>
        <td>
          {% if d.persistence_checks %}
            {% for key,value in d.persistence_checks.items() %}
              <div>{{ key }}: <strong>{{ value }}</strong></div>
            {% endfor %}
          {% else %}
            —
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </table>
  {% else %}
    <div class="alert">
      Data-flow results were not recorded for this run.
      <span class="muted">
        This section does not invent or infer results.
      </span>
    </div>
  {% endif %}
</section>

<section>
  <h2>Evidence & Diagnostics</h2>

  {% set error_count = namespace(value=0) %}
  {% set request_count = namespace(value=0) %}

  {% for t in tests %}
    {% if t.console_errors %}
      {% set error_count.value = error_count.value + (t.console_errors|length) %}
    {% endif %}
    {% if t.failed_requests %}
      {% set request_count.value = request_count.value + (t.failed_requests|length) %}
    {% endif %}
  {% endfor %}

  <table>
    <tr><th>Diagnostic</th><th>Count</th></tr>
    <tr><td>Console errors</td><td>{{ error_count.value }}</td></tr>
    <tr><td>Failed network requests</td><td>{{ request_count.value }}</td></tr>
    <tr><td>Pages with screenshots</td><td>{{ screenshot_count }}</td></tr>
  </table>
</section>

<section>
  <h2>Recommended Actions</h2>

  {% if bugs %}
    <div class="alert">
      <strong>Priority:</strong>
      Review and reproduce the confirmed bugs listed above.
    </div>
  {% endif %}

  {% if failed_count %}
    <div class="alert">
      <strong>Functional failures:</strong>
      Review failed tests and their evidence before release.
    </div>
  {% endif %}

  {% if not bugs and not failed_count %}
    <div class="alert">
      <strong>No confirmed functional defects in this run.</strong>
      Continue expanding coverage for deeper workflows and edge cases.
    </div>
  {% endif %}
</section>

<footer>
  HMS AI QA Agent · Automated test evidence · {{ run.id }}
</footer>

</div>
</body>
</html>
"""
)


def _relative_screenshot(output_dir: Path, screenshot_path: Optional[str]) -> Optional[str]:
    """Return a path relative to output_dir suitable for an <img src>, or
    None if there's no screenshot path or it isn't under output_dir.
    Never fabricates or guesses a path."""
    if not screenshot_path:
        return None
    try:
        p = Path(screenshot_path)
        if p.is_absolute():
            return str(p.relative_to(output_dir))
        return screenshot_path
    except ValueError:
        return None


def _quality_score(
    tests: List[Dict[str, Any]],
    bugs: List[Dict[str, Any]],
) -> int:
    if not tests:
        return 0

    passed = sum(
        1 for t in tests
        if t.get("status") == "passed"
    )

    base = (passed / len(tests)) * 100

    # Small penalty for confirmed bugs.
    penalty = min(len(bugs) * 5, 30)

    return max(0, min(100, round(base - penalty)))


def _overall_status(
    tests: List[Dict[str, Any]],
    bugs: List[Dict[str, Any]],
) -> tuple[str, str]:

    if any(
        str(b.get("severity", "")).upper()
        in {"P0", "CRITICAL"}
        for b in bugs
    ):
        return "FAIL", "fail"

    if bugs or any(
        t.get("status") == "failed"
        for t in tests
    ):
        return "WARNING", "warn"

    if not tests:
        return "WARNING", "warn"

    return "PASS", "pass"


def generate_reports(
    output_dir: Path,
    run: Dict[str, Any],
    pages: List[Dict],
    tests: List[Dict],
    bugs: List[Dict],
    dataflow: List[Dict[str, Any]] | None = None,
) -> None:

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    dataflow = dataflow or []

    # Attach a report-relative screenshot path to each bug so the HTML
    # template can embed it directly. Does not touch screenshot_path
    # itself — that stays exactly as captured by the test engine.
    for b in bugs:
        b["screenshot_rel"] = _relative_screenshot(output_dir, b.get("screenshot_path"))

    passed_count = sum(
        1 for t in tests
        if t.get("status") == "passed"
    )

    failed_count = sum(
        1 for t in tests
        if t.get("status") == "failed"
    )

    quality_score = _quality_score(
        tests,
        bugs,
    )

    overall_status, overall_class = _overall_status(
        tests,
        bugs,
    )

    screenshot_count = sum(
        1 for p in pages
        if p.get("screenshot_path")
    )

    report_data = {
        "run": run,
        "summary": {
            "overall_status": overall_status,
            "quality_score": quality_score,
            "total_tests": len(tests),
            "passed": passed_count,
            "failed": failed_count,
            "pass_rate": (
                round(
                    passed_count / len(tests),
                    4,
                )
                if tests
                else None
            ),
            "pages_discovered": len(pages),
            "bugs_confirmed": len(bugs),
            "screenshots": screenshot_count,
        },
        "pages": pages,
        "tests": tests,
        "bugs": bugs,
        "dataflow": dataflow,
    }

    (
        output_dir / "report.json"
    ).write_text(
        json.dumps(
            report_data,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    with open(
        output_dir / "report.csv",
        "w",
        newline="",
        encoding="utf-8",
    ) as fh:

        writer = csv.writer(fh)

        writer.writerow(
            [
                "type",
                "name_or_url",
                "category_or_module",
                "status_or_severity",
                "detail",
            ]
        )

        for t in tests:
            writer.writerow(
                [
                    "test",
                    t.get("name"),
                    t.get("category"),
                    t.get("status"),
                    t.get("detail") or "",
                ]
            )

        for b in bugs:
            writer.writerow(
                [
                    "bug",
                    b.get("title"),
                    b.get("module"),
                    b.get("severity"),
                    b.get("actual") or "",
                ]
            )

        for d in dataflow:
            writer.writerow(
                [
                    "dataflow",
                    d.get("url"),
                    "persistence",
                    d.get("status"),
                    d.get("detail") or "",
                ]
            )

    html = _HTML_TEMPLATE.render(
        run=run,
        pages=pages,
        tests=tests,
        bugs=bugs,
        dataflow=dataflow,
        passed_count=passed_count,
        failed_count=failed_count,
        quality_score=quality_score,
        overall_status=overall_status,
        overall_class=overall_class,
        screenshot_count=screenshot_count,
    )

    (
        output_dir / "report.html"
    ).write_text(
        html,
        encoding="utf-8",
    )
