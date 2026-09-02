"""Bug reproduction + severity classification (Phase 1 version).

Full spec calls for an LLM-assisted root-cause/dedup engine — that lands in
a later phase once there's enough test variety for dedup to matter. Phase 1
implements the reproduction and severity pieces deterministically so nothing
downstream is blocked on Ollama being reachable.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import List, Optional

from app.agents.functional.functional_agent import FunctionalAgent, TestCase, TestResult

_bug_counter = itertools.count(1)


@dataclass
class Bug:
    bug_ref: str
    title: str
    module: str
    severity: str
    url: str
    steps: List[str]
    expected: str
    actual: str
    reproducibility: str
    screenshot_path: Optional[str]
    console_errors: List[str]
    network_errors: List[str]


def _guess_module(url: str) -> str:
    # crude path-segment heuristic; refined in Phase 3 using the full site map
    segments = [s for s in url.split("/") if s and "://" not in s and "." not in s]
    return segments[-1].replace("-", " ").title() if segments else "Unknown"


def classify_severity(result: TestResult) -> str:
    text = " ".join(result.console_errors + result.failed_requests).lower()
    if any(k in text for k in ("500", "unauthorized data", "cannot read", "fatal")):
        return "P1"
    if result.failed_requests:
        return "P2"
    if result.console_errors:
        return "P3"
    return "P4"


async def reproduce_failure(
    agent: FunctionalAgent, test_case: TestCase, max_retries: int
) -> tuple[bool, List[TestResult]]:
    """Re-run a failing test up to `max_retries` more times. Returns
    (confirmed, all_results). `confirmed` is True only if the failure
    reproduced on every attempt (simple, conservative definition for
    Phase 1 — flaky/intermittent failures are reported separately in a
    later phase rather than silently dropped)."""
    results: List[TestResult] = []
    for attempt in range(1, max_retries + 1):
        result = await agent.execute(test_case, attempt_index=attempt)
        results.append(result)
        if result.status == "passed":
            return False, results
    return True, results


def build_bug(result: TestResult, reproducibility: str) -> Bug:
    ref = f"BUG-{next(_bug_counter):04d}"
    return Bug(
        bug_ref=ref,
        title=f"{result.test_case.name} — failed",
        module=_guess_module(result.test_case.target_url),
        severity=classify_severity(result),
        url=result.test_case.target_url,
        steps=[f"Navigate to {result.test_case.target_url}", "Observe console/network errors"],
        expected="Page loads without console errors or failed network requests.",
        actual=result.detail,
        reproducibility=reproducibility,
        screenshot_path=result.screenshot_path,
        console_errors=result.console_errors,
        network_errors=result.failed_requests,
    )
