"""Bug reproduction + severity classification (Phase 1 version).

Full spec calls for an LLM-assisted root-cause/dedup engine — that lands in
a later phase once there's enough test variety for dedup to matter. Phase 1
implements the reproduction and severity pieces deterministically so nothing
downstream is blocked on Ollama being reachable.
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urlparse

from app.agents.functional.functional_agent import FunctionalAgent, TestCase, TestResult

_bug_counter = itertools.count(1)

_SEVERITY_RANK = {"P0": 4, "P1": 3, "P2": 2, "P3": 1, "P4": 0}


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
    # Every test/page occurrence merged into this bug (including its own
    # first occurrence). Populated by deduplicate_bugs(); empty for a bug
    # that hasn't gone through dedup yet.
    affected: List[Dict[str, str]] = field(default_factory=list)
    # Stable signature used ONLY for dedup grouping — not persisted/reported
    # verbatim, just used to decide which bugs represent the same issue.
    signature: str = ""
    # Numeric reproducibility, parsed from `reproducibility` where possible
    # (e.g. "3/3 retries failed" -> 100). None when it can't be parsed
    # (e.g. "not retried") — never guessed.
    reproducibility_pct: Optional[int] = None
    # Root cause / recommended fix: populated by root_cause_analyzer.py
    # (Slice 3). None until analysis runs — the report renders these as
    # "Not available" rather than inventing anything.
    root_cause: Optional[str] = None
    recommended_fix: Optional[str] = None
    # Model confidence (0-100) in root_cause, when analysis was run and
    # evidence was judged sufficient. None otherwise.
    confidence: Optional[int] = None
    # Short, human-readable description of what this test actually
    # checked, derived only from existing TestCase data.
    what_was_tested: str = ""


def _guess_module(url: str) -> str:
    # crude path-segment heuristic; refined in Phase 3 using the full site map
    segments = [s for s in url.split("/") if s and "://" not in s and "." not in s]
    return segments[-1].replace("-", " ").title() if segments else "Unknown"


def _normalize_message(text: str) -> str:
    """Normalize a console/error message for dedup comparison: lowercase,
    collapse whitespace, and replace runs of digits with '#' so messages
    that differ only by a timestamp, line number, or record ID compare
    equal — without discarding the original text anywhere else."""
    text = (text or "").strip().lower()
    text = re.sub(r"\d+", "#", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _normalize_endpoint(url: str) -> str:
    """Normalize a failed-request URL to its path with numeric/ID-like
    segments and the query string stripped, so the same broken endpoint
    hit with different record IDs across pages compares equal."""
    try:
        path = urlparse(url).path
    except ValueError:
        path = url
    segments = path.split("/")
    normalized = [
        "#" if re.fullmatch(r"[0-9a-fA-F-]{1,36}", s) and any(c.isdigit() for c in s) else s
        for s in segments
    ]
    return "/".join(normalized)


def dedupe_console_messages(messages: List[str]) -> List[str]:
    """Deduplicate console/error messages using a normalized comparison
    key (whitespace/digit-insensitive), while preserving each distinct
    message's original text and first-seen order. Genuinely different
    messages are never collapsed together."""
    seen: set[str] = set()
    result: List[str] = []
    for msg in messages:
        key = _normalize_message(msg)
        if key not in seen:
            seen.add(key)
            result.append(msg)
    return result


def _signature_module(url: str) -> str:
    """Module classification used ONLY for dedup signatures — strips
    numeric/ID-like path segments so /patients/12, /patients/45, and
    /patients/99 all reduce to the same 'patients' module instead of
    colliding on the record ID (which _guess_module, used for display,
    does not do — it intentionally uses the LAST segment for a readable
    title, which is often the ID itself)."""
    try:
        path = urlparse(url).path
    except ValueError:
        path = url
    segments = [s for s in path.split("/") if s]
    meaningful = [
        s for s in segments
        if not (re.fullmatch(r"[0-9a-fA-F-]{1,36}", s) and any(c.isdigit() for c in s))
    ]
    if meaningful:
        return meaningful[0].lower()
    return segments[0].lower() if segments else "unknown"


def _bug_signature(result: TestResult) -> str:
    """Stable signature deciding whether two failing tests represent the
    SAME underlying issue: (ID-stripped) module + test category + the
    normalized dominant failure signal. Deliberately does NOT use title
    or severity, per requirement: never merge bugs solely because those
    match."""
    module = _signature_module(result.test_case.target_url)
    category = result.test_case.category
    if result.failed_requests:
        signal = f"network:{_normalize_endpoint(result.failed_requests[0])}"
    elif result.console_errors:
        signal = f"console:{_normalize_message(result.console_errors[0])}"
    else:
        # No console/network signal (e.g. a deep-audit finding) — fall back
        # to the normalized detail text so unrelated failures with no
        # console/network evidence don't all collapse into one bucket.
        signal = f"detail:{_normalize_message(result.detail or '')}"
    return f"{module}|{category}|{signal}"


# ======================================================================
# Root-cause-aware clustering (adds ONTO the Slice 1/2 per-module
# signature dedup above; never replaces it).
#
# The per-module signature dedup above only ever merges repeated
# occurrences of the SAME page/module — it correctly leaves e.g.
# /welcome and /patients as separate candidates even when both fail
# for what is actually the same systemic reason (a shared trailing-
# slash routing issue, or a shared cross-origin dependency failing on
# every page). That under-merging is what produces "26 near-identical
# 404 bugs" from one real underlying cause.
#
# The functions below detect a small, deliberately narrow set of such
# cross-page root-cause patterns from concrete request/response
# evidence. When a bug matches one, deduplicate_bugs() clusters it by
# that root-cause key INSTEAD OF the per-module signature — across
# modules/pages — so all matching occurrences become one bug. When a
# bug matches none of them (e.g. an unrelated API 404, or a bug with no
# network evidence at all such as a deep-page-audit finding), it falls
# straight back to the untouched per-module signature dedup above, so
# nothing not covered by these rules changes behavior at all.
# ======================================================================

def _bug_category(bug: "Bug") -> str:
    """Recover the test-category component from a Bug's stored
    signature (format "<module>|<category>|<signal>", built by
    _bug_signature() above). Used only to gate which root-cause rules
    may consider this bug — e.g. deep-page-audit findings (unnamed
    interactive elements, horizontal overflow, etc.) must never be
    pulled into a network/404 root-cause cluster. Returns "" if the
    signature isn't in the expected shape rather than raising, so a
    bug built outside the normal build_bug() flow (e.g. directly in a
    test) degrades to "no root-cause clustering" instead of crashing."""
    parts = (bug.signature or "").split("|", 2)
    return parts[1] if len(parts) >= 2 else ""


def _normalize_request_url(url: str) -> Dict[str, object]:
    """Break a request/page URL into the components used for root-cause
    comparison: same-origin-or-not, and a trailing-slash-insensitive
    path with the query string dropped entirely (this is where
    query-string noise like Next.js `?_rsc=...` cache-busting tokens
    gets removed for clustering purposes — the ORIGINAL url is never
    modified or lost, this is only ever used to build a comparison
    key). Never raises on a malformed/relative URL; degrades to
    treating it as an empty-origin relative path instead."""
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return {"origin": "", "path": url or "", "had_trailing_slash": False}
    path = parsed.path or ""
    had_trailing_slash = len(path) > 1 and path.endswith("/")
    return {
        "origin": f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else "",
        "path": path.rstrip("/") or "/",
        "had_trailing_slash": had_trailing_slash,
    }


def _root_cause_cluster_key(bug: "Bug") -> Optional[str]:
    """Return a cross-page root-cause cluster key for `bug` if — and
    only if — there is concrete request/response evidence of one of the
    recognized shared-cause patterns below. Returns None otherwise,
    which means "no opinion, use the normal per-module signature dedup"
    — this function never itself decides two bugs are UNRELATED, it
    only ever opts a bug INTO a narrower cross-page cluster.

    Deliberately conservative: a same-origin 404 whose failed-request
    path does not match the tested page's own path (e.g. an actual API
    endpoint returning 404) matches neither pattern below and is left
    alone, so it never gets merged with an unrelated missing route.
    """
    if not bug.network_errors:
        # No failed network request recorded at all — e.g. a deep-page
        # audit / accessibility finding. Root-cause network clustering
        # never applies; let it fall back to the per-module dedup.
        return None

    if _bug_category(bug) == "deep_page_audit":
        # Belt-and-braces: even if a deep-page-audit finding somehow
        # carries a network_errors entry, it must never be folded into
        # a network/404 root-cause cluster (requirement: keep the
        # unnamed-interactive / horizontal-overflow clusters separate
        # from HTTP/network clusters).
        return None

    evidence_text = " ".join(
        bug.console_errors + bug.network_errors + [bug.actual or ""]
    ).lower()

    failed = _normalize_request_url(bug.network_errors[0])
    target = _normalize_request_url(bug.url)

    same_origin = (
        not failed["origin"] or not target["origin"] or failed["origin"] == target["origin"]
    )

    # Pattern 1 — shared trailing-slash routing failure: the failed
    # request is (same-origin) the tested page's OWN path, differing
    # from it only by a trailing slash, and the failure is a 404.
    # e.g. /welcome -> /welcome/, /patients -> /patients/,
    # /settings/beds-management -> /settings/beds-management/.
    if (
        "404" in evidence_text
        and failed["path"]
        and target["path"]
        and failed["path"] == target["path"]
        and failed["had_trailing_slash"] != target["had_trailing_slash"]
        and same_origin
    ):
        return "rootcause:trailing-slash-routing"

    # Pattern 2 — shared cross-origin dependency: the failed request is
    # to a DIFFERENT origin than the page under test (e.g. every page
    # embeds a request to a shared third-party/shared-service host).
    # Distinct pages hitting the same cross-origin path are one shared
    # dependency issue, not one bug per page. Query-string noise (e.g.
    # differing `?_rsc=...` tokens) is dropped by _normalize_request_url
    # above, so different tokens on the same path still cluster.
    if failed["origin"] and target["origin"] and failed["origin"] != target["origin"]:
        return f"rootcause:cross-origin:{failed['origin']}{failed['path']}"

    return None


def _root_cause_title(root_key: str, fallback_title: str) -> str:
    """Human-readable title for a bug produced by root-cause
    clustering, describing the shared underlying issue rather than a
    single page's test name (e.g. "Shared trailing-slash routing
    failures across pages" instead of "Smoke: page loads cleanly —
    <page> — failed"). Falls back to the bug's own original title for
    any key this function doesn't recognize, rather than guessing."""
    if root_key == "rootcause:trailing-slash-routing":
        return (
            "Shared trailing-slash routing failures across pages "
            "(page URL returns 404 when requested with a trailing slash)"
        )
    if root_key.startswith("rootcause:cross-origin:"):
        origin_and_path = root_key[len("rootcause:cross-origin:"):]
        return f"Shared cross-origin request failure — {origin_and_path}"
    return fallback_title


