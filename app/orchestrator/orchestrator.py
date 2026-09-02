"""HMS AI QA Agent orchestrator.

LOGIN -> DISCOVER -> GENERATE TESTS -> EXECUTE -> REPRODUCE FAILURES
-> BUILD BUGS -> DEDUPLICATE -> PERSIST EVIDENCE -> AI ANALYSIS
-> UPDATE ANALYSIS FIELDS -> REPORT.

Phase 2 adds safe functional UI audits while keeping destructive
and state-changing actions disabled.

Crash-safety (Slice: bug persistence, matches db.py/ollama_client.py):
Bug evidence is written to the DB via db.add_bug() immediately after
deduplication — BEFORE analyze_bugs() (the Ollama phase) runs. If the
process is killed or times out during AI analysis, every confirmed bug's
full evidence is already durable; only the root_cause/recommended_fix/
confidence fields (added via db.update_bug_analysis() afterward) may be
missing, and report generation already treats those as "Not available"
rather than inventing anything. Report generation itself is reached in a
`finally`-adjacent path regardless of whether analysis succeeded, so an
Ollama outage never prevents report.html/json/csv from being written.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from app.agents.dataflow.dataflow_agent import DataFlowAgent
from app.agents.analyzer.bug_analyzer import (
    build_bug,
    deduplicate_bugs,
    reproduce_failure,
    sort_bugs_by_severity,
)
from app.agents.analyzer.root_cause_analyzer import analyze_bugs
from app.agents.discovery.discovery_agent import DiscoveryAgent
from app.agents.functional.functional_agent import FunctionalAgent
from app.browser.browser_manager import BrowserManager
from app.database.db import Database
from app.llm.ollama_client import OllamaClient
from app.reporting.report_generator import generate_reports
from app.utils.config import AppConfig, Secrets
from app.utils.logger import register_secret, setup_logging

logger = logging.getLogger("hms_qa_agent.orchestrator")

# Root-cause analysis is capped to the N highest-severity bugs per run to
# bound added latency. Kept as a module-level constant (rather than
# buried in a call site) so it's visible from one place and easy to wire
# into config later if needed.
MAX_ANALYSIS_BUGS = 10


class Orchestrator:

    def __init__(
        self,
        secrets: Secrets,
        app_config: AppConfig,
        project_root: Path,
    ) -> None:

        self.secrets = secrets
        self.config = app_config
        self.project_root = project_root

        self.run_id = (
            f"run_"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_"
            f"{uuid.uuid4().hex[:6]}"
        )

        self.run_dir = (
            project_root
            / app_config.reporting.output_dir
            / self.run_id
        )

        self.screenshots_dir = self.run_dir / "screenshots"
        self.logs_dir = self.run_dir / "logs"
        self.bugs_dir = self.run_dir / "bugs"

        for directory in (
            self.run_dir,
            self.screenshots_dir,
            self.logs_dir,
            self.bugs_dir,
        ):
            directory.mkdir(
                parents=True,
                exist_ok=True,
            )

        # Register credentials with the log redaction system.
        register_secret(
            secrets.hms_email,
            secrets.hms_password,
        )

        self.logger = setup_logging(
            self.logs_dir
        )

        self.db = Database(
            self.run_dir / "run.db"
        )

        self.ollama = OllamaClient(
            host=secrets.ollama_host,
            model=secrets.ollama_model,
            temperature=app_config.llm.temperature,
            max_tokens=app_config.llm.max_tokens,
            timeout_seconds=app_config.llm.request_timeout_seconds,
            connect_timeout_seconds=app_config.llm.connect_timeout_seconds,
            max_retries=app_config.llm.max_retries,
        )

    # ================================================================
    # MAIN RUN
    # ================================================================

    async def run(self) -> dict:

        started_at = (
            datetime.now(timezone.utc)
            .isoformat()
        )

        self.db.create_run(
            self.run_id,
            started_at,
            self.secrets.hms_url,
            self.secrets.environment,
        )

        self.logger.info(
            "=== HMS AI QA Agent — run %s ===",
            self.run_id,
        )

        self.logger.info(
            "Target: %s (environment=%s)",
            self.secrets.hms_url,
            self.secrets.environment,
        )

        # ------------------------------------------------------------
        # Mutation safety
        # ------------------------------------------------------------

        if not self.config.testing.allow_mutations:

            self.logger.info(
                "Mutation testing disabled. "
                "Safe/read-only testing mode is active."
            )

        if not self.secrets.is_mutation_allowed_environment():

            self.logger.warning(
                "HMS_ENVIRONMENT=%s — "
                "destructive/mutating tests remain "
                "force-disabled.",
                self.secrets.environment,
            )

        # ------------------------------------------------------------
        # Ollama health
        # ------------------------------------------------------------

        ollama_ok = await self.ollama.health_check()
        # Stored so _test_and_analyze() can skip guaranteed-to-fail
        # root-cause analysis calls if Ollama was already unreachable here.
        self.ollama_ok = ollama_ok

        if ollama_ok:

            self.logger.info(
                "Ollama reachable at %s (model=%s).",
                self.secrets.ollama_host,
                self.secrets.ollama_model,
            )

        else:

            self.logger.warning(
                "Ollama not reachable at %s — "
                "continuing without LLM-assisted analysis.",
                self.secrets.ollama_host,
            )

        # ------------------------------------------------------------
        # Browser
        # ------------------------------------------------------------

        browser = BrowserManager(
            headless=self.config.browser.headless,
            viewport={
                "width": self.config.browser.viewport.width,
                "height": self.config.browser.viewport.height,
            },
            navigation_timeout_ms=(
                self.config.browser.navigation_timeout_ms
            ),
            action_timeout_ms=(
                self.config.browser.action_timeout_ms
            ),
        )

        await browser.start()

        status = "failed"
        bugs = []
        dataflow_results = []
        reports_written = False

        try:

            # --------------------------------------------------------
            # LOGIN
            # --------------------------------------------------------

            login_ok = await self._login(
                browser
            )

            if not login_ok:

                self.logger.error(
                    "Login could not be verified. "
                    "Aborting run."
                )

                status = "login_failed"

            else:

                # ----------------------------------------------------
                # DISCOVERY
                # ----------------------------------------------------

                pages = await self._discover(
                    browser
                )

                # ----------------------------------------------------
                # TESTING
                #
                # Bug evidence is persisted to the DB inside
                # _test_and_analyze() itself, BEFORE Ollama analysis
                # runs — so even if the block below (report writing)
                # never executes because of an unrelated crash, bug
                # evidence for this run is already durable.
                # ----------------------------------------------------

                tests, bugs, dataflow_results = (
                    await self._test_and_analyze(
                        browser,
                        pages,
                    )
                )

                status = "completed"

        except Exception as exc:

            self.logger.exception(
                "Unhandled error during QA run: %s",
                exc,
            )

            status = "failed"

        finally:

            # ----------------------------------------------------
            # REPORTS
            #
            # Always attempt to write reports, even if the run above
            # failed partway through — bugs/dataflow_results reflect
            # whatever was actually collected. If Ollama analysis
            # failed or was skipped, root_cause/recommended_fix on
            # each bug are simply None, which report_generator.py
            # renders as "Not available" rather than inventing a
            # cause. This satisfies the requirement that Ollama
            # failure must never prevent report generation.
            # ----------------------------------------------------

            try:
                self._write_reports(bugs, dataflow_results)
                reports_written = True
            except Exception as exc:
                self.logger.exception(
                    "Report generation failed: %s",
                    exc,
                )

            await browser.close()

            finished_at = (
                datetime.now(timezone.utc)
                .isoformat()
            )

            self.db.finish_run(
                self.run_id,
                finished_at,
                status,
            )

            self.db.close()

        return {
            "run_id": self.run_id,
            "status": status,
            "report_dir": str(self.run_dir),
            "reports_written": reports_written,
        }

    # ================================================================
    # LOGIN
    # ================================================================

    async def _login(
        self,
        browser: BrowserManager,
    ) -> bool:

        await browser.goto(
            self.secrets.hms_url
        )

        pre_login_url = browser.page.url

        form_found = (
            await browser.detect_and_fill_login(
                self.secrets.hms_email,
                self.secrets.hms_password,
            )
        )

        if not form_found:

            self.logger.info(
                "No login form detected — "
                "assuming already authenticated "
                "or no authentication required."
            )

            return True

        ok = await browser.verify_login_success(
            pre_login_url
        )

        self.logger.info(
            "Login %s.",
            "verified" if ok else "could not be verified",
        )

        return ok

    # ================================================================
    # DISCOVERY
    # ================================================================

    async def _discover(
        self,
        browser: BrowserManager,
    ):

        discovery = DiscoveryAgent(
            browser=browser,
            base_url=self.secrets.hms_url,
            max_pages=self.config.discovery.max_pages,
            max_depth=self.config.discovery.max_depth,
            screenshot_dir=str(
                self.screenshots_dir
            ),
        )

        pages = await discovery.crawl(
            browser.page.url
        )

        # ------------------------------------------------------------
        # Store discovered pages
        # ------------------------------------------------------------

        for page in pages:

            self.db.add_page(
                self.run_id,
                {
                    "url": page.url,
                    "title": page.title,
                    "depth": page.depth,
                    "forms": page.forms,
                    "links": page.links,
                    "screenshot_path": (
                        page.screenshot_path
                    ),
                },
                datetime.now(
                    timezone.utc
                ).isoformat(),
            )

        self.logger.info(
            "Discovery complete: %d pages found.",
            len(pages),
        )

        # ------------------------------------------------------------
        # Discovery statistics
        # ------------------------------------------------------------

        total_inputs = sum(
            len(getattr(page, "inputs", []))
            for page in pages
        )

        total_buttons = sum(
            len(getattr(page, "buttons", []))
            for page in pages
        )

        total_selects = sum(
            len(getattr(page, "selects", []))
            for page in pages
        )

        total_checkboxes = sum(
            len(getattr(page, "checkboxes", []))
            for page in pages
        )

        total_radios = sum(
            len(getattr(page, "radios", []))
            for page in pages
        )

        total_tables = sum(
            len(getattr(page, "tables", []))
            for page in pages
        )

        total_interactive = sum(
            len(
                getattr(
                    page,
                    "interactive_elements",
                    [],
                )
            )
            for page in pages
        )

        self.logger.info(
            (
                "Discovery elements: "
                "inputs=%d, buttons=%d, selects=%d, "
                "checkboxes=%d, radios=%d, tables=%d, "
                "interactive=%d"
            ),
            total_inputs,
            total_buttons,
            total_selects,
            total_checkboxes,
            total_radios,
            total_tables,
            total_interactive,
        )

        return pages

    # ================================================================
    # TESTING + BUG ANALYSIS
    # ================================================================

    async def _test_and_analyze(
        self,
        browser: BrowserManager,
        pages,
    ):

        functional = FunctionalAgent(
            browser,
            screenshot_dir=str(
                self.screenshots_dir
            ),
        )

        # ------------------------------------------------------------
        # Phase 1 smoke tests
        # ------------------------------------------------------------

        smoke_tests = (
            functional.generate_smoke_tests(
                pages
            )
        )

        # ------------------------------------------------------------
        # Phase 2 safe functional audits
        # ------------------------------------------------------------

        functional_tests = (
            functional.generate_functional_tests(
                pages
            )
        )

        # ------------------------------------------------------------
        # Combine
        # ------------------------------------------------------------

        test_cases = (
            smoke_tests
            + functional_tests
        )

        # ------------------------------------------------------------
        # Data-flow / persistence testing
        #
        # IMPORTANT:
        # This remains disabled unless explicitly enabled in TEST.
        # ------------------------------------------------------------

        dataflow_enabled = (
            self.secrets.environment.upper() == "TEST"
            and getattr(
                self.config.testing,
                "allow_test_data_mutations",
                False,
            )
        )

        dataflow = DataFlowAgent(
            browser,
            screenshot_dir=str(self.screenshots_dir),
            enabled=dataflow_enabled,
        )

        dataflow_results = []

        if dataflow_enabled:
            self.logger.info(
                "Data-flow testing ENABLED for TEST environment."
            )

            for page in pages:
                try:
                    result = await dataflow.run_page(page.url)
                    dataflow_results.append(result)

                    self.logger.info(
                        "Data-flow [%s] %s — %s",
                        result.status.upper(),
                        page.url,
                        result.detail,
                    )

                except Exception as exc:
                    self.logger.exception(
                        "Data-flow test error on %s: %s",
                        page.url,
                        exc,
                    )
        else:
            self.logger.info(
                "Data-flow testing disabled. "
                "No test data will be created or modified."
            )

        self.logger.info(
            "Data-flow results: %d",
            len(dataflow_results),
        )

        self.logger.info(
            (
                "Generated %d smoke test(s) + "
                "%d functional test(s) = "
                "%d total test(s)."
            ),
            len(smoke_tests),
            len(functional_tests),
            len(test_cases),
        )

        results = []
        bugs = []

        # ============================================================
        # EXECUTE TESTS
        # ============================================================

        for index, test_case in enumerate(
            test_cases,
            start=1,
        ):

            self.logger.info(
                "Running test [%d/%d]: %s",
                index,
                len(test_cases),
                test_case.name,
            )

            try:

                result = await functional.execute(
                    test_case
                )

            except Exception as exc:

                self.logger.exception(
                    "Test execution crashed: %s",
                    test_case.name,
                )

                from app.agents.functional.functional_agent import (
                    TestResult,
                )

                result = TestResult(
                    test_case=test_case,
                    status="failed",
                    detail=(
                        "Test execution exception: "
                        f"{exc}"
                    ),
                )

            # --------------------------------------------------------
            # FAILED TEST
            # --------------------------------------------------------

            if result.status == "failed":

                self.logger.warning(
                    "Test failed: %s",
                    test_case.name,
                )

                confirmed, retry_results = (
                    await reproduce_failure(
                        functional,
                        test_case,
                        self.config.testing.max_retries_before_bug,
                    )
                )

                final_result = (
                    retry_results[-1]
                    if retry_results
                    else result
                )

                if retry_results:

                    failed_retries = sum(
                        1
                        for retry in retry_results
                        if retry.status == "failed"
                    )

                    reproducibility = (
                        f"{failed_retries}/"
                        f"{len(retry_results)} retries failed"
                    )

                else:

                    reproducibility = (
                        "not retried"
                    )

                # ----------------------------------------------------
                # CONFIRMED BUG
                # ----------------------------------------------------

                if confirmed:

                    bug = build_bug(
                        final_result,
                        reproducibility,
                    )

                    # NOTE: bugs are collected here only. They are
                    # deduplicated and persisted to the DB in one pass
                    # AFTER the full test loop (see BUG DEDUPLICATION
                    # below), not immediately per-test, so that multiple
                    # failing tests sharing the same underlying issue can
                    # be merged into a single master bug before anything
                    # is written.
                    bugs.append(
                        bug
                    )

                    result = final_result

                # ----------------------------------------------------
                # NOT REPRODUCIBLE
                # ----------------------------------------------------

                else:

                    result = final_result

                    result.detail = (
                        (result.detail or "")
                        + " "
                        "(not reproducible on retry — "
                        "not filed as a bug)"
                    )

            # --------------------------------------------------------
            # STORE RESULT
            # --------------------------------------------------------

            results.append(
                result
            )

            self.db.add_test(
                self.run_id,
                test_case.name,
                test_case.category,
                result.status,
                datetime.now(
                    timezone.utc
                ).isoformat(),
                test_case.target_url,
                result.detail,
            )

        # ============================================================
        # BUG DEDUPLICATION
        #
        # Candidate bugs collected during the loop above are merged here:
        # multiple failing tests that share the same module + test
        # category + normalized failure signal (or a recognized
        # cross-page root-cause pattern) become one master bug, with
        # every occurrence preserved in that bug's `affected` list.
        # ============================================================

        bugs = deduplicate_bugs(bugs)

        # ============================================================
        # PERSIST BUG EVIDENCE — BEFORE ANY OLLAMA ANALYSIS
        #
        # This is the crash-safety fix: every confirmed bug's full
        # evidence (steps, expected/actual, severity, screenshots,
        # console/network errors, affected pages) is written to the DB
        # right here, before analyze_bugs() (the slow Ollama phase)
        # runs. root_cause/recommended_fix/confidence are inserted as
        # NULL and filled in afterward via update_bug_analysis() — so a
        # crash or timeout during AI analysis can never lose a
        # confirmed bug, only leave its AI fields unset.
        # ============================================================

        for bug in bugs:

            self.db.add_bug(
                self.run_id,
                {
                    "bug_ref": bug.bug_ref,
                    "title": bug.title,
                    "module": bug.module,
                    "severity": bug.severity,
                    "url": bug.url,
                    "steps": bug.steps,
                    "expected": bug.expected,
                    "actual": bug.actual,
                    "reproducibility": bug.reproducibility,
                    "reproducibility_pct": bug.reproducibility_pct,
                    "screenshot_path": bug.screenshot_path,
                    "console_errors": bug.console_errors,
                    "network_errors": bug.network_errors,
                    "what_was_tested": bug.what_was_tested,
                    "affected": bug.affected,
                },
                datetime.now(timezone.utc).isoformat(),
            )

            self.logger.warning(
                "Bug confirmed and persisted: %s [%s] %s (%d occurrence(s) merged)",
                bug.bug_ref,
                bug.severity,
                bug.title,
                len(bug.affected),
            )

        # ============================================================
        # ROOT-CAUSE ANALYSIS (Slice 3, item 1)
        #
        # Runs AFTER persistence, so bug evidence above is already
        # durable regardless of what happens here. Capped to the top
        # MAX_ANALYSIS_BUGS bugs by severity to bound added latency.
        # Ollama failures (unreachable, timeout, malformed response)
        # are fully contained inside analyze_bugs() — which mutates
        # each bug's root_cause/recommended_fix/confidence in place and
        # never raises — and the outer try/except here is
        # defense-in-depth on top of that.
        # ============================================================

        ordered_bugs = sort_bugs_by_severity(bugs)
        bugs_considered_for_analysis = ordered_bugs[:MAX_ANALYSIS_BUGS]

        try:
            analysis_counts = await analyze_bugs(
                self.ollama,
                ordered_bugs,
                max_bugs=MAX_ANALYSIS_BUGS,
                ollama_reachable=getattr(self, "ollama_ok", False),
            )
            self.logger.info(
                "Root-cause analysis (AI-assisted, max %d bugs by severity): "
                "%d analyzed, %d insufficient evidence, %d unavailable, %d skipped (cap).",
                MAX_ANALYSIS_BUGS,
                analysis_counts["analyzed"],
                analysis_counts["insufficient"],
                analysis_counts["unavailable"],
                analysis_counts["skipped"],
            )
        except Exception as exc:
            self.logger.warning(
                "Root-cause analysis batch failed unexpectedly (continuing without it, "
                "bug evidence already persisted): %s: %s",
                type(exc).__name__, exc,
            )

        # ============================================================
        # UPDATE AI-ANALYSIS FIELDS
        #
        # analyze_bugs() mutates each considered bug's root_cause/
        # recommended_fix/confidence in place and never raises, so even
        # if the try/except above caught something mid-batch, every bug
        # that WAS reached before the failure already has its fields
        # set here — only bugs after the failure point remain NULL,
        # exactly reflecting "not yet analyzed" rather than losing them.
        # ============================================================

        analyzed_at = datetime.now(timezone.utc).isoformat()

        for bug in bugs_considered_for_analysis:
            if bug.root_cause is not None:
                self.db.update_bug_analysis(
                    self.run_id,
                    bug.bug_ref,
                    bug.root_cause,
                    bug.recommended_fix,
                    bug.confidence,
                    analyzed_at,
                )

        # ============================================================
        # FINAL STATISTICS
        # ============================================================

        passed = sum(
            1
            for result in results
            if result.status == "passed"
        )

        failed = sum(
            1
            for result in results
            if result.status == "failed"
        )

        skipped = sum(
            1
            for result in results
            if result.status == "skipped"
        )

        self.logger.info(
            (
                "Testing complete: "
                "%d passed, "
                "%d failed, "
                "%d skipped, "
                "%d bug(s) confirmed."
            ),
            passed,
            failed,
            skipped,
            len(bugs),
        )

        return results, bugs, dataflow_results

    # ================================================================
    # REPORTING
    # ================================================================

    def _write_reports(self, bugs=None, dataflow_results=None) -> None:

        data = self.db.fetch_all(
            self.run_id
        )

        # Bugs are serialized from the in-memory, already-deduplicated
        # list (not re-fetched from the DB) so fields that only exist on
        # the in-memory Bug object — affected occurrences, numeric
        # reproducibility, root_cause/recommended_fix as set by this
        # run's own analysis pass — reach the report even if this method
        # is reached via the `finally` path after an exception. db.py
        # itself is intentionally left unchanged in shape here (it's
        # the source of truth for pages/tests, and for bug evidence in
        # the case of a crash between persistence and this call).
        serialized_bugs = [asdict(b) for b in (bugs or [])]

        serialized_dataflow = []

        for result in (dataflow_results or []):
            if hasattr(result, "__dict__"):
                serialized_dataflow.append(
                    dict(result.__dict__)
                )
            elif isinstance(result, dict):
                serialized_dataflow.append(result)

        generate_reports(
            self.run_dir,
            data["run"],
            data["pages"],
            data["tests"],
            serialized_bugs,
            serialized_dataflow,
        )

        self.logger.info(
            "Reports written to %s",
            self.run_dir,
        )