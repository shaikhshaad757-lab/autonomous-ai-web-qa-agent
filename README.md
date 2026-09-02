# HMS AI QA Agent

An autonomous QA testing agent for an **authorized** Hospital Management System
(HMS) test/staging environment. You give it a URL and login credentials; it
logs in, discovers the application, generates and runs test cases, reproduces
and classifies bugs, and produces a professional report.

> **Authorized use only.** This tool must only be pointed at systems you own
> or are explicitly authorized to test. On startup it requires you to type a
> confirmation phrase, and it disables destructive/mutating tests unless you
> explicitly mark the environment as `TEST` or `STAGING`.

## Status: Phase 1 (of 5)

This repo is being built incrementally, per the implementation plan below.
**Phase 1 is implemented now.** Later phases build on top of it without
breaking what's already working.

| Phase | Scope | Status |
|---|---|---|
| 1 | Ollama connection, config, Playwright, login, discovery, screenshots, console monitoring, basic test gen/exec, bug report | ✅ implemented |
| 2 | Negative/boundary/form/duplicate/navigation testing | ⏳ next |
| 3 | API testing, workflow testing, role-based testing, data integrity | ⏳ planned |
| 4 | Security checks, network-failure testing, responsive testing, multi-browser | ⏳ planned |
| 5 | Regression, coverage tracking, live dashboard, pause/resume, deeper AI analysis | ⏳ planned |

## What Phase 1 actually does

1. Reads HMS URL + credentials from **environment variables** (never CLI args,
   never hard-coded) or an interactive masked prompt.
2. Requires an explicit `I confirm this is an authorized test environment.`
   confirmation before touching the target.
3. Launches Playwright (Chromium), navigates to the URL, detects the login
   form heuristically (email/username + password fields), logs in, and
   verifies success (URL change / login form disappearance / dashboard
   heuristics).
4. Crawls the authenticated app breadth-first (sidebar/navbar links, up to a
   configurable depth/page limit) and builds a JSON "site map" of discovered
   pages, forms, and links.
5. On every page visited: takes a screenshot, captures console errors and
   failed network requests, and records basic page metadata (title, forms,
   inputs, buttons).
6. Generates a trivial first pass of "smoke" test cases (visit page, assert
   no fatal console error, assert page loaded) — this is the seed the later
   phases (negative testing, fuzzing, workflows, etc.) build on.
7. Runs those tests, and for any failure, re-runs it twice more before
   recording it as a bug (basic reproduction logic — the full reproduction
   engine comes in a later phase).
8. Writes `reports/run_<timestamp>/` containing `report.json`, `report.csv`,
   `report.html`, `screenshots/`, and `logs/`.

Everything downstream (Phases 2-5) reads/writes the same site map, database,
and report structures, so nothing here is throwaway scaffolding.

## Setup

```bash
cd hms-ai-qa-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
# edit .env with your target HMS URL and credentials
```

### `.env`

```env
HMS_URL=https://staging.your-hospital-system.example.com
HMS_EMAIL=tester@example.com
HMS_PASSWORD=change-me
HMS_ENVIRONMENT=TEST         # TEST | STAGING | PRODUCTION
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.1
```

`HMS_ENVIRONMENT=PRODUCTION` is accepted but the agent will refuse to run any
mutating/destructive test — Phase 1 doesn't mutate data at all yet, so this
mainly gates future phases.

### Ollama

Install Ollama separately (https://ollama.com) and pull a model:

```bash
ollama pull llama3.1
ollama serve   # if not already running as a service
```

## Run

```bash
python main.py
```

You'll be prompted to confirm you're authorized to test the target, then the
agent runs autonomously and prints a summary, e.g.:

```
Login: OK (2.1s)
Discovery: 14 pages, 6 forms, 42 links found
Tests executed: 14
Passed: 12   Failed: 2
Bugs confirmed: 1 (P2)
Report: reports/run_20260819_101500/report.html
```

## Project layout

```
app/
├── orchestrator/     # ties phases together, run state, coverage tracking
├── agents/
│   ├── discovery/     # app map building (Phase 1)
│   ├── browser/       # login + navigation helpers (Phase 1)
│   ├── functional/    # positive/CRUD test agent (Phase 1 seed, grows in P2)
│   ├── negative/       # Phase 2
│   ├── workflow/       # Phase 3
│   ├── api/             # Phase 3
│   ├── security/       # Phase 4
│   ├── data/            # Phase 3 (DB integrity)
│   ├── performance/    # optional, later
│   └── analyzer/        # bug dedup/severity/root-cause (Ollama-backed)
├── browser/           # Playwright BrowserManager wrapper
├── llm/                # OllamaClient (structured JSON + Pydantic validation)
├── database/           # SQLite persistence (run state, bugs, coverage)
├── reporting/          # report.json / .csv / .html generation
└── utils/              # logging, secret masking, config loading

config/config.yaml       # non-secret settings (timeouts, limits, viewport…)
.env.example              # secret settings template (never commit real .env)
reports/                  # generated at runtime, one folder per run
```

## Safety guarantees in Phase 1

- Credentials are read only from environment variables / masked prompt, never
  logged, never embedded in screenshots (login page screenshots are taken
  *before* filling the password field is echoed anywhere), and never sent to
  the LLM.
- No destructive actions are taken — Phase 1 only reads/navigates. Create,
  edit, and delete testing is introduced in Phase 2 behind an explicit
  `allow_mutations` config flag that defaults to `false` and is force-disabled
  when `HMS_ENVIRONMENT=PRODUCTION`.
- The agent will not run at all unless the user types the exact authorization
  confirmation string.
