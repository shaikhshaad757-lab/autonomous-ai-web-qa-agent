"""LLM-assisted root-cause analysis for confirmed bugs (Slice 3, item 1).

Uses ONLY the evidence already captured on a Bug object (title, steps,
expected/actual, console/network errors, reproducibility, affected-page
count). Never invents evidence not present on the Bug. If the model can't
determine a specific root cause from what's given, it must say so via
`evidence_sufficient=False` rather than guess.

Ollama failures — unreachable host, timeout, malformed/invalid structured
output — are caught here and turned into a safe "unavailable" result on
the bug. They must NEVER propagate up and fail the QA run.

All results are clearly labeled as AI-assisted analysis (see
AI_LABEL_PREFIX) so a project manager reading the report never mistakes
this for a verified, human-confirmed root cause.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import BaseModel, Field

from app.agents.analyzer.bug_analyzer import Bug
from app.llm.ollama_client import OllamaClient

logger = logging.getLogger("hms_qa_agent.analyzer.root_cause")

UNAVAILABLE_ROOT_CAUSE = "Root cause analysis unavailable."
UNAVAILABLE_FIX = "Recommended fix unavailable."
AI_LABEL_PREFIX = "[AI-assisted analysis] "


class RootCauseAnalysis(BaseModel):
    evidence_sufficient: bool
    root_cause: str
    recommended_fix: str
    confidence: Optional[int] = Field(default=None, ge=0, le=100)


def _build_prompt(bug: Bug) -> str:
    """Build a prompt strictly from the bug's own already-captured
    fields. Never adds external context, framework assumptions, or
    anything not present on the Bug object itself."""
    console = "\n".join(f"  - {c}" for c in bug.console_errors) or "  (none captured)"
    network = "\n".join(f"  - {n}" for n in bug.network_errors) or "  (none captured)"
    steps = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(bug.steps)) or "  (none recorded)"
    affected_count = len(bug.affected) if bug.affected else 1

    return f"""You are assisting a QA engineer by analyzing ONE confirmed
software bug found by an automated browser test. Base your analysis
STRICTLY on the evidence below. Do not assume anything about the
application's framework, backend, or implementation that is not stated
here. If the evidence given is not specific enough to name a plausible
root cause, set evidence_sufficient to false and say so plainly rather
than guessing.

Bug: {bug.title}
Module/page: {bug.module}
URL: {bug.url}
What was tested: {bug.what_was_tested}
Severity: {bug.severity}
Reproducibility: {bug.reproducibility}
Occurrences after deduplication: {affected_count}

Steps to reproduce:
{steps}

Expected result: {bug.expected}
Actual result: {bug.actual}

Console errors captured:
{console}

Network/API errors captured:
{network}

Respond with a JSON object with exactly these keys:
- evidence_sufficient (boolean): true ONLY if the evidence above is
  specific enough to name a plausible root cause; false if it is too
  generic (e.g. only "page loaded with errors", no concrete error text
  or failing endpoint).
- root_cause (string): a concise, plain-English explanation grounded
  ONLY in the evidence above. If evidence_sufficient is false, explain
  what additional evidence would be needed instead of guessing.
- recommended_fix (string): a concise, actionable suggestion for a
  developer. If evidence_sufficient is false, suggest what to capture
  next (e.g. reproduce manually and capture the full stack trace).
- confidence (integer 0-100, or null): your confidence in root_cause.
  Use null if evidence_sufficient is false.
"""


async def analyze_bug(ollama: OllamaClient, bug: Bug) -> str:
    """Analyze a single bug, mutating its root_cause, recommended_fix,
    and confidence fields in place. Returns one of:
    'analyzed' | 'insufficient' | 'unavailable'.

    Never raises — any failure (unreachable Ollama, timeout, malformed
    response) results in the bug being labeled
    'Root cause analysis unavailable.' instead.
    """
    try:
        prompt = _build_prompt(bug)
        result = await ollama.generate_structured(prompt, RootCauseAnalysis)
    except Exception as exc:  # intentionally broad: must never crash the run
        logger.warning("Root-cause analysis failed for %s: %s", bug.bug_ref, exc)
        bug.root_cause = UNAVAILABLE_ROOT_CAUSE
        bug.recommended_fix = UNAVAILABLE_FIX
        bug.confidence = None
        return "unavailable"

    if result.evidence_sufficient:
        bug.root_cause = AI_LABEL_PREFIX + result.root_cause
        bug.recommended_fix = AI_LABEL_PREFIX + result.recommended_fix
        bug.confidence = result.confidence
        return "analyzed"

    bug.root_cause = AI_LABEL_PREFIX + (
        result.root_cause
        or "Evidence captured is insufficient to determine a specific root cause."
    )
    bug.recommended_fix = AI_LABEL_PREFIX + (
        result.recommended_fix
        or "Gather additional evidence (e.g. full stack trace, network payload) before proposing a fix."
    )
    bug.confidence = None
    return "insufficient"


async def analyze_bugs(
    ollama: OllamaClient,
    bugs: List[Bug],
    max_bugs: int = 10,
    ollama_reachable: bool = True,
) -> dict:
    """Analyze up to `max_bugs` bugs from the front of `bugs` (the caller
    is expected to have already ordered them, e.g. by severity via
    sort_bugs_by_severity()). Bugs beyond the cap are left with whatever
    root_cause/recommended_fix they already had (None -> "Not available"
    in the report).

    If `ollama_reachable` is False (from a prior health check), skips
    calling Ollama entirely for the capped bugs and marks them
    unavailable directly, avoiding guaranteed-to-fail calls.

    Returns a summary dict {analyzed, insufficient, unavailable, skipped}
    for logging. Never raises.
    """
    to_analyze = bugs[:max_bugs]
    skipped = max(len(bugs) - len(to_analyze), 0)
    counts = {"analyzed": 0, "insufficient": 0, "unavailable": 0, "skipped": skipped}

    for bug in to_analyze:
        if not ollama_reachable:
            bug.root_cause = UNAVAILABLE_ROOT_CAUSE
            bug.recommended_fix = UNAVAILABLE_FIX
            bug.confidence = None
            counts["unavailable"] += 1
            continue

        try:
            status = await analyze_bug(ollama, bug)
        except Exception as exc:  # defense-in-depth beyond analyze_bug's own try/except
            logger.warning("Unexpected error analyzing %s: %s", bug.bug_ref, exc)
            bug.root_cause = UNAVAILABLE_ROOT_CAUSE
            bug.recommended_fix = UNAVAILABLE_FIX
            bug.confidence = None
            status = "unavailable"

        counts[status] += 1

    return counts