def _root_cause_module(root_key: str, fallback_module: str) -> str:
    """Display module for a root-cause-clustered bug. A cluster spans
    many pages/modules, so the single page-derived module from the
    first occurrence would be misleading; use a label that reflects
    the shared nature of the cluster instead."""
    if root_key == "rootcause:trailing-slash-routing":
        return "Routing (multiple pages)"
    if root_key.startswith("rootcause:cross-origin:"):
        return "Shared dependency (cross-origin)"
    return fallback_module


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


def _parse_reproducibility_pct(reproducibility: str) -> Optional[int]:
    """Parse a numeric reproducibility percentage from strings like
    '3/3 retries failed' -> 100. Returns None for non-numeric forms
    (e.g. 'not retried') rather than guessing a value."""
    match = re.match(r"(\d+)\s*/\s*(\d+)", reproducibility or "")
    if not match:
        return None
    numerator, denominator = int(match.group(1)), int(match.group(2))
    if denominator == 0:
        return None
    return round((numerator / denominator) * 100)


def _what_was_tested(result: TestResult) -> str:
    """Human-readable description of what this test actually checked,
    taken directly from the TestCase's own name — never invented."""
    return result.test_case.name


def _reproduction_steps(result: TestResult) -> List[str]:
    """Build a concrete 3-step reproduction procedure from the TestCase's
    own data (category / element type / field name, where present) and
    what was actually observed in the result. Uses getattr() defensively
    since TestCase's optional fields (element_type, test_data) may or may
    not be populated depending on test category — never invents behavior
    that wasn't actually captured."""
    tc = result.test_case
    category = getattr(tc, "category", "") or ""
    element_type = getattr(tc, "element_type", None)
    test_data = getattr(tc, "test_data", None) or {}
    field_name = test_data.get("field_name") if isinstance(test_data, dict) else None

    step1 = f"Navigate to {tc.target_url}"

    if category == "smoke":
        step2 = "Allow the page to load fully"
    elif category == "deep_page_audit":
        step2 = "Allow the page to render and run an automated DOM audit"
    elif field_name and element_type:
        step2 = f"Locate the {element_type} field '{field_name}' and inspect/interact with it"
    elif element_type:
        step2 = f"Locate and interact with the {element_type} element being audited"
    elif field_name:
        step2 = f"Locate the field '{field_name}'"
    elif category:
        step2 = f"Perform the '{category.replace('_', ' ')}' check on this page"
    else:
        step2 = "Perform the automated check on this page"

    if result.failed_requests:
        step3 = f"Observe the failed network request: {result.failed_requests[0]}"
    elif result.console_errors:
        step3 = f"Observe the console error: {result.console_errors[0]}"
    elif result.detail:
        step3 = f"Observe the result: {result.detail}"
    else:
        step3 = "Observe the failure"

    return [step1, step2, step3]


def build_bug(result: TestResult, reproducibility: str) -> Bug:
    ref = f"BUG-{next(_bug_counter):04d}"
    return Bug(
        bug_ref=ref,
        title=f"{result.test_case.name} — failed",
        module=_guess_module(result.test_case.target_url),
        severity=classify_severity(result),
        url=result.test_case.target_url,
        steps=_reproduction_steps(result),
        expected="Page loads without console errors or failed network requests.",
        actual=result.detail,
        reproducibility=reproducibility,
        screenshot_path=result.screenshot_path,
        console_errors=dedupe_console_messages(result.console_errors),
        network_errors=result.failed_requests,
        affected=[],
        signature=_bug_signature(result),
        reproducibility_pct=_parse_reproducibility_pct(reproducibility),
        root_cause=None,
        recommended_fix=None,
        what_was_tested=_what_was_tested(result),
    )


def deduplicate_bugs(candidates: List[Bug]) -> List[Bug]:
    """Merge bugs representing the same underlying issue into one master
    bug per distinct issue, run BEFORE final bug IDs/report ordering are
    finalized. Every later match's test/page is appended to the master's
    `affected` list as evidence rather than creating a separate bug
    entry, and the numeric/qualitative reproducibility of the master
    (its own first occurrence) is preserved as-is — later merged
    occurrences never overwrite it.

    Two independent grouping keys feed the same merge loop:

    1. Root-cause key (_root_cause_cluster_key) — a small, deliberately
       narrow set of cross-page patterns backed by concrete request
       evidence (e.g. every page 404ing on its own URL+"/", or every
       page hitting the same failing cross-origin dependency). When a
       bug matches one of these, it is clustered by that pattern ACROSS
       modules/pages, and the resulting bug's title/module are rewritten
       to describe the shared cause instead of a single page's name.

    2. Per-module signature (bug.signature = module + test category +
       normalized dominant failure signal) — the original Slice 1/2
       dedup, unchanged, used whenever a bug matches no root-cause
       pattern. This is what keeps unrelated same-category failures
       (e.g. two different broken API endpoints, both 404s) from ever
       being merged just because they share a status code.

    Never merges bugs solely because their title or severity match.
    """
    merged: Dict[str, Bug] = {}
    order: List[str] = []

    for bug in candidates:
        root_key = _root_cause_cluster_key(bug)
        # root_key already carries its own "rootcause:..." prefix (see
        # _root_cause_cluster_key) so it can't collide with a
        # "signature:..." key from the per-module fallback below.
        key = root_key if root_key else f"signature:{bug.signature}"

        if key not in merged:
            merged[key] = bug
            order.append(key)
            if root_key:
                bug.title = _root_cause_title(root_key, bug.title)
                bug.module = _root_cause_module(root_key, bug.module)
            # The master's own occurrence counts as the first affected entry.
            bug.affected.append({"url": bug.url, "detail": bug.actual})
        else:
            master = merged[key]
            master.affected.append({"url": bug.url, "detail": bug.actual})
            master.console_errors = dedupe_console_messages(
                master.console_errors + bug.console_errors
            )
            for net_err in (bug.network_errors or []):
                if net_err not in master.network_errors:
                    master.network_errors.append(net_err)
            if _SEVERITY_RANK.get(bug.severity, -1) > _SEVERITY_RANK.get(master.severity, -1):
                master.severity = bug.severity

    return [merged[key] for key in order]


def sort_bugs_by_severity(bugs: List[Bug]) -> List[Bug]:
    """Return a NEW list of the given bugs ordered highest-severity first
    (P0 > P1 > P2 > P3 > P4), preserving relative order among bugs of
    equal severity (stable sort). Does not mutate the input list — used
    by root-cause analysis to decide which bugs get priority within the
    per-run analysis cap."""
    return sorted(bugs, key=lambda b: -_SEVERITY_RANK.get(b.severity, -1))
